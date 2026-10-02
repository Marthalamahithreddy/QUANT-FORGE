"""The public entry point for event-study alignment: wires DB resolution to pure calculation.

:func:`align_earnings_event` is the one function this milestone's research
layer expects callers to use. It does three things, each delegated to the
module that owns it:

1. Resolve the event's issuer to a listing as of ``event_date``
   (:mod:`quantforge.research.listing_resolution` -- the database read).
2. Fetch the trading sessions QuantForge actually holds price data for around
   that date (this module, a thin query -- see :func:`_available_sessions`).
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
from datetime import date, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.models.earnings import EarningsEvent
from quantforge.models.enums import EarningsTiming
from quantforge.models.market import DailyPriceBar
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

__all__ = ["EventStudyAlignment", "align_earnings_event"]

#: Default window, in calendar days, searched on either side of ``event_date``
#: for available trading sessions. Generous enough to span a long holiday
#: weekend or a short gap in ingested history while staying a small, bounded
#: query -- this is a per-event lookup, not a full-history scan.
_DEFAULT_WINDOW_DAYS = 10


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


def _available_sessions(
    session: Session,
    *,
    listing_id: uuid.UUID,
    start: date,
    end: date,
) -> list[date]:
    """Return the distinct, ordered trading sessions held for a listing in a window.

    Sourced directly from :class:`~quantforge.models.market.DailyPriceBar`,
    per the Step 8 design note to prefer the price history QuantForge actually
    has over introducing an exchange-calendar dependency: "available session"
    here means "a session we have a price observation for", not "a session the
    venue was open", which is the conservative reading this milestone commits
    to.
    """
    statement = (
        sa.select(DailyPriceBar.trading_date)
        .where(
            DailyPriceBar.listing_id == listing_id,
            DailyPriceBar.trading_date >= start,
            DailyPriceBar.trading_date <= end,
        )
        .distinct()
        .order_by(DailyPriceBar.trading_date)
    )
    return list(session.scalars(statement))


def align_earnings_event(
    session: Session,
    event: EarningsEvent,
    *,
    mode: ResearchMode,
    security_id: uuid.UUID | None = None,
    window_days: int = _DEFAULT_WINDOW_DAYS,
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
        window_days: How many calendar days on either side of ``event_date``
            to search for available trading sessions. The search is still
            over actual stored sessions, never calendar-day arithmetic; this
            only bounds the query.

    Returns:
        The computed :class:`EventStudyAlignment`.
    """
    timing = event.timing
    eligibility = classify_point_in_time_eligibility(mode, observed_at=event.observed_at)
    listing_resolution = resolve_company_listing_for_date(
        session,
        company_id=event.company_id,
        on_date=event.event_date,
        security_id=security_id,
    )

    session_alignment: SessionAlignment | None = None
    if listing_resolution.status is ListingResolutionStatus.RESOLVED:
        assert listing_resolution.listing_id is not None  # guaranteed by RESOLVED, for mypy
        window_start = event.event_date - timedelta(days=window_days)
        window_end = event.event_date + timedelta(days=window_days)
        available_sessions = _available_sessions(
            session,
            listing_id=listing_resolution.listing_id,
            start=window_start,
            end=window_end,
        )
        session_alignment = align_event_to_sessions(
            event_date=event.event_date,
            timing=timing,
            available_sessions=available_sessions,
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
                    "observed_at is NULL and no other availability evidence exists; "
                    "a point-in-time tradability claim cannot be supported"
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
    )
