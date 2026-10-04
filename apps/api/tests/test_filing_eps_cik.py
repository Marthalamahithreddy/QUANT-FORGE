"""Optional registrant (CIK) qualification of ``GET /api/v1/filings/{accession}/eps``, without a DB.

Qualification is filing-identity resolution, so it must live in the extraction/repository layer and
never in an EPS accounting rule. These tests pin the contract and that boundary.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import quantforge
from quantforge.core.config import Settings
from quantforge.financials.comparative_eps import select_comparative_eps
from quantforge.financials.eps import EpsKind, EpsPeriodClass, select_eps_fact
from quantforge.financials.eps_change import compute_eps_change
from quantforge.financials.eps_extraction import resolve_eps_inputs
from quantforge.main import create_app
from quantforge.schemas.filing_eps import FilingEpsQuery

SRC = Path(quantforge.__file__).resolve().parent
ACCESSION = "0000320193-24-000010"


def test_the_cik_is_optional_and_a_strict_ten_digit_string() -> None:
    assert FilingEpsQuery(period_class="QUARTER", eps_kind="DILUTED").cik is None
    assert FilingEpsQuery(period_class="QUARTER", eps_kind="DILUTED", cik="0000320193").cik == (
        "0000320193"
    )
    for bad in ("320193", "00003201930", "000032019X", " 0000320193", ""):
        with pytest.raises(ValidationError):
            FilingEpsQuery(period_class="QUARTER", eps_kind="DILUTED", cik=bad)


def test_openapi_documents_cik_as_an_optional_exact_pattern_string_and_the_response_cik() -> None:
    spec = create_app(Settings(_env_file=None)).openapi()
    operation = spec["paths"]["/api/v1/filings/{accession}/eps"]["get"]
    params = {p["name"]: p for p in operation["parameters"]}
    cik = params["cik"]
    assert cik["in"] == "query" and cik["required"] is False
    options = cik["schema"].get("anyOf", [cik["schema"]])
    assert any(o.get("type") == "string" and o.get("pattern") == "^[0-9]{10}$" for o in options)
    assert "default" not in params["period_class"]["schema"]
    assert params["period_class"]["required"] and params["eps_kind"]["required"]
    props = spec["components"]["schemas"]["FilingEpsResponse"]["properties"]
    assert "company_cik" in props and "accession" in props
    assert {"200", "422", "503"} <= set(operation["responses"])


@pytest.mark.parametrize("bad", ["320193", "00003201930", "000032019X", "%200000320193", "abc"])
def test_a_malformed_cik_is_422_and_no_new_404_exists(bad: str) -> None:
    with TestClient(create_app(Settings(_env_file=None))) as client:
        response = client.get(
            f"/api/v1/filings/{ACCESSION}/eps",
            params={"period_class": "QUARTER", "eps_kind": "DILUTED", "cik": bad},
        )
    assert response.status_code == 422


def test_resolution_rejects_a_malformed_cik_before_any_query() -> None:
    with pytest.raises(ValueError, match="ten-digit"):
        resolve_eps_inputs(
            None,  # type: ignore[arg-type]  # never reached: validation comes first
            ACCESSION,
            period_class=EpsPeriodClass.QUARTER,
            eps_kind=EpsKind.DILUTED,
            company_cik="320193",
        )


def test_the_pure_selectors_take_no_qualifying_parameter() -> None:
    """Qualification chooses WHICH filing context enters the selectors, never HOW facts are chosen.

    ``company_cik`` on a selector is the filing's own (already resolved) identity, echoed back; it
    is not used to filter facts. The selectors' signatures and modules stay free of any repository.
    """
    assert list(inspect.signature(select_eps_fact).parameters) == [
        "accession_number",
        "company_cik",
        "form",
        "submission_period_ends",
        "facts",
        "period_class",
        "eps_kind",
    ]
    assert list(inspect.signature(select_comparative_eps).parameters) == [
        "accession_number",
        "company_cik",
        "form",
        "submission_period_ends",
        "facts",
        "period_class",
        "eps_kind",
    ]
    assert list(inspect.signature(compute_eps_change).parameters) == ["comparative"]
    for relative in ("eps.py", "comparative_eps.py", "eps_change.py"):
        tree = ast.parse((SRC / "financials" / relative).read_text(encoding="utf-8"))
        imported = {
            n.module
            for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.module is not None
        }
        assert not any("repositories" in m or "sqlalchemy" in m for m in imported), relative
        # the CIK is only ever copied through; the one comparison is the internal consistency check
        # that a comparative observation and its current observation name the same registrant
        compares = {
            ast.unparse(n)
            for n in ast.walk(tree)
            if isinstance(n, ast.Compare) and "cik" in ast.unparse(n).lower()
        }
        assert compares <= {"self.current.company_cik != self.company_cik"}, (relative, compares)
