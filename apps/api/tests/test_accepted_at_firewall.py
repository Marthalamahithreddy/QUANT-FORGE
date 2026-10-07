"""``SecFiling.accepted_at`` is filing-acceptance provenance only (step 11.2H hard invariant).

Structural tests: it never flows into earnings ``observed_at``, ``known_as_of``, BMO/AMC, sessions,
signals, reproducibility or any API, and the EDGAR-header parser stays apart from FSDS.
"""

from __future__ import annotations

import ast
from pathlib import Path

import quantforge
from quantforge.models.enums import AvailabilityBasis
from quantforge.research.availability import APPROVED_AVAILABILITY_BASES

_SRC = Path(quantforge.__file__).resolve().parent

#: Packages that must never read the acceptance instant (as code, not prose).
_FORBIDDEN_PACKAGES = (
    "research",
    "signals",
    "reproducibility",
    "observation",
    "backtest",
    "api",
    "schemas",
    "research_records",
    "market",
    "identity",
    "cache",
)
#: The only production modules that touch ``accepted_at`` as code.
_ALLOWED_CODE_USES = {
    "ingestion/canonical.py",  # CanonicalSecFiling.accepted_at (None from bulk ingestion)
    "ingestion/filings.py",  # the identity insert writes it as None
    "ingestion/filing_acceptance.py",  # the on-demand header service (NULL -> instant)
    "ingestion/cli.py",  # prints the established instant of the one accession
    "providers/sec/mapper.py",  # passes accepted_at=None: the JSON never supplies it
    "models/filing.py",  # the column
    "providers/sec/header.py",  # FilingHeader.accepted_at
    "repositories/financial_fact.py",  # FactTrace.filing_accepted_at (provenance read, unused downstream)
}


def _code_uses(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {"accepted_at", "filing_accepted_at"}:
            return True
        if isinstance(node, ast.Name) and node.id in {"accepted_at", "filing_accepted_at"}:
            return True
        if isinstance(node, ast.keyword) and node.arg in {"accepted_at", "filing_accepted_at"}:
            return True
        if isinstance(node, ast.arg) and node.arg in {"accepted_at", "filing_accepted_at"}:
            return True
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id in {"accepted_at", "filing_accepted_at"}
        ):
            return True
    return False


def test_only_the_expected_modules_use_accepted_at_as_code() -> None:
    users = {str(path.relative_to(_SRC)) for path in _SRC.rglob("*.py") if _code_uses(path)}
    assert users == _ALLOWED_CODE_USES, users ^ _ALLOWED_CODE_USES


def test_no_research_signal_reproducibility_or_api_module_reads_it() -> None:
    offenders = [
        str(path.relative_to(_SRC))
        for package in _FORBIDDEN_PACKAGES
        for path in (_SRC / package).rglob("*.py")
        if _code_uses(path)
    ]
    assert offenders == []


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


def test_research_and_signal_code_never_imports_the_filing_acceptance_machinery() -> None:
    forbidden_modules = (
        "quantforge.ingestion.filing_acceptance",
        "quantforge.providers.sec.header",
    )
    offenders = [
        (str(path.relative_to(_SRC)), name)
        for package in ("research", "signals", "reproducibility", "observation", "backtest")
        for path in (_SRC / package).rglob("*.py")
        for name in _imported_modules(path)
        if name.startswith(forbidden_modules) or name == "quantforge.models.filing.SecFiling"
    ]
    assert offenders == []


def test_the_earnings_availability_basis_stays_source_publication_only() -> None:
    assert AvailabilityBasis.SEC_FILING_ACCEPTANCE not in APPROVED_AVAILABILITY_BASES
    assert set(APPROVED_AVAILABILITY_BASES) == {AvailabilityBasis.SOURCE_PUBLICATION}


def test_the_header_parser_is_kept_apart_from_fsds() -> None:
    fsds = _SRC / "providers/sec/fsds.py"
    assert not any("sec.header" in name for name in _imported_modules(fsds))
    assert not _code_uses(fsds), "FSDS accepted text is separate verbatim evidence"
    header = _SRC / "providers/sec/header.py"
    assert not any("fsds" in name for name in _imported_modules(header))


def test_the_submissions_mapper_never_sets_an_acceptance_instant() -> None:
    tree = ast.parse((_SRC / "providers/sec/mapper.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "accepted_at":
            assert isinstance(node.value, ast.Constant) and node.value.value is None
    assert not any(
        "zoneinfo" in name or name == "datetime.datetime"
        for name in _imported_modules(_SRC / "providers/sec/mapper.py")
    )
