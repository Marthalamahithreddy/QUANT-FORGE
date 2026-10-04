"""Descriptive EPS change over comparative observations: arithmetic, transitions, invariants.

Observations are produced by composing the real current/comparative selectors, so this layer is
tested exactly as it is used. No database, no float, no clock.
"""

from __future__ import annotations

import ast
import dataclasses
import random
from decimal import (
    ROUND_DOWN,
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    getcontext,
    localcontext,
)
from pathlib import Path

import pytest

import quantforge
from quantforge import financials
from quantforge.financials import eps_change as eps_change_module
from quantforge.financials.comparative_eps import ComparativeEpsObservation, ComparativeEpsStatus
from quantforge.financials.eps_change import (
    EPS_CHANGE_PRECISION,
    EpsChangeObservation,
    EpsChangeStatus,
    EpsTransition,
    PercentageUnavailableReason,
    _signed_change,
    classify_transition,
    compute_eps_change,
    eps_change_context,
)
from tests.test_comparative_eps import (
    CURRENT_END,
    PRIOR_END,
    current_fact,
    fact,
    prior_fact,
    select,
)

SRC = Path(quantforge.__file__).resolve().parent
T = EpsTransition
D = Decimal


def observe(current: str | Decimal, comparative: str | Decimal) -> ComparativeEpsObservation:
    obs = select(
        [current_fact(value=D(current)), prior_fact(value=D(comparative))],
    )
    assert obs.status is ComparativeEpsStatus.AVAILABLE
    return obs


def change(current: str | Decimal, comparative: str | Decimal) -> EpsChangeObservation:
    result = compute_eps_change(observe(current, comparative))
    assert result.status is EpsChangeStatus.AVAILABLE
    return result


# ── eps_change = current - comparative, signed, exact ────────────────────────────────────


@pytest.mark.parametrize(
    ("current", "comparative", "expected"),
    [
        ("1.50", "1.00", "0.50"),
        ("0.50", "1.00", "-0.50"),
        ("-0.50", "-1.00", "0.50"),
        ("-1.50", "-1.00", "-0.50"),
        ("0", "-1", "1"),
        ("-1", "0", "-1"),
    ],
)
def test_eps_change_is_the_signed_difference_current_minus_comparative(
    current: str, comparative: str, expected: str
) -> None:
    result = change(current, comparative)
    assert result.eps_change == D(expected) and isinstance(result.eps_change, Decimal)


def test_eps_change_is_never_an_absolute_value() -> None:
    assert change("0.5", "1.0").eps_change == D("-0.5")  # a decrease stays negative
    assert change("-1.5", "-1.0").eps_change == D("-0.5")


# ── the full sign matrix: every combination maps to exactly one transition ────────────────

NEG = [D("-2"), D("-1"), D("-0.5")]
ZERO = [D("0"), D("0.0"), D("-0"), D("-0.0000")]
POS = [D("0.5"), D("1"), D("2")]


def expected_transition(current: Decimal, comparative: Decimal) -> EpsTransition:
    """An independent restatement of the rules, written as a table, not as the production branches."""

    def sign(v: Decimal) -> int:
        return (v > 0) - (v < 0)

    table = {
        (1, 1): {1: T.POSITIVE_INCREASED, 0: T.POSITIVE_UNCHANGED, -1: T.POSITIVE_DECREASED},
        (1, 0): {0: T.PROFIT_TO_ZERO},
        (1, -1): {0: T.PROFIT_TO_LOSS},
        (0, 1): {0: T.ZERO_TO_PROFIT},
        (0, 0): {0: T.ZERO_UNCHANGED},
        (0, -1): {0: T.ZERO_TO_LOSS},
        (-1, 1): {0: T.LOSS_TO_PROFIT},
        (-1, 0): {0: T.LOSS_TO_ZERO},
        # both negative: a loss that moves up toward zero narrowed, down widened
        (-1, -1): {1: T.LOSS_NARROWED, 0: T.LOSS_UNCHANGED, -1: T.LOSS_WIDENED},
    }
    direction = sign(current - comparative)
    return table[(sign(comparative), sign(current))][
        direction if sign(comparative) * sign(current) > 0 else 0
    ]


@pytest.mark.parametrize("comparative", [*NEG, *ZERO, *POS])
@pytest.mark.parametrize("current", [*NEG, *ZERO, *POS])
def test_every_sign_combination_maps_to_exactly_the_expected_transition(
    comparative: Decimal, current: Decimal
) -> None:
    result = change(current, comparative)
    assert result.transition is expected_transition(current, comparative)
    assert classify_transition(current=current, comparative=comparative) is result.transition


def test_the_sign_matrix_reaches_every_transition_and_each_is_distinct() -> None:
    seen = {
        classify_transition(current=c, comparative=p)
        for c in (*NEG, *ZERO, *POS)
        for p in (*NEG, *ZERO, *POS)
    }
    assert seen == set(EpsTransition) and len(EpsTransition) == 13


@pytest.mark.parametrize(
    ("current", "comparative", "expected"),
    [
        ("1.5", "1.0", T.POSITIVE_INCREASED),
        ("0.5", "1.0", T.POSITIVE_DECREASED),
        ("1.0", "1.00", T.POSITIVE_UNCHANGED),
        (
            "-0.5",
            "-1.0",
            T.LOSS_NARROWED,
        ),  # -1.00 -> -0.50: the loss got smaller, not "+50% growth"
        ("-1.5", "-1.0", T.LOSS_WIDENED),
        ("-1.0", "-1.00", T.LOSS_UNCHANGED),
        ("0.1", "-0.1", T.LOSS_TO_PROFIT),
        ("-0.1", "0.1", T.PROFIT_TO_LOSS),
        ("0.1", "0", T.ZERO_TO_PROFIT),
        ("-0.1", "0", T.ZERO_TO_LOSS),
        ("0", "0.1", T.PROFIT_TO_ZERO),
        ("0", "-0.1", T.LOSS_TO_ZERO),
        ("0", "0", T.ZERO_UNCHANGED),
    ],
)
def test_the_named_transition_semantics(current: str, comparative: str, expected: T) -> None:
    assert change(current, comparative).transition is expected


@pytest.mark.parametrize("zero", ["0", "0.0", "-0", "-0.0000", "0E+3"])
def test_zero_of_any_spelling_including_negative_zero_is_one_zero_class(zero: str) -> None:
    assert change(zero, "0").transition is T.ZERO_UNCHANGED
    assert change(zero, "1").transition is T.PROFIT_TO_ZERO
    assert change(zero, "-1").transition is T.LOSS_TO_ZERO
    assert change("1", zero).transition is T.ZERO_TO_PROFIT
    assert change("-1", zero).transition is T.ZERO_TO_LOSS
    assert change(zero, zero).eps_change == 0


# ── percentage: a ratio, only for a positive comparative ─────────────────────────────────


def test_percentage_is_a_ratio_for_a_positive_comparative() -> None:
    result = change("1.50", "1.00")
    assert result.percentage_change == D("0.5") and result.percentage_unavailable_reason is None
    assert change("1.00", "1.00").percentage_change == 0
    assert change("0.5", "1.0").percentage_change == D("-0.5")


def test_percentage_is_defined_for_any_current_sign_against_a_positive_base() -> None:
    profit_to_loss = change("-0.50", "1.00")
    assert profit_to_loss.eps_change == D("-1.50")
    assert profit_to_loss.percentage_change == D("-1.5")
    assert profit_to_loss.transition is T.PROFIT_TO_LOSS
    to_zero = change("0", "2")
    assert to_zero.percentage_change == D("-1") and to_zero.transition is T.PROFIT_TO_ZERO


@pytest.mark.parametrize("zero", ["0", "-0", "0.0000"])
def test_a_zero_comparative_has_no_percentage_but_keeps_change_and_transition(zero: str) -> None:
    result = change("1.5", zero)
    assert result.percentage_change is None
    assert result.percentage_unavailable_reason is PercentageUnavailableReason.ZERO_COMPARATIVE
    assert result.eps_change == D("1.5") and result.transition is T.ZERO_TO_PROFIT


@pytest.mark.parametrize("current", ["-2", "-0.5", "0", "1"])
def test_a_negative_comparative_has_no_percentage_and_never_uses_abs(current: str) -> None:
    result = change(current, "-1")
    assert result.percentage_change is None
    assert result.percentage_unavailable_reason is PercentageUnavailableReason.NEGATIVE_COMPARATIVE
    assert result.eps_change == D(current) - D("-1")


def test_tiny_denominators_are_deterministic_with_no_threshold() -> None:
    assert change("0.0002", "0.0001").percentage_change == D(1)
    assert change("0.0001", "0.0001").percentage_change == 0
    tiny_negative = change("0.0001", "-0.0001")
    assert tiny_negative.transition is T.LOSS_TO_PROFIT and tiny_negative.percentage_change is None
    assert tiny_negative.eps_change == D("0.0002")
    assert change("1234567.0000", "0.0001").percentage_change == D(12345669999)


def test_a_repeating_decimal_uses_the_explicit_50_digit_context() -> None:
    result = change("2", "3")
    assert result.eps_change == D(-1)
    expected = Context(prec=50, rounding=ROUND_HALF_EVEN, traps=[], flags=[]).divide(D(-1), D(3))
    assert result.percentage_change == expected and isinstance(result.percentage_change, Decimal)
    assert len(result.percentage_change.as_tuple().digits) == 50
    assert str(result.percentage_change).startswith("-0.3333333333")
    observation = observe("2", "3")
    assert compute_eps_change(observation) == compute_eps_change(observation), "deterministic"


def test_large_values_near_the_numeric_28_4_envelope_stay_exact_decimals() -> None:
    big = D("999999999999999999999999.9999")
    result = change(-big, big)
    assert result.eps_change == D(
        "-1999999999999999999999999.9998"
    )  # 29 digits: exact, not rounded
    assert result.transition is T.PROFIT_TO_LOSS
    assert result.percentage_change == eps_change_context().divide(
        D("-1999999999999999999999999.9998"), big
    )
    assert change(big, D("0.0001")).eps_change == D("999999999999999999999999.9998")


def _derived(result: EpsChangeObservation) -> tuple[object, ...]:
    return (result.eps_change, result.transition, result.percentage_change)


def test_the_result_does_not_depend_on_or_mutate_the_global_decimal_context() -> None:
    reference = change("2", "3")
    before = (getcontext().prec, getcontext().rounding, dict(getcontext().flags))
    with localcontext() as hostile:
        hostile.prec = 3
        hostile.rounding = ROUND_DOWN
        result = change("2", "3")
        big = change("999999999999999999999999.9999", "-999999999999999999999999.9999")
        assert _derived(result) == _derived(reference), "a hostile global context changes nothing"
        assert big.eps_change == D("1999999999999999999999999.9998")
        assert (hostile.prec, hostile.rounding) == (3, ROUND_DOWN)
    assert (getcontext().prec, getcontext().rounding, dict(getcontext().flags)) == before
    fresh = eps_change_context()
    assert fresh.prec == EPS_CHANGE_PRECISION == 50 and fresh.rounding == ROUND_HALF_EVEN
    assert eps_change_context() is not fresh, (
        "a fresh context per call: no shared flags or settings"
    )


def test_the_domain_percentage_is_not_quantized_or_formatted() -> None:
    result = change("1.2345", "1.0000")
    assert result.percentage_change == D("0.2345")
    third = change("4", "3").percentage_change
    assert third is not None and len(third.as_tuple().digits) == 50, "not rounded to 2/4/6 digits"
    assert isinstance(third, Decimal)


# ── input unavailable: no arithmetic, nothing reinterpreted ──────────────────────────────


def _failing_observations() -> dict[ComparativeEpsStatus, ComparativeEpsObservation]:
    return {
        ComparativeEpsStatus.CURRENT_UNAVAILABLE: select([prior_fact()]),
        ComparativeEpsStatus.COMPARATIVE_MISSING: select([current_fact()]),
        ComparativeEpsStatus.COMPARATIVE_PERIOD_MISMATCH: select(
            [current_fact(), fact(period_end=PRIOR_END.replace(month=5, day=31))]
        ),
        ComparativeEpsStatus.COMPARATIVE_DIMENSIONAL_ONLY: select(
            [current_fact(), prior_fact(segments="ClassAxis=A;")]
        ),
        ComparativeEpsStatus.COMPARATIVE_COREG_ONLY: select(
            [current_fact(), prior_fact(coreg="G")]
        ),
        ComparativeEpsStatus.COMPARATIVE_WRONG_UNIT: select(
            [current_fact(), prior_fact(unit="CAD")]
        ),
        ComparativeEpsStatus.COMPARATIVE_NULL_VALUE: select(
            [current_fact(), prior_fact(value=None)]
        ),
        ComparativeEpsStatus.COMPARATIVE_AMBIGUOUS: select(
            [current_fact(), prior_fact(), prior_fact(taxonomy_version="us-gaap/2020")]
        ),
    }


def test_every_unavailable_comparative_is_input_unavailable_and_nothing_is_calculated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*_a: object, **_k: object) -> None:
        raise AssertionError("arithmetic was attempted on unavailable input")

    monkeypatch.setattr(eps_change_module, "_signed_change", explode)
    monkeypatch.setattr(eps_change_module, "_percentage", explode)
    monkeypatch.setattr(eps_change_module, "classify_transition", explode)
    observations = _failing_observations()
    assert set(observations) == set(ComparativeEpsStatus) - {ComparativeEpsStatus.AVAILABLE}
    for status, observation in observations.items():
        assert observation.status is status
        result = compute_eps_change(observation)
        assert result.status is EpsChangeStatus.INPUT_UNAVAILABLE
        assert result.comparative is observation, "the full evidence envelope is carried"
        assert result.comparative.status is status, "the failure is not reinterpreted"
        assert (
            result.eps_change,
            result.transition,
            result.percentage_change,
            result.percentage_unavailable_reason,
        ) == (None, None, None, None)


# ── the constructor re-derives and verifies every field ──────────────────────────────────


def test_the_observation_rejects_every_contradictory_combination() -> None:
    good = change("1.50", "1.00")
    base = observe("1.50", "1.00")
    unavailable = select([current_fact()])

    def build(**overrides: object) -> EpsChangeObservation:
        fields: dict[str, object] = {
            f.name: getattr(good, f.name) for f in dataclasses.fields(EpsChangeObservation)
        }
        fields.update(overrides)
        return EpsChangeObservation(**fields)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="AVAILABLE holds exactly when"):
        build(status=EpsChangeStatus.INPUT_UNAVAILABLE)
    with pytest.raises(ValueError, match="AVAILABLE holds exactly when"):
        build(comparative=unavailable)
    with pytest.raises(ValueError, match="must carry no change"):
        EpsChangeObservation(
            comparative=unavailable,
            status=EpsChangeStatus.INPUT_UNAVAILABLE,
            eps_change=D(1),
        )
    with pytest.raises(ValueError, match="current - comparative"):
        build(eps_change=D("-0.50"))  # comparative - current
    with pytest.raises(ValueError, match="current - comparative"):
        build(eps_change=D("0.51"))
    with pytest.raises(ValueError, match="exact finite Decimal"):
        build(eps_change=None)
    with pytest.raises(ValueError, match="exact finite Decimal"):
        build(eps_change=0.5)
    with pytest.raises(ValueError, match="transition contradicts"):
        build(transition=T.POSITIVE_DECREASED)
    with pytest.raises(ValueError, match="transition contradicts"):
        build(transition=None)
    with pytest.raises(ValueError, match="percentage_change must equal"):
        build(percentage_change=D("0.4"))
    with pytest.raises(ValueError, match="percentage_change must be an exact finite Decimal"):
        build(percentage_change=None)
    with pytest.raises(ValueError, match="contradicts the comparative sign"):
        build(percentage_unavailable_reason=PercentageUnavailableReason.ZERO_COMPARATIVE)
    zero = change("1", "0")
    with pytest.raises(ValueError, match="only for a positive comparative"):
        EpsChangeObservation(
            comparative=zero.comparative,
            status=EpsChangeStatus.AVAILABLE,
            eps_change=D(1),
            transition=T.ZERO_TO_PROFIT,
            percentage_change=D(1),
            percentage_unavailable_reason=PercentageUnavailableReason.ZERO_COMPARATIVE,
        )
    with pytest.raises(ValueError, match="contradicts the comparative sign"):
        EpsChangeObservation(
            comparative=zero.comparative,
            status=EpsChangeStatus.AVAILABLE,
            eps_change=D(1),
            transition=T.ZERO_TO_PROFIT,
            percentage_unavailable_reason=PercentageUnavailableReason.NEGATIVE_COMPARATIVE,
        )
    assert base.status is ComparativeEpsStatus.AVAILABLE


def test_the_observation_is_immutable_and_repeated_transformation_is_equal() -> None:
    observation = observe("1.53", "1.31")
    first, second = compute_eps_change(observation), compute_eps_change(observation)
    assert first == second
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.eps_change = D(0)  # type: ignore[misc]


def test_classify_transition_rejects_non_decimal_and_non_finite_input() -> None:
    for bad in (0.5, 1, "1", None, D("NaN"), D("Infinity")):
        with pytest.raises(ValueError, match="exact finite Decimal"):
            classify_transition(current=bad, comparative=D(1))  # type: ignore[arg-type]


# ── invariants over many seeded random numeric inputs ────────────────────────────────────


def test_invariants_hold_over_seeded_random_values() -> None:
    rng = random.Random(1062)
    values = [D(rng.randint(-99_999_999, 99_999_999)).scaleb(-4) for _ in range(400)]
    for _ in range(400):
        current, comparative = rng.choice(values), rng.choice(values)
        result = change(current, comparative)
        assert result.eps_change == current - comparative
        assert result.transition is expected_transition(current, comparative)
        if comparative > 0:
            assert result.percentage_unavailable_reason is None
            assert result.percentage_change is not None
            reconstructed = result.percentage_change * comparative
            assert abs(reconstructed - result.eps_change) <= D("1e-40")
        else:
            assert result.percentage_change is None and result.percentage_unavailable_reason
        if current == comparative:
            assert result.eps_change == 0
            assert result.transition in {T.POSITIVE_UNCHANGED, T.LOSS_UNCHANGED, T.ZERO_UNCHANGED}
        if comparative <= 0:
            assert result.percentage_change is None


# ── composition and structural guarantees ────────────────────────────────────────────────

FORBIDDEN_IMPORTS = (
    "quantforge.signals",
    "quantforge.research",
    "quantforge.backtest",
    "quantforge.market",
    "quantforge.identity",
    "quantforge.providers",
    "quantforge.repositories",
    "quantforge.ingestion",
    "quantforge.models",
    "quantforge.financials.eps_extraction",
    "quantforge.financials.comparative_eps_extraction",
    "sqlalchemy",
)
FORBIDDEN_NAMES = {
    "accepted_at",
    "source_accepted",
    "observed_at",
    "ingested_at",
    "available_at",
    "known_as_of",
    "utcnow",
    "today",
    "now",
    "buy",
    "sell",
    "bullish",
    "bearish",
    "signal",
    "score",
    "rank",
    "growth",
    "surprise",
    "consensus",
    "estimate",
    "beat",
    "miss",
    "improvement",
    "deterioration",
}


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def test_the_eps_change_layer_only_composes_the_comparative_observation() -> None:
    names = _imports(SRC / "financials/eps_change.py")
    assert not {
        n for n in names if any(n == bad or n.startswith(f"{bad}.") for bad in FORBIDDEN_IMPORTS)
    }
    assert "quantforge.financials.comparative_eps" in names


def test_no_float_abs_quantize_global_context_or_forbidden_vocabulary_in_the_domain_code() -> None:
    tree = ast.parse((SRC / "financials/eps_change.py").read_text(encoding="utf-8"))
    calls = {
        (n.func.id if isinstance(n.func, ast.Name) else n.func.attr)
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))
    }
    assert calls.isdisjoint({"float", "abs", "quantize", "round", "localcontext", "setcontext"})
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }
    assert not names & {"getcontext", "setcontext", "localcontext", "quantize"}
    assert not names & FORBIDDEN_NAMES
    fields = {f.name for f in dataclasses.fields(EpsChangeObservation)}
    assert not fields & FORBIDDEN_NAMES


def test_the_package_exports_resolve_lazily_and_nothing_else_imports_eps_change() -> None:
    assert financials.compute_eps_change is compute_eps_change
    assert financials.EpsTransition is EpsTransition
    offenders = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if "financials" not in path.parts
        and any(n.startswith("quantforge.financials") for n in _imports(path))
    ]
    assert set(offenders) <= {
        "api/v1/filing_eps.py",
        "api/v1/filing_eps_presenter.py",
        "schemas/filing_eps.py",
        "observation/assembler.py",
        "observation/models.py",
        "schemas/research_observation.py",
    }, (
        "only the read-only filing EPS API and the observation layer may consume the EPS change layer"
    )


def test_the_selector_inputs_used_here_match_the_fixture_dates() -> None:
    assert (observe("1", "1").current.period_end, PRIOR_END) == (CURRENT_END, PRIOR_END)


def test_a_float_or_int_percentage_is_rejected_even_when_numerically_equal() -> None:
    good = change("1.50", "1.00")
    for bad in (0.5, 1 / 2):
        with pytest.raises(ValueError, match="exact finite Decimal"):
            EpsChangeObservation(
                comparative=good.comparative,
                status=EpsChangeStatus.AVAILABLE,
                eps_change=good.eps_change,
                transition=good.transition,
                percentage_change=bad,  # type: ignore[arg-type]
            )


def test_a_zero_difference_is_normalised_to_positive_zero() -> None:
    for current, comparative in (("-0.0000", "0"), ("0", "-0"), ("-0", "-0"), ("1.5", "1.5")):
        result = change(current, comparative).eps_change
        assert result is not None
        assert result == 0
        assert not result.is_signed()


def test_input_outside_the_exact_envelope_raises_value_error_never_a_rounded_change() -> None:
    with pytest.raises(ValueError, match="outside the exact"):
        _signed_change(D("1E+60"), D("1"))
    with pytest.raises(ValueError, match="outside the exact"):
        _signed_change(D("9E+999999"), D("-9E+999999"))


def test_each_call_uses_its_own_context_so_flags_never_accumulate() -> None:
    first, second = eps_change_context(), eps_change_context()
    first.divide(D(1), D(3))  # sets the Inexact/Rounded flags on `first` only
    assert any(first.flags.values()) and not any(second.flags.values())
    assert first is not second


def test_the_domain_contains_no_float_literal_and_no_evaluative_enum_value() -> None:
    tree = ast.parse((SRC / "financials/eps_change.py").read_text(encoding="utf-8"))
    floats = [
        n for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, float)
    ]
    assert floats == []
    values = {
        e.value.lower() for e in (*EpsTransition, *PercentageUnavailableReason, *EpsChangeStatus)
    }
    banned = (
        "buy",
        "sell",
        "bull",
        "bear",
        "beat",
        "miss",
        "improv",
        "deterior",
        "growth",
        "signal",
    )
    assert not [v for v in values if any(word in v for word in banned)]


def test_the_constructor_requires_canonical_decimals_not_merely_equal_ones() -> None:
    good = change("1.00", "1.00")
    for bad in (D("-0.00"), D("0.000000"), D("-0")):
        with pytest.raises(ValueError, match="canonical"):
            EpsChangeObservation(
                comparative=good.comparative,
                status=EpsChangeStatus.AVAILABLE,
                eps_change=bad,
                transition=good.transition,
                percentage_change=good.percentage_change,
            )
    for bad_pct in (D("-0E+5"), D("0E+5"), D("0.000")):
        with pytest.raises(ValueError, match="percentage_change must equal"):
            EpsChangeObservation(
                comparative=good.comparative,
                status=EpsChangeStatus.AVAILABLE,
                eps_change=good.eps_change,
                transition=good.transition,
                percentage_change=bad_pct,
            )
    rebuilt = EpsChangeObservation(
        comparative=good.comparative,
        status=EpsChangeStatus.AVAILABLE,
        eps_change=good.eps_change,
        transition=good.transition,
        percentage_change=good.percentage_change,
    )
    assert rebuilt == good
