"""Integration tests for resolving a company to a listing as of a date.

Needs real PostgreSQL because the half-open ``[valid_from, valid_to)``
boundary comparisons are exactly the kind of date-comparison behaviour the
project's own testing policy reserves for the real database rather than
SQLite (see ``apps/api/README.md``). Everything here is about *which row* is
returned, never about session-date arithmetic -- that is covered without a
database in ``tests/test_research_calendar.py``.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session

from quantforge.models.company import Company
from quantforge.models.enums import SecurityType
from quantforge.models.security import Exchange, Listing, Security
from quantforge.research.listing_resolution import (
    ListingResolutionStatus,
    resolve_company_listing_for_date,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def company(db_session: Session) -> Company:
    row = Company(legal_name="Resolution Test Co", display_name="Resolution Test")
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def exchange(db_session: Session) -> Exchange:
    row = Exchange(mic="XRES", name="Resolution Test Exchange", timezone="America/New_York")
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def second_exchange(db_session: Session) -> Exchange:
    row = Exchange(mic="XRS2", name="Resolution Test Exchange Two", timezone="Europe/London")
    db_session.add(row)
    db_session.commit()
    return row


def _security(company: Company, db_session: Session, *, name: str = "Ordinary Shares") -> Security:
    row = Security(
        company_id=company.id,
        security_type=SecurityType.EQUITY,
        name=name,
        currency="USD",
    )
    db_session.add(row)
    db_session.commit()
    return row


def _listing(
    security: Security,
    exchange: Exchange,
    db_session: Session,
    **overrides: object,
) -> Listing:
    fields: dict[str, object] = {"is_primary": True, "valid_to": None}
    fields.update(overrides)
    row = Listing(
        security_id=security.id,
        exchange_id=exchange.id,
        **fields,
    )
    db_session.add(row)
    db_session.commit()
    return row


# ── single-security, single-venue resolution ────────────────────────────────


def test_resolves_the_single_valid_listing(
    company: Company, exchange: Exchange, db_session: Session
) -> None:
    security = _security(company, db_session)
    listing = _listing(security, exchange, db_session, ticker="RES1", valid_from=date(2020, 1, 2))

    result = resolve_company_listing_for_date(
        db_session, company_id=company.id, on_date=date(2024, 6, 1)
    )

    assert result.status is ListingResolutionStatus.RESOLVED
    assert result.listing_id == listing.id
    assert result.security_id == security.id


# ── listing validity boundaries ─────────────────────────────────────────────


def test_a_date_before_valid_from_has_no_listing(
    company: Company, exchange: Exchange, db_session: Session
) -> None:
    _listing(
        _security(company, db_session),
        exchange,
        db_session,
        ticker="RES2",
        valid_from=date(2022, 3, 1),
    )

    result = resolve_company_listing_for_date(
        db_session, company_id=company.id, on_date=date(2022, 2, 28)
    )

    assert result.status is ListingResolutionStatus.NO_LISTING_FOR_DATE


def test_the_valid_from_date_itself_is_inclusive(
    company: Company, exchange: Exchange, db_session: Session
) -> None:
    listing = _listing(
        _security(company, db_session),
        exchange,
        db_session,
        ticker="RES3",
        valid_from=date(2022, 3, 1),
    )

    result = resolve_company_listing_for_date(
        db_session, company_id=company.id, on_date=date(2022, 3, 1)
    )

    assert result.status is ListingResolutionStatus.RESOLVED
    assert result.listing_id == listing.id


def test_the_valid_to_date_itself_is_exclusive(
    company: Company, exchange: Exchange, db_session: Session
) -> None:
    """A ticker-change boundary: the old listing does not cover its own valid_to date."""
    security = _security(company, db_session)
    old = _listing(
        security,
        exchange,
        db_session,
        ticker="OLD1",
        valid_from=date(2020, 1, 2),
        valid_to=date(2023, 4, 10),
    )
    new = _listing(security, exchange, db_session, ticker="NEW1", valid_from=date(2023, 4, 10))

    day_before = resolve_company_listing_for_date(
        db_session, company_id=company.id, on_date=date(2023, 4, 9)
    )
    handover_day = resolve_company_listing_for_date(
        db_session, company_id=company.id, on_date=date(2023, 4, 10)
    )

    assert day_before.status is ListingResolutionStatus.RESOLVED
    assert day_before.listing_id == old.id
    assert handover_day.status is ListingResolutionStatus.RESOLVED
    assert handover_day.listing_id == new.id


def test_a_date_at_or_after_valid_to_has_no_listing_under_the_old_period(
    company: Company, exchange: Exchange, db_session: Session
) -> None:
    """Without a successor listing, the date range past valid_to is unresolved, not misattributed."""
    _listing(
        _security(company, db_session),
        exchange,
        db_session,
        ticker="RES4",
        valid_from=date(2020, 1, 2),
        valid_to=date(2021, 1, 1),
    )

    result = resolve_company_listing_for_date(
        db_session, company_id=company.id, on_date=date(2021, 6, 1)
    )

    assert result.status is ListingResolutionStatus.NO_LISTING_FOR_DATE


# ── dual-venue ambiguity within one security ────────────────────────────────


def test_dual_venue_listing_with_no_declared_primary_is_ambiguous(
    company: Company, exchange: Exchange, second_exchange: Exchange, db_session: Session
) -> None:
    security = _security(company, db_session)
    _listing(
        security, exchange, db_session, ticker="DUAL", valid_from=date(2020, 1, 2), is_primary=False
    )
    _listing(
        security,
        second_exchange,
        db_session,
        ticker="DUAL",
        valid_from=date(2020, 1, 2),
        is_primary=False,
    )

    result = resolve_company_listing_for_date(
        db_session, company_id=company.id, on_date=date(2024, 6, 1)
    )

    assert result.status is ListingResolutionStatus.AMBIGUOUS_LISTING
    assert result.listing_id is None


def test_dual_venue_listing_resolves_via_the_primary_flag(
    company: Company, exchange: Exchange, second_exchange: Exchange, db_session: Session
) -> None:
    security = _security(company, db_session)
    primary = _listing(
        security, exchange, db_session, ticker="DUAL", valid_from=date(2020, 1, 2), is_primary=True
    )
    _listing(
        security,
        second_exchange,
        db_session,
        ticker="DUAL",
        valid_from=date(2020, 1, 2),
        is_primary=False,
    )

    result = resolve_company_listing_for_date(
        db_session, company_id=company.id, on_date=date(2024, 6, 1)
    )

    assert result.status is ListingResolutionStatus.RESOLVED
    assert result.listing_id == primary.id


# ── multi-security ambiguity at the company level ───────────────────────────


def test_a_company_with_two_securities_is_ambiguous_without_a_security_id(
    company: Company, exchange: Exchange, db_session: Session
) -> None:
    common = _security(company, db_session, name="Common Shares")
    preferred = _security(company, db_session, name="Preferred Shares")
    _listing(common, exchange, db_session, ticker="COMM", valid_from=date(2020, 1, 2))
    _listing(preferred, exchange, db_session, ticker="PREF", valid_from=date(2020, 1, 2))

    result = resolve_company_listing_for_date(
        db_session, company_id=company.id, on_date=date(2024, 6, 1)
    )

    assert result.status is ListingResolutionStatus.AMBIGUOUS_LISTING


def test_an_explicit_security_id_disambiguates_a_multi_security_company(
    company: Company, exchange: Exchange, db_session: Session
) -> None:
    common = _security(company, db_session, name="Common Shares")
    preferred = _security(company, db_session, name="Preferred Shares")
    common_listing = _listing(
        common, exchange, db_session, ticker="COMM2", valid_from=date(2020, 1, 2)
    )
    _listing(preferred, exchange, db_session, ticker="PREF2", valid_from=date(2020, 1, 2))

    result = resolve_company_listing_for_date(
        db_session, company_id=company.id, on_date=date(2024, 6, 1), security_id=common.id
    )

    assert result.status is ListingResolutionStatus.RESOLVED
    assert result.listing_id == common_listing.id
    assert result.security_id == common.id
