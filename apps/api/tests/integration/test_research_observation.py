"""Step 10.8A: the research observation assembler against real PostgreSQL.

The existing layers are already covered exhaustively; these tests prove the COMPOSITION: that each
existing result is carried unchanged, that sections stay independent (a refused PIT section never
blanks the descriptive one), that a filing is linked only when the caller supplies it, and that the
assembler is read-only and provider-free.
"""

from __future__ import annotations

import socket
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from tests.fsds_fixture import (
    ACC_10Q,
    ACC_10QA,
    ACC_OTHER,
    CIK_MAIN,
    CIK_OTHER,
    build_package,
    num_row,
)

from quantforge.financials import (
    ComparativeEpsStatus,
    EpsChangeStatus,
    EpsKind,
    EpsPeriodClass,
    EpsStatus,
)
from quantforge.ingestion.financial_facts import FinancialFactIngestionService
from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.ingestion.sources import get_or_create_data_source
from quantforge.models.benchmark import Benchmark, BenchmarkPriceBar
from quantforge.models.company import Company
from quantforge.models.earnings import EarningsEvent
from quantforge.models.enums import (
    AvailabilityBasis,
    EarningsTiming,
    PriceBasis,
    ProviderType,
    SecFormType,
    SecurityType,
)
from quantforge.models.filing import SecFiling
from quantforge.models.market import DailyPriceBar
from quantforge.models.provenance import DataSource
from quantforge.models.security import Exchange, Listing, Security
from quantforge.observation import EarningsEventNotFoundError, assemble_research_observation
from quantforge.observation.models import (
    AccountingSection,
    AccountingState,
    DescriptiveSection,
    DisclosureEvidence,
    DisclosureEvidenceLevel,
    FilingLinkStatus,
    PeriodVerification,
    PointInTimeSection,
    PointInTimeState,
    RefusalScope,
    SectionState,
)
from quantforge.research.availability import PointInTimeEligibility, ResearchMode
from quantforge.research.benchmark_returns import BenchmarkReturnStatus
from quantforge.research.calendar import AlignmentStatus
from quantforge.research.event_study import analyze_earnings_event
from quantforge.research.returns import ForwardReturnStatus

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
GAAP = "us-gaap/2024"
ACC_8K = "0000320193-24-000030"
ACC_8K_PLAIN = "0000320193-24-000031"
HORIZONS = [1, 2]


def _eps(adsh: str, ddate: str, qtrs: str, value: str) -> dict[str, str]:
    return num_row(
        adsh=adsh,
        tag="EarningsPerShareDiluted",
        version=GAAP,
        ddate=ddate,
        qtrs=qtrs,
        uom="USD",
        value=value,
    )


@pytest.fixture
def market(db_session: Session) -> dict[str, object]:
    """One company (with a CIK), listing, stock bars, a benchmark with levels and an event."""
    stock_source = DataSource(
        name="obs-stock-vendor", provider_type=ProviderType.MARKET_DATA_VENDOR
    )
    bench_source = DataSource(
        name="obs-bench-vendor", provider_type=ProviderType.MARKET_DATA_VENDOR
    )
    company = Company(legal_name="Observation Co", display_name="Observation Co", cik=CIK_MAIN)
    exchange = Exchange(mic="XNYS", name="Observation Exchange", timezone="America/New_York")
    db_session.add_all([stock_source, bench_source, company, exchange])
    db_session.commit()
    security = Security(
        company_id=company.id, security_type=SecurityType.EQUITY, name="Shares", currency="USD"
    )
    benchmark = Benchmark(name="Observation Index", symbol="OBSI", currency="USD")
    db_session.add_all([security, benchmark])
    db_session.commit()
    listing = Listing(
        security_id=security.id,
        exchange_id=exchange.id,
        ticker="OBSV",
        valid_from=date(2018, 1, 2),
        is_primary=True,
    )
    db_session.add(listing)
    db_session.commit()
    sessions: list[date] = []
    day = date(2026, 3, 2)
    while len(sessions) < 4:
        if day.weekday() < 5:
            sessions.append(day)
        day += timedelta(days=1)
    for index, trading_date in enumerate(sessions):
        close = Decimal(100 + 10 * index)
        db_session.add(
            DailyPriceBar(
                price_basis=PriceBasis.SPLIT_ADJUSTED,
                listing_id=listing.id,
                trading_date=trading_date,
                open=close,
                high=close,
                low=close,
                close=close,
                source_id=stock_source.id,
            )
        )
        db_session.add(
            BenchmarkPriceBar(
                price_basis=PriceBasis.SPLIT_ADJUSTED,
                benchmark_id=benchmark.id,
                trading_date=trading_date,
                close=Decimal(200 + 5 * index),
                source_id=bench_source.id,
            )
        )
    db_session.commit()
    return {
        "company": company,
        "benchmark": benchmark,
        "sessions": sessions,
        "stock_source": stock_source,
    }


def _event(db_session: Session, market: dict[str, object], **overrides: object) -> EarningsEvent:
    company = market["company"]
    sessions = market["sessions"]
    assert isinstance(company, Company)
    assert isinstance(sessions, list)
    fields: dict[str, object] = {
        "company_id": company.id,
        "fiscal_year": 2026,
        "fiscal_quarter": 1,
        "event_date": sessions[0],
        "timing": EarningsTiming.BEFORE_MARKET,
        "reported_eps": Decimal("1.2000"),
        "estimated_eps": Decimal("1.0000"),
        "source_id": market["stock_source"].id,  # type: ignore[attr-defined]
    }
    fields.update(overrides)
    if fields.get("observed_at") is not None:
        fields.setdefault("observed_at_basis", AvailabilityBasis.SOURCE_PUBLICATION)
    row = EarningsEvent(**fields)
    db_session.add(row)
    db_session.commit()
    return row


def _filing(
    db_session: Session,
    company: Company,
    accession: str,
    form: SecFormType,
    items: str | None = None,
) -> SecFiling:
    filing_date = date(2024, 5, 17) if form is SecFormType.FORM_10Q_A else date(2024, 5, 3)
    source = get_or_create_data_source(
        db_session, name="sec-edgar", provider_type=ProviderType.REGULATORY_FILING
    )
    row = SecFiling(
        company_id=company.id,
        accession_number=accession,
        form=form,
        filing_date=filing_date,
        accepted_at=datetime(2024, 5, 3, 20, 30, 22, tzinfo=UTC),
        items=items,
        source_id=source.id,
        ingested_at=NOW,
        created_at=NOW,
    )
    db_session.add(row)
    db_session.commit()
    return row


def _ingest_facts(session: Session, tmp_path: Path, accessions: tuple[str, ...]) -> None:
    nums = [
        _eps(ACC_10Q, "20240331", "1", "1.5300"),
        _eps(ACC_10Q, "20230331", "1", "1.5200"),
        _eps(ACC_10QA, "20240331", "1", "1.5400"),
        _eps(ACC_10QA, "20230331", "1", "1.5200"),
    ]
    package = build_package(tmp_path / "2024q2.zip", nums=nums)
    FinancialFactIngestionService(
        session=session, store=RawResponseStore(root=tmp_path / "raw"), now=NOW
    ).ingest_fsds_package(package, package_name="2024q2", accessions=accessions)
    session.commit()


def _assemble(session: Session, event: EarningsEvent, market: dict[str, object], **kw: object):  # type: ignore[no-untyped-def]
    benchmark = market["benchmark"]
    assert isinstance(benchmark, Benchmark)
    options: dict[str, object] = {"horizons": HORIZONS, "benchmark_id": benchmark.id}
    options.update(kw)
    return assemble_research_observation(session, event.id, **options)  # type: ignore[arg-type]


QUARTER: dict[str, Any] = {"eps_period_class": EpsPeriodClass.QUARTER, "eps_kind": EpsKind.DILUTED}


def test_full_observation_carries_every_existing_result_and_pit_refusal_does_not_blank_descriptive(
    db_session: Session, market: dict[str, object], tmp_path: Path
) -> None:
    company = market["company"]
    assert isinstance(company, Company)
    _filing(db_session, company, ACC_10Q, SecFormType.FORM_10Q)
    _ingest_facts(db_session, tmp_path, (ACC_10Q,))
    event = _event(db_session, market)  # observed_at NULL: PIT must refuse

    observation = _assemble(db_session, event, market, filing_accession=ACC_10Q, **QUARTER)

    # descriptive: exactly what the existing engine returns for the same pinned inputs
    expected = analyze_earnings_event(
        db_session,
        event,
        mode=ResearchMode.DESCRIPTIVE,
        horizons=HORIZONS,
        benchmark_id=observation.parameters.benchmark_id,
    )
    study = observation.descriptive.study
    assert study == expected
    assert observation.descriptive.state is SectionState.AVAILABLE
    by_horizon = {fr.horizon: fr.return_value for fr in study.stock_returns.forward_returns}
    assert by_horizon == {1: Decimal("0.1"), 2: Decimal("0.2")}
    assert [a.abnormal_return for a in study.abnormal_returns] == [
        Decimal("0.1") - Decimal("0.025"),
        Decimal("0.2") - Decimal("0.05"),
    ]
    assert study.eps_surprise.absolute_surprise == Decimal("0.2000")

    # PIT refused, observation still returned normally
    assert observation.point_in_time.state is PointInTimeState.REFUSED
    assert observation.point_in_time.eligibility is (
        PointInTimeEligibility.INELIGIBLE_NO_AVAILABILITY_EVIDENCE
    )
    assert observation.point_in_time.study is None
    pit_alignment = analyze_earnings_event(
        db_session, event, mode=ResearchMode.POINT_IN_TIME, horizons=HORIZONS
    ).alignment.session_alignment
    assert pit_alignment is not None
    assert pit_alignment.status is AlignmentStatus.NOT_POINT_IN_TIME_ELIGIBLE

    # accounting: the existing stack's values, unchanged and Decimal
    change = observation.accounting.change
    assert observation.accounting.state is AccountingState.EVALUATED
    assert change is not None and change.status is EpsChangeStatus.AVAILABLE
    assert change.comparative.current.value == Decimal("1.5300")
    assert change.comparative.comparative_fact is not None
    assert change.comparative.comparative_fact.value == Decimal("1.5200")
    assert change.eps_change == Decimal("0.0100")
    assert isinstance(change.eps_change, Decimal)
    assert not isinstance(study.stock_returns.forward_returns[0].return_value, float)

    # link, evidence, identity, parameters, provenance
    assert observation.filing_link.status is FilingLinkStatus.CALLER_SUPPLIED_CIK_CONSISTENT
    assert observation.filing_link.period_verification is PeriodVerification.PERIOD_UNVERIFIED
    assert observation.disclosure_evidence.level is DisclosureEvidenceLevel.D0_FILING_EXISTS
    assert observation.identity.company_cik == CIK_MAIN
    assert observation.identity.listing_resolution.listing_id is not None
    assert observation.parameters.horizons == (1, 2)
    assert observation.provenance.event_fingerprint == event.fingerprint
    assert [role for role, _ in observation.provenance.accounting_artifact_sha256] == [
        "current",
        "comparative",
    ]
    assert "OBSERVATION_NOT_PERSISTED" in observation.provenance.known_gaps
    codes = {(r.scope, r.code) for r in observation.refusals}
    assert codes == {
        (RefusalScope.POINT_IN_TIME, "INELIGIBLE_NO_AVAILABILITY_EVIDENCE"),
    }


def test_an_eligible_pit_event_carries_the_pit_mode_study(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _event(db_session, market, observed_at=datetime(2026, 3, 2, 11, 0, tzinfo=UTC))
    observation = _assemble(db_session, event, market)
    assert observation.point_in_time.state is PointInTimeState.EVIDENCE_ELIGIBLE
    assert observation.point_in_time.study is not None
    assert observation.point_in_time.study.research_mode is ResearchMode.POINT_IN_TIME
    assert observation.descriptive.study.research_mode is ResearchMode.DESCRIPTIVE


def test_defaults_are_the_existing_research_defaults_and_visible(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _event(db_session, market)
    observation = assemble_research_observation(db_session, event.id)
    assert observation.parameters.horizons == (1, 5, 20)
    assert observation.parameters.benchmark_id is None
    assert observation.parameters.price_basis is PriceBasis.SPLIT_ADJUSTED
    assert not hasattr(observation.parameters, "modes"), "both modes are always evaluated"


def test_no_filing_means_not_linked_and_market_research_still_works(
    db_session: Session, market: dict[str, object], tmp_path: Path
) -> None:
    company = market["company"]
    assert isinstance(company, Company)
    # a perfectly good filing exists, but is never auto-linked
    _filing(db_session, company, ACC_10Q, SecFormType.FORM_10Q)
    _ingest_facts(db_session, tmp_path, (ACC_10Q,))
    event = _event(db_session, market)

    observation = _assemble(db_session, event, market)

    assert observation.filing_link.status is FilingLinkStatus.NOT_LINKED
    assert observation.filing_link.accession_number is None
    assert observation.filing_link.company_cik == CIK_MAIN
    assert observation.accounting.state is AccountingState.NOT_REQUESTED
    assert observation.accounting.change is None
    assert observation.disclosure_evidence.level is DisclosureEvidenceLevel.NONE
    assert observation.descriptive.state is SectionState.AVAILABLE


def test_a_cik_mismatch_refuses_the_filing_but_not_the_observation(
    db_session: Session, market: dict[str, object]
) -> None:
    other = Company(legal_name="Other Co", display_name="Other Co", cik=CIK_OTHER)
    db_session.add(other)
    db_session.commit()
    _filing(db_session, other, ACC_OTHER, SecFormType.FORM_10Q)
    event = _event(db_session, market)

    observation = _assemble(db_session, event, market, filing_accession=ACC_OTHER, **QUARTER)

    assert observation.filing_link.status is FilingLinkStatus.CIK_MISMATCH_REFUSED
    assert observation.filing_link.form is None, "a foreign filing is not described"
    assert observation.accounting.state is AccountingState.LINK_REFUSED
    assert observation.accounting.change is None
    assert observation.disclosure_evidence.level is DisclosureEvidenceLevel.NONE
    assert observation.provenance.filing_source_id is None
    assert observation.descriptive.state is SectionState.AVAILABLE
    assert (RefusalScope.FILING_LINK, "CIK_MISMATCH_REFUSED") in {
        (r.scope, r.code) for r in observation.refusals
    }


def test_a_joint_accession_links_only_the_event_companys_own_row(
    db_session: Session, market: dict[str, object], tmp_path: Path
) -> None:
    company = market["company"]
    assert isinstance(company, Company)
    other = Company(legal_name="Co-registrant", display_name="Co-registrant", cik=CIK_OTHER)
    db_session.add(other)
    db_session.commit()
    _filing(db_session, other, ACC_10Q, SecFormType.FORM_10Q)  # same accession, other registrant
    _filing(db_session, company, ACC_10Q, SecFormType.FORM_10Q)
    _ingest_facts(db_session, tmp_path, (ACC_10Q,))
    event = _event(db_session, market)

    observation = _assemble(db_session, event, market, filing_accession=ACC_10Q, **QUARTER)

    assert observation.filing_link.status is FilingLinkStatus.CALLER_SUPPLIED_CIK_CONSISTENT
    assert observation.accounting.change is not None
    assert observation.accounting.change.comparative.company_cik == CIK_MAIN


def test_an_unstored_accession_is_refused_and_never_fetched(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _event(db_session, market)
    observation = _assemble(
        db_session, event, market, filing_accession="0000111111-24-000001", **QUARTER
    )
    assert observation.filing_link.status is FilingLinkStatus.FILING_NOT_STORED
    assert observation.accounting.state is AccountingState.LINK_REFUSED


def test_an_amendment_accession_is_used_exactly_never_replaced_by_the_original(
    db_session: Session, market: dict[str, object], tmp_path: Path
) -> None:
    company = market["company"]
    assert isinstance(company, Company)
    _filing(db_session, company, ACC_10Q, SecFormType.FORM_10Q)
    _filing(db_session, company, ACC_10QA, SecFormType.FORM_10Q_A)
    _ingest_facts(db_session, tmp_path, (ACC_10Q, ACC_10QA))
    event = _event(db_session, market)

    amended = _assemble(db_session, event, market, filing_accession=ACC_10QA, **QUARTER)
    original = _assemble(db_session, event, market, filing_accession=ACC_10Q, **QUARTER)

    assert amended.filing_link.form is SecFormType.FORM_10Q_A
    assert amended.accounting.change is not None
    assert amended.accounting.change.comparative.current.value == Decimal("1.5400")
    assert amended.accounting.change.comparative.accession_number == ACC_10QA
    assert original.accounting.change is not None
    assert original.accounting.change.comparative.current.value == Decimal("1.5300")


def test_d0_and_d1_are_metadata_categories_with_no_time(
    db_session: Session, market: dict[str, object]
) -> None:
    company = market["company"]
    assert isinstance(company, Company)
    _filing(db_session, company, ACC_8K, SecFormType.FORM_8K, items="2.02,9.01")
    _filing(db_session, company, ACC_8K_PLAIN, SecFormType.FORM_8K, items="5.02,12.02")
    event = _event(db_session, market)

    d1 = _assemble(db_session, event, market, filing_accession=ACC_8K, **QUARTER)
    d0 = _assemble(db_session, event, market, filing_accession=ACC_8K_PLAIN, **QUARTER)

    assert d1.disclosure_evidence.level is DisclosureEvidenceLevel.D1_ITEM_2_02_DECLARED
    assert d0.disclosure_evidence.level is DisclosureEvidenceLevel.D0_FILING_EXISTS, (
        "12.02 is not 2.02"
    )
    # D1 upgrades nothing: no availability, PIT still refused, no timestamp anywhere on it
    assert d1.point_in_time.state is PointInTimeState.REFUSED
    assert d1.event.observed_at is None
    assert set(d1.disclosure_evidence.__slots__) == {
        "level",
        "accession_number",
        "form",
        "items",
    }
    # an 8-K carries no FSDS facts: the existing refusal is carried, the observation succeeds
    assert d1.accounting.change is not None
    assert d1.accounting.change.comparative.current.status is EpsStatus.UNSUPPORTED_FORM_PERIOD


def test_an_accounting_refusal_is_carried_with_its_own_status(
    db_session: Session, market: dict[str, object], tmp_path: Path
) -> None:
    company = market["company"]
    assert isinstance(company, Company)
    _filing(db_session, company, ACC_10Q, SecFormType.FORM_10Q)
    _filing(db_session, company, ACC_10QA, SecFormType.FORM_10Q_A)
    package = build_package(
        tmp_path / "p.zip",
        nums=[_eps(ACC_10QA, "20240331", "1", "1.5400")],  # current only: no comparative
    )
    FinancialFactIngestionService(
        session=db_session, store=RawResponseStore(root=tmp_path / "raw"), now=NOW
    ).ingest_fsds_package(package, package_name="2024q2", accessions=(ACC_10QA,))
    db_session.commit()
    event = _event(db_session, market)

    observation = _assemble(db_session, event, market, filing_accession=ACC_10QA, **QUARTER)

    change = observation.accounting.change
    assert change is not None and change.status is EpsChangeStatus.INPUT_UNAVAILABLE
    assert change.comparative.status is ComparativeEpsStatus.COMPARATIVE_MISSING
    assert (RefusalScope.ACCOUNTING, "COMPARATIVE_MISSING") in {
        (r.scope, r.code) for r in observation.refusals
    }
    assert observation.descriptive.state is SectionState.AVAILABLE


def test_a_benchmark_without_levels_keeps_its_refusal_and_no_fallback_is_used(
    db_session: Session, market: dict[str, object]
) -> None:
    empty = Benchmark(name="Empty Index", symbol="EMPT", currency="USD")
    db_session.add(empty)
    db_session.commit()
    event = _event(db_session, market)

    observation = assemble_research_observation(
        db_session, event.id, horizons=HORIZONS, benchmark_id=empty.id
    )

    study = observation.descriptive.study
    assert study.benchmark_id == empty.id
    assert {b.status for b in study.benchmark_returns} == {
        BenchmarkReturnStatus.MISSING_ANCHOR_PRICE
    }
    assert all(
        fr.status is ForwardReturnStatus.AVAILABLE for fr in study.stock_returns.forward_returns
    ), "stock returns are unaffected"
    assert observation.descriptive.state is SectionState.AVAILABLE
    assert {(r.scope, r.code) for r in observation.refusals} >= {
        (RefusalScope.BENCHMARK, "MISSING_ANCHOR_PRICE"),
        (RefusalScope.POINT_IN_TIME, "INELIGIBLE_NO_AVAILABILITY_EVIDENCE"),
    }


def test_unknown_event_and_unqualified_accession_are_explicit_errors(
    db_session: Session, market: dict[str, object]
) -> None:
    with pytest.raises(EarningsEventNotFoundError):
        assemble_research_observation(db_session, uuid.uuid4())
    event = _event(db_session, market)
    with pytest.raises(ValueError, match="explicit eps_period_class"):
        assemble_research_observation(db_session, event.id, filing_accession=ACC_10Q)
    with pytest.raises(ValueError, match="only meaningful"):
        assemble_research_observation(db_session, event.id, **QUARTER)
    with pytest.raises(ValueError, match="filing_accession must look like"):
        assemble_research_observation(db_session, event.id, filing_accession="nope", **QUARTER)


@contextmanager
def _record_statements(session: Session) -> Iterator[list[str]]:
    statements: list[str] = []

    def capture(_c, _cur, statement, *_rest):  # type: ignore[no-untyped-def]
        statements.append(statement)

    engine = session.get_bind()
    sa.event.listen(engine, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        sa.event.remove(engine, "before_cursor_execute", capture)


def test_assembly_is_read_only_provider_free_and_its_sql_cost_is_recorded(
    db_session: Session,
    market: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company = market["company"]
    assert isinstance(company, Company)
    _filing(db_session, company, ACC_10Q, SecFormType.FORM_10Q)
    _ingest_facts(db_session, tmp_path, (ACC_10Q,))
    refused = _event(db_session, market)
    eligible = _event(
        db_session,
        market,
        fiscal_quarter=2,
        observed_at=datetime(2026, 3, 2, 11, 0, tzinfo=UTC),
    )
    db_session.expire_all()

    def tripwire(*_a: object, **_k: object) -> None:
        raise AssertionError("network access during observation assembly")

    monkeypatch.setattr(socket.socket, "connect", tripwire)
    monkeypatch.setattr(socket, "create_connection", tripwire)

    counts: dict[str, int] = {}
    for name, event in (("pit_refused", refused), ("pit_eligible", eligible)):
        db_session.expire_all()
        with _record_statements(db_session) as statements:
            _assemble(db_session, event, market, filing_accession=ACC_10Q, **QUARTER)
        counts[name] = len(statements)
        assert statements, "the assembler must read"
        # SAVEPOINT/RELEASE are the test harness's own join_transaction_mode, not domain writes
        allowed = ("SELECT", "WITH", "SAVEPOINT", "RELEASE SAVEPOINT")
        writes = [s for s in statements if not s.lstrip().upper().startswith(allowed)]
        assert writes == [], writes
    # evidence for 10.8B / 10.9; a loose bound only catches pathological N+1 growth
    print(f"observation statement counts: {counts}")  # noqa: T201
    assert counts["pit_refused"] < 40
    assert counts["pit_eligible"] > counts["pit_refused"], "dual-mode repeats reads when eligible"
    assert counts["pit_eligible"] < 80


def test_observation_level_invariants_reject_contradictory_assemblies(
    db_session: Session, market: dict[str, object], tmp_path: Path
) -> None:
    company = market["company"]
    assert isinstance(company, Company)
    _filing(db_session, company, ACC_10Q, SecFormType.FORM_10Q)
    _ingest_facts(db_session, tmp_path, (ACC_10Q,))
    event = _event(db_session, market)
    linked = _assemble(db_session, event, market, filing_accession=ACC_10Q, **QUARTER)
    unlinked = _assemble(db_session, event, market)
    none_evidence = DisclosureEvidence(DisclosureEvidenceLevel.NONE, None, None, None)

    # evidence without a consistent link; a consistent link without evidence or accounting
    with pytest.raises(ValueError, match="requires a consistent link"):
        replace(unlinked, disclosure_evidence=linked.disclosure_evidence)
    with pytest.raises(ValueError, match="at least D0"):
        replace(linked, disclosure_evidence=none_evidence)
    with pytest.raises(ValueError, match="evaluates accounting"):
        replace(linked, accounting=AccountingSection(AccountingState.NOT_REQUESTED))
    # accounting state must match the link status
    with pytest.raises(ValueError, match="accounting must be"):
        replace(unlinked, accounting=AccountingSection(AccountingState.LINK_REFUSED))
    # the link must be the requested accession
    with pytest.raises(ValueError, match="requested accession"):
        replace(unlinked, filing_link=linked.filing_link)
    # every section describes the same event
    other = replace(linked.event, event_id=uuid.uuid4())
    with pytest.raises(ValueError, match="same event"):
        replace(linked, event=other)
    # a PIT study must be an ELIGIBLE POINT_IN_TIME study
    with pytest.raises(ValueError, match="ELIGIBLE POINT_IN_TIME"):
        PointInTimeSection(PointInTimeEligibility.ELIGIBLE, linked.descriptive.study)
    # the descriptive section must be DESCRIPTIVE
    pit_event = _event(
        db_session, market, fiscal_quarter=3, observed_at=datetime(2026, 3, 2, 11, 0, tzinfo=UTC)
    )
    eligible = _assemble(db_session, pit_event, market)
    assert eligible.point_in_time.study is not None
    with pytest.raises(ValueError, match="DESCRIPTIVE study"):
        DescriptiveSection(eligible.point_in_time.study)
    with pytest.raises(ValueError, match="different event"):
        replace(linked, point_in_time=eligible.point_in_time)


def test_each_refusal_scope_is_reported_from_the_originating_status(
    db_session: Session, market: dict[str, object]
) -> None:
    company = market["company"]
    assert isinstance(company, Company)
    event = _event(db_session, market, estimated_eps=None)  # vendor estimate missing
    observation = assemble_research_observation(
        db_session, event.id, horizons=[10], security_id=None
    )  # 10 sessions are not stored
    codes = {(r.scope, r.code, r.horizon) for r in observation.refusals}
    assert (RefusalScope.EVENT, "MISSING_ESTIMATED_EPS", None) in codes
    assert (RefusalScope.MARKET, "MISSING_TARGET_PRICE", 10) in codes
    assert observation.descriptive.state is SectionState.UNAVAILABLE

    ghost = Company(legal_name="No Listing Co", display_name="No Listing Co")
    db_session.add(ghost)
    db_session.commit()
    orphan = _event(db_session, {**market, "company": ghost}, fiscal_quarter=4)
    unresolved = assemble_research_observation(db_session, orphan.id, horizons=HORIZONS)
    assert (RefusalScope.MARKET, "NO_LISTING_FOR_DATE") in {
        (r.scope, r.code) for r in unresolved.refusals
    }


def test_pit_study_horizon_refusals_and_a_zero_comparative_percentage_are_reported(
    db_session: Session, market: dict[str, object], tmp_path: Path
) -> None:
    company = market["company"]
    assert isinstance(company, Company)
    _filing(db_session, company, ACC_10Q, SecFormType.FORM_10Q)
    package = build_package(
        tmp_path / "z.zip",
        nums=[
            _eps(ACC_10Q, "20240331", "1", "1.5300"),
            _eps(ACC_10Q, "20230331", "1", "0.0000"),
        ],
    )
    FinancialFactIngestionService(
        session=db_session, store=RawResponseStore(root=tmp_path / "raw"), now=NOW
    ).ingest_fsds_package(package, package_name="2024q2", accessions=(ACC_10Q,))
    db_session.commit()
    event = _event(db_session, market, observed_at=datetime(2026, 3, 2, 11, 0, tzinfo=UTC))

    observation = assemble_research_observation(
        db_session, event.id, horizons=[10], filing_accession=ACC_10Q, **QUARTER
    )

    codes = {(r.scope, r.code) for r in observation.refusals}
    assert observation.point_in_time.state is PointInTimeState.EVIDENCE_ELIGIBLE
    assert (RefusalScope.POINT_IN_TIME, "MISSING_TARGET_PRICE") in codes
    assert (RefusalScope.MARKET, "MISSING_TARGET_PRICE") in codes
    assert (RefusalScope.ACCOUNTING, "ZERO_COMPARATIVE") in codes
    assert observation.accounting.change is not None
    assert observation.accounting.change.eps_change == Decimal("1.5300")


def test_a_company_without_a_cik_cannot_establish_a_consistent_link(
    db_session: Session, market: dict[str, object]
) -> None:
    keyless = Company(legal_name="Keyless Co", display_name="Keyless Co")
    db_session.add(keyless)
    db_session.commit()
    _filing(db_session, keyless, ACC_10Q, SecFormType.FORM_10Q)
    event = _event(db_session, {**market, "company": keyless})
    observation = assemble_research_observation(
        db_session, event.id, horizons=HORIZONS, filing_accession=ACC_10Q, **QUARTER
    )
    assert observation.filing_link.status is FilingLinkStatus.CIK_MISMATCH_REFUSED
    assert observation.accounting.state is AccountingState.LINK_REFUSED


def test_skipping_the_pit_study_when_the_policy_refuses_loses_nothing_the_engine_would_return(
    db_session: Session, market: dict[str, object]
) -> None:
    """The optimisation is semantics-preserving: a full PIT-mode study of a refused event is all
    refusal (no session, no returns, no surprise), so nothing valid is withheld by skipping it."""
    event = _event(db_session, market)  # observed_at NULL
    full = analyze_earnings_event(
        db_session,
        event,
        mode=ResearchMode.POINT_IN_TIME,
        horizons=HORIZONS,
        benchmark_id=market["benchmark"].id,  # type: ignore[attr-defined]
    )
    assert (
        full.point_in_time_eligibility is PointInTimeEligibility.INELIGIBLE_NO_AVAILABILITY_EVIDENCE
    )
    assert full.alignment.session_alignment is not None
    assert full.alignment.session_alignment.event_session is None
    assert full.alignment.session_alignment.first_post_event_session is None
    assert {fr.status for fr in full.stock_returns.forward_returns} == {
        ForwardReturnStatus.NO_ALIGNMENT
    }
    assert all(fr.return_value is None for fr in full.stock_returns.forward_returns)
    assert full.eps_surprise.absolute_surprise is None
    observation = _assemble(db_session, event, market)
    assert observation.point_in_time.eligibility is full.point_in_time_eligibility
    assert observation.point_in_time.study is None
