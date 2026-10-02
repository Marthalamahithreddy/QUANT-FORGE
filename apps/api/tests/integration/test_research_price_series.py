"""Integration tests for resolving one coherent close-price series.

Needs real PostgreSQL because the point is to prove the *data* -- distinct
``DailyPriceBar`` sources sharing a listing -- drives the ambiguity decision,
not a mock. The pure arithmetic this series feeds is already covered,
without a database, in ``tests/test_research_returns.py``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from quantforge.models.company import Company
from quantforge.models.enums import PriceBasis, ProviderType, SecurityType
from quantforge.models.market import DailyPriceBar
from quantforge.models.provenance import DataSource
from quantforge.models.security import Exchange, Listing, Security
from quantforge.research.price_series import PriceSeriesStatus, fetch_coherent_price_series

pytestmark = pytest.mark.integration


@pytest.fixture
def source_a(db_session: Session) -> DataSource:
    row = DataSource(name="price-series-vendor-a", provider_type=ProviderType.MARKET_DATA_VENDOR)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def source_b(db_session: Session) -> DataSource:
    row = DataSource(name="price-series-vendor-b", provider_type=ProviderType.MARKET_DATA_VENDOR)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def listing(db_session: Session) -> Listing:
    company = Company(legal_name="Price Series Test Co", display_name="Price Series Test")
    db_session.add(company)
    db_session.commit()

    exchange = Exchange(mic="XPRS", name="Price Series Exchange", timezone="America/New_York")
    db_session.add(exchange)
    db_session.commit()

    security = Security(
        company_id=company.id,
        security_type=SecurityType.EQUITY,
        name="Ordinary Shares",
        currency="USD",
    )
    db_session.add(security)
    db_session.commit()

    row = Listing(
        security_id=security.id,
        exchange_id=exchange.id,
        ticker="PSER",
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


# ── single-source auto-resolution ───────────────────────────────────────────


def test_a_single_source_resolves_automatically(
    listing: Listing, source_a: DataSource, db_session: Session
) -> None:
    db_session.add_all(
        [
            _bar(listing, source_a, date(2026, 1, 8), Decimal("100.00")),
            _bar(listing, source_a, date(2026, 1, 9), Decimal("101.00")),
        ]
    )
    db_session.commit()

    result = fetch_coherent_price_series(
        db_session, listing_id=listing.id, start=date(2026, 1, 1), end=date(2026, 1, 31)
    )

    assert result.status is PriceSeriesStatus.AVAILABLE
    assert result.source_id == source_a.id
    assert result.series == [
        (date(2026, 1, 8), Decimal("100.00")),
        (date(2026, 1, 9), Decimal("101.00")),
    ]


def test_no_price_data_in_the_window(listing: Listing, db_session: Session) -> None:
    result = fetch_coherent_price_series(
        db_session, listing_id=listing.id, start=date(2026, 1, 1), end=date(2026, 1, 31)
    )
    assert result.status is PriceSeriesStatus.NO_PRICE_DATA
    assert result.source_id is None
    assert result.series == []


# ── multiple sources: explicit ambiguity, never an arbitrary tiebreak ───────


def test_two_sources_in_the_window_is_explicitly_ambiguous(
    listing: Listing, source_a: DataSource, source_b: DataSource, db_session: Session
) -> None:
    db_session.add_all(
        [
            _bar(listing, source_a, date(2026, 1, 8), Decimal("100.00")),
            _bar(listing, source_b, date(2026, 1, 9), Decimal("101.00")),
        ]
    )
    db_session.commit()

    result = fetch_coherent_price_series(
        db_session, listing_id=listing.id, start=date(2026, 1, 1), end=date(2026, 1, 31)
    )

    assert result.status is PriceSeriesStatus.AMBIGUOUS_PRICE_SOURCE
    assert result.source_id is None
    assert result.series == []


def test_an_explicit_source_id_bypasses_the_ambiguity_check(
    listing: Listing, source_a: DataSource, source_b: DataSource, db_session: Session
) -> None:
    db_session.add_all(
        [
            _bar(listing, source_a, date(2026, 1, 8), Decimal("100.00")),
            _bar(listing, source_b, date(2026, 1, 8), Decimal("999.00")),
            _bar(listing, source_a, date(2026, 1, 9), Decimal("101.00")),
        ]
    )
    db_session.commit()

    result = fetch_coherent_price_series(
        db_session,
        listing_id=listing.id,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        source_id=source_a.id,
    )

    assert result.status is PriceSeriesStatus.AVAILABLE
    assert result.source_id == source_a.id
    # Only source_a's bars appear -- source_b's conflicting same-day price for
    # 2026-01-08 never enters the series.
    assert result.series == [
        (date(2026, 1, 8), Decimal("100.00")),
        (date(2026, 1, 9), Decimal("101.00")),
    ]


def test_an_explicit_source_with_no_bars_is_no_price_data(
    listing: Listing, source_a: DataSource, source_b: DataSource, db_session: Session
) -> None:
    db_session.add(_bar(listing, source_a, date(2026, 1, 8), Decimal("100.00")))
    db_session.commit()

    result = fetch_coherent_price_series(
        db_session,
        listing_id=listing.id,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        source_id=source_b.id,
    )

    assert result.status is PriceSeriesStatus.NO_PRICE_DATA
    assert result.source_id == source_b.id


def test_a_second_source_outside_the_window_does_not_create_ambiguity(
    listing: Listing, source_a: DataSource, source_b: DataSource, db_session: Session
) -> None:
    db_session.add_all(
        [
            _bar(listing, source_a, date(2026, 1, 8), Decimal("100.00")),
            _bar(listing, source_b, date(2027, 6, 1), Decimal("200.00")),  # outside the window
        ]
    )
    db_session.commit()

    result = fetch_coherent_price_series(
        db_session, listing_id=listing.id, start=date(2026, 1, 1), end=date(2026, 1, 31)
    )

    assert result.status is PriceSeriesStatus.AVAILABLE
    assert result.source_id == source_a.id


# ── price basis: research reads SPLIT_ADJUSTED only ─────────────────────────


def _as_traded_bar(
    listing: Listing, source: DataSource, day: date, close: Decimal
) -> DailyPriceBar:
    bar = _bar(listing, source, day, close)
    bar.price_basis = PriceBasis.AS_TRADED
    return bar


def test_the_series_ignores_as_traded_rows_from_the_same_source(
    listing: Listing, source_a: DataSource, db_session: Session
) -> None:
    db_session.add_all(
        [
            _bar(listing, source_a, date(2026, 1, 8), Decimal("100.00")),
            _bar(listing, source_a, date(2026, 1, 9), Decimal("101.00")),
            _as_traded_bar(listing, source_a, date(2026, 1, 8), Decimal("1000.00")),
            _as_traded_bar(listing, source_a, date(2026, 1, 9), Decimal("9999.00")),
        ]
    )
    db_session.commit()

    result = fetch_coherent_price_series(
        db_session, listing_id=listing.id, start=date(2026, 1, 1), end=date(2026, 1, 31)
    )

    assert result.status is PriceSeriesStatus.AVAILABLE
    assert result.series == [
        (date(2026, 1, 8), Decimal("100.00")),
        (date(2026, 1, 9), Decimal("101.00")),
    ]


def test_an_explicit_source_still_reads_only_split_adjusted(
    listing: Listing, source_a: DataSource, db_session: Session
) -> None:
    db_session.add_all(
        [
            _bar(listing, source_a, date(2026, 1, 8), Decimal("100.00")),
            _as_traded_bar(listing, source_a, date(2026, 1, 8), Decimal("1000.00")),
        ]
    )
    db_session.commit()

    result = fetch_coherent_price_series(
        db_session,
        listing_id=listing.id,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        source_id=source_a.id,
    )

    assert result.series == [(date(2026, 1, 8), Decimal("100.00"))]


def test_a_listing_with_only_as_traded_bars_has_no_research_price_data(
    listing: Listing, source_a: DataSource, db_session: Session
) -> None:
    db_session.add(_as_traded_bar(listing, source_a, date(2026, 1, 8), Decimal("1000.00")))
    db_session.commit()

    result = fetch_coherent_price_series(
        db_session, listing_id=listing.id, start=date(2026, 1, 1), end=date(2026, 1, 31)
    )

    assert result.status is PriceSeriesStatus.NO_PRICE_DATA
    assert result.series == []
