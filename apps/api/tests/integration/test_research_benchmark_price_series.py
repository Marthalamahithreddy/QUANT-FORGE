"""Integration tests for resolving one coherent benchmark close-level series.

Needs real PostgreSQL to prove the *data* -- distinct ``BenchmarkPriceBar``
sources sharing a benchmark -- drives the ambiguity decision. Structurally
parallel to ``tests/integration/test_research_price_series.py``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from quantforge.models.benchmark import Benchmark, BenchmarkPriceBar
from quantforge.models.enums import PriceBasis, ProviderType
from quantforge.models.provenance import DataSource
from quantforge.research.benchmark_price_series import fetch_coherent_benchmark_price_series
from quantforge.research.price_series import PriceSeriesStatus

pytestmark = pytest.mark.integration


@pytest.fixture
def source_a(db_session: Session) -> DataSource:
    row = DataSource(
        name="benchmark-series-vendor-a", provider_type=ProviderType.MARKET_DATA_VENDOR
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def source_b(db_session: Session) -> DataSource:
    row = DataSource(
        name="benchmark-series-vendor-b", provider_type=ProviderType.MARKET_DATA_VENDOR
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.fixture
def benchmark(db_session: Session) -> Benchmark:
    row = Benchmark(name="Benchmark Series Test Index", symbol="BSTI", currency="USD")
    db_session.add(row)
    db_session.commit()
    return row


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


def test_a_single_source_resolves_automatically(
    benchmark: Benchmark, source_a: DataSource, db_session: Session
) -> None:
    db_session.add_all(
        [
            _level(benchmark, source_a, date(2026, 1, 8), Decimal("4000.00")),
            _level(benchmark, source_a, date(2026, 1, 9), Decimal("4010.00")),
        ]
    )
    db_session.commit()

    result = fetch_coherent_benchmark_price_series(
        db_session, benchmark_id=benchmark.id, start=date(2026, 1, 1), end=date(2026, 1, 31)
    )

    assert result.status is PriceSeriesStatus.AVAILABLE
    assert result.source_id == source_a.id
    assert result.series == [
        (date(2026, 1, 8), Decimal("4000.00")),
        (date(2026, 1, 9), Decimal("4010.00")),
    ]


def test_no_benchmark_data_in_the_window(benchmark: Benchmark, db_session: Session) -> None:
    result = fetch_coherent_benchmark_price_series(
        db_session, benchmark_id=benchmark.id, start=date(2026, 1, 1), end=date(2026, 1, 31)
    )
    assert result.status is PriceSeriesStatus.NO_PRICE_DATA
    assert result.series == []


def test_two_sources_in_the_window_is_explicitly_ambiguous(
    benchmark: Benchmark, source_a: DataSource, source_b: DataSource, db_session: Session
) -> None:
    db_session.add_all(
        [
            _level(benchmark, source_a, date(2026, 1, 8), Decimal("4000.00")),
            _level(benchmark, source_b, date(2026, 1, 9), Decimal("4010.00")),
        ]
    )
    db_session.commit()

    result = fetch_coherent_benchmark_price_series(
        db_session, benchmark_id=benchmark.id, start=date(2026, 1, 1), end=date(2026, 1, 31)
    )

    assert result.status is PriceSeriesStatus.AMBIGUOUS_PRICE_SOURCE
    assert result.series == []


def test_an_explicit_source_id_bypasses_the_ambiguity_check(
    benchmark: Benchmark, source_a: DataSource, source_b: DataSource, db_session: Session
) -> None:
    db_session.add_all(
        [
            _level(benchmark, source_a, date(2026, 1, 8), Decimal("4000.00")),
            _level(benchmark, source_b, date(2026, 1, 8), Decimal("9999.00")),
        ]
    )
    db_session.commit()

    result = fetch_coherent_benchmark_price_series(
        db_session,
        benchmark_id=benchmark.id,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        source_id=source_a.id,
    )

    assert result.status is PriceSeriesStatus.AVAILABLE
    assert result.series == [(date(2026, 1, 8), Decimal("4000.00"))]


def test_an_explicit_source_with_no_levels_is_no_price_data(
    benchmark: Benchmark, source_a: DataSource, source_b: DataSource, db_session: Session
) -> None:
    db_session.add(_level(benchmark, source_a, date(2026, 1, 8), Decimal("4000.00")))
    db_session.commit()

    result = fetch_coherent_benchmark_price_series(
        db_session,
        benchmark_id=benchmark.id,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        source_id=source_b.id,
    )

    assert result.status is PriceSeriesStatus.NO_PRICE_DATA
    assert result.source_id == source_b.id
    assert result.series == []


# ── price basis: benchmark research reads SPLIT_ADJUSTED only ───────────────


def _as_traded_level(
    benchmark: Benchmark, source: DataSource, day: date, close: Decimal
) -> BenchmarkPriceBar:
    level = _level(benchmark, source, day, close)
    level.price_basis = PriceBasis.AS_TRADED
    return level


def test_the_benchmark_series_ignores_as_traded_levels_and_stays_unambiguous(
    benchmark: Benchmark, source_a: DataSource, db_session: Session
) -> None:
    db_session.add_all(
        [
            _level(benchmark, source_a, date(2026, 1, 8), Decimal("4000.00")),
            _level(benchmark, source_a, date(2026, 1, 9), Decimal("4010.00")),
            _as_traded_level(benchmark, source_a, date(2026, 1, 8), Decimal("40000.00")),
            _as_traded_level(benchmark, source_a, date(2026, 1, 9), Decimal("1.00")),
        ]
    )
    db_session.commit()

    result = fetch_coherent_benchmark_price_series(
        db_session, benchmark_id=benchmark.id, start=date(2026, 1, 1), end=date(2026, 1, 31)
    )

    assert result.status is PriceSeriesStatus.AVAILABLE
    assert result.series == [
        (date(2026, 1, 8), Decimal("4000.00")),
        (date(2026, 1, 9), Decimal("4010.00")),
    ]


def test_a_benchmark_with_only_as_traded_levels_has_no_research_data(
    benchmark: Benchmark, source_a: DataSource, db_session: Session
) -> None:
    db_session.add(_as_traded_level(benchmark, source_a, date(2026, 1, 8), Decimal("40000.00")))
    db_session.commit()

    result = fetch_coherent_benchmark_price_series(
        db_session, benchmark_id=benchmark.id, start=date(2026, 1, 1), end=date(2026, 1, 31)
    )

    assert result.status is PriceSeriesStatus.NO_PRICE_DATA
