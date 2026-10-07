"""The public entry point for event-study alignment: wires DB resolution to pure calculation.

:func:`align_earnings_event` is the one function this milestone's research
layer expects callers to use. It does three things, each delegated to the
module that owns it:

1. Resolve the event's issuer to a listing as of ``event_date``
   (:mod:`quantforge.research.listing_resolution` -- the database read).
2. Look up the venue of the resolved listing (this module, a thin query -- see
   :func:`_listing_exchange_mic`) so the exchange calendar's session sequence
   for THAT venue is used. Stored price bars no longer define sessions (Step
   11.2E): an absent bar is a missing price, not a non-trading day.
3. Compute the session alignment and the point-in-time eligibility
   (:mod:`quantforge.research.calendar` and
   :mod:`quantforge.research.availability` -- both pure).

Nothing here is persisted. :class:`EventStudyAlignment` is a computed research
object, rebuilt on demand from the fact tables rather than stored: the
methodology for mapping an event to a session is exactly the kind of decision
that should stay re-runnable rather than frozen into a row, for the same
reason ``earnings_event`` itself stores no surprise or return figures (see
:mod:`quantforge.models.earnings`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.market.sessions import SessionCalendar
from quantforge.models.earnings import EarningsEvent
from quantforge.models.enums import EarningsTiming
from quantforge.models.security import Exchange, Listing
from quantforge.research.availability import (
    PointInTimeEligibility,
    ResearchMode,
    classify_point_in_time_eligibility,
)
from quantforge.research.calendar import AlignmentStatus, SessionAlignment, align_event_to_sessions
from quantforge.research.listing_resolution import (
    ListingResolution,
    ListingResolutionStatus,
    resolve_company_listing_for_date,
)

__all__ = [
    "ALIGNMENT_WINDOW_DAYS",
    "EventStudyAlignment",
    "align_earnings_event",
    "resolve_session_calendar",
]

#: The calendar-day half-width within which stored sessions are pinned around an event date by
#: the reproducibility manifest (the digest of stored session dates read). Alignment itself no
#: longer reads stored sessions: the anchor comes from the exchange calendar. Public so
#: reproducibility can pin exactly this window.
ALIGNMENT_WINDOW_DAYS = 10


@dataclass(frozen=True, slots=True)
class EventStudyAlignment:
    """Provider-independent description of how one earnings event aligns to sessions.

    Carries the event's own identity and facts (never re-derived or
    re-interpreted) alongside the two computed outcomes: whether a listing
    could be resolved, and -- only if it could -- whether and how it aligns to
    a trading session, and whether it is eligible to be treated as
    point-in-time tradable.

    ``session_alignment`` is ``None`` exactly when ``listing_resolution.status``
    is not ``RESOLVED``: there is no listing to compute price-session
    alignment against, so no attempt is made rather than reporting a
    misleading "not aligned" for a different reason.
    """

    event_id: uuid.UUID
    event_fingerprint: str
    company_id: uuid.UUID
    event_date: date
    event_timestamp: datetime | None
    timing: EarningsTiming
    research_mode: ResearchMode
    point_in_time_eligibility: PointInTimeEligibility
    listing_resolution: ListingResolution
    session_alignment: SessionAlignment | None
    #: The venue whose exchange-calendar session sequence was used: the stored listing's
    #: exchange (the venue of the bars that are read). ``None`` when no listing resolved. It
    #: says nothing about where the issuer traded on the event date -- venue history is not
    #: established, and the resolver's own ``exchange_mic`` stays ``None`` when it is not.
    session_calendar_mic: str | None = None


def resolve_session_calendar(calendar: SessionCalendar | None) -> SessionCalendar:
    """The given calendar, or the shared ``exchange_calendars`` adapter built on first use.

    The import is deliberately local: ``quantforge.market`` exports the adapter lazily, and
    importing it here at module level would load ``exchange_calendars`` and pandas whenever
    research is imported (``tests/test_module_isolation.py`` forbids that).
    """
    if calendar is not None:
        return calendar
    from quantforge.market import default_session_calendar  # noqa: PLC0415 - lazy by design

    return default_session_calendar()


def _listing_exchange_mic(session: Session, *, listing_id: uuid.UUID) -> str | None:
    """The exchange MIC of the stored listing whose bars are read (one small query).

    The bars of a listing were ingested for that listing's venue, so its session sequence is
    the matching one. For the supported US venues (XNYS/XNAS/ARCX) the calendar is the same
    US-equity sequence; any other MIC is refused by the calendar adapter, never defaulted.
    """
    return session.scalar(
        sa.select(Exchange.mic)
        .join(Listing, Listing.exchange_id == Exchange.id)
        .where(Listing.id == listing_id)
    )


def align_earnings_event(
    session: Session,
    event: EarningsEvent,
    *,
    mode: ResearchMode,
    security_id: uuid.UUID | None = None,
    calendar: SessionCalendar | None = None,
) -> EventStudyAlignment:
    """Align one earnings event to QuantForge's own trading sessions.

    Args:
        session: Database session to read listings and price bars from.
        event: The earnings event to align. Read-only -- nothing is written
            back onto it or anywhere else.
        mode: Which research question is being asked (see
            :class:`~quantforge.research.availability.ResearchMode`). Session
            alignment itself is computed the same way regardless of mode --
            "which session does this event map to" is a calendar fact, not an
            epistemic claim -- but in ``POINT_IN_TIME`` mode, a result that is
            not eligible has its session fields withheld rather than exposed
            as if they were safe to trade on.
        security_id: Passed straight through to
            :func:`~quantforge.research.listing_resolution.resolve_company_listing_for_date`
            to disambiguate a multi-security issuer.
        calendar: The exchange session calendar. Defaults to the shared
            ``exchange_calendars``-backed adapter (built lazily, so importing
            this module loads no calendar library). Tests inject a fake.

    Returns:
        The computed :class:`EventStudyAlignment`.
    """
    timing = event.timing
    eligibility = classify_point_in_time_eligibility(
        mode, observed_at=event.observed_at, observed_at_basis=event.observed_at_basis
    )
    listing_resolution = resolve_company_listing_for_date(
        session,
        company_id=event.company_id,
        on_date=event.event_date,
        security_id=security_id,
    )

    session_alignment: SessionAlignment | None = None
    session_calendar_mic: str | None = None
    if listing_resolution.status is ListingResolutionStatus.RESOLVED:
        assert listing_resolution.listing_id is not None  # guaranteed by RESOLVED, for mypy
        session_calendar_mic = _listing_exchange_mic(
            session, listing_id=listing_resolution.listing_id
        )
        if session_calendar_mic is None:
            session_alignment = SessionAlignment(
                event_session=None,
                first_post_event_session=None,
                status=AlignmentStatus.NO_SESSION_DATA,
                reason="the resolved listing has no exchange, so no session calendar applies",
            )
        else:
            session_alignment = align_event_to_sessions(
                event_date=event.event_date,
                timing=timing,
                calendar=resolve_session_calendar(calendar),
                mic=session_calendar_mic,
            )

        if (
            mode is ResearchMode.POINT_IN_TIME
            and eligibility is not PointInTimeEligibility.ELIGIBLE
        ):
            # The event's timing/session mapping may well be ALIGNED -- that
            # is a calendar fact and does not depend on observed_at -- but
            # POINT_IN_TIME mode is a stronger claim than "this session
            # exists": it asserts the information was knowable by then. With
            # no availability evidence, withholding the session fields (not
            # just appending a caveat) is what makes it structurally
            # impossible for a caller to use them as if the claim held.
            session_alignment = SessionAlignment(
                event_session=None,
                first_post_event_session=None,
                status=AlignmentStatus.NOT_POINT_IN_TIME_ELIGIBLE,
                reason=(
                    "no approved availability evidence exists (observed_at is NULL, or "
                    "its basis is missing or not approved); a point-in-time tradability "
                    "claim cannot be supported"
                ),
            )

    return EventStudyAlignment(
        event_id=event.id,
        event_fingerprint=event.fingerprint,
        company_id=event.company_id,
        event_date=event.event_date,
        event_timestamp=event.event_timestamp,
        timing=timing,
        research_mode=mode,
        point_in_time_eligibility=eligibility,
        listing_resolution=listing_resolution,
        session_alignment=session_alignment,
        session_calendar_mic=session_calendar_mic,
    )
