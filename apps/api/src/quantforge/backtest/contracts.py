"""Execution-eligibility vocabulary and result contract.

See :mod:`quantforge.backtest.execution` for the rules. Nothing here is a
fill: a candidate execution session/price type says *when* a decision could
defensibly execute, never at what numeric price, and no price value is read
anywhere in this package.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from quantforge.research.availability import PointInTimeEligibility, ResearchMode
from quantforge.signals.eps_surprise import EpsSurpriseDirection, SignalType

__all__ = [
    "ExecutionEligibility",
    "ExecutionEligibilityStatus",
    "ExecutionPriceType",
]


class ExecutionEligibilityStatus(StrEnum):
    """Whether, and why (or why not), a signal has a defensible candidate execution instant."""

    #: The signal was provably known strictly before a real session-open
    #: instant. ``candidate_execution_session``/``_price_type``/``_open_at``
    #: are populated. A candidate only -- no order, fill or price exists.
    CANDIDATE_EXECUTION_ELIGIBLE = "CANDIDATE_EXECUTION_ELIGIBLE"

    #: research_mode is DESCRIPTIVE. A descriptive signal may be researched
    #: and evaluated but must never become an executable decision.
    NOT_POINT_IN_TIME_SIGNAL = "NOT_POINT_IN_TIME_SIGNAL"

    #: POINT_IN_TIME but ``observed_at`` evidence is missing, so no
    #: availability claim can be supported.
    NOT_POINT_IN_TIME_ELIGIBLE = "NOT_POINT_IN_TIME_ELIGIBLE"

    #: The event's timing is UNKNOWN; before/after-market cannot be inferred
    #: (``observed_at`` alone is deliberately not used to guess it).
    UNSUPPORTED_TIMING = "UNSUPPORTED_TIMING"

    #: The signal itself is not AVAILABLE (missing/zero EPS, no effective
    #: session or listing); there is no direction or value to assess.
    SIGNAL_UNAVAILABLE = "SIGNAL_UNAVAILABLE"

    #: DURING_MARKET: daily bars cannot support a deterministic intraday
    #: execution point, even with an authoritative session clock.
    INSUFFICIENT_TEMPORAL_PRECISION = "INSUFFICIENT_TEMPORAL_PRECISION"

    #: The historical listing's MIC is missing or not in the reviewed
    #: supported set (:data:`quantforge.market.SUPPORTED_MICS`).
    UNSUPPORTED_MIC = "UNSUPPORTED_MIC"

    #: A BEFORE_MARKET event's date is not a trading session for its venue.
    NOT_A_TRADING_SESSION = "NOT_A_TRADING_SESSION"

    #: The session clock has no answer for this date (outside the calendar's
    #: covered range).
    SESSION_CLOCK_UNAVAILABLE = "SESSION_CLOCK_UNAVAILABLE"

    #: ``observed_at`` is not strictly before the candidate session open
    #: (equal counts as not before). No later session is searched.
    INFORMATION_NOT_KNOWN_BEFORE_OPEN = "INFORMATION_NOT_KNOWN_BEFORE_OPEN"


class ExecutionPriceType(StrEnum):
    """Which price a candidate execution would reference. A label, never a value."""

    #: The open of the event's own session (BEFORE_MARKET only).
    SAME_SESSION_OPEN = "SAME_SESSION_OPEN"

    #: The open of the first session strictly after the event date
    #: (AFTER_MARKET only).
    NEXT_SESSION_OPEN = "NEXT_SESSION_OPEN"


@dataclass(frozen=True, slots=True)
class ExecutionEligibility:
    """Whether one signal has a defensible candidate execution instant -- never the trade itself.

    Echoes the signal's identity and classification. Carries no forward
    return, benchmark/abnormal return, price, position size or capital figure.
    ``direction`` is passed through unchanged and is never mapped to a side.

    The signal's research ``effective_session`` is deliberately not echoed:
    it is a price-bar-derived research alignment, and carrying it beside the
    calendar-derived candidate session would put two "session" answers on one
    object. ``candidate_execution_session`` is the only execution session.

    ``candidate_execution_session``, ``candidate_execution_price_type`` and
    ``candidate_execution_open_at`` are populated if and only if ``status`` is
    ``CANDIDATE_EXECUTION_ELIGIBLE``; ``candidate_execution_open_at`` is the
    calendar-supplied UTC instant ``observed_at`` was strictly compared to.
    """

    event_id: uuid.UUID
    company_id: uuid.UUID
    security_id: uuid.UUID | None
    listing_id: uuid.UUID | None
    exchange_mic: str | None
    signal_type: SignalType
    direction: EpsSurpriseDirection | None
    signal_value: Decimal | None
    research_mode: ResearchMode
    point_in_time_eligibility: PointInTimeEligibility
    observed_at: datetime | None
    status: ExecutionEligibilityStatus
    candidate_execution_session: date | None
    candidate_execution_price_type: ExecutionPriceType | None
    candidate_execution_open_at: datetime | None
    reason: str
