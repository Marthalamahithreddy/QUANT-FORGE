"""Step 11.2E: missing stored bars never move an anchor or a horizon target (real DB, real calendar).

Every scenario runs the production entry point (``analyze_earnings_event``) with the real
``exchange_calendars`` adapter against stored rows. The event is AFTER_MARKET on Wednesday
2026-03-04, so the proven anchor is Thursday 2026-03-05 and the proven sessions after it are
03-06, 03-09, 03-10, 03-11, 03-12, 03-13 ... A missing STORED bar is a missing price: the horizon
is unavailable and its target date is never replaced by a later stored bar. Real exchange holidays
(Good Friday 2026-04-03) are the only non-sessions.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.models.benchmark import Benchmark, BenchmarkPriceBar
from quantforge.models.company import Company
from quantforge.models.earnings import EarningsEvent
from quantforge.models.enums import (
    EarningsTiming,
    PriceBasis,
    ProviderType,
    SecurityType,
)
from quantforge.models.market import DailyPriceBar
from quantforge.models.provenance import DataSource
from quantforge.models.security import Exchange, Listing, Security
from quantforge.research.abnormal_returns import AbnormalReturnStatus
from quantforge.research.availability import ResearchMode
from quantforge.research.benchmark_returns import BenchmarkReturnStatus
from quantforge.research.calendar import AlignmentStatus
from quantforge.research.event_study import EventStudyResult, analyze_earnings_event
from quantforge.research.returns import ForwardReturn, ForwardReturnStatus

pytestmark = pytest.mark.integration

_EVENT_DAY = date(2026, 3, 4)  # Wednesday
_ANCHOR = date(2026, 3, 5)
# proven sessions after the anchor (all real XNYS sessions)
_P1, _P2, _P3, _P4, _P5 = (
    date(2026, 3, 6),
    date(2026, 3, 9),
    date(2026, 3, 10),
    date(2026, 3, 11),
    date(2026, 3, 12),
)
_SESSIONS = [_ANCHOR, _P1, _P2, _P3, _P4, _P5, date(2026, 3, 13)]


def _price(day: date) -> Decimal:
    return Decimal(100) + Decimal((day - _ANCHOR).days)


@pytest.fixture
def source(db_session: Session) -> DataSource:
    row = DataSource(name="session-gap-stock", provider_type=ProviderType.MARKET_DATA_VENDOR)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def benchmark_source(db_session: Session) -> DataSource:
    row = DataSource(name="session-gap-benchmark", provider_type=ProviderType.MARKET_DATA_VENDOR)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def company(db_session: Session) -> Company:
    row = Company(legal_name="Session Gap Test Co", display_name="Session Gap Test")
    db_session.add(row)
    db_session.commit()
    return row


def _exchange(db_session: Session, mic: str) -> Exchange:
    existing = db_session.scalar(sa.select(Exchange).where(Exchange.mic == mic))
    if existing is not None:
        return existing
    row = Exchange(mic=mic, name=f"Test {mic}", timezone="America/New_York")
    db_session.add(row)
    db_session.commit()
    return row


def _listing(db_session: Session, company: Company, mic: str, ticker: str = "SGAP") -> Listing:
    security = Security(
        company_id=company.id, security_type=SecurityType.EQUITY, name="Ordinary", currency="USD"
    )
    db_session.add(security)
    db_session.commit()
    row = Listing(
        security_id=security.id,
        exchange_id=_exchange(db_session, mic).id,
        ticker=ticker,
        valid_from=date(2018, 1, 2),
        is_primary=True,
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def listing(db_session: Session, company: Company) -> Listing:
    return _listing(db_session, company, "XNYS")


@pytest.fixture
def benchmark(db_session: Session) -> Benchmark:
    row = Benchmark(name="Session Gap Index", symbol="SGPI", currency="USD")
    db_session.add(row)
    db_session.commit()
    return row


def _bars(db_session: Session, listing: Listing, source: DataSource, days: list[date]) -> None:
    db_session.add_all(
        DailyPriceBar(
            price_basis=PriceBasis.SPLIT_ADJUSTED,
            listing_id=listing.id,
            trading_date=day,
            open=_price(day),
            high=_price(day),
            low=_price(day),
            close=_price(day),
            source_id=source.id,
        )
        for day in days
    )
    db_session.commit()


def _levels(
    db_session: Session, benchmark: Benchmark, source: DataSource, days: list[date]
) -> None:
    db_session.add_all(
        BenchmarkPriceBar(
            price_basis=PriceBasis.SPLIT_ADJUSTED,
            benchmark_id=benchmark.id,
            trading_date=day,
            close=Decimal(200) + Decimal((day - _ANCHOR).days),
            source_id=source.id,
        )
        for day in days
    )
    db_session.commit()


def _event(
    db_session: Session,
    company: Company,
    source: DataSource,
    *,
    timing: EarningsTiming = EarningsTiming.AFTER_MARKET,
    event_date: date = _EVENT_DAY,
) -> EarningsEvent:
    row = EarningsEvent(
        company_id=company.id,
        fiscal_year=2026,
        fiscal_quarter=1,
        event_date=event_date,
        timing=timing,
        reported_eps=Decimal("1.2000"),
        estimated_eps=Decimal("1.0000"),
        source_id=source.id,
    )
    db_session.add(row)
    db_session.commit()
    return row


def _study(
    db_session: Session,
    event: EarningsEvent,
    *,
    horizons: tuple[int, ...] = (1, 2, 3, 4, 5),
    benchmark: Benchmark | None = None,
) -> EventStudyResult:
    return analyze_earnings_event(
        db_session,
        event,
        mode=ResearchMode.DESCRIPTIVE,
        horizons=horizons,
        benchmark_id=None if benchmark is None else benchmark.id,
    )


def _by_h(study: EventStudyResult) -> dict[int, ForwardReturn]:
    return {fr.horizon: fr for fr in study.stock_returns.forward_returns}


# ── anchor ───────────────────────────────────────────────────────────────────


def test_complete_session_sequence(
    db_session: Session, company: Company, source: DataSource, listing: Listing
) -> None:
    _bars(db_session, listing, source, _SESSIONS)
    study = _study(db_session, _event(db_session, company, source))
    assert study.alignment.session_alignment is not None
    assert study.alignment.session_alignment.first_post_event_session == _ANCHOR
    assert study.alignment.session_calendar_mic == "XNYS"
    by_h = _by_h(study)
    for horizon, target in zip((1, 2, 3, 4, 5), (_P1, _P2, _P3, _P4, _P5), strict=True):
        assert by_h[horizon].status is ForwardReturnStatus.AVAILABLE
        assert by_h[horizon].target_session == target


def test_missing_anchor_bar_is_not_replaced_by_the_next_stored_bar(
    db_session: Session, company: Company, source: DataSource, listing: Listing
) -> None:
    _bars(db_session, listing, source, [d for d in _SESSIONS if d != _ANCHOR])
    study = _study(db_session, _event(db_session, company, source))
    assert study.alignment.session_alignment is not None
    # the anchor is the calendar's, even though no bar exists for it
    assert study.alignment.session_alignment.first_post_event_session == _ANCHOR
    assert study.stock_returns.anchor_session == _ANCHOR
    for forward in study.stock_returns.forward_returns:
        assert forward.status is ForwardReturnStatus.MISSING_ANCHOR_PRICE
        assert forward.anchor_session == _ANCHOR
        assert forward.return_value is None


# ── interior / target ────────────────────────────────────────────────────────


def test_missing_interior_session_does_not_shift_later_targets(
    db_session: Session, company: Company, source: DataSource, listing: Listing
) -> None:
    """Stored D0 D1 D2(absent) D3 D4 D5: +5 is D5 (never D6) and +2 is unavailable."""
    _bars(db_session, listing, source, [d for d in _SESSIONS if d != _P2])
    by_h = _by_h(_study(db_session, _event(db_session, company, source)))
    assert by_h[1].status is ForwardReturnStatus.AVAILABLE
    assert by_h[2].status is ForwardReturnStatus.MISSING_TARGET_PRICE
    assert by_h[2].target_session == _P2
    assert by_h[2].return_value is None
    assert by_h[3].target_session == _P3
    assert by_h[5].status is ForwardReturnStatus.AVAILABLE
    assert by_h[5].target_session == _P5


def test_missing_target_is_unavailable_and_not_replaced_by_a_later_bar(
    db_session: Session, company: Company, source: DataSource, listing: Listing
) -> None:
    _bars(db_session, listing, source, [d for d in _SESSIONS if d != _P5])  # 03-13 present
    by_h = _by_h(_study(db_session, _event(db_session, company, source)))
    assert by_h[5].status is ForwardReturnStatus.MISSING_TARGET_PRICE
    assert by_h[5].target_session == _P5
    assert by_h[5].target_close is None


def test_plus_one_missing_but_later_bars_present_and_horizon_independence(
    db_session: Session, company: Company, source: DataSource, listing: Listing
) -> None:
    _bars(db_session, listing, source, [d for d in _SESSIONS if d != _P1])
    by_h = _by_h(_study(db_session, _event(db_session, company, source), horizons=(1, 2, 20)))
    assert by_h[1].status is ForwardReturnStatus.MISSING_TARGET_PRICE
    assert by_h[2].status is ForwardReturnStatus.AVAILABLE
    assert by_h[20].status is ForwardReturnStatus.MISSING_TARGET_PRICE
    assert by_h[20].target_session == date(
        2026, 4, 2
    )  # the 20th real session (Good Friday skipped)


def test_a_real_holiday_never_consumes_a_horizon_slot(
    db_session: Session, company: Company, source: DataSource, listing: Listing
) -> None:
    """AMC Wed 2026-04-01: anchor Thu 04-02; Good Friday 04-03 is closed; +1 is Mon 04-06."""
    days = [date(2026, 4, 2), date(2026, 4, 6), date(2026, 4, 7)]
    _bars(db_session, listing, source, days)
    event = _event(db_session, company, source, event_date=date(2026, 4, 1))
    by_h = _by_h(_study(db_session, event, horizons=(1, 2)))
    assert by_h[1].status is ForwardReturnStatus.AVAILABLE
    assert by_h[1].target_session == date(2026, 4, 6)
    assert by_h[2].target_session == date(2026, 4, 7)


def test_a_bar_stored_on_a_closed_day_is_never_counted(
    db_session: Session, company: Company, source: DataSource, listing: Listing
) -> None:
    days = [date(2026, 4, 2), date(2026, 4, 3), date(2026, 4, 6)]  # 04-03 is Good Friday
    _bars(db_session, listing, source, days)
    event = _event(db_session, company, source, event_date=date(2026, 4, 1))
    by_h = _by_h(_study(db_session, event, horizons=(1,)))
    assert by_h[1].target_session == date(2026, 4, 6)


# ── timing ───────────────────────────────────────────────────────────────────


def test_amc_does_not_require_the_event_day_bar(
    db_session: Session, company: Company, source: DataSource, listing: Listing
) -> None:
    _bars(db_session, listing, source, _SESSIONS)  # no bar on the event day 03-04
    by_h = _by_h(_study(db_session, _event(db_session, company, source), horizons=(1,)))
    assert by_h[1].status is ForwardReturnStatus.AVAILABLE


def test_bmo_missing_event_day_bar_makes_the_anchor_missing_not_shifted(
    db_session: Session, company: Company, source: DataSource, listing: Listing
) -> None:
    _bars(db_session, listing, source, _SESSIONS)  # the BMO anchor is the event day 03-04: no bar
    event = _event(db_session, company, source, timing=EarningsTiming.BEFORE_MARKET)
    study = _study(db_session, event, horizons=(1,))
    assert study.stock_returns.anchor_session == _EVENT_DAY
    only = study.stock_returns.forward_returns[0]
    assert only.status is ForwardReturnStatus.MISSING_ANCHOR_PRICE
    assert only.anchor_session == _EVENT_DAY


def test_bmo_with_the_event_day_bar_anchors_on_the_event_day(
    db_session: Session, company: Company, source: DataSource, listing: Listing
) -> None:
    _bars(db_session, listing, source, [_EVENT_DAY, *_SESSIONS])
    event = _event(db_session, company, source, timing=EarningsTiming.BEFORE_MARKET)
    only = _study(db_session, event, horizons=(1,)).stock_returns.forward_returns[0]
    assert only.status is ForwardReturnStatus.AVAILABLE
    assert only.anchor_session == _EVENT_DAY
    assert only.target_session == _ANCHOR


# ── benchmark alignment ──────────────────────────────────────────────────────


def test_benchmark_uses_the_stocks_own_proven_dates(
    db_session: Session,
    company: Company,
    source: DataSource,
    listing: Listing,
    benchmark: Benchmark,
    benchmark_source: DataSource,
) -> None:
    _bars(db_session, listing, source, _SESSIONS)
    _levels(db_session, benchmark, benchmark_source, _SESSIONS)
    study = _study(db_session, _event(db_session, company, source), benchmark=benchmark)
    for stock, bench in zip(
        study.stock_returns.forward_returns, study.benchmark_returns, strict=True
    ):
        assert bench.status is BenchmarkReturnStatus.AVAILABLE
        assert (bench.anchor_session, bench.target_session) == (
            stock.anchor_session,
            stock.target_session,
        )


def test_benchmark_missing_a_required_session_does_not_shift_or_hide_the_stock_return(
    db_session: Session,
    company: Company,
    source: DataSource,
    listing: Listing,
    benchmark: Benchmark,
    benchmark_source: DataSource,
) -> None:
    _bars(db_session, listing, source, _SESSIONS)
    # benchmark lacks 03-09 (the stock's +2 target) but has 03-10 etc.
    _levels(db_session, benchmark, benchmark_source, [d for d in _SESSIONS if d != _P2])
    study = _study(db_session, _event(db_session, company, source), benchmark=benchmark)
    by_stock = {s.horizon: s for s in study.stock_returns.forward_returns}
    by_bench = {b.horizon: b for b in study.benchmark_returns}
    assert by_stock[2].status is ForwardReturnStatus.AVAILABLE and by_stock[2].target_session == _P2
    assert by_bench[2].status is BenchmarkReturnStatus.MISSING_TARGET_PRICE
    assert by_bench[2].target_session == _P2
    assert by_bench[3].status is BenchmarkReturnStatus.AVAILABLE
    assert by_bench[3].target_session == _P3
    abnormal = {a.horizon: a for a in study.abnormal_returns}
    assert abnormal[2].status is not AbnormalReturnStatus.AVAILABLE
    assert abnormal[3].status is AbnormalReturnStatus.AVAILABLE


def test_benchmark_anchor_missing_makes_every_benchmark_return_unavailable_not_shifted(
    db_session: Session,
    company: Company,
    source: DataSource,
    listing: Listing,
    benchmark: Benchmark,
    benchmark_source: DataSource,
) -> None:
    _bars(db_session, listing, source, _SESSIONS)
    _levels(db_session, benchmark, benchmark_source, [d for d in _SESSIONS if d != _ANCHOR])
    study = _study(db_session, _event(db_session, company, source), benchmark=benchmark)
    assert all(
        b.status is BenchmarkReturnStatus.MISSING_ANCHOR_PRICE for b in study.benchmark_returns
    )
    assert all(
        s.status is ForwardReturnStatus.AVAILABLE for s in study.stock_returns.forward_returns
    )


def test_stock_missing_and_benchmark_complete(
    db_session: Session,
    company: Company,
    source: DataSource,
    listing: Listing,
    benchmark: Benchmark,
    benchmark_source: DataSource,
) -> None:
    _bars(db_session, listing, source, [d for d in _SESSIONS if d != _P3])
    _levels(db_session, benchmark, benchmark_source, _SESSIONS)
    study = _study(db_session, _event(db_session, company, source), benchmark=benchmark)
    by_bench = {b.horizon: b for b in study.benchmark_returns}
    assert by_bench[3].status is BenchmarkReturnStatus.NO_STOCK_RETURN
    assert by_bench[4].status is BenchmarkReturnStatus.AVAILABLE
    assert by_bench[4].target_session == _P4


# ── refusal instead of fallback ──────────────────────────────────────────────


def test_an_unsupported_venue_refuses_instead_of_falling_back_to_stored_bars(
    db_session: Session, company: Company, source: DataSource
) -> None:
    listing = _listing(db_session, company, "XLON", ticker="LGAP")
    _bars(db_session, listing, source, _SESSIONS)
    study = _study(db_session, _event(db_session, company, source))
    assert study.alignment.session_alignment is not None
    assert study.alignment.session_alignment.status is AlignmentStatus.NO_SESSION_DATA
    assert study.alignment.session_calendar_mic == "XLON"
    assert all(
        s.status is ForwardReturnStatus.NO_ALIGNMENT for s in study.stock_returns.forward_returns
    )


def test_no_bars_at_all_is_a_missing_anchor_price_not_an_alignment_failure(
    db_session: Session, company: Company, source: DataSource, listing: Listing
) -> None:
    study = _study(db_session, _event(db_session, company, source))
    assert study.alignment.session_alignment is not None
    assert study.alignment.session_alignment.status is AlignmentStatus.ALIGNED
    assert all(
        s.status is ForwardReturnStatus.MISSING_ANCHOR_PRICE
        for s in study.stock_returns.forward_returns
    )


# ── query counts ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("horizons", [(1,), (1, 2, 3), tuple(range(1, 11))])
def test_query_count_does_not_grow_with_the_number_of_horizons(
    db_session: Session,
    company: Company,
    source: DataSource,
    listing: Listing,
    horizons: tuple[int, ...],
) -> None:
    _bars(db_session, listing, source, _SESSIONS)
    event = _event(db_session, company, source)
    db_session.expire_all()
    statements: list[str] = []

    def _count(conn: object, cursor: object, statement: str, *args: object) -> None:
        statements.append(statement)

    engine = db_session.get_bind()
    sa.event.listen(engine, "before_cursor_execute", _count)
    try:
        _study(db_session, event, horizons=horizons)
    finally:
        sa.event.remove(engine, "before_cursor_execute", _count)
    # one count for every horizon list: the sessions are proven in memory, prices in ONE read
    assert len(statements) <= 12, statements
    recorded = len(statements)
    print(f"QUERY_COUNT horizons={len(horizons)} statements={recorded}")  # noqa: T201
    assert recorded == len(statements)
