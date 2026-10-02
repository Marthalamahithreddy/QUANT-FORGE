"""Unit tests for pure benchmark-return arithmetic.

Operates entirely on plain ``{date: Decimal}`` dicts and
:class:`~quantforge.research.returns.ForwardReturn` instances (themselves
plain dataclasses, no database) -- matching how
``tests/test_research_returns.py`` tests
:mod:`quantforge.research.returns`.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from quantforge.research.benchmark_returns import (
    BenchmarkReturnStatus,
    compute_benchmark_return_for_stock_return,
    no_benchmark_selected,
)
from quantforge.research.returns import ForwardReturn, ForwardReturnStatus

_BENCHMARK_ID = uuid.uuid4()
_S0 = date(2026, 3, 4)
_S1 = date(2026, 3, 5)
_S5 = date(2026, 3, 10)


def _available_stock_return(
    *, horizon: int = 1, anchor: date = _S0, target: date = _S1
) -> ForwardReturn:
    return ForwardReturn(
        horizon=horizon,
        anchor_session=anchor,
        anchor_close=Decimal("100.00"),
        target_session=target,
        target_close=Decimal("110.00"),
        return_value=Decimal("0.10"),
        status=ForwardReturnStatus.AVAILABLE,
        reason="test fixture",
    )


def _unavailable_stock_return(*, horizon: int = 1) -> ForwardReturn:
    return ForwardReturn(
        horizon=horizon,
        anchor_session=None,
        anchor_close=None,
        target_session=None,
        target_close=None,
        return_value=None,
        status=ForwardReturnStatus.NO_ALIGNMENT,
        reason="test fixture",
    )


# ── positive / negative / zero returns, exact Decimal ───────────────────────


def test_a_positive_benchmark_return() -> None:
    stock_return = _available_stock_return()
    prices = {_S0: Decimal("200.00"), _S1: Decimal("210.00")}
    result = compute_benchmark_return_for_stock_return(
        benchmark_id=_BENCHMARK_ID, stock_return=stock_return, prices=prices
    )
    assert result.status is BenchmarkReturnStatus.AVAILABLE
    assert result.anchor_session == _S0
    assert result.target_session == _S1
    assert result.anchor_close == Decimal("200.00")
    assert result.target_close == Decimal("210.00")
    assert result.benchmark_return == Decimal("210.00") / Decimal("200.00") - 1


def test_a_negative_benchmark_return() -> None:
    stock_return = _available_stock_return()
    prices = {_S0: Decimal("200.00"), _S1: Decimal("190.00")}
    result = compute_benchmark_return_for_stock_return(
        benchmark_id=_BENCHMARK_ID, stock_return=stock_return, prices=prices
    )
    assert result.status is BenchmarkReturnStatus.AVAILABLE
    assert result.benchmark_return == Decimal("190.00") / Decimal("200.00") - 1
    assert result.benchmark_return is not None
    assert result.benchmark_return < 0


def test_a_zero_benchmark_return() -> None:
    stock_return = _available_stock_return()
    prices = {_S0: Decimal("200.00"), _S1: Decimal("200.00")}
    result = compute_benchmark_return_for_stock_return(
        benchmark_id=_BENCHMARK_ID, stock_return=stock_return, prices=prices
    )
    assert result.status is BenchmarkReturnStatus.AVAILABLE
    assert result.benchmark_return == Decimal("0")


def test_decimal_precision_is_exact() -> None:
    stock_return = _available_stock_return()
    prices = {_S0: Decimal("123.456789"), _S1: Decimal("124.987654")}
    result = compute_benchmark_return_for_stock_return(
        benchmark_id=_BENCHMARK_ID, stock_return=stock_return, prices=prices
    )
    assert isinstance(result.benchmark_return, Decimal)
    assert result.benchmark_return == Decimal("124.987654") / Decimal("123.456789") - 1


# ── missing endpoints ────────────────────────────────────────────────────────


def test_missing_anchor_price() -> None:
    stock_return = _available_stock_return()
    prices = {_S1: Decimal("210.00")}  # no entry for S0
    result = compute_benchmark_return_for_stock_return(
        benchmark_id=_BENCHMARK_ID, stock_return=stock_return, prices=prices
    )
    assert result.status is BenchmarkReturnStatus.MISSING_ANCHOR_PRICE
    assert result.anchor_close is None
    assert result.benchmark_return is None


def test_missing_target_price() -> None:
    stock_return = _available_stock_return()
    prices = {_S0: Decimal("200.00")}  # no entry for S1
    result = compute_benchmark_return_for_stock_return(
        benchmark_id=_BENCHMARK_ID, stock_return=stock_return, prices=prices
    )
    assert result.status is BenchmarkReturnStatus.MISSING_TARGET_PRICE
    assert result.anchor_close == Decimal("200.00")
    assert result.target_close is None
    assert result.benchmark_return is None


# ── invalid prices ───────────────────────────────────────────────────────────


def test_zero_anchor_price_is_invalid() -> None:
    stock_return = _available_stock_return()
    prices = {_S0: Decimal("0"), _S1: Decimal("210.00")}
    result = compute_benchmark_return_for_stock_return(
        benchmark_id=_BENCHMARK_ID, stock_return=stock_return, prices=prices
    )
    assert result.status is BenchmarkReturnStatus.INVALID_PRICE
    assert result.benchmark_return is None


def test_negative_anchor_price_is_invalid() -> None:
    stock_return = _available_stock_return()
    prices = {_S0: Decimal("-5.00"), _S1: Decimal("210.00")}
    result = compute_benchmark_return_for_stock_return(
        benchmark_id=_BENCHMARK_ID, stock_return=stock_return, prices=prices
    )
    assert result.status is BenchmarkReturnStatus.INVALID_PRICE
    assert result.benchmark_return is None


def test_zero_target_price_is_invalid() -> None:
    stock_return = _available_stock_return()
    prices = {_S0: Decimal("200.00"), _S1: Decimal("0")}
    result = compute_benchmark_return_for_stock_return(
        benchmark_id=_BENCHMARK_ID, stock_return=stock_return, prices=prices
    )
    assert result.status is BenchmarkReturnStatus.INVALID_PRICE
    assert result.anchor_close == Decimal("200.00")
    assert result.benchmark_return is None


def test_negative_target_price_is_invalid() -> None:
    stock_return = _available_stock_return()
    prices = {_S0: Decimal("200.00"), _S1: Decimal("-1.00")}
    result = compute_benchmark_return_for_stock_return(
        benchmark_id=_BENCHMARK_ID, stock_return=stock_return, prices=prices
    )
    assert result.status is BenchmarkReturnStatus.INVALID_PRICE
    assert result.benchmark_return is None


# ── no stock return -> no benchmark return ──────────────────────────────────


def test_an_unavailable_stock_return_yields_no_stock_return_status() -> None:
    stock_return = _unavailable_stock_return()
    result = compute_benchmark_return_for_stock_return(
        benchmark_id=_BENCHMARK_ID, stock_return=stock_return, prices={_S0: Decimal("200.00")}
    )
    assert result.status is BenchmarkReturnStatus.NO_STOCK_RETURN
    assert result.benchmark_return is None
    assert result.anchor_session is None


# ── no benchmark requested at all ───────────────────────────────────────────


def test_no_benchmark_selected_produces_one_result_per_horizon() -> None:
    stock_returns = [_available_stock_return(horizon=1), _available_stock_return(horizon=5)]
    results = no_benchmark_selected(stock_returns)
    assert {r.horizon for r in results} == {1, 5}
    for result in results:
        assert result.status is BenchmarkReturnStatus.NO_BENCHMARK
        assert result.benchmark_id is None
        assert result.benchmark_return is None


# ── endpoint matching: exact dates, never independent session counting ─────


def test_benchmark_uses_the_stocks_s0_exactly() -> None:
    """The benchmark's own session list differs from the stock's; only S0/SN matter."""
    stock_return = _available_stock_return(anchor=_S0, target=_S5)
    # The benchmark has data on dates the stock series never mentions, plus
    # the exact two endpoint dates.
    prices = {
        date(2026, 3, 3): Decimal("999.00"),
        _S0: Decimal("200.00"),
        date(2026, 3, 6): Decimal("888.00"),
        date(2026, 3, 9): Decimal("777.00"),
        _S5: Decimal("220.00"),
    }
    result = compute_benchmark_return_for_stock_return(
        benchmark_id=_BENCHMARK_ID, stock_return=stock_return, prices=prices
    )
    assert result.status is BenchmarkReturnStatus.AVAILABLE
    assert result.anchor_session == _S0
    assert result.anchor_close == Decimal("200.00")


def test_benchmark_uses_the_stocks_sn_exactly() -> None:
    stock_return = _available_stock_return(anchor=_S0, target=_S5)
    prices = {_S0: Decimal("200.00"), _S5: Decimal("220.00")}
    result = compute_benchmark_return_for_stock_return(
        benchmark_id=_BENCHMARK_ID, stock_return=stock_return, prices=prices
    )
    assert result.target_session == _S5
    assert result.target_close == Decimal("220.00")


def test_benchmark_does_not_independently_count_n_sessions() -> None:
    """If the benchmark counted its own 5th session, it would land on a different
    date than the stock's S5 -- proving the match is by date, not by position."""
    stock_return = _available_stock_return(horizon=5, anchor=_S0, target=_S5)
    # The benchmark has MORE sessions than the stock between S0 and S5 (e.g. it
    # trades on a day the stock's venue was closed). If horizon counting were
    # done on the benchmark's own series, "the 5th benchmark session after S0"
    # would be an earlier date than _S5.
    benchmark_dense_sessions = [
        _S0,
        date(2026, 3, 5),
        date(2026, 3, 6),
        date(2026, 3, 7),  # the benchmark's own "5th session" would already be here
        date(2026, 3, 9),
        _S5,  # the stock's actual S5 -- later than the benchmark's own 5th session
    ]
    prices = {
        day: Decimal("200.00") + Decimal(index)
        for index, day in enumerate(benchmark_dense_sessions)
    }
    result = compute_benchmark_return_for_stock_return(
        benchmark_id=_BENCHMARK_ID, stock_return=stock_return, prices=prices
    )
    assert result.status is BenchmarkReturnStatus.AVAILABLE
    assert result.target_session == _S5
    assert result.target_close == prices[_S5]
    assert result.target_close != prices[date(2026, 3, 7)]
