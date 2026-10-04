"""Point-in-time eligibility and revision selection.

Two different research questions get confused if they share one type, so this
module keeps them apart from the start:

1. **Descriptive**: "what happened around this reported earnings event?" This
   may use an observation whose ``observed_at`` is ``NULL`` -- the fact that
   QuantForge does not know exactly when a source made the information public
   does not stop a retrospective study of what the event was and what
   followed it.
2. **Point-in-time / tradable**: "what could a strategy have known at decision
   time T?" This is a stronger claim, and it requires actual availability
   evidence. An ``observed_at`` of ``NULL`` means exactly what it says --
   undisclosed -- and undisclosed is not evidence of early availability. It
   must never be treated as "available immediately" (which would be
   optimistic look-ahead) nor silently dropped into "available at
   ``ingested_at``" (``ingested_at`` is when *QuantForge* happened to fetch
   it, not when the market could have known it).

Nothing in this module reads from the database: it operates on plain values
and on already-loaded :class:`~quantforge.models.earnings.EarningsEvent`
instances, never on a ``Session``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Final

from quantforge.models.earnings import EarningsEvent
from quantforge.models.enums import AvailabilityBasis

__all__ = [
    "APPROVED_AVAILABILITY_BASES",
    "PointInTimeEligibility",
    "ResearchMode",
    "classify_point_in_time_eligibility",
    "is_point_in_time_availability_eligible",
    "select_known_as_of",
]

#: The availability evidence QuantForge treats as "known at ``observed_at``" for a
#: point-in-time/execution decision. The ONE place this is decided -- nothing
#: else compares a basis directly.
#:
#: * ``SOURCE_PUBLICATION`` is approved: the source itself states when this
#:   observation was published.
#: * ``SEC_FILING_ACCEPTANCE`` is deliberately NOT approved yet. It is a sound
#:   "the filing was on EDGAR by about T" bound for a filing-availability
#:   strategy, but (a) acceptance is only a LOWER bound on public availability
#:   (a filing accepted after 5:30 p.m. Eastern is normally not disseminated
#:   until the next business day; SEC publishes no first-availability time),
#:   the submissions-JSON field's official definition is unresolved (its UTC
#:   reading is empirically validated, step 10.7A.1), and (b) no filing-based
#:   signal exists to define the strategy it would be approved *for*. Adding it
#:   here is a one-line, reviewed change once both are settled.
APPROVED_AVAILABILITY_BASES: Final[frozenset[AvailabilityBasis]] = frozenset(
    {AvailabilityBasis.SOURCE_PUBLICATION}
)


def is_point_in_time_availability_eligible(
    observed_at: datetime | None,
    observed_at_basis: AvailabilityBasis | None,
) -> bool:
    """Whether a timestamp and its stated meaning may support a point-in-time claim.

    True only when a timestamp exists, it states what it proves, and that kind
    of evidence is in :data:`APPROVED_AVAILABILITY_BASES`. A timestamp with no
    basis, a basis with no timestamp, and an unapproved basis are all False --
    ``observed_at IS NOT NULL`` alone is never enough.
    """
    return (
        observed_at is not None
        and observed_at_basis is not None
        and observed_at_basis in APPROVED_AVAILABILITY_BASES
    )


class ResearchMode(StrEnum):
    """Which of the two research questions a caller is asking.

    This is a caller-stated intent, not something inferred from the data --
    the same :class:`~quantforge.models.earnings.EarningsEvent` row can
    legitimately be read either way, and it is the research question, not the
    row, that decides which rules apply.
    """

    #: "What happened?" -- retrospective, may use observations with no known
    #: availability time.
    DESCRIPTIVE = "DESCRIPTIVE"

    #: "What could have been known and traded on at decision time T?" --
    #: requires genuine availability evidence.
    POINT_IN_TIME = "POINT_IN_TIME"


class PointInTimeEligibility(StrEnum):
    """Whether one observation may be treated as point-in-time tradable."""

    #: The research mode is ``DESCRIPTIVE``; eligibility does not apply.
    NOT_APPLICABLE = "NOT_APPLICABLE"

    #: ``observed_at`` is genuinely stated, so a decision-time comparison is
    #: possible.
    ELIGIBLE = "ELIGIBLE"

    #: ``observed_at`` is ``NULL`` and no other availability evidence exists.
    #: The observation must not silently qualify as tradable.
    INELIGIBLE_NO_AVAILABILITY_EVIDENCE = "INELIGIBLE_NO_AVAILABILITY_EVIDENCE"

    #: A timestamp exists but its basis is missing or not approved for
    #: point-in-time use (see ``APPROVED_AVAILABILITY_BASES``). Evidence exists;
    #: it just does not prove what a tradability claim needs.
    INELIGIBLE_UNAPPROVED_AVAILABILITY_BASIS = "INELIGIBLE_UNAPPROVED_AVAILABILITY_BASIS"


def classify_point_in_time_eligibility(
    mode: ResearchMode,
    *,
    observed_at: datetime | None,
    observed_at_basis: AvailabilityBasis | None,
) -> PointInTimeEligibility:
    """Classify whether an observation may be used as point-in-time tradable.

    ``observed_at`` together with its ``observed_at_basis`` is the only
    availability evidence this schema carries (see
    ``EarningsEvent.observed_at``'s own documentation); there is no
    fallback to ``ingested_at``, which records when QuantForge fetched the
    fact, not when the source made it public. Treating the two as
    interchangeable is exactly the kind of look-ahead bias this function
    exists to block.
    """
    if mode is ResearchMode.DESCRIPTIVE:
        return PointInTimeEligibility.NOT_APPLICABLE
    if observed_at is None:
        return PointInTimeEligibility.INELIGIBLE_NO_AVAILABILITY_EVIDENCE
    if not is_point_in_time_availability_eligible(observed_at, observed_at_basis):
        return PointInTimeEligibility.INELIGIBLE_UNAPPROVED_AVAILABILITY_BASIS
    return PointInTimeEligibility.ELIGIBLE


def select_known_as_of(
    events: Sequence[EarningsEvent],
    *,
    as_of: datetime,
) -> EarningsEvent | None:
    """Return the revision of a fiscal period actually knowable at ``as_of``.

    Several rows can describe the same fiscal period -- a preliminary release,
    a restatement, two vendors -- and ``events`` is expected to already be
    narrowed to one such group (e.g. via
    :meth:`~quantforge.repositories.earnings.EarningsRepository.list_for_fiscal_period`).
    This function is the point-in-time-safe way to pick *one* of them: the
    revision with the latest ``observed_at`` that is still ``<= as_of``.

    A row with ``observed_at IS NULL`` contributes no availability evidence
    and is excluded outright, never defaulted to "available immediately" or
    to its ``ingested_at``. A row whose ``observed_at`` is *after* ``as_of``
    is a future revision relative to the decision time and is excluded for
    the same reason a backtest must never peek at tomorrow's restatement:
    selecting it would let a later, more-accurate revision silently stand in
    for what was actually known at the time, which is look-ahead bias by
    another name.

    Returns:
        The latest revision known by ``as_of``, or ``None`` if no row in
        ``events`` carries availability evidence placing it at or before
        ``as_of``. ``None`` is a meaningful answer -- it means "nothing in
        this group is usable for a point-in-time decision at this instant",
        not "there is no data".
    """
    known = [
        event
        for event in events
        if event.observed_at is not None
        and is_point_in_time_availability_eligible(event.observed_at, event.observed_at_basis)
        and event.observed_at <= as_of
    ]
    if not known:
        return None

    def _observed_at(event: EarningsEvent) -> datetime:
        assert event.observed_at is not None  # narrowed by the filter above, for mypy
        return event.observed_at

    return max(known, key=_observed_at)
