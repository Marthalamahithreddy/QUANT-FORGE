"""Unit tests for EPS surprise arithmetic.

Pure ``Decimal`` arithmetic over plain values -- no database, no ORM
instances -- matching how ``tests/test_research_calendar.py`` tests
:mod:`quantforge.research.calendar`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from quantforge.models.enums import AvailabilityBasis
from quantforge.research.availability import ResearchMode
from quantforge.research.eps_surprise import (
    EpsSurpriseStatus,
    compute_eps_surprise,
    compute_eps_surprise_for_observation,
)

# ── canonical examples from the Step 8.3 brief ──────────────────────────────


def test_a_positive_beat() -> None:
    result = compute_eps_surprise(reported_eps=Decimal("1.20"), estimated_eps=Decimal("1.00"))
    assert result.status is EpsSurpriseStatus.AVAILABLE
    assert result.absolute_surprise == Decimal("0.20")
    assert result.surprise_ratio == Decimal("0.20")


def test_a_positive_miss() -> None:
    result = compute_eps_surprise(reported_eps=Decimal("0.80"), estimated_eps=Decimal("1.00"))
    assert result.status is EpsSurpriseStatus.AVAILABLE
    assert result.absolute_surprise == Decimal("-0.20")
    assert result.surprise_ratio == Decimal("-0.20")


def test_negative_estimate_with_a_bigger_loss_than_expected() -> None:
    """reported=-0.50 vs estimate=-0.40: a worse-than-expected loss."""
    result = compute_eps_surprise(reported_eps=Decimal("-0.50"), estimated_eps=Decimal("-0.40"))
    assert result.status is EpsSurpriseStatus.AVAILABLE
    assert result.absolute_surprise == Decimal("-0.10")
    assert result.surprise_ratio == Decimal("-0.25")


def test_negative_estimate_with_an_improvement() -> None:
    """reported=-0.30 vs estimate=-0.50: a smaller-than-expected loss."""
    result = compute_eps_surprise(reported_eps=Decimal("-0.30"), estimated_eps=Decimal("-0.50"))
    assert result.status is EpsSurpriseStatus.AVAILABLE
    assert result.absolute_surprise == Decimal("0.20")
    assert result.surprise_ratio == Decimal("0.40")


def test_reported_exactly_equal_to_estimate() -> None:
    result = compute_eps_surprise(reported_eps=Decimal("1.00"), estimated_eps=Decimal("1.00"))
    assert result.status is EpsSurpriseStatus.AVAILABLE
    assert result.absolute_surprise == Decimal("0.00")
    assert result.surprise_ratio == Decimal("0")


# ── zero estimate ────────────────────────────────────────────────────────────


def test_zero_estimate_still_reports_a_meaningful_absolute_surprise() -> None:
    result = compute_eps_surprise(reported_eps=Decimal("0.75"), estimated_eps=Decimal("0"))
    assert result.status is EpsSurpriseStatus.ZERO_ESTIMATE
    assert result.absolute_surprise == Decimal("0.75")
    assert result.surprise_ratio is None


def test_zero_estimate_with_a_negative_reported_value() -> None:
    result = compute_eps_surprise(reported_eps=Decimal("-0.10"), estimated_eps=Decimal("0.00"))
    assert result.status is EpsSurpriseStatus.ZERO_ESTIMATE
    assert result.absolute_surprise == Decimal("-0.10")
    assert result.surprise_ratio is None


# ── missing values must not become zero ─────────────────────────────────────


def test_missing_reported_eps() -> None:
    result = compute_eps_surprise(reported_eps=None, estimated_eps=Decimal("1.00"))
    assert result.status is EpsSurpriseStatus.MISSING_REPORTED_EPS
    assert result.reported_eps is None
    assert result.estimated_eps == Decimal("1.00")
    assert result.absolute_surprise is None
    assert result.surprise_ratio is None


def test_missing_estimated_eps() -> None:
    result = compute_eps_surprise(reported_eps=Decimal("1.00"), estimated_eps=None)
    assert result.status is EpsSurpriseStatus.MISSING_ESTIMATED_EPS
    assert result.reported_eps == Decimal("1.00")
    assert result.estimated_eps is None
    assert result.absolute_surprise is None
    assert result.surprise_ratio is None


def test_both_missing() -> None:
    result = compute_eps_surprise(reported_eps=None, estimated_eps=None)
    assert result.status is EpsSurpriseStatus.MISSING_REPORTED_EPS
    assert result.absolute_surprise is None
    assert result.surprise_ratio is None


# ── Decimal precision, no float ──────────────────────────────────────────────


def test_decimal_precision_is_preserved_exactly() -> None:
    reported = Decimal("1.2345")
    estimated = Decimal("1.1111")
    result = compute_eps_surprise(reported_eps=reported, estimated_eps=estimated)
    assert result.status is EpsSurpriseStatus.AVAILABLE
    assert result.absolute_surprise == Decimal("0.1234")
    assert isinstance(result.absolute_surprise, Decimal)
    assert isinstance(result.surprise_ratio, Decimal)


def test_no_float_is_ever_produced() -> None:
    result = compute_eps_surprise(reported_eps=Decimal("0.1"), estimated_eps=Decimal("0.2"))
    assert not isinstance(result.absolute_surprise, float)
    assert not isinstance(result.surprise_ratio, float)
    # The canonical float trap: 0.1 - 0.2 != -0.1 in binary floating point.
    assert result.absolute_surprise == Decimal("-0.1")


# ── point-in-time gate is not weakened ───────────────────────────────────────


def test_descriptive_mode_uses_the_observation_regardless_of_observed_at() -> None:
    result = compute_eps_surprise_for_observation(
        reported_eps=Decimal("1.20"),
        estimated_eps=Decimal("1.00"),
        observed_at=None,
        observed_at_basis=None,
        mode=ResearchMode.DESCRIPTIVE,
    )
    assert result.status is EpsSurpriseStatus.AVAILABLE
    assert result.surprise_ratio == Decimal("0.20")


def test_point_in_time_mode_rejects_a_null_observed_at() -> None:
    """The Step 8.1 availability rule is not weakened for a derived statistic."""
    result = compute_eps_surprise_for_observation(
        reported_eps=Decimal("1.20"),
        estimated_eps=Decimal("1.00"),
        observed_at=None,
        observed_at_basis=None,
        mode=ResearchMode.POINT_IN_TIME,
    )
    assert result.status is EpsSurpriseStatus.NOT_POINT_IN_TIME_ELIGIBLE
    assert result.absolute_surprise is None
    assert result.surprise_ratio is None
    # The input facts are still echoed -- they are not themselves the
    # point-in-time claim being withheld.
    assert result.reported_eps == Decimal("1.20")
    assert result.estimated_eps == Decimal("1.00")


def test_point_in_time_mode_accepts_a_known_observed_at() -> None:
    observed = datetime(2026, 6, 15, 20, 5, tzinfo=UTC)
    result = compute_eps_surprise_for_observation(
        reported_eps=Decimal("1.20"),
        estimated_eps=Decimal("1.00"),
        observed_at=observed,
        observed_at_basis=AvailabilityBasis.SOURCE_PUBLICATION,
        mode=ResearchMode.POINT_IN_TIME,
    )
    assert result.status is EpsSurpriseStatus.AVAILABLE
    assert result.surprise_ratio == Decimal("0.20")


@pytest.mark.parametrize("mode", [ResearchMode.DESCRIPTIVE, ResearchMode.POINT_IN_TIME])
def test_missing_reported_eps_is_not_masked_by_the_point_in_time_gate(mode: ResearchMode) -> None:
    """A missing value is still a missing value, independent of the availability gate."""
    result = compute_eps_surprise_for_observation(
        reported_eps=None,
        estimated_eps=Decimal("1.00"),
        observed_at=datetime(2026, 6, 15, 20, 5, tzinfo=UTC),
        observed_at_basis=AvailabilityBasis.SOURCE_PUBLICATION,
        mode=mode,
    )
    assert result.status is EpsSurpriseStatus.MISSING_REPORTED_EPS
