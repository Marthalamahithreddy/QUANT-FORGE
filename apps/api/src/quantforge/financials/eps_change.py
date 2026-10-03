"""Descriptive EPS change over an AVAILABLE comparative EPS observation.

This is a pure transformation of a :class:`~quantforge.financials.comparative_eps.ComparativeEpsObservation`
(current EPS plus **comparative EPS as presented in this filing**). It retrieves nothing, selects
nothing and reads no clock: the comparative observation is the whole evidence envelope.

What it produces, and what it never is
--------------------------------------
* ``eps_change = current_eps - comparative_eps``: SIGNED, exact ``Decimal`` (no ``abs``). Not named
  "absolute" and not "growth".
* ``transition``: ONE explicit :class:`EpsTransition` from the signs of the two values and the direction
  of the move. It carries the economic reading for loss/zero bases, where a percentage misleads.
* ``percentage_change = (current_eps - comparative_eps) / comparative_eps``: a **ratio** (``0.5`` means
  +50%; never multiplied by 100, matching the repo's ``surprise_ratio`` convention), defined **only for a
  positive comparative**. Zero and negative bases give ``None`` plus a typed
  :class:`PercentageUnavailableReason`; ``abs(comparative)`` is never used, because it would call a
  narrowing loss "positive growth". For a positive base the ratio stays mechanically defined whatever the
  current sign (``1.00 -> -0.50`` is ``-1.5``).

It is not an earnings surprise (no estimate, no consensus, no beat/miss), not a signal (no direction
-to-trade mapping, no score, no threshold) and says nothing about when a fact was known (no
availability, no acceptance time, no clock). Nothing is persisted.

Decimal arithmetic policy
-------------------------
Every operation runs in an explicit, isolated context built fresh per call by :func:`eps_change_context`
(never the process-global context, which is neither read nor mutated, and never a shared object whose flags
could accumulate): 50 significant digits, ``ROUND_HALF_EVEN``, and traps for invalid operation, division by
zero and overflow. Stored EPS values fit NUMERIC(28,4) (<= 28 digits), so the subtraction is exact; its
context also traps ``Inexact``, so an out-of-envelope input raises ``ValueError`` instead of rounding
silently. A zero difference is normalised to ``+0``. Division can repeat (``-1/3``) and is correctly rounded to 50
significant digits by that context; the domain result is **not quantized** to presentation digits and is
never formatted, so display rounding belongs to a later layer. There is no float anywhere.

The 50 digits are an *internal, deterministic calculation precision* chosen to comfortably exceed the
persisted ``FinancialFact`` NUMERIC(28,4) input envelope and keep useful precision for non-terminating
ratios. They are not a requirement or a property of the SEC, FSDS or PostgreSQL. The input-envelope rejection
(a value that would force inexact subtraction raises ``ValueError``) is a domain-contract defence for
manually constructed values; real persisted FSDS values are not expected to reach it. One calculation
policy serves both the transformer and the constructor's re-derivation (the same ``_signed_change`` and
``_percentage``), so the two can never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
)
from enum import StrEnum
from typing import Final

from quantforge.financials.comparative_eps import ComparativeEpsObservation, ComparativeEpsStatus

__all__ = [
    "EPS_CHANGE_PRECISION",
    "EpsChangeObservation",
    "EpsChangeStatus",
    "EpsTransition",
    "PercentageUnavailableReason",
    "classify_transition",
    "compute_eps_change",
    "eps_change_context",
]

EPS_CHANGE_PRECISION: Final = 50


def eps_change_context(*, exact: bool = False) -> Context:
    """A fresh, isolated arithmetic context (never installed as the thread's context).

    ``exact=True`` additionally traps ``Inexact``: used for subtraction, which must never round.
    """
    traps = [InvalidOperation, DivisionByZero, Overflow]
    if exact:
        traps.append(Inexact)
    return Context(
        prec=EPS_CHANGE_PRECISION,
        rounding=ROUND_HALF_EVEN,
        Emin=-999_999,
        Emax=999_999,
        capitals=1,
        clamp=0,
        flags=[],
        traps=traps,
    )


class EpsChangeStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    #: The comparative observation is not AVAILABLE; it already explains why. No arithmetic is done.
    INPUT_UNAVAILABLE = "INPUT_UNAVAILABLE"


class PercentageUnavailableReason(StrEnum):
    """Why ``percentage_change`` is ``None`` although ``eps_change`` and ``transition`` exist."""

    #: The comparative is zero: there is no "percent above nothing". Not infinity, not 0%.
    ZERO_COMPARATIVE = "ZERO_COMPARATIVE"
    #: The comparative is negative: a percentage "growth" against a loss base is economically misleading.
    NEGATIVE_COMPARATIVE = "NEGATIVE_COMPARATIVE"


class EpsTransition(StrEnum):
    """One non-overlapping numeric relationship between comparative and current EPS.

    Descriptive only. "Profit" and "loss" mean a positive and a negative EPS figure; ``LOSS_NARROWED`` and
    ``LOSS_WIDENED`` describe the loss getting smaller or larger in magnitude, not good or bad news.
    """

    POSITIVE_INCREASED = "POSITIVE_INCREASED"
    POSITIVE_DECREASED = "POSITIVE_DECREASED"
    POSITIVE_UNCHANGED = "POSITIVE_UNCHANGED"
    LOSS_NARROWED = "LOSS_NARROWED"
    LOSS_WIDENED = "LOSS_WIDENED"
    LOSS_UNCHANGED = "LOSS_UNCHANGED"
    LOSS_TO_PROFIT = "LOSS_TO_PROFIT"
    PROFIT_TO_LOSS = "PROFIT_TO_LOSS"
    ZERO_TO_PROFIT = "ZERO_TO_PROFIT"
    ZERO_TO_LOSS = "ZERO_TO_LOSS"
    PROFIT_TO_ZERO = "PROFIT_TO_ZERO"
    LOSS_TO_ZERO = "LOSS_TO_ZERO"
    ZERO_UNCHANGED = "ZERO_UNCHANGED"


def _same(actual: Decimal, expected: Decimal) -> bool:
    """Canonical equality: same value AND same sign/digits/exponent as the transformer emits."""
    return actual == expected and actual.as_tuple() == expected.as_tuple()


def _require_decimal(value: object, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        message = f"{name} must be an exact finite Decimal, got {type(value).__name__}"
        raise ValueError(message)
    return value


def classify_transition(*, current: Decimal, comparative: Decimal) -> EpsTransition:  # noqa: PLR0911
    """The single transition between two exact EPS values. Zero (including ``-0``) is one class."""
    cur = _require_decimal(current, "current")
    cmp = _require_decimal(comparative, "comparative")
    if cmp > 0:
        if cur > 0:
            if cur > cmp:
                return EpsTransition.POSITIVE_INCREASED
            return (
                EpsTransition.POSITIVE_DECREASED if cur < cmp else EpsTransition.POSITIVE_UNCHANGED
            )
        return EpsTransition.PROFIT_TO_ZERO if cur == 0 else EpsTransition.PROFIT_TO_LOSS
    if cmp < 0:
        if cur > 0:
            return EpsTransition.LOSS_TO_PROFIT
        if cur == 0:
            return EpsTransition.LOSS_TO_ZERO
        # both negative: compare magnitudes through the signed values (no abs)
        if cur > cmp:
            return EpsTransition.LOSS_NARROWED
        return EpsTransition.LOSS_WIDENED if cur < cmp else EpsTransition.LOSS_UNCHANGED
    if cur > 0:
        return EpsTransition.ZERO_TO_PROFIT
    return EpsTransition.ZERO_TO_LOSS if cur < 0 else EpsTransition.ZERO_UNCHANGED


def _signed_change(current: Decimal, comparative: Decimal) -> Decimal:
    context = eps_change_context(exact=True)
    try:
        difference = context.subtract(current, comparative)
        return context.plus(difference) if difference == 0 else difference  # -0 -> +0
    except DecimalException as exc:
        message = f"EPS values outside the exact {EPS_CHANGE_PRECISION}-digit arithmetic envelope: {type(exc).__name__}"
        raise ValueError(message) from exc


def _percentage(
    difference: Decimal, comparative: Decimal
) -> tuple[Decimal | None, PercentageUnavailableReason | None]:
    """``difference / comparative`` for a positive comparative; ``difference`` is the exact change."""
    if comparative > 0:
        try:
            ratio = eps_change_context().divide(difference, comparative)
        except DecimalException as exc:  # pragma: no cover - see below
            # Unreachable for any exact ``difference``: the ratio's magnitude is bounded by the
            # 50-digit exactness of the subtraction, far inside the exponent range. Defensive only.
            message = f"percentage_change is not computable: {type(exc).__name__}"
            raise ValueError(message) from exc
        return ratio, None
    if comparative == 0:
        return None, PercentageUnavailableReason.ZERO_COMPARATIVE
    return None, PercentageUnavailableReason.NEGATIVE_COMPARATIVE


@dataclass(frozen=True, slots=True)
class EpsChangeObservation:
    """EPS change over one comparative observation, or ``INPUT_UNAVAILABLE`` with nothing computed.

    ``comparative`` is the evidence envelope (current observation, the comparative fact, why anything
    was refused). The other fields exist only when ``status`` is ``AVAILABLE`` and are re-derived and
    verified by the constructor, so no contradictory combination can be built.
    """

    comparative: ComparativeEpsObservation
    status: EpsChangeStatus
    eps_change: Decimal | None = None
    transition: EpsTransition | None = None
    percentage_change: Decimal | None = None
    percentage_unavailable_reason: PercentageUnavailableReason | None = None

    def __post_init__(self) -> None:
        available = self.comparative.status is ComparativeEpsStatus.AVAILABLE
        if (self.status is EpsChangeStatus.AVAILABLE) != available:
            message = "AVAILABLE holds exactly when the comparative observation is AVAILABLE"
            raise ValueError(message)
        if not available:
            if (
                self.eps_change is not None
                or self.transition is not None
                or self.percentage_change is not None
                or self.percentage_unavailable_reason is not None
            ):
                message = "INPUT_UNAVAILABLE must carry no change, transition or percentage"
                raise ValueError(message)
            return
        current = _require_decimal(self.comparative.current.value, "current EPS")
        comparative = _require_decimal(self.comparative.comparative_value, "comparative EPS")
        change = _require_decimal(self.eps_change, "eps_change")
        expected_change = _signed_change(current, comparative)
        if not _same(change, expected_change):
            message = "eps_change must equal current - comparative (canonical, zero as +0)"
            raise ValueError(message)
        if self.transition is not classify_transition(current=current, comparative=comparative):
            message = "transition contradicts the current and comparative values"
            raise ValueError(message)
        expected_percentage, expected_reason = _percentage(expected_change, comparative)
        if self.percentage_unavailable_reason is not expected_reason:
            message = "percentage_unavailable_reason contradicts the comparative sign"
            raise ValueError(message)
        if expected_percentage is None:
            if self.percentage_change is not None:
                message = "percentage_change exists only for a positive comparative"
                raise ValueError(message)
        elif not _same(
            _require_decimal(self.percentage_change, "percentage_change"), expected_percentage
        ):
            message = "percentage_change must equal (current - comparative) / comparative"
            raise ValueError(message)


def compute_eps_change(comparative: ComparativeEpsObservation) -> EpsChangeObservation:
    """Describe the EPS change in ``comparative``, or ``INPUT_UNAVAILABLE`` if it is not AVAILABLE.

    Composes the comparative selector's output; it never retrieves, selects or reconciles facts.
    """
    if comparative.status is not ComparativeEpsStatus.AVAILABLE:
        return EpsChangeObservation(
            comparative=comparative, status=EpsChangeStatus.INPUT_UNAVAILABLE
        )
    current = _require_decimal(comparative.current.value, "current EPS")
    prior = _require_decimal(comparative.comparative_value, "comparative EPS")
    difference = _signed_change(current, prior)
    percentage, reason = _percentage(difference, prior)
    return EpsChangeObservation(
        comparative=comparative,
        status=EpsChangeStatus.AVAILABLE,
        eps_change=difference,
        transition=classify_transition(current=current, comparative=prior),
        percentage_change=percentage,
        percentage_unavailable_reason=reason,
    )
