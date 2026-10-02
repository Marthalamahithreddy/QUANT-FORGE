"""Market-session contracts: the pure, dependency-free half of the session clock.

:class:`MarketSession`, :class:`SessionLookup` and the :class:`SessionCalendar`
protocol are all that the rest of QuantForge (notably
:mod:`quantforge.backtest`) needs. They import only the standard library, so
depending on them never loads ``exchange_calendars`` or pandas; that adapter
lives in :mod:`quantforge.market.calendar` and is exported lazily.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Protocol

__all__ = ["MarketSession", "SessionCalendar", "SessionLookup", "SessionLookupStatus"]


class SessionLookupStatus(StrEnum):
    """Why a session lookup did, or honestly did not, produce a session."""

    #: ``session`` is populated.
    AVAILABLE = "AVAILABLE"

    #: The date is a weekend or exchange holiday (``session_for`` only).
    NOT_A_SESSION = "NOT_A_SESSION"

    #: The MIC is not in :data:`SUPPORTED_MICS`.
    UNSUPPORTED_MIC = "UNSUPPORTED_MIC"

    #: The date, or the session being searched for, lies outside the range the
    #: calendar covers.
    OUT_OF_CALENDAR_RANGE = "OUT_OF_CALENDAR_RANGE"


@dataclass(frozen=True, slots=True)
class MarketSession:
    """One exchange trading session: its date and exact UTC open/close instants."""

    trading_date: date
    open_at: datetime
    close_at: datetime

    def __post_init__(self) -> None:
        for name, value in (("open_at", self.open_at), ("close_at", self.close_at)):
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                message = f"{name} must be a timezone-aware UTC datetime, got {value!r}"
                raise ValueError(message)
        if self.close_at <= self.open_at:
            message = f"close_at {self.close_at} must be after open_at {self.open_at}"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class SessionLookup:
    """The outcome of one calendar lookup. ``session`` is set iff ``status`` is AVAILABLE."""

    status: SessionLookupStatus
    mic: str
    requested_date: date
    session: MarketSession | None
    reason: str

    def __post_init__(self) -> None:
        if (self.status is SessionLookupStatus.AVAILABLE) != (self.session is not None):
            message = (
                f"session must be set if and only if status is AVAILABLE (status={self.status})"
            )
            raise ValueError(message)


class SessionCalendar(Protocol):
    """What the rest of QuantForge may ask of a market calendar."""

    def session_for(self, mic: str, trading_date: date) -> SessionLookup:
        """The session on ``trading_date`` itself, or NOT_A_SESSION."""
        ...

    def next_session(self, mic: str, trading_date: date) -> SessionLookup:
        """The first session strictly after ``trading_date``.

        ``trading_date`` need not itself be a session (a Saturday or holiday is
        a valid starting point).
        """
        ...

    def previous_session(self, mic: str, trading_date: date) -> SessionLookup:
        """The last session strictly before ``trading_date`` (which need not be a session)."""
        ...
