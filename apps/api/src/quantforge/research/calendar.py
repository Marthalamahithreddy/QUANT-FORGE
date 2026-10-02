"""Deterministic earnings-event-to-trading-session alignment.

This module answers one narrow question: given an earnings event's declared
``event_date``/``timing`` and the trading sessions QuantForge actually holds
price data for, which session does the event belong to, and which is the
first session the market could have reacted in?

Everything here is a pure function over plain values -- no database session,
no ORM, no I/O. ``available_sessions`` is the caller's responsibility to
supply, and it must already be the real, ordered set of sessions QuantForge
has price observations for (see
:mod:`quantforge.research.listing_resolution` and
:mod:`quantforge.research.alignment` for where that comes from). This module
never assumes ``next session = event_date + 1 calendar day``: weekends,
holidays and gaps in the stored price history are handled by searching the
supplied session list, never by date arithmetic.

Conservatism over precision
----------------------------
A ``DURING_MARKET`` or ``UNKNOWN`` timing never produces a session. Daily
bars cannot establish an exact intraday execution point, and an undeclared
timing cannot be guessed at as before- or after-market -- both would assert
knowledge the source never gave. See :class:`AlignmentStatus` for the full
set of ways alignment can honestly fail.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final

from quantforge.models.enums import EarningsTiming

__all__ = ["AlignmentStatus", "SessionAlignment", "align_event_to_sessions"]


class AlignmentStatus(StrEnum):
    """How (or whether) an earnings event was mapped onto trading sessions.

    ``ALIGNED`` is the only status under which a caller may treat
    ``first_post_event_session`` as a usable research anchor. Every other
    member is a documented refusal, not a partial answer -- the point of this
    enum is that a caller can never mistake "we could not determine this" for
    a session that happens to be ``None`` by coincidence.
    """

    #: A first tradable session was determined. ``first_post_event_session``
    #: is populated; ``event_session`` may or may not be, depending on
    #: whether ``event_date`` itself was an available session.
    ALIGNED = "ALIGNED"

    #: The source declared no timing (``EarningsTiming.UNKNOWN``). Before- vs
    #: after-market cannot be guessed; no session is produced.
    UNKNOWN_TIMING = "UNKNOWN_TIMING"

    #: The source declared ``DURING_MARKET``. Daily bars cannot establish an
    #: exact intraday execution point, so no session is produced.
    INTRADAY_PRECISION_UNSUPPORTED = "INTRADAY_PRECISION_UNSUPPORTED"

    #: Timing was BEFORE_MARKET or AFTER_MARKET, but no qualifying session
    #: exists in the supplied ``available_sessions`` -- e.g. the listing has
    #: no price history yet, or the event is more recent than any stored bar.
    NO_SESSION_DATA = "NO_SESSION_DATA"

    #: Session mapping would otherwise have succeeded, but the research mode
    #: is ``POINT_IN_TIME`` and the observation carries no availability
    #: evidence (``observed_at IS NULL``). Never produced by
    #: :func:`align_event_to_sessions` itself -- a purely calendar-level
    #: calculation has no notion of research mode -- but set by
    #: :func:`~quantforge.research.alignment.align_earnings_event`, which
    #: withholds ``event_session``/``first_post_event_session`` in this case
    #: rather than expose dates a caller could mistake for tradable.
    NOT_POINT_IN_TIME_ELIGIBLE = "NOT_POINT_IN_TIME_ELIGIBLE"


@dataclass(frozen=True, slots=True)
class SessionAlignment:
    """The result of mapping one earnings event onto QuantForge's own sessions.

    ``event_session`` is purely descriptive -- "was ``event_date`` itself a
    session we have data for" -- and is ``None`` whenever it was not,
    regardless of whether ``first_post_event_session`` could still be
    determined (an after-market event dated to a non-trading day is a real
    case this is meant to represent honestly, not paper over).

    ``first_post_event_session`` is the field a later return calculation is
    meant to anchor to. It is ``None`` whenever ``status`` is not ``ALIGNED``.
    """

    event_session: date | None
    first_post_event_session: date | None
    status: AlignmentStatus
    reason: str


#: Sessions searched for in this module are always compared against this
#: invariant: a genuinely ordered, de-duplicated sequence. Re-sorting
#: defensively here costs nothing and means a caller's ordering bug cannot
#: silently corrupt the result, matching "ordered sessions, not calendar-day
#: arithmetic" even if a caller passes sessions out of order.
def _ordered_unique(sessions: list[date]) -> list[date]:
    return sorted(set(sessions))


_NO_SESSION_REASON: Final[str] = (
    "no available trading session satisfies this event's timing within the supplied session window"
)


def align_event_to_sessions(
    *,
    event_date: date,
    timing: EarningsTiming,
    available_sessions: list[date],
) -> SessionAlignment:
    """Map one earnings event onto the trading sessions QuantForge actually holds.

    Deliberately takes no ``event_timestamp``. Even when a source states one,
    it does not raise this function's precision beyond before/after-market
    framing, which is bounded by daily-bar granularity rather than by how
    exactly the event's moment is known -- and an exact instant implying a
    genuine intraday execution point is exactly the case ``DURING_MARKET``
    already refuses to resolve. The timestamp is preserved for provenance one
    layer up, on
    :class:`~quantforge.research.alignment.EventStudyAlignment`, rather than
    threaded through here unused.

    Args:
        event_date: The event's provider-stated calendar date.
        timing: The event's declared session phase. Never re-derived from
            an exact timestamp's clock hour -- that is a model-level
            invariant this function also respects by simply not accepting one.
        available_sessions: The real trading sessions QuantForge holds price
            data for, for the relevant listing. Must come from stored price
            observations (see :mod:`quantforge.research.alignment`), never
            from a generated calendar -- this function does not know what a
            "normal" trading day is and will not guess one.

    Returns:
        A :class:`SessionAlignment` describing what could, and could not, be
        determined.
    """
    sessions = _ordered_unique(available_sessions)
    event_session = event_date if event_date in sessions else None

    if timing is EarningsTiming.UNKNOWN:
        return SessionAlignment(
            event_session=None,
            first_post_event_session=None,
            status=AlignmentStatus.UNKNOWN_TIMING,
            reason="source declared no timing; before- vs after-market cannot be inferred",
        )

    if timing is EarningsTiming.DURING_MARKET:
        return SessionAlignment(
            event_session=None,
            first_post_event_session=None,
            status=AlignmentStatus.INTRADAY_PRECISION_UNSUPPORTED,
            reason=(
                "daily bars cannot establish an exact intraday execution point "
                "for a during-market event"
            ),
        )

    # Only BEFORE_MARKET and AFTER_MARKET reach here: UNKNOWN and
    # DURING_MARKET both returned above, and those four members are all of
    # EarningsTiming -- mypy's exhaustiveness check over the StrEnum confirms
    # there is no remaining case, so no `else` branch is reachable to guard.
    if timing is EarningsTiming.BEFORE_MARKET:
        # Information released before the open is tradable from the first
        # session at or after event_date: if event_date itself is a session,
        # that is the answer; if event_date falls on a gap (weekend/holiday/
        # provider misdate), the first real session after the gap is still
        # the first point the market could react, and guessing otherwise
        # would be the calendar-arithmetic mistake this module exists to
        # avoid.
        candidates = [s for s in sessions if s >= event_date]
    else:
        # EarningsTiming.AFTER_MARKET. Information released after the close
        # is never tradable using that same session's own close -- using it
        # would let a backtest "trade" on a price that was set before the
        # news existed -- so the search is strictly after event_date even
        # when event_date is itself a session.
        candidates = [s for s in sessions if s > event_date]

    if not candidates:
        return SessionAlignment(
            event_session=event_session,
            first_post_event_session=None,
            status=AlignmentStatus.NO_SESSION_DATA,
            reason=_NO_SESSION_REASON,
        )

    return SessionAlignment(
        event_session=event_session,
        first_post_event_session=candidates[0],
        status=AlignmentStatus.ALIGNED,
        reason="resolved from the ordered list of available trading sessions",
    )
