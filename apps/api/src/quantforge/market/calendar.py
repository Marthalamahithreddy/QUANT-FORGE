"""Historical market-session clock, backed by an authoritative exchange calendar.

Answers one question the rest of QuantForge could not answer before Step 10.4:
*at what exact UTC instants did a given exchange's session open and close on
a given trading date?* Every instant here originates from the
``exchange_calendars`` dependency -- this module never constructs 09:30, 16:00
or an early-close time itself, never derives a holiday, and never implements
daylight-saving rules. It only (a) restricts which MICs QuantForge will
answer for, (b) converts the library's pandas values to standard timezone-
aware UTC :class:`~datetime.datetime`, and (c) turns the library's exceptions
into typed domain outcomes.

The rest of QuantForge depends on :class:`SessionCalendar` (a structural
protocol) and :class:`MarketSession`, never on ``exchange_calendars`` or
pandas -- no calendar object or ``pandas.Timestamp`` crosses this module's
boundary.

Explicit MIC allow-list
------------------------
Only the MICs in :data:`SUPPORTED_MICS` are answered. Any other MIC is
:attr:`SessionLookupStatus.UNSUPPORTED_MIC` -- it is never defaulted to NYSE,
and an arbitrary MIC is never passed through to the library. ``XNYS``,
``XNAS`` and ``ARCX`` are all US-equity venues that ``exchange_calendars``
currently resolves to the same underlying NYSE-hours calendar; that aliasing
is a property of the library, and QuantForge still validates each MIC
explicitly rather than relying on it.

Ordinary outcomes are values, not exceptions
---------------------------------------------
A weekend, a holiday, an unsupported venue and a date outside the calendar's
range are all expected inputs and come back as a :class:`SessionLookup` with
a status. Only a programming error (a malformed :class:`MarketSession`)
raises.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import exchange_calendars as xcals
from exchange_calendars.errors import DateOutOfBounds, RequestedSessionOutOfBounds

from quantforge.market.sessions import (
    MarketSession,
    SessionLookup,
    SessionLookupStatus,
)

__all__ = ["SUPPORTED_MICS", "ExchangeCalendarsSessionCalendar"]

#: The reviewed US-equity MICs QuantForge answers for. Each is also the
#: ``exchange_calendars`` calendar name used for it. Extending this is a
#: deliberate, reviewed change, never a pass-through.
SUPPORTED_MICS: frozenset[str] = frozenset({"XNYS", "XNAS", "ARCX"})

#: Earliest session the calendars are built for. Wider than any earnings
#: history QuantForge could hold; the upper bound is the library default
#: (about a year past today).
_CALENDAR_START = "2000-01-01"
_CALENDAR_START_DATE = date.fromisoformat(_CALENDAR_START)
#: A date between the calendar's start and its first session is only a covered
#: weekend/holiday if the gap is that short. A longer gap would mean the calendar
#: clamped its own start (a venue that did not exist yet), which is not ours to
#: call "not a session".
_MAX_LEADING_GAP_DAYS = 7


def _in_leading_gap(trading_date: date, first_session: date) -> bool:
    return (
        _CALENDAR_START_DATE <= trading_date < first_session
        and (first_session - _CALENDAR_START_DATE).days <= _MAX_LEADING_GAP_DAYS
    )


def _to_utc(timestamp: Any) -> datetime:
    converted: datetime = timestamp.to_pydatetime().astimezone(UTC)
    return converted


def _require_plain_date(value: date) -> None:
    # ``datetime`` is a ``date`` subclass, and a time-of-day on a *session label*
    # is a caller bug the library would reject with a raw ValueError.
    if isinstance(value, datetime):
        message = f"expected a calendar date, got a datetime: {value!r}"
        raise TypeError(message)


class ExchangeCalendarsSessionCalendar:
    """:class:`SessionCalendar` backed by ``exchange_calendars``.

    Calendars are built lazily, one per supported MIC, and held on the
    instance -- there is no module-level calendar state. A calendar's upper
    bound (about a year past the day it was first built) is fixed at build
    time, so construct a fresh instance per run rather than keeping one alive
    for years; lookups beyond the bound are
    :attr:`SessionLookupStatus.OUT_OF_CALENDAR_RANGE`, never a guess.
    """

    def __init__(self) -> None:
        self._calendars: dict[str, xcals.ExchangeCalendar] = {}

    def _calendar(self, mic: str) -> xcals.ExchangeCalendar | None:
        if mic not in SUPPORTED_MICS:
            return None
        if mic not in self._calendars:
            self._calendars[mic] = xcals.get_calendar(mic, start=_CALENDAR_START)
        return self._calendars[mic]

    @staticmethod
    def _unsupported(mic: str, on: date) -> SessionLookup:
        return SessionLookup(
            status=SessionLookupStatus.UNSUPPORTED_MIC,
            mic=mic,
            requested_date=on,
            session=None,
            reason=f"MIC {mic!r} is not in the reviewed supported set {sorted(SUPPORTED_MICS)}",
        )

    @staticmethod
    def _out_of_range(mic: str, on: date, detail: str) -> SessionLookup:
        return SessionLookup(
            status=SessionLookupStatus.OUT_OF_CALENDAR_RANGE,
            mic=mic,
            requested_date=on,
            session=None,
            reason=f"{detail} is outside the {mic} calendar's range",
        )

    @staticmethod
    def _not_a_session(mic: str, on: date) -> SessionLookup:
        return SessionLookup(
            status=SessionLookupStatus.NOT_A_SESSION,
            mic=mic,
            requested_date=on,
            session=None,
            reason=f"{on} is not a {mic} trading session (weekend or holiday)",
        )

    @staticmethod
    def _available(
        calendar: xcals.ExchangeCalendar, mic: str, requested: date, session_date: date
    ) -> SessionLookup:
        market_session = MarketSession(
            trading_date=session_date,
            open_at=_to_utc(calendar.session_open(session_date)),
            close_at=_to_utc(calendar.session_close(session_date)),
        )
        return SessionLookup(
            status=SessionLookupStatus.AVAILABLE,
            mic=mic,
            requested_date=requested,
            session=market_session,
            reason="session from the exchange calendar",
        )

    def session_for(self, mic: str, trading_date: date) -> SessionLookup:
        _require_plain_date(trading_date)
        calendar = self._calendar(mic)
        if calendar is None:
            return self._unsupported(mic, trading_date)
        if _in_leading_gap(trading_date, calendar.first_session.date()):
            # Covered by the calendar but before its first session: a
            # weekend/holiday at the very start of the range, not a range error.
            return self._not_a_session(mic, trading_date)
        try:
            is_session = calendar.is_session(trading_date)
        except DateOutOfBounds:
            return self._out_of_range(mic, trading_date, f"date {trading_date}")
        if not is_session:
            return self._not_a_session(mic, trading_date)
        return self._available(calendar, mic, trading_date, trading_date)

    def next_session(self, mic: str, trading_date: date) -> SessionLookup:
        return self._adjacent(mic, trading_date, forward=True)

    def previous_session(self, mic: str, trading_date: date) -> SessionLookup:
        return self._adjacent(mic, trading_date, forward=False)

    def _adjacent(self, mic: str, trading_date: date, *, forward: bool) -> SessionLookup:
        _require_plain_date(trading_date)
        calendar = self._calendar(mic)
        if calendar is None:
            return self._unsupported(mic, trading_date)
        direction = "next" if forward else "previous"
        first_session = calendar.first_session.date()
        if forward and _in_leading_gap(trading_date, first_session):
            # The calendar covers every session from its start date, and none
            # falls between that start and its first session, so the next session
            # after such a date is the first session -- an in-range answer. Dates
            # before the calendar's start stay OUT_OF_CALENDAR_RANGE: their true
            # next session is not covered.
            return self._available(calendar, mic, trading_date, first_session)
        try:
            if calendar.is_session(trading_date):
                found = (
                    calendar.next_session(trading_date)
                    if forward
                    else calendar.previous_session(trading_date)
                )
            else:
                # Not a session: the nearest session in the requested
                # direction is already strictly after / before the date.
                found = calendar.date_to_session(trading_date, direction=direction)
        except DateOutOfBounds, RequestedSessionOutOfBounds:
            return self._out_of_range(
                mic, trading_date, f"the {direction} session relative to {trading_date}"
            )
        return self._available(calendar, mic, trading_date, found.date())
