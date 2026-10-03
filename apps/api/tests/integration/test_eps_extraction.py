"""EPS extraction over facts ingested from FSDS-shaped packages, against real PostgreSQL.

The pure rules are covered exhaustively in ``tests/test_eps_selection.py``; this proves the
adapter reads the right stored rows for ONE accession, with the 10.6F quarter / YTD / amendment /
comparative / dimension data as the source, and writes nothing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from tests.fsds_fixture import (
    ACC_10K,
    ACC_10Q,
    ACC_10QA,
    CIK_MAIN,
    build_package,
    default_nums,
    num_row,
)

from quantforge.financials import (
    EpsFactObservation,
    EpsKind,
    EpsPeriodClass,
    EpsStatus,
    extract_eps_observation,
)
from quantforge.ingestion.financial_facts import FinancialFactIngestionService
from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.ingestion.sources import get_or_create_data_source
from quantforge.models.company import Company
from quantforge.models.enums import ProviderType, SecFormType
from quantforge.models.filing import SecFiling
from quantforge.models.financial_fact import (
    FinancialFact,
    FinancialFactArtifact,
    FinancialFactSubmission,
)
from quantforge.repositories.financial_fact import FinancialFactRepository

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
GAAP = "us-gaap/2024"
EPS = "EarningsPerShareDiluted"
CLASS_A = "StatementClassOfStockAxis=CommonClassAMember;"
USD_PER_SHARE = "USD/shares"

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


def _eps(adsh: str, ddate: str, qtrs: str, value: str, **extra: str) -> dict[str, str]:
    return num_row(
        adsh=adsh,
        tag=extra.pop("tag", EPS),
        version=extra.pop("version", GAAP),
        ddate=ddate,
        qtrs=qtrs,
        uom=extra.pop("uom", USD_PER_SHARE),
        value=value,
        **extra,
    )


#: One clean filing set: a 10-Q (quarter, YTD, a prior-year comparative, a class-A variant and a
#: co-registrant variant), a 10-K (annual), and a 10-Q/A restating the quarter.
def _nums() -> list[dict[str, str]]:
    return [
        _eps(ACC_10Q, "20240331", "1", "1.5300"),
        _eps(ACC_10Q, "20240331", "2", "3.9100"),
        _eps(ACC_10Q, "20230331", "1", "1.5200"),
        _eps(ACC_10Q, "20240331", "1", "9.9900", segments=CLASS_A),
        _eps(ACC_10Q, "20240331", "1", "8.8800", coreg="GuarantorSubsidiaryMember"),
        _eps(ACC_10K, "20230930", "4", "6.1300", version="us-gaap/2023"),
        _eps(ACC_10QA, "20240331", "1", "1.5400"),
        # not EPS: must never be read
        num_row(adsh=ACC_10Q, tag="NetIncomeLoss", version=GAAP, ddate="20240331", qtrs="1"),
    ]


@pytest.fixture
def company(db_session: Session) -> Company:
    row = Company(legal_name="Example Filer Inc.", display_name="Example Filer", cik=CIK_MAIN)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def filings(db_session: Session, company: Company) -> dict[str, SecFiling]:
    source = get_or_create_data_source(
        db_session, name="sec-edgar", provider_type=ProviderType.REGULATORY_FILING
    )
    rows: dict[str, SecFiling] = {}
    for accession, (form, filing_date, accepted_at) in FILINGS.items():
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
        db_session.add(row)
        rows[accession] = row
    db_session.commit()
    return rows


def _ingest(
    session: Session,
    tmp_path: Path,
    nums: list[dict[str, str]],
    accessions: tuple[str, ...] = (ACC_10Q, ACC_10K, ACC_10QA),
) -> None:
    package = build_package(tmp_path / "2024q2.zip", nums=nums)
    FinancialFactIngestionService(
        session=session, store=RawResponseStore(root=tmp_path / "raw"), now=NOW
    ).ingest_fsds_package(package, package_name="2024q2", accessions=accessions)
    session.commit()


def _extract(
    session: Session,
    accession: str,
    period: EpsPeriodClass = EpsPeriodClass.QUARTER,
    kind: EpsKind = EpsKind.DILUTED,
) -> EpsFactObservation:
    return extract_eps_observation(session, accession, period_class=period, eps_kind=kind)


def test_a_simple_quarter_is_selected_from_stored_facts_with_traceable_provenance(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(db_session, tmp_path, _nums())

    result = _extract(db_session, ACC_10Q)

    assert result.status is EpsStatus.AVAILABLE
    assert result.value == Decimal("1.5300")
    assert result.form is SecFormType.FORM_10Q and result.company_cik == CIK_MAIN
    assert (result.duration_quarters, result.period_end) == (1, date(2024, 3, 31))
    assert (result.segments, result.coreg) == ("", "")
    assert result.fact_id is not None
    trace = FinancialFactRepository(db_session).trace(result.fact_id)
    assert trace is not None and trace.accession_number == ACC_10Q
    assert trace.artifact_name == "2024q2" and trace.member_digests["num.txt"]


def test_the_ytd_prior_year_dimensional_and_coreg_rows_are_never_selected(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(db_session, tmp_path, _nums())
    result = _extract(db_session, ACC_10Q)
    # 3.91 (YTD), 1.52 (comparative), 9.99 (class A) and 8.88 (co-registrant) all exist in the DB.
    assert db_session.query(FinancialFact).filter(FinancialFact.tag == EPS).count() == 5 + 2
    assert result.value == Decimal("1.5300")


def test_an_annual_10k_is_selected_and_a_10k_quarter_is_unsupported(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(db_session, tmp_path, _nums())

    annual = _extract(db_session, ACC_10K, EpsPeriodClass.ANNUAL)
    assert annual.status is EpsStatus.AVAILABLE and annual.value == Decimal("6.1300")
    assert annual.taxonomy_version == "us-gaap/2023", "no single taxonomy year is assumed"
    assert _extract(db_session, ACC_10K, EpsPeriodClass.QUARTER).status is (
        EpsStatus.UNSUPPORTED_FORM_PERIOD
    )
    assert _extract(db_session, ACC_10Q, EpsPeriodClass.ANNUAL).status is (
        EpsStatus.UNSUPPORTED_FORM_PERIOD
    )


def test_original_and_amendment_are_independent_and_neither_supersedes(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(db_session, tmp_path, _nums())

    original = _extract(db_session, ACC_10Q)
    amendment = _extract(db_session, ACC_10QA)

    assert original.value == Decimal("1.5300") and original.form is SecFormType.FORM_10Q
    assert amendment.value == Decimal("1.5400") and amendment.form is SecFormType.FORM_10Q_A
    assert original.fact_id != amendment.fact_id
    assert _extract(db_session, ACC_10Q).value == Decimal("1.5300"), "unchanged by the amendment"


def test_a_stored_filing_without_ingested_facts_is_not_ingested_not_missing(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(db_session, tmp_path, _nums(), accessions=(ACC_10Q,))

    not_ingested = _extract(db_session, ACC_10K, EpsPeriodClass.ANNUAL)
    assert not_ingested.status is EpsStatus.NOT_INGESTED
    assert not_ingested.form is SecFormType.FORM_10K, "the stored filing's form is still reported"
    unknown = _extract(db_session, "0000111111-24-000001")
    assert unknown.status is EpsStatus.NOT_INGESTED and unknown.form is None


def test_an_ingested_accession_with_no_eps_fact_is_missing(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(
        db_session,
        tmp_path,
        [num_row(adsh=ACC_10Q, tag="NetIncomeLoss", version=GAAP, ddate="20240331", qtrs="1")],
        accessions=(ACC_10Q,),
    )
    assert _extract(db_session, ACC_10Q).status is EpsStatus.MISSING


def test_two_taxonomy_releases_in_one_accession_are_ambiguous(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    # The 10.6F default fixture states the same quarter under us-gaap/2024 AND us-gaap/2023.
    _ingest(db_session, tmp_path, default_nums())
    result = _extract(db_session, ACC_10Q)
    assert result.status is EpsStatus.AMBIGUOUS
    assert "us-gaap/2023, us-gaap/2024" in result.reason


def test_dimensional_only_coreg_only_and_wrong_unit_and_null_from_the_database(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(
        db_session,
        tmp_path,
        [
            _eps(ACC_10Q, "20240331", "1", "9.9900", segments=CLASS_A),
            _eps(ACC_10K, "20230930", "4", "8.8800", coreg="GuarantorSubsidiaryMember"),
            _eps(ACC_10QA, "20240331", "1", "153.0000", uom="USD"),
        ],
    )
    assert _extract(db_session, ACC_10Q).status is EpsStatus.DIMENSIONAL_ONLY
    assert _extract(db_session, ACC_10K, EpsPeriodClass.ANNUAL).status is EpsStatus.COREG_ONLY
    assert _extract(db_session, ACC_10QA).status is EpsStatus.WRONG_UNIT


def test_a_null_source_value_is_null_value_not_zero(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(db_session, tmp_path, [_eps(ACC_10Q, "20240331", "1", "")], accessions=(ACC_10Q,))
    stored = db_session.scalars(sa.select(FinancialFact.value)).all()
    assert stored == [None]
    result = _extract(db_session, ACC_10Q)
    assert result.status is EpsStatus.NULL_VALUE and result.value is None
    assert result.fact_id is not None


def test_an_extension_with_the_standard_name_is_extension_only(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(
        db_session,
        tmp_path,
        [_eps(ACC_10Q, "20240331", "1", "9.9900", version=ACC_10Q)],
        accessions=(ACC_10Q,),
    )
    assert _extract(db_session, ACC_10Q).status is EpsStatus.EXTENSION_ONLY


def test_basic_and_diluted_is_only_returned_when_explicitly_requested(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(
        db_session,
        tmp_path,
        [_eps(ACC_10Q, "20240331", "1", "2.0000", tag="EarningsPerShareBasicAndDiluted")],
        accessions=(ACC_10Q,),
    )
    assert _extract(db_session, ACC_10Q).status is EpsStatus.MISSING
    combined = _extract(db_session, ACC_10Q, kind=EpsKind.BASIC_AND_DILUTED)
    assert combined.status is EpsStatus.AVAILABLE and combined.value == Decimal("2.0000")
    assert combined.eps_kind is EpsKind.BASIC_AND_DILUTED


def test_basic_and_continuing_operations_eps_are_not_substituted(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(
        db_session,
        tmp_path,
        [
            _eps(ACC_10Q, "20240331", "1", "1.6000", tag="EarningsPerShareBasic"),
            _eps(
                ACC_10Q,
                "20240331",
                "1",
                "1.7000",
                tag="IncomeLossFromContinuingOperationsPerDilutedShare",
            ),
        ],
        accessions=(ACC_10Q,),
    )
    assert _extract(db_session, ACC_10Q).status is EpsStatus.MISSING


def test_extraction_writes_nothing_and_reads_no_availability_time(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(db_session, tmp_path, _nums())
    tables = (FinancialFact, FinancialFactSubmission, FinancialFactArtifact, SecFiling)
    before = [db_session.query(table).count() for table in tables]
    # Make the filing's acceptance time nonsensical: selection must not depend on it.
    db_session.execute(sa.update(SecFiling).values(accepted_at=datetime(2099, 1, 1, tzinfo=UTC)))
    db_session.commit()

    result = _extract(db_session, ACC_10Q)

    assert result.value == Decimal("1.5300")
    assert [db_session.query(table).count() for table in tables] == before
    assert not db_session.new and not db_session.dirty and not db_session.deleted


def test_a_malformed_accession_is_rejected_up_front_not_reported_as_a_refusal(
    db_session: Session,
) -> None:
    for bad in ("000032019324000123", " 0000320193-24-000010", "0000320193-24-00001"):
        with pytest.raises(ValueError, match="accession_number must look like"):
            _extract(db_session, bad)


def _store_other_company_filing(session: Session, accession: str) -> SecFiling:
    other = Company(legal_name="Co-registrant Inc.", display_name="Co-registrant", cik="0000555555")
    session.add(other)
    session.commit()
    source = get_or_create_data_source(
        session, name="sec-edgar", provider_type=ProviderType.REGULATORY_FILING
    )
    form, filing_date, accepted_at = FILINGS[accession]
    row = SecFiling(
        company_id=other.id,
        accession_number=accession,
        form=form,
        filing_date=filing_date,
        accepted_at=accepted_at,
        source_id=source.id,
        ingested_at=NOW,
        created_at=NOW,
    )
    session.add(row)
    session.commit()
    return row


def test_a_joint_accession_uses_only_the_filing_that_holds_the_ingested_facts(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _store_other_company_filing(db_session, ACC_10Q)  # same accession under a second company
    _ingest(db_session, tmp_path, _nums())

    result = _extract(db_session, ACC_10Q)

    assert result.status is EpsStatus.AVAILABLE and result.value == Decimal("1.5300")
    assert result.company_cik == CIK_MAIN, "FSDS bound the facts to the registrant's own filing"


def test_a_joint_accession_with_no_ingested_facts_is_not_ingested(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _store_other_company_filing(db_session, ACC_10K)
    _ingest(db_session, tmp_path, _nums(), accessions=(ACC_10Q,))

    result = _extract(db_session, ACC_10K, EpsPeriodClass.ANNUAL)

    assert result.status is EpsStatus.NOT_INGESTED
    assert result.company_cik is None and result.form is None


def test_a_joint_accession_with_facts_under_two_filings_is_ambiguous(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    other = _store_other_company_filing(db_session, ACC_10Q)
    _ingest(db_session, tmp_path, _nums())
    artifact_id = db_session.scalars(sa.select(FinancialFactArtifact.id)).one()
    db_session.add(
        FinancialFactSubmission(
            artifact_id=artifact_id,
            sec_filing_id=other.id,
            period_end=date(2024, 3, 31),
            coregistrant_count=1,
            created_at=NOW,
        )
    )
    db_session.commit()

    result = _extract(db_session, ACC_10Q)

    assert result.status is EpsStatus.AMBIGUOUS
    assert "not unique" in result.reason and "ingested submission" in result.reason


def test_a_wrong_unit_entity_wide_fact_is_not_masked_by_a_dimensional_variant(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(
        db_session,
        tmp_path,
        [
            _eps(ACC_10Q, "20240331", "1", "153.0000", uom="USD"),
            _eps(ACC_10Q, "20240331", "1", "9.9900", segments=CLASS_A),
        ],
        accessions=(ACC_10Q,),
    )
    assert _extract(db_session, ACC_10Q).status is EpsStatus.WRONG_UNIT


def test_a_fact_bound_to_another_filing_is_refused_loudly(
    db_session: Session,
    tmp_path: Path,
    filings: dict[str, SecFiling],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ingest(db_session, tmp_path, _nums())
    amendment_fact = FinancialFactRepository(db_session).facts_for_filing_tag(
        filings[ACC_10QA].id, EPS
    )
    assert amendment_fact, "the amendment's own EPS fact exists"
    monkeypatch.setattr(
        FinancialFactRepository, "facts_for_filing_tag", lambda *_args, **_kw: amendment_fact
    )
    with pytest.raises(RuntimeError, match="bound to another filing"):
        _extract(db_session, ACC_10Q)
