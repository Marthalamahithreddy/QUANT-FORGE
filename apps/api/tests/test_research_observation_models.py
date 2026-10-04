"""Step 10.8A: observation contract invariants and structural isolation (no database)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from quantforge.models.enums import PriceBasis, SecFormType
from quantforge.observation.models import (
    AccountingSection,
    AccountingState,
    DisclosureEvidence,
    DisclosureEvidenceLevel,
    FilingLink,
    FilingLinkStatus,
    ObservationParameters,
    PeriodVerification,
    PointInTimeSection,
    derive_disclosure_evidence_level,
)
from quantforge.research.availability import PointInTimeEligibility

SRC = Path(__file__).resolve().parents[1] / "src" / "quantforge"
OBSERVATION_FILES = sorted((SRC / "observation").glob("*.py"))
ACC = "0000320193-24-000010"


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


@pytest.mark.parametrize(
    ("form", "items", "level"),
    [
        (SecFormType.FORM_8K, "2.02,9.01", DisclosureEvidenceLevel.D1_ITEM_2_02_DECLARED),
        (SecFormType.FORM_8K_A, " 2.02 ", DisclosureEvidenceLevel.D1_ITEM_2_02_DECLARED),
        (SecFormType.FORM_8K, "12.02,5.02", DisclosureEvidenceLevel.D0_FILING_EXISTS),
        (SecFormType.FORM_8K, "2.020", DisclosureEvidenceLevel.D0_FILING_EXISTS),
        (SecFormType.FORM_8K, None, DisclosureEvidenceLevel.D0_FILING_EXISTS),
        (SecFormType.FORM_10Q, "2.02", DisclosureEvidenceLevel.D0_FILING_EXISTS),
    ],
)
def test_d1_requires_an_8k_family_form_and_the_exact_item_token(
    form: SecFormType, items: str | None, level: DisclosureEvidenceLevel
) -> None:
    assert derive_disclosure_evidence_level(form, items) is level


def test_evidence_constructor_rejects_a_level_contradicting_stored_metadata() -> None:
    with pytest.raises(ValueError, match="contradicts"):
        DisclosureEvidence(
            DisclosureEvidenceLevel.D1_ITEM_2_02_DECLARED, ACC, SecFormType.FORM_8K, "5.02"
        )
    with pytest.raises(ValueError, match="contradicts"):
        DisclosureEvidence(
            DisclosureEvidenceLevel.D0_FILING_EXISTS, ACC, SecFormType.FORM_8K, "2.02"
        )
    with pytest.raises(ValueError, match="no filing"):
        DisclosureEvidence(DisclosureEvidenceLevel.NONE, ACC, None, None)


def test_link_invariants_make_contradictory_states_unconstructible() -> None:
    with pytest.raises(ValueError, match="requested"):
        FilingLink(
            FilingLinkStatus.NOT_LINKED, PeriodVerification.NOT_APPLICABLE, ACC, None, None, "r"
        )
    with pytest.raises(ValueError, match="PERIOD_UNVERIFIED applies"):
        FilingLink(
            FilingLinkStatus.CIK_MISMATCH_REFUSED,
            PeriodVerification.PERIOD_UNVERIFIED,
            ACC,
            None,
            None,
            "r",
        )
    with pytest.raises(ValueError, match="form is carried"):
        FilingLink(
            FilingLinkStatus.CIK_MISMATCH_REFUSED,
            PeriodVerification.NOT_APPLICABLE,
            ACC,
            None,
            SecFormType.FORM_10Q,
            "r",
        )


def test_there_is_no_verified_period_state() -> None:
    assert {m.value for m in PeriodVerification} == {"NOT_APPLICABLE", "PERIOD_UNVERIFIED"}


def test_accounting_and_pit_sections_reject_masquerading() -> None:
    with pytest.raises(ValueError, match="exactly when accounting"):
        AccountingSection(AccountingState.EVALUATED)
    with pytest.raises(ValueError, match="exactly when accounting"):
        AccountingSection(AccountingState.LINK_REFUSED, change=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exactly when availability"):
        PointInTimeSection(PointInTimeEligibility.ELIGIBLE)
    with pytest.raises(ValueError, match="always evaluated"):
        PointInTimeSection(PointInTimeEligibility.NOT_APPLICABLE)


def test_parameters_require_explicit_eps_selection_only_with_an_accession() -> None:
    assert ObservationParameters(horizons=(1, 5, 20)).price_basis is PriceBasis.SPLIT_ADJUSTED
    with pytest.raises(ValueError, match="requires an explicit"):
        ObservationParameters(horizons=(1,), filing_accession=ACC)
    with pytest.raises(ValueError, match="horizon"):
        ObservationParameters(horizons=(0,))
    with pytest.raises(ValueError, match="research price basis"):
        ObservationParameters(horizons=(1,), price_basis=PriceBasis.AS_TRADED)


@pytest.mark.parametrize("path", OBSERVATION_FILES, ids=lambda p: p.name)
def test_observation_modules_do_arithmetic_on_nothing_and_never_use_float(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    arithmetic = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.BinOp)
        and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div))
        # string / tuple concatenation is not financial arithmetic
        and not isinstance(n.left, (ast.Constant, ast.Tuple, ast.List))
    ]
    assert arithmetic == [], "return/EPS math belongs to the existing layers"
    floats = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "float"
    ]
    assert floats == []


FORBIDDEN = (
    "quantforge.signals",
    "quantforge.backtest",
    "quantforge.market",
    "quantforge.providers",
    "quantforge.ingestion",
    "quantforge.api",
    "httpx",
    "requests",
    "socket",
    "urllib",
)


@pytest.mark.parametrize("path", OBSERVATION_FILES, ids=lambda p: p.name)
def test_observation_has_no_signal_execution_provider_ingestion_or_network_dependency(
    path: Path,
) -> None:
    offenders = {
        n for n in _imports(path) if any(n == bad or n.startswith(f"{bad}.") for bad in FORBIDDEN)
    }
    assert offenders == set()


def test_observation_never_touches_acceptance_time_or_writes() -> None:
    for path in [*OBSERVATION_FILES, SRC / "repositories" / "filing.py"]:
        text = path.read_text(encoding="utf-8")
        assert "accepted_at" not in text and "source_accepted" not in text, path
        for forbidden in (".add(", ".commit(", ".flush(", ".delete(", "sa.insert", "sa.update"):
            assert forbidden not in text, (path, forbidden)
        for word in ("BUY", "SELL", "bullish", "bearish", "recommend"):
            assert word not in text, (path, word)


def test_only_the_read_only_api_surface_consumes_the_observation_layer() -> None:
    """Research, financials, signals, backtest, market, identity, ingestion and providers sit BELOW it."""
    offenders = []
    for path in SRC.rglob("*.py"):
        if "observation" in path.parts:
            continue
        if any(n.startswith("quantforge.observation") for n in _imports(path)):
            offenders.append(str(path.relative_to(SRC)))
    assert set(offenders) <= {
        "api/v1/research_observations.py",
        "api/v1/research_observation_presenter.py",
        "schemas/research_observation.py",
    }, offenders
