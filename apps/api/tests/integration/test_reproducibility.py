# ruff: noqa: F811 - fixtures imported from the observation integration tests
"""Step 10.9A reproducibility manifest, fingerprints, level and verifier against real PostgreSQL.

The manifest identifies what a computation actually CONSUMED, so these tests change one thing at a
time and require the fingerprint to move only when consumed evidence, request or methodology moved.
"""

from __future__ import annotations

import socket
import time
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from tests.fsds_fixture import ACC_10Q, ACC_10QA, build_package
from tests.integration.test_research_observation import (  # noqa: F401 - fixtures and helpers
    NOW,
    _eps,
    _event,
    _filing,
    _ingest_facts,
    _record_statements,
    market,
)

from quantforge.financials import EpsKind, EpsPeriodClass
from quantforge.ingestion.financial_facts import FinancialFactIngestionService
from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.models.benchmark import Benchmark, BenchmarkPriceBar
from quantforge.models.company import Company
from quantforge.models.earnings import EarningsEvent
from quantforge.models.enums import PriceBasis, SecFormType
from quantforge.models.ingestion import IngestionRun
from quantforge.models.market import DailyPriceBar
from quantforge.models.provenance import DataSource
from quantforge.observation import assemble_research_observation
from quantforge.reproducibility import (
    ReproducibilityGap,
    ReproducibilityLevel,
    ReproducibilityRecord,
    VerificationStatus,
    build_reproducibility_record,
    canonical_bytes,
    compare_records,
    fingerprint_of,
    verify_rerun,
)
from quantforge.reproducibility.manifest import EvidenceState
from quantforge.reproducibility.methodology import current_methodology

pytestmark = pytest.mark.integration

ONE_HORIZON = [1]
QUARTER: dict[str, Any] = {"eps_period_class": EpsPeriodClass.QUARTER, "eps_kind": EpsKind.DILUTED}


def _run(db_session: Session) -> IngestionRun:
    run = IngestionRun(
        provider="test",
        dataset="obs",
        started_at=NOW,
        request_parameters={},
        records_received=0,
        records_written=0,
        records_skipped=0,
        records_failed=0,
    )
    db_session.add(run)
    db_session.flush()
    return run


def _pin_runs(db_session: Session, event: EarningsEvent) -> None:
    """Give the event and every stock bar an ingestion run (so a COMPLETE case can exist)."""
    run = _run(db_session)
    event.ingestion_run_id = run.id
    db_session.execute(sa.update(DailyPriceBar).values(ingestion_run_id=run.id))
    db_session.commit()


def _record(
    db_session: Session,
    event: EarningsEvent,
    market: dict[str, object],
    *,
    benchmark: bool = False,
    horizons: list[int] | None = None,
    **extra: Any,
) -> ReproducibilityRecord:
    db_session.expire_all()
    options: dict[str, Any] = {"horizons": horizons or ONE_HORIZON}
    if benchmark:
        bench = market["benchmark"]
        assert isinstance(bench, Benchmark)
        options["benchmark_id"] = bench.id
    options.update(extra)
    observation = assemble_research_observation(db_session, event.id, **options)
    return build_reproducibility_record(db_session, observation)


def _bump(db_session: Session, model: Any, **where: Any) -> None:
    row: Any = db_session.scalars(sa.select(model).filter_by(**where)).one()
    row.close = row.close + Decimal("1")
    if hasattr(row, "high"):  # keep the price-bar CHECK (high >= close) satisfied
        row.high = row.high + Decimal("1")
    db_session.commit()


def test_identical_inputs_give_identical_bytes_and_fingerprints(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _event(db_session, market)
    first = _record(db_session, event, market, benchmark=True)
    second = _record(db_session, event, market, benchmark=True)
    assert canonical_bytes(first.manifest) == canonical_bytes(second.manifest)
    assert first.fingerprints == second.fingerprints
    assert first.result_fingerprint == second.result_fingerprint
    assert first.fingerprints.manifest.algorithm == "sha256"


def test_effective_defaults_are_in_the_manifest_not_null(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _event(db_session, market)
    db_session.expire_all()
    record = build_reproducibility_record(
        db_session, assemble_research_observation(db_session, event.id)
    )
    request = record.manifest.request
    assert request.horizons == (1, 5, 20)
    assert request.price_basis is PriceBasis.SPLIT_ADJUSTED
    assert [m.value for m in request.modes] == ["DESCRIPTIVE", "POINT_IN_TIME"]


@pytest.mark.parametrize("change", ["horizon", "benchmark", "filing", "eps_kind"])
def test_changing_one_request_parameter_changes_the_manifest_fingerprint(
    db_session: Session, market: dict[str, object], tmp_path: Path, change: str
) -> None:
    company = market["company"]
    assert isinstance(company, Company)
    _filing(db_session, company, ACC_10Q, SecFormType.FORM_10Q)
    _filing(db_session, company, ACC_10QA, SecFormType.FORM_10Q_A)
    _ingest_facts(db_session, tmp_path, (ACC_10Q, ACC_10QA))
    event = _event(db_session, market)
    base_args: dict[str, Any] = {"filing_accession": ACC_10Q, **QUARTER}
    base = _record(db_session, event, market, **base_args)
    altered_args = dict(base_args)
    kwargs: dict[str, Any] = {}
    if change == "horizon":
        kwargs["horizons"] = [1, 2]
    elif change == "benchmark":
        kwargs["benchmark"] = True
    elif change == "filing":
        altered_args["filing_accession"] = ACC_10QA
    else:
        altered_args["eps_kind"] = EpsKind.BASIC_AND_DILUTED
    altered = _record(db_session, event, market, **kwargs, **altered_args)
    assert altered.fingerprints.manifest != base.fingerprints.manifest
    assert altered.fingerprints.request != base.fingerprints.request


def test_event_evidence_change_changes_the_manifest(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _event(db_session, market)
    before = _record(db_session, event, market)
    event.estimated_eps = Decimal("0.9000")
    db_session.commit()
    after = _record(db_session, event, market)
    assert after.manifest.evidence.event.fingerprint != before.manifest.evidence.event.fingerprint
    assert after.fingerprints.evidence != before.fingerprints.evidence
    assert after.fingerprints.manifest != before.fingerprints.manifest


def test_consumed_price_evidence_change_changes_the_manifest(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _event(db_session, market)
    sessions = market["sessions"]
    assert isinstance(sessions, list)
    before = _record(db_session, event, market)
    listing_id = before.manifest.evidence.stock.listing_id
    _bump(db_session, DailyPriceBar, listing_id=listing_id, trading_date=sessions[1])  # target
    after = _record(db_session, event, market)
    assert after.fingerprints.evidence != before.fingerprints.evidence
    assert after.fingerprints.request == before.fingerprints.request
    assert after.fingerprints.methodology == before.fingerprints.methodology


def test_consumed_accounting_evidence_change_changes_the_manifest(
    db_session: Session, market: dict[str, object], tmp_path: Path
) -> None:
    company = market["company"]
    assert isinstance(company, Company)
    _filing(db_session, company, ACC_10Q, SecFormType.FORM_10Q)
    _ingest_facts(db_session, tmp_path, (ACC_10Q,))
    event = _event(db_session, market)
    before = _record(db_session, event, market, filing_accession=ACC_10Q, **QUARTER)
    assert [p.role for p in before.manifest.evidence.accounting.facts] == ["current", "comparative"]
    assert before.manifest.evidence.accounting.artifacts
    db_session.execute(sa.text("UPDATE financial_fact SET value = value + 1"))
    db_session.commit()
    after = _record(db_session, event, market, filing_accession=ACC_10Q, **QUARTER)
    assert after.fingerprints.evidence != before.fingerprints.evidence


def test_unused_stored_evidence_never_changes_the_manifest_or_the_result(
    db_session: Session, market: dict[str, object], tmp_path: Path
) -> None:
    company = market["company"]
    assert isinstance(company, Company)
    sessions = market["sessions"]
    assert isinstance(sessions, list)
    _filing(db_session, company, ACC_10Q, SecFormType.FORM_10Q)
    _ingest_facts(db_session, tmp_path, (ACC_10Q,))
    event = _event(db_session, market)
    before = _record(db_session, event, market, filing_accession=ACC_10Q, **QUARTER)
    listing_id = before.manifest.evidence.stock.listing_id

    # 1. a stock bar whose close is not used (horizon 1 uses only sessions[0] and sessions[1])
    _bump(db_session, DailyPriceBar, listing_id=listing_id, trading_date=sessions[3])
    # 2. a bar far outside every read window
    far = DailyPriceBar(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        listing_id=listing_id,
        trading_date=date(2020, 1, 6),
        open=Decimal("1"),
        high=Decimal("1"),
        low=Decimal("1"),
        close=Decimal("1"),
        source_id=before.manifest.evidence.stock.price_source_id,
    )
    db_session.add(far)
    # 3. another event of the same company, another filing, and a benchmark bar
    _event(db_session, market, fiscal_quarter=3)
    _filing(db_session, company, ACC_10QA, SecFormType.FORM_10Q_A)
    benchmark = market["benchmark"]
    assert isinstance(benchmark, Benchmark)
    db_session.add(
        BenchmarkPriceBar(
            price_basis=PriceBasis.SPLIT_ADJUSTED,
            benchmark_id=benchmark.id,
            trading_date=sessions[2],
            close=Decimal("999"),
            source_id=far.source_id,
        )
    )
    db_session.commit()

    after = _record(db_session, event, market, filing_accession=ACC_10Q, **QUARTER)
    assert after.fingerprints == before.fingerprints
    assert after.result_fingerprint == before.result_fingerprint


def test_a_new_session_bar_inside_the_read_window_is_consumed_evidence(
    db_session: Session, market: dict[str, object]
) -> None:
    """The session SET is consumed (a later-added bar could shift the anchor/target)."""
    event = _event(db_session, market)
    sessions = market["sessions"]
    assert isinstance(sessions, list)
    before = _record(db_session, event, market)
    extra_day = sessions[-1] + timedelta(days=1)
    stock = before.manifest.evidence.stock
    db_session.add(
        DailyPriceBar(
            price_basis=PriceBasis.SPLIT_ADJUSTED,
            listing_id=stock.listing_id,
            trading_date=extra_day,
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            source_id=stock.price_source_id,
        )
    )
    db_session.commit()
    after = _record(db_session, event, market)
    assert after.manifest.evidence.stock.sessions != before.manifest.evidence.stock.sessions
    assert after.fingerprints.evidence != before.fingerprints.evidence


def test_no_benchmark_is_complete_when_everything_else_is_pinned(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _event(db_session, market)
    _pin_runs(db_session, event)
    record = _record(db_session, event, market)
    assert record.manifest.evidence.benchmark.state is EvidenceState.NOT_REQUESTED
    assert record.manifest.evidence.accounting.state is EvidenceState.NOT_REQUESTED
    assert record.gaps == () and record.level is ReproducibilityLevel.COMPLETE
    assert record.residual_assumptions, "residual assumptions are always disclosed"


def test_a_consumed_benchmark_is_partial_because_the_table_has_no_run_id(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _event(db_session, market)
    _pin_runs(db_session, event)
    record = _record(db_session, event, market, benchmark=True)
    bench = record.manifest.evidence.benchmark
    assert bench.state is EvidenceState.PINNED and len(bench.bars) == 2
    assert not hasattr(bench.bars[0], "ingestion_run_id"), "nothing is fabricated"
    assert record.gaps == (ReproducibilityGap.BENCHMARK_BARS_WITHOUT_INGESTION_RUN,)
    assert record.level is ReproducibilityLevel.PARTIAL


def test_a_requested_benchmark_with_no_levels_consumes_no_bars_so_it_adds_no_gap(
    db_session: Session, market: dict[str, object]
) -> None:
    empty = Benchmark(name="Empty Index", symbol="EMPT", currency="USD")
    db_session.add(empty)
    db_session.commit()
    event = _event(db_session, market)
    _pin_runs(db_session, event)
    record = _record(db_session, event, market, benchmark_id=empty.id)
    bench = record.manifest.evidence.benchmark
    assert bench.state is EvidenceState.PINNED and bench.bars == () and bench.session_count == 0
    assert record.level is ReproducibilityLevel.COMPLETE
    # but its absence is pinned: a level added later changes the evidence fingerprint
    db_session.add(
        BenchmarkPriceBar(
            price_basis=PriceBasis.SPLIT_ADJUSTED,
            benchmark_id=empty.id,
            trading_date=record.manifest.evidence.stock.bars[0].session,
            close=Decimal("5"),
            source_id=record.manifest.evidence.stock.price_source_id,
        )
    )
    db_session.commit()
    assert (
        _record(db_session, event, market, benchmark_id=empty.id).fingerprints.evidence
        != record.fingerprints.evidence
    )


def test_missing_ingestion_runs_are_reported_as_gaps(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _event(db_session, market)  # no run on the event or the bars
    record = _record(db_session, event, market)
    assert set(record.gaps) == {
        ReproducibilityGap.EVENT_WITHOUT_INGESTION_RUN,
        ReproducibilityGap.STOCK_BAR_WITHOUT_INGESTION_RUN,
    }
    assert record.level is ReproducibilityLevel.PARTIAL


def test_accounting_absent_by_design_is_not_a_gap_but_not_ingested_is(
    db_session: Session, market: dict[str, object]
) -> None:
    company = market["company"]
    assert isinstance(company, Company)
    event = _event(db_session, market)
    _pin_runs(db_session, event)
    none = _record(db_session, event, market)
    assert none.manifest.evidence.filing.state is EvidenceState.NOT_REQUESTED
    assert none.level is ReproducibilityLevel.COMPLETE
    filing = _filing(db_session, company, ACC_10Q, SecFormType.FORM_10Q)
    filing.ingestion_run_id = _run(db_session).id
    db_session.commit()
    unread = _record(db_session, event, market, filing_accession=ACC_10Q, **QUARTER)
    assert unread.manifest.evidence.accounting.artifacts == ()
    assert unread.gaps == (ReproducibilityGap.ACCOUNTING_NOT_INGESTED,)


def test_accounting_refusal_keeps_the_pinned_package_so_the_refusal_is_reproducible(
    db_session: Session, market: dict[str, object], tmp_path: Path
) -> None:
    company = market["company"]
    assert isinstance(company, Company)
    filing = _filing(db_session, company, ACC_10QA, SecFormType.FORM_10Q_A)
    filing.ingestion_run_id = _run(db_session).id
    package = build_package(tmp_path / "p.zip", nums=[_eps(ACC_10QA, "20240331", "1", "1.5400")])
    FinancialFactIngestionService(
        session=db_session, store=RawResponseStore(root=tmp_path / "raw"), now=NOW
    ).ingest_fsds_package(package, package_name="2024q2", accessions=(ACC_10QA,))
    db_session.commit()
    event = _event(db_session, market)
    _pin_runs(db_session, event)

    record = _record(db_session, event, market, filing_accession=ACC_10QA, **QUARTER)

    accounting = record.manifest.evidence.accounting
    assert accounting.state is EvidenceState.PINNED
    assert len(accounting.artifacts) == 1 and [f.role for f in accounting.facts] == ["current"]
    assert record.level is ReproducibilityLevel.COMPLETE
    report = verify_rerun(
        db_session, event.id, horizons=ONE_HORIZON, filing_accession=ACC_10QA, **QUARTER
    )
    assert report.status is VerificationStatus.MATCH, "the same refusal reproduces"


def test_refused_link_requested_but_not_read(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _event(db_session, market)
    record = _record(db_session, event, market, filing_accession="0000111111-24-000001", **QUARTER)
    assert record.manifest.evidence.filing.state is EvidenceState.NOT_READ
    assert record.manifest.evidence.accounting.state is EvidenceState.NOT_READ
    assert record.manifest.request.filing_accession == "0000111111-24-000001"


def test_pit_refusal_is_reproducible_and_keeps_the_descriptive_result(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _event(db_session, market)  # observed_at NULL
    _pin_runs(db_session, event)
    first = _record(db_session, event, market)
    second = _record(db_session, event, market)
    assert first.manifest.evidence.pit.eligibility.value == "INELIGIBLE_NO_AVAILABILITY_EVIDENCE"
    assert first.manifest.evidence.pit.observed_at is None
    assert first.result_fingerprint == second.result_fingerprint
    # an eligible event has a different PIT evidence identity and result
    eligible = _event(
        db_session, market, fiscal_quarter=2, observed_at=datetime(2026, 3, 2, 11, 0, tzinfo=UTC)
    )
    other = _record(db_session, eligible, market)
    assert other.manifest.evidence.pit.observed_at == datetime(2026, 3, 2, 11, 0, tzinfo=UTC)
    assert other.result_fingerprint != first.result_fingerprint


def test_a_methodology_version_change_changes_the_fingerprint_with_the_same_result(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _event(db_session, market)
    db_session.expire_all()
    observation = assemble_research_observation(db_session, event.id, horizons=ONE_HORIZON)
    base = build_reproducibility_record(db_session, observation)
    bumped = build_reproducibility_record(
        db_session, observation, methodology=current_methodology({"eps_surprise": "eps-surprise/2"})
    )
    assert bumped.result_fingerprint == base.result_fingerprint
    assert bumped.fingerprints.methodology != base.fingerprints.methodology
    assert bumped.fingerprints.manifest != base.fingerprints.manifest
    assert bumped.fingerprints.evidence == base.fingerprints.evidence
    report = compare_records(base, bumped)
    assert report.status is VerificationStatus.MANIFEST_CHANGED
    assert report.changed_sections == ("methodology",)


def test_a_deliberately_altered_result_changes_only_the_result_fingerprint(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _event(db_session, market)
    db_session.expire_all()
    observation = assemble_research_observation(db_session, event.id, horizons=ONE_HORIZON)
    base = build_reproducibility_record(db_session, observation)
    altered_event = replace(observation.event, reported_eps=Decimal("9.9999"))
    altered = build_reproducibility_record(db_session, replace(observation, event=altered_event))
    assert altered.fingerprints.manifest == base.fingerprints.manifest
    assert altered.result_fingerprint != base.result_fingerprint
    assert compare_records(base, altered).status is VerificationStatus.RESULT_CHANGED


def test_known_gaps_roadmap_text_is_not_part_of_the_result(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _event(db_session, market)
    db_session.expire_all()
    observation = assemble_research_observation(db_session, event.id, horizons=ONE_HORIZON)
    base = build_reproducibility_record(db_session, observation)
    edited = replace(observation, provenance=replace(observation.provenance, known_gaps=("X",)))
    assert (
        build_reproducibility_record(db_session, edited).result_fingerprint
        == base.result_fingerprint
    )


def test_verifier_match_incomplete_and_changed(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _event(db_session, market)
    _pin_runs(db_session, event)
    match = verify_rerun(db_session, event.id, horizons=ONE_HORIZON)
    assert match.status is VerificationStatus.MATCH and match.level is ReproducibilityLevel.COMPLETE

    bench = market["benchmark"]
    assert isinstance(bench, Benchmark)
    incomplete = verify_rerun(db_session, event.id, horizons=ONE_HORIZON, benchmark_id=bench.id)
    assert incomplete.status is VerificationStatus.EVIDENCE_INCOMPLETE
    assert incomplete.expected.fingerprints == incomplete.actual.fingerprints
    assert incomplete.level is ReproducibilityLevel.PARTIAL, "a match is not a complete claim"
    assert incomplete.baseline == "RERUN"

    baseline = _record(db_session, event, market)
    sessions = market["sessions"]
    assert isinstance(sessions, list)
    _bump(
        db_session,
        DailyPriceBar,
        listing_id=baseline.manifest.evidence.stock.listing_id,
        trading_date=sessions[1],
    )
    changed = verify_rerun(db_session, event.id, horizons=ONE_HORIZON, expected=baseline)
    assert changed.status is VerificationStatus.MANIFEST_CHANGED and changed.baseline == "SUPPLIED"
    assert changed.changed_sections == ("evidence",)
    assert any(path.startswith("evidence.stock.bars") for path in changed.changed_paths)


def test_verifier_refuses_a_dirty_session(db_session: Session, market: dict[str, object]) -> None:
    event = _event(db_session, market)
    event.estimated_eps = Decimal("0.5")  # pending, unflushed change
    with pytest.raises(ValueError, match="clean session"):
        verify_rerun(db_session, event.id, horizons=ONE_HORIZON)
    db_session.rollback()


def test_unknown_event_is_an_error_not_a_record(db_session: Session) -> None:
    with pytest.raises(LookupError):
        verify_rerun(db_session, uuid.uuid4(), horizons=ONE_HORIZON)


def test_manifest_building_is_read_only_provider_free_and_cheap(
    db_session: Session,
    market: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company = market["company"]
    assert isinstance(company, Company)
    _filing(db_session, company, ACC_10Q, SecFormType.FORM_10Q)
    _ingest_facts(db_session, tmp_path, (ACC_10Q,))
    event = _event(db_session, market, observed_at=datetime(2026, 3, 2, 11, 0, tzinfo=UTC))

    def tripwire(*_a: object, **_k: object) -> None:
        raise AssertionError("network access while building a manifest")

    monkeypatch.setattr(socket.socket, "connect", tripwire)
    monkeypatch.setattr(socket, "create_connection", tripwire)

    db_session.expire_all()
    with _record_statements(db_session) as assembly:
        observation = assemble_research_observation(
            db_session,
            event.id,
            horizons=[1, 2],
            benchmark_id=market["benchmark"].id,  # type: ignore[attr-defined]
            filing_accession=ACC_10Q,
            **QUARTER,
        )
    with _record_statements(db_session) as manifest:
        build_reproducibility_record(db_session, observation)
    with _record_statements(db_session) as rerun:
        verify_rerun(
            db_session,
            event.id,
            horizons=[1, 2],
            benchmark_id=market["benchmark"].id,  # type: ignore[attr-defined]
            filing_accession=ACC_10Q,
            **QUARTER,
        )
    allowed = ("SELECT", "WITH", "SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK")
    for statements in (assembly, manifest, rerun):
        assert not [s for s in statements if not s.lstrip().upper().startswith(allowed)]
    print(  # noqa: T201
        f"assembly={len(assembly)} manifest_added={len(manifest)} verify_rerun={len(rerun)}"
    )
    assert len(manifest) <= 4, "stock window, benchmark window, filing packages (+ savepoint)"
    assert len(manifest) < len(assembly)


def test_canonicalisation_and_fingerprinting_are_negligible_next_to_assembly(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _event(db_session, market)
    db_session.expire_all()
    observation = assemble_research_observation(db_session, event.id, horizons=[1, 2])
    record = build_reproducibility_record(db_session, observation)
    started = time.perf_counter()
    for _ in range(50):
        fingerprint_of(record.manifest)
        fingerprint_of(observation)
    per_call = (time.perf_counter() - started) / 100
    assert per_call < 0.01, f"{per_call:.5f}s per fingerprint"


def test_a_second_source_on_a_held_date_changes_the_evidence_not_just_the_result(
    db_session: Session, market: dict[str, object]
) -> None:
    event = _event(db_session, market)
    sessions = market["sessions"]
    assert isinstance(sessions, list)
    before = _record(db_session, event, market)
    stock_source = market["stock_source"]
    assert isinstance(stock_source, DataSource)
    other = DataSource(name="second-stock-vendor", provider_type=stock_source.provider_type)
    db_session.add(other)
    db_session.commit()
    db_session.add(
        DailyPriceBar(
            price_basis=PriceBasis.SPLIT_ADJUSTED,
            listing_id=before.manifest.evidence.stock.listing_id,
            trading_date=sessions[1],
            open=Decimal("5"),
            high=Decimal("5"),
            low=Decimal("5"),
            close=Decimal("5"),
            source_id=other.id,
        )
    )
    db_session.commit()
    report = verify_rerun(db_session, event.id, horizons=ONE_HORIZON, expected=before)
    assert report.status is VerificationStatus.MANIFEST_CHANGED, "not RESULT_CHANGED"
    assert report.changed_sections == ("evidence",)
