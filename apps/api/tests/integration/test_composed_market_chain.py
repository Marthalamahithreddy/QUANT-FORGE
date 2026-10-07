"""Step 11.2E: the normal ingestion -> research -> reproducibility chain, with NO manual repair.

One golden-shaped event (an NKE-like after-market-close earnings event on 2026-10-01, observed
by QuantForge only on 2026-10-07) is built entirely through the real services: a dated listing
snapshot fact, the earnings event, stock bars (including the real NKE record whose vwap lies
below its low) and benchmark bars. Every ``ingestion_run_id`` comes from the service that wrote
the row. Then the actual research assembler, manifest builder, save and verify run over it.
Providers are stubs that feed the real mapper, so nothing here touches a network.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from tests.integration.test_ingestion_earnings import StubEarningsProvider, _capture
from tests.integration.test_listing_history import (
    FIGI_A,
    StubListingProvider,
    _snapshot,
)

from quantforge.ingestion.canonical import CanonicalEarningsEvent, ResolvedVenue
from quantforge.ingestion.listing_history import ListingHistoryIngestionService
from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.ingestion.service import EarningsIngestionService, IngestionService
from quantforge.models.benchmark import Benchmark
from quantforge.models.company import Company
from quantforge.models.earnings import EarningsEvent
from quantforge.models.enums import (
    EarningsTiming,
    IdentifierType,
    PriceBasis,
    ProviderType,
    ReproducibilityLevel,
    SecurityType,
)
from quantforge.models.market import DailyPriceBar
from quantforge.models.provenance import DataSource
from quantforge.models.research_record import ResearchObservationRecord
from quantforge.models.security import Exchange, Listing, Security, SecurityIdentifier
from quantforge.observation.assembler import assemble_research_observation
from quantforge.providers.base import DailyBarsResult
from quantforge.providers.earnings import EarningsResult
from quantforge.providers.massive.dto import MassiveDailyBarDTO
from quantforge.providers.massive.mapper import to_canonical_daily_bar
from quantforge.reproducibility import VerificationStatus
from quantforge.reproducibility.manifest import EvidenceState, build_reproducibility_record
from quantforge.reproducibility.methodology import current_methodology
from quantforge.research.calendar import AlignmentStatus
from quantforge.research.returns import ForwardReturnStatus
from quantforge.research_records import save_research_record, verify_stored_record

pytestmark = pytest.mark.integration

FIRST_OBSERVED = date(2026, 10, 7)
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
EVENT_DAY = date(2026, 10, 1)
XNYS = ResolvedVenue(mic="XNYS", name="New York Stock Exchange", timezone_name="America/New_York")

# The observed NKE aggregate rows (Massive v2 aggs, adjusted=true): t is epoch ms at 04:00 ET.
_NKE = [
    {
        "t": 1790827200000,
        "o": 35.45,
        "h": 36.235,
        "l": 35.02,
        "c": 35.15,
        "vw": 34.8298,
        "v": 112413524.118262,
        "n": 560588,
    },
    {
        "t": 1790913600000,
        "o": 32.553,
        "h": 33.97,
        "l": 31.97,
        "c": 33.87,
        "vw": 33.1565,
        "v": 142717008.024328,
        "n": 693009,
    },
    {
        "t": 1791172800000,
        "o": 33.82,
        "h": 34.12,
        "l": 32.76,
        "c": 33.96,
        "vw": 33.4753,
        "v": 69574914.414271,
        "n": 348365,
    },
    {
        "t": 1791259200000,
        "o": 33.7,
        "h": 34.62,
        "l": 33.5,
        "c": 34.61,
        "vw": 34.2191,
        "v": 50148121.392537,
        "n": 256686,
    },
]
_SPY = [
    {
        "t": row["t"],
        "o": 600 + i,
        "h": 602 + i,
        "l": 598 + i,
        "c": 601 + i,
        "vw": 600.5 + i,
        "v": 1000000 + i,
        "n": 1000 + i,
    }
    for i, row in enumerate(_NKE)
]


class _BarsProvider:
    name = "massive"

    def __init__(self, rows: list[dict[str, Any]], symbol: str) -> None:
        self._rows, self._symbol = rows, symbol

    async def get_daily_bars(self, *_a: object, **_k: object) -> DailyBarsResult:
        bars = [
            to_canonical_daily_bar(
                MassiveDailyBarDTO.from_provider(row, identifier=self._symbol),
                venue=XNYS,
                price_basis=PriceBasis.SPLIT_ADJUSTED,
                identifier=self._symbol,
            )
            for row in self._rows
        ]
        return DailyBarsResult(bars=bars, captures=(_capture(),))


def _earnings(timing: EarningsTiming = EarningsTiming.AFTER_MARKET) -> EarningsResult:
    event = CanonicalEarningsEvent(
        ticker="NKE",
        fiscal_year=2027,
        fiscal_quarter=1,
        event_date=EVENT_DAY,
        event_timestamp=None,
        timing=timing,
        reported_eps=Decimal("0.48"),
        estimated_eps=Decimal("0.4364"),
        reported_revenue=None,
        estimated_revenue=None,
        currency="USD",
        observed_at=None,
    )
    return EarningsResult(events=[event], captures=(_capture(),))


@pytest.fixture
def chain(db_session: Session, tmp_path: Path) -> dict[str, Any]:
    exchange = db_session.scalar(sa.select(Exchange).where(Exchange.mic == "XNYS"))
    if exchange is None:
        exchange = Exchange(mic="XNYS", name="NYSE test", timezone="America/New_York")
        db_session.add(exchange)
    source = DataSource(name="massive", provider_type=ProviderType.MARKET_DATA_VENDOR)
    company = Company(legal_name="Composed Chain Inc.", display_name="Chain", cik="0000320187")
    db_session.add_all([source, company])
    db_session.flush()
    security = Security(
        company_id=company.id, security_type=SecurityType.EQUITY, name="Common", currency="USD"
    )
    db_session.add(security)
    db_session.flush()
    db_session.add_all(
        [
            SecurityIdentifier(
                security_id=security.id,
                identifier_type=IdentifierType.FIGI_SHARE_CLASS,
                identifier_value=FIGI_A,
                source_id=source.id,
            ),
            Listing(
                security_id=security.id,
                exchange_id=exchange.id,
                ticker="NKE",
                valid_from=FIRST_OBSERVED,  # first observed AFTER the event: no history yet
                is_primary=True,
            ),
        ]
    )
    benchmark = Benchmark(name="SPY (ETF proxy for the S&P 500)", symbol="SPY", currency="USD")
    db_session.add(benchmark)
    db_session.commit()
    return {"tmp": tmp_path, "benchmark": benchmark, "security": security}


def _ingest_everything(
    db_session: Session,
    chain: dict[str, Any],
    *,
    bars: list[dict[str, Any]] = _NKE,
    timing: EarningsTiming = EarningsTiming.AFTER_MARKET,
) -> EarningsEvent:
    store = RawResponseStore(root=chain["tmp"] / "raw")
    snapshot = _snapshot(as_of_date=EVENT_DAY, ticker="NKE", cik="0000320187", primary_mic="XNYS")
    asyncio.run(
        ListingHistoryIngestionService(
            session=db_session, provider=StubListingProvider([snapshot]), store=store, now=NOW
        ).ingest_ticker_snapshot("NKE", EVENT_DAY)
    )
    db_session.commit()
    asyncio.run(
        EarningsIngestionService(
            session=db_session,
            provider=StubEarningsProvider(_earnings(timing)),
            store=store,
            now=NOW,
        ).ingest_earnings("NKE", date(2026, 9, 25), date(2026, 10, 2))
    )
    db_session.commit()
    asyncio.run(
        IngestionService(
            session=db_session,
            provider=_BarsProvider(bars, "NKE"),  # type: ignore[arg-type]
            store=store,
            now=NOW,
        ).ingest_daily_bars(
            "NKE", EVENT_DAY, date(2026, 10, 6), price_basis=PriceBasis.SPLIT_ADJUSTED
        )
    )
    db_session.commit()
    asyncio.run(
        IngestionService(
            session=db_session,
            provider=_BarsProvider(_SPY, "SPY"),  # type: ignore[arg-type]
            store=store,
            now=NOW,
        ).ingest_benchmark_daily_bars(
            "SPY",
            EVENT_DAY,
            date(2026, 10, 6),
            name="SPY (ETF proxy for the S&P 500)",
            price_basis=PriceBasis.SPLIT_ADJUSTED,
        )
    )
    db_session.commit()
    return db_session.scalars(sa.select(EarningsEvent)).one()


def test_the_real_nke_aggregate_ingests_whole_through_the_service(
    db_session: Session, chain: dict[str, Any]
) -> None:
    _ingest_everything(db_session, chain)
    stored = db_session.scalars(sa.select(DailyPriceBar).order_by(DailyPriceBar.trading_date)).all()
    assert [bar.trading_date for bar in stored] == [
        EVENT_DAY,
        date(2026, 10, 2),
        date(2026, 10, 5),
        date(2026, 10, 6),
    ]
    assert stored[0].vwap == Decimal("34.8298") and stored[0].low == Decimal("35.02")
    assert all(bar.ingestion_run_id is not None for bar in stored)


def test_listing_resolves_from_the_dated_fact_and_the_calendar_anchors_the_event(
    db_session: Session, chain: dict[str, Any]
) -> None:
    event = _ingest_everything(db_session, chain)
    observation = assemble_research_observation(
        db_session, event.id, horizons=(1, 2, 5), benchmark_id=chain["benchmark"].id
    )
    study = observation.descriptive.study
    resolution = observation.identity.listing_resolution
    assert resolution.listing_id is not None
    assert resolution.exchange_mic is None  # venue on that date is NOT established
    assert study.alignment.session_calendar_mic == "XNYS"
    assert study.alignment.session_alignment is not None
    assert study.alignment.session_alignment.status is AlignmentStatus.ALIGNED
    assert study.alignment.session_alignment.event_session == EVENT_DAY
    assert study.alignment.session_alignment.first_post_event_session == date(2026, 10, 2)
    by_h = {r.horizon: r for r in study.stock_returns.forward_returns}
    assert by_h[1].status is ForwardReturnStatus.AVAILABLE and by_h[1].target_session == date(
        2026, 10, 5
    )
    assert by_h[2].status is ForwardReturnStatus.AVAILABLE and by_h[2].target_session == date(
        2026, 10, 6
    )
    assert by_h[5].status is ForwardReturnStatus.INSUFFICIENT_FORWARD_SESSIONS
    assert by_h[5].target_session == date(2026, 10, 9)  # proven by the calendar, never moved
    # the benchmark uses the stock's own dates
    for stock, bench in zip(
        study.stock_returns.forward_returns, study.benchmark_returns, strict=True
    ):
        if stock.status is ForwardReturnStatus.AVAILABLE:
            assert (bench.anchor_session, bench.target_session) == (
                stock.anchor_session,
                stock.target_session,
            )
    # no point-in-time claim: the event carries no availability evidence
    assert observation.point_in_time.state.value == "REFUSED"


def test_naturally_run_stamped_evidence_gives_a_complete_manifest_without_manual_repair(
    db_session: Session, chain: dict[str, Any]
) -> None:
    event = _ingest_everything(db_session, chain)
    assert event.ingestion_run_id is not None
    for configuration in (None, chain["benchmark"].id):
        observation = assemble_research_observation(
            db_session, event.id, horizons=(1, 2, 5), benchmark_id=configuration
        )
        record = build_reproducibility_record(db_session, observation)
        evidence = record.manifest.evidence
        assert evidence.stock.state is EvidenceState.PINNED
        assert evidence.benchmark.state is (
            EvidenceState.NOT_REQUESTED if configuration is None else EvidenceState.PINNED
        )
        assert evidence.filing.state is EvidenceState.NOT_REQUESTED
        assert evidence.accounting.state is EvidenceState.NOT_REQUESTED
        assert {pin.session for pin in evidence.stock.bars} == {
            date(2026, 10, 2),
            date(2026, 10, 5),
            date(2026, 10, 6),
        }
        assert all(pin.ingestion_run_id is not None for pin in evidence.stock.bars)
        assert record.baseline.level is ReproducibilityLevel.COMPLETE, record.baseline.gaps
        assert record.baseline.gaps == ()


def test_save_load_and_verify_the_composed_record(
    db_session: Session, chain: dict[str, Any]
) -> None:
    event = _ingest_everything(db_session, chain)
    saved = save_research_record(
        db_session, event.id, horizons=(1, 2, 5), benchmark_id=chain["benchmark"].id
    )
    db_session.commit()
    assert saved.created is True and saved.record.baseline.level is ReproducibilityLevel.COMPLETE
    report = verify_stored_record(db_session, saved.record.record_id)
    assert report.status is VerificationStatus.MATCH
    assert (
        db_session.scalars(sa.select(ResearchObservationRecord)).one().id == saved.record.record_id
    )


def test_a_missing_required_stock_session_makes_the_horizon_unavailable_through_the_whole_chain(
    db_session: Session, chain: dict[str, Any]
) -> None:
    event = _ingest_everything(db_session, chain, bars=[_NKE[0], _NKE[1], _NKE[3]])  # 10-05 absent
    observation = assemble_research_observation(
        db_session, event.id, horizons=(1, 2), benchmark_id=chain["benchmark"].id
    )
    by_h = {r.horizon: r for r in observation.descriptive.study.stock_returns.forward_returns}
    assert by_h[1].status is ForwardReturnStatus.INSUFFICIENT_FORWARD_SESSIONS
    assert by_h[1].target_session == date(2026, 10, 5)
    assert by_h[2].status is ForwardReturnStatus.AVAILABLE
    assert by_h[2].target_session == date(2026, 10, 6)  # not shifted, not borrowed


def test_a_record_saved_under_the_previous_session_methodology_verifies_as_manifest_changed(
    db_session: Session, chain: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Old immutable records are never rewritten and never silently MATCH the new algorithm."""
    event = _ingest_everything(db_session, chain)
    old = current_methodology(
        {
            "session_alignment": "stored-bar-sessions/1",
            "forward_returns": "close-to-close-session-returns/1",
        }
    )
    with monkeypatch.context() as patched:
        patched.setitem(build_reproducibility_record.__kwdefaults__, "methodology", old)  # type: ignore[arg-type]
        saved = save_research_record(db_session, event.id, horizons=(1, 2))
        db_session.commit()
    stored_text = db_session.scalars(sa.select(ResearchObservationRecord)).one().canonical_manifest
    assert "stored-bar-sessions/1" in stored_text
    report = verify_stored_record(db_session, saved.record.record_id)
    assert report.status is VerificationStatus.MANIFEST_CHANGED
    assert report.changed_sections == ("methodology",)
    assert any("session_alignment" in path or "components" in path for path in report.changed_paths)
    # the stored record itself was not rewritten
    assert (
        "stored-bar-sessions/1"
        in db_session.scalars(sa.select(ResearchObservationRecord)).one().canonical_manifest
    )


def test_before_market_timing_anchors_on_the_event_day_bar(
    db_session: Session, chain: dict[str, Any]
) -> None:
    event = _ingest_everything(db_session, chain, timing=EarningsTiming.BEFORE_MARKET)
    observation = assemble_research_observation(db_session, event.id, horizons=(1,))
    study = observation.descriptive.study
    assert study.stock_returns.anchor_session == EVENT_DAY
    only = study.stock_returns.forward_returns[0]
    assert only.status is ForwardReturnStatus.AVAILABLE and only.target_session == date(2026, 10, 2)
