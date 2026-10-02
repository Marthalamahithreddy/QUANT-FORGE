"""Fetch a single, coherent close-level series for one benchmark.

``BenchmarkPriceBar`` carries exactly the same provenance shape as
``DailyPriceBar`` -- uniqueness on ``(benchmark_id, trading_date, source_id, price_basis)``,
a nullable ``observed_at``, an always-present ``ingested_at`` -- for the same
reason: two vendors can disagree about the same index level on the same date,
and both rows should survive rather than one overwriting the other. That means
the same risk :mod:`quantforge.research.price_series` guards against for stock
prices applies here too: resolving a benchmark's close independently per date
could silently splice one vendor's level for the anchor session with a
different vendor's level for the target session. See that module's docstring
for the full reasoning; the policy here is identical, applied to
``BenchmarkPriceBar`` instead of ``DailyPriceBar``.

This is kept as its own small module -- structurally parallel to
``price_series.py``, reusing its ``PriceSeriesStatus``/``PriceSeriesResolution``
vocabulary rather than redefining it -- the same way
:class:`~quantforge.repositories.benchmark.BenchmarkRepository` and
:class:`~quantforge.repositories.market.MarketDataRepository` are already two
small parallel implementations in this codebase rather than one generic
repository forced over both fact tables.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.models.benchmark import BenchmarkPriceBar
from quantforge.models.enums import RESEARCH_PRICE_BASIS
from quantforge.research.price_series import PriceSeriesResolution, PriceSeriesStatus

__all__ = ["fetch_coherent_benchmark_price_series"]


def _closes_from_source(
    session: Session,
    *,
    benchmark_id: uuid.UUID,
    source_id: uuid.UUID,
    start: date,
    end: date,
) -> list[tuple[date, Decimal]]:
    statement = (
        sa.select(BenchmarkPriceBar.trading_date, BenchmarkPriceBar.close)
        .where(
            BenchmarkPriceBar.benchmark_id == benchmark_id,
            BenchmarkPriceBar.source_id == source_id,
            BenchmarkPriceBar.price_basis == RESEARCH_PRICE_BASIS,
            BenchmarkPriceBar.trading_date >= start,
            BenchmarkPriceBar.trading_date <= end,
        )
        .order_by(BenchmarkPriceBar.trading_date)
    )
    return [(row.trading_date, row.close) for row in session.execute(statement)]


def fetch_coherent_benchmark_price_series(
    session: Session,
    *,
    benchmark_id: uuid.UUID,
    start: date,
    end: date,
    source_id: uuid.UUID | None = None,
) -> PriceSeriesResolution:
    """Resolve one source's close-level series for a benchmark over ``[start, end]``.

    Args:
        session: Database session to read from.
        benchmark_id: The benchmark to read levels for -- an explicit choice
            the caller already made; this function never selects a default
            benchmark.
        start: Inclusive lower bound on ``trading_date``.
        end: Inclusive upper bound on ``trading_date``.
        source_id: Use this source's levels only, skipping the ambiguity
            check entirely. When omitted, a single distinct source in the
            window resolves automatically; more than one is an explicit
            ``AMBIGUOUS_PRICE_SOURCE``.

    Returns:
        A :class:`~quantforge.research.price_series.PriceSeriesResolution`.
        Never raises for an ordinary "no data" or "ambiguous" outcome.
    """
    if source_id is not None:
        series = _closes_from_source(
            session, benchmark_id=benchmark_id, source_id=source_id, start=start, end=end
        )
        if not series:
            return PriceSeriesResolution(
                status=PriceSeriesStatus.NO_PRICE_DATA,
                source_id=source_id,
                series=[],
                reason=f"no benchmark levels from source {source_id} for benchmark {benchmark_id} in [{start}, {end}]",
            )
        return PriceSeriesResolution(
            status=PriceSeriesStatus.AVAILABLE,
            source_id=source_id,
            series=series,
            reason="resolved from the explicitly requested source",
        )

    distinct_sources = list(
        session.scalars(
            sa.select(BenchmarkPriceBar.source_id)
            .where(
                BenchmarkPriceBar.benchmark_id == benchmark_id,
                BenchmarkPriceBar.price_basis == RESEARCH_PRICE_BASIS,
                BenchmarkPriceBar.trading_date >= start,
                BenchmarkPriceBar.trading_date <= end,
            )
            .distinct()
        )
    )

    if not distinct_sources:
        return PriceSeriesResolution(
            status=PriceSeriesStatus.NO_PRICE_DATA,
            source_id=None,
            series=[],
            reason=f"no benchmark levels exist for benchmark {benchmark_id} in [{start}, {end}]",
        )

    if len(distinct_sources) > 1:
        return PriceSeriesResolution(
            status=PriceSeriesStatus.AMBIGUOUS_PRICE_SOURCE,
            source_id=None,
            series=[],
            reason=(
                f"{len(distinct_sources)} distinct sources report benchmark {benchmark_id} "
                "in this window; pass an explicit source_id to disambiguate"
            ),
        )

    sole_source = distinct_sources[0]
    series = _closes_from_source(
        session, benchmark_id=benchmark_id, source_id=sole_source, start=start, end=end
    )
    return PriceSeriesResolution(
        status=PriceSeriesStatus.AVAILABLE,
        source_id=sole_source,
        series=series,
        reason="exactly one source covers this window",
    )
