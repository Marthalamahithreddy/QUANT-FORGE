"""Public entry point for Step 8.4: forward returns anchored to Step 8.2 alignment.

:func:`compute_event_forward_returns` composes three pieces, each owned by its
own module exactly as :func:`quantforge.research.alignment.align_earnings_event`
does for Step 8.1/8.2:

1. The :class:`~quantforge.research.alignment.EventStudyAlignment` the caller
   already computed (not re-computed here -- see the module docstring on
   why outcome data must never feed back into alignment).
2. :func:`quantforge.research.price_series.fetch_coherent_price_series` --
   the one database read, resolving a single vendor's close series for the
   window this calculation needs.
3. :func:`quantforge.research.returns.compute_forward_returns` -- pure
   close-to-close arithmetic over that series.

Outcomes are not inputs
------------------------
A forward return's target price necessarily exists *after* the event -- that
is the entire point of measuring an aftermath. This is fine, and is not a
look-ahead violation: nothing here feeds a future price back into event
selection, revision selection, or alignment, each of which is computed from
information available at or before the event, independently of this module.
Keeping this one-directional (alignment -> returns, never returns -> alignment)
is what keeps "did the market react" a separate question from "what could
have been known".
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date, timedelta

from sqlalchemy.orm import Session

from quantforge.market.sessions import SessionCalendar
from quantforge.research.alignment import EventStudyAlignment, resolve_session_calendar
from quantforge.research.calendar import AlignmentStatus, proven_sessions_after
from quantforge.research.price_series import PriceSeriesStatus, fetch_coherent_price_series
from quantforge.research.returns import (
    EventForwardReturns,
    ForwardReturn,
    ForwardReturnStatus,
    compute_forward_returns,
    no_alignment_forward_returns,
    validate_horizons,
)

__all__ = ["DEFAULT_HORIZONS", "compute_event_forward_returns", "forward_price_window_end"]

#: The horizons Step 8.4 is required to support.
DEFAULT_HORIZONS: tuple[int, ...] = (1, 5, 20)

#: Calendar-day padding applied per requested trading session when sizing the
#: price-series query window. Three calendar days per session comfortably
#: covers a five-day trading week (5 sessions / 7 calendar days ~= 1.4) plus
#: routine holidays, without the window being unbounded. This is a query-sizing
#: heuristic only: targets are the calendar's PROVEN sessions, looked up by
#: exact date, and a proven target outside the window simply has no price.
_CALENDAR_DAYS_PER_SESSION_BUFFER = 3

#: Minimum window padding regardless of horizon, so even horizon=1 searches
#: across a real holiday weekend rather than a window of only a few days.
_MIN_WINDOW_PADDING_DAYS = 14


def _window_end(anchor_session: date, horizons: Sequence[int]) -> date:
    max_horizon = max(horizons)
    padding_days = max(max_horizon * _CALENDAR_DAYS_PER_SESSION_BUFFER, _MIN_WINDOW_PADDING_DAYS)
    return anchor_session + timedelta(days=padding_days)


def forward_price_window_end(anchor_session: date, horizons: Sequence[int]) -> date:
    """The last calendar date the forward-return price query reads (public view of the window)."""
    return _window_end(anchor_session, horizons)


def _all_horizons_with_status(
    *,
    anchor_session: date | None,
    horizons: Sequence[int],
    status: ForwardReturnStatus,
    reason: str,
) -> list[ForwardReturn]:
    return [
        ForwardReturn(
            horizon=horizon,
            anchor_session=anchor_session,
            anchor_close=None,
            target_session=None,
            target_close=None,
            return_value=None,
            status=status,
            reason=reason,
        )
        for horizon in horizons
    ]


def compute_event_forward_returns(
    session: Session,
    alignment: EventStudyAlignment,
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    source_id: uuid.UUID | None = None,
    calendar: SessionCalendar | None = None,
) -> EventForwardReturns:
    """Compute forward returns for one already-aligned earnings event.

    Args:
        session: Database session to read price bars from.
        alignment: The :class:`~quantforge.research.alignment.EventStudyAlignment`
            already computed by
            :func:`quantforge.research.alignment.align_earnings_event` for
            this event. Not recomputed here -- this function trusts the
            anchor session and listing it was already given, which is what
            keeps alignment and returns independently testable and prevents
            a future price from ever influencing which listing or session was
            chosen.
        horizons: Number of trading sessions after the anchor to measure to.
            Every element produces exactly one result, regardless of outcome.
        source_id: Restrict to one price source, bypassing the coherent-series
            ambiguity check. See
            :func:`quantforge.research.price_series.fetch_coherent_price_series`.
        calendar: The exchange session calendar the targets are proven from
            (default: the shared adapter). Horizon ``N`` means the N-th
            PROVEN exchange session after the anchor, never the N-th stored bar.

    Returns:
        An :class:`~quantforge.research.returns.EventForwardReturns` with one
        :class:`~quantforge.research.returns.ForwardReturn` per requested
        horizon.
    """
    validate_horizons(horizons)
    horizons_list = list(horizons)
    listing_id = alignment.listing_resolution.listing_id
    session_alignment = alignment.session_alignment

    if (
        session_alignment is None
        or session_alignment.status is not AlignmentStatus.ALIGNED
        or session_alignment.first_post_event_session is None
    ):
        # Covers every refusal Step 8.1/8.2 can produce: no listing resolved,
        # an ambiguous listing, UNKNOWN/DURING_MARKET timing, no session data,
        # or a point-in-time-ineligible observation. None of these are a
        # reason to guess an anchor.
        return no_alignment_forward_returns(
            event_id=alignment.event_id, listing_id=listing_id, horizons=horizons_list
        )

    anchor_session = session_alignment.first_post_event_session
    assert listing_id is not None  # a non-None session_alignment implies a RESOLVED listing
    mic = alignment.session_calendar_mic
    if mic is None:
        # An ALIGNED anchor is always proven from one venue's calendar. An alignment that names
        # none (built by hand, or from before the field existed) cannot prove session targets, so
        # it is refused like any other missing alignment rather than priced from stored dates.
        return no_alignment_forward_returns(
            event_id=alignment.event_id, listing_id=listing_id, horizons=horizons_list
        )

    window_end = _window_end(anchor_session, horizons_list)
    price_resolution = fetch_coherent_price_series(
        session,
        listing_id=listing_id,
        start=anchor_session,
        end=window_end,
        source_id=source_id,
    )

    if price_resolution.status is PriceSeriesStatus.NO_PRICE_DATA:
        forward_returns = _all_horizons_with_status(
            anchor_session=anchor_session,
            horizons=horizons_list,
            status=ForwardReturnStatus.MISSING_ANCHOR_PRICE,
            reason=price_resolution.reason,
        )
    elif price_resolution.status is PriceSeriesStatus.AMBIGUOUS_PRICE_SOURCE:
        forward_returns = _all_horizons_with_status(
            anchor_session=anchor_session,
            horizons=horizons_list,
            status=ForwardReturnStatus.AMBIGUOUS_PRICE_SOURCE,
            reason=price_resolution.reason,
        )
    else:
        proven = proven_sessions_after(
            calendar=resolve_session_calendar(calendar),
            mic=mic,
            anchor_session=anchor_session,
            count=max(horizons_list),
        )
        forward_returns = compute_forward_returns(
            anchor_session=anchor_session,
            price_series=price_resolution.series,
            horizons=horizons_list,
            forward_sessions=proven.sessions,
            forward_sessions_reason=proven.reason if not proven.complete else "",
        )

    return EventForwardReturns(
        event_id=alignment.event_id,
        listing_id=listing_id,
        price_source_id=price_resolution.source_id,
        anchor_session=anchor_session,
        forward_returns=forward_returns,
    )
