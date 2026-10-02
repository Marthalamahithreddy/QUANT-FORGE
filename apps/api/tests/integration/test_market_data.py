"""Integration tests for market, benchmark and earnings persistence."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from quantforge.models.benchmark import Benchmark, BenchmarkPriceBar
from quantforge.models.company import Company
from quantforge.models.earnings import EarningsEvent
from quantforge.models.enums import EarningsTiming, PriceBasis, ProviderType, SecurityType
from quantforge.models.market import DailyPriceBar
from quantforge.models.provenance import DataSource
from quantforge.models.security import Exchange, Listing, Security
from quantforge.repositories.benchmark import BenchmarkRepository
from quantforge.repositories.earnings import EarningsRepository
from quantforge.repositories.market import MarketDataRepository
from quantforge.repositories.security import (
    ExchangeRepository,
    ListingRepository,
    SecurityRepository,
)

pytestmark = pytest.mark.integration

#: A value with no exact binary-float representation. If any layer quietly
#: coerced this to a float, the round-trip assertion would fail.
TRICKY_CLOSE = Decimal("0.1")
EXACT_SUM = TRICKY_CLOSE + Decimal("0.2")


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def source(db_session: Session) -> DataSource:
    row = DataSource(
        name="integration-vendor",
        provider_type=ProviderType.MARKET_DATA_VENDOR,
        base_url="https://vendor.invalid",
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def other_source(db_session: Session) -> DataSource:
    row = DataSource(name="integration-vendor-b", provider_type=ProviderType.EXCHANGE_FEED)
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def company(db_session: Session) -> Company:
    row = Company(legal_name="Market Test Industries", display_name="Market Test")
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def exchange(db_session: Session) -> Exchange:
    row = Exchange(mic="XMKT", name="Market Test Exchange", timezone="America/New_York")
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def security(company: Company, db_session: Session) -> Security:
    """A persisted instrument. Committed, because the primary key is assigned at
    flush time and every dependent fixture needs a real id."""
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
    """A persisted listing, for the same reason as ``security``."""
    row = Listing(
        security_id=security.id,
        exchange_id=exchange.id,
        ticker="MKTS",
        valid_from=date(2018, 1, 2),
        is_primary=True,
    )
    db_session.add(row)
    db_session.commit()
    return row


def _bar(listing: Listing, source: DataSource, day: date, **overrides: object) -> DailyPriceBar:
    """Build a coherent daily bar with sensible defaults."""
    defaults: dict[str, object] = {
        "listing_id": listing.id,
        "trading_date": day,
        "open": Decimal("100.000000"),
        "high": Decimal("105.000000"),
        "low": Decimal("99.000000"),
        "close": Decimal("103.250000"),
        "volume": 1_000_000,
        "source_id": source.id,
        "price_basis": PriceBasis.SPLIT_ADJUSTED,
    }
    return DailyPriceBar(**{**defaults, **overrides})


@pytest.fixture
def three_bars(listing: Listing, source: DataSource, db_session: Session) -> list[DailyPriceBar]:
    """Three consecutive sessions, stored out of order to prove ordering is the
    repository's job and not an accident of insertion order."""
    days = [date(2024, 1, 4), date(2024, 1, 3), date(2024, 1, 5)]
    bars = [
        _bar(listing, source, day, close=Decimal(f"{100 + index}.000000"))
        for index, day in enumerate(days)
    ]
    db_session.add_all(bars)
    db_session.commit()
    return bars


# ── price bar persistence ───────────────────────────────────────────────────


def test_price_bar_round_trip_preserves_exact_decimal(
    listing: Listing, source: DataSource, db_session: Session
) -> None:
    """Decimal exactness survives PostgreSQL NUMERIC and the Python round trip.

    ``0.1`` is the discriminator: as a binary float, ``0.1 + 0.2 != 0.3``. The
    stored value must still satisfy that identity when read back.
    """
    repo = MarketDataRepository(db_session)
    created = repo.add(
        _bar(
            listing,
            source,
            date(2024, 2, 1),
            open=TRICKY_CLOSE,
            high=Decimal("0.300000"),
            low=Decimal("0.100000"),
            close=TRICKY_CLOSE,
            adjusted_close=None,
        )
    )
    db_session.commit()

    loaded = repo.get(created.id)
    assert loaded is not None
    assert isinstance(loaded.close, Decimal)
    assert loaded.close == TRICKY_CLOSE
    assert loaded.open == TRICKY_CLOSE
    # The float identity that must NOT hold.
    assert Decimal("0.30000000000000000004") != EXACT_SUM
    # The decimal identity that must hold.
    assert loaded.low + (EXACT_SUM - TRICKY_CLOSE) == loaded.high


# ── magnitude is bounded by NUMERIC, not by the schema ──────────────────────
#
# These pin that removing the hand-picked 1e9 price ceiling did not move the
# limit: PostgreSQL's NUMERIC(20, 6) still refuses to overflow, it just refuses
# at the real boundary rather than five orders of magnitude below it.


#: ``NUMERIC(20, 6)`` is 14 integer digits plus 6 fractional digits.
_NUMERIC_20_6_MAX = Decimal("99999999999999.999999")

#: One unit past the integer capacity, so PostgreSQL must reject it.
_NUMERIC_20_6_OVERFLOW = Decimal("100000000000000.000000")


def test_a_price_at_the_numeric_maximum_persists_exactly(
    listing: Listing, source: DataSource, db_session: Session
) -> None:
    """The largest value the column can hold must survive a real round trip.

    This is the end-to-end counterpart to the schema-level test: it proves the
    value the old boundary refused is not merely validated, it is storable.
    """
    bar = _bar(
        listing,
        source,
        date(2024, 1, 2),
        open=_NUMERIC_20_6_MAX,
        high=_NUMERIC_20_6_MAX,
        low=_NUMERIC_20_6_MAX,
        close=_NUMERIC_20_6_MAX,
    )
    db_session.add(bar)
    db_session.commit()

    stored = db_session.get(DailyPriceBar, bar.id)
    assert stored is not None
    assert stored.close == _NUMERIC_20_6_MAX


def test_a_price_past_the_numeric_range_is_rejected_by_the_database(
    listing: Listing, source: DataSource, db_session: Session
) -> None:
    """Overflow is still an error, but it is the database's error now.

    ``DataError`` from the driver, not a Pydantic ``ValidationError``: the limit
    lives in the column type, which is where it can be seen by every writer
    including ``COPY`` and raw SQL.
    """
    db_session.add(
        _bar(
            listing,
            source,
            date(2024, 1, 2),
            open=_NUMERIC_20_6_OVERFLOW,
            high=_NUMERIC_20_6_OVERFLOW,
            low=_NUMERIC_20_6_OVERFLOW,
            close=_NUMERIC_20_6_OVERFLOW,
        )
    )
    with pytest.raises(DataError):
        db_session.commit()
    db_session.rollback()


def test_a_very_large_benchmark_level_persists_exactly(
    source: DataSource, db_session: Session
) -> None:
    benchmark = Benchmark(
        name="Very High Index",
        symbol="VHI",
        currency="USD",
        source_id=source.id,
    )
    db_session.add(benchmark)
    db_session.flush()
    db_session.add(
        BenchmarkPriceBar(
            price_basis=PriceBasis.SPLIT_ADJUSTED,
            benchmark_id=benchmark.id,
            trading_date=date(2024, 1, 2),
            close=_NUMERIC_20_6_MAX,
            source_id=source.id,
        )
    )
    db_session.commit()

    stored = db_session.scalars(
        sa.select(BenchmarkPriceBar).where(BenchmarkPriceBar.benchmark_id == benchmark.id)
    ).one()
    assert stored.close == _NUMERIC_20_6_MAX


def test_price_bar_scale_is_preserved_to_six_places(
    listing: Listing, source: DataSource, db_session: Session
) -> None:
    """NUMERIC(20, 6) keeps six decimal places rather than truncating to cents."""
    repo = MarketDataRepository(db_session)
    created = repo.add(
        _bar(
            listing,
            source,
            date(2024, 2, 2),
            open=Decimal("12.100000"),
            high=Decimal("12.400000"),
            low=Decimal("12.000000"),
            close=Decimal("12.345678"),
        )
    )
    db_session.commit()

    loaded = repo.get(created.id)
    assert loaded is not None
    assert loaded.close == Decimal("12.345678")
    assert str(loaded.close) == "12.345678"


def test_adjusted_close_is_never_equal_to_close_by_default(
    listing: Listing, source: DataSource, db_session: Session
) -> None:
    """NULL means 'the source published no adjusted close'.

    Conflating NULL with ``close`` would make a raw series indistinguishable from
    an adjusted one, which is precisely the ambiguity this schema avoids.
    """
    repo = MarketDataRepository(db_session)
    created = repo.add(_bar(listing, source, date(2024, 2, 5), adjusted_close=None))
    db_session.commit()

    loaded = repo.get(created.id)
    assert loaded is not None
    assert loaded.adjusted_close is None
    assert loaded.close == Decimal("103.250000")


def test_adjusted_close_may_differ_from_close(
    listing: Listing, source: DataSource, db_session: Session
) -> None:
    repo = MarketDataRepository(db_session)
    created = repo.add(_bar(listing, source, date(2024, 2, 6), adjusted_close=Decimal("51.625000")))
    db_session.commit()

    loaded = repo.get(created.id)
    assert loaded is not None
    assert loaded.adjusted_close == Decimal("51.625000")
    assert loaded.adjusted_close != loaded.close


def test_observed_at_and_ingested_at_stay_distinct(
    listing: Listing, source: DataSource, db_session: Session
) -> None:
    """The point-in-time distinction, verified on a real row.

    The source says the bar was available on 2024-03-01T21:05Z (after the US
    close). QuantForge ingested it much later. Collapsing the two would let a
    backtest trade on data the market had not published yet.
    """
    observed = datetime(2024, 3, 1, 21, 5, tzinfo=UTC)
    ingested = observed + timedelta(days=3, hours=2)
    repo = MarketDataRepository(db_session)
    created = repo.add(
        _bar(listing, source, date(2024, 3, 1), observed_at=observed, ingested_at=ingested)
    )
    db_session.commit()

    loaded = repo.get(created.id)
    assert loaded is not None
    assert loaded.observed_at is not None
    assert loaded.ingested_at is not None
    assert loaded.observed_at == observed
    assert loaded.ingested_at == ingested
    assert loaded.ingested_at > loaded.observed_at


def test_observed_at_may_be_unknown(
    listing: Listing, source: DataSource, db_session: Session
) -> None:
    """NULL is 'the source disclosed nothing', not 'equals ingestion time'."""
    repo = MarketDataRepository(db_session)
    created = repo.add(_bar(listing, source, date(2024, 3, 4), observed_at=None))
    db_session.commit()

    loaded = repo.get(created.id)
    assert loaded is not None
    assert loaded.observed_at is None
    assert loaded.ingested_at is not None


def test_ingested_at_defaults_to_now_when_unspecified(
    listing: Listing, source: DataSource, db_session: Session
) -> None:
    before = datetime.now(UTC)
    repo = MarketDataRepository(db_session)
    created = repo.add(_bar(listing, source, date(2024, 3, 5)))
    db_session.commit()
    after = datetime.now(UTC)

    loaded = repo.get(created.id)
    assert loaded is not None
    assert before <= loaded.ingested_at <= after


# ── uniqueness and provenance ───────────────────────────────────────────────


def test_duplicate_observation_from_same_source_is_rejected(
    listing: Listing, source: DataSource, db_session: Session
) -> None:
    db_session.add_all(
        [
            _bar(listing, source, date(2024, 4, 1)),
            _bar(listing, source, date(2024, 4, 1)),
        ]
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_two_sources_may_report_the_same_session(
    listing: Listing, source: DataSource, other_source: DataSource, db_session: Session
) -> None:
    """Vendor disagreement is preserved, not silently overwritten.

    This is why the uniqueness key includes ``source_id``.
    """
    db_session.add_all(
        [
            _bar(listing, source, date(2024, 4, 2), close=Decimal("100.000000")),
            _bar(listing, other_source, date(2024, 4, 2), close=Decimal("100.050000")),
        ]
    )
    db_session.commit()

    rows = list(
        db_session.scalars(
            sa.select(DailyPriceBar).where(
                DailyPriceBar.listing_id == listing.id,
                DailyPriceBar.trading_date == date(2024, 4, 2),
            )
        )
    )
    assert len(rows) == 2
    assert {row.close for row in rows} == {Decimal("100.000000"), Decimal("100.050000")}


def test_get_for_date_can_select_a_source(
    listing: Listing, source: DataSource, other_source: DataSource, db_session: Session
) -> None:
    db_session.add_all(
        [
            _bar(
                listing,
                source,
                date(2024, 4, 3),
                open=Decimal("9.800000"),
                high=Decimal("10.200000"),
                low=Decimal("9.700000"),
                close=Decimal("10.000000"),
            ),
            _bar(
                listing,
                other_source,
                date(2024, 4, 3),
                open=Decimal("10.100000"),
                high=Decimal("10.600000"),
                low=Decimal("10.000000"),
                close=Decimal("10.500000"),
            ),
        ]
    )
    db_session.commit()

    repo = MarketDataRepository(db_session)
    chosen = repo.get_for_date(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        listing_id=listing.id,
        trading_date=date(2024, 4, 3),
        source_id=other_source.id,
    )
    assert chosen is not None
    assert chosen.close == Decimal("10.500000")


def test_sources_for_listing_reports_distinct_provenance(
    listing: Listing, source: DataSource, other_source: DataSource, db_session: Session
) -> None:
    db_session.add_all(
        [
            _bar(listing, source, date(2024, 4, 8)),
            _bar(listing, other_source, date(2024, 4, 9)),
        ]
    )
    db_session.commit()

    found = MarketDataRepository(db_session).sources_for_listing(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        listing_id=listing.id,
        start=date(2024, 4, 1),
        end=date(2024, 4, 30),
    )
    assert set(found) == {source.id, other_source.id}


# ── check constraints ───────────────────────────────────────────────────────


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "adjusted_close"])
def test_negative_prices_are_rejected(
    listing: Listing, source: DataSource, db_session: Session, field: str
) -> None:
    bar = _bar(listing, source, date(2024, 5, 1))
    setattr(bar, field, Decimal("-1.000000"))
    db_session.add(bar)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_negative_volume_is_rejected(
    listing: Listing, source: DataSource, db_session: Session
) -> None:
    db_session.add(_bar(listing, source, date(2024, 5, 2), volume=-1))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_zero_volume_is_accepted(listing: Listing, source: DataSource, db_session: Session) -> None:
    """Zero is a legitimate halted/no-trade session, so only negatives are barred."""
    db_session.add(_bar(listing, source, date(2024, 5, 3), volume=0))
    db_session.commit()


def test_null_volume_is_accepted(listing: Listing, source: DataSource, db_session: Session) -> None:
    """Some feeds omit volume; the constraint must tolerate NULL."""
    db_session.add(_bar(listing, source, date(2024, 5, 4), volume=None))
    db_session.commit()


# The coherent baseline is open=100, high=105, low=99, close=103.25. Each case
# below breaks exactly one of the five implied OHLC bounds.
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("open", Decimal("110.000000")),  # high >= open violated
        ("close", Decimal("110.000000")),  # high >= close violated
        ("low", Decimal("101.000000")),  # low <= open violated
        ("high", Decimal("98.000000")),  # high >= low violated
    ],
)
def test_incoherent_ohlc_is_rejected(
    listing: Listing,
    source: DataSource,
    db_session: Session,
    field: str,
    value: Decimal,
) -> None:
    bar = _bar(listing, source, date(2024, 5, 5))
    setattr(bar, field, value)
    db_session.add(bar)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_coherent_ohlc_is_accepted(
    listing: Listing, source: DataSource, db_session: Session
) -> None:
    """All five bounds satisfied simultaneously."""
    db_session.add(
        _bar(
            listing,
            source,
            date(2024, 5, 6),
            open=Decimal("10.000000"),
            high=Decimal("10.000000"),
            low=Decimal("10.000000"),
            close=Decimal("10.000000"),
        )
    )
    db_session.commit()


# ── repository range reads ──────────────────────────────────────────────────


def test_price_bars_are_returned_in_chronological_order(
    listing: Listing, three_bars: list[DailyPriceBar], db_session: Session
) -> None:
    """Insertion order was 04, 03, 05; the read must still be ascending."""
    rows = MarketDataRepository(db_session).list_range(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        listing_id=listing.id,
        start=date(2024, 1, 1),
        end=date(2024, 1, 31),
    )
    assert [row.trading_date for row in rows] == [
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
    ]


def test_price_range_bounds_are_inclusive_on_both_ends(
    listing: Listing, three_bars: list[DailyPriceBar], db_session: Session
) -> None:
    repo = MarketDataRepository(db_session)
    exact = repo.list_range(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        listing_id=listing.id,
        start=date(2024, 1, 3),
        end=date(2024, 1, 5),
    )
    assert len(exact) == 3

    narrow = repo.list_range(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        listing_id=listing.id,
        start=date(2024, 1, 4),
        end=date(2024, 1, 4),
    )
    assert [row.trading_date for row in narrow] == [date(2024, 1, 4)]


def test_price_range_can_be_narrowed_to_one_source(
    listing: Listing, source: DataSource, other_source: DataSource, db_session: Session
) -> None:
    db_session.add_all(
        [
            _bar(listing, source, date(2024, 6, 3)),
            _bar(listing, other_source, date(2024, 6, 4)),
        ]
    )
    db_session.commit()

    rows = MarketDataRepository(db_session).list_range(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        listing_id=listing.id,
        start=date(2024, 6, 1),
        end=date(2024, 6, 30),
        source_id=source.id,
    )
    assert [row.source_id for row in rows] == [source.id]


def test_close_series_helper_returns_decimals(
    listing: Listing, three_bars: list[DailyPriceBar], db_session: Session
) -> None:
    """The helper exists to keep prices from ever leaving as floats."""
    rows = MarketDataRepository(db_session).list_range(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        listing_id=listing.id,
        start=date(2024, 1, 1),
        end=date(2024, 1, 31),
    )
    # Bars were created for 01-04, 01-03, 01-05 with closes 100, 101, 102 in that
    # insertion order, so chronological order is 01-03 -> 101, 01-04 -> 100, 01-05 -> 102.
    closes = MarketDataRepository.close_series(rows)
    assert all(isinstance(value, Decimal) for value in closes)
    assert closes == [Decimal("101.000000"), Decimal("100.000000"), Decimal("102.000000")]


# ── benchmark ───────────────────────────────────────────────────────────────


@pytest.fixture
def benchmark(db_session: Session, source: DataSource) -> Benchmark:
    row = Benchmark(
        name="Integration Broad Market Index",
        symbol="IBMI",
        currency="USD",
        source_id=source.id,
    )
    db_session.add(row)
    db_session.commit()
    return row


def test_benchmark_price_bar_persists(
    benchmark: Benchmark, source: DataSource, db_session: Session
) -> None:
    repo = BenchmarkRepository(db_session)
    created = repo.add_price_bar(
        BenchmarkPriceBar(
            price_basis=PriceBasis.SPLIT_ADJUSTED,
            benchmark_id=benchmark.id,
            trading_date=date(2024, 1, 3),
            close=Decimal("4500.123456"),
            source_id=source.id,
        )
    )
    db_session.commit()

    loaded = repo.get(benchmark.id)
    assert loaded is not None
    assert loaded.symbol == "IBMI"
    assert [bar.id for bar in loaded.price_bars] == [created.id]
    assert loaded.price_bars[0].close == Decimal("4500.123456")


def test_duplicate_benchmark_level_from_same_source_is_rejected(
    benchmark: Benchmark, source: DataSource, db_session: Session
) -> None:
    db_session.add_all(
        [
            BenchmarkPriceBar(
                price_basis=PriceBasis.SPLIT_ADJUSTED,
                benchmark_id=benchmark.id,
                trading_date=date(2024, 1, 4),
                close=Decimal("4500.000000"),
                source_id=source.id,
            ),
            BenchmarkPriceBar(
                price_basis=PriceBasis.SPLIT_ADJUSTED,
                benchmark_id=benchmark.id,
                trading_date=date(2024, 1, 4),
                close=Decimal("4501.000000"),
                source_id=source.id,
            ),
        ]
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_two_sources_may_report_the_same_benchmark_session(
    benchmark: Benchmark, source: DataSource, other_source: DataSource, db_session: Session
) -> None:
    db_session.add_all(
        [
            BenchmarkPriceBar(
                price_basis=PriceBasis.SPLIT_ADJUSTED,
                benchmark_id=benchmark.id,
                trading_date=date(2024, 1, 5),
                close=Decimal("4500.000000"),
                source_id=source.id,
            ),
            BenchmarkPriceBar(
                price_basis=PriceBasis.SPLIT_ADJUSTED,
                benchmark_id=benchmark.id,
                trading_date=date(2024, 1, 5),
                close=Decimal("4500.500000"),
                source_id=other_source.id,
            ),
        ]
    )
    db_session.commit()

    rows = BenchmarkRepository(db_session).list_price_bars(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        benchmark_id=benchmark.id,
        start=date(2024, 1, 1),
        end=date(2024, 1, 31),
    )
    assert len(rows) == 2


def test_benchmark_levels_are_returned_in_date_order(
    benchmark: Benchmark, source: DataSource, db_session: Session
) -> None:
    db_session.add_all(
        [
            BenchmarkPriceBar(
                price_basis=PriceBasis.SPLIT_ADJUSTED,
                benchmark_id=benchmark.id,
                trading_date=date(2024, 1, 9),
                close=Decimal("4502.000000"),
                source_id=source.id,
            ),
            BenchmarkPriceBar(
                price_basis=PriceBasis.SPLIT_ADJUSTED,
                benchmark_id=benchmark.id,
                trading_date=date(2024, 1, 8),
                close=Decimal("4501.000000"),
                source_id=source.id,
            ),
        ]
    )
    db_session.commit()

    rows = BenchmarkRepository(db_session).list_price_bars(
        price_basis=PriceBasis.SPLIT_ADJUSTED,
        benchmark_id=benchmark.id,
        start=date(2024, 1, 1),
        end=date(2024, 1, 31),
    )
    assert [row.trading_date for row in rows] == [date(2024, 1, 8), date(2024, 1, 9)]


def test_negative_benchmark_level_is_rejected(
    benchmark: Benchmark, source: DataSource, db_session: Session
) -> None:
    db_session.add(
        BenchmarkPriceBar(
            price_basis=PriceBasis.SPLIT_ADJUSTED,
            benchmark_id=benchmark.id,
            trading_date=date(2024, 1, 10),
            close=Decimal("-1.000000"),
            source_id=source.id,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# ── earnings ────────────────────────────────────────────────────────────────


def test_earnings_event_round_trip_preserves_aware_timestamp(
    company: Company, source: DataSource, db_session: Session
) -> None:
    """A non-UTC instant is normalised to UTC on write and comes back absolute.

    ``2024-04-18 08:30 America/New_York`` is ``12:30Z`` in EDT. Storing the wall
    clock without the zone would move the event by an hour twice a year.
    """
    eastern = timezone(timedelta(hours=-4), name="EDT")
    event_timestamp = datetime(2024, 4, 18, 8, 30, tzinfo=eastern)
    repo = EarningsRepository(db_session)
    created = repo.add(
        EarningsEvent(
            company_id=company.id,
            fiscal_year=2024,
            fiscal_quarter=1,
            event_date=date(2024, 4, 18),
            event_timestamp=event_timestamp,
            timing=EarningsTiming.BEFORE_MARKET,
            reported_eps=Decimal("1.2400"),
            estimated_eps=Decimal("1.1800"),
            reported_revenue=Decimal("94500000000.0000"),
            estimated_revenue=Decimal("93100000000.0000"),
            currency="USD",
            source_id=source.id,
        )
    )
    db_session.commit()

    loaded = repo.get(created.id)
    assert loaded is not None
    assert loaded.event_timestamp is not None
    assert loaded.event_timestamp.tzinfo is not None
    assert loaded.event_timestamp == event_timestamp
    assert loaded.event_timestamp.astimezone(UTC) == datetime(2024, 4, 18, 12, 30, tzinfo=UTC)
    assert loaded.reported_eps == Decimal("1.2400")
    assert loaded.reported_revenue == Decimal("94500000000.0000")
    assert loaded.timing is EarningsTiming.BEFORE_MARKET


def test_earnings_allows_negative_eps(
    company: Company, source: DataSource, db_session: Session
) -> None:
    """A loss-making quarter is legitimate; no non-negative constraint applies."""
    db_session.add(
        EarningsEvent(
            company_id=company.id,
            fiscal_year=2023,
            fiscal_quarter=4,
            event_date=date(2024, 2, 1),
            event_timestamp=datetime(2024, 2, 1, 21, 5, tzinfo=UTC),
            timing=EarningsTiming.AFTER_MARKET,
            reported_eps=Decimal("-0.8750"),
            source_id=source.id,
        )
    )
    db_session.commit()


def test_earnings_allows_undisclosed_figures(
    company: Company, source: DataSource, db_session: Session
) -> None:
    """A preliminary release may state revenue but not EPS, or vice versa."""
    db_session.add(
        EarningsEvent(
            company_id=company.id,
            fiscal_year=2024,
            fiscal_quarter=2,
            event_date=date(2024, 7, 1),
            event_timestamp=datetime(2024, 7, 1, 20, 0, tzinfo=UTC),
            timing=EarningsTiming.UNKNOWN,
            reported_revenue=Decimal("100.0000"),
            source_id=source.id,
        )
    )
    db_session.commit()


def test_earnings_allows_annual_reporter_without_a_quarter(
    company: Company, source: DataSource, db_session: Session
) -> None:
    db_session.add(
        EarningsEvent(
            company_id=company.id,
            fiscal_year=2023,
            fiscal_quarter=None,
            event_date=date(2024, 3, 20),
            event_timestamp=datetime(2024, 3, 20, 12, 0, tzinfo=UTC),
            source_id=source.id,
        )
    )
    db_session.commit()


@pytest.mark.parametrize("fiscal_quarter", [0, 5, -1, 99])
def test_invalid_fiscal_quarter_is_rejected(
    company: Company, source: DataSource, db_session: Session, fiscal_quarter: int
) -> None:
    db_session.add(
        EarningsEvent(
            company_id=company.id,
            fiscal_year=2024,
            fiscal_quarter=fiscal_quarter,
            event_date=date(2024, 5, 1),
            event_timestamp=datetime(2024, 5, 1, 12, 0, tzinfo=UTC),
            source_id=source.id,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


@pytest.mark.parametrize("fiscal_quarter", [1, 2, 3, 4, None])
def test_valid_fiscal_quarters_are_accepted(
    company: Company, source: DataSource, db_session: Session, fiscal_quarter: int | None
) -> None:
    db_session.add(
        EarningsEvent(
            company_id=company.id,
            fiscal_year=2024,
            fiscal_quarter=fiscal_quarter,
            event_date=date(2024, 5, 1),
            event_timestamp=datetime(2024, 5, 1, 12, 0, tzinfo=UTC),
            source_id=source.id,
        )
    )
    db_session.commit()


@pytest.mark.parametrize("fiscal_year", [1899, 2201, 0, -2024])
def test_out_of_range_fiscal_year_is_rejected(
    company: Company, source: DataSource, db_session: Session, fiscal_year: int
) -> None:
    db_session.add(
        EarningsEvent(
            company_id=company.id,
            fiscal_year=fiscal_year,
            event_date=date(2024, 5, 1),
            event_timestamp=datetime(2024, 5, 1, 12, 0, tzinfo=UTC),
            source_id=source.id,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_no_derived_analytics_columns_exist(company: Company, source: DataSource) -> None:
    """The fact table stores source facts only.

    A surprise or return column would freeze one methodology's answer into the
    schema and make it unanswerable what a different definition would produce.
    """
    columns = set(EarningsEvent.__table__.columns.keys())
    for derived in (
        "eps_surprise",
        "revenue_surprise",
        "surprise_pct",
        "return_1d",
        "abnormal_return",
    ):
        assert derived not in columns


def test_several_events_may_describe_one_fiscal_period(
    company: Company, source: DataSource, other_source: DataSource, db_session: Session
) -> None:
    """Preliminary, final and per-vendor rows coexist for a single period.

    There is deliberately no uniqueness on
    ``(company_id, fiscal_year, fiscal_quarter)``; forcing one row per period
    would force an overwrite policy at write time.
    """
    db_session.add_all(
        [
            EarningsEvent(
                company_id=company.id,
                fiscal_year=2024,
                fiscal_quarter=1,
                event_date=date(2024, 4, 18),
                event_timestamp=datetime(2024, 4, 18, 12, 30, tzinfo=UTC),
                reported_eps=Decimal("1.2000"),
                source_id=source.id,
            ),
            EarningsEvent(
                company_id=company.id,
                fiscal_year=2024,
                fiscal_quarter=1,
                event_date=date(2024, 5, 3),
                event_timestamp=datetime(2024, 5, 3, 12, 30, tzinfo=UTC),
                reported_eps=Decimal("1.2400"),
                source_id=source.id,
            ),
            EarningsEvent(
                company_id=company.id,
                fiscal_year=2024,
                fiscal_quarter=1,
                event_date=date(2024, 5, 3),
                event_timestamp=datetime(2024, 5, 3, 12, 31, tzinfo=UTC),
                reported_eps=Decimal("1.2300"),
                source_id=other_source.id,
            ),
        ]
    )
    db_session.commit()

    rows = EarningsRepository(db_session).list_for_fiscal_period(
        company_id=company.id, fiscal_year=2024, fiscal_quarter=1
    )
    assert len(rows) == 3


def test_annual_fiscal_query_matches_only_null_quarter(
    company: Company, source: DataSource, db_session: Session
) -> None:
    """``fiscal_quarter=None`` means 'annual', not 'any quarter of the year'."""
    db_session.add_all(
        [
            EarningsEvent(
                company_id=company.id,
                fiscal_year=2022,
                fiscal_quarter=None,
                event_date=date(2023, 3, 1),
                event_timestamp=datetime(2023, 3, 1, 12, 0, tzinfo=UTC),
                source_id=source.id,
            ),
            EarningsEvent(
                company_id=company.id,
                fiscal_year=2022,
                fiscal_quarter=1,
                event_date=date(2022, 5, 1),
                event_timestamp=datetime(2022, 5, 1, 12, 0, tzinfo=UTC),
                source_id=source.id,
            ),
        ]
    )
    db_session.commit()

    annual = EarningsRepository(db_session).list_for_fiscal_period(
        company_id=company.id, fiscal_year=2022
    )
    assert len(annual) == 1
    assert annual[0].fiscal_quarter is None


# ── repository chronological reads ──────────────────────────────────────────


def test_earnings_events_are_returned_in_chronological_order(
    company: Company, source: DataSource, db_session: Session
) -> None:
    """Inserted newest-first; read back oldest-first."""
    db_session.add_all(
        [
            EarningsEvent(
                company_id=company.id,
                fiscal_year=2024,
                fiscal_quarter=3,
                event_date=date(2024, 10, 30),
                event_timestamp=datetime(2024, 10, 30, 20, 5, tzinfo=UTC),
                source_id=source.id,
            ),
            EarningsEvent(
                company_id=company.id,
                fiscal_year=2024,
                fiscal_quarter=1,
                event_date=date(2024, 4, 18),
                event_timestamp=datetime(2024, 4, 18, 12, 30, tzinfo=UTC),
                source_id=source.id,
            ),
            EarningsEvent(
                company_id=company.id,
                fiscal_year=2024,
                fiscal_quarter=2,
                event_date=date(2024, 7, 25),
                event_timestamp=datetime(2024, 7, 25, 20, 5, tzinfo=UTC),
                source_id=source.id,
            ),
        ]
    )
    db_session.commit()

    rows = EarningsRepository(db_session).list_chronological(company_id=company.id)
    assert [row.fiscal_quarter for row in rows] == [1, 2, 3]


def test_earnings_window_bounds_are_inclusive(
    company: Company, source: DataSource, db_session: Session
) -> None:
    db_session.add_all(
        [
            EarningsEvent(
                company_id=company.id,
                fiscal_year=2024,
                fiscal_quarter=1,
                event_date=date(2024, 4, 1),
                event_timestamp=datetime(2024, 4, 1, 12, 0, tzinfo=UTC),
                source_id=source.id,
            ),
            EarningsEvent(
                company_id=company.id,
                fiscal_year=2024,
                fiscal_quarter=2,
                event_date=date(2024, 7, 1),
                event_timestamp=datetime(2024, 7, 1, 12, 0, tzinfo=UTC),
                source_id=source.id,
            ),
        ]
    )
    db_session.commit()

    rows = EarningsRepository(db_session).list_chronological(
        company_id=company.id, start=date(2024, 4, 1)
    )
    assert len(rows) == 2

    rows = EarningsRepository(db_session).list_chronological(
        company_id=company.id, end=date(2024, 4, 1)
    )
    assert len(rows) == 1


def test_earnings_day_range_read_matches_event_date_not_timestamp(
    company: Company, source: DataSource, db_session: Session
) -> None:
    """The boundary dates are inclusive, and the day after is excluded.

    ``list_between_dates`` compares directly against ``event_date`` -- a plain
    ``DATE`` -- rather than widening an ``event_timestamp`` range as it used
    to: there is no time zone left to get wrong once the column being
    compared is already a calendar date. The third event also carries no
    ``event_timestamp`` at all, to confirm the range read finds it purely
    from ``event_date``.
    """
    db_session.add_all(
        [
            EarningsEvent(
                company_id=company.id,
                fiscal_year=2024,
                fiscal_quarter=1,
                event_date=date(2024, 4, 10),
                event_timestamp=datetime(2024, 4, 10, 0, 0, 0, tzinfo=UTC),
                source_id=source.id,
            ),
            EarningsEvent(
                company_id=company.id,
                fiscal_year=2024,
                fiscal_quarter=2,
                event_date=date(2024, 4, 30),
                event_timestamp=None,
                source_id=source.id,
            ),
            EarningsEvent(
                company_id=company.id,
                fiscal_year=2024,
                fiscal_quarter=3,
                event_date=date(2024, 5, 1),
                event_timestamp=datetime(2024, 5, 1, 0, 0, 0, tzinfo=UTC),
                source_id=source.id,
            ),
        ]
    )
    db_session.commit()

    rows = EarningsRepository(db_session).list_between_dates(
        company_id=company.id, start=date(2024, 4, 10), end=date(2024, 4, 30)
    )
    assert len(rows) == 2
    assert {row.fiscal_quarter for row in rows} == {1, 2}


def test_company_exposes_its_earnings_in_order(
    company: Company, source: DataSource, db_session: Session
) -> None:
    db_session.add_all(
        [
            EarningsEvent(
                company_id=company.id,
                fiscal_year=2024,
                fiscal_quarter=2,
                event_date=date(2024, 7, 25),
                event_timestamp=datetime(2024, 7, 25, 20, 5, tzinfo=UTC),
                source_id=source.id,
            ),
            EarningsEvent(
                company_id=company.id,
                fiscal_year=2024,
                fiscal_quarter=1,
                event_date=date(2024, 4, 18),
                event_timestamp=datetime(2024, 4, 18, 12, 30, tzinfo=UTC),
                source_id=source.id,
            ),
        ]
    )
    db_session.commit()

    assert [e.fiscal_quarter for e in company.earnings_events] == [1, 2]


# ── instrument graph composition ────────────────────────────────────────────


def test_full_instrument_graph_resolves_end_to_end(
    company: Company, exchange: Exchange, security: Security, listing: Listing, db_session: Session
) -> None:
    """Ticker -> listing -> security -> company, the join every research path takes."""
    resolved = ListingRepository(db_session).resolve_by_ticker(
        listing.ticker, on_date=date(2020, 1, 2), exchange_id=exchange.id
    )
    assert resolved is not None
    assert resolved.security_id == security.id
    assert resolved.security.company_id == company.id
    assert resolved.exchange.mic == "XMKT"
    assert resolved.exchange.timezone == "America/New_York"

    assert SecurityRepository(db_session).get(resolved.security_id) is not None
    assert ExchangeRepository(db_session).get_by_mic("XMKT") is not None


def test_restricting_a_source_deletes_nothing_provenance_related(
    listing: Listing, source: DataSource, db_session: Session
) -> None:
    """Deactivating a source preserves the rows it produced.

    ``ON DELETE RESTRICT`` plus ``is_active`` means history is never orphaned.
    """
    source.is_active = False
    db_session.commit()

    db_session.add(_bar(listing, source, date(2024, 7, 1)))
    db_session.commit()

    db_session.delete(source)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    assert db_session.get(DataSource, source.id) is not None


def test_price_bar_requires_a_known_listing(source: DataSource, db_session: Session) -> None:
    """No orphan observations: a bar is meaningless without its listing."""
    db_session.add(
        DailyPriceBar(
            price_basis=PriceBasis.SPLIT_ADJUSTED,
            listing_id=uuid.uuid4(),
            trading_date=date(2024, 7, 2),
            open=Decimal("1.000000"),
            high=Decimal("1.000000"),
            low=Decimal("1.000000"),
            close=Decimal("1.000000"),
            source_id=source.id,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# ── price basis in the repositories ─────────────────────────────────────────


def test_repository_reads_only_the_requested_price_basis(
    listing: Listing, source: DataSource, db_session: Session
) -> None:
    day = date(2024, 5, 1)
    db_session.add_all(
        [
            _bar(listing, source, day, close=Decimal("100.000000")),
            _bar(
                listing,
                source,
                day,
                price_basis=PriceBasis.AS_TRADED,
                open=Decimal("1000.000000"),
                high=Decimal("1050.000000"),
                low=Decimal("990.000000"),
                close=Decimal("1030.000000"),
            ),
        ]
    )
    db_session.commit()
    repo = MarketDataRepository(db_session)
    adjusted = repo.list_range(
        price_basis=PriceBasis.SPLIT_ADJUSTED, listing_id=listing.id, start=day, end=day
    )
    traded = repo.list_range(
        price_basis=PriceBasis.AS_TRADED, listing_id=listing.id, start=day, end=day
    )

    assert [bar.close for bar in adjusted] == [Decimal("100.000000")]
    assert [bar.close for bar in traded] == [Decimal("1030.000000")]
    got = repo.get_for_date(
        price_basis=PriceBasis.AS_TRADED, listing_id=listing.id, trading_date=day
    )
    assert got is not None and got.close == Decimal("1030.000000")
    assert repo.sources_for_listing(
        price_basis=PriceBasis.AS_TRADED, listing_id=listing.id, start=day, end=day
    ) == [source.id]


def test_repository_reports_nothing_for_a_basis_that_was_never_ingested(
    listing: Listing, source: DataSource, db_session: Session
) -> None:
    day = date(2024, 5, 2)
    db_session.add(_bar(listing, source, day))
    db_session.commit()
    repo = MarketDataRepository(db_session)

    assert (
        repo.list_range(price_basis=PriceBasis.AS_TRADED, listing_id=listing.id, start=day, end=day)
        == []
    )
    assert (
        repo.get_for_date(price_basis=PriceBasis.AS_TRADED, listing_id=listing.id, trading_date=day)
        is None
    )
