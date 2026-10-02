"""Runtime import isolation: modules that must not depend on outcomes do not load them.

An AST scan proves what a file *imports*; it cannot see what a package
``__init__`` pulls in as a side effect. These tests start a clean interpreter
per target and inspect ``sys.modules``, so they hold regardless of how the
import happens.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from quantforge import market, research, signals
from quantforge.research import EventStudyResult
from quantforge.signals import SignalCohortSummary

#: Modules that consume or compute realized outcomes (returns, benchmark and
#: abnormal returns, the event study, signal evaluation/analytics) or read
#: price series for them.
_OUTCOME_MODULES = [
    "quantforge.research.returns",
    "quantforge.research.event_returns",
    "quantforge.research.benchmark_returns",
    "quantforge.research.abnormal_returns",
    "quantforge.research.event_study",
    "quantforge.research.price_series",
    "quantforge.research.benchmark_price_series",
    "quantforge.signals.evaluation",
    "quantforge.signals.analytics",
]

#: Third-party calendar machinery that only the adapter module may load.
_CALENDAR_LIBRARIES = ["exchange_calendars", "pandas"]

_PROBE = """
import importlib, json, sys
importlib.import_module({target!r})
print(json.dumps([m for m in {outcomes!r} if m in sys.modules]))
"""


def _outcome_modules_loaded_by(target: str) -> list[str]:
    result = subprocess.run(
        [sys.executable, "-c", _PROBE.format(target=target, outcomes=_OUTCOME_MODULES)],
        capture_output=True,
        text=True,
        check=True,
    )
    loaded: list[str] = json.loads(result.stdout)
    return loaded


@pytest.mark.parametrize(
    "target",
    [
        "quantforge.signals.eps_surprise",
        "quantforge.backtest",
        "quantforge.backtest.execution",
        "quantforge.backtest.contracts",
        "quantforge.market",
        "quantforge.research.alignment",
        "quantforge.research.availability",
    ],
)
def test_importing_generation_and_execution_modules_loads_no_outcome_module(target: str) -> None:
    assert _outcome_modules_loaded_by(target) == []


@pytest.mark.parametrize(
    "target",
    [
        "quantforge.backtest",
        "quantforge.backtest.execution",
        "quantforge.market",
        "quantforge.market.sessions",
        "quantforge.signals.eps_surprise",
    ],
)
def test_only_the_calendar_adapter_loads_the_calendar_libraries(target: str) -> None:
    probe = (
        "import importlib, json, sys\n"
        f"importlib.import_module({target!r})\n"
        f"print(json.dumps([m for m in {_CALENDAR_LIBRARIES!r} if m in sys.modules]))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert json.loads(result.stdout) == []


def test_the_adapter_module_does_load_exchange_calendars() -> None:
    probe = (
        "import importlib, json, sys\n"
        "importlib.import_module('quantforge.market.calendar')\n"
        "print(json.dumps('exchange_calendars' in sys.modules))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert json.loads(result.stdout) is True


def test_the_probe_itself_detects_a_loaded_outcome_module() -> None:
    assert "quantforge.research.returns" in _outcome_modules_loaded_by(
        "quantforge.research.returns"
    )


# ── the lazy package API still behaves like an eager one ────────────────────


def test_package_level_exports_still_resolve() -> None:
    assert research.ResearchMode.POINT_IN_TIME.value == "POINT_IN_TIME"
    assert callable(research.compute_forward_returns)
    assert signals.SignalType.EPS_SURPRISE.value == "EPS_SURPRISE"
    assert callable(signals.evaluate_eps_surprise_signal)
    for package in (research, signals):
        assert set(package.__all__) <= set(dir(package))


def test_from_import_of_a_lazy_export_works() -> None:
    assert EventStudyResult.__name__ == "EventStudyResult"
    assert SignalCohortSummary.__name__ == "SignalCohortSummary"


def test_unknown_package_attribute_raises_attribute_error() -> None:
    for package in (research, signals):
        with pytest.raises(AttributeError, match="no attribute"):
            package.does_not_exist  # noqa: B018


def test_market_adapter_exports_resolve_lazily_and_unknown_names_fail() -> None:
    assert market.ExchangeCalendarsSessionCalendar.__name__ == "ExchangeCalendarsSessionCalendar"
    assert "XNYS" in market.SUPPORTED_MICS
    assert set(market.__all__) <= set(dir(market))
    with pytest.raises(AttributeError, match="no attribute"):
        market.does_not_exist  # noqa: B018
