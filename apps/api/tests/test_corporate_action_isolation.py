"""The split guard is a standalone primitive: nothing existing depends on it.

Step 8/9 research reads SPLIT_ADJUSTED prices and Step 10 execution eligibility has
no holding horizon, so a known split must change neither. The strongest regression
guard available is structural: no research, signal or backtest module may import the
split tables, the guard, or split ingestion. Wiring the guard into a fill engine is a
later, explicit step.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import quantforge
from quantforge.providers.massive import mapper

SRC = Path(quantforge.__file__).resolve().parent
FORBIDDEN = (
    "quantforge.market.corporate_actions",
    "quantforge.repositories.stock_split",
    "quantforge.models.corporate_action",
    "quantforge.ingestion.splits",
)
PROTECTED_PACKAGES = ("research", "signals", "backtest")


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


@pytest.mark.parametrize("package", PROTECTED_PACKAGES)
def test_research_signals_and_backtest_do_not_depend_on_splits(package: str) -> None:
    offenders = {
        str(path.relative_to(SRC)): sorted(name for name in _imports(path) if name in FORBIDDEN)
        for path in (SRC / package).rglob("*.py")
    }
    assert {k: v for k, v in offenders.items() if v} == {}


@pytest.mark.parametrize("package", PROTECTED_PACKAGES)
def test_protected_packages_do_not_pull_in_the_guard_through_the_market_package(
    package: str,
) -> None:
    # ``from quantforge.market import evaluate_split_guard`` would also be coupling.
    guard_names = {"evaluate_split_guard", "SplitFact", "CorporateActionGuardResult", "GuardStatus"}
    for path in (SRC / package).rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "quantforge.market":
                assert not guard_names & {alias.name for alias in node.names}, path


def test_the_guard_module_is_pure_standard_library() -> None:
    imports = _imports(SRC / "market" / "corporate_actions.py")
    third_party = {
        name.split(".")[0]
        for name in imports
        if name.split(".")[0]
        not in {"__future__", "uuid", "collections", "dataclasses", "datetime", "decimal", "enum"}
    }
    assert third_party == set(), "the guard must not touch the database, pandas or the calendar"


# ── listing identity is reference-data reconstruction, never availability evidence ──────────────

PIT_MODULES = (
    "research/availability.py",
    "research/eps_surprise.py",
    "models/earnings.py",
    "backtest/contracts.py",
    "backtest/execution.py",
    "signals/eps_surprise.py",
    "signals/evaluation.py",
)


@pytest.mark.parametrize("relative", PIT_MODULES)
def test_availability_and_signal_modules_do_not_import_listing_history(relative: str) -> None:
    offenders = {
        name
        for name in _imports(SRC / relative)
        if name.startswith(("quantforge.identity", "quantforge.models.listing_history"))
    }
    assert offenders == set(), "listing identity must never feed observed_at or a PIT claim"


# ── the legacy-FIGI relabel invariant (migration 0010) ──────────────────────────────────────────


def test_the_only_security_identifier_writer_stores_the_share_class_figi() -> None:
    """Migration 0010 relabels Massive-attributed ``FIGI`` rows as ``FIGI_SHARE_CLASS``. That is safe
    only while the sole production writer of identifiers stores ``share_class_figi``, so pin it."""
    writers = [
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if "SecurityIdentifier(" in path.read_text(encoding="utf-8")
        and path.name not in {"security.py"}  # the model's own repr
    ]
    assert writers == ["ingestion/reference.py"]

    source = (SRC / "ingestion" / "reference.py").read_text(encoding="utf-8")
    assert source.count("figi = reference.share_class_figi") >= 2
    assert "composite_figi" not in source.split("SecurityIdentifier(", 1)[1].split(")", 1)[0]
    assert "identifier_value=figi" in source


def test_the_ticker_event_date_assumption_stays_labelled_unverified_in_the_adapter() -> None:
    assert mapper.TICKER_EVENT_DATE_SEMANTICS_VERIFIED is False
