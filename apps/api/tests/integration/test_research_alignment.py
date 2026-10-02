"""Integration tests for the composed event-study alignment entry point.

:func:`align_earnings_event` wires a real database read (listing resolution,
stored price sessions) to the pure calendar/availability logic that is
already unit-tested in ``tests/test_research_calendar.py`` and
``tests/test_research_availability.py``. These tests exist to prove the
wiring, the point-in-time gating, and the database-dependent edge cases
(ambiguous/missing listings) -- not to re-litigate every calendar case, which
stays in the pure suite.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
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
from quantforge.research.availability import PointInTimeEligibility, ResearchMode
from quantforge.research.calendar import AlignmentStatus
from quantforge.research.listing_resolution import ListingResolutionStatus

pytestmark = pytest.mark.integration


@pytest.fixture
def source(db_session: Session) -> DataSource:
    row = DataSource(
        name="alignment-test-vendor",
        provider_type=ProviderType.MARKET_DATA_VENDOR,
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def company(db_session: Session) -> Company:
    row = Company(legal_name="Alignment Test Industries", display_name="Alignment Test")
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def exchange(db_session: Session) -> Exchange:
    row = Exchange(mic="XALN", name="Alignment Test Exchange", timezone="America/New_York")
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
        ticker="ALGN",
        valid_from=date(2018, 1, 2),
        is_primary=True,
    )
    db_session.add(row)
    db_session.commit()
    return row


def _bar(listing: Listing, source: DataSource, day: date) -> DailyPriceBar:
    return DailyPriceBar(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        listing_id=listing.id,
        trading_date=day,
        open=Decimal("100.000000"),
        high=Decimal("101.000000"),
        low=Decimal("99.000000"),
        close=Decimal("100.500000"),
        volume=1_000_000,
        source_id=source.id,
    )


def _put_bars(listing: Listing, source: DataSource, db_session: Session, days: list[date]) -> None:
    db_session.add_all(_bar(listing, source, day) for day in days)
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
        "source_id": source.id,
    }
    fields.update(overrides)
    row = EarningsEvent(**fields)
    db_session.add(row)
    db_session.commit()
    return row


#: A normal Mon-Fri trading week surrounding the fixture events' event_date.
_WEEK = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7), date(2026, 1, 8), date(2026, 1, 9)]


# ── end-to-end wiring, a sanity check per timing ────────────────────────────


def test_before_market_event_aligns_end_to_end(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(company, source, db_session, timing=EarningsTiming.BEFORE_MARKET)

    result = align_earnings_event(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    assert result.listing_resolution.status is ListingResolutionStatus.RESOLVED
    assert result.session_alignment is not None
    assert result.session_alignment.status is AlignmentStatus.ALIGNED
    assert result.session_alignment.event_session == date(2026, 1, 7)
    assert result.session_alignment.first_post_event_session == date(2026, 1, 7)


def test_after_market_event_aligns_to_the_next_session_end_to_end(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(company, source, db_session, timing=EarningsTiming.AFTER_MARKET)

    result = align_earnings_event(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    assert result.session_alignment is not None
    assert result.session_alignment.status is AlignmentStatus.ALIGNED
    assert result.session_alignment.event_session == date(2026, 1, 7)
    assert result.session_alignment.first_post_event_session == date(2026, 1, 8)


# ── exact timestamp is preserved, never re-derived ─────────────────────────


def test_an_exact_event_timestamp_is_preserved_unchanged(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    exact_instant = datetime(2026, 1, 7, 21, 5, tzinfo=UTC)
    event = _event(
        company,
        source,
        db_session,
        timing=EarningsTiming.AFTER_MARKET,
        event_timestamp=exact_instant,
    )

    result = align_earnings_event(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    assert result.event_timestamp == exact_instant


# ── descriptive vs point-in-time mode ───────────────────────────────────────


def test_descriptive_mode_accepts_a_null_observed_at(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(
        company, source, db_session, timing=EarningsTiming.AFTER_MARKET, observed_at=None
    )

    result = align_earnings_event(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    assert result.point_in_time_eligibility is PointInTimeEligibility.NOT_APPLICABLE
    assert result.session_alignment is not None
    assert result.session_alignment.status is AlignmentStatus.ALIGNED
    assert result.session_alignment.first_post_event_session == date(2026, 1, 8)


def test_point_in_time_mode_rejects_a_null_observed_at(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    """Invariant 3: observed_at=NULL must not silently become point-in-time tradable."""
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(
        company, source, db_session, timing=EarningsTiming.AFTER_MARKET, observed_at=None
    )

    result = align_earnings_event(db_session, event, mode=ResearchMode.POINT_IN_TIME)

    assert (
        result.point_in_time_eligibility
        is PointInTimeEligibility.INELIGIBLE_NO_AVAILABILITY_EVIDENCE
    )
    assert result.session_alignment is not None
    assert result.session_alignment.status is AlignmentStatus.NOT_POINT_IN_TIME_ELIGIBLE
    assert result.session_alignment.event_session is None
    assert result.session_alignment.first_post_event_session is None


def test_point_in_time_mode_accepts_a_known_observed_at(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    observed = datetime(2026, 1, 7, 21, 5, tzinfo=UTC)
    _put_bars(listing, source, db_session, _WEEK)
    event = _event(
        company, source, db_session, timing=EarningsTiming.AFTER_MARKET, observed_at=observed
    )

    result = align_earnings_event(db_session, event, mode=ResearchMode.POINT_IN_TIME)

    assert result.point_in_time_eligibility is PointInTimeEligibility.ELIGIBLE
    assert result.session_alignment is not None
    assert result.session_alignment.status is AlignmentStatus.ALIGNED
    assert result.session_alignment.first_post_event_session == date(2026, 1, 8)


# ── listing resolution failures propagate, no session is attempted ─────────


def test_no_listing_for_the_event_date_yields_no_session_alignment(
    company: Company, source: DataSource, db_session: Session
) -> None:
    """No listing exists at all yet, so no session calculation is attempted."""
    event = _event(company, source, db_session, timing=EarningsTiming.BEFORE_MARKET)

    result = align_earnings_event(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    assert result.listing_resolution.status is ListingResolutionStatus.NO_LISTING_FOR_DATE
    assert result.session_alignment is None


def test_ambiguous_listing_yields_no_session_alignment(
    company: Company, exchange: Exchange, db_session: Session
) -> None:
    second_venue = Exchange(
        mic="XALT", name="Alignment Test Exchange Two", timezone="Europe/London"
    )
    db_session.add(second_venue)
    db_session.commit()

    security = Security(
        company_id=company.id,
        security_type=SecurityType.EQUITY,
        name="Ordinary Shares",
        currency="USD",
    )
    db_session.add(security)
    db_session.commit()
    db_session.add_all(
        [
            Listing(
                security_id=security.id,
                exchange_id=exchange.id,
                ticker="DUAL2",
                valid_from=date(2018, 1, 2),
                is_primary=False,
            ),
            Listing(
                security_id=security.id,
                exchange_id=second_venue.id,
                ticker="DUAL2",
                valid_from=date(2018, 1, 2),
                is_primary=False,
            ),
        ]
    )
    db_session.commit()

    source = DataSource(name="ambiguous-vendor", provider_type=ProviderType.MARKET_DATA_VENDOR)
    db_session.add(source)
    db_session.commit()
    event = _event(company, source, db_session, timing=EarningsTiming.BEFORE_MARKET)

    result = align_earnings_event(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    assert result.listing_resolution.status is ListingResolutionStatus.AMBIGUOUS_LISTING
    assert result.session_alignment is None


def test_insufficient_price_history_yields_no_session_data(
    company: Company, source: DataSource, listing: Listing, db_session: Session
) -> None:
    """A resolved listing with no price bars at all still fails honestly, not silently."""
    event = _event(company, source, db_session, timing=EarningsTiming.BEFORE_MARKET)

    result = align_earnings_event(db_session, event, mode=ResearchMode.DESCRIPTIVE)

    assert result.listing_resolution.status is ListingResolutionStatus.RESOLVED
    assert result.session_alignment is not None
    assert result.session_alignment.status is AlignmentStatus.NO_SESSION_DATA
