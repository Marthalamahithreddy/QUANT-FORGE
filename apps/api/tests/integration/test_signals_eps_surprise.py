"""Integration tests for the EPS-surprise signal generator.

Mirrors ``tests/integration/test_research_alignment.py``'s fixture style:
these tests exist to prove the database-dependent wiring (listing
resolution, session alignment, point-in-time gating) and the structural
separation from outcome data, not to re-litigate pure classification, which
stays in ``tests/test_signals_eps_surprise.py``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from quantforge.backtest import ExecutionEligibilityStatus, assess_execution_eligibility
from quantforge.market import ExchangeCalendarsSessionCalendar
from quantforge.models.company import Company
from quantforge.models.earnings import EarningsEvent
from quantforge.models.enums import EarningsTiming, PriceBasis, ProviderType, SecurityType
from quantforge.models.market import DailyPriceBar
from quantforge.models.provenance import DataSource
from quantforge.models.security import Exchange, Listing, Security
from quantforge.research.availability import PointInTimeEligibility, ResearchMode
from quantforge.signals.eps_surprise import (
    EpsSurpriseDirection,
    EpsSurpriseSignalStatus,
    generate_eps_surprise_signal,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def source(db_session: Session) -> DataSource:
    row = DataSource(name="signal-test-vendor", provider_type=ProviderType.MARKET_DATA_VENDOR)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def company(db_session: Session) -> Company:
    row = Company(legal_name="Signal Test Industries", display_name="Signal Test")
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def exchange(db_session: Session) -> Exchange:
    row = Exchange(mic="XSIG", name="Signal Test Exchange", timezone="America/New_York")
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
        ticker="SIGN",
        valid_from=date(2018, 1, 2),
        is_primary=True,
    )
    db_session.add(row)
    db_session.commit()
    return row


def _bar(listing: Listing, source: DataSource, day: date, close: Decimal) -> DailyPriceBar:
    low = close - Decimal("5.000000")
    high = close + Decimal("5.000000")
    return DailyPriceBar(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        listing_id=listing.id,
        trading_date=day,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=1_000_000,
        source_id=source.id,
    )


def _put_bars(
    listing: Listing,
    source: DataSource,
    db_session: Session,
    days: list[date],
    *,
    close: Decimal = Decimal("100.000000"),
) -> None:
    db_session.add_all(_bar(listing, source, day, close) for day in days)
    db_session.commit()


def _event(
    company: Company, source: DataSource, db_session: Session, **overrides: object
) -> EarningsEvent:
    fields: dict[str, object] = {
        "company_id": company.id,
        "fiscal_year": 2026,
        "fiscal_quarter": 2,
        "event_date": date(2026, 1, 7),  # a Wednesday
        "timing": EarningsTiming.AFTER_MARKET,
        "reported_eps": Decimal("1.1000"),
        "estimated_eps": Decimal("1.0000"),
        "source_id": source.id,
    }
    fields.update(overrides)
    row = EarningsEvent(**fields)
    db_session.add(row)
    db_session.commit()
    return row


#: A normal Mon-Fri trading week surrounding the fixture events' event_date.
_WEEK = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8), date(2026, 1, 9)]


# ── sign-only classification, wired end to end ──────────────────────────────


def test_positive_surprise_yields_positive_signal(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(
        company,
        source,
        db_session,
        reported_eps=Decimal("1.2000"),
        estimated_eps=Decimal("1.0000"),
    )

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    assert signal.status is EpsSurpriseSignalStatus.AVAILABLE
    assert signal.direction is EpsSurpriseDirection.POSITIVE
    assert signal.surprise_ratio == Decimal("0.2")
    assert signal.signal_value == Decimal("0.2")


def test_negative_surprise_yields_negative_signal(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(
        company,
        source,
        db_session,
        reported_eps=Decimal("0.8000"),
        estimated_eps=Decimal("1.0000"),
    )

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    assert signal.status is EpsSurpriseSignalStatus.AVAILABLE
    assert signal.direction is EpsSurpriseDirection.NEGATIVE
    assert signal.signal_value == signal.surprise_ratio


def test_exact_match_yields_neutral_signal(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(
        company,
        source,
        db_session,
        reported_eps=Decimal("1.0000"),
        estimated_eps=Decimal("1.0000"),
    )

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    assert signal.status is EpsSurpriseSignalStatus.AVAILABLE
    assert signal.direction is EpsSurpriseDirection.NEUTRAL
    assert signal.signal_value == Decimal("0")


def test_negative_estimate_denominator_uses_absolute_value(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    """A loss-making consensus estimate: the Step 8 abs(estimated_eps) denominator rule."""
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(
        company,
        source,
        db_session,
        reported_eps=Decimal("-0.5000"),
        estimated_eps=Decimal("-1.0000"),
    )

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    # absolute_surprise = -0.5 - (-1.0) = 0.5; ratio = 0.5 / abs(-1.0) = 0.5 -> POSITIVE
    # (a smaller loss than expected is correctly a beat).
    assert signal.status is EpsSurpriseSignalStatus.AVAILABLE
    assert signal.surprise_ratio == Decimal("0.5")
    assert signal.direction is EpsSurpriseDirection.POSITIVE


# ── unavailable/missing states, never silently zeroed ───────────────────────


def test_zero_estimate_is_unavailable_not_derived_from_absolute_surprise(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(
        company,
        source,
        db_session,
        reported_eps=Decimal("1.0000"),
        estimated_eps=Decimal("0.0000"),
    )

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    assert signal.status is EpsSurpriseSignalStatus.UNAVAILABLE_ZERO_ESTIMATE
    assert signal.direction is None
    assert signal.surprise_ratio is None
    assert signal.signal_value is None


def test_missing_reported_eps_is_unavailable(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(company, source, db_session, reported_eps=None, estimated_eps=Decimal("1.0000"))

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    assert signal.status is EpsSurpriseSignalStatus.MISSING_REPORTED_EPS
    assert signal.direction is None
    assert signal.signal_value is None
    assert signal.reported_eps is None
    assert signal.estimated_eps == Decimal("1.0000")


def test_missing_estimated_eps_is_unavailable(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(company, source, db_session, reported_eps=Decimal("1.0000"), estimated_eps=None)

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    assert signal.status is EpsSurpriseSignalStatus.MISSING_ESTIMATED_EPS
    assert signal.direction is None
    assert signal.signal_value is None


# ── descriptive vs point-in-time mode ───────────────────────────────────────


def test_descriptive_mode_classifies_despite_null_observed_at(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(
        company,
        source,
        db_session,
        observed_at=None,
        reported_eps=Decimal("1.2000"),
        estimated_eps=Decimal("1.0000"),
    )

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    assert signal.point_in_time_eligibility is PointInTimeEligibility.NOT_APPLICABLE
    assert signal.status is EpsSurpriseSignalStatus.AVAILABLE
    assert signal.direction is EpsSurpriseDirection.POSITIVE
    assert signal.signal_value == Decimal("0.2")


def test_point_in_time_mode_rejects_null_observed_at(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    """Invariant: observed_at=NULL must never become a tradable signal."""
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(
        company,
        source,
        db_session,
        observed_at=None,
        reported_eps=Decimal("1.2000"),
        estimated_eps=Decimal("1.0000"),
    )

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.POINT_IN_TIME)

    assert (
        signal.point_in_time_eligibility
        is PointInTimeEligibility.INELIGIBLE_NO_AVAILABILITY_EVIDENCE
    )
    assert signal.status is EpsSurpriseSignalStatus.NOT_POINT_IN_TIME_ELIGIBLE
    assert signal.direction is None
    assert signal.signal_value is None
    assert signal.effective_session is None
    # Raw inputs are still echoed -- only the derived classification is withheld.
    assert signal.reported_eps == Decimal("1.2000")
    assert signal.estimated_eps == Decimal("1.0000")


def test_point_in_time_mode_accepts_a_known_observed_at(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    observed = datetime(2026, 1, 7, 21, 5, tzinfo=UTC)
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(
        company,
        source,
        db_session,
        observed_at=observed,
        reported_eps=Decimal("1.2000"),
        estimated_eps=Decimal("1.0000"),
    )

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.POINT_IN_TIME)

    assert signal.point_in_time_eligibility is PointInTimeEligibility.ELIGIBLE
    assert signal.status is EpsSurpriseSignalStatus.AVAILABLE
    assert signal.direction is EpsSurpriseDirection.POSITIVE


def test_a_future_revision_cannot_influence_an_earlier_signal(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    """The generator never queries other revisions -- it only reads the row it was given."""
    _put_bars(listing, source, db_session, _WEEK)
    early_observed = datetime(2026, 1, 7, 21, 5, tzinfo=UTC)
    early_event = _event(
        company,
        source,
        db_session,
        observed_at=early_observed,
        reported_eps=Decimal("1.1000"),
        estimated_eps=Decimal("1.0000"),
    )
    # A later-arriving restatement of the *same* fiscal period, with a flipped
    # sign and a later observed_at. If generate_eps_surprise_signal queried
    # "latest revision" itself instead of operating on the row it was given,
    # this would silently corrupt the earlier signal.
    restated = EarningsEvent(
        company_id=company.id,
        fiscal_year=2026,
        fiscal_quarter=2,
        event_date=date(2026, 1, 7),
        timing=EarningsTiming.AFTER_MARKET,
        observed_at=datetime(2026, 2, 1, tzinfo=UTC),
        reported_eps=Decimal("0.5000"),
        estimated_eps=Decimal("1.0000"),
        source_id=source.id,
    )
    db_session.add(restated)
    db_session.commit()

    signal = generate_eps_surprise_signal(db_session, early_event, mode=ResearchMode.POINT_IN_TIME)

    assert signal.status is EpsSurpriseSignalStatus.AVAILABLE
    assert signal.direction is EpsSurpriseDirection.POSITIVE
    assert signal.signal_value == Decimal("0.1")
    assert signal.reported_eps == Decimal("1.1000")


# ── timing semantics inherited from Step 8 ──────────────────────────────────


def test_before_market_may_use_the_event_own_session(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(company, source, db_session, timing=EarningsTiming.BEFORE_MARKET)

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    assert signal.status is EpsSurpriseSignalStatus.AVAILABLE
    assert signal.effective_session == date(2026, 1, 7)


def test_after_market_effective_session_is_never_the_same_session(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(company, source, db_session, timing=EarningsTiming.AFTER_MARKET)

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    assert signal.status is EpsSurpriseSignalStatus.AVAILABLE
    assert signal.effective_session == date(2026, 1, 8)


def test_during_market_produces_no_fabricated_effective_session(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(company, source, db_session, timing=EarningsTiming.DURING_MARKET)

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    assert signal.status is EpsSurpriseSignalStatus.UNSUPPORTED_TIMING
    assert signal.effective_session is None
    assert signal.direction is None
    assert signal.signal_value is None


def test_unknown_timing_produces_no_guessed_effective_session(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(company, source, db_session, timing=EarningsTiming.UNKNOWN)

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    assert signal.status is EpsSurpriseSignalStatus.UNSUPPORTED_TIMING
    assert signal.effective_session is None


# ── listing validity is enforced, not bypassed ──────────────────────────────


def test_no_listing_for_the_event_date_yields_no_effective_session(
    company: Company, source: DataSource, db_session: Session
) -> None:
    """No listing exists yet -- historical listing-validity rules are not bypassed."""
    event = _event(company, source, db_session, timing=EarningsTiming.BEFORE_MARKET)

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    assert signal.status is EpsSurpriseSignalStatus.NO_EFFECTIVE_SESSION
    assert signal.listing_id is None
    assert signal.effective_session is None


def test_insufficient_price_history_yields_no_effective_session(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    """A resolved listing with no price bars at all still fails honestly."""
    event = _event(company, source, db_session, timing=EarningsTiming.BEFORE_MARKET)

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    assert signal.status is EpsSurpriseSignalStatus.NO_EFFECTIVE_SESSION
    assert signal.listing_id == listing.id


# ── look-ahead tripwire ──────────────────────────────────────────────────────


def test_radically_different_future_prices_do_not_change_the_signal(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    """The strongest regression test: future prices cannot leak into a signal.

    Two otherwise-identical events are aligned against daily bars whose close
    prices differ by orders of magnitude (100 vs. 999999). If any forward
    price or return ever influenced this generator, these two calls would
    disagree; they must not.
    """
    _put_bars(listing, source, db_session, _WEEK, close=Decimal("100.000000"))
    event_low = _event(
        company,
        source,
        db_session,
        reported_eps=Decimal("1.2000"),
        estimated_eps=Decimal("1.0000"),
    )
    signal_low = generate_eps_surprise_signal(db_session, event_low, mode=ResearchMode.DESCRIPTIVE)

    # A second, separate listing/company pair with the identical event facts
    # but wildly different stored prices for the same sessions.
    other_company = Company(legal_name="Signal Test Industries Two", display_name="Signal Test 2")
    db_session.add(other_company)
    db_session.commit()
    other_security = Security(
        company_id=other_company.id,
        security_type=SecurityType.EQUITY,
        name="Ordinary Shares",
        currency="USD",
    )
    db_session.add(other_security)
    db_session.commit()
    exchange = db_session.get(Exchange, listing.exchange_id)
    assert exchange is not None
    other_listing = Listing(
        security_id=other_security.id,
        exchange_id=exchange.id,
        ticker="SIGN2",
        valid_from=date(2018, 1, 2),
        is_primary=True,
    )
    db_session.add(other_listing)
    db_session.commit()
    _put_bars(other_listing, source, db_session, _WEEK, close=Decimal("999999.000000"))
    event_high = _event(
        other_company,
        source,
        db_session,
        reported_eps=Decimal("1.2000"),
        estimated_eps=Decimal("1.0000"),
    )
    signal_high = generate_eps_surprise_signal(
        db_session, event_high, mode=ResearchMode.DESCRIPTIVE
    )

    assert signal_low.status == signal_high.status
    assert signal_low.direction == signal_high.direction
    assert signal_low.signal_value == signal_high.signal_value
    assert signal_low.surprise_ratio == signal_high.surprise_ratio
    assert signal_low.absolute_surprise == signal_high.absolute_surprise
    assert signal_low.effective_session == signal_high.effective_session


# ── continuous feature (Step 9.3): signal_value and direction agree ─────────


@pytest.mark.parametrize(
    ("reported", "estimated"),
    [
        (Decimal("1.5000"), Decimal("1.0000")),  # positive
        (Decimal("0.5000"), Decimal("1.0000")),  # negative
        (Decimal("1.0000"), Decimal("1.0000")),  # neutral
    ],
)
def test_signal_value_and_direction_never_disagree(
    company: Company,
    source: DataSource,
    listing: Listing,
    db_session: Session,
    reported: Decimal,
    estimated: Decimal,
) -> None:
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(company, source, db_session, reported_eps=reported, estimated_eps=estimated)

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    assert signal.status is EpsSurpriseSignalStatus.AVAILABLE
    assert signal.signal_value == signal.surprise_ratio
    if signal.signal_value is not None and signal.signal_value > 0:
        assert signal.direction is EpsSurpriseDirection.POSITIVE
    elif signal.signal_value is not None and signal.signal_value < 0:
        assert signal.direction is EpsSurpriseDirection.NEGATIVE
    else:
        assert signal.direction is EpsSurpriseDirection.NEUTRAL


def test_signal_value_retains_full_decimal_precision(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    """A ratio that is lossy in binary floating point must survive end to end."""
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(
        company,
        source,
        db_session,
        reported_eps=Decimal("1.0001"),
        estimated_eps=Decimal("1.0000"),
    )

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    assert signal.signal_value == Decimal("0.0001")
    assert isinstance(signal.signal_value, Decimal)


# ── observed_at provenance and historical exchange MIC (Step 10.4) ──────────


def test_signal_carries_the_selected_events_observed_at_in_utc(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    observed = datetime(2026, 1, 7, 21, 5, 30, 123456, tzinfo=UTC)
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(company, source, db_session, observed_at=observed)

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.POINT_IN_TIME)

    assert signal.observed_at == observed
    assert signal.observed_at is not None
    assert signal.observed_at.utcoffset() == timedelta(0)


def test_signal_observed_at_is_normalised_to_utc_from_any_offset(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    eastern = timezone(timedelta(hours=-5))
    observed = datetime(2026, 1, 7, 16, 5, tzinfo=eastern)
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(company, source, db_session, observed_at=observed)
    event.observed_at = observed  # keep the original offset on the in-memory object

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.POINT_IN_TIME)

    assert signal.observed_at == observed
    assert signal.observed_at is not None
    assert signal.observed_at.utcoffset() == timedelta(0)


def test_null_observed_at_is_never_replaced_by_ingested_at(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(company, source, db_session, observed_at=None)
    assert event.ingested_at is not None  # our own clock exists and must stay out of it

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.POINT_IN_TIME)

    assert signal.observed_at is None
    assert (
        signal.point_in_time_eligibility
        is PointInTimeEligibility.INELIGIBLE_NO_AVAILABILITY_EVIDENCE
    )


def test_a_future_revision_cannot_change_an_earlier_signals_observed_at(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    _put_bars(listing, source, db_session, _WEEK)
    early = datetime(2026, 1, 7, 21, 5, tzinfo=UTC)
    early_event = _event(company, source, db_session, observed_at=early)
    db_session.add(
        EarningsEvent(
            company_id=early_event.company_id,
            fiscal_year=2026,
            fiscal_quarter=2,
            event_date=date(2026, 1, 7),
            timing=EarningsTiming.AFTER_MARKET,
            observed_at=datetime(2026, 2, 1, tzinfo=UTC),
            reported_eps=Decimal("0.5000"),
            estimated_eps=Decimal("1.0000"),
            source_id=source.id,
        )
    )
    db_session.commit()

    signal = generate_eps_surprise_signal(db_session, early_event, mode=ResearchMode.POINT_IN_TIME)

    assert signal.observed_at == early


def test_descriptive_signal_still_carries_observed_at(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    observed = datetime(2026, 1, 7, 21, 5, tzinfo=UTC)
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(company, source, db_session, observed_at=observed)

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    assert signal.observed_at == observed


def test_signal_exchange_mic_is_the_resolved_listings_venue(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(company, source, db_session, observed_at=datetime(2026, 1, 7, 21, tzinfo=UTC))

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.POINT_IN_TIME)

    assert signal.exchange_mic == "XSIG"


def test_signal_with_no_resolved_listing_carries_no_mic(
    company: Company, source: DataSource, db_session: Session
) -> None:
    event = _event(company, source, db_session, observed_at=datetime(2026, 1, 7, 21, tzinfo=UTC))

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.POINT_IN_TIME)

    assert signal.exchange_mic is None


def test_historical_event_uses_the_venue_valid_then_not_the_current_listing(
    company: Company, source: DataSource, security: Security, db_session: Session
) -> None:
    old_exchange = Exchange(mic="XOLD", name="Old Venue", timezone="America/New_York")
    new_exchange = Exchange(mic="XNEW", name="New Venue", timezone="America/New_York")
    db_session.add_all([old_exchange, new_exchange])
    db_session.commit()
    old = Listing(
        security_id=security.id,
        exchange_id=old_exchange.id,
        ticker="MOVE",
        valid_from=date(2018, 1, 2),
        valid_to=date(2022, 1, 3),
        is_primary=False,
    )
    new = Listing(
        security_id=security.id,
        exchange_id=new_exchange.id,
        ticker="MOVE",
        valid_from=date(2022, 1, 3),
        is_primary=True,
    )
    db_session.add_all([old, new])
    db_session.commit()
    event = _event(
        company,
        source,
        db_session,
        event_date=date(2020, 1, 7),
        observed_at=datetime(2020, 1, 7, 21, tzinfo=UTC),
    )

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.POINT_IN_TIME)

    assert signal.listing_id == old.id
    assert signal.exchange_mic == "XOLD"


# ── end to end: DB signal -> session clock -> candidate ─────────────────────


def _nyse_listing(security: Security, db_session: Session, mic: str = "XNYS") -> Listing:
    venue = Exchange(mic=mic, name=f"{mic} venue", timezone="America/New_York")
    db_session.add(venue)
    db_session.commit()
    row = Listing(
        security_id=security.id,
        exchange_id=venue.id,
        ticker="E2E",
        valid_from=date(2018, 1, 2),
        is_primary=True,
    )
    db_session.add(row)
    db_session.commit()
    return row


_E2E_DAYS = [date(2024, 3, 13), date(2024, 3, 14), date(2024, 3, 15), date(2024, 3, 18)]


def test_end_to_end_after_market_friday_event_gets_a_monday_open_candidate(
    company: Company, source: DataSource, security: Security, db_session: Session
) -> None:
    listing = _nyse_listing(security, db_session)
    _put_bars(listing, source, db_session, _E2E_DAYS)
    event = _event(
        company,
        source,
        db_session,
        event_date=date(2024, 3, 15),
        timing=EarningsTiming.AFTER_MARKET,
        observed_at=datetime(2024, 3, 15, 20, 30, tzinfo=UTC),
    )

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.POINT_IN_TIME)
    result = assess_execution_eligibility(signal, ExchangeCalendarsSessionCalendar())

    assert result.status is ExecutionEligibilityStatus.CANDIDATE_EXECUTION_ELIGIBLE
    assert result.candidate_execution_session == date(2024, 3, 18)
    assert result.candidate_execution_open_at == datetime(2024, 3, 18, 13, 30, tzinfo=UTC)


def test_end_to_end_null_observed_at_is_never_a_candidate(
    company: Company, source: DataSource, security: Security, db_session: Session
) -> None:
    listing = _nyse_listing(security, db_session)
    _put_bars(listing, source, db_session, _E2E_DAYS)
    event = _event(
        company,
        source,
        db_session,
        event_date=date(2024, 3, 15),
        timing=EarningsTiming.BEFORE_MARKET,
        observed_at=None,
    )

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.POINT_IN_TIME)
    result = assess_execution_eligibility(signal, ExchangeCalendarsSessionCalendar())

    assert result.status is not ExecutionEligibilityStatus.CANDIDATE_EXECUTION_ELIGIBLE


def test_end_to_end_unsupported_venue_is_refused(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(
        company,
        source,
        db_session,
        timing=EarningsTiming.AFTER_MARKET,
        observed_at=datetime(2026, 1, 7, 21, tzinfo=UTC),
    )

    signal = generate_eps_surprise_signal(db_session, event, mode=ResearchMode.POINT_IN_TIME)
    result = assess_execution_eligibility(signal, ExchangeCalendarsSessionCalendar())

    assert result.status is ExecutionEligibilityStatus.UNSUPPORTED_MIC


def test_a_naive_observed_at_is_rejected_not_reinterpreted_as_local_time(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(company, source, db_session, observed_at=datetime(2026, 1, 7, 21, 5, tzinfo=UTC))
    event.observed_at = datetime(2026, 1, 7, 8, 0)  # noqa: DTZ001 -- deliberately naive

    with pytest.raises(ValueError, match="naive observed_at"):
        generate_eps_surprise_signal(db_session, event, mode=ResearchMode.POINT_IN_TIME)
