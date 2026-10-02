"""EPS surprise: a pure, Decimal-exact comparison of reported vs. estimated EPS.

This module computes one number family -- the absolute and percentage EPS
surprise -- from values the caller has already decided to use. It does not
select *which* :class:`~quantforge.models.earnings.EarningsEvent` row to read
those values from: that choice belongs to
:mod:`quantforge.research.availability` (:func:`~quantforge.research.availability.select_known_as_of`
for picking a revision, :func:`~quantforge.research.availability.classify_point_in_time_eligibility`
for whether the chosen observation may be treated as point-in-time tradable).
Baking row selection into a number-crunching function would let it silently
drift from the Step 8.1 availability rules this module is required to respect,
not re-implement.

Everything here is ``Decimal`` end to end, matching ``EarningsEvent.reported_eps``
/``estimated_eps`` (``NUMERIC(20, 4)``, see :mod:`quantforge.models.base`).
There is no ``float`` conversion anywhere in this module: a loss-making
quarter's EPS is exact to four decimal places in the database, and a `float`
would reintroduce the binary-fraction error QuantForge's price path already
refuses everywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from quantforge.research.availability import (
    PointInTimeEligibility,
    ResearchMode,
    classify_point_in_time_eligibility,
)

__all__ = [
    "EpsSurpriseResult",
    "EpsSurpriseStatus",
    "compute_eps_surprise",
    "compute_eps_surprise_for_observation",
]


class EpsSurpriseStatus(StrEnum):
    """Why an EPS surprise could, or honestly could not, be computed."""

    #: Both figures are present and ``estimated_eps`` is non-zero.
    #: ``absolute_surprise`` and ``surprise_ratio`` are both populated.
    AVAILABLE = "AVAILABLE"

    #: ``reported_eps`` is ``None``. Never treated as zero.
    MISSING_REPORTED_EPS = "MISSING_REPORTED_EPS"

    #: ``estimated_eps`` is ``None``. Never treated as zero.
    MISSING_ESTIMATED_EPS = "MISSING_ESTIMATED_EPS"

    #: ``estimated_eps`` is exactly zero. A percentage surprise against a zero
    #: base is undefined -- there is no "percent above nothing" -- so
    #: ``surprise_ratio`` is ``None``. ``absolute_surprise`` is still
    #: computable and still meaningful (it is simply ``reported_eps``), so it
    #: is populated.
    ZERO_ESTIMATE = "ZERO_ESTIMATE"

    #: Set only by :func:`compute_eps_surprise_for_observation`, never by
    #: :func:`compute_eps_surprise` itself: the research mode is
    #: ``POINT_IN_TIME`` and the observation carries no availability evidence
    #: (``observed_at IS NULL``). Mirrors
    #: ``AlignmentStatus.NOT_POINT_IN_TIME_ELIGIBLE`` in
    #: :mod:`quantforge.research.calendar` -- the same rule, applied to a
    #: different derived quantity.
    NOT_POINT_IN_TIME_ELIGIBLE = "NOT_POINT_IN_TIME_ELIGIBLE"


@dataclass(frozen=True, slots=True)
class EpsSurpriseResult:
    """The result of comparing one reported EPS figure to one estimate.

    ``reported_eps``/``estimated_eps`` echo the inputs for audit purposes --
    they are never fabricated when missing, so a ``None`` here always means
    "this input was genuinely absent", not zero. ``absolute_surprise`` and
    ``surprise_ratio`` are ``None`` whenever ``status`` is not ``AVAILABLE``
    or ``ZERO_ESTIMATE`` (and ``surprise_ratio`` is always ``None`` under
    ``ZERO_ESTIMATE``), so a caller can never mistake "could not be computed"
    for a genuine zero surprise.
    """

    status: EpsSurpriseStatus
    reported_eps: Decimal | None
    estimated_eps: Decimal | None
    absolute_surprise: Decimal | None
    surprise_ratio: Decimal | None
    reason: str


def compute_eps_surprise(
    *,
    reported_eps: Decimal | None,
    estimated_eps: Decimal | None,
) -> EpsSurpriseResult:
    """Compute the absolute and percentage EPS surprise, exactly in ``Decimal``.

    ``absolute_surprise = reported_eps - estimated_eps``.
    ``surprise_ratio = absolute_surprise / abs(estimated_eps)``, defined only
    when ``estimated_eps`` is non-zero.

    Neither input is ever defaulted to zero when missing -- a company that has
    not yet reported and a company that reported exactly zero EPS are
    different facts, and collapsing "unknown" into "0" would corrupt every
    downstream statistic that averages surprises.
    """
    if reported_eps is None:
        return EpsSurpriseResult(
            status=EpsSurpriseStatus.MISSING_REPORTED_EPS,
            reported_eps=None,
            estimated_eps=estimated_eps,
            absolute_surprise=None,
            surprise_ratio=None,
            reason="reported_eps is not yet available",
        )
    if estimated_eps is None:
        return EpsSurpriseResult(
            status=EpsSurpriseStatus.MISSING_ESTIMATED_EPS,
            reported_eps=reported_eps,
            estimated_eps=None,
            absolute_surprise=None,
            surprise_ratio=None,
            reason="estimated_eps is not available",
        )

    absolute_surprise = reported_eps - estimated_eps

    if estimated_eps == 0:
        return EpsSurpriseResult(
            status=EpsSurpriseStatus.ZERO_ESTIMATE,
            reported_eps=reported_eps,
            estimated_eps=estimated_eps,
            absolute_surprise=absolute_surprise,
            surprise_ratio=None,
            reason="estimated_eps is zero; a percentage surprise against a zero base is undefined",
        )

    return EpsSurpriseResult(
        status=EpsSurpriseStatus.AVAILABLE,
        reported_eps=reported_eps,
        estimated_eps=estimated_eps,
        absolute_surprise=absolute_surprise,
        surprise_ratio=absolute_surprise / abs(estimated_eps),
        reason="computed from reported and estimated EPS",
    )


def compute_eps_surprise_for_observation(
    *,
    reported_eps: Decimal | None,
    estimated_eps: Decimal | None,
    observed_at: datetime | None,
    mode: ResearchMode,
) -> EpsSurpriseResult:
    """Compute an EPS surprise subject to the Step 8.1 point-in-time gate.

    This is the function a caller working from a single, already-selected
    :class:`~quantforge.models.earnings.EarningsEvent` observation should use
    (pass its ``reported_eps``/``estimated_eps``/``observed_at`` directly).
    In ``ResearchMode.DESCRIPTIVE`` it behaves exactly like
    :func:`compute_eps_surprise`. In ``ResearchMode.POINT_IN_TIME``, an
    observation with no availability evidence (``observed_at IS NULL``) never
    produces a usable surprise figure -- ``absolute_surprise`` and
    ``surprise_ratio`` are withheld (status
    ``NOT_POINT_IN_TIME_ELIGIBLE``) exactly as
    ``quantforge.research.alignment.align_earnings_event`` withholds its
    session fields for the same reason. ``reported_eps``/``estimated_eps``
    are still echoed back: they are input facts from the row the caller
    already selected, not a derived point-in-time claim.
    """
    eligibility = classify_point_in_time_eligibility(mode, observed_at=observed_at)
    if mode is ResearchMode.POINT_IN_TIME and eligibility is not PointInTimeEligibility.ELIGIBLE:
        return EpsSurpriseResult(
            status=EpsSurpriseStatus.NOT_POINT_IN_TIME_ELIGIBLE,
            reported_eps=reported_eps,
            estimated_eps=estimated_eps,
            absolute_surprise=None,
            surprise_ratio=None,
            reason=(
                "observed_at is NULL and no other availability evidence exists; "
                "a point-in-time EPS-surprise claim cannot be supported"
            ),
        )
    return compute_eps_surprise(reported_eps=reported_eps, estimated_eps=estimated_eps)
