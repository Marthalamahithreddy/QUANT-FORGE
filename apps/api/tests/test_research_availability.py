"""Unit tests for point-in-time eligibility and revision selection.

:func:`select_known_as_of` operates on plain, unpersisted
:class:`~quantforge.models.earnings.EarningsEvent` instances -- SQLAlchemy
declarative objects can be constructed and inspected without ever touching a
``Session`` or a database -- so these tests need neither PostgreSQL nor the
``integration`` marker.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from quantforge.models.earnings import EarningsEvent
from quantforge.models.enums import EarningsTiming
from quantforge.research.availability import (
    PointInTimeEligibility,
    ResearchMode,
    classify_point_in_time_eligibility,
    select_known_as_of,
)

_COMPANY_ID = uuid.uuid4()
_SOURCE_ID = uuid.uuid4()


def _event(**overrides: object) -> EarningsEvent:
    fields: dict[str, object] = {
        "company_id": _COMPANY_ID,
        "fiscal_year": 2026,
        "fiscal_quarter": 2,
        "event_date": date(2026, 6, 15),
        "timing": EarningsTiming.AFTER_MARKET,
        "reported_eps": Decimal("1.00"),
        "source_id": _SOURCE_ID,
        "observed_at": None,
    }
    fields.update(overrides)
    return EarningsEvent(**fields)


# ── eligibility classification ──────────────────────────────────────────────


def test_descriptive_mode_is_not_applicable_regardless_of_observed_at() -> None:
    assert (
        classify_point_in_time_eligibility(ResearchMode.DESCRIPTIVE, observed_at=None)
        is PointInTimeEligibility.NOT_APPLICABLE
    )
    assert (
        classify_point_in_time_eligibility(
            ResearchMode.DESCRIPTIVE, observed_at=datetime(2026, 6, 15, tzinfo=UTC)
        )
        is PointInTimeEligibility.NOT_APPLICABLE
    )


def test_point_in_time_mode_with_no_observed_at_is_ineligible() -> None:
    """observed_at=NULL must never silently become historically tradable."""
    assert (
        classify_point_in_time_eligibility(ResearchMode.POINT_IN_TIME, observed_at=None)
        is PointInTimeEligibility.INELIGIBLE_NO_AVAILABILITY_EVIDENCE
    )


def test_point_in_time_mode_with_a_known_observed_at_is_eligible() -> None:
    assert (
        classify_point_in_time_eligibility(
            ResearchMode.POINT_IN_TIME, observed_at=datetime(2026, 6, 15, 20, 5, tzinfo=UTC)
        )
        is PointInTimeEligibility.ELIGIBLE
    )


# ── revision selection: no look-ahead ────────────────────────────────────────


def test_no_revisions_known_yet_returns_none() -> None:
    as_of = datetime(2026, 6, 1, tzinfo=UTC)
    preliminary = _event(observed_at=datetime(2026, 6, 15, 20, 5, tzinfo=UTC))
    assert select_known_as_of([preliminary], as_of=as_of) is None


def test_a_row_with_no_observed_at_contributes_no_evidence() -> None:
    """A NULL observed_at is excluded outright, never treated as 'available immediately'."""
    as_of = datetime(2026, 7, 1, tzinfo=UTC)
    undisclosed = _event(observed_at=None)
    assert select_known_as_of([undisclosed], as_of=as_of) is None


def test_a_known_observed_at_at_or_before_decision_time_is_selected() -> None:
    observed = datetime(2026, 6, 15, 20, 5, tzinfo=UTC)
    event = _event(observed_at=observed)
    assert select_known_as_of([event], as_of=observed) is event
    assert select_known_as_of([event], as_of=observed + timedelta(days=1)) is event


def test_a_future_revision_is_never_selected() -> None:
    """Invariant 1: a later revision must not be treated as known at an earlier decision time.

    Two rows describe the same fiscal period: a preliminary one observed
    first, and a restatement observed later. At a decision time between the
    two, only the preliminary one is knowable.
    """
    as_of = datetime(2026, 6, 20, tzinfo=UTC)
    preliminary = _event(
        reported_eps=Decimal("1.00"), observed_at=datetime(2026, 6, 15, 20, 5, tzinfo=UTC)
    )
    restatement = _event(
        reported_eps=Decimal("0.95"), observed_at=datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
    )
    selected = select_known_as_of([preliminary, restatement], as_of=as_of)
    assert selected is preliminary
    assert selected is not restatement


def test_the_latest_known_revision_as_of_the_decision_time_wins() -> None:
    """Once the restatement's own observed_at has passed, it becomes the answer."""
    as_of = datetime(2026, 7, 2, tzinfo=UTC)
    preliminary = _event(
        reported_eps=Decimal("1.00"), observed_at=datetime(2026, 6, 15, 20, 5, tzinfo=UTC)
    )
    restatement = _event(
        reported_eps=Decimal("0.95"), observed_at=datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
    )
    selected = select_known_as_of([preliminary, restatement], as_of=as_of)
    assert selected is restatement
