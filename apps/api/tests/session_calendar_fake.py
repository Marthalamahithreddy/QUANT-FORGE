"""A deterministic in-memory :class:`SessionCalendar` for research tests (no calendar library).

The session list is given EXPLICITLY by the test, so a holiday is whatever the test leaves out.
It exists to prove research behaviour against a known calendar; the real adapter is exercised
separately in ``tests/test_research_sessions_real_calendar.py``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time

from quantforge.market.sessions import (
    MarketSession,
    SessionLookup,
    SessionLookupStatus,
)


def _session(day: date) -> MarketSession:
    return MarketSession(
        trading_date=day,
        open_at=datetime.combine(day, time(14, 30), tzinfo=UTC),
        close_at=datetime.combine(day, time(21, 0), tzinfo=UTC),
    )


class FakeSessionCalendar:
    """Sessions are exactly ``sessions``; ``supported`` MICs only; range is [first, last]."""

    def __init__(self, sessions: Iterable[date], *, supported: Iterable[str] = ("XNYS",)) -> None:
        self._sessions = sorted(set(sessions))
        self._supported = frozenset(supported)
        self.lookups = 0

    def _unsupported(self, mic: str, on: date) -> SessionLookup | None:
        if mic in self._supported:
            return None
        return SessionLookup(SessionLookupStatus.UNSUPPORTED_MIC, mic, on, None, "unsupported mic")

    def _in_range(self, on: date) -> bool:
        return self._sessions[0] <= on <= self._sessions[-1]

    def session_for(self, mic: str, trading_date: date) -> SessionLookup:
        self.lookups += 1
        if (bad := self._unsupported(mic, trading_date)) is not None:
            return bad
        if not self._in_range(trading_date):
            return SessionLookup(
                SessionLookupStatus.OUT_OF_CALENDAR_RANGE, mic, trading_date, None, "out of range"
            )
        if trading_date in self._sessions:
            return SessionLookup(
                SessionLookupStatus.AVAILABLE, mic, trading_date, _session(trading_date), "fake"
            )
        return SessionLookup(SessionLookupStatus.NOT_A_SESSION, mic, trading_date, None, "closed")

    def next_session(self, mic: str, trading_date: date) -> SessionLookup:
        self.lookups += 1
        if (bad := self._unsupported(mic, trading_date)) is not None:
            return bad
        if trading_date < self._sessions[0]:
            return SessionLookup(
                SessionLookupStatus.OUT_OF_CALENDAR_RANGE, mic, trading_date, None, "before range"
            )
        later = [day for day in self._sessions if day > trading_date]
        if not later:
            return SessionLookup(
                SessionLookupStatus.OUT_OF_CALENDAR_RANGE, mic, trading_date, None, "past range"
            )
        return SessionLookup(
            SessionLookupStatus.AVAILABLE, mic, trading_date, _session(later[0]), "fake"
        )

    def previous_session(self, mic: str, trading_date: date) -> SessionLookup:
        self.lookups += 1
        if (bad := self._unsupported(mic, trading_date)) is not None:
            return bad
        earlier = [day for day in self._sessions if day < trading_date]
        if not earlier:
            return SessionLookup(
                SessionLookupStatus.OUT_OF_CALENDAR_RANGE, mic, trading_date, None, "before range"
            )
        return SessionLookup(
            SessionLookupStatus.AVAILABLE, mic, trading_date, _session(earlier[-1]), "fake"
        )
