"""EPS-surprise signal: the first Step 9 signal family.

A sign-only classification of EPS surprise (:class:`EpsSurpriseDirection`),
gated by exactly the point-in-time and session-alignment rules Step 8 already
defines -- this module invents no new timing model and no new revision-
selection policy. Every number here is produced by an existing Step 8
function:

* :func:`~quantforge.research.alignment.align_earnings_event` resolves the
  listing and the session this event maps to.
* :func:`~quantforge.research.eps_surprise.compute_eps_surprise_for_observation`
  computes the surprise figures, already gated by point-in-time eligibility.

This module only adds the translation from those results to a signal
contract, and the sign-only classification rule itself.

Effective session, and the limits of what can be proven about it
------------------------------------------------------------------
For ``BEFORE_MARKET``/``AFTER_MARKET`` timing, the signal's
``effective_session`` is exactly
:attr:`~quantforge.research.calendar.SessionAlignment.first_post_event_session`
as Step 8 already computes it: for ``BEFORE_MARKET`` this may be the event's
own session; for ``AFTER_MARKET`` the search is strictly after the event date,
so it is never earlier than the next session. ``DURING_MARKET`` and
``UNKNOWN`` never produce an effective session (see
:class:`~quantforge.research.calendar.AlignmentStatus`).

An event being dated ``BEFORE_MARKET`` does not, by itself, prove the
information was actually available before that session's open -- but this
codebase deliberately has no market-calendar/session-clock contract (see
:mod:`quantforge.research.calendar`'s own "do not fabricate a clock time"
discipline), so there is no session-open instant to compare ``observed_at``
against, and inventing one here would be exactly the fabrication Step 8
already refused. The only availability evidence this schema carries is
``observed_at`` itself, and the only question it can honestly answer is
whether that evidence exists at all -- which is precisely what
``PointInTimeEligibility`` already represents. This module does not claim a
finer-grained, sub-day proof than that; where Step 8 would withhold a result
as ``NOT_POINT_IN_TIME_ELIGIBLE``, this module does the same, rather than
asserting a precision the data cannot support.

No future outcome ever enters this module. It does not import
:mod:`quantforge.research.event_study`, :mod:`quantforge.research.returns`,
:mod:`quantforge.research.benchmark_returns`, or
:mod:`quantforge.research.abnormal_returns`.

The continuous feature is the raw surprise ratio, nothing else (yet)
----------------------------------------------------------------------
``signal_value`` is the continuous feature a later evaluation/backtesting
layer should read -- and, for this first version, it is defined as exactly
``surprise_ratio``: no clipping, no winsorization, no z-scoring, no
percentile rank, no arbitrary threshold. ``surprise_ratio`` itself stays on
the result too (it predates ``signal_value``, from Step 9.1/9.2's own
EPS-surprise audit fields), so the two are numerically identical today. They
are kept as two separate fields anyway because they answer different
questions and are expected to diverge later: ``surprise_ratio`` is this
family's raw, permanent audit figure (what Step 8's own
:mod:`quantforge.research.eps_surprise` computed), while ``signal_value`` is
the feature a cross-sectional normalization step -- historical z-score,
sector-relative surprise, rolling percentile rank -- would someday overwrite
with a transformed figure, once that step defines its own historical-universe
and point-in-time rules. That is future work and is explicitly out of scope
here; this module computes no normalization of any kind.

``direction`` and ``signal_value`` are never computed on separate paths: both
derive from the one ``surprise_ratio`` value inside
:func:`_signal_outcome_from_eps_surprise`, so they cannot disagree.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy.orm import Session

from quantforge.models.earnings import EarningsEvent
from quantforge.models.enums import EarningsTiming
from quantforge.research.alignment import EventStudyAlignment, align_earnings_event
from quantforge.research.availability import (
    PointInTimeEligibility,
    ResearchMode,
    classify_point_in_time_eligibility,
)
from quantforge.research.calendar import AlignmentStatus
from quantforge.research.eps_surprise import (
    EpsSurpriseResult,
    EpsSurpriseStatus,
    compute_eps_surprise_for_observation,
)
from quantforge.research.listing_resolution import ListingResolutionStatus

__all__ = [
    "EpsSurpriseDirection",
    "EpsSurpriseSignal",
    "EpsSurpriseSignalStatus",
    "SignalType",
    "classify_eps_surprise_direction",
    "generate_eps_surprise_signal",
]


class SignalType(StrEnum):
    """Which signal family produced a result.

    Exactly one member exists so far. Carried on the result itself (rather
    than left implicit in the module a caller happened to import from) so a
    later consumer that pools signals from several families can still answer
    "what generated this?" from the object alone.
    """

    EPS_SURPRISE = "EPS_SURPRISE"


class EpsSurpriseDirection(StrEnum):
    """Sign-only classification of an EPS surprise.

    Deliberately has no magnitude tiers (e.g. "strong" vs "mild") and no
    investment-recommendation vocabulary (``BUY``/``SELL``/``HOLD``): this is
    a research classification of a reported fact, not a recommendation.
    """

    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL = "NEUTRAL"


class EpsSurpriseSignalStatus(StrEnum):
    """Why an EPS-surprise signal could, or honestly could not, be produced."""

    #: Both EPS figures were available, the estimate was non-zero, and the
    #: event resolved to an effective session. ``direction`` is populated.
    AVAILABLE = "AVAILABLE"

    #: The research mode is ``POINT_IN_TIME`` and the observation (or its
    #: session alignment) carries no availability evidence. Mirrors
    #: ``EpsSurpriseStatus.NOT_POINT_IN_TIME_ELIGIBLE`` and
    #: ``AlignmentStatus.NOT_POINT_IN_TIME_ELIGIBLE``.
    NOT_POINT_IN_TIME_ELIGIBLE = "NOT_POINT_IN_TIME_ELIGIBLE"

    #: The listing could not be resolved for this event date, or no trading
    #: session qualifies within the alignment window. No effective session
    #: exists for the signal to attach to.
    NO_EFFECTIVE_SESSION = "NO_EFFECTIVE_SESSION"

    #: The event's timing is ``DURING_MARKET`` (no intraday execution
    #: precision from daily bars) or ``UNKNOWN`` (before/after cannot be
    #: inferred). No effective session is guessed in either case.
    UNSUPPORTED_TIMING = "UNSUPPORTED_TIMING"

    #: ``reported_eps`` is ``None``. Never treated as zero.
    MISSING_REPORTED_EPS = "MISSING_REPORTED_EPS"

    #: ``estimated_eps`` is ``None``. Never treated as zero.
    MISSING_ESTIMATED_EPS = "MISSING_ESTIMATED_EPS"

    #: ``estimated_eps`` is exactly zero, so ``surprise_ratio`` is undefined.
    #: A direction is never derived from ``absolute_surprise``'s sign instead
    #: -- that would silently switch the signal's definition.
    UNAVAILABLE_ZERO_ESTIMATE = "UNAVAILABLE_ZERO_ESTIMATE"


@dataclass(frozen=True, slots=True)
class EpsSurpriseSignal:
    """One EPS-surprise signal observation.

    Contains only facts knowable at (or before) ``effective_session`` and the
    identity needed to audit them. Deliberately carries no forward return, no
    benchmark return, no abnormal return, no target session and no target
    price -- those are outcomes for a later evaluation/backtesting layer, not
    signal inputs (see the module and package docstrings).

    ``reported_eps``/``estimated_eps`` echo the inputs whenever the event
    itself states them, even when ``status`` withholds the derived surprise
    figures -- the same convention
    :class:`~quantforge.research.eps_surprise.EpsSurpriseResult` already uses,
    continued here rather than reinvented.

    ``signal_value`` is the continuous feature (see the module docstring for
    why it is a separate field from ``surprise_ratio`` despite being
    numerically identical today); it is ``None`` under every status except
    ``AVAILABLE``, exactly like ``direction``.
    """

    signal_type: SignalType
    event_id: uuid.UUID
    company_id: uuid.UUID
    security_id: uuid.UUID | None
    listing_id: uuid.UUID | None
    research_mode: ResearchMode
    point_in_time_eligibility: PointInTimeEligibility
    event_date: date
    timing: EarningsTiming
    effective_session: date | None
    status: EpsSurpriseSignalStatus
    direction: EpsSurpriseDirection | None
    signal_value: Decimal | None
    reported_eps: Decimal | None
    estimated_eps: Decimal | None
    absolute_surprise: Decimal | None
    surprise_ratio: Decimal | None
    reason: str


def classify_eps_surprise_direction(surprise_ratio: Decimal) -> EpsSurpriseDirection:
    """Classify an EPS surprise ratio by sign only.

    ``surprise_ratio > 0`` -> ``POSITIVE``, ``< 0`` -> ``NEGATIVE``,
    ``== 0`` -> ``NEUTRAL``. No magnitude thresholds: a 0.1% beat and a 40%
    beat are both ``POSITIVE`` in this first signal definition.
    """
    if surprise_ratio > 0:
        return EpsSurpriseDirection.POSITIVE
    if surprise_ratio < 0:
        return EpsSurpriseDirection.NEGATIVE
    return EpsSurpriseDirection.NEUTRAL


def _signal_outcome_from_eps_surprise(
    eps_surprise: EpsSurpriseResult,
) -> tuple[EpsSurpriseSignalStatus, EpsSurpriseDirection | None, Decimal | None]:
    """Pure translation from an already-computed EPS surprise to a signal outcome.

    Recomputes nothing :mod:`quantforge.research.eps_surprise` already
    decided; it only renames that module's status vocabulary into this one's,
    reads ``signal_value`` straight off ``surprise_ratio`` with no further
    transformation, and applies the sign-only classification rule to that same
    value -- a single read of ``surprise_ratio`` feeds both outputs, so
    ``direction`` and ``signal_value`` cannot disagree.

    ``eps_surprise.status`` can never be ``NOT_POINT_IN_TIME_ELIGIBLE`` here:
    :func:`generate_eps_surprise_signal` only calls this after
    ``_effective_session_from_alignment`` has confirmed
    ``AlignmentStatus.ALIGNED``, which by construction
    (:func:`~quantforge.research.alignment.align_earnings_event`) means either
    ``ResearchMode.DESCRIPTIVE`` or a ``POINT_IN_TIME`` observation already
    classified ``ELIGIBLE`` -- the same ``mode``/``observed_at`` pair this
    function's caller passes to
    :func:`~quantforge.research.eps_surprise.compute_eps_surprise_for_observation`
    can therefore never re-derive ``INELIGIBLE_NO_AVAILABILITY_EVIDENCE``.
    """
    if eps_surprise.status is EpsSurpriseStatus.MISSING_REPORTED_EPS:
        return EpsSurpriseSignalStatus.MISSING_REPORTED_EPS, None, None
    if eps_surprise.status is EpsSurpriseStatus.MISSING_ESTIMATED_EPS:
        return EpsSurpriseSignalStatus.MISSING_ESTIMATED_EPS, None, None
    if eps_surprise.status is EpsSurpriseStatus.ZERO_ESTIMATE:
        return EpsSurpriseSignalStatus.UNAVAILABLE_ZERO_ESTIMATE, None, None

    # Only EpsSurpriseStatus.AVAILABLE remains: mypy's exhaustiveness check
    # over the StrEnum confirms there is no remaining case to guard with an
    # `else`, and AVAILABLE is the one status that guarantees a ratio.
    assert eps_surprise.surprise_ratio is not None
    signal_value = eps_surprise.surprise_ratio
    return (
        EpsSurpriseSignalStatus.AVAILABLE,
        classify_eps_surprise_direction(signal_value),
        signal_value,
    )


def _effective_session_from_alignment(
    alignment: EventStudyAlignment,
) -> tuple[date | None, EpsSurpriseSignalStatus | None, str]:
    """Resolve the signal's effective session from Step 8 alignment, or explain why not.

    Returns ``(effective_session, status, reason)``. ``status`` is ``None``
    exactly when alignment succeeded (``ALIGNED``) and the caller should
    proceed to the EPS-surprise computation; otherwise it is the terminal
    signal status, ``effective_session`` is ``None``, and the caller must not
    attempt an EPS-surprise computation (there is no effective session for it
    to attach to).
    """
    if (
        alignment.listing_resolution.status is not ListingResolutionStatus.RESOLVED
        or alignment.session_alignment is None
    ):
        return (
            None,
            EpsSurpriseSignalStatus.NO_EFFECTIVE_SESSION,
            alignment.listing_resolution.reason,
        )

    session_status = alignment.session_alignment.status
    if session_status is AlignmentStatus.ALIGNED:
        return alignment.session_alignment.first_post_event_session, None, ""
    if session_status is AlignmentStatus.NOT_POINT_IN_TIME_ELIGIBLE:
        return (
            None,
            EpsSurpriseSignalStatus.NOT_POINT_IN_TIME_ELIGIBLE,
            alignment.session_alignment.reason,
        )
    if session_status in (
        AlignmentStatus.UNKNOWN_TIMING,
        AlignmentStatus.INTRADAY_PRECISION_UNSUPPORTED,
    ):
        return None, EpsSurpriseSignalStatus.UNSUPPORTED_TIMING, alignment.session_alignment.reason
    # Only AlignmentStatus.NO_SESSION_DATA remains.
    return None, EpsSurpriseSignalStatus.NO_EFFECTIVE_SESSION, alignment.session_alignment.reason


def generate_eps_surprise_signal(
    session: Session,
    event: EarningsEvent,
    *,
    mode: ResearchMode,
    security_id: uuid.UUID | None = None,
) -> EpsSurpriseSignal:
    """Generate the EPS-surprise signal for one already-selected earnings observation.

    Args:
        session: Database session to read listings and price bars from (used
            only by :func:`~quantforge.research.alignment.align_earnings_event`
            to resolve the listing and the available trading sessions --
            never to read a forward or benchmark price).
        event: The earnings observation to generate a signal for. Already
            selected by the caller: this function does not query for
            candidate revisions and does not choose among them. When several
            revisions of the same fiscal period exist, the caller must pick
            one first, with
            :func:`quantforge.research.availability.select_known_as_of`,
            exactly as
            :func:`quantforge.research.event_study.analyze_earnings_event`
            requires. Duplicating that choice here would create a second,
            competing place where "which revision is right" could be
            decided.
        mode: Which research question is being asked. Propagated unchanged
            into the alignment and the EPS-surprise computation.
        security_id: Passed through to
            :func:`~quantforge.research.alignment.align_earnings_event` to
            disambiguate a multi-security issuer.

    Returns:
        The computed :class:`EpsSurpriseSignal`. Never raises for an
        ordinary "cannot be determined" case -- see
        :class:`EpsSurpriseSignalStatus` for the full set of honest refusals.
    """
    eligibility = classify_point_in_time_eligibility(mode, observed_at=event.observed_at)
    alignment = align_earnings_event(session, event, mode=mode, security_id=security_id)

    effective_session, override_status, override_reason = _effective_session_from_alignment(
        alignment
    )

    if override_status is not None:
        return EpsSurpriseSignal(
            signal_type=SignalType.EPS_SURPRISE,
            event_id=event.id,
            company_id=event.company_id,
            security_id=alignment.listing_resolution.security_id,
            listing_id=alignment.listing_resolution.listing_id,
            research_mode=mode,
            point_in_time_eligibility=eligibility,
            event_date=event.event_date,
            timing=event.timing,
            effective_session=None,
            status=override_status,
            direction=None,
            signal_value=None,
            reported_eps=event.reported_eps,
            estimated_eps=event.estimated_eps,
            absolute_surprise=None,
            surprise_ratio=None,
            reason=override_reason,
        )

    eps_surprise = compute_eps_surprise_for_observation(
        reported_eps=event.reported_eps,
        estimated_eps=event.estimated_eps,
        observed_at=event.observed_at,
        mode=mode,
    )
    status, direction, signal_value = _signal_outcome_from_eps_surprise(eps_surprise)

    return EpsSurpriseSignal(
        signal_type=SignalType.EPS_SURPRISE,
        event_id=event.id,
        company_id=event.company_id,
        security_id=alignment.listing_resolution.security_id,
        listing_id=alignment.listing_resolution.listing_id,
        research_mode=mode,
        point_in_time_eligibility=eligibility,
        event_date=event.event_date,
        timing=event.timing,
        effective_session=effective_session,
        status=status,
        direction=direction,
        signal_value=signal_value,
        reported_eps=eps_surprise.reported_eps,
        estimated_eps=eps_surprise.estimated_eps,
        absolute_surprise=eps_surprise.absolute_surprise,
        surprise_ratio=eps_surprise.surprise_ratio,
        reason=eps_surprise.reason,
    )
