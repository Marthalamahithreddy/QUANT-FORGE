"""Unit tests for pure close-to-close forward-return arithmetic.

Operates entirely on plain ``(date, Decimal)`` pairs -- no database, no ORM,
no alignment objects -- matching how ``tests/test_research_calendar.py`` tests
:mod:`quantforge.research.calendar`. Database-facing price-source resolution
is tested separately in ``tests/integration/test_research_price_series.py``,
and the full wiring in ``tests/integration/test_research_event_returns.py``.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from quantforge.research.returns import (
    ForwardReturn,
    ForwardReturnStatus,
    compute_forward_returns,
    no_alignment_forward_returns,
)

_S0 = date(2026, 1, 8)  # anchor session (a Thursday)
_S1 = date(2026, 1, 9)  # Friday
_S2 = date(2026, 1, 12)  # Monday (weekend skipped)
_S3 = date(2026, 1, 13)
_S4 = date(2026, 1, 14)
_S5 = date(2026, 1, 15)

#: A coherent run of six sessions: the anchor plus five forward sessions,
#: deliberately crossing a weekend with no Saturday/Sunday entries.
_SERIES = [
    (_S0, Decimal("100.00")),
    (_S1, Decimal("101.00")),
    (_S2, Decimal("99.00")),
    (_S3, Decimal("102.00")),
    (_S4, Decimal("103.00")),
    (_S5, Decimal("104.00")),
]


def _returns_by_horizon(
    *, anchor_session: date, price_series: list[tuple[date, Decimal]], horizons: list[int]
) -> dict[int, ForwardReturn]:
    results = compute_forward_returns(
        anchor_session=anchor_session, price_series=price_series, horizons=horizons
    )
    return {result.horizon: result for result in results}


# ── exact horizons ───────────────────────────────────────────────────────────


def test_exact_one_session_return() -> None:
    by_horizon = _returns_by_horizon(anchor_session=_S0, price_series=_SERIES, horizons=[1])
    result = by_horizon[1]
    assert result.status is ForwardReturnStatus.AVAILABLE
    assert result.target_session == _S1
    assert result.target_close == Decimal("101.00")
    assert result.return_value == Decimal("101.00") / Decimal("100.00") - 1


def test_exact_five_session_return() -> None:
    by_horizon = _returns_by_horizon(anchor_session=_S0, price_series=_SERIES, horizons=[5])
    result = by_horizon[5]
    assert result.status is ForwardReturnStatus.AVAILABLE
    assert result.target_session == _S5
    assert result.target_close == Decimal("104.00")
    assert result.return_value == Decimal("104.00") / Decimal("100.00") - 1


def _weekday_sessions(start: date, count: int) -> list[date]:
    """Return ``count`` consecutive Mon-Fri dates starting from ``start`` (inclusive)."""
    sessions: list[date] = []
    day = start
    while len(sessions) < count:
        if day.weekday() < 5:
            sessions.append(day)
        day += timedelta(days=1)
    return sessions


def test_exact_twenty_session_return() -> None:
    # Anchor plus 20 forward sessions: 21 consecutive weekday dates.
    sessions = _weekday_sessions(date(2026, 3, 2), 21)  # starts on a Monday
    price = Decimal("50.00")
    series = [(session_date, price + Decimal(index)) for index, session_date in enumerate(sessions)]

    anchor = sessions[0]
    by_horizon = _returns_by_horizon(anchor_session=anchor, price_series=series, horizons=[20])
    result = by_horizon[20]
    assert result.status is ForwardReturnStatus.AVAILABLE
    assert result.target_session == sessions[20]
    assert result.target_close == price + Decimal(20)
    assert result.return_value == (price + Decimal(20)) / price - 1


# ── horizon means N sessions AFTER S0, never S0 itself ──────────────────────


def test_horizon_one_is_never_the_anchor_session_itself() -> None:
    by_horizon = _returns_by_horizon(anchor_session=_S0, price_series=_SERIES, horizons=[1])
    assert by_horizon[1].target_session != _S0
    assert by_horizon[1].target_session == _S1


def test_weekend_gap_does_not_shift_the_session_count() -> None:
    """S0=Thursday; S1 is Friday, S2 is the following Monday -- the weekend is simply absent."""
    by_horizon = _returns_by_horizon(anchor_session=_S0, price_series=_SERIES, horizons=[2])
    assert by_horizon[2].target_session == _S2


def test_a_holiday_gap_does_not_shift_the_session_count() -> None:
    """A mid-week date simply missing from the series does not consume a horizon slot."""
    series_with_gap = [
        (date(2026, 2, 2), Decimal("10.00")),  # anchor
        (date(2026, 2, 3), Decimal("11.00")),  # S1
        # 2026-02-04 is a holiday: no bar at all, not a zero or forward-filled value.
        (date(2026, 2, 5), Decimal("12.00")),  # S2
    ]
    by_horizon = _returns_by_horizon(
        anchor_session=date(2026, 2, 2), price_series=series_with_gap, horizons=[1, 2]
    )
    assert by_horizon[1].target_session == date(2026, 2, 3)
    assert by_horizon[2].target_session == date(2026, 2, 5)


# ── insufficient history ─────────────────────────────────────────────────────


def test_insufficient_five_session_history() -> None:
    short_series = _SERIES[:3]  # anchor + only two forward sessions
    by_horizon = _returns_by_horizon(anchor_session=_S0, price_series=short_series, horizons=[5])
    result = by_horizon[5]
    assert result.status is ForwardReturnStatus.INSUFFICIENT_FORWARD_SESSIONS
    assert result.return_value is None
    assert result.target_session is None


def test_insufficient_twenty_session_history() -> None:
    by_horizon = _returns_by_horizon(anchor_session=_S0, price_series=_SERIES, horizons=[20])
    result = by_horizon[20]
    assert result.status is ForwardReturnStatus.INSUFFICIENT_FORWARD_SESSIONS
    assert result.return_value is None


def test_every_requested_horizon_produces_a_result_even_when_some_fail() -> None:
    """Requested [1, 5, 20] must never silently drop an unavailable horizon."""
    by_horizon = _returns_by_horizon(anchor_session=_S0, price_series=_SERIES, horizons=[1, 5, 20])
    assert set(by_horizon) == {1, 5, 20}
    assert by_horizon[1].status is ForwardReturnStatus.AVAILABLE
    assert by_horizon[5].status is ForwardReturnStatus.AVAILABLE
    assert by_horizon[20].status is ForwardReturnStatus.INSUFFICIENT_FORWARD_SESSIONS


# ── missing / invalid prices ─────────────────────────────────────────────────


def test_missing_anchor_price() -> None:
    series_without_anchor = [row for row in _SERIES if row[0] != _S0]
    by_horizon = _returns_by_horizon(
        anchor_session=_S0, price_series=series_without_anchor, horizons=[1]
    )
    result = by_horizon[1]
    assert result.status is ForwardReturnStatus.MISSING_ANCHOR_PRICE
    assert result.anchor_close is None
    assert result.return_value is None


def test_invalid_zero_anchor_close() -> None:
    series = [(_S0, Decimal("0")), (_S1, Decimal("101.00"))]
    by_horizon = _returns_by_horizon(anchor_session=_S0, price_series=series, horizons=[1])
    result = by_horizon[1]
    assert result.status is ForwardReturnStatus.INVALID_PRICE
    assert result.return_value is None


def test_invalid_negative_anchor_close() -> None:
    series = [(_S0, Decimal("-5.00")), (_S1, Decimal("101.00"))]
    by_horizon = _returns_by_horizon(anchor_session=_S0, price_series=series, horizons=[1])
    result = by_horizon[1]
    assert result.status is ForwardReturnStatus.INVALID_PRICE
    assert result.anchor_close == Decimal("-5.00")
    assert result.return_value is None


def test_invalid_target_close() -> None:
    series = [(_S0, Decimal("100.00")), (_S1, Decimal("0"))]
    by_horizon = _returns_by_horizon(anchor_session=_S0, price_series=series, horizons=[1])
    result = by_horizon[1]
    assert result.status is ForwardReturnStatus.INVALID_PRICE
    assert result.anchor_close == Decimal("100.00")
    assert result.target_session == _S1
    assert result.return_value is None


def test_an_invalid_target_at_one_horizon_does_not_block_a_different_horizon() -> None:
    series = [
        (_S0, Decimal("100.00")),
        (_S1, Decimal("0")),  # invalid: horizon 1 fails
        (_S2, Decimal("99.00")),  # valid: horizon 2 should still succeed
    ]
    by_horizon = _returns_by_horizon(anchor_session=_S0, price_series=series, horizons=[1, 2])
    assert by_horizon[1].status is ForwardReturnStatus.INVALID_PRICE
    assert by_horizon[2].status is ForwardReturnStatus.AVAILABLE


# ── Decimal precision, no float, no forward-filling ─────────────────────────


def test_decimal_precision_is_preserved_in_the_return() -> None:
    series = [(_S0, Decimal("100.123456")), (_S1, Decimal("101.654321"))]
    by_horizon = _returns_by_horizon(anchor_session=_S0, price_series=series, horizons=[1])
    result = by_horizon[1]
    assert isinstance(result.return_value, Decimal)
    assert result.return_value == Decimal("101.654321") / Decimal("100.123456") - 1


def test_no_forward_filling_a_missing_target_session() -> None:
    """A horizon landing past the end of the series is INSUFFICIENT, never the last known price."""
    by_horizon = _returns_by_horizon(anchor_session=_S0, price_series=_SERIES, horizons=[20])
    result = by_horizon[20]
    assert result.target_close is None
    assert result.status is not ForwardReturnStatus.AVAILABLE


# ── session search is not calendar-day arithmetic ───────────────────────────


def test_sessions_are_searched_in_order_not_computed_from_calendar_days() -> None:
    """A caller handing the series out of order, with no duplicate dates, gets the same answer."""
    shuffled = [_SERIES[3], _SERIES[0], _SERIES[5], _SERIES[1], _SERIES[2], _SERIES[4]]
    ordered_result = _returns_by_horizon(anchor_session=_S0, price_series=_SERIES, horizons=[3])[3]
    shuffled_result = _returns_by_horizon(anchor_session=_S0, price_series=shuffled, horizons=[3])[
        3
    ]
    assert ordered_result.target_session == shuffled_result.target_session == _S3


# ── invalid horizon input ────────────────────────────────────────────────────


@pytest.mark.parametrize("bad_horizon", [0, -1, -5])
def test_a_non_positive_horizon_is_rejected(bad_horizon: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        compute_forward_returns(anchor_session=_S0, price_series=_SERIES, horizons=[bad_horizon])


# ── no-alignment shortcut never fabricates a return ─────────────────────────


def test_no_alignment_forward_returns_produces_no_fabricated_data() -> None:
    event_id = uuid.uuid4()
    listing_id = uuid.uuid4()
    result = no_alignment_forward_returns(
        event_id=event_id, listing_id=listing_id, horizons=[1, 5, 20]
    )
    assert result.event_id == event_id
    assert result.listing_id == listing_id
    assert result.anchor_session is None
    assert result.price_source_id is None
    assert {fr.horizon for fr in result.forward_returns} == {1, 5, 20}
    for forward_return in result.forward_returns:
        assert forward_return.status is ForwardReturnStatus.NO_ALIGNMENT
        assert forward_return.return_value is None
        assert forward_return.anchor_close is None
        assert forward_return.target_close is None
