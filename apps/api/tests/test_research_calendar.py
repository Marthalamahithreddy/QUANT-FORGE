"""Unit tests for pure earnings-event-to-trading-session alignment.

Everything under test here is a pure function over plain ``date`` lists -- no
database, no ORM instances, no fixtures beyond the dates themselves. That is
deliberate: this is exactly the layer the Step 8 design calls out as safe to
verify without PostgreSQL, so turning these into integration tests would only
slow the suite down for no additional confidence.
"""

from __future__ import annotations

from datetime import date

import pytest

from quantforge.models.enums import EarningsTiming
from quantforge.research.calendar import AlignmentStatus, SessionAlignment, align_event_to_sessions

# A representative run of real sessions: Mon-Fri, skipping the weekend, with
# 2026-01-01 (a Thursday) deliberately omitted to stand in for a market
# holiday.
_MON = date(2026, 1, 5)
_TUE = date(2026, 1, 6)
_WED = date(2026, 1, 7)
_THU = date(2026, 1, 8)
_FRI = date(2026, 1, 9)
_NEXT_MON = date(2026, 1, 12)

_NORMAL_WEEK = [_MON, _TUE, _WED, _THU, _FRI]


def _align(
    *,
    event_date: date,
    timing: EarningsTiming,
    available_sessions: list[date],
) -> SessionAlignment:
    return align_event_to_sessions(
        event_date=event_date, timing=timing, available_sessions=available_sessions
    )


# ── BMO / AMC on an ordinary mid-week session ──────────────────────────────


def test_bmo_on_a_normal_trading_session() -> None:
    result = _align(
        event_date=_WED, timing=EarningsTiming.BEFORE_MARKET, available_sessions=_NORMAL_WEEK
    )
    assert result.status is AlignmentStatus.ALIGNED
    assert result.event_session == _WED
    assert result.first_post_event_session == _WED


def test_amc_on_a_normal_trading_session() -> None:
    result = _align(
        event_date=_WED, timing=EarningsTiming.AFTER_MARKET, available_sessions=_NORMAL_WEEK
    )
    assert result.status is AlignmentStatus.ALIGNED
    assert result.event_session == _WED
    assert result.first_post_event_session == _THU


def test_amc_never_uses_the_same_sessions_own_close() -> None:
    """Invariant 4: an AFTER_MARKET event's tradable session is strictly later.

    Even though ``_WED`` is itself an available session, it must never be
    returned as the post-event tradable session for an AMC release.
    """
    result = _align(
        event_date=_WED, timing=EarningsTiming.AFTER_MARKET, available_sessions=_NORMAL_WEEK
    )
    assert result.first_post_event_session != _WED
    assert result.first_post_event_session is not None
    assert result.first_post_event_session > _WED


# ── gaps: weekends and holidays, never calendar-day arithmetic ────────────


def test_friday_amc_rolls_to_the_next_available_session() -> None:
    """Friday AMC -> Monday, found by searching sessions, not by +1/+3 days."""
    sessions = [_FRI, _NEXT_MON]
    result = _align(
        event_date=_FRI, timing=EarningsTiming.AFTER_MARKET, available_sessions=sessions
    )
    assert result.status is AlignmentStatus.ALIGNED
    assert result.event_session == _FRI
    assert result.first_post_event_session == _NEXT_MON


def test_weekend_event_date_before_market() -> None:
    """A BMO event dated to a weekend still resolves to the next real session.

    ``event_session`` is honestly ``None`` -- the weekend date itself was
    never a session QuantForge has data for -- but the first tradable session
    is still determinable by searching forward, never by assuming "+1 day".
    """
    saturday = date(2026, 1, 10)
    sessions = [_FRI, _NEXT_MON]
    result = _align(
        event_date=saturday, timing=EarningsTiming.BEFORE_MARKET, available_sessions=sessions
    )
    assert result.status is AlignmentStatus.ALIGNED
    assert result.event_session is None
    assert result.first_post_event_session == _NEXT_MON


def test_weekend_event_date_after_market() -> None:
    saturday = date(2026, 1, 10)
    sessions = [_FRI, _NEXT_MON]
    result = _align(
        event_date=saturday, timing=EarningsTiming.AFTER_MARKET, available_sessions=sessions
    )
    assert result.status is AlignmentStatus.ALIGNED
    assert result.event_session is None
    assert result.first_post_event_session == _NEXT_MON


def test_holiday_gap_before_market() -> None:
    """A BMO event dated to a mid-week holiday rolls forward to the next session."""
    holiday = date(2026, 1, 1)
    sessions = [_FRI, _NEXT_MON]  # neither contains the holiday itself
    result = _align(
        event_date=holiday, timing=EarningsTiming.BEFORE_MARKET, available_sessions=sessions
    )
    assert result.status is AlignmentStatus.ALIGNED
    assert result.event_session is None
    assert result.first_post_event_session == _FRI


# ── timing that refuses a session rather than guessing ─────────────────────


def test_during_market_is_never_resolved_to_a_session() -> None:
    result = _align(
        event_date=_WED, timing=EarningsTiming.DURING_MARKET, available_sessions=_NORMAL_WEEK
    )
    assert result.status is AlignmentStatus.INTRADAY_PRECISION_UNSUPPORTED
    assert result.event_session is None
    assert result.first_post_event_session is None


def test_unknown_timing_is_never_resolved_to_a_session() -> None:
    result = _align(event_date=_WED, timing=EarningsTiming.UNKNOWN, available_sessions=_NORMAL_WEEK)
    assert result.status is AlignmentStatus.UNKNOWN_TIMING
    assert result.event_session is None
    assert result.first_post_event_session is None


# ── insufficient / exhausted price history ─────────────────────────────────


@pytest.mark.parametrize("timing", [EarningsTiming.BEFORE_MARKET, EarningsTiming.AFTER_MARKET])
def test_no_price_history_at_all(timing: EarningsTiming) -> None:
    result = _align(event_date=_WED, timing=timing, available_sessions=[])
    assert result.status is AlignmentStatus.NO_SESSION_DATA
    assert result.event_session is None
    assert result.first_post_event_session is None


def test_amc_with_no_later_session_yet() -> None:
    """The event's own session is known, but nothing has been ingested after it."""
    result = _align(
        event_date=_FRI, timing=EarningsTiming.AFTER_MARKET, available_sessions=[_MON, _TUE, _FRI]
    )
    assert result.status is AlignmentStatus.NO_SESSION_DATA
    assert result.event_session == _FRI
    assert result.first_post_event_session is None


# ── ordering ────────────────────────────────────────────────────────────────


def test_sessions_are_treated_as_an_ordered_set_regardless_of_input_order() -> None:
    """A caller handing sessions out of order, with duplicates, must not change the answer.

    This also stands in for "ordered sessions rather than calendar offsets":
    the function must search the actual supplied set rather than compute
    ``event_date + 1``.
    """
    shuffled_with_duplicates = [_FRI, _TUE, _TUE, _MON, _WED, _WED, _THU]
    ordered = _align(
        event_date=_WED, timing=EarningsTiming.AFTER_MARKET, available_sessions=_NORMAL_WEEK
    )
    shuffled = _align(
        event_date=_WED,
        timing=EarningsTiming.AFTER_MARKET,
        available_sessions=shuffled_with_duplicates,
    )
    assert shuffled.first_post_event_session == ordered.first_post_event_session == _THU
