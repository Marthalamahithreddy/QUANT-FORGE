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
    ComparativeEpsObservation,
    ComparativeEpsStatus,
    EpsFactObservation,
    EpsKind,
    EpsPeriodClass,
    EpsStatus,
    extract_comparative_eps_observation,
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
USD_UNIT = "USD"

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
        uom=extra.pop("uom", USD_UNIT),
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
            _eps(ACC_10QA, "20240331", "1", "153.0000", uom="CAD"),
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
            _eps(ACC_10Q, "20240331", "1", "153.0000", uom="CAD"),
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


def test_a_usd_per_shares_unit_is_not_accepted_through_ingestion_and_selection(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    # Real 2026q1 FSDS states the bare currency code; any other spelling fails closed.
    _ingest(
        db_session,
        tmp_path,
        [_eps(ACC_10Q, "20240331", "1", "1.5300", uom="USD/shares")],
        accessions=(ACC_10Q,),
    )
    assert _extract(db_session, ACC_10Q).status is EpsStatus.WRONG_UNIT


# ── same-accession comparative EPS (10.6H) ───────────────────────────────────────────────

C = ComparativeEpsStatus


def _comparative(
    session: Session,
    accession: str,
    period: EpsPeriodClass = EpsPeriodClass.QUARTER,
    kind: EpsKind = EpsKind.DILUTED,
) -> ComparativeEpsObservation:
    return extract_comparative_eps_observation(
        session, accession, period_class=period, eps_kind=kind
    )


def test_a_simple_10q_comparative_is_selected_with_traceable_provenance(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(db_session, tmp_path, _nums())

    result = _comparative(db_session, ACC_10Q)

    assert result.status is C.AVAILABLE
    assert result.comparative_value == Decimal(
        "1.5200"
    )  # the prior-year column: stored, not derived
    assert result.current.value == Decimal("1.5300")
    assert result.comparative_period_end == date(2023, 3, 31) and result.period_month_distance == 12
    assert result.comparative_fact_id is not None
    trace = FinancialFactRepository(db_session).trace(result.comparative_fact_id)
    assert trace is not None and trace.accession_number == ACC_10Q


def test_the_year_to_date_comparative_is_never_the_quarter_comparative(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    nums = [
        *_nums(),
        _eps(ACC_10Q, "20230331", "2", "3.1000"),  # prior-year YTD
    ]
    _ingest(db_session, tmp_path, nums)
    result = _comparative(db_session, ACC_10Q)
    assert result.status is C.AVAILABLE and result.comparative_value == Decimal("1.5200")


def test_a_10k_with_several_earlier_annual_columns_selects_the_one_12_month_ends_before(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    nums = [
        _eps(ACC_10K, "20230930", "4", "6.1300", version="us-gaap/2023"),
        _eps(ACC_10K, "20220930", "4", "5.6700", version="us-gaap/2023"),
        _eps(ACC_10K, "20210930", "4", "3.2800", version="us-gaap/2023"),
    ]
    _ingest(db_session, tmp_path, nums, accessions=(ACC_10K,))
    result = _comparative(db_session, ACC_10K, EpsPeriodClass.ANNUAL)
    assert result.status is C.AVAILABLE and result.comparative_value == Decimal("5.6700")
    assert result.comparative_period_end == date(2022, 9, 30)


def test_52_53_week_and_fiscal_year_change_relationships_are_refused_and_not_missing(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(
        db_session,
        tmp_path,
        [
            _eps(ACC_10Q, "20240331", "1", "1.5300"),
            _eps(
                ACC_10Q, "20230228", "1", "1.5200"
            ),  # 13 months before: not the 12-month-end relationship
            _eps(ACC_10K, "20230930", "4", "6.1300"),
            _eps(ACC_10K, "20220131", "4", "5.0000"),  # 20 months: a fiscal-year change
        ],
        accessions=(ACC_10Q, ACC_10K),
    )
    quarter = _comparative(db_session, ACC_10Q)
    annual = _comparative(db_session, ACC_10K, EpsPeriodClass.ANNUAL)
    assert quarter.status is C.COMPARATIVE_PERIOD_MISMATCH
    assert annual.status is C.COMPARATIVE_PERIOD_MISMATCH


def test_the_real_11_month_52_53_week_pattern_is_refused_end_to_end(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(
        db_session,
        tmp_path,
        [
            _eps(ACC_10QA, "20240331", "1", "1.5400"),
            _eps(ACC_10QA, "20230430", "1", "1.4900"),  # 11 month-ends before: the Costco pattern
        ],
        accessions=(ACC_10QA,),
    )
    result = _comparative(db_session, ACC_10QA)
    assert result.status is C.COMPARATIVE_PERIOD_MISMATCH and result.comparative_fact is None
    assert "11 months before" in result.reason


def test_missing_wrong_unit_null_dimensional_and_ambiguous_comparatives_from_the_database(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(
        db_session,
        tmp_path,
        [
            _eps(ACC_10Q, "20240331", "1", "1.5300"),  # no earlier fact at all
            _eps(ACC_10K, "20230930", "4", "6.1300", version="us-gaap/2023"),
            _eps(ACC_10K, "20220930", "4", "5.6700", version="us-gaap/2023", uom="CAD"),
            _eps(ACC_10QA, "20240331", "1", "1.5400"),
            _eps(ACC_10QA, "20230331", "1", "", version="us-gaap/2023"),
            _eps(ACC_10QA, "20230331", "1", "1.4900", version="us-gaap/2022"),
        ],
    )
    assert _comparative(db_session, ACC_10Q).status is C.COMPARATIVE_MISSING
    assert _comparative(db_session, ACC_10K, EpsPeriodClass.ANNUAL).status is (
        C.COMPARATIVE_WRONG_UNIT
    )
    # a NULL and a numeric candidate at the validated period are ambiguous, not resolved
    assert _comparative(db_session, ACC_10QA).status is C.COMPARATIVE_AMBIGUOUS


def test_a_null_comparative_and_a_dimensional_only_comparative_from_the_database(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(
        db_session,
        tmp_path,
        [
            _eps(ACC_10Q, "20240331", "1", "1.5300"),
            _eps(ACC_10Q, "20230331", "1", ""),
            _eps(ACC_10QA, "20240331", "1", "1.5400"),
            _eps(ACC_10QA, "20230331", "1", "9.9900", segments=CLASS_A),
        ],
    )
    null = _comparative(db_session, ACC_10Q)
    assert null.status is C.COMPARATIVE_NULL_VALUE and null.comparative_fact is not None
    assert null.comparative_value is None
    assert _comparative(db_session, ACC_10QA).status is C.COMPARATIVE_DIMENSIONAL_ONLY


def test_original_and_amendment_have_independent_comparatives_with_no_supersession(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(
        db_session,
        tmp_path,
        [
            _eps(ACC_10Q, "20240331", "1", "1.5300"),
            _eps(ACC_10Q, "20230331", "1", "1.5200"),
            _eps(ACC_10QA, "20240331", "1", "1.5400"),
            _eps(ACC_10QA, "20230331", "1", "1.4900"),
        ],
    )
    assert _comparative(db_session, ACC_10Q).comparative_value == Decimal("1.5200")
    assert _comparative(db_session, ACC_10QA).comparative_value == Decimal("1.4900")


def test_the_comparative_is_never_borrowed_from_another_accession(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    # ACC_10QA states a perfect prior-year fact; ACC_10Q does not: no cross-accession lookup.
    _ingest(
        db_session,
        tmp_path,
        [
            _eps(ACC_10Q, "20240331", "1", "1.5300"),
            _eps(ACC_10QA, "20240331", "1", "1.5400"),
            _eps(ACC_10QA, "20230331", "1", "1.4900"),
        ],
    )
    assert _comparative(db_session, ACC_10Q).status is C.COMPARATIVE_MISSING
    assert _comparative(db_session, ACC_10QA).status is C.AVAILABLE


def test_basic_and_diluted_comparative_needs_its_own_concept_in_the_database(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(
        db_session,
        tmp_path,
        [
            _eps(ACC_10Q, "20240331", "1", "2.0000", tag="EarningsPerShareBasicAndDiluted"),
            _eps(ACC_10Q, "20230331", "1", "1.9000", tag="EarningsPerShareBasicAndDiluted"),
            _eps(ACC_10Q, "20240331", "1", "1.5300"),
            _eps(ACC_10Q, "20230331", "1", "1.5200"),
        ],
        accessions=(ACC_10Q,),
    )
    combined = _comparative(db_session, ACC_10Q, kind=EpsKind.BASIC_AND_DILUTED)
    diluted = _comparative(db_session, ACC_10Q, kind=EpsKind.DILUTED)
    assert combined.comparative_value == Decimal("1.9000")
    assert diluted.comparative_value == Decimal("1.5200")


def test_current_failures_and_unknown_accessions_propagate_as_current_unavailable(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(
        db_session,
        tmp_path,
        [
            _eps(ACC_10Q, "20240331", "1", "1.5300", uom="CAD"),  # wrong-unit current
            _eps(ACC_10Q, "20230331", "1", "1.5200"),
        ],
        accessions=(ACC_10Q,),
    )
    wrong = _comparative(db_session, ACC_10Q)
    assert wrong.status is C.CURRENT_UNAVAILABLE and wrong.current.status is EpsStatus.WRONG_UNIT
    not_ingested = _comparative(db_session, ACC_10K, EpsPeriodClass.ANNUAL)
    assert not_ingested.current.status is EpsStatus.NOT_INGESTED
    unknown = _comparative(db_session, "0000111111-24-000001")
    assert unknown.status is C.CURRENT_UNAVAILABLE and unknown.form is None
    with pytest.raises(ValueError, match="accession_number must look like"):
        _comparative(db_session, "not-an-accession")


def test_comparative_extraction_writes_nothing_and_ignores_acceptance_times(
    db_session: Session, tmp_path: Path, filings: dict[str, SecFiling]
) -> None:
    _ingest(db_session, tmp_path, _nums())
    tables = (FinancialFact, FinancialFactSubmission, FinancialFactArtifact, SecFiling)
    before = [db_session.query(table).count() for table in tables]
    db_session.execute(sa.update(SecFiling).values(accepted_at=datetime(2099, 1, 1, tzinfo=UTC)))
    db_session.commit()

    result = _comparative(db_session, ACC_10Q)

    assert result.comparative_value == Decimal("1.5200")
    assert [db_session.query(table).count() for table in tables] == before
    assert not db_session.new and not db_session.dirty and not db_session.deleted
