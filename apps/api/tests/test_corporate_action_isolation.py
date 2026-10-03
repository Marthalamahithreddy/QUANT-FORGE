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
