"""Integration tests for the unified Step 8.6 earnings event-study entry point.

Proves the full composed chain: ``EarningsEvent`` -> availability/EPS
surprise -> Step 8.1/8.2 alignment -> Step 8.4 stock forward returns ->
Step 8.5 benchmark returns matched to the stock's own endpoints -> abnormal
returns. Pure arithmetic for each piece is already covered without a
database in the corresponding ``tests/test_research_*.py`` files; these
tests exist to prove the wiring, the benchmark-specific database-dependent
edge cases, and the point-in-time/failure-isolation behavior that only
surface once real rows are involved.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from quantforge.models.benchmark import Benchmark, BenchmarkPriceBar
from quantforge.models.company import Company
from quantforge.models.earnings import EarningsEvent
from quantforge.models.enums import (
    AvailabilityBasis,
    EarningsTiming,
    PriceBasis,
    ProviderType,
    SecurityType,
)
from quantforge.models.market import DailyPriceBar
from quantforge.models.provenance import DataSource
from quantforge.models.security import Exchange, Listing, Security
from quantforge.research.abnormal_returns import AbnormalReturnStatus
from quantforge.research.availability import ResearchMode, select_known_as_of
from quantforge.research.benchmark_returns import BenchmarkReturnStatus
from quantforge.research.event_study import analyze_earnings_event
from quantforge.research.returns import ForwardReturnStatus

pytestmark = pytest.mark.integration


@pytest.fixture
def source(db_session: Session) -> DataSource:
    row = DataSource(name="event-study-stock-vendor", provider_type=ProviderType.MARKET_DATA_VENDOR)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def benchmark_source(db_session: Session) -> DataSource:
    row = DataSource(
        name="event-study-benchmark-vendor", provider_type=ProviderType.MARKET_DATA_VENDOR
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def company(db_session: Session) -> Company:
    row = Company(legal_name="Event Study Test Co", display_name="Event Study Test")
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def exchange(db_session: Session) -> Exchange:
    row = Exchange(mic="XNYS", name="Event Study Test Exchange", timezone="America/New_York")
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def security(company: Company, db_session: Session) -> Security:
    row = Security(
        company_id=company.id,
        security_type=SecurityType.EQUITY,
        name="Ordinary Shares",
        currency="USD",
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def listing(security: Security, exchange: Exchange, db_session: Session) -> Listing:
    row = Listing(
        security_id=security.id,
        exchange_id=exchange.id,
        ticker="ESTD",
        valid_from=date(2018, 1, 2),
        is_primary=True,
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def benchmark(db_session: Session) -> Benchmark:
    row = Benchmark(name="Event Study Test Index", symbol="ESTI", currency="USD")
    db_session.add(row)
    db_session.commit()
    return row


def _bar(listing: Listing, source: DataSource, day: date, close: Decimal) -> DailyPriceBar:
    return DailyPriceBar(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        listing_id=listing.id,
        trading_date=day,
        open=close,
        high=close,
        low=close,
        close=close,
        source_id=source.id,
    )


def _level(
    benchmark: Benchmark, source: DataSource, day: date, close: Decimal
) -> BenchmarkPriceBar:
    return BenchmarkPriceBar(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        benchmark_id=benchmark.id,
        trading_date=day,
        close=close,
        source_id=source.id,
    )


def _weekday_sessions(start: date, count: int) -> list[date]:
    sessions: list[date] = []
    day = start
    while len(sessions) < count:
        if day.weekday() < 5:
            sessions.append(day)
        day += timedelta(days=1)
    return sessions


def _put_stock_bars(
    listing: Listing,
    source: DataSource,
    db_session: Session,
    sessions: list[date],
    start_price: Decimal,
) -> list[Decimal]:
    prices = [start_price + Decimal(index) for index in range(len(sessions))]
    db_session.add_all(
        _bar(listing, source, day, price) for day, price in zip(sessions, prices, strict=True)
    )
    db_session.commit()
    return prices


def _put_benchmark_levels(
    benchmark: Benchmark,
    source: DataSource,
    db_session: Session,
    sessions: list[date],
    start_level: Decimal,
) -> list[Decimal]:
    levels = [start_level + Decimal(2 * index) for index in range(len(sessions))]
    db_session.add_all(
        _level(benchmark, source, day, level) for day, level in zip(sessions, levels, strict=True)
    )
    db_session.commit()
    return levels


def _event(
    company: Company, source: DataSource, db_session: Session, **overrides: object
) -> EarningsEvent:
    fields: dict[str, object] = {
        "company_id": company.id,
        "fiscal_year": 2026,
        "fiscal_quarter": 2,
        "event_date": date(2026, 3, 4),
        "timing": EarningsTiming.AFTER_MARKET,
        "reported_eps": Decimal("1.2000"),
        "estimated_eps": Decimal("1.0000"),
        "source_id": source.id,
    }
    fields.update(overrides)
    if fields.get("observed_at") is not None:
        fields.setdefault("observed_at_basis", AvailabilityBasis.SOURCE_PUBLICATION)
    row = EarningsEvent(**fields)
    db_session.add(row)
    db_session.commit()
    return row


# ── Section R: a deterministic, hand-auditable numeric example ─────────────


def test_deterministic_numeric_example(
    company: Company,
    source: DataSource,
    listing: Listing,
    benchmark: Benchmark,
    benchmark_source: DataSource,
    db_session: Session,
) -> None:
    """Stock S0=100, S1=110 (+10%); benchmark S0=200, S1=210 (+5%); abnormal = +5%."""
    sessions = _weekday_sessions(date(2026, 3, 2), 3)  # S0, S1, S2
    db_session.add_all(
        [
            _bar(listing, source, sessions[0], Decimal("100")),
            _bar(listing, source, sessions[1], Decimal("110")),
            _bar(listing, source, sessions[2], Decimal("121")),
        ]
    )
    db_session.add_all(
        [
            _level(benchmark, benchmark_source, sessions[0], Decimal("200")),
            _level(benchmark, benchmark_source, sessions[1], Decimal("210")),
            _level(benchmark, benchmark_source, sessions[2], Decimal("220.50")),
        ]
    )
    db_session.commit()

    event = _event(
        company, source, db_session, timing=EarningsTiming.BEFORE_MARKET, event_date=sessions[0]
    )

    result = analyze_earnings_event(
        db_session, event, mode=ResearchMode.DESCRIPTIVE, horizons=[1, 2], benchmark_id=benchmark.id
    )

    by_horizon_stock = {fr.horizon: fr for fr in result.stock_returns.forward_returns}
    by_horizon_bench = {br.horizon: br for br in result.benchmark_returns}
    by_horizon_abn = {ar.horizon: ar for ar in result.abnormal_returns}

    # Horizon 1: stock +10%, benchmark +5%, abnormal +5%.
    assert by_horizon_stock[1].return_value == Decimal("0.10")
    assert by_horizon_bench[1].benchmark_return == Decimal("0.05")
    assert by_horizon_abn[1].status is AbnormalReturnStatus.AVAILABLE
    assert by_horizon_abn[1].abnormal_return == Decimal("0.05")

    # Horizon 2: stock 121/100 - 1 = 0.21; benchmark 220.50/200 - 1 = 0.1025;
    # abnormal = 0.21 - 0.1025 = 0.1075.
    assert by_horizon_stock[2].return_value == Decimal("0.21")
    assert by_horizon_bench[2].benchmark_return == Decimal("0.1025")
    assert by_horizon_abn[2].abnormal_return == Decimal("0.1075")


# ── BMO / AMC end-to-end, exact endpoint matching ───────────────────────────


def test_before_market_event_end_to_end(
    company: Company,
    source: DataSource,
    listing: Listing,
    benchmark: Benchmark,
    benchmark_source: DataSource,
    db_session: Session,
) -> None:
    sessions = _weekday_sessions(date(2026, 3, 2), 25)
    stock_prices = _put_stock_bars(listing, source, db_session, sessions, Decimal("100.00"))
    bench_levels = _put_benchmark_levels(
        benchmark, benchmark_source, db_session, sessions, Decimal("1000.00")
    )
    event = _event(
        company, source, db_session, timing=EarningsTiming.BEFORE_MARKET, event_date=sessions[2]
    )

    result = analyze_earnings_event(
        db_session, event, mode=ResearchMode.DESCRIPTIVE, benchmark_id=benchmark.id
    )

    assert result.alignment.session_alignment is not None
    assert result.alignment.session_alignment.first_post_event_session == sessions[2]
    assert result.stock_returns.anchor_session == sessions[2]

    by_horizon_stock = {fr.horizon: fr for fr in result.stock_returns.forward_returns}
    by_horizon_bench = {br.horizon: br for br in result.benchmark_returns}
    by_horizon_abn = {ar.horizon: ar for ar in result.abnormal_returns}

    assert by_horizon_stock[1].target_session == sessions[3]
    assert by_horizon_bench[1].anchor_session == sessions[2]
    assert by_horizon_bench[1].target_session == sessions[3]
    assert by_horizon_bench[1].anchor_close == bench_levels[2]
    assert by_horizon_bench[1].target_close == bench_levels[3]

    expected_stock = stock_prices[3] / stock_prices[2] - 1
    expected_bench = bench_levels[3] / bench_levels[2] - 1
    assert by_horizon_stock[1].return_value == expected_stock
    assert by_horizon_bench[1].benchmark_return == expected_bench
    assert by_horizon_abn[1].abnormal_return == expected_stock - expected_bench


def test_after_market_event_end_to_end(
    company: Company,
    source: DataSource,
    listing: Listing,
    benchmark: Benchmark,
    benchmark_source: DataSource,
    db_session: Session,
) -> None:
    sessions = _weekday_sessions(date(2026, 3, 2), 25)
    _put_stock_bars(listing, source, db_session, sessions, Decimal("100.00"))
    _put_benchmark_levels(benchmark, benchmark_source, db_session, sessions, Decimal("1000.00"))
    event = _event(
        company, source, db_session, timing=EarningsTiming.AFTER_MARKET, event_date=sessions[2]
    )

    result = analyze_earnings_event(
        db_session, event, mode=ResearchMode.DESCRIPTIVE, benchmark_id=benchmark.id
    )

    # AMC's anchor is inherited from Step 8.2 alignment: the *next* session
    # after the event date, never the event date itself.
    assert result.alignment.session_alignment is not None
    assert result.alignment.session_alignment.first_post_event_session == sessions[3]
    by_horizon_bench = {br.horizon: br for br in result.benchmark_returns}
    assert by_horizon_bench[1].anchor_session == sessions[3]
    assert by_horizon_bench[1].target_session == sessions[4]


# ── benchmark missing an endpoint ───────────────────────────────────────────


def test_benchmark_missing_the_anchor_session(
    company: Company,
    source: DataSource,
    listing: Listing,
    benchmark: Benchmark,
    benchmark_source: DataSource,
    db_session: Session,
) -> None:
    sessions = _weekday_sessions(date(2026, 3, 2), 10)
    _put_stock_bars(listing, source, db_session, sessions, Decimal("100.00"))
    # Benchmark data starts only *after* the anchor session.
    db_session.add_all(
        _level(benchmark, benchmark_source, day, Decimal("1000.00") + Decimal(index))
        for index, day in enumerate(sessions[3:])
    )
    db_session.commit()
    event = _event(
        company, source, db_session, timing=EarningsTiming.BEFORE_MARKET, event_date=sessions[2]
    )

    result = analyze_earnings_event(
        db_session, event, mode=ResearchMode.DESCRIPTIVE, horizons=[1], benchmark_id=benchmark.id
    )

    by_horizon_stock = {fr.horizon: fr for fr in result.stock_returns.forward_returns}
    by_horizon_bench = {br.horizon: br for br in result.benchmark_returns}
    by_horizon_abn = {ar.horizon: ar for ar in result.abnormal_returns}

    # The stock leg still succeeds -- missing benchmark data must not erase it.
    assert by_horizon_stock[1].status is ForwardReturnStatus.AVAILABLE
    assert by_horizon_bench[1].status is BenchmarkReturnStatus.MISSING_ANCHOR_PRICE
    assert by_horizon_abn[1].status is AbnormalReturnStatus.BENCHMARK_RETURN_UNAVAILABLE
    assert by_horizon_abn[1].abnormal_return is None


def test_benchmark_missing_one_target_endpoint(
    company: Company,
    source: DataSource,
    listing: Listing,
    benchmark: Benchmark,
    benchmark_source: DataSource,
    db_session: Session,
) -> None:
    sessions = _weekday_sessions(date(2026, 3, 2), 10)
    _put_stock_bars(listing, source, db_session, sessions, Decimal("100.00"))
    # Benchmark has the anchor and horizon-1's target, but a gap right where
    # horizon 5's target would be.
    present = [sessions[2], sessions[3], *sessions[8:]]
    db_session.add_all(
        _level(benchmark, benchmark_source, day, Decimal("1000.00") + Decimal(index))
        for index, day in enumerate(present)
    )
    db_session.commit()
    event = _event(
        company, source, db_session, timing=EarningsTiming.BEFORE_MARKET, event_date=sessions[2]
    )

    result = analyze_earnings_event(
        db_session, event, mode=ResearchMode.DESCRIPTIVE, horizons=[1, 5], benchmark_id=benchmark.id
    )

    by_horizon_bench = {br.horizon: br for br in result.benchmark_returns}
    by_horizon_abn = {ar.horizon: ar for ar in result.abnormal_returns}

    assert by_horizon_bench[1].status is BenchmarkReturnStatus.AVAILABLE
    assert by_horizon_abn[1].status is AbnormalReturnStatus.AVAILABLE
    assert by_horizon_bench[5].status is BenchmarkReturnStatus.MISSING_TARGET_PRICE
    assert by_horizon_abn[5].status is AbnormalReturnStatus.BENCHMARK_RETURN_UNAVAILABLE


# ── multiple benchmark sources ───────────────────────────────────────────────


def test_ambiguous_benchmark_price_source(
    company: Company,
    source: DataSource,
    listing: Listing,
    benchmark: Benchmark,
    db_session: Session,
) -> None:
    source_a = DataSource(name="event-study-bench-a", provider_type=ProviderType.MARKET_DATA_VENDOR)
    source_b = DataSource(name="event-study-bench-b", provider_type=ProviderType.MARKET_DATA_VENDOR)
    db_session.add_all([source_a, source_b])
    db_session.commit()

    sessions = _weekday_sessions(date(2026, 3, 2), 10)
    _put_stock_bars(listing, source, db_session, sessions, Decimal("100.00"))
    db_session.add_all(
        [
            _level(benchmark, source_a, sessions[2], Decimal("1000.00")),
            _level(benchmark, source_b, sessions[3], Decimal("1010.00")),
        ]
    )
    db_session.commit()
    event = _event(
        company, source, db_session, timing=EarningsTiming.BEFORE_MARKET, event_date=sessions[2]
    )

    result = analyze_earnings_event(
        db_session, event, mode=ResearchMode.DESCRIPTIVE, horizons=[1], benchmark_id=benchmark.id
    )

    by_horizon_bench = {br.horizon: br for br in result.benchmark_returns}
    by_horizon_abn = {ar.horizon: ar for ar in result.abnormal_returns}
    assert by_horizon_bench[1].status is BenchmarkReturnStatus.AMBIGUOUS_BENCHMARK_PRICE_SOURCE
    assert by_horizon_abn[1].status is AbnormalReturnStatus.BENCHMARK_RETURN_UNAVAILABLE


def test_explicit_benchmark_source_id_resolves_the_ambiguity(
    company: Company,
    source: DataSource,
    listing: Listing,
    benchmark: Benchmark,
    db_session: Session,
) -> None:
    source_a = DataSource(name="event-study-bench-c", provider_type=ProviderType.MARKET_DATA_VENDOR)
    source_b = DataSource(name="event-study-bench-d", provider_type=ProviderType.MARKET_DATA_VENDOR)
    db_session.add_all([source_a, source_b])
    db_session.commit()

    sessions = _weekday_sessions(date(2026, 3, 2), 10)
    _put_stock_bars(listing, source, db_session, sessions, Decimal("100.00"))
    db_session.add_all(
        [
            _level(benchmark, source_a, sessions[2], Decimal("1000.00")),
            _level(benchmark, source_a, sessions[3], Decimal("1010.00")),
            _level(benchmark, source_b, sessions[3], Decimal("9999.00")),
        ]
    )
    db_session.commit()
    event = _event(
        company, source, db_session, timing=EarningsTiming.BEFORE_MARKET, event_date=sessions[2]
    )

    result = analyze_earnings_event(
        db_session,
        event,
        mode=ResearchMode.DESCRIPTIVE,
        horizons=[1],
        benchmark_id=benchmark.id,
        benchmark_source_id=source_a.id,
    )

    by_horizon_bench = {br.horizon: br for br in result.benchmark_returns}
    assert by_horizon_bench[1].status is BenchmarkReturnStatus.AVAILABLE
    assert by_horizon_bench[1].target_close == Decimal("1010.00")


# ── no benchmark requested at all ───────────────────────────────────────────


def test_no_benchmark_requested(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    sessions = _weekday_sessions(date(2026, 3, 2), 10)
    _put_stock_bars(listing, source, db_session, sessions, Decimal("100.00"))
    event = _event(
        company, source, db_session, timing=EarningsTiming.BEFORE_MARKET, event_date=sessions[2]
    )

    result = analyze_earnings_event(db_session, event, mode=ResearchMode.DESCRIPTIVE, horizons=[1])

    assert result.benchmark_id is None
    by_horizon_bench = {br.horizon: br for br in result.benchmark_returns}
    by_horizon_abn = {ar.horizon: ar for ar in result.abnormal_returns}
    assert by_horizon_bench[1].status is BenchmarkReturnStatus.NO_BENCHMARK
    assert by_horizon_abn[1].status is AbnormalReturnStatus.BENCHMARK_RETURN_UNAVAILABLE


# ── partial horizon availability never fails the whole study ───────────────


def test_partial_horizon_availability(
    company: Company,
    source: DataSource,
    listing: Listing,
    benchmark: Benchmark,
    benchmark_source: DataSource,
    db_session: Session,
) -> None:
    sessions = _weekday_sessions(date(2026, 3, 2), 4)  # anchor + only 2 forward sessions
    _put_stock_bars(listing, source, db_session, sessions, Decimal("100.00"))
    _put_benchmark_levels(benchmark, benchmark_source, db_session, sessions, Decimal("1000.00"))
    event = _event(
        company, source, db_session, timing=EarningsTiming.BEFORE_MARKET, event_date=sessions[0]
    )

    result = analyze_earnings_event(
        db_session,
        event,
        mode=ResearchMode.DESCRIPTIVE,
        horizons=[1, 5, 20],
        benchmark_id=benchmark.id,
    )

    by_horizon_stock = {fr.horizon: fr for fr in result.stock_returns.forward_returns}
    by_horizon_bench = {br.horizon: br for br in result.benchmark_returns}
    by_horizon_abn = {ar.horizon: ar for ar in result.abnormal_returns}

    assert by_horizon_stock[1].status is ForwardReturnStatus.AVAILABLE
    assert by_horizon_bench[1].status is BenchmarkReturnStatus.AVAILABLE
    assert by_horizon_abn[1].status is AbnormalReturnStatus.AVAILABLE

    for horizon in (5, 20):
        assert by_horizon_stock[horizon].status is ForwardReturnStatus.MISSING_TARGET_PRICE
        assert by_horizon_bench[horizon].status is BenchmarkReturnStatus.NO_STOCK_RETURN
        assert by_horizon_abn[horizon].status is AbnormalReturnStatus.STOCK_RETURN_UNAVAILABLE

    # EPS surprise must remain available even though two of three horizons failed.
    assert result.eps_surprise.surprise_ratio == Decimal("0.20")


# ── point-in-time discipline ─────────────────────────────────────────────────


def test_point_in_time_eligible_event(
    company: Company,
    source: DataSource,
    listing: Listing,
    benchmark: Benchmark,
    benchmark_source: DataSource,
    db_session: Session,
) -> None:
    sessions = _weekday_sessions(date(2026, 3, 2), 10)
    _put_stock_bars(listing, source, db_session, sessions, Decimal("100.00"))
    _put_benchmark_levels(benchmark, benchmark_source, db_session, sessions, Decimal("1000.00"))
    observed = datetime(2026, 3, 4, 21, 5, tzinfo=UTC)
    event = _event(
        company,
        source,
        db_session,
        timing=EarningsTiming.BEFORE_MARKET,
        event_date=sessions[2],
        observed_at=observed,
        observed_at_basis=AvailabilityBasis.SOURCE_PUBLICATION,
    )

    result = analyze_earnings_event(
        db_session, event, mode=ResearchMode.POINT_IN_TIME, horizons=[1], benchmark_id=benchmark.id
    )

    assert result.point_in_time_eligibility.value == "ELIGIBLE"
    by_horizon_stock = {fr.horizon: fr for fr in result.stock_returns.forward_returns}
    by_horizon_bench = {br.horizon: br for br in result.benchmark_returns}
    assert by_horizon_stock[1].status is ForwardReturnStatus.AVAILABLE
    assert by_horizon_bench[1].status is BenchmarkReturnStatus.AVAILABLE
    assert result.eps_surprise.surprise_ratio is not None


def test_point_in_time_ineligible_event(
    company: Company,
    source: DataSource,
    listing: Listing,
    benchmark: Benchmark,
    benchmark_source: DataSource,
    db_session: Session,
) -> None:
    sessions = _weekday_sessions(date(2026, 3, 2), 10)
    _put_stock_bars(listing, source, db_session, sessions, Decimal("100.00"))
    _put_benchmark_levels(benchmark, benchmark_source, db_session, sessions, Decimal("1000.00"))
    event = _event(
        company,
        source,
        db_session,
        timing=EarningsTiming.BEFORE_MARKET,
        event_date=sessions[2],
        observed_at=None,
    )

    result = analyze_earnings_event(
        db_session, event, mode=ResearchMode.POINT_IN_TIME, horizons=[1], benchmark_id=benchmark.id
    )

    assert result.point_in_time_eligibility.value == "INELIGIBLE_NO_AVAILABILITY_EVIDENCE"
    # EPS surprise is withheld under the point-in-time gate.
    assert result.eps_surprise.absolute_surprise is None
    assert result.eps_surprise.surprise_ratio is None
    # Session alignment/forward returns are withheld too -- the whole study
    # honestly reports "not point-in-time eligible", not a half-tradable answer.
    by_horizon_stock = {fr.horizon: fr for fr in result.stock_returns.forward_returns}
    assert by_horizon_stock[1].status is ForwardReturnStatus.NO_ALIGNMENT


def test_descriptive_event_with_null_observed_at(
    company: Company,
    source: DataSource,
    listing: Listing,
    benchmark: Benchmark,
    benchmark_source: DataSource,
    db_session: Session,
) -> None:
    """DESCRIPTIVE mode may still fully analyze an event with no availability evidence."""
    sessions = _weekday_sessions(date(2026, 3, 2), 10)
    _put_stock_bars(listing, source, db_session, sessions, Decimal("100.00"))
    _put_benchmark_levels(benchmark, benchmark_source, db_session, sessions, Decimal("1000.00"))
    event = _event(
        company,
        source,
        db_session,
        timing=EarningsTiming.BEFORE_MARKET,
        event_date=sessions[2],
        observed_at=None,
    )

    result = analyze_earnings_event(
        db_session, event, mode=ResearchMode.DESCRIPTIVE, horizons=[1], benchmark_id=benchmark.id
    )

    assert result.point_in_time_eligibility.value == "NOT_APPLICABLE"
    assert result.eps_surprise.surprise_ratio is not None
    by_horizon_stock = {fr.horizon: fr for fr in result.stock_returns.forward_returns}
    assert by_horizon_stock[1].status is ForwardReturnStatus.AVAILABLE


# ── no future revision leakage ───────────────────────────────────────────────


def test_the_study_respects_the_externally_selected_revision_not_a_future_one(
    company: Company,
    source: DataSource,
    listing: Listing,
    db_session: Session,
) -> None:
    """``analyze_earnings_event`` never re-selects a revision -- it studies exactly
    the row it was given, even when a later, more-accurate revision exists in
    the database for the same fiscal period."""
    sessions = _weekday_sessions(date(2026, 3, 2), 10)
    _put_stock_bars(listing, source, db_session, sessions, Decimal("100.00"))

    decision_time = datetime(2026, 3, 10, tzinfo=UTC)
    preliminary = _event(
        company,
        source,
        db_session,
        timing=EarningsTiming.BEFORE_MARKET,
        event_date=sessions[2],
        reported_eps=Decimal("1.00"),
        estimated_eps=Decimal("1.00"),
        observed_at=datetime(2026, 3, 4, 20, 0, tzinfo=UTC),
        observed_at_basis=AvailabilityBasis.SOURCE_PUBLICATION,
    )
    restatement = _event(
        company,
        source,
        db_session,
        timing=EarningsTiming.BEFORE_MARKET,
        event_date=sessions[2],
        reported_eps=Decimal("0.50"),  # a much worse restated figure
        estimated_eps=Decimal("1.00"),
        observed_at=datetime(2026, 4, 1, tzinfo=UTC),  # after decision_time
        observed_at_basis=AvailabilityBasis.SOURCE_PUBLICATION,
    )

    selected = select_known_as_of([preliminary, restatement], as_of=decision_time)
    assert selected is preliminary

    result = analyze_earnings_event(
        db_session, selected, mode=ResearchMode.POINT_IN_TIME, horizons=[1]
    )

    assert result.event_id == preliminary.id
    assert result.event_id != restatement.id
    # The preliminary figure (zero surprise) is used, never the restatement's.
    assert result.eps_surprise.absolute_surprise == Decimal("0.00")


# ── Step 10.5: research reads SPLIT_ADJUSTED only, whatever else exists ─────


def _as_traded_bar(
    listing: Listing, source: DataSource, day: date, close: Decimal
) -> DailyPriceBar:
    bar = _bar(listing, source, day, close)
    bar.price_basis = PriceBasis.AS_TRADED
    return bar


def _as_traded_level(
    benchmark: Benchmark, source: DataSource, day: date, close: Decimal
) -> BenchmarkPriceBar:
    level = _level(benchmark, source, day, close)
    level.price_basis = PriceBasis.AS_TRADED
    return level


def test_wildly_different_as_traded_prices_never_change_the_event_study(
    company: Company,
    source: DataSource,
    listing: Listing,
    benchmark: Benchmark,
    benchmark_source: DataSource,
    db_session: Session,
) -> None:
    """Same numbers as the deterministic example, plus absurd AS_TRADED rows.

    Stock/benchmark/abnormal returns, the alignment and the study as a whole
    must be identical with and without them: research selects SPLIT_ADJUSTED
    explicitly and never relies on row order or "the only row".
    """
    sessions = _weekday_sessions(date(2026, 3, 2), 3)
    db_session.add_all(
        [
            _bar(listing, source, sessions[0], Decimal("100")),
            _bar(listing, source, sessions[1], Decimal("110")),
            _bar(listing, source, sessions[2], Decimal("121")),
            _level(benchmark, benchmark_source, sessions[0], Decimal("200")),
            _level(benchmark, benchmark_source, sessions[1], Decimal("210")),
            _level(benchmark, benchmark_source, sessions[2], Decimal("220.50")),
        ]
    )
    db_session.commit()
    event = _event(
        company, source, db_session, timing=EarningsTiming.BEFORE_MARKET, event_date=sessions[0]
    )

    def study() -> object:
        return analyze_earnings_event(
            db_session,
            event,
            mode=ResearchMode.DESCRIPTIVE,
            horizons=[1, 2],
            benchmark_id=benchmark.id,
        )

    baseline = study()

    # AS_TRADED rows, inserted *first in id order is irrelevant*: values are
    # chosen so any accidental read would visibly change every return.
    db_session.add_all(
        [
            _as_traded_bar(listing, source, sessions[0], Decimal("1")),
            _as_traded_bar(listing, source, sessions[1], Decimal("9999")),
            _as_traded_bar(listing, source, sessions[2], Decimal("5")),
            _as_traded_level(benchmark, benchmark_source, sessions[0], Decimal("7")),
            _as_traded_level(benchmark, benchmark_source, sessions[1], Decimal("70000")),
            _as_traded_level(benchmark, benchmark_source, sessions[2], Decimal("3")),
        ]
    )
    db_session.commit()

    with_both_bases = study()

    assert with_both_bases == baseline


def test_a_session_present_only_as_traded_is_not_a_research_session(
    company: Company,
    source: DataSource,
    listing: Listing,
    benchmark: Benchmark,
    benchmark_source: DataSource,
    db_session: Session,
) -> None:
    """An AS_TRADED-only bar must not create a session the split-adjusted series lacks."""
    sessions = _weekday_sessions(date(2026, 3, 2), 3)
    db_session.add_all(
        [
            _bar(listing, source, sessions[0], Decimal("100")),
            _as_traded_bar(listing, source, sessions[1], Decimal("110")),
        ]
    )
    db_session.commit()
    event = _event(
        company, source, db_session, timing=EarningsTiming.BEFORE_MARKET, event_date=sessions[0]
    )

    result = analyze_earnings_event(
        db_session, event, mode=ResearchMode.DESCRIPTIVE, horizons=[1], benchmark_id=None
    )

    assert result.stock_returns.forward_returns[0].status is (
        ForwardReturnStatus.MISSING_TARGET_PRICE
    )


def test_a_second_basis_does_not_create_source_ambiguity(
    company: Company,
    source: DataSource,
    listing: Listing,
    benchmark: Benchmark,
    benchmark_source: DataSource,
    db_session: Session,
) -> None:
    """Both bases from the *same* source must not look like several sources, or several rows."""
    sessions = _weekday_sessions(date(2026, 3, 2), 2)
    db_session.add_all(
        [
            _bar(listing, source, sessions[0], Decimal("100")),
            _bar(listing, source, sessions[1], Decimal("110")),
            _as_traded_bar(listing, source, sessions[0], Decimal("1000")),
            _as_traded_bar(listing, source, sessions[1], Decimal("1100")),
            _level(benchmark, benchmark_source, sessions[0], Decimal("200")),
            _level(benchmark, benchmark_source, sessions[1], Decimal("210")),
            _as_traded_level(benchmark, benchmark_source, sessions[0], Decimal("2000")),
            _as_traded_level(benchmark, benchmark_source, sessions[1], Decimal("2100")),
        ]
    )
    db_session.commit()
    event = _event(
        company, source, db_session, timing=EarningsTiming.BEFORE_MARKET, event_date=sessions[0]
    )

    result = analyze_earnings_event(
        db_session, event, mode=ResearchMode.DESCRIPTIVE, horizons=[1], benchmark_id=benchmark.id
    )

    assert result.stock_returns.forward_returns[0].return_value == Decimal("0.10")
    assert result.benchmark_returns[0].benchmark_return == Decimal("0.05")
    assert result.abnormal_returns[0].status is AbnormalReturnStatus.AVAILABLE
