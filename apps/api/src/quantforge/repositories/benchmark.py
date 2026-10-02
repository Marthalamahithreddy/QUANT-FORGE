"""Benchmark and benchmark level persistence."""

from __future__ import annotations

import uuid
from datetime import date

import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.models.benchmark import Benchmark, BenchmarkPriceBar

__all__ = ["BenchmarkRepository"]


class BenchmarkRepository:
    """Reads and writes benchmark series and their daily levels.

    The level series exists so a later analytics layer can compute benchmark
    returns and, from those, abnormal returns. Only raw levels are stored here;
    no return, excess return or z-score is precomputed.
    """

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, benchmark: Benchmark) -> Benchmark:
        """Stage a new benchmark for insertion and return it."""
        self._session.add(benchmark)
        return benchmark

    def add_price_bar(self, bar: BenchmarkPriceBar) -> BenchmarkPriceBar:
        """Stage one benchmark level for insertion and return it."""
        self._session.add(bar)
        return bar

    def get(self, benchmark_id: uuid.UUID) -> Benchmark | None:
        """Return the benchmark with this id, or ``None``."""
        return self._session.get(Benchmark, benchmark_id)

    def list_all(self, *, limit: int = 100, offset: int = 0) -> list[Benchmark]:
        """Return a page of benchmarks ordered by name.

        Ordered by ``(name, id)`` so pagination is stable when two benchmarks
        share a name, which is common across vendors.
        """
        statement = (
            sa.select(Benchmark).order_by(Benchmark.name, Benchmark.id).limit(limit).offset(offset)
        )
        return list(self._session.scalars(statement))

    def list_price_bars(
        self,
        *,
        benchmark_id: uuid.UUID,
        start: date,
        end: date,
        source_id: uuid.UUID | None = None,
    ) -> list[BenchmarkPriceBar]:
        """Return benchmark levels within an inclusive date range, oldest first.

        Both bounds are inclusive, matching :meth:`MarketDataRepository.list_range`
        so the two fact tables cannot drift into different conventions.

        Args:
            benchmark_id: Series to read.
            start: Inclusive first session date.
            end: Inclusive last session date.
            source_id: Restrict to one provider. Optional because the same
                benchmark can be reported by several vendors, and picking one
                silently would embed a vendor choice in the query.
        """
        conditions = [
            BenchmarkPriceBar.benchmark_id == benchmark_id,
            BenchmarkPriceBar.trading_date >= start,
            BenchmarkPriceBar.trading_date <= end,
        ]
        if source_id is not None:
            conditions.append(BenchmarkPriceBar.source_id == source_id)
        statement = (
            sa.select(BenchmarkPriceBar)
            .where(*conditions)
            .order_by(BenchmarkPriceBar.trading_date, BenchmarkPriceBar.ingested_at.desc())
        )
        return list(self._session.scalars(statement))
