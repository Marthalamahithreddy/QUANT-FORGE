"""Unit tests for earnings-event-to-trading-session alignment against an explicit calendar.

Since Step 11.2E the anchor comes from a :class:`SessionCalendar`, never from stored price bars.
These tests use :class:`tests.session_calendar_fake.FakeSessionCalendar` so every holiday is
whatever the test leaves out of its session list; no database, no calendar library. The real
``exchange_calendars`` adapter is exercised in ``tests/test_research_sessions_real_calendar.py``.
"""

from __future__ import annotations

from datetime import date

import pytest

from quantforge.market.sessions import SessionLookup, SessionLookupStatus
from quantforge.models.enums import EarningsTiming
from quantforge.research.calendar import (
    AlignmentStatus,
    SessionAlignment,
    align_event_to_sessions,
    proven_sessions_after,
)
from tests.session_calendar_fake import FakeSessionCalendar

_MIC = "XNYS"
# 2026-01-01 (Thursday) is a holiday and absent; the weekend 10-11 January is absent.
_MON = date(2026, 1, 5)
_TUE = date(2026, 1, 6)
_WED = date(2026, 1, 7)
_THU = date(2026, 1, 8)
_FRI = date(2026, 1, 9)
_NEXT_MON = date(2026, 1, 12)
_NEXT_TUE = date(2026, 1, 13)
_SESSIONS = [date(2025, 12, 31), _MON, _TUE, _WED, _THU, _FRI, _NEXT_MON, _NEXT_TUE]


def _calendar() -> FakeSessionCalendar:
    return FakeSessionCalendar(_SESSIONS)


def _align(
    event_date: date, timing: EarningsTiming, calendar: FakeSessionCalendar | None = None
) -> SessionAlignment:
    return align_event_to_sessions(
        event_date=event_date, timing=timing, calendar=calendar or _calendar(), mic=_MIC
    )


# ── BMO / AMC on an ordinary mid-week session ──────────────────────────────


def test_bmo_on_a_normal_trading_session() -> None:
    result = _align(_WED, EarningsTiming.BEFORE_MARKET)
    assert result.status is AlignmentStatus.ALIGNED
    assert result.event_session == _WED
    assert result.first_post_event_session == _WED


def test_amc_on_a_normal_trading_session() -> None:
    result = _align(_WED, EarningsTiming.AFTER_MARKET)
    assert result.status is AlignmentStatus.ALIGNED
    assert result.event_session == _WED
    assert result.first_post_event_session == _THU


def test_amc_never_uses_the_same_sessions_own_close() -> None:
    result = _align(_WED, EarningsTiming.AFTER_MARKET)
    assert result.first_post_event_session is not None
    assert result.first_post_event_session > _WED


# ── closed days come from the calendar, never from date arithmetic ─────────


def test_friday_amc_rolls_to_the_next_session() -> None:
    result = _align(_FRI, EarningsTiming.AFTER_MARKET)
    assert result.status is AlignmentStatus.ALIGNED
    assert result.event_session == _FRI
    assert result.first_post_event_session == _NEXT_MON


def test_weekend_event_date_before_market() -> None:
    result = _align(date(2026, 1, 10), EarningsTiming.BEFORE_MARKET)
    assert result.status is AlignmentStatus.ALIGNED
    assert result.event_session is None
    assert result.first_post_event_session == _NEXT_MON


def test_weekend_event_date_after_market() -> None:
    result = _align(date(2026, 1, 10), EarningsTiming.AFTER_MARKET)
    assert result.status is AlignmentStatus.ALIGNED
    assert result.event_session is None
    assert result.first_post_event_session == _NEXT_MON


def test_holiday_before_market_rolls_to_the_next_calendar_session() -> None:
    result = _align(date(2026, 1, 1), EarningsTiming.BEFORE_MARKET)
    assert result.status is AlignmentStatus.ALIGNED
    assert result.event_session is None
    assert result.first_post_event_session == _MON  # 2026-01-02 is also absent in this calendar


def test_the_anchor_is_chosen_by_the_calendar_not_by_stored_bars() -> None:
    """There is no price input at all: the same answer whether or not any bar exists."""
    first = _align(_WED, EarningsTiming.AFTER_MARKET)
    second = _align(_WED, EarningsTiming.AFTER_MARKET, FakeSessionCalendar(_SESSIONS))
    assert first == second
    assert first.first_post_event_session == _THU


# ── timing that refuses a session rather than guessing ─────────────────────


def test_during_market_is_never_resolved_to_a_session() -> None:
    result = _align(_WED, EarningsTiming.DURING_MARKET)
    assert result.status is AlignmentStatus.INTRADAY_PRECISION_UNSUPPORTED
    assert result.event_session is None
    assert result.first_post_event_session is None


def test_unknown_timing_is_never_resolved_to_a_session() -> None:
    calendar = _calendar()
    result = _align(_WED, EarningsTiming.UNKNOWN, calendar)
    assert result.status is AlignmentStatus.UNKNOWN_TIMING
    assert result.first_post_event_session is None
    assert calendar.lookups == 0  # a refusal on timing never needs the calendar


# ── what the calendar cannot prove is refused, never guessed ───────────────


def test_unsupported_venue_is_no_session_data() -> None:
    result = align_event_to_sessions(
        event_date=_WED, timing=EarningsTiming.AFTER_MARKET, calendar=_calendar(), mic="XLON"
    )
    assert result.status is AlignmentStatus.NO_SESSION_DATA
    assert result.first_post_event_session is None
    assert "UNSUPPORTED_MIC" in result.reason


@pytest.mark.parametrize("timing", [EarningsTiming.BEFORE_MARKET, EarningsTiming.AFTER_MARKET])
def test_an_event_date_outside_the_calendar_range_is_no_session_data(
    timing: EarningsTiming,
) -> None:
    result = _align(date(2031, 1, 6), timing)
    assert result.status is AlignmentStatus.NO_SESSION_DATA
    assert result.first_post_event_session is None
    assert "OUT_OF_CALENDAR_RANGE" in result.reason


def test_amc_on_the_last_calendar_session_has_no_proven_next_session() -> None:
    result = _align(_NEXT_TUE, EarningsTiming.AFTER_MARKET)
    assert result.status is AlignmentStatus.NO_SESSION_DATA
    assert result.event_session == _NEXT_TUE
    assert result.first_post_event_session is None


# ── proven_sessions_after ───────────────────────────────────────────────────


def test_proven_sessions_skip_closed_days_and_stop_honestly_at_the_calendar_edge() -> None:
    proven = proven_sessions_after(calendar=_calendar(), mic=_MIC, anchor_session=_THU, count=5)
    assert proven.sessions == (_FRI, _NEXT_MON, _NEXT_TUE)
    assert proven.complete is False
    assert "OUT_OF_CALENDAR_RANGE" in proven.reason


def test_proven_sessions_complete_when_the_calendar_covers_them() -> None:
    proven = proven_sessions_after(calendar=_calendar(), mic=_MIC, anchor_session=_MON, count=3)
    assert proven.sessions == (_TUE, _WED, _THU)
    assert proven.complete is True


def test_proven_sessions_reject_a_non_positive_count() -> None:
    with pytest.raises(ValueError, match="positive"):
        proven_sessions_after(calendar=_calendar(), mic=_MIC, anchor_session=_MON, count=0)


def test_a_calendar_that_cannot_answer_yields_no_sessions_not_stored_dates() -> None:
    class _Broken:
        def session_for(self, mic: str, trading_date: date) -> SessionLookup:
            return SessionLookup(SessionLookupStatus.UNSUPPORTED_MIC, mic, trading_date, None, "no")

        next_session = session_for
        previous_session = session_for

    proven = proven_sessions_after(calendar=_Broken(), mic=_MIC, anchor_session=_MON, count=3)
    assert proven.sessions == ()
    assert proven.complete is False
