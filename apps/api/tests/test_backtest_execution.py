"""Unit tests for execution-eligibility assessment.

:func:`assess_execution_eligibility` is pure (no database, no outcome objects),
so every test builds an :class:`~quantforge.signals.eps_surprise.EpsSurpriseSignal`
in memory. Boundary tests use the real
:class:`~quantforge.market.ExchangeCalendarsSessionCalendar` so the session
opens are authoritative calendar output, never hand-invented clocks.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

import quantforge.backtest as backtest_package
from quantforge.backtest.contracts import (
    ExecutionEligibility,
    ExecutionEligibilityStatus,
    ExecutionPriceType,
)
from quantforge.backtest.execution import assess_execution_eligibility
from quantforge.market import ExchangeCalendarsSessionCalendar
from quantforge.models.enums import EarningsTiming
from quantforge.research.availability import PointInTimeEligibility, ResearchMode
from quantforge.signals.eps_surprise import (
    EpsSurpriseDirection,
    EpsSurpriseSignal,
    EpsSurpriseSignalStatus,
    SignalType,
)

_CALENDAR = ExchangeCalendarsSessionCalendar()
Status = ExecutionEligibilityStatus

# Friday 2024-03-15: XNYS opens 13:30Z (EDT). The next session is Monday 03-18, also 13:30Z.
_FRIDAY = date(2024, 3, 15)
_FRIDAY_OPEN = datetime(2024, 3, 15, 13, 30, tzinfo=UTC)
_MONDAY = date(2024, 3, 18)
_MONDAY_OPEN = datetime(2024, 3, 18, 13, 30, tzinfo=UTC)


def _signal(**overrides: object) -> EpsSurpriseSignal:
    fields: dict[str, object] = {
        "signal_type": SignalType.EPS_SURPRISE,
        "event_id": uuid.uuid4(),
        "company_id": uuid.uuid4(),
        "security_id": uuid.uuid4(),
        "listing_id": uuid.uuid4(),
        "research_mode": ResearchMode.POINT_IN_TIME,
        "point_in_time_eligibility": PointInTimeEligibility.ELIGIBLE,
        "event_date": _FRIDAY,
        "timing": EarningsTiming.BEFORE_MARKET,
        "observed_at": _FRIDAY_OPEN - timedelta(hours=2),
        "exchange_mic": "XNYS",
        "effective_session": _FRIDAY,
        "status": EpsSurpriseSignalStatus.AVAILABLE,
        "direction": EpsSurpriseDirection.POSITIVE,
        "signal_value": Decimal("0.2000"),
        "reported_eps": Decimal("1.2000"),
        "estimated_eps": Decimal("1.0000"),
        "absolute_surprise": Decimal("0.2000"),
        "surprise_ratio": Decimal("0.2000"),
        "reason": "test signal",
    }
    fields.update(overrides)
    return EpsSurpriseSignal(**fields)  # type: ignore[arg-type]


def _assess(signal: EpsSurpriseSignal) -> ExecutionEligibility:
    return assess_execution_eligibility(signal, _CALENDAR)


def _assert_no_candidate(result: ExecutionEligibility) -> None:
    assert result.candidate_execution_session is None
    assert result.candidate_execution_price_type is None
    assert result.candidate_execution_open_at is None


# ── classification refusals (no clock consulted) ────────────────────────────


def test_descriptive_signal_is_never_executable_even_with_perfect_timing() -> None:
    result = _assess(
        _signal(
            research_mode=ResearchMode.DESCRIPTIVE,
            point_in_time_eligibility=PointInTimeEligibility.NOT_APPLICABLE,
        )
    )

    assert result.status is Status.NOT_POINT_IN_TIME_SIGNAL
    _assert_no_candidate(result)


def test_point_in_time_ineligible_signal_is_not_executable() -> None:
    result = _assess(
        _signal(
            point_in_time_eligibility=PointInTimeEligibility.INELIGIBLE_NO_AVAILABILITY_EVIDENCE,
            status=EpsSurpriseSignalStatus.NOT_POINT_IN_TIME_ELIGIBLE,
            observed_at=None,
            direction=None,
            signal_value=None,
            effective_session=None,
        )
    )

    assert result.status is Status.NOT_POINT_IN_TIME_ELIGIBLE
    _assert_no_candidate(result)


@pytest.mark.parametrize(
    "status",
    [
        EpsSurpriseSignalStatus.MISSING_REPORTED_EPS,
        EpsSurpriseSignalStatus.MISSING_ESTIMATED_EPS,
        EpsSurpriseSignalStatus.UNAVAILABLE_ZERO_ESTIMATE,
        EpsSurpriseSignalStatus.NO_EFFECTIVE_SESSION,
    ],
)
def test_unavailable_signal_is_not_executable(status: EpsSurpriseSignalStatus) -> None:
    result = _assess(
        _signal(status=status, direction=None, signal_value=None, effective_session=None)
    )

    assert result.status is Status.SIGNAL_UNAVAILABLE
    assert result.direction is None
    assert result.signal_value is None
    _assert_no_candidate(result)


def test_unknown_timing_stays_unsupported_even_with_a_perfect_observed_at() -> None:
    result = _assess(_signal(timing=EarningsTiming.UNKNOWN))

    assert result.status is Status.UNSUPPORTED_TIMING
    _assert_no_candidate(result)


def test_during_market_stays_refused_even_with_an_authoritative_clock() -> None:
    result = _assess(_signal(timing=EarningsTiming.DURING_MARKET))

    assert result.status is Status.INSUFFICIENT_TEMPORAL_PRECISION
    _assert_no_candidate(result)


def test_timing_refusals_take_precedence_over_signal_unavailability() -> None:
    unknown = _assess(
        _signal(timing=EarningsTiming.UNKNOWN, status=EpsSurpriseSignalStatus.UNSUPPORTED_TIMING)
    )
    during = _assess(
        _signal(
            timing=EarningsTiming.DURING_MARKET, status=EpsSurpriseSignalStatus.UNSUPPORTED_TIMING
        )
    )

    assert unknown.status is Status.UNSUPPORTED_TIMING
    assert during.status is Status.INSUFFICIENT_TEMPORAL_PRECISION


# ── evidence refusals ───────────────────────────────────────────────────────


def test_malformed_eligible_signal_without_observed_at_is_refused() -> None:
    result = _assess(_signal(observed_at=None))

    assert result.status is Status.NOT_POINT_IN_TIME_ELIGIBLE
    _assert_no_candidate(result)


def test_naive_observed_at_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _assess(_signal(observed_at=datetime(2024, 3, 15, 8, 0)))  # noqa: DTZ001 -- deliberately naive


def test_missing_mic_is_refused() -> None:
    result = _assess(_signal(exchange_mic=None))

    assert result.status is Status.UNSUPPORTED_MIC
    _assert_no_candidate(result)


@pytest.mark.parametrize("timing", [EarningsTiming.BEFORE_MARKET, EarningsTiming.AFTER_MARKET])
def test_unsupported_mic_is_refused_never_defaulted_to_nyse(timing: EarningsTiming) -> None:
    result = _assess(_signal(timing=timing, exchange_mic="XLON"))

    assert result.status is Status.UNSUPPORTED_MIC
    _assert_no_candidate(result)


@pytest.mark.parametrize("mic", ["XNYS", "XNAS", "ARCX"])
def test_every_supported_mic_reaches_a_candidate(mic: str) -> None:
    result = _assess(_signal(exchange_mic=mic))

    assert result.status is Status.CANDIDATE_EXECUTION_ELIGIBLE
    assert result.exchange_mic == mic


# ── BEFORE_MARKET: SAME_SESSION_OPEN, strict < ──────────────────────────────


def test_before_market_observed_before_open_is_a_same_session_candidate() -> None:
    result = _assess(_signal(observed_at=_FRIDAY_OPEN - timedelta(hours=2)))

    assert result.status is Status.CANDIDATE_EXECUTION_ELIGIBLE
    assert result.candidate_execution_session == _FRIDAY
    assert result.candidate_execution_price_type is ExecutionPriceType.SAME_SESSION_OPEN
    assert result.candidate_execution_open_at == _FRIDAY_OPEN


def test_before_market_one_microsecond_before_open_is_eligible() -> None:
    result = _assess(_signal(observed_at=_FRIDAY_OPEN - timedelta(microseconds=1)))

    assert result.status is Status.CANDIDATE_EXECUTION_ELIGIBLE


def test_before_market_exactly_at_open_is_refused() -> None:
    result = _assess(_signal(observed_at=_FRIDAY_OPEN))

    assert result.status is Status.INFORMATION_NOT_KNOWN_BEFORE_OPEN
    _assert_no_candidate(result)


def test_before_market_after_open_is_refused_despite_the_label() -> None:
    result = _assess(_signal(observed_at=_FRIDAY_OPEN + timedelta(minutes=1)))

    assert result.status is Status.INFORMATION_NOT_KNOWN_BEFORE_OPEN
    _assert_no_candidate(result)


def test_before_market_late_observation_does_not_fall_through_to_a_later_session() -> None:
    result = _assess(_signal(observed_at=_FRIDAY_OPEN + timedelta(hours=1)))

    assert result.candidate_execution_session is None  # no forward scan


def test_before_market_event_on_a_weekend_is_not_a_trading_session() -> None:
    result = _assess(_signal(event_date=date(2024, 3, 16), observed_at=_FRIDAY_OPEN))

    assert result.status is Status.NOT_A_TRADING_SESSION
    _assert_no_candidate(result)


def test_before_market_event_outside_the_calendar_range_is_clock_unavailable() -> None:
    result = _assess(
        _signal(event_date=date(1990, 1, 2), observed_at=datetime(1990, 1, 2, tzinfo=UTC))
    )

    assert result.status is Status.SESSION_CLOCK_UNAVAILABLE
    _assert_no_candidate(result)


def test_non_utc_aware_observed_at_is_compared_by_instant() -> None:
    eastern = timezone(timedelta(hours=-4))
    before = datetime(2024, 3, 15, 9, 29, 59, tzinfo=eastern)  # 13:29:59Z
    at_open = datetime(2024, 3, 15, 9, 30, tzinfo=eastern)  # 13:30:00Z

    assert _assess(_signal(observed_at=before)).status is Status.CANDIDATE_EXECUTION_ELIGIBLE
    assert _assess(_signal(observed_at=at_open)).status is Status.INFORMATION_NOT_KNOWN_BEFORE_OPEN


def test_before_market_uses_the_dst_correct_open() -> None:
    # 2024-01-16 (EST) opens 14:30Z; 13:45Z is before that open, though it would be
    # after a (wrong) fixed 13:30Z open.
    january = _assess(
        _signal(event_date=date(2024, 1, 16), observed_at=datetime(2024, 1, 16, 13, 45, tzinfo=UTC))
    )

    assert january.status is Status.CANDIDATE_EXECUTION_ELIGIBLE
    assert january.candidate_execution_open_at == datetime(2024, 1, 16, 14, 30, tzinfo=UTC)


# ── AFTER_MARKET: NEXT_SESSION_OPEN, strict < ───────────────────────────────

_AMC_OBSERVED = datetime(2024, 3, 15, 20, 30, tzinfo=UTC)  # after the Friday close


def _amc(**overrides: object) -> EpsSurpriseSignal:
    fields: dict[str, object] = {
        "timing": EarningsTiming.AFTER_MARKET,
        "observed_at": _AMC_OBSERVED,
    }
    fields.update(overrides)
    return _signal(**fields)


def test_after_market_friday_resolves_to_monday_open() -> None:
    result = _assess(_amc())

    assert result.status is Status.CANDIDATE_EXECUTION_ELIGIBLE
    assert result.candidate_execution_session == _MONDAY
    assert result.candidate_execution_price_type is ExecutionPriceType.NEXT_SESSION_OPEN
    assert result.candidate_execution_open_at == _MONDAY_OPEN


def test_after_market_one_microsecond_before_next_open_is_eligible() -> None:
    result = _assess(_amc(observed_at=_MONDAY_OPEN - timedelta(microseconds=1)))

    assert result.status is Status.CANDIDATE_EXECUTION_ELIGIBLE


def test_after_market_exactly_at_next_open_is_refused() -> None:
    result = _assess(_amc(observed_at=_MONDAY_OPEN))

    assert result.status is Status.INFORMATION_NOT_KNOWN_BEFORE_OPEN
    _assert_no_candidate(result)


def test_after_market_after_next_open_is_refused_without_scanning_forward() -> None:
    result = _assess(_amc(observed_at=_MONDAY_OPEN + timedelta(hours=3)))

    assert result.status is Status.INFORMATION_NOT_KNOWN_BEFORE_OPEN
    _assert_no_candidate(result)


def test_after_market_does_not_require_observation_after_the_event_close() -> None:
    # Observed hours before the event session even opened: still strictly before the
    # next open, and no "observed_at >= close" precondition is imposed.
    result = _assess(_amc(observed_at=_FRIDAY_OPEN - timedelta(hours=5)))

    assert result.status is Status.CANDIDATE_EXECUTION_ELIGIBLE
    assert result.candidate_execution_session == _MONDAY


def test_after_market_holiday_boundary_selects_the_next_actual_session() -> None:
    # Wednesday 2024-11-27 AMC: Thursday is Thanksgiving, so the next session is the
    # Friday 2024-11-29 early-close day (opens 14:30Z), not the next calendar day.
    result = _assess(
        _signal(
            timing=EarningsTiming.AFTER_MARKET,
            event_date=date(2024, 11, 27),
            observed_at=datetime(2024, 11, 27, 21, 0, tzinfo=UTC),
        )
    )

    assert result.status is Status.CANDIDATE_EXECUTION_ELIGIBLE
    assert result.candidate_execution_session == date(2024, 11, 29)
    assert result.candidate_execution_open_at == datetime(2024, 11, 29, 14, 30, tzinfo=UTC)


def test_after_market_on_a_holiday_still_uses_the_calendars_next_session() -> None:
    result = _assess(
        _signal(
            timing=EarningsTiming.AFTER_MARKET,
            event_date=date(2024, 11, 28),
            observed_at=datetime(2024, 11, 28, 21, 0, tzinfo=UTC),
        )
    )

    assert result.candidate_execution_session == date(2024, 11, 29)


def test_after_market_outside_the_calendar_range_is_clock_unavailable() -> None:
    result = _assess(
        _amc(event_date=date(2099, 1, 2), observed_at=datetime(2099, 1, 2, tzinfo=UTC))
    )

    assert result.status is Status.SESSION_CLOCK_UNAVAILABLE
    _assert_no_candidate(result)


# ── the label is not proof; ingested_at/observed_at cannot be conflated ─────


def test_the_timing_label_alone_never_makes_a_signal_eligible() -> None:
    late = _signal(
        timing=EarningsTiming.BEFORE_MARKET, observed_at=_FRIDAY_OPEN + timedelta(hours=1)
    )
    assert _assess(late).status is not Status.CANDIDATE_EXECUTION_ELIGIBLE


def test_candidate_fields_are_populated_if_and_only_if_eligible() -> None:
    signals = [
        _signal(),
        _signal(observed_at=_FRIDAY_OPEN),
        _amc(),
        _amc(observed_at=_MONDAY_OPEN),
        _signal(research_mode=ResearchMode.DESCRIPTIVE),
        _signal(timing=EarningsTiming.DURING_MARKET),
        _signal(exchange_mic="XLON"),
    ]
    for signal in signals:
        result = _assess(signal)
        eligible = result.status is Status.CANDIDATE_EXECUTION_ELIGIBLE
        populated = (
            result.candidate_execution_session,
            result.candidate_execution_price_type,
            result.candidate_execution_open_at,
        )
        assert all(value is not None for value in populated) == eligible
        assert all(value is None for value in populated) == (not eligible)


# ── identity and direction are echoed, never recomputed or mapped ───────────


def test_identity_and_evidence_fields_are_echoed_unchanged() -> None:
    signal = _signal()
    result = _assess(signal)

    for name in (
        "event_id",
        "company_id",
        "security_id",
        "listing_id",
        "exchange_mic",
        "signal_type",
        "research_mode",
        "point_in_time_eligibility",
        "observed_at",
    ):
        assert getattr(result, name) == getattr(signal, name)
    assert result.reason


def test_research_effective_session_is_not_echoed_beside_the_candidate_session() -> None:
    assert "effective_session" not in ExecutionEligibility.__dataclass_fields__


def test_direction_and_signal_value_are_echoed_including_neutral() -> None:
    for direction, value in (
        (EpsSurpriseDirection.NEGATIVE, Decimal("-0.3")),
        (EpsSurpriseDirection.NEUTRAL, Decimal("0")),
    ):
        result = _assess(_signal(direction=direction, signal_value=value))
        assert result.direction is direction
        assert result.signal_value == value


def test_no_order_side_vocabulary_exists_in_the_contract() -> None:
    side_words = {"BUY", "SELL", "LONG", "SHORT"}
    names = {m.name for m in Status} | {m.name for m in ExecutionPriceType}
    names |= set(ExecutionEligibility.__dataclass_fields__)
    assert not {n for n in map(str.upper, names) if any(w in n.split("_") for w in side_words)}


def test_price_types_are_limited_to_the_two_open_candidates() -> None:
    assert {m.value for m in ExecutionPriceType} == {"SAME_SESSION_OPEN", "NEXT_SESSION_OPEN"}


def test_result_is_immutable() -> None:
    result = _assess(_signal())

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.status = Status.NOT_POINT_IN_TIME_SIGNAL  # type: ignore[misc]


# ── no price, no outcome, no look-ahead ─────────────────────────────────────


def test_assessment_accepts_only_a_signal_and_a_calendar() -> None:
    parameters = list(inspect.signature(assess_execution_eligibility).parameters)
    assert parameters == ["signal", "calendar"]


def test_eligibility_is_deterministic_for_the_same_inputs() -> None:
    signal = _signal()
    assert _assess(signal) == _assess(signal)


def test_signal_and_result_carry_no_forward_looking_field() -> None:
    forward = {
        "forward_return",
        "target_price",
        "target_session",
        "future_close",
        "benchmark_return",
        "abnormal_return",
        "close",
        "open",
        "price",
        "fill_price",
    }
    assert set(EpsSurpriseSignal.__dataclass_fields__).isdisjoint(forward)
    assert set(ExecutionEligibility.__dataclass_fields__).isdisjoint(forward)


_FORBIDDEN_PREFIXES = (
    "quantforge.signals.evaluation",
    "quantforge.signals.analytics",
    "quantforge.research.returns",
    "quantforge.research.benchmark_returns",
    "quantforge.research.abnormal_returns",
    "quantforge.research.event_returns",
    "quantforge.research.event_study",
    "quantforge.research.price_series",
    "quantforge.research.benchmark_price_series",
    "quantforge.models.market",
    "quantforge.models.benchmark",
    "exchange_calendars",
    "pandas",
    "sqlalchemy",
    "importlib",
)
_DYNAMIC_IMPORT_NAMES = {"importlib", "__import__"}


def _imports_and_dynamic_uses(source: str, module_name: str) -> tuple[set[str], set[str]]:
    """Every dotted module (and imported submodule) a file imports, with relative imports
    resolved against ``module_name``, plus any dynamic-import name it references."""
    package_parts = module_name.split(".")[:-1]
    imported: set[str] = set()
    dynamic: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base_parts = (
                package_parts[: len(package_parts) - (node.level - 1)] if node.level else []
            )
            base = ".".join([*base_parts, *([node.module] if node.module else [])])
            imported.add(base)
            imported.update(f"{base}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Name) and node.id in _DYNAMIC_IMPORT_NAMES:
            dynamic.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in _DYNAMIC_IMPORT_NAMES:
            dynamic.add(node.attr)
    return imported, dynamic


def _violations(source: str, module_name: str = "quantforge.backtest.sample") -> set[str]:
    imported, dynamic = _imports_and_dynamic_uses(source, module_name)
    forbidden = {
        name
        for name in imported
        if any(name == p or name.startswith(p + ".") for p in _FORBIDDEN_PREFIXES)
    }
    return forbidden | dynamic


@pytest.mark.parametrize(
    "source",
    [
        "from quantforge.research import returns",
        "from quantforge.signals import evaluation",
        "from quantforge.signals import analytics as a",
        "from quantforge.research.returns import compute_forward_returns",
        "import quantforge.research.abnormal_returns",
        "from ..research import benchmark_returns",
        "from ..research.event_study import analyze_earnings_event",
        "from quantforge.models.market import DailyPriceBar",
        "import exchange_calendars",
        "from pandas import Timestamp",
        "import importlib\nimportlib.import_module('x')",
        "__import__('quantforge.research.returns')",
    ],
)
def test_the_import_scanner_catches_bypass_shapes(source: str) -> None:
    assert _violations(source), source


@pytest.mark.parametrize(
    "source",
    [
        "from quantforge.market import SessionCalendar",
        "from quantforge.signals.eps_surprise import EpsSurpriseSignal",
        "from quantforge.research.availability import ResearchMode",
        "from . import contracts",
        "from .contracts import ExecutionEligibility",
    ],
)
def test_the_import_scanner_allows_legitimate_imports(source: str) -> None:
    assert _violations(source) == set()


def test_no_backtest_module_imports_an_outcome_price_or_calendar_library_module() -> None:
    package_dir = Path(inspect.getfile(backtest_package)).parent
    files = sorted(package_dir.glob("*.py"))
    assert {f.name for f in files} >= {"__init__.py", "contracts.py", "execution.py"}
    for file in files:
        module_name = f"quantforge.backtest.{file.stem}"
        assert _violations(file.read_text(), module_name) == set(), file.name
