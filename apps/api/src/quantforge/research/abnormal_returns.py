"""Simple market-adjusted (benchmark-adjusted) abnormal returns.

``abnormal_return_N = stock_return_N - benchmark_return_N``.

This is a **simple market-adjusted return**, nothing more. It is deliberately
not named or documented as:

* CAPM alpha -- that requires a regression against the benchmark to estimate
  beta; this subtracts the benchmark return directly, with an implicit
  beta of 1.
* risk-adjusted alpha or factor alpha -- no risk model or factor loadings
  are estimated anywhere in this codebase.
* excess return from a regression model -- there is no regression here at
  all, just a subtraction.

Calling this anything stronger would overstate what a one-line subtraction
of two split-adjusted, dividend-excluded price returns can support. See
:mod:`quantforge.research.returns` and
:mod:`quantforge.research.benchmark_returns` for the full price-basis
explanation this inherits directly: an abnormal return computed here already
accounts for stock splits on both legs (Massive's ``adjusted=true`` aggregates
are split-adjusted), but excludes cash dividends on both legs -- it is a
price-return comparison, not a total-return one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from quantforge.research.benchmark_returns import BenchmarkReturn, BenchmarkReturnStatus
from quantforge.research.returns import ForwardReturn, ForwardReturnStatus

__all__ = ["AbnormalReturn", "AbnormalReturnStatus", "compute_abnormal_return"]


class AbnormalReturnStatus(StrEnum):
    """Why an abnormal return could, or honestly could not, be computed."""

    #: Both the stock and benchmark returns were available; ``abnormal_return``
    #: is populated.
    AVAILABLE = "AVAILABLE"

    #: The stock's own forward return for this horizon was not available.
    #: An abnormal return can never be more available than its stock leg.
    STOCK_RETURN_UNAVAILABLE = "STOCK_RETURN_UNAVAILABLE"

    #: The benchmark return for this horizon was not available (no benchmark
    #: selected, a missing endpoint, an ambiguous source, ...). A missing
    #: benchmark return is never treated as a zero benchmark return -- that
    #: would silently collapse the abnormal return to the raw stock return
    #: while claiming it was benchmark-adjusted.
    BENCHMARK_RETURN_UNAVAILABLE = "BENCHMARK_RETURN_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class AbnormalReturn:
    """One horizon's abnormal-return outcome, carrying both legs for audit.

    ``stock_return``/``benchmark_return`` are the full sub-results, not just
    their numeric values -- a caller inspecting why an abnormal return is
    unavailable should never have to re-fetch the stock or benchmark leg to
    find out which one failed.
    """

    horizon: int
    stock_return: ForwardReturn
    benchmark_return: BenchmarkReturn
    abnormal_return: Decimal | None
    status: AbnormalReturnStatus
    reason: str


def compute_abnormal_return(
    *,
    stock_return: ForwardReturn,
    benchmark_return: BenchmarkReturn,
) -> AbnormalReturn:
    """Compute one horizon's simple market-adjusted abnormal return.

    Args:
        stock_return: The stock's forward return for this horizon.
        benchmark_return: The benchmark return already matched to this same
            horizon's anchor/target sessions (see
            :func:`quantforge.research.benchmark_returns.compute_benchmark_return_for_stock_return`).
            Both arguments must describe the same ``horizon``; this function
            does not re-derive or cross-check the interval itself, since that
            is already the matching function's job.

    Returns:
        The computed :class:`AbnormalReturn`. Never fabricates a zero
        benchmark return when one is missing -- a missing component makes the
        whole abnormal return unavailable, not a smaller number.
    """
    if stock_return.horizon != benchmark_return.horizon:
        message = (
            f"stock_return horizon {stock_return.horizon} does not match "
            f"benchmark_return horizon {benchmark_return.horizon}"
        )
        raise ValueError(message)

    if (
        stock_return.status is not ForwardReturnStatus.AVAILABLE
        or stock_return.return_value is None
    ):
        return AbnormalReturn(
            horizon=stock_return.horizon,
            stock_return=stock_return,
            benchmark_return=benchmark_return,
            abnormal_return=None,
            status=AbnormalReturnStatus.STOCK_RETURN_UNAVAILABLE,
            reason="the stock forward return for this horizon was not available",
        )

    if (
        benchmark_return.status is not BenchmarkReturnStatus.AVAILABLE
        or benchmark_return.benchmark_return is None
    ):
        return AbnormalReturn(
            horizon=stock_return.horizon,
            stock_return=stock_return,
            benchmark_return=benchmark_return,
            abnormal_return=None,
            status=AbnormalReturnStatus.BENCHMARK_RETURN_UNAVAILABLE,
            reason="the benchmark return for this horizon was not available",
        )

    return AbnormalReturn(
        horizon=stock_return.horizon,
        stock_return=stock_return,
        benchmark_return=benchmark_return,
        abnormal_return=stock_return.return_value - benchmark_return.benchmark_return,
        status=AbnormalReturnStatus.AVAILABLE,
        reason="simple market-adjusted abnormal return (stock return minus benchmark return)",
    )
