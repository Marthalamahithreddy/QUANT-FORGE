"""Execution eligibility: when could a generated signal defensibly execute?

:func:`assess_execution_eligibility` takes one already-generated
:class:`~quantforge.signals.eps_surprise.EpsSurpriseSignal` plus an injected
:class:`~quantforge.market.SessionCalendar` and returns whether the signal was
provably known strictly before a real session-open instant. It performs no
database access, reads no price, and neither imports nor loads
:mod:`quantforge.signals.evaluation`, :mod:`quantforge.signals.analytics`, or
the return / benchmark / abnormal-return / event-study research modules. Two
tests enforce it: an AST scan of every file in this package (direct imports,
including relative ones and dynamic-import calls) and a clean-interpreter
``sys.modules`` check (``tests/test_module_isolation.py``), which also covers
what package ``__init__`` files pull in -- ``quantforge.research`` and
``quantforge.signals`` export lazily for exactly that reason. It does not import
``exchange_calendars`` or pandas either: the calendar arrives as a protocol.

Research alignment is not execution
------------------------------------
Step 8/9's ``effective_session`` answers "which session does this event's
aftermath attach to?" and says nothing about when the information was public.
Execution asks a stricter question -- was it public before the candidate
instant? -- which needs two real instants. ``observed_at`` (carried on the
signal straight from the selected earnings observation, never ``ingested_at``)
is compared against a session open supplied by the calendar. The timing label
(``BEFORE_MARKET``/``AFTER_MARKET``) only chooses *which* session to ask about;
it is never proof of availability.

Rules (after the signal is POINT_IN_TIME, ELIGIBLE, AVAILABLE)
---------------------------------------------------------------
* ``BEFORE_MARKET``: the session is the one on ``event_date``. If
  ``observed_at < session.open_at`` (strict) the candidate is
  ``SAME_SESSION_OPEN`` on that session. Otherwise -- including
  ``observed_at == open_at`` -- ``INFORMATION_NOT_KNOWN_BEFORE_OPEN``.
  If ``event_date`` is not a session, ``NOT_A_TRADING_SESSION``.
* ``AFTER_MARKET``: the session is the first session strictly after
  ``event_date`` per the calendar (never calendar-day arithmetic). If
  ``observed_at < next.open_at`` (strict) the candidate is
  ``NEXT_SESSION_OPEN``; otherwise ``INFORMATION_NOT_KNOWN_BEFORE_OPEN``. No
  prior-close precondition is imposed, and no later session is searched.
* ``DURING_MARKET``: refused (``INSUFFICIENT_TEMPORAL_PRECISION``); daily bars
  give no deterministic intraday execution point.
* ``UNKNOWN`` timing: refused (``UNSUPPORTED_TIMING``); timing is not inferred
  from ``observed_at``.

Session identity comes from the signal's ``exchange_mic`` -- the MIC of the
listing historically resolved for the event date -- never a current listing, a
ticker lookup, or a default venue.

``observed_at`` is trusted as a real instant: a source that stored only a date
(midnight) would satisfy ``observed_at < open`` without proving intraday
availability, so a provider mapper must supply genuine instants or leave it
NULL (as Finnhub's does today, which keeps every such signal ineligible).

A candidate is not a fill. No price is read here, and Massive's split-adjusted
bars are not claimed suitable for execution accounting (see HANDOFF's
price-basis note); a later slice must settle the price basis before any
numeric fill exists. ``direction`` is carried through and never mapped to
BUY/SELL.
"""

from __future__ import annotations

from datetime import datetime

from quantforge.backtest.contracts import (
    ExecutionEligibility,
    ExecutionEligibilityStatus,
    ExecutionPriceType,
)
from quantforge.market import MarketSession, SessionCalendar, SessionLookup, SessionLookupStatus
from quantforge.models.enums import EarningsTiming
from quantforge.research.availability import (
    PointInTimeEligibility,
    ResearchMode,
    is_point_in_time_availability_eligible,
)
from quantforge.signals.eps_surprise import EpsSurpriseSignal, EpsSurpriseSignalStatus

__all__ = ["assess_execution_eligibility"]

_LOOKUP_REFUSALS: dict[SessionLookupStatus, ExecutionEligibilityStatus] = {
    SessionLookupStatus.UNSUPPORTED_MIC: ExecutionEligibilityStatus.UNSUPPORTED_MIC,
    SessionLookupStatus.NOT_A_SESSION: ExecutionEligibilityStatus.NOT_A_TRADING_SESSION,
    SessionLookupStatus.OUT_OF_CALENDAR_RANGE: ExecutionEligibilityStatus.SESSION_CLOCK_UNAVAILABLE,
}


def _result(
    signal: EpsSurpriseSignal,
    status: ExecutionEligibilityStatus,
    reason: str,
    *,
    session: MarketSession | None = None,
    price_type: ExecutionPriceType | None = None,
) -> ExecutionEligibility:
    return ExecutionEligibility(
        event_id=signal.event_id,
        company_id=signal.company_id,
        security_id=signal.security_id,
        listing_id=signal.listing_id,
        exchange_mic=signal.exchange_mic,
        signal_type=signal.signal_type,
        direction=signal.direction,
        signal_value=signal.signal_value,
        research_mode=signal.research_mode,
        point_in_time_eligibility=signal.point_in_time_eligibility,
        observed_at=signal.observed_at,
        observed_at_basis=signal.observed_at_basis,
        status=status,
        candidate_execution_session=session.trading_date if session else None,
        candidate_execution_price_type=price_type,
        candidate_execution_open_at=session.open_at if session else None,
        reason=reason,
    )


def _assess_against_open(
    signal: EpsSurpriseSignal,
    lookup: SessionLookup,
    price_type: ExecutionPriceType,
    observed_at: datetime,
) -> ExecutionEligibility:
    if lookup.session is None:
        return _result(signal, _LOOKUP_REFUSALS[lookup.status], lookup.reason)
    session = lookup.session
    if observed_at < session.open_at:
        return _result(
            signal,
            ExecutionEligibilityStatus.CANDIDATE_EXECUTION_ELIGIBLE,
            f"observed_at {observed_at.isoformat()} is strictly before the "
            f"{session.trading_date} session open {session.open_at.isoformat()}",
            session=session,
            price_type=price_type,
        )
    return _result(
        signal,
        ExecutionEligibilityStatus.INFORMATION_NOT_KNOWN_BEFORE_OPEN,
        f"observed_at {observed_at.isoformat()} is not strictly before the "
        f"{session.trading_date} session open {session.open_at.isoformat()}",
    )


def _signal_refusal(signal: EpsSurpriseSignal) -> ExecutionEligibility | None:
    """Refusals decidable from the signal's classification alone, before any clock is consulted."""
    if signal.research_mode is not ResearchMode.POINT_IN_TIME:
        return _result(
            signal,
            ExecutionEligibilityStatus.NOT_POINT_IN_TIME_SIGNAL,
            "a DESCRIPTIVE signal may be researched and evaluated but must never "
            "become an executable decision",
        )
    if signal.point_in_time_eligibility is not PointInTimeEligibility.ELIGIBLE:
        return _result(
            signal,
            ExecutionEligibilityStatus.NOT_POINT_IN_TIME_ELIGIBLE,
            "no approved availability evidence exists (observed_at is NULL, or its basis "
            "is missing or not approved); a point-in-time tradability claim cannot be "
            "supported",
        )
    if signal.timing is EarningsTiming.UNKNOWN:
        return _result(
            signal,
            ExecutionEligibilityStatus.UNSUPPORTED_TIMING,
            "the source declared no timing; before- vs after-market is not inferred",
        )
    if signal.timing is EarningsTiming.DURING_MARKET:
        return _result(
            signal,
            ExecutionEligibilityStatus.INSUFFICIENT_TEMPORAL_PRECISION,
            "daily bars cannot establish an exact intraday execution point for a "
            "during-market event",
        )
    if signal.status is not EpsSurpriseSignalStatus.AVAILABLE:
        return _result(
            signal,
            ExecutionEligibilityStatus.SIGNAL_UNAVAILABLE,
            f"the EPS-surprise signal itself is unavailable ({signal.status.value}); "
            "there is no direction or signal_value to assess for execution",
        )
    return None


def assess_execution_eligibility(
    signal: EpsSurpriseSignal, calendar: SessionCalendar
) -> ExecutionEligibility:
    """Assess whether one signal has a defensible candidate execution instant.

    Args:
        signal: An already-generated signal. Its ``observed_at`` and
            ``exchange_mic`` are the only availability/venue evidence used.
        calendar: The session clock to resolve opens from.

    Returns:
        The :class:`~quantforge.backtest.contracts.ExecutionEligibility`.
        Ordinary refusals are statuses, not exceptions.

    Raises:
        ValueError: ``signal.observed_at`` is a naive datetime -- a programming
            error, since an instant cannot be compared to a session open without
            a zone. (Aware datetimes compare by instant, whatever their offset.)
    """
    refusal = _signal_refusal(signal)
    if refusal is not None:
        return refusal

    # Defensive: an ELIGIBLE signal always carries approved availability
    # evidence. If a malformed one does not (no timestamp, or a basis that is
    # missing or not approved), refuse rather than compare against nothing.
    observed_at = signal.observed_at
    if observed_at is None or not is_point_in_time_availability_eligible(
        observed_at, signal.observed_at_basis
    ):
        return _result(
            signal,
            ExecutionEligibilityStatus.NOT_POINT_IN_TIME_ELIGIBLE,
            "the signal carries no approved availability evidence; availability cannot be "
            "established",
        )
    if observed_at.tzinfo is None:
        message = f"signal.observed_at must be timezone-aware, got naive {observed_at!r}"
        raise ValueError(message)

    mic = signal.exchange_mic
    if mic is None:
        return _result(
            signal,
            ExecutionEligibilityStatus.UNSUPPORTED_MIC,
            "no historically resolved exchange MIC is attached to the signal",
        )

    # Only BEFORE_MARKET / AFTER_MARKET remain.
    if signal.timing is EarningsTiming.BEFORE_MARKET:
        return _assess_against_open(
            signal,
            calendar.session_for(mic, signal.event_date),
            ExecutionPriceType.SAME_SESSION_OPEN,
            observed_at,
        )
    return _assess_against_open(
        signal,
        calendar.next_session(mic, signal.event_date),
        ExecutionPriceType.NEXT_SESSION_OPEN,
        observed_at,
    )
