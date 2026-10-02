"""Benchmark returns matched exactly to a stock return's own endpoint dates.

This is deliberately **not** "the benchmark's own N-th session after S0".
:func:`quantforge.research.returns.compute_forward_returns` already
establishes ``S0``/``S_N`` from the *stock's* ordered session list, and the
benchmark can easily have a different set of trading sessions (a different
venue, a different calendar, a gap in ingested benchmark history). Counting
sessions independently on the benchmark side would silently measure two
different intervals and call the result one return. Instead, this module
takes the stock's own ``anchor_session``/``target_session`` dates as given and
looks the benchmark's close up on exactly those two calendar dates -- nothing
is forward-filled, backward-filled, or substituted from a nearby session.

Kept pure and separate from :mod:`quantforge.research.benchmark_price_series`
for the same reason :mod:`quantforge.research.returns` is kept separate from
:mod:`quantforge.research.price_series`: the arithmetic should be testable
with plain dicts of dates to closes, with no database involved.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from quantforge.research.returns import ForwardReturn, ForwardReturnStatus

__all__ = [
    "BenchmarkReturn",
    "BenchmarkReturnStatus",
    "compute_benchmark_return_for_stock_return",
    "no_benchmark_selected",
]


class BenchmarkReturnStatus(StrEnum):
    """Why a benchmark return could, or honestly could not, be computed."""

    #: Both benchmark endpoint prices were found and valid; ``benchmark_return``
    #: is populated.
    AVAILABLE = "AVAILABLE"

    #: The caller never supplied a benchmark identity at all. Distinct from
    #: every other status: those all mean "a benchmark was requested but its
    #: data could not support this horizon", while this means "no benchmark
    #: was requested in the first place". See :func:`no_benchmark_selected`.
    NO_BENCHMARK = "NO_BENCHMARK"

    #: The matching stock :class:`~quantforge.research.returns.ForwardReturn`
    #: was not itself ``AVAILABLE`` (no alignment, insufficient history,
    #: invalid price, ...), so there is no ``S0``/``S_N`` pair to match a
    #: benchmark return to. A benchmark return can never be more available
    #: than the stock interval it is supposed to match.
    NO_STOCK_RETURN = "NO_STOCK_RETURN"

    #: The benchmark has no price observation on the stock's own anchor
    #: session date.
    MISSING_ANCHOR_PRICE = "MISSING_ANCHOR_PRICE"

    #: The benchmark has no price observation on the stock's own target
    #: session date. Note this is checked independently of the anchor: a
    #: benchmark missing just one endpoint is not silently treated as
    #: missing both.
    MISSING_TARGET_PRICE = "MISSING_TARGET_PRICE"

    #: A resolved benchmark close is not a positive price (``<= 0``).
    INVALID_PRICE = "INVALID_PRICE"

    #: More than one :class:`~quantforge.models.provenance.DataSource`
    #: reports this benchmark's level in the window needed, and none was
    #: specified explicitly. Mirrors
    #: ``quantforge.research.returns.ForwardReturnStatus.AMBIGUOUS_PRICE_SOURCE``,
    #: named distinctly because it is reported on the benchmark side of the
    #: study, never conflated with an ambiguous *stock* price source.
    AMBIGUOUS_BENCHMARK_PRICE_SOURCE = "AMBIGUOUS_BENCHMARK_PRICE_SOURCE"


@dataclass(frozen=True, slots=True)
class BenchmarkReturn:
    """One horizon's benchmark-return outcome, matched to a stock return's own endpoints."""

    benchmark_id: uuid.UUID | None
    horizon: int
    anchor_session: date | None
    anchor_close: Decimal | None
    target_session: date | None
    target_close: Decimal | None
    benchmark_return: Decimal | None
    status: BenchmarkReturnStatus
    reason: str


def no_benchmark_selected(stock_returns: list[ForwardReturn]) -> list[BenchmarkReturn]:
    """Build the explicit "no benchmark requested" result for every stock horizon.

    Used when the caller analyzing an earnings event supplied no benchmark
    identity at all. Never guesses a default index (no "US equity -> S&P
    500" heuristic) -- the research layer requires an explicit benchmark, and
    this is the honest, typed stand-in for "none was given", one entry per
    requested horizon so a caller cannot mistake an omitted benchmark for a
    silently zero abnormal return.
    """
    return [
        BenchmarkReturn(
            benchmark_id=None,
            horizon=stock_return.horizon,
            anchor_session=None,
            anchor_close=None,
            target_session=None,
            target_close=None,
            benchmark_return=None,
            status=BenchmarkReturnStatus.NO_BENCHMARK,
            reason="no benchmark was supplied for this event study",
        )
        for stock_return in stock_returns
    ]


def compute_benchmark_return_for_stock_return(
    *,
    benchmark_id: uuid.UUID,
    stock_return: ForwardReturn,
    prices: dict[date, Decimal],
) -> BenchmarkReturn:
    """Compute one horizon's benchmark return, matched exactly to the stock's own endpoints.

    Args:
        benchmark_id: The benchmark this return is being computed for.
        stock_return: The stock's own :class:`~quantforge.research.returns.ForwardReturn`
            for this horizon. Its ``anchor_session``/``target_session`` are
            used verbatim -- this function never derives its own benchmark
            session count.
        prices: ``{trading_date: close}`` for the benchmark, from a single
            coherent source (see
            :mod:`quantforge.research.benchmark_price_series`). A date with no
            entry means "the benchmark has no observation for that date", not
            zero.

    Returns:
        The matching :class:`BenchmarkReturn`.
    """
    if (
        stock_return.status is not ForwardReturnStatus.AVAILABLE
        or stock_return.anchor_session is None
        or stock_return.target_session is None
    ):
        return BenchmarkReturn(
            benchmark_id=benchmark_id,
            horizon=stock_return.horizon,
            anchor_session=None,
            anchor_close=None,
            target_session=None,
            target_close=None,
            benchmark_return=None,
            status=BenchmarkReturnStatus.NO_STOCK_RETURN,
            reason="the stock forward return for this horizon was not available",
        )

    anchor_session = stock_return.anchor_session
    target_session = stock_return.target_session

    anchor_close = prices.get(anchor_session)
    if anchor_close is None:
        return BenchmarkReturn(
            benchmark_id=benchmark_id,
            horizon=stock_return.horizon,
            anchor_session=anchor_session,
            anchor_close=None,
            target_session=None,
            target_close=None,
            benchmark_return=None,
            status=BenchmarkReturnStatus.MISSING_ANCHOR_PRICE,
            reason=f"no benchmark observation exists for the stock's anchor session {anchor_session}",
        )
    if anchor_close <= 0:
        return BenchmarkReturn(
            benchmark_id=benchmark_id,
            horizon=stock_return.horizon,
            anchor_session=anchor_session,
            anchor_close=anchor_close,
            target_session=None,
            target_close=None,
            benchmark_return=None,
            status=BenchmarkReturnStatus.INVALID_PRICE,
            reason=f"benchmark anchor close {anchor_close} is not a valid positive price",
        )

    target_close = prices.get(target_session)
    if target_close is None:
        return BenchmarkReturn(
            benchmark_id=benchmark_id,
            horizon=stock_return.horizon,
            anchor_session=anchor_session,
            anchor_close=anchor_close,
            target_session=target_session,
            target_close=None,
            benchmark_return=None,
            status=BenchmarkReturnStatus.MISSING_TARGET_PRICE,
            reason=f"no benchmark observation exists for the stock's target session {target_session}",
        )
    if target_close <= 0:
        return BenchmarkReturn(
            benchmark_id=benchmark_id,
            horizon=stock_return.horizon,
            anchor_session=anchor_session,
            anchor_close=anchor_close,
            target_session=target_session,
            target_close=target_close,
            benchmark_return=None,
            status=BenchmarkReturnStatus.INVALID_PRICE,
            reason=f"benchmark target close {target_close} is not a valid positive price",
        )

    return BenchmarkReturn(
        benchmark_id=benchmark_id,
        horizon=stock_return.horizon,
        anchor_session=anchor_session,
        anchor_close=anchor_close,
        target_session=target_session,
        target_close=target_close,
        benchmark_return=target_close / anchor_close - 1,
        status=BenchmarkReturnStatus.AVAILABLE,
        reason="computed from the benchmark's close on the stock's own anchor and target sessions",
    )
