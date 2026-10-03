"""Corporate-action guard: refuse a holding period that would need a share-count change.

QuantForge has no corporate-action adjustment engine and does not adjust anything
here. When a position is held across a split its share count changes, and
nothing downstream knows how to account for that -- so the only safe answer is to
refuse. This module is the pure decision; it reads no database, no calendar and
no pandas, and is exported eagerly like the other market contracts.

What "crosses a split" means
----------------------------
A position is entered in session ``E`` and exited in session ``X`` (``E <= X``);
a split takes effect on calendar date ``S`` (Massive: the prior session's
post-market is the last to show pre-split prices, and on ``S`` all trading --
pre-market included -- is already adjusted). The holder's quantity changes
overnight into ``S``, so the holding crosses the split iff::

    E < S <= X

* ``S == E``: the position was opened on a day that already trades post-split, so
  no quantity change is held across -- not blocked.
* ``S == X``: the position was held overnight into the first adjusted session and
  exited on it -- blocked.
* ``S < E`` or ``S > X``: before entry / after exit -- not blocked.
* ``E == X`` (a same-session round trip) holds nothing overnight -- not blocked.

The comparison is on calendar dates, with no shifting: if ``S`` is a weekend or
holiday (the effective session is the next one), ``E < S <= X`` is still exactly
right, because ``X`` is a session and so lies on or after that next session
whenever it lies on or after ``S``. A non-session ``S`` is therefore neither
rejected nor moved. The caller passes session dates; this module does not verify
they are sessions.

Absence is not evidence
-----------------------
An empty split table may mean "no split" or "never fetched". The guard returns
``CLEAR`` only when the dates a split could fall on, ``(E, X]``, are fully inside
ingested **coverage**; otherwise it returns ``COVERAGE_UNAVAILABLE`` and names the
uncovered dates. A known split blocks regardless of coverage -- a known fact is
never hidden by missing coverage elsewhere.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum

__all__ = [
    "CorporateActionGuardResult",
    "CoverageInterval",
    "GuardStatus",
    "SplitFact",
    "evaluate_split_guard",
]

_ONE_DAY = timedelta(days=1)


class GuardStatus(StrEnum):
    """The outcome of asking whether a holding period may proceed."""

    #: No split falls in ``(E, X]`` and coverage proves none was missed.
    CLEAR = "CLEAR"

    #: At least one split takes effect in ``(E, X]``; share-count adjustment would
    #: be required and is not supported.
    BLOCKED_BY_SPLIT = "BLOCKED_BY_SPLIT"

    #: Split data does not cover every date a split could fall on. This is NOT
    #: "no split": it is "unknown", and must be treated as unsafe.
    COVERAGE_UNAVAILABLE = "COVERAGE_UNAVAILABLE"

    #: The exit session precedes the entry session.
    INVALID_INTERVAL = "INVALID_INTERVAL"


@dataclass(frozen=True, slots=True)
class SplitFact:
    """One stored split, as the guard needs it. Orientation: ``new_shares / old_shares``."""

    split_id: uuid.UUID
    security_id: uuid.UUID
    execution_date: date
    old_shares: Decimal
    new_shares: Decimal
    adjustment_type: str
    provider_split_id: str


@dataclass(frozen=True, slots=True)
class CoverageInterval:
    """Inclusive date range over which a complete split fetch succeeded."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            message = f"coverage end {self.end} precedes start {self.start}"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class CorporateActionGuardResult:
    """The guard's answer, with the facts that explain it."""

    status: GuardStatus
    security_id: uuid.UUID
    start_session: date
    end_session: date
    #: Splits taking effect in ``(start_session, end_session]``; set for BLOCKED.
    blocking_splits: tuple[SplitFact, ...]
    #: Inclusive ranges of ``(start_session, end_session]`` that no coverage
    #: proves; set for COVERAGE_UNAVAILABLE.
    uncovered_ranges: tuple[CoverageInterval, ...]
    reason: str

    @property
    def is_clear(self) -> bool:
        return self.status is GuardStatus.CLEAR


def _merge(intervals: Iterable[CoverageInterval]) -> list[CoverageInterval]:
    """Union of inclusive date ranges; ranges that touch (end + 1 day == start) merge."""
    merged: list[CoverageInterval] = []
    for interval in sorted(intervals, key=lambda i: (i.start, i.end)):
        if merged and interval.start <= merged[-1].end + _ONE_DAY:
            if interval.end > merged[-1].end:
                merged[-1] = CoverageInterval(merged[-1].start, interval.end)
        else:
            merged.append(interval)
    return merged


def _uncovered(
    needed: CoverageInterval, coverage: Iterable[CoverageInterval]
) -> tuple[CoverageInterval, ...]:
    """The parts of ``needed`` that no coverage interval proves."""
    gaps: list[CoverageInterval] = []
    cursor = needed.start
    for interval in _merge(coverage):
        if interval.end < cursor:
            continue
        if interval.start > needed.end:
            break
        if interval.start > cursor:
            gaps.append(CoverageInterval(cursor, interval.start - _ONE_DAY))
        cursor = max(cursor, interval.end + _ONE_DAY)
        if cursor > needed.end:
            return tuple(gaps)
    if cursor <= needed.end:
        gaps.append(CoverageInterval(cursor, needed.end))
    return tuple(gaps)


def evaluate_split_guard(
    *,
    security_id: uuid.UUID,
    start_session: date,
    end_session: date,
    splits: Sequence[SplitFact],
    coverage: Sequence[CoverageInterval],
) -> CorporateActionGuardResult:
    """Decide whether holding ``security_id`` from ``start_session`` to ``end_session`` is safe.

    ``splits`` and ``coverage`` are the caller's stored facts for this security;
    ``splits`` entries for other securities are ignored. See the module docstring
    for the ``E < S <= X`` rule and for why an empty ``splits`` alone is never
    enough to return ``CLEAR``.
    """

    def result(
        status: GuardStatus,
        reason: str,
        *,
        blocking: tuple[SplitFact, ...] = (),
        uncovered: tuple[CoverageInterval, ...] = (),
    ) -> CorporateActionGuardResult:
        return CorporateActionGuardResult(
            status=status,
            security_id=security_id,
            start_session=start_session,
            end_session=end_session,
            blocking_splits=blocking,
            uncovered_ranges=uncovered,
            reason=reason,
        )

    if end_session < start_session:
        return result(
            GuardStatus.INVALID_INTERVAL,
            f"exit session {end_session} precedes entry session {start_session}",
        )

    blocking = tuple(
        sorted(
            (
                split
                for split in splits
                if split.security_id == security_id
                and start_session < split.execution_date <= end_session
            ),
            key=lambda split: (split.execution_date, split.provider_split_id),
        )
    )
    if blocking:
        dates = ", ".join(split.execution_date.isoformat() for split in blocking)
        return result(
            GuardStatus.BLOCKED_BY_SPLIT,
            f"{len(blocking)} share-count event(s) take effect after entry {start_session} "
            f"and on or before exit {end_session} ({dates}); share-count adjustment is not "
            "supported",
            blocking=blocking,
        )

    if end_session == start_session:
        return result(
            GuardStatus.CLEAR,
            "entry and exit are the same session; nothing is held overnight, so no split "
            "can change the position",
        )

    needed = CoverageInterval(start_session + _ONE_DAY, end_session)
    gaps = _uncovered(needed, coverage)
    if gaps:
        text = ", ".join(f"{gap.start}..{gap.end}" for gap in gaps)
        return result(
            GuardStatus.COVERAGE_UNAVAILABLE,
            f"no split was found, but split data does not cover {text}; absence of a split "
            "is not proven",
            uncovered=gaps,
        )
    return result(
        GuardStatus.CLEAR,
        f"split data covers {needed.start}..{needed.end} and records no split in it",
    )
