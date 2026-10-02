"""Pure unit tests for EPS-surprise signal classification.

Covers only the deterministic, no-database pieces: sign-only direction
classification, and the Decimal-exactness/float-avoidance discipline every
Step 8 numeric module already upholds. Database-dependent effective-session
and point-in-time wiring is covered by
``tests/integration/test_signals_eps_surprise.py``.
"""

from __future__ import annotations

from decimal import Decimal

from quantforge.signals.eps_surprise import EpsSurpriseDirection, classify_eps_surprise_direction


def test_positive_surprise_is_positive() -> None:
    assert classify_eps_surprise_direction(Decimal("0.1500")) is EpsSurpriseDirection.POSITIVE


def test_negative_surprise_is_negative() -> None:
    assert classify_eps_surprise_direction(Decimal("-0.1500")) is EpsSurpriseDirection.NEGATIVE


def test_zero_surprise_is_neutral() -> None:
    assert classify_eps_surprise_direction(Decimal("0")) is EpsSurpriseDirection.NEUTRAL


def test_a_tiny_positive_surprise_is_still_positive() -> None:
    """No magnitude threshold: even a 0.01% beat is POSITIVE, not NEUTRAL."""
    assert classify_eps_surprise_direction(Decimal("0.0001")) is EpsSurpriseDirection.POSITIVE


def test_a_tiny_negative_surprise_is_still_negative() -> None:
    assert classify_eps_surprise_direction(Decimal("-0.0001")) is EpsSurpriseDirection.NEGATIVE


def test_classification_is_exact_decimal_with_no_float_conversion() -> None:
    """A ratio that is lossy in binary floating point must still classify correctly."""
    ratio = Decimal("1") / Decimal("3")  # exact Decimal division, not float division
    assert classify_eps_surprise_direction(ratio) is EpsSurpriseDirection.POSITIVE
    assert classify_eps_surprise_direction(-ratio) is EpsSurpriseDirection.NEGATIVE
