"""Accession-bound financial facts against real PostgreSQL, from tiny FSDS-shaped packages.

Covers binding to stored filings, idempotency, the amendment / quarter-vs-YTD / restatement
tripwires, conflicts, package revisions, rollback, raw provenance and the no-availability rule.
No network is touched anywhere.
"""

from __future__ import annotations

import hashlib
import json
import uuid
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from tests.fsds_fixture import (
    ACC_10K,
    ACC_10Q,
    ACC_10QA,
    ACC_20F,
    CIK_MAIN,
    build_package,
    default_nums,
    default_subs,
    num_row,
    sub_row,
)

from quantforge.ingestion import financial_facts as ingestion_module
from quantforge.ingestion.financial_facts import (
    AccessionCikMismatchError,
    AccessionNotInPackageError,
    FactIngestionResult,
    FinancialFactConflictError,
    FinancialFactIngestionService,
    FsdsRowsRejectedError,
    InvalidFactRequestError,
    MissingSecFilingError,
    NothingToIngestError,
    PackageRevisionError,
    SubmissionMetadataMismatchError,
    UnsupportedFormError,
)
from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.ingestion.sources import get_or_create_data_source
from quantforge.models.company import Company
from quantforge.models.enums import (
    AvailabilityBasis,
    FinancialFactSourceKind,
    IngestionStatus,
    ProviderType,
    SecFormType,
)
from quantforge.models.filing import SecFiling
from quantforge.models.financial_fact import (
    FinancialFact,
    FinancialFactArtifact,
    FinancialFactSubmission,
)
from quantforge.models.ingestion import IngestionRun
from quantforge.repositories.financial_fact import FinancialFactRepository
from quantforge.research.availability import APPROVED_AVAILABILITY_BASES

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
GAAP = "us-gaap/2024"
EPS = "EarningsPerShareDiluted"
SOURCE = "sec-edgar"

#: The stored filing for each fixture accession: (form, filing_date, EDGAR acceptance instant).
FILINGS = {
    ACC_10Q: (SecFormType.FORM_10Q, date(2024, 5, 3), datetime(2024, 5, 3, 20, 30, 22, tzinfo=UTC)),
    ACC_10K: (
        SecFormType.FORM_10K,
        date(2023, 11, 3),
        datetime(2023, 11, 3, 22, 8, 27, tzinfo=UTC),
    ),
    ACC_10QA: (
        SecFormType.FORM_10Q_A,
        date(2024, 5, 17),
        datetime(2024, 5, 17, 13, 12, 40, tzinfo=UTC),
    ),
}


@pytest.fixture
def company(db_session: Session) -> Company:
    row = Company(legal_name="Example Filer Inc.", display_name="Example Filer", cik=CIK_MAIN)
    db_session.add(row)
    db_session.commit()
    return row


def _store_filings(
    session: Session, company: Company, accessions: tuple[str, ...] = (ACC_10Q, ACC_10K, ACC_10QA)
) -> dict[str, SecFiling]:
    source = get_or_create_data_source(
        session, name=SOURCE, provider_type=ProviderType.REGULATORY_FILING
    )
    rows: dict[str, SecFiling] = {}
    for accession in accessions:
        form, filing_date, accepted_at = FILINGS[accession]
        row = SecFiling(
            company_id=company.id,
            accession_number=accession,
            form=form,
            filing_date=filing_date,
            accepted_at=accepted_at,
            source_id=source.id,
            ingested_at=NOW,
            created_at=NOW,
        )
        session.add(row)
        rows[accession] = row
    session.commit()
    return rows


@pytest.fixture
def filings(db_session: Session, company: Company) -> dict[str, SecFiling]:
    return _store_filings(db_session, company)


def _service(session: Session, tmp_path: Path) -> FinancialFactIngestionService:
    return FinancialFactIngestionService(
        session=session, store=RawResponseStore(root=tmp_path / "raw"), now=NOW
    )


def _ingest(
    session: Session,
    tmp_path: Path,
    package: Path,
    *,
    name: str = "2024q2",
    accessions: tuple[str, ...] = (ACC_10Q, ACC_10K, ACC_10QA),
    ciks: tuple[str, ...] = (),
) -> FactIngestionResult:
    result = _service(session, tmp_path).ingest_fsds_package(
        package, package_name=name, accessions=accessions, ciks=ciks
    )
    session.commit()
    return result


def _failed_ingest(session: Session, tmp_path: Path, package: Path, **kwargs: Any) -> BaseException:
    """Run an ingestion expected to fail; commit what the CLI commits (the failed run)."""
    with pytest.raises(Exception) as caught:
        _service(session, tmp_path).ingest_fsds_package(
            package,
            package_name=kwargs.pop("name", "2024q2"),
            accessions=kwargs.pop("accessions", (ACC_10Q, ACC_10K, ACC_10QA)),
            ciks=kwargs.pop("ciks", ()),
        )
    session.commit()
    return caught.value


def _facts(session: Session, accession: str) -> list[FinancialFact]:
    return FinancialFactRepository(session).facts_for_accession(accession)


# ── happy path and provenance ─────────────────────────────────────────────────────────────


def test_facts_are_stored_bound_to_their_filings_with_a_full_provenance_chain(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    package = build_package(tmp_path / "2024q2.zip")
    result = _ingest(db_session, tmp_path, package)

    assert result.unbound_accessions == ()
    assert result.bound_accessions == tuple(sorted((ACC_10Q, ACC_10K, ACC_10QA)))
    run = db_session.query(IngestionRun).one()
    assert (run.provider, run.dataset, run.status) == (
        "sec-edgar",
        "sec-fsds",
        IngestionStatus.SUCCEEDED,
    )
    assert (run.records_received, run.records_written, run.records_skipped, run.records_failed) == (
        14,
        14,
        0,
        0,
    )
    assert db_session.query(FinancialFact).count() == 14
    for accession, filing in filings.items():
        assert {f.sec_filing_id for f in _facts(db_session, accession)} == {filing.id}

    artifact = db_session.query(FinancialFactArtifact).one()
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    assert (artifact.source_kind, artifact.artifact_name, artifact.sha256) == (
        FinancialFactSourceKind.SEC_FSDS,
        "2024q2",
        digest,
    )
    assert artifact.byte_length == package.stat().st_size
    assert artifact.num_has_segments is True
    assert artifact.ingestion_run_id == run.id
    # The ZIP lives in the raw store, content-addressed, and is byte-identical to the input.
    raw = tmp_path / "raw" / artifact.raw_artifact_path
    assert raw.read_bytes() == package.read_bytes()
    assert artifact.raw_artifact_path == f"sec-edgar/sec-fsds/2024q2/{digest}.zip"
    assert set(artifact.member_digests) == {"sub.txt", "num.txt"}

    # fact -> raw artifact trace
    some_fact = _facts(db_session, ACC_10Q)[0]
    trace = FinancialFactRepository(db_session).trace(some_fact.id)
    assert trace is not None
    assert (trace.accession_number, trace.artifact_name, trace.artifact_sha256) == (
        ACC_10Q,
        "2024q2",
        digest,
    )
    assert trace.member_digests == artifact.member_digests
    assert trace.ingestion_run_id == run.id
    assert trace.company_cik == CIK_MAIN


def test_the_run_manifest_never_invents_a_retrieval_time_or_records_the_local_path(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    package = build_package(tmp_path / "2024q2.zip")
    _ingest(db_session, tmp_path, package)
    run = db_session.query(IngestionRun).one()
    assert run.raw_artifact_path
    manifest_text = (tmp_path / "raw" / run.raw_artifact_path).read_text()
    manifest = json.loads(manifest_text)
    entry = manifest["artifacts"][0]
    assert entry["retrieved_at"] is None
    assert entry["origin"] == "local_file"
    assert str(tmp_path) not in manifest_text
    assert str(tmp_path) not in json.dumps(run.request_parameters)
    assert run.request_parameters["package"] == "2024q2"


def test_member_digests_equal_the_digests_of_the_members_actually_parsed(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    package = build_package(tmp_path / "2024q2.zip")
    _ingest(db_session, tmp_path, package)
    artifact = db_session.query(FinancialFactArtifact).one()
    with zipfile.ZipFile(package) as archive:
        assert artifact.member_digests == {
            "sub.txt": hashlib.sha256(archive.read("sub.txt")).hexdigest(),
            "num.txt": hashlib.sha256(archive.read("num.txt")).hexdigest(),
        }


def test_submission_context_is_stored_verbatim_and_acceptance_text_is_not_an_instant(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(db_session, tmp_path, build_package(tmp_path / "2024q2.zip"))
    submission = (
        db_session.query(FinancialFactSubmission).filter_by(sec_filing_id=filings[ACC_10Q].id).one()
    )
    assert submission.period_end == date(2024, 3, 31)
    assert (submission.fiscal_year, submission.fiscal_period, submission.fiscal_year_end) == (
        2024,
        "Q2",
        "0930",
    )
    assert submission.source_accepted == "2024-05-03 16:30:22"
    assert isinstance(submission.source_accepted, str)
    # The filing's own acceptance record is a different, separately stored fact.
    assert filings[ACC_10Q].accepted_at == datetime(2024, 5, 3, 20, 30, 22, tzinfo=UTC)


def test_exact_values_round_trip_through_postgres(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(db_session, tmp_path, build_package(tmp_path / "2024q2.zip"))
    values = {
        (f.tag, f.duration_quarters): f.value
        for f in _facts(db_session, ACC_10Q)
        if f.unit == "USD"
    }
    assert values[("Assets", 0)] == Decimal("12345678901234567890.1234")
    assert values[("NetIncomeLoss", 1)] == Decimal("-1234567.0000")
    shares = next(f for f in _facts(db_session, ACC_10Q) if f.unit == "shares")
    assert shares.value == Decimal("15500000000.0000")
    assert all(isinstance(f.value, Decimal) for f in _facts(db_session, ACC_10Q))


def test_a_legacy_layout_package_is_recorded_as_having_no_segments_column(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    # The pre-reprocessing layout compiled only entity-wide / co-registrant data: no dimensional rows.
    legacy_nums = [row for row in default_nums() if not row["segments"]]
    package = build_package(tmp_path / "2023q4.zip", nums=legacy_nums, with_segments=False)
    _ingest(db_session, tmp_path, package, name="2023q4")
    assert db_session.query(FinancialFactArtifact).one().num_has_segments is False
    assert {f.segments for f in _facts(db_session, ACC_10Q)} == {""}


def test_dimensional_rows_flattened_into_a_layout_without_segments_collide_and_are_refused(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    """Rows that look identical under the key once the dimension is lost are never merged."""
    package = build_package(
        tmp_path / "2023q4.zip", with_segments=False
    )  # keeps the dimensional row
    error = _failed_ingest(db_session, tmp_path, package, name="2023q4")
    assert isinstance(error, FinancialFactConflictError)
    assert error.stored == Decimal("90000000000.0000") and error.incoming == Decimal(
        "45000000000.0000"
    )
    assert db_session.query(FinancialFact).count() == 0


# ── idempotency ───────────────────────────────────────────────────────────────────────────


def test_an_identical_rerun_writes_nothing_and_skips_everything(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    package = build_package(tmp_path / "2024q2.zip")
    _ingest(db_session, tmp_path, package)
    _ingest(db_session, tmp_path, package)

    assert db_session.query(FinancialFact).count() == 14
    assert db_session.query(FinancialFactArtifact).count() == 1
    assert db_session.query(FinancialFactSubmission).count() == 3
    tallies = sorted((r.records_written, r.records_skipped) for r in db_session.query(IngestionRun))
    assert tallies == [(0, 14), (14, 0)]


def test_a_later_bounded_run_adds_only_what_it_selects(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    package = build_package(tmp_path / "2024q2.zip")
    _ingest(db_session, tmp_path, package, accessions=(ACC_10K,))
    assert db_session.query(FinancialFact).count() == 1
    _ingest(db_session, tmp_path, package, accessions=(ACC_10Q,))
    assert db_session.query(FinancialFact).count() == 13
    assert db_session.query(FinancialFactArtifact).count() == 1


def test_identical_duplicate_num_rows_collapse_but_conflicting_duplicates_do_not(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    row = num_row(
        adsh=ACC_10Q, tag="Assets", version=GAAP, ddate="20240331", qtrs="0", value="5.0000"
    )
    package = build_package(tmp_path / "2024q2.zip", nums=[row, dict(row)])
    result = _ingest(db_session, tmp_path, package, accessions=(ACC_10Q,))
    assert (
        result.outcome.records_received,
        result.outcome.records_written,
        result.outcome.records_skipped,
    ) == (2, 1, 1)

    conflicting = dict(row, value="6.0000")
    bad = build_package(tmp_path / "2024q3.zip", nums=[row, conflicting])
    error = _failed_ingest(db_session, tmp_path, bad, name="2024q3", accessions=(ACC_10Q,))
    assert isinstance(error, FinancialFactConflictError)
    assert db_session.query(FinancialFact).count() == 1  # nothing from the failed run survived


# ── the three tripwires ───────────────────────────────────────────────────────────────────


def test_tripwire_quarter_and_ytd_of_one_concept_in_one_accession_stay_separate_rows(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(db_session, tmp_path, build_package(tmp_path / "2024q2.zip"))
    repo = FinancialFactRepository(db_session)
    eps = repo.facts_for_concept(ACC_10Q, tag=EPS, version=GAAP, period_end=date(2024, 3, 31))
    assert [(f.duration_quarters, f.value) for f in eps] == [
        (1, Decimal("1.5300")),
        (2, Decimal("3.9100")),
    ]
    # Narrowing is explicit; with no duration the layer never picks "the quarter" for the caller.
    only_quarter = repo.facts_for_concept(
        ACC_10Q, tag=EPS, version=GAAP, period_end=date(2024, 3, 31), duration_quarters=1
    )
    assert [f.value for f in only_quarter] == [Decimal("1.5300")]
    # And the schema itself forbids collapsing them into one identity.
    assert len({(f.tag, f.version, f.period_end, f.duration_quarters, f.unit) for f in eps}) == 2


def test_tripwire_an_amendment_never_overwrites_the_original_accession(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(db_session, tmp_path, build_package(tmp_path / "2024q2.zip"))
    repo = FinancialFactRepository(db_session)

    original = repo.facts_for_concept(
        ACC_10Q, tag=EPS, version=GAAP, period_end=date(2024, 3, 31), duration_quarters=1
    )
    amended = repo.facts_for_concept(
        ACC_10QA, tag=EPS, version=GAAP, period_end=date(2024, 3, 31), duration_quarters=1
    )
    assert [f.value for f in original] == [Decimal("1.5300")]
    assert [f.value for f in amended] == [Decimal("1.5400")]
    assert original[0].sec_filing_id == filings[ACC_10Q].id
    assert amended[0].sec_filing_id == filings[ACC_10QA].id
    assert original[0].sec_filing_id != amended[0].sec_filing_id
    # No link is invented between them from the form names.
    assert {c.name for c in FinancialFact.__table__.columns}.isdisjoint(
        {"amends", "amended_by", "supersedes"}
    )


def test_tripwire_a_later_accession_never_mutates_an_earlier_accessions_fact(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    """Restatement/look-ahead: a later filing re-reporting the same period under a new value is a
    separate fact; the earlier accession keeps exactly what it reported."""
    first = build_package(tmp_path / "2024q2.zip")
    _ingest(db_session, tmp_path, first, accessions=(ACC_10Q,))
    before = [
        (f.id, f.tag, f.period_end, f.duration_quarters, f.value)
        for f in _facts(db_session, ACC_10Q)
    ]

    # A later 10-K/A restates the 2024-03-31 quarter's EPS and Assets under its own accession.
    later = "0000320193-24-000999"
    later_subs = [
        sub_row(
            adsh=later,
            cik="320193",
            form="10-K/A",
            period="20240930",
            fy="2024",
            fp="FY",
            filed="20241105",
            accepted="2024-11-05 17:00:00",
        )
    ]
    later_nums = [
        num_row(
            adsh=later,
            tag=EPS,
            version=GAAP,
            ddate="20240331",
            qtrs="1",
            uom="USD/shares",
            value="9.9999",
        ),
        num_row(adsh=later, tag="Assets", version=GAAP, ddate="20240331", qtrs="0", value="1.0000"),
    ]
    source = db_session.query(SecFiling).first()
    assert source is not None
    db_session.add(
        SecFiling(
            company_id=source.company_id,
            accession_number=later,
            form=SecFormType.FORM_10K_A,
            filing_date=date(2024, 11, 5),
            accepted_at=datetime(2024, 11, 5, 22, 0, tzinfo=UTC),
            source_id=source.source_id,
            ingested_at=NOW,
            created_at=NOW,
        )
    )
    db_session.commit()
    _ingest(
        db_session,
        tmp_path,
        build_package(tmp_path / "2024q4.zip", subs=later_subs, nums=later_nums),
        name="2024q4",
        accessions=(later,),
    )

    after = [
        (f.id, f.tag, f.period_end, f.duration_quarters, f.value)
        for f in _facts(db_session, ACC_10Q)
    ]
    assert after == before, "the earlier accession's facts are byte-for-byte unchanged"
    restated = FinancialFactRepository(db_session).facts_for_concept(later, tag=EPS, version=GAAP)
    assert [f.value for f in restated] == [Decimal("9.9999")]
    # There is no "latest value for this period" anywhere in the read layer.
    assert not hasattr(FinancialFactRepository, "latest")
    assert not hasattr(FinancialFactRepository, "current")


# ── custom concepts, versions, dimensions, units ──────────────────────────────────────────


def test_custom_standard_dimensional_and_coregistrant_facts_are_all_preserved_distinctly(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(db_session, tmp_path, build_package(tmp_path / "2024q2.zip"))
    facts = _facts(db_session, ACC_10Q)
    extension = [f for f in facts if f.tag == "ServicesRevenueAdjusted"]
    assert [(f.version, f.value) for f in extension] == [(ACC_10Q, Decimal("23000000000.0000"))]
    eps_versions = {
        f.version
        for f in facts
        if f.tag == EPS and f.duration_quarters == 1 and f.period_end == date(2024, 3, 31)
    }
    assert eps_versions == {"us-gaap/2024", "us-gaap/2023"}
    revenues = {(f.segments, f.coreg): f.value for f in facts if f.tag == "Revenues"}
    assert len(revenues) == 3
    assert {f.unit for f in facts} == {"USD", "USD/shares", "shares", "pure"}


def test_tag_alone_is_not_a_concept_the_query_requires_the_version(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(db_session, tmp_path, build_package(tmp_path / "2024q2.zip"))
    repo = FinancialFactRepository(db_session)
    assert len(repo.facts_for_concept(ACC_10Q, tag=EPS, version="us-gaap/2023")) == 1
    assert repo.facts_for_concept(ACC_10Q, tag=EPS, version="us-gaap/1999") == []
    with pytest.raises(TypeError):
        repo.facts_for_concept(ACC_10Q, tag=EPS)  # type: ignore[call-arg]


def test_queries_are_deterministic_and_ordered_by_identity(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(db_session, tmp_path, build_package(tmp_path / "2024q2.zip"))
    first = [f.id for f in _facts(db_session, ACC_10Q)]
    second = [f.id for f in _facts(db_session, ACC_10Q)]
    assert first == second
    keys = [
        (f.tag, f.version, f.period_end, f.duration_quarters, f.unit, f.segments, f.coreg)
        for f in _facts(db_session, ACC_10Q)
    ]
    assert keys == sorted(keys)


# ── binding ───────────────────────────────────────────────────────────────────────────────


def test_a_missing_sec_filing_for_an_explicit_accession_fails_loudly_and_writes_nothing(
    db_session: Session, tmp_path: Path, company: Company
) -> None:
    _store_filings(db_session, company, accessions=(ACC_10K,))  # the 10-Q's filing is absent
    error = _failed_ingest(
        db_session, tmp_path, build_package(tmp_path / "2024q2.zip"), accessions=(ACC_10Q,)
    )
    assert isinstance(error, MissingSecFilingError)
    assert db_session.query(FinancialFact).count() == 0
    assert db_session.query(FinancialFactArtifact).count() == 0
    assert db_session.query(SecFiling).filter_by(accession_number=ACC_10Q).count() == 0, (
        "never created from FSDS"
    )
    run = db_session.query(IngestionRun).one()
    assert run.status is IngestionStatus.FAILED and run.error_type == "MissingSecFilingError"


def test_a_cik_selection_reports_unbound_accessions_and_binds_the_rest(
    db_session: Session, tmp_path: Path, company: Company
) -> None:
    _store_filings(db_session, company, accessions=(ACC_10Q, ACC_10K))  # no stored 10-Q/A
    result = _ingest(
        db_session,
        tmp_path,
        build_package(tmp_path / "2024q2.zip"),
        accessions=(),
        ciks=("320193",),
    )
    assert result.unbound_accessions == (ACC_10QA,)
    assert set(result.bound_accessions) == {ACC_10Q, ACC_10K}
    assert result.outcome.status is IngestionStatus.PARTIAL
    assert _facts(db_session, ACC_10QA) == []
    assert db_session.query(FinancialFact).count() == 13


def test_a_cik_selection_with_nothing_bindable_is_an_error(
    db_session: Session, tmp_path: Path, company: Company
) -> None:
    error = _failed_ingest(
        db_session,
        tmp_path,
        build_package(tmp_path / "2024q2.zip"),
        accessions=(),
        ciks=("320193",),
    )
    assert isinstance(error, NothingToIngestError)
    assert db_session.query(FinancialFact).count() == 0


def test_out_of_scope_forms_are_reported_on_cik_selection_and_refused_when_explicit(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    package = build_package(tmp_path / "2024q2.zip")
    result = _ingest(db_session, tmp_path, package, accessions=(), ciks=("320193", "999999"))
    assert result.out_of_scope_forms == ((ACC_20F, "20-F"),)
    assert _facts(db_session, ACC_20F) == []
    error = _failed_ingest(db_session, tmp_path, package, accessions=(ACC_20F,), name="2024q2")
    assert isinstance(error, UnsupportedFormError)


def test_an_accession_absent_from_the_package_is_an_error(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    error = _failed_ingest(
        db_session,
        tmp_path,
        build_package(tmp_path / "2024q2.zip"),
        accessions=("0000320193-24-000777",),
    )
    assert isinstance(error, AccessionNotInPackageError)


def test_a_cik_mismatch_between_the_package_and_the_stored_filings_company_fails_loudly(
    db_session: Session, tmp_path: Path
) -> None:
    other = Company(legal_name="Someone Else", display_name="Someone Else", cik="0000111111")
    db_session.add(other)
    db_session.commit()
    _store_filings(db_session, other, accessions=(ACC_10Q,))
    error = _failed_ingest(
        db_session, tmp_path, build_package(tmp_path / "2024q2.zip"), accessions=(ACC_10Q,)
    )
    assert isinstance(error, AccessionCikMismatchError)
    assert error.source_cik == CIK_MAIN and error.stored_ciks == ["0000111111"]
    assert db_session.query(FinancialFact).count() == 0


def test_a_company_without_a_cik_makes_the_binding_unverifiable_and_is_refused(
    db_session: Session, tmp_path: Path
) -> None:
    nameless = Company(legal_name="No CIK Corp", display_name="No CIK", cik=None)
    db_session.add(nameless)
    db_session.commit()
    _store_filings(db_session, nameless, accessions=(ACC_10Q,))
    error = _failed_ingest(
        db_session, tmp_path, build_package(tmp_path / "2024q2.zip"), accessions=(ACC_10Q,)
    )
    assert isinstance(error, AccessionCikMismatchError)
    assert error.stored_ciks == [None]


def test_a_joint_submission_binds_to_the_registrant_the_package_names(
    db_session: Session, tmp_path: Path, company: Company
) -> None:
    other = Company(legal_name="Co-registrant", display_name="Co-registrant", cik="0000111111")
    db_session.add(other)
    db_session.commit()
    rows = _store_filings(db_session, company, accessions=(ACC_10Q,))
    co = _store_filings(db_session, other, accessions=(ACC_10Q,))
    _ingest(db_session, tmp_path, build_package(tmp_path / "2024q2.zip"), accessions=(ACC_10Q,))
    assert {f.sec_filing_id for f in _facts(db_session, ACC_10Q)} == {rows[ACC_10Q].id}
    assert co[ACC_10Q].id != rows[ACC_10Q].id


@pytest.mark.parametrize(
    ("override", "field"),
    [({"form": "10-K"}, "form"), ({"filed": "20240504"}, "filing_date")],
)
def test_a_form_or_filing_date_disagreement_with_the_stored_filing_is_refused(
    db_session: Session,
    tmp_path: Path,
    filings: dict[str, SecFiling],
    override: dict[str, str],
    field: str,
) -> None:
    subs = default_subs()
    subs[0].update(override)
    error = _failed_ingest(
        db_session,
        tmp_path,
        build_package(tmp_path / "2024q2.zip", subs=subs),
        accessions=(ACC_10Q,),
    )
    assert isinstance(error, SubmissionMetadataMismatchError)
    assert set(error.differences) == {field}
    assert db_session.query(FinancialFact).count() == 0


# ── conflicts and revisions ───────────────────────────────────────────────────────────────


def test_a_changed_value_for_a_stored_identity_is_a_conflict_and_never_overwrites(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(db_session, tmp_path, build_package(tmp_path / "2024q2.zip"))
    changed = [dict(r) for r in default_nums()]
    for row in changed:
        if (
            row["adsh"] == ACC_10Q
            and row["tag"] == EPS
            and row["qtrs"] == "1"
            and row["version"] == GAAP
        ):
            row["value"] = "1.9999"
    error = _failed_ingest(
        db_session, tmp_path, build_package(tmp_path / "2024q3.zip", nums=changed), name="2024q3"
    )
    assert isinstance(error, FinancialFactConflictError)
    assert error.stored == Decimal("1.5300") and error.incoming == Decimal("1.9999")
    stored = FinancialFactRepository(db_session).facts_for_concept(
        ACC_10Q, tag=EPS, version=GAAP, period_end=date(2024, 3, 31), duration_quarters=1
    )
    assert [f.value for f in stored] == [Decimal("1.5300")]
    assert db_session.query(FinancialFact).count() == 14
    assert db_session.query(FinancialFactArtifact).count() == 1, (
        "the failed run's artifact row rolled back"
    )


def test_a_conflict_in_a_later_batch_rolls_back_every_earlier_batch(
    db_session: Session,
    tmp_path: Path,
    filings: dict[str, SecFiling],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ingestion_module, "FACT_BATCH_SIZE", 2)
    rows = [
        num_row(
            adsh=ACC_10Q,
            tag=f"Concept{i}",
            version=GAAP,
            ddate="20240331",
            qtrs="0",
            value=f"{i}.0000",
        )
        for i in range(5)
    ]
    rows.append(dict(rows[0], value="999.0000"))  # conflicts with the first row, in a later batch
    error = _failed_ingest(
        db_session,
        tmp_path,
        build_package(tmp_path / "2024q2.zip", nums=rows),
        accessions=(ACC_10Q,),
    )
    assert isinstance(error, FinancialFactConflictError)
    assert db_session.query(FinancialFact).count() == 0, "no partial canonical fact writes"
    failed = db_session.query(IngestionRun).one()
    assert failed.status is IngestionStatus.FAILED
    assert failed.records_written == 0
    assert failed.raw_artifact_path, "the raw evidence of the failed run stays referenced"


def test_a_changed_package_under_the_same_name_is_refused_and_both_byte_sets_survive(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    original = build_package(tmp_path / "2024q2.zip")
    _ingest(db_session, tmp_path, original)
    original_bytes = original.read_bytes()

    revised_nums = [
        *default_nums(),
        num_row(
            adsh=ACC_10Q, tag="Extra", version=GAAP, ddate="20240331", qtrs="0", value="1.0000"
        ),
    ]
    revised = build_package(tmp_path / "revised.zip", nums=revised_nums)
    error = _failed_ingest(db_session, tmp_path, revised, name="2024q2")
    assert isinstance(error, PackageRevisionError)
    assert error.known_sha256 == [hashlib.sha256(original_bytes).hexdigest()]
    assert error.new_sha256 == hashlib.sha256(revised.read_bytes()).hexdigest()

    assert db_session.query(FinancialFact).count() == 14, "no fact from the revised bytes"
    assert db_session.query(FinancialFactArtifact).count() == 1
    stored = db_session.query(FinancialFactArtifact).one()
    # The old evidence is intact and the new bytes are preserved too (content-addressed).
    assert (tmp_path / "raw" / stored.raw_artifact_path).read_bytes() == original_bytes
    zips = sorted(p.name for p in (tmp_path / "raw/sec-edgar/sec-fsds/2024q2").glob("*.zip"))
    assert len(zips) == 2
    failed = db_session.query(IngestionRun).filter_by(status=IngestionStatus.FAILED).one()
    assert failed.error_type == "PackageRevisionError"
    assert failed.raw_artifact_path, "the failed run points at the revised package's manifest"


def test_rejected_rows_fail_the_run_keep_the_raw_package_and_store_no_facts(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    nums = [
        num_row(
            adsh=ACC_10Q, tag="Assets", version=GAAP, ddate="20240331", qtrs="0", value="1.0000"
        ),
        num_row(adsh=ACC_10Q, tag="Bad", version=GAAP, ddate="20240331", qtrs="0", value="NaN"),
    ]
    package = build_package(tmp_path / "2024q2.zip", nums=nums)
    error = _failed_ingest(db_session, tmp_path, package, accessions=(ACC_10Q,))
    assert isinstance(error, FsdsRowsRejectedError)
    assert error.count == 1 and "plain finite decimal" in error.samples[0]
    assert db_session.query(FinancialFact).count() == 0
    assert db_session.query(FinancialFactArtifact).count() == 0
    run = db_session.query(IngestionRun).one()
    assert run.status is IngestionStatus.FAILED
    assert (run.records_received, run.records_failed, run.records_written) == (2, 1, 0)
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    assert (tmp_path / "raw" / f"sec-edgar/sec-fsds/2024q2/{digest}.zip").is_file(), (
        "evidence retained"
    )


def test_a_rejected_sub_row_fails_the_run_before_anything_is_written(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    subs = default_subs()
    subs[0]["period"] = "20240231"
    error = _failed_ingest(
        db_session,
        tmp_path,
        build_package(tmp_path / "2024q2.zip", subs=subs),
        accessions=(ACC_10Q,),
    )
    assert isinstance(error, FsdsRowsRejectedError)
    assert db_session.query(FinancialFact).count() == 0


# ── request validation leaves no run row ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"name": "2024-q2"}, "package name"),
        ({"name": "2024q5"}, "package name"),
        ({"accessions": ("000032019324000010",)}, "accession must look like"),
        ({"ciks": ("abc",)}, "CIK"),
        ({"ciks": ("0",)}, "CIK"),
        ({"accessions": (), "ciks": ()}, "at least one"),
    ],
)
def test_an_invalid_request_is_refused_before_any_run_is_recorded(
    db_session: Session, tmp_path: Path, kwargs: dict[str, Any], fragment: str
) -> None:
    package = build_package(tmp_path / "2024q2.zip")
    with pytest.raises(InvalidFactRequestError, match=fragment):
        _service(db_session, tmp_path).ingest_fsds_package(
            package,
            package_name=kwargs.get("name", "2024q2"),
            accessions=kwargs.get("accessions", (ACC_10Q,)),
            ciks=kwargs.get("ciks", ()),
        )
    assert db_session.query(IngestionRun).count() == 0
    assert not (tmp_path / "raw").exists(), "nothing was copied for an invalid request"


def test_a_missing_package_file_is_refused_before_any_run(
    db_session: Session, tmp_path: Path
) -> None:
    with pytest.raises(InvalidFactRequestError, match="not a file"):
        _service(db_session, tmp_path).ingest_fsds_package(
            tmp_path / "absent.zip", package_name="2024q2", accessions=(ACC_10Q,)
        )
    assert db_session.query(IngestionRun).count() == 0


def test_a_corrupt_package_fails_the_run_and_is_preserved_as_evidence(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    path = tmp_path / "2024q2.zip"
    path.write_bytes(b"not a zip at all")
    error = _failed_ingest(db_session, tmp_path, path, accessions=(ACC_10Q,))
    assert type(error).__name__ == "FsdsPackageError"
    run = db_session.query(IngestionRun).one()
    assert run.status is IngestionStatus.FAILED
    assert run.raw_artifact_path


# ── schema-level guarantees ───────────────────────────────────────────────────────────────


def test_the_database_forbids_a_second_fact_with_the_same_identity(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(db_session, tmp_path, build_package(tmp_path / "2024q2.zip"))
    fact = _facts(db_session, ACC_10Q)[0]
    duplicate = FinancialFact(
        sec_filing_id=fact.sec_filing_id,
        submission_id=fact.submission_id,
        tag=fact.tag,
        version=fact.version,
        period_end=fact.period_end,
        duration_quarters=fact.duration_quarters,
        unit=fact.unit,
        segments=fact.segments,
        coreg=fact.coreg,
        value=Decimal("0"),
        ingested_at=NOW,
        created_at=NOW,
    )
    db_session.add(duplicate)
    with pytest.raises(sa.exc.IntegrityError, match="uq_financial_fact_identity"):
        db_session.flush()
    db_session.rollback()


def test_a_fact_cannot_exist_without_a_filing_and_a_filing_with_facts_cannot_be_deleted(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(db_session, tmp_path, build_package(tmp_path / "2024q2.zip"))
    fact = _facts(db_session, ACC_10Q)[0]
    orphan = FinancialFact(
        sec_filing_id=uuid.uuid4(),
        submission_id=fact.submission_id,
        tag="X",
        version=GAAP,
        period_end=date(2024, 3, 31),
        duration_quarters=0,
        unit="USD",
        value=None,
        ingested_at=NOW,
        created_at=NOW,
    )
    db_session.add(orphan)
    with pytest.raises(sa.exc.IntegrityError, match="fk_financial_fact_sec_filing_id"):
        db_session.flush()
    db_session.rollback()
    with pytest.raises(sa.exc.IntegrityError, match="financial_fact"):
        db_session.execute(sa.delete(SecFiling).where(SecFiling.id == filings[ACC_10Q].id))
    db_session.rollback()


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("duration_quarters", -1, "duration_quarters_non_negative"),
        ("tag", "", "tag_not_blank"),
        ("version", "", "version_not_blank"),
        ("unit", "", "unit_not_blank"),
        ("period_end", date(1899, 1, 1), "period_end_range"),
    ],
)
def test_check_constraints_back_the_canonical_validation(
    db_session: Session,
    tmp_path: Path,
    filings: dict[str, SecFiling],
    column: str,
    value: object,
    constraint: str,
) -> None:
    _ingest(db_session, tmp_path, build_package(tmp_path / "2024q2.zip"))
    fact = _facts(db_session, ACC_10Q)[0]
    fields: dict[str, Any] = {
        "sec_filing_id": fact.sec_filing_id,
        "submission_id": fact.submission_id,
        "tag": "Other",
        "version": GAAP,
        "period_end": date(2024, 3, 31),
        "duration_quarters": 0,
        "unit": "USD",
        "value": Decimal("1"),
        "ingested_at": NOW,
        "created_at": NOW,
    }
    fields[column] = value
    db_session.add(FinancialFact(**fields))
    with pytest.raises(sa.exc.IntegrityError, match=constraint):
        db_session.flush()
    db_session.rollback()


def test_the_value_column_cannot_hold_more_than_the_source_can_state(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(db_session, tmp_path, build_package(tmp_path / "2024q2.zip"))
    fact = _facts(db_session, ACC_10Q)[0]
    db_session.add(
        FinancialFact(
            sec_filing_id=fact.sec_filing_id,
            submission_id=fact.submission_id,
            tag="Wide",
            version=GAAP,
            period_end=date(2024, 3, 31),
            duration_quarters=0,
            unit="USD",
            value=Decimal("1" + "0" * 24),
            ingested_at=NOW,
            created_at=NOW,
        )
    )
    with pytest.raises(sa.exc.DBAPIError):
        db_session.flush()
    db_session.rollback()


def test_the_same_package_name_may_hold_only_one_row_per_content_digest(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(db_session, tmp_path, build_package(tmp_path / "2024q2.zip"))
    artifact = db_session.query(FinancialFactArtifact).one()
    db_session.add(
        FinancialFactArtifact(
            source_id=artifact.source_id,
            source_kind=artifact.source_kind,
            artifact_name=artifact.artifact_name,
            sha256=artifact.sha256,
            byte_length=1,
            raw_artifact_path="x",
            member_digests={},
            num_has_segments=True,
            ingested_at=NOW,
            created_at=NOW,
        )
    )
    with pytest.raises(sa.exc.IntegrityError, match="uq_financial_fact_artifact_content"):
        db_session.flush()
    db_session.rollback()


# ── availability: none is claimed ─────────────────────────────────────────────────────────


def test_no_fact_table_carries_an_availability_or_observation_time() -> None:
    for model in (FinancialFact, FinancialFactSubmission, FinancialFactArtifact):
        columns = {c.name for c in model.__table__.columns}
        assert not columns & {
            "observed_at",
            "observed_at_basis",
            "available_at",
            "published_at",
            "first_public_at",
        }, model


def test_sec_filing_acceptance_remains_an_unapproved_availability_basis() -> None:
    assert AvailabilityBasis.SEC_FILING_ACCEPTANCE not in APPROVED_AVAILABILITY_BASES
    assert frozenset({AvailabilityBasis.SOURCE_PUBLICATION}) == APPROVED_AVAILABILITY_BASES


def test_the_trace_keeps_filing_acceptance_and_artifact_ingestion_as_separate_facts(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(db_session, tmp_path, build_package(tmp_path / "2024q2.zip"))
    trace = FinancialFactRepository(db_session).trace(_facts(db_session, ACC_10Q)[0].id)
    assert trace is not None
    assert trace.filing_accepted_at == datetime(2024, 5, 3, 20, 30, 22, tzinfo=UTC)
    assert trace.artifact_ingested_at == NOW
    assert trace.filing_accepted_at != trace.artifact_ingested_at
    assert not hasattr(trace, "available_at")
    assert FinancialFactRepository(db_session).trace(uuid.uuid4()) is None


def test_no_secret_or_contact_is_stored_anywhere(
    db_session: Session,
    tmp_path: Path,
    filings: dict[str, SecFiling],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QF_SEC_USER_AGENT", "QuantForgeTest secret-contact@example.invalid")
    _ingest(db_session, tmp_path, build_package(tmp_path / "2024q2.zip"))
    blob = json.dumps(
        [
            [r.request_parameters, r.error_message, r.raw_artifact_path]
            for r in db_session.query(IngestionRun)
        ]
    ) + str(
        [(a.raw_artifact_path, a.member_digests) for a in db_session.query(FinancialFactArtifact)]
    )
    assert "secret-contact" not in blob
    for path in (tmp_path / "raw").rglob("*.json"):
        assert "secret-contact" not in path.read_text()


def test_a_fact_cannot_point_at_a_submission_of_a_different_filing(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(db_session, tmp_path, build_package(tmp_path / "2024q2.zip"))
    fact = _facts(db_session, ACC_10Q)[0]
    other_submission = (
        db_session.query(FinancialFactSubmission).filter_by(sec_filing_id=filings[ACC_10K].id).one()
    )
    db_session.add(
        FinancialFact(
            sec_filing_id=fact.sec_filing_id,  # the 10-Q ...
            submission_id=other_submission.id,  # ... but the 10-K's submission record
            tag="Mismatch",
            version=GAAP,
            period_end=date(2024, 3, 31),
            duration_quarters=0,
            unit="USD",
            value=None,
            ingested_at=NOW,
            created_at=NOW,
        )
    )
    with pytest.raises(sa.exc.IntegrityError, match="fk_financial_fact_submission_same_filing"):
        db_session.flush()
    db_session.rollback()


def test_non_ascii_coregistrant_text_round_trips_and_is_bounded_in_bytes(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    row = num_row(
        adsh=ACC_10Q,
        tag="Assets",
        version=GAAP,
        ddate="20240331",
        qtrs="0",
        coreg="Société Générale SA",
        value="1.0000",
    )
    _ingest(
        db_session,
        tmp_path,
        build_package(tmp_path / "2024q2.zip", nums=[row]),
        accessions=(ACC_10Q,),
    )
    assert _facts(db_session, ACC_10Q)[0].coreg == "Société Générale SA"


def test_a_failed_run_still_records_how_many_rows_were_read(
    db_session: Session,
    tmp_path: Path,
    filings: dict[str, SecFiling],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ingestion_module, "FACT_BATCH_SIZE", 2)
    rows = [
        num_row(
            adsh=ACC_10Q,
            tag=f"Concept{i}",
            version=GAAP,
            ddate="20240331",
            qtrs="0",
            value=f"{i}.0000",
        )
        for i in range(4)
    ]
    rows.append(dict(rows[0], value="999.0000"))
    error = _failed_ingest(
        db_session,
        tmp_path,
        build_package(tmp_path / "2024q2.zip", nums=rows),
        accessions=(ACC_10Q,),
    )
    assert isinstance(error, FinancialFactConflictError)
    run = db_session.query(IngestionRun).one()
    assert run.records_received == 5
    assert run.records_written == 0


def test_a_rejected_row_stops_further_writes_and_nothing_is_kept(
    db_session: Session,
    tmp_path: Path,
    filings: dict[str, SecFiling],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ingestion_module, "FACT_BATCH_SIZE", 2)
    calls: list[int] = []
    real = FinancialFactIngestionService._write_batch

    def counting(self: Any, batch: list[Any], *args: Any) -> tuple[int, int]:
        calls.append(len(batch))
        return real(self, batch, *args)

    monkeypatch.setattr(FinancialFactIngestionService, "_write_batch", counting)
    rows = [
        num_row(adsh=ACC_10Q, tag="Bad", version=GAAP, ddate="20240331", qtrs="0", value="NaN"),
        *[
            num_row(
                adsh=ACC_10Q,
                tag=f"Concept{i}",
                version=GAAP,
                ddate="20240331",
                qtrs="0",
                value=f"{i}.0000",
            )
            for i in range(10)
        ],
    ]
    error = _failed_ingest(
        db_session,
        tmp_path,
        build_package(tmp_path / "2024q2.zip", nums=rows),
        accessions=(ACC_10Q,),
    )
    assert isinstance(error, FsdsRowsRejectedError)
    assert calls == [], "the first row was rejected, so no batch was ever written"
    assert db_session.query(FinancialFact).count() == 0


def test_a_rejection_on_the_last_rows_is_not_masked_by_a_conflict_in_the_tail_batch(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(db_session, tmp_path, build_package(tmp_path / "2024q2.zip"))
    rows = [dict(r) for r in default_nums()]
    for row in rows:
        if (
            row["adsh"] == ACC_10Q
            and row["tag"] == EPS
            and row["qtrs"] == "1"
            and row["version"] == GAAP
        ):
            row["value"] = "1.9999"  # would be a conflict with the stored 1.5300 ...
    rows.append(  # ... but the stream's final selected row is malformed
        num_row(adsh=ACC_10Q, tag="Bad", version=GAAP, ddate="20240331", qtrs="0", value="NaN")
    )
    error = _failed_ingest(
        db_session, tmp_path, build_package(tmp_path / "2024q3.zip", nums=rows), name="2024q3"
    )
    assert isinstance(error, FsdsRowsRejectedError), "the rejection, not a masking conflict"
    assert db_session.query(FinancialFact).count() == 14


def test_a_rejection_still_reports_complete_read_and_rejected_counts(
    db_session: Session,
    tmp_path: Path,
    filings: dict[str, SecFiling],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ingestion_module, "FACT_BATCH_SIZE", 2)
    rows = [
        num_row(adsh=ACC_10Q, tag="Bad1", version=GAAP, ddate="20240331", qtrs="0", value="NaN"),
        *[
            num_row(
                adsh=ACC_10Q,
                tag=f"Concept{i}",
                version=GAAP,
                ddate="20240331",
                qtrs="0",
                value=f"{i}.0000",
            )
            for i in range(6)
        ],
        num_row(adsh=ACC_10Q, tag="Bad2", version=GAAP, ddate="20240331", qtrs="0", value="1e5"),
    ]
    error = _failed_ingest(
        db_session,
        tmp_path,
        build_package(tmp_path / "2024q2.zip", nums=rows),
        accessions=(ACC_10Q,),
    )
    assert isinstance(error, FsdsRowsRejectedError)
    assert error.count == 2
    run = db_session.query(IngestionRun).one()
    assert (run.records_received, run.records_failed, run.records_written) == (8, 2, 0)
