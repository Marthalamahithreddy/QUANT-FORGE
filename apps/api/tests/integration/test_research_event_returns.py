"""Integration tests for the composed forward-return entry point.

Proves the full chain required by Step 8.4's focused integration coverage:
``EarningsEvent`` -> Step 8.1/8.2 alignment -> stored ``DailyPriceBar``
series -> forward-return calculation, for both a BEFORE_MARKET and an
AFTER_MARKET event. Pure return arithmetic is already covered without a
database in ``tests/test_research_returns.py``; these tests exist to prove
the wiring and the database-dependent edge cases (listing-validity crossing,
ambiguous price sources, no-alignment short-circuiting) that only exist once
real rows are involved.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from quantforge.models.company import Company
from quantforge.models.earnings import EarningsEvent
from quantforge.models.enums import EarningsTiming, PriceBasis, ProviderType, SecurityType
from quantforge.models.market import DailyPriceBar
from quantforge.models.provenance import DataSource
from quantforge.models.security import Exchange, Listing, Security
from quantforge.research.alignment import align_earnings_event
from quantforge.research.availability import ResearchMode
from quantforge.research.event_returns import compute_event_forward_returns
from quantforge.research.returns import ForwardReturnStatus

pytestmark = pytest.mark.integration


@pytest.fixture
def source(db_session: Session) -> DataSource:
    row = DataSource(name="event-returns-vendor", provider_type=ProviderType.MARKET_DATA_VENDOR)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def company(db_session: Session) -> Company:
    row = Company(legal_name="Event Returns Test Co", display_name="Event Returns Test")
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def exchange(db_session: Session) -> Exchange:
    row = Exchange(mic="XRTN", name="Event Returns Test Exchange", timezone="America/New_York")
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
        ticker="RTRN",
        valid_from=date(2018, 1, 2),
        is_primary=True,
    )
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


def _weekday_sessions(start: date, count: int) -> list[date]:
    sessions: list[date] = []
    day = start
    while len(sessions) < count:
        if day.weekday() < 5:
            sessions.append(day)
        day += timedelta(days=1)
    return sessions


def _put_bars(
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


def _event(
    company: Company, source: DataSource, db_session: Session, **overrides: object
) -> EarningsEvent:
    fields: dict[str, object] = {
        "company_id": company.id,
        "fiscal_year": 2026,
        "fiscal_quarter": 2,
        "event_date": date(2026, 3, 4),  # a Wednesday
        "timing": EarningsTiming.AFTER_MARKET,
        "reported_eps": Decimal("1.1000"),
        "source_id": source.id,
    }
    fields.update(overrides)
    row = EarningsEvent(**fields)
    db_session.add(row)
    db_session.commit()
    return row


# ── section P: EarningsEvent -> alignment -> stored bars -> forward returns ─


def test_before_market_event_end_to_end(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    sessions = _weekday_sessions(date(2026, 3, 2), 25)  # Monday through well past horizon 20
    prices = _put_bars(listing, source, db_session, sessions, Decimal("100.00"))
    event = _event(
        company, source, db_session, timing=EarningsTiming.BEFORE_MARKET, event_date=sessions[2]
    )

    alignment = align_earnings_event(db_session, event, mode=ResearchMode.DESCRIPTIVE)
    result = compute_event_forward_returns(db_session, alignment)

    assert result.anchor_session == sessions[2]
    assert result.price_source_id == source.id
    by_horizon = {fr.horizon: fr for fr in result.forward_returns}
    assert by_horizon[1].status is ForwardReturnStatus.AVAILABLE
    assert by_horizon[1].target_session == sessions[3]
    assert by_horizon[1].return_value == prices[3] / prices[2] - 1
    assert by_horizon[5].target_session == sessions[7]
    assert by_horizon[20].target_session == sessions[22]


def test_after_market_event_end_to_end(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    sessions = _weekday_sessions(date(2026, 3, 2), 25)
    prices = _put_bars(listing, source, db_session, sessions, Decimal("200.00"))
    event = _event(
        company, source, db_session, timing=EarningsTiming.AFTER_MARKET, event_date=sessions[2]
    )

    alignment = align_earnings_event(db_session, event, mode=ResearchMode.DESCRIPTIVE)
    result = compute_event_forward_returns(db_session, alignment)

    # AMC's anchor is the *next* session after the event date, inherited
    # directly from Step 8.2 alignment -- never recomputed here.
    assert alignment.session_alignment is not None
    assert alignment.session_alignment.first_post_event_session == sessions[3]
    assert result.anchor_session == sessions[3]

    by_horizon = {fr.horizon: fr for fr in result.forward_returns}
    assert by_horizon[1].target_session == sessions[4]
    assert by_horizon[1].return_value == prices[4] / prices[3] - 1


# ── UNKNOWN / DURING_MARKET never fabricate a return ────────────────────────


@pytest.mark.parametrize("timing", [EarningsTiming.UNKNOWN, EarningsTiming.DURING_MARKET])
def test_unresolved_timing_produces_no_fabricated_return(
    company: Company,
    source: DataSource,
    listing: Listing,
    db_session: Session,
    timing: EarningsTiming,
) -> None:
    sessions = _weekday_sessions(date(2026, 3, 2), 10)
    _put_bars(listing, source, db_session, sessions, Decimal("50.00"))
    event = _event(company, source, db_session, timing=timing, event_date=sessions[2])

    alignment = align_earnings_event(db_session, event, mode=ResearchMode.DESCRIPTIVE)
    result = compute_event_forward_returns(db_session, alignment)

    assert result.anchor_session is None
    for forward_return in result.forward_returns:
        assert forward_return.status is ForwardReturnStatus.NO_ALIGNMENT
        assert forward_return.return_value is None


@pytest.mark.parametrize("bad_horizons", [[], [0], [1, -3]])
def test_invalid_horizons_are_rejected_even_when_the_event_does_not_align(
    company: Company,
    source: DataSource,
    listing: Listing,
    db_session: Session,
    bad_horizons: list[int],
) -> None:
    """An UNKNOWN-timing event never aligns; validation must still fire first."""
    sessions = _weekday_sessions(date(2026, 3, 2), 10)
    _put_bars(listing, source, db_session, sessions, Decimal("50.00"))
    event = _event(
        company, source, db_session, timing=EarningsTiming.UNKNOWN, event_date=sessions[2]
    )
    alignment = align_earnings_event(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    with pytest.raises(ValueError, match="horizon"):
        compute_event_forward_returns(db_session, alignment, horizons=bad_horizons)


def test_no_listing_produces_no_fabricated_return(
    company: Company, source: DataSource, db_session: Session
) -> None:
    """No listing was ever ingested for this issuer -- alignment cannot even attempt a session."""
    event = _event(company, source, db_session, timing=EarningsTiming.BEFORE_MARKET)

    alignment = align_earnings_event(db_session, event, mode=ResearchMode.DESCRIPTIVE)
    result = compute_event_forward_returns(db_session, alignment)

    assert result.listing_id is None
    for forward_return in result.forward_returns:
        assert forward_return.status is ForwardReturnStatus.NO_ALIGNMENT


def test_point_in_time_ineligible_observation_produces_no_fabricated_return(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    sessions = _weekday_sessions(date(2026, 3, 2), 10)
    _put_bars(listing, source, db_session, sessions, Decimal("50.00"))
    event = _event(
        company,
        source,
        db_session,
        timing=EarningsTiming.AFTER_MARKET,
        event_date=sessions[2],
        observed_at=None,
    )

    alignment = align_earnings_event(db_session, event, mode=ResearchMode.POINT_IN_TIME)
    result = compute_event_forward_returns(db_session, alignment)

    for forward_return in result.forward_returns:
        assert forward_return.status is ForwardReturnStatus.NO_ALIGNMENT


# ── insufficient history end-to-end ─────────────────────────────────────────


def test_insufficient_forward_history_end_to_end(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    sessions = _weekday_sessions(date(2026, 3, 2), 4)  # anchor + only 2 forward sessions
    _put_bars(listing, source, db_session, sessions, Decimal("10.00"))
    event = _event(
        company, source, db_session, timing=EarningsTiming.BEFORE_MARKET, event_date=sessions[0]
    )

    alignment = align_earnings_event(db_session, event, mode=ResearchMode.DESCRIPTIVE)
    result = compute_event_forward_returns(db_session, alignment)

    by_horizon = {fr.horizon: fr for fr in result.forward_returns}
    assert by_horizon[1].status is ForwardReturnStatus.AVAILABLE
    assert by_horizon[5].status is ForwardReturnStatus.INSUFFICIENT_FORWARD_SESSIONS
    assert by_horizon[20].status is ForwardReturnStatus.INSUFFICIENT_FORWARD_SESSIONS


# ── missing anchor price ─────────────────────────────────────────────────────


def test_an_explicit_source_with_no_bars_at_all_is_a_missing_anchor_price(
    company: Company, listing: Listing, db_session: Session
) -> None:
    """Alignment can resolve an anchor from one vendor's bars while another vendor has none at all.

    The anchor session itself is real (vendor A reported it), but a caller
    explicitly restricting the return calculation to vendor B -- which never
    reported this listing at all -- must get an honest "no anchor price" for
    every horizon, not a return computed against vendor A's price by mistake.
    """
    source_a = DataSource(name="returns-vendor-e", provider_type=ProviderType.MARKET_DATA_VENDOR)
    source_b = DataSource(name="returns-vendor-f", provider_type=ProviderType.MARKET_DATA_VENDOR)
    db_session.add_all([source_a, source_b])
    db_session.commit()

    sessions = _weekday_sessions(date(2026, 3, 2), 5)
    _put_bars(listing, source_a, db_session, sessions, Decimal("10.00"))

    event = _event(
        company, source_a, db_session, timing=EarningsTiming.BEFORE_MARKET, event_date=sessions[0]
    )
    alignment = align_earnings_event(db_session, event, mode=ResearchMode.DESCRIPTIVE)
    assert alignment.session_alignment is not None
    assert alignment.session_alignment.first_post_event_session == sessions[0]

    result = compute_event_forward_returns(
        db_session, alignment, horizons=[1], source_id=source_b.id
    )

    assert result.price_source_id == source_b.id
    by_horizon = {fr.horizon: fr for fr in result.forward_returns}
    assert by_horizon[1].status is ForwardReturnStatus.MISSING_ANCHOR_PRICE
    assert by_horizon[1].return_value is None


# ── ambiguous price source, end-to-end ──────────────────────────────────────


def test_ambiguous_price_source_end_to_end(
    company: Company, listing: Listing, db_session: Session
) -> None:
    source_a = DataSource(name="returns-vendor-a", provider_type=ProviderType.MARKET_DATA_VENDOR)
    source_b = DataSource(name="returns-vendor-b", provider_type=ProviderType.MARKET_DATA_VENDOR)
    db_session.add_all([source_a, source_b])
    db_session.commit()

    sessions = _weekday_sessions(date(2026, 3, 2), 10)
    db_session.add(_bar(listing, source_a, sessions[2], Decimal("100.00")))
    db_session.add(_bar(listing, source_b, sessions[3], Decimal("101.00")))
    db_session.commit()

    event = _event(
        company, source_a, db_session, timing=EarningsTiming.BEFORE_MARKET, event_date=sessions[2]
    )
    alignment = align_earnings_event(db_session, event, mode=ResearchMode.DESCRIPTIVE)
    result = compute_event_forward_returns(db_session, alignment)

    for forward_return in result.forward_returns:
        assert forward_return.status is ForwardReturnStatus.AMBIGUOUS_PRICE_SOURCE
        assert forward_return.return_value is None


def test_an_explicit_source_id_resolves_what_would_otherwise_be_ambiguous(
    company: Company, listing: Listing, db_session: Session
) -> None:
    source_a = DataSource(name="returns-vendor-c", provider_type=ProviderType.MARKET_DATA_VENDOR)
    source_b = DataSource(name="returns-vendor-d", provider_type=ProviderType.MARKET_DATA_VENDOR)
    db_session.add_all([source_a, source_b])
    db_session.commit()

    sessions = _weekday_sessions(date(2026, 3, 2), 10)
    db_session.add_all(
        [
            _bar(listing, source_a, sessions[2], Decimal("100.00")),
            _bar(listing, source_a, sessions[3], Decimal("102.00")),
            _bar(listing, source_b, sessions[3], Decimal("999.00")),
        ]
    )
    db_session.commit()

    event = _event(
        company, source_a, db_session, timing=EarningsTiming.BEFORE_MARKET, event_date=sessions[2]
    )
    alignment = align_earnings_event(db_session, event, mode=ResearchMode.DESCRIPTIVE)
    result = compute_event_forward_returns(
        db_session, alignment, horizons=[1], source_id=source_a.id
    )

    assert result.price_source_id == source_a.id
    by_horizon = {fr.horizon: fr for fr in result.forward_returns}
    assert by_horizon[1].status is ForwardReturnStatus.AVAILABLE
    assert by_horizon[1].target_close == Decimal("102.00")


# ── listing-validity boundary: a ticker change mid-horizon ──────────────────


def test_a_ticker_change_mid_horizon_does_not_silently_cross_listings(
    company: Company,
    exchange: Exchange,
    security: Security,
    source: DataSource,
    db_session: Session,
) -> None:
    """Bars beyond the old listing's ticker change must not be picked up as if they still belonged to it.

    The old listing's own ``valid_to`` ends partway through the horizon
    window. A handful of sessions exist after that point, but they are
    deliberately stored under a *new* ``Listing`` row (the real-world shape
    of a ticker change), never under the old ``listing_id``. The old
    listing's own price series therefore legitimately runs out early, and the
    horizons that would need those later sessions must report
    ``INSUFFICIENT_FORWARD_SESSIONS`` -- not silently succeed using the new
    listing's bars.
    """
    sessions = _weekday_sessions(date(2026, 3, 2), 10)
    change_date = sessions[5]

    old_listing = Listing(
        security_id=security.id,
        exchange_id=exchange.id,
        ticker="OLDT",
        valid_from=date(2018, 1, 2),
        valid_to=change_date,
        is_primary=False,
    )
    new_listing = Listing(
        security_id=security.id,
        exchange_id=exchange.id,
        ticker="NEWT",
        valid_from=change_date,
        is_primary=True,
    )
    db_session.add_all([old_listing, new_listing])
    db_session.commit()

    # Only the first three sessions (through just before the change) are
    # stored under the old listing -- exactly as a real ticker change would
    # leave it, since the new listing is a distinct row with its own bars.
    db_session.add_all(
        _bar(old_listing, source, day, Decimal("10.00") + Decimal(index))
        for index, day in enumerate(sessions[:3])
    )
    # The new listing has its own, independent bars for the remaining sessions.
    db_session.add_all(
        _bar(new_listing, source, day, Decimal("999.00") + Decimal(index))
        for index, day in enumerate(sessions[5:])
    )
    db_session.commit()

    event = _event(
        company, source, db_session, timing=EarningsTiming.BEFORE_MARKET, event_date=sessions[0]
    )
    alignment = align_earnings_event(db_session, event, mode=ResearchMode.DESCRIPTIVE)
    assert alignment.listing_resolution.listing_id == old_listing.id

    result = compute_event_forward_returns(db_session, alignment, horizons=[1, 2, 5])

    assert result.listing_id == old_listing.id
    by_horizon = {fr.horizon: fr for fr in result.forward_returns}
    # Horizons reachable within the old listing's own bars still work...
    assert by_horizon[1].status is ForwardReturnStatus.AVAILABLE
    assert by_horizon[1].target_close == Decimal("11.00")
    # ...but a horizon that would require crossing into the new listing's
    # bars is reported as insufficient, never satisfied from the new listing.
    assert by_horizon[5].status is ForwardReturnStatus.INSUFFICIENT_FORWARD_SESSIONS
    for forward_return in result.forward_returns:
        if forward_return.target_close is not None:
            assert forward_return.target_close < Decimal("999.00")
