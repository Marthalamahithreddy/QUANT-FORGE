"""Deterministic earnings-event-to-trading-session alignment.

This module answers one narrow question: given an earnings event's declared
``event_date``/``timing``, which exchange trading session does the event belong
to, and which is the first session the market could have reacted in?

The session sequence comes from an authoritative exchange calendar
(:class:`~quantforge.market.sessions.SessionCalendar`, backed by
``exchange_calendars`` in production), NOT from whichever price bars happen to
be stored. That distinction is the whole point of Step 11.2E: a stored bar
being absent is a MISSING PRICE, never a non-trading day, and must never move
an anchor or a horizon target. A weekend or exchange holiday is a real
non-session because the calendar says so; this module never counts weekdays,
never hard-codes holidays and never adds days.

Everything here is a pure function over plain values and a calendar object --
no database session, no ORM, no I/O.

Conservatism over precision
----------------------------
A ``DURING_MARKET`` or ``UNKNOWN`` timing never produces a session. Daily
bars cannot establish an exact intraday execution point, and an undeclared
timing cannot be guessed at as before- or after-market. A venue the calendar
does not support, or a date beyond the calendar's range, is
``NO_SESSION_DATA``: the session sequence is unproven, so nothing is guessed.
See :class:`AlignmentStatus` for the full set of ways alignment can honestly fail.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from quantforge.market.sessions import SessionCalendar, SessionLookupStatus
from quantforge.models.enums import EarningsTiming

__all__ = [
    "AlignmentStatus",
    "ProvenSessions",
    "SessionAlignment",
    "align_event_to_sessions",
    "proven_sessions_after",
]


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

    #: Timing was BEFORE_MARKET or AFTER_MARKET, but the exchange calendar
    #: cannot PROVE a qualifying session: the venue is not one the calendar
    #: supports, or the date lies outside the calendar's range. (Before
    #: Step 11.2E this meant "no stored bar nearby"; a missing stored bar is
    #: now a missing price, reported by the returns, never an alignment
    #: failure and never a shifted anchor.)
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

    ``event_session`` is purely descriptive -- "was ``event_date`` itself an
    exchange trading session" -- and is ``None`` whenever it was not,
    regardless of whether ``first_post_event_session`` could still be
    determined (an after-market event dated to a non-trading day is a real
    case this is meant to represent honestly, not paper over). It says nothing
    about whether a price bar is stored for that date.

    ``first_post_event_session`` is the field a later return calculation is
    meant to anchor to. It is ``None`` whenever ``status`` is not ``ALIGNED``.
    """

    event_session: date | None
    first_post_event_session: date | None
    status: AlignmentStatus
    reason: str


def _lookup_reason(status: SessionLookupStatus, detail: str) -> str:
    return f"the exchange calendar cannot prove a session ({status.value}): {detail}"


def align_event_to_sessions(
    *,
    event_date: date,
    timing: EarningsTiming,
    calendar: SessionCalendar,
    mic: str,
) -> SessionAlignment:
    """Map one earnings event onto the venue's trading sessions, per the exchange calendar.

    Deliberately takes no ``event_timestamp``. Even when a source states one,
    it does not raise this function's precision beyond before/after-market
    framing, which is bounded by daily-bar granularity rather than by how
    exactly the event's moment is known -- and an exact instant implying a
    genuine intraday execution point is exactly the case ``DURING_MARKET``
    already refuses to resolve. The timestamp is preserved for provenance one
    layer up, on
    :class:`~quantforge.research.alignment.EventStudyAlignment`.

    Args:
        event_date: The event's provider-stated calendar date.
        timing: The event's declared session phase. Never re-derived from
            an exact timestamp's clock hour.
        calendar: The session calendar. The anchor is chosen from IT, never
            from stored price bars: a missing bar cannot choose an anchor.
        mic: The venue whose session sequence is used (see
            :func:`quantforge.research.alignment.align_earnings_event` for how
            it is chosen). An unsupported venue is ``NO_SESSION_DATA``.

    Returns:
        A :class:`SessionAlignment` describing what could, and could not, be
        determined.
    """
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

    # Only BEFORE_MARKET and AFTER_MARKET reach here.
    on_event_date = calendar.session_for(mic, event_date)
    if on_event_date.status is SessionLookupStatus.AVAILABLE:
        event_session: date | None = event_date
    elif on_event_date.status is SessionLookupStatus.NOT_A_SESSION:
        event_session = None
    else:
        return SessionAlignment(
            event_session=None,
            first_post_event_session=None,
            status=AlignmentStatus.NO_SESSION_DATA,
            reason=_lookup_reason(on_event_date.status, on_event_date.reason),
        )

    if timing is EarningsTiming.BEFORE_MARKET and event_session is not None:
        # Information released before the open is tradable from the first
        # session at or after event_date: event_date itself, when it is a session.
        return SessionAlignment(
            event_session=event_session,
            first_post_event_session=event_session,
            status=AlignmentStatus.ALIGNED,
            reason="event date is an exchange session; before-market information is tradable in it",
        )

    # BEFORE_MARKET on a non-session date, or AFTER_MARKET (always strictly
    # after event_date, even when event_date is itself a session: using that
    # session's own close would trade on a price set before the news existed).
    following = calendar.next_session(mic, event_date)
    if following.status is not SessionLookupStatus.AVAILABLE or following.session is None:
        return SessionAlignment(
            event_session=event_session,
            first_post_event_session=None,
            status=AlignmentStatus.NO_SESSION_DATA,
            reason=_lookup_reason(following.status, following.reason),
        )
    return SessionAlignment(
        event_session=event_session,
        first_post_event_session=following.session.trading_date,
        status=AlignmentStatus.ALIGNED,
        reason="first exchange session strictly after the event date, from the exchange calendar",
    )


@dataclass(frozen=True, slots=True)
class ProvenSessions:
    """The exchange sessions proven to follow an anchor, in order.

    ``sessions[N-1]`` is the N-th proven session strictly after the anchor.
    ``sessions`` may be shorter than requested when the calendar cannot prove
    more (``complete`` is then False and ``reason`` says why); it is never
    padded, never filled from stored bars.
    """

    sessions: tuple[date, ...]
    complete: bool
    reason: str


def proven_sessions_after(
    *, calendar: SessionCalendar, mic: str, anchor_session: date, count: int
) -> ProvenSessions:
    """The next ``count`` exchange sessions strictly after ``anchor_session``, from the calendar.

    One calendar lookup per session (no database access). Stops early, honestly,
    at the first lookup the calendar cannot answer.
    """
    if count < 1:
        message = f"count must be a positive number of sessions, got {count!r}"
        raise ValueError(message)
    found: list[date] = []
    cursor = anchor_session
    for _ in range(count):
        lookup = calendar.next_session(mic, cursor)
        if lookup.status is not SessionLookupStatus.AVAILABLE or lookup.session is None:
            return ProvenSessions(
                sessions=tuple(found),
                complete=False,
                reason=_lookup_reason(lookup.status, lookup.reason),
            )
        cursor = lookup.session.trading_date
        found.append(cursor)
    return ProvenSessions(
        sessions=tuple(found), complete=True, reason="every session is from the exchange calendar"
    )
