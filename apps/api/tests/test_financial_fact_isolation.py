"""Financial facts are foundation data: nothing downstream depends on them, and they depend on
nothing that decides signals, returns, execution, corporate actions or availability.

Structural (AST) guards, so a later slice cannot quietly couple facts to a signal, treat the SEC
CompanyFacts API as history, or read a filing's acceptance as a fact's availability.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import quantforge

SRC = Path(quantforge.__file__).resolve().parent

FACT_MODULES = (
    "models/financial_fact.py",
    "ingestion/financial_facts.py",
    "providers/sec/fsds.py",
    "repositories/financial_fact.py",
)
FORBIDDEN_FOR_FACTS = (
    "quantforge.signals",
    "quantforge.backtest",
    "quantforge.research",
    "quantforge.market",
    "quantforge.models.earnings",
    "quantforge.models.corporate_action",
    "quantforge.ingestion.splits",
    "quantforge.identity",
    "quantforge.models.listing_history",
)
FACT_PACKAGES_FORBIDDEN_TO_IMPORT_THEM = ("research", "signals", "backtest", "market", "identity")
FACT_MODULE_PATHS = (
    "quantforge.models.financial_fact",
    "quantforge.ingestion.financial_facts",
    "quantforge.providers.sec.fsds",
    "quantforge.repositories.financial_fact",
)


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


@pytest.mark.parametrize("relative", FACT_MODULES)
def test_fact_modules_import_no_signal_return_execution_split_or_availability_code(
    relative: str,
) -> None:
    offenders = {
        name
        for name in _imports(SRC / relative)
        if any(name == bad or name.startswith(f"{bad}.") for bad in FORBIDDEN_FOR_FACTS)
    }
    assert offenders == set()


@pytest.mark.parametrize("package", FACT_PACKAGES_FORBIDDEN_TO_IMPORT_THEM)
def test_research_signals_backtest_market_and_identity_do_not_import_financial_facts(
    package: str,
) -> None:
    offenders: dict[str, list[str]] = {}
    for path in (SRC / package).rglob("*.py"):
        bad = sorted(
            name
            for name in _imports(path)
            if any(name == target or name.startswith(f"{target}.") for target in FACT_MODULE_PATHS)
        )
        if bad:
            offenders[str(path.relative_to(SRC))] = bad
    assert offenders == {}


def test_the_availability_policy_and_the_earnings_models_do_not_know_about_facts() -> None:
    for relative in ("research/availability.py", "models/earnings.py", "models/enums.py"):
        assert not any("financial_fact" in name for name in _imports(SRC / relative)), relative


def test_no_fact_module_converts_a_value_through_float() -> None:
    for relative in FACT_MODULES:
        tree = ast.parse((SRC / relative).read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "float"
        ]
        assert calls == [], f"{relative} must never use float()"


def test_fact_modules_make_no_network_request_and_nothing_reads_the_companyfacts_api() -> None:
    network = {"httpx", "httpx2", "requests", "urllib", "socket", "aiohttp"}
    for relative in FACT_MODULES:
        offenders = {name for name in _imports(SRC / relative) if name.split(".")[0] in network}
        assert offenders == set(), relative
    # CompanyFacts is an as-of-now aggregate: unsafe as history. No code anywhere reads it.
    for path in SRC.rglob("*.py"):
        assert "api/xbrl/companyfacts" not in path.read_text(encoding="utf-8"), path


def test_fact_ingestion_does_not_touch_the_sec_acceptance_semantics() -> None:
    ingestion = (SRC / "ingestion/financial_facts.py").read_text(encoding="utf-8")
    assert "parse_acceptance_datetime" not in ingestion
    assert "AvailabilityBasis" not in ingestion
    assert "APPROVED_AVAILABILITY_BASES" not in ingestion


PARSERS = {"strptime", "fromisoformat", "to_datetime", "parse", "parse_acceptance_datetime"}


def test_the_fsds_accepted_text_is_read_by_nothing_but_its_parser_canonical_type_writer_and_model() -> (
    None
):
    """Widening or changing how ``accepted`` is stored must never turn it into evidence."""
    allowed = {
        "providers/sec/fsds.py",
        "ingestion/canonical.py",
        "ingestion/financial_facts.py",
        "models/financial_fact.py",
    }
    readers = []
    for path in SRC.rglob("*.py"):
        relative = str(path.relative_to(SRC))
        text = path.read_text(encoding="utf-8")
        if ("source_accepted" in text or "source_stated_accepted" in text) and (
            relative not in allowed
        ):
            readers.append(relative)
    assert readers == []
    # and the stored text is never parsed into a datetime anywhere it is handled
    for relative in allowed:
        tree = ast.parse((SRC / relative).read_text(encoding="utf-8"))
        parses = [
            n
            for n in ast.walk(tree)
            if (isinstance(n, ast.Attribute) and n.attr in PARSERS)
            or (isinstance(n, ast.Name) and n.id in PARSERS)
        ]
        assert parses == [], relative
