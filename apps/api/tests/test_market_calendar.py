"""Tests for the market-session clock adapter, against concrete historical dates.

Expected instants are asserted here as the authoritative reference values;
none of them is hardcoded in the implementation, which only converts what the
``exchange_calendars`` dependency returns.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from quantforge.market import (
    SUPPORTED_MICS,
    ExchangeCalendarsSessionCalendar,
    MarketSession,
    SessionLookup,
    SessionLookupStatus,
)
from quantforge.market.calendar import _in_leading_gap

_CAL = ExchangeCalendarsSessionCalendar()


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def test_ordinary_session_has_exact_utc_open_and_close() -> None:
    result = _CAL.session_for("XNYS", date(2024, 3, 15))

    assert result.status is SessionLookupStatus.AVAILABLE
    assert result.session == MarketSession(
        trading_date=date(2024, 3, 15),
        open_at=_utc(2024, 3, 15, 13, 30),
        close_at=_utc(2024, 3, 15, 20, 0),
    )


def test_weekend_is_not_a_session() -> None:
    result = _CAL.session_for("XNYS", date(2024, 3, 16))

    assert result.status is SessionLookupStatus.NOT_A_SESSION
    assert result.session is None


def test_thanksgiving_is_not_a_session() -> None:
    assert _CAL.session_for("XNYS", date(2024, 11, 28)).status is SessionLookupStatus.NOT_A_SESSION


def test_day_after_thanksgiving_is_an_early_close() -> None:
    session = _CAL.session_for("XNYS", date(2024, 11, 29)).session

    assert session is not None
    assert session.open_at == _utc(2024, 11, 29, 14, 30)
    assert session.close_at == _utc(2024, 11, 29, 18, 0)  # 13:00 ET


def test_open_instant_follows_daylight_saving_time() -> None:
    winter = _CAL.session_for("XNYS", date(2024, 1, 16)).session
    summer = _CAL.session_for("XNYS", date(2024, 7, 16)).session

    assert winter is not None and summer is not None
    assert winter.open_at == _utc(2024, 1, 16, 14, 30)
    assert summer.open_at == _utc(2024, 7, 16, 13, 30)


@pytest.mark.parametrize("mic", ["XNYS", "XNAS", "ARCX"])
def test_every_supported_mic_resolves_a_session(mic: str) -> None:
    result = _CAL.session_for(mic, date(2024, 3, 15))

    assert result.status is SessionLookupStatus.AVAILABLE
    assert result.mic == mic
    assert result.session is not None
    assert result.session.open_at == _utc(2024, 3, 15, 13, 30)


def test_supported_mics_are_exactly_the_reviewed_set() -> None:
    assert frozenset({"XNYS", "XNAS", "ARCX"}) == SUPPORTED_MICS


@pytest.mark.parametrize("method", ["session_for", "next_session", "previous_session"])
@pytest.mark.parametrize("mic", ["XLON", "XXXX", "xnys", ""])
def test_unknown_mic_is_unsupported_never_defaulted_to_nyse(method: str, mic: str) -> None:
    result = getattr(_CAL, method)(mic, date(2024, 3, 15))

    assert result.status is SessionLookupStatus.UNSUPPORTED_MIC
    assert result.session is None


def test_next_session_after_friday_is_monday() -> None:
    result = _CAL.next_session("XNYS", date(2024, 3, 15))  # Friday

    assert result.session is not None
    assert result.session.trading_date == date(2024, 3, 18)


def test_next_session_from_a_weekend_date_is_monday() -> None:
    result = _CAL.next_session("XNYS", date(2024, 3, 16))  # Saturday

    assert result.session is not None
    assert result.session.trading_date == date(2024, 3, 18)


def test_next_session_across_thanksgiving_skips_the_holiday() -> None:
    wednesday = _CAL.next_session("XNYS", date(2024, 11, 27))
    thursday = _CAL.next_session("XNYS", date(2024, 11, 28))

    assert wednesday.session is not None and thursday.session is not None
    assert wednesday.session.trading_date == date(2024, 11, 29)
    assert thursday.session.trading_date == date(2024, 11, 29)


def test_next_session_is_strictly_after_a_session_date() -> None:
    result = _CAL.next_session("XNYS", date(2024, 3, 13))

    assert result.session is not None
    assert result.session.trading_date == date(2024, 3, 14)


def test_previous_session_is_strictly_before_and_skips_non_sessions() -> None:
    monday = _CAL.previous_session("XNYS", date(2024, 3, 18))
    holiday = _CAL.previous_session("XNYS", date(2024, 11, 28))

    assert monday.session is not None and holiday.session is not None
    assert monday.session.trading_date == date(2024, 3, 15)
    assert holiday.session.trading_date == date(2024, 11, 27)


@pytest.mark.parametrize(
    ("method", "on"),
    [
        ("session_for", date(1990, 1, 2)),
        ("session_for", date(2099, 1, 2)),
        ("next_session", date(2099, 1, 2)),
        ("previous_session", date(1990, 1, 2)),
        ("previous_session", date(2000, 1, 3)),  # first session: nothing before it
    ],
)
def test_dates_outside_the_calendar_range_are_a_typed_outcome(method: str, on: date) -> None:
    result = getattr(_CAL, method)("XNYS", on)

    assert result.status is SessionLookupStatus.OUT_OF_CALENDAR_RANGE
    assert result.session is None


def test_returned_instants_are_stdlib_utc_datetimes() -> None:
    session = _CAL.session_for("XNYS", date(2024, 3, 15)).session

    assert session is not None
    for instant in (session.open_at, session.close_at):
        assert type(instant) is datetime  # not a pandas.Timestamp subclass
        assert instant.utcoffset() == timedelta(0)
    assert type(session.trading_date) is date


def test_calendar_is_cached_per_instance() -> None:
    calendar = ExchangeCalendarsSessionCalendar()
    calendar.session_for("XNYS", date(2024, 3, 15))
    calendar.session_for("XNYS", date(2024, 3, 14))

    assert list(calendar._calendars) == ["XNYS"]


# ── MarketSession validation ────────────────────────────────────────────────


def test_market_session_rejects_naive_instants() -> None:
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        MarketSession(
            date(2024, 3, 15),
            datetime(2024, 3, 15, 13, 30),  # noqa: DTZ001 -- deliberately naive
            _utc(2024, 3, 15, 20),
        )


def test_market_session_rejects_non_utc_offset() -> None:
    eastern = timezone(timedelta(hours=-5))
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        MarketSession(
            date(2024, 3, 15), datetime(2024, 3, 15, 9, 30, tzinfo=eastern), _utc(2024, 3, 15, 20)
        )


@pytest.mark.parametrize("close_hour", [13, 12])
def test_market_session_requires_close_after_open(close_hour: int) -> None:
    with pytest.raises(ValueError, match="after open_at"):
        MarketSession(date(2024, 3, 15), _utc(2024, 3, 15, 13), _utc(2024, 3, 15, close_hour))


# ── SessionLookup invariant and input validation ────────────────────────────


def test_session_lookup_requires_a_session_exactly_when_available() -> None:
    session = MarketSession(date(2024, 3, 15), _utc(2024, 3, 15, 13, 30), _utc(2024, 3, 15, 20))

    with pytest.raises(ValueError, match="if and only if"):
        SessionLookup(SessionLookupStatus.AVAILABLE, "XNYS", date(2024, 3, 15), None, "x")
    with pytest.raises(ValueError, match="if and only if"):
        SessionLookup(SessionLookupStatus.NOT_A_SESSION, "XNYS", date(2024, 3, 15), session, "x")


@pytest.mark.parametrize("method", ["session_for", "next_session", "previous_session"])
def test_a_datetime_is_rejected_as_a_programming_error(method: str) -> None:
    with pytest.raises(TypeError, match="calendar date"):
        getattr(_CAL, method)("XNYS", datetime(2024, 3, 15, 23, tzinfo=UTC))


def test_supported_mics_cannot_be_mutated() -> None:
    assert not hasattr(SUPPORTED_MICS, "add")


def test_next_session_from_a_non_session_date_at_the_calendar_start_is_in_range() -> None:
    # 2000-01-01 is a Saturday, before the first session (Monday 2000-01-03), yet the
    # answer is covered by the calendar.
    result = _CAL.next_session("XNYS", date(2000, 1, 1))

    assert result.status is SessionLookupStatus.AVAILABLE
    assert result.session is not None
    assert result.session.trading_date == date(2000, 1, 3)


def test_next_session_before_the_calendar_start_is_not_guessed() -> None:
    result = _CAL.next_session("XNYS", date(1999, 12, 31))

    assert result.status is SessionLookupStatus.OUT_OF_CALENDAR_RANGE
    assert result.session is None


def test_previous_session_far_past_the_calendar_end_is_not_guessed() -> None:
    result = _CAL.previous_session("XNYS", date(2099, 1, 1))

    assert result.status is SessionLookupStatus.OUT_OF_CALENDAR_RANGE


def test_session_for_a_non_session_date_at_the_calendar_start_is_not_a_session() -> None:
    # 2000-01-01/02 are the weekend before the first session: covered, just not sessions.
    for day in (date(2000, 1, 1), date(2000, 1, 2)):
        result = _CAL.session_for("XNYS", day)
        assert result.status is SessionLookupStatus.NOT_A_SESSION, day


def test_a_long_leading_gap_is_not_called_a_non_session() -> None:
    """If a calendar's first session were far after its start, the gap is uncovered, not a holiday."""
    assert _in_leading_gap(date(2000, 1, 1), date(2000, 1, 3)) is True
    assert _in_leading_gap(date(2000, 1, 1), date(2000, 6, 1)) is False
    assert _in_leading_gap(date(1999, 12, 31), date(2000, 1, 3)) is False
