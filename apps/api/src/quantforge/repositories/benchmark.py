"""Benchmark and benchmark level persistence."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.models.benchmark import Benchmark, BenchmarkPriceBar
from quantforge.models.enums import PriceBasis

__all__ = ["BenchmarkIdentity", "BenchmarkRepository"]


@dataclass(frozen=True, slots=True)
class BenchmarkIdentity:
    """The identity columns of one stored benchmark (no description, source or timestamps)."""

    id: uuid.UUID
    name: str
    symbol: str | None
    currency: str | None
    is_active: bool


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

    def list_identities(self, *, limit: int, offset: int) -> list[BenchmarkIdentity]:
        """A page of benchmark IDENTITY columns only, ordered ``(name, id)``.

        Selects ``id``, ``name``, ``symbol``, ``currency`` and ``is_active``: the description,
        source and timestamps are never read, so a discovery surface cannot expose what it never
        loads. ``(name, id)`` is a total order (names are not unique) and ``ix_benchmark_name``
        serves it. ``limit`` and ``offset`` are required (no hidden default page size).
        """
        statement = (
            sa.select(
                Benchmark.id,
                Benchmark.name,
                Benchmark.symbol,
                Benchmark.currency,
                Benchmark.is_active,
            )
            .order_by(Benchmark.name, Benchmark.id)
            .limit(limit)
            .offset(offset)
        )
        return [BenchmarkIdentity(*row) for row in self._session.execute(statement).all()]

    def count(self) -> int:
        """The number of stored benchmarks (active and inactive)."""
        return self._session.execute(sa.select(sa.func.count()).select_from(Benchmark)).scalar_one()

    def list_price_bars(
        self,
        *,
        benchmark_id: uuid.UUID,
        start: date,
        end: date,
        price_basis: PriceBasis,
        source_id: uuid.UUID | None = None,
    ) -> list[BenchmarkPriceBar]:
        """Return benchmark levels within an inclusive date range, oldest first.

        Both bounds are inclusive, matching :meth:`MarketDataRepository.list_range`
        so the two fact tables cannot drift into different conventions.

        Args:
            benchmark_id: Series to read.
            start: Inclusive first session date.
            end: Inclusive last session date.
            price_basis: Required: which split-adjustment basis to read.
            source_id: Restrict to one provider. Optional because the same
                benchmark can be reported by several vendors, and picking one
                silently would embed a vendor choice in the query.
        """
        conditions = [
            BenchmarkPriceBar.benchmark_id == benchmark_id,
            BenchmarkPriceBar.trading_date >= start,
            BenchmarkPriceBar.trading_date <= end,
            BenchmarkPriceBar.price_basis == price_basis,
        ]
        if source_id is not None:
            conditions.append(BenchmarkPriceBar.source_id == source_id)
        statement = (
            sa.select(BenchmarkPriceBar)
            .where(*conditions)
            .order_by(BenchmarkPriceBar.trading_date, BenchmarkPriceBar.ingested_at.desc())
        )
        return list(self._session.scalars(statement))
