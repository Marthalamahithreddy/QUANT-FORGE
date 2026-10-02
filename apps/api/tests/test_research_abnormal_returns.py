"""Unit tests for pure abnormal-return arithmetic.

Operates entirely on plain :class:`~quantforge.research.returns.ForwardReturn`
and :class:`~quantforge.research.benchmark_returns.BenchmarkReturn` instances
-- both plain dataclasses, no database.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from quantforge.research.abnormal_returns import AbnormalReturnStatus, compute_abnormal_return
from quantforge.research.benchmark_returns import BenchmarkReturn, BenchmarkReturnStatus
from quantforge.research.returns import ForwardReturn, ForwardReturnStatus

_BENCHMARK_ID = uuid.uuid4()
_S0 = date(2026, 3, 4)
_S1 = date(2026, 3, 5)


def _stock_return(
    *,
    horizon: int = 1,
    value: Decimal | None,
    status: ForwardReturnStatus = ForwardReturnStatus.AVAILABLE,
) -> ForwardReturn:
    return ForwardReturn(
        horizon=horizon,
        anchor_session=_S0 if value is not None else None,
        anchor_close=Decimal("100.00") if value is not None else None,
        target_session=_S1 if value is not None else None,
        target_close=Decimal("110.00") if value is not None else None,
        return_value=value,
        status=status,
        reason="test fixture",
    )


def _benchmark_return(
    *,
    horizon: int = 1,
    value: Decimal | None,
    status: BenchmarkReturnStatus = BenchmarkReturnStatus.AVAILABLE,
) -> BenchmarkReturn:
    return BenchmarkReturn(
        benchmark_id=_BENCHMARK_ID,
        horizon=horizon,
        anchor_session=_S0 if value is not None else None,
        anchor_close=Decimal("200.00") if value is not None else None,
        target_session=_S1 if value is not None else None,
        target_close=Decimal("210.00") if value is not None else None,
        benchmark_return=value,
        status=status,
        reason="test fixture",
    )


# ── sign combinations from the brief ────────────────────────────────────────


def test_stock_positive_benchmark_positive() -> None:
    result = compute_abnormal_return(
        stock_return=_stock_return(value=Decimal("0.10")),
        benchmark_return=_benchmark_return(value=Decimal("0.04")),
    )
    assert result.status is AbnormalReturnStatus.AVAILABLE
    assert result.abnormal_return == Decimal("0.06")


def test_stock_negative_benchmark_positive() -> None:
    result = compute_abnormal_return(
        stock_return=_stock_return(value=Decimal("-0.05")),
        benchmark_return=_benchmark_return(value=Decimal("0.03")),
    )
    assert result.status is AbnormalReturnStatus.AVAILABLE
    assert result.abnormal_return == Decimal("-0.08")


def test_stock_positive_benchmark_negative() -> None:
    result = compute_abnormal_return(
        stock_return=_stock_return(value=Decimal("0.02")),
        benchmark_return=_benchmark_return(value=Decimal("-0.03")),
    )
    assert result.status is AbnormalReturnStatus.AVAILABLE
    assert result.abnormal_return == Decimal("0.05")


def test_equal_returns_yield_zero_abnormal_return() -> None:
    result = compute_abnormal_return(
        stock_return=_stock_return(value=Decimal("0.07")),
        benchmark_return=_benchmark_return(value=Decimal("0.07")),
    )
    assert result.status is AbnormalReturnStatus.AVAILABLE
    assert result.abnormal_return == Decimal("0")


# ── unavailable components ──────────────────────────────────────────────────


def test_unavailable_stock_return_makes_abnormal_return_unavailable() -> None:
    result = compute_abnormal_return(
        stock_return=_stock_return(value=None, status=ForwardReturnStatus.NO_ALIGNMENT),
        benchmark_return=_benchmark_return(value=Decimal("0.03")),
    )
    assert result.status is AbnormalReturnStatus.STOCK_RETURN_UNAVAILABLE
    assert result.abnormal_return is None


def test_unavailable_benchmark_return_makes_abnormal_return_unavailable() -> None:
    """A missing benchmark return must never be treated as a zero benchmark return."""
    result = compute_abnormal_return(
        stock_return=_stock_return(value=Decimal("0.10")),
        benchmark_return=_benchmark_return(value=None, status=BenchmarkReturnStatus.NO_BENCHMARK),
    )
    assert result.status is AbnormalReturnStatus.BENCHMARK_RETURN_UNAVAILABLE
    assert result.abnormal_return is None


def test_both_components_unavailable() -> None:
    result = compute_abnormal_return(
        stock_return=_stock_return(
            value=None, status=ForwardReturnStatus.INSUFFICIENT_FORWARD_SESSIONS
        ),
        benchmark_return=_benchmark_return(
            value=None, status=BenchmarkReturnStatus.NO_STOCK_RETURN
        ),
    )
    # Stock unavailability is checked first -- a caller debugging a fully
    # failed horizon sees the stock-side reason, not a secondary one.
    assert result.status is AbnormalReturnStatus.STOCK_RETURN_UNAVAILABLE
    assert result.abnormal_return is None


# ── Decimal precision ────────────────────────────────────────────────────────


def test_decimal_precision_is_preserved() -> None:
    stock_value = Decimal("0.123456")
    benchmark_value = Decimal("0.045678")
    result = compute_abnormal_return(
        stock_return=_stock_return(value=stock_value),
        benchmark_return=_benchmark_return(value=benchmark_value),
    )
    assert isinstance(result.abnormal_return, Decimal)
    assert result.abnormal_return == stock_value - benchmark_value


def test_no_float_is_ever_produced() -> None:
    result = compute_abnormal_return(
        stock_return=_stock_return(value=Decimal("0.1")),
        benchmark_return=_benchmark_return(value=Decimal("0.2")),
    )
    assert not isinstance(result.abnormal_return, float)
    assert result.abnormal_return == Decimal("-0.1")


# ── horizon mismatch guard ───────────────────────────────────────────────────


def test_mismatched_horizons_are_rejected() -> None:
    with pytest.raises(ValueError, match="horizon"):
        compute_abnormal_return(
            stock_return=_stock_return(horizon=1, value=Decimal("0.10")),
            benchmark_return=_benchmark_return(horizon=5, value=Decimal("0.04")),
        )


# ── sub-results are preserved for audit ─────────────────────────────────────


def test_both_sub_results_are_preserved_on_the_result() -> None:
    stock_return = _stock_return(value=Decimal("0.10"))
    benchmark_return = _benchmark_return(value=Decimal("0.04"))
    result = compute_abnormal_return(stock_return=stock_return, benchmark_return=benchmark_return)
    assert result.stock_return is stock_return
    assert result.benchmark_return is benchmark_return
