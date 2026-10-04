"""The public contract of the two filing APIs, pinned with focused assertions (no database).

``GET /api/v1/filings`` and ``GET /api/v1/filings/{accession}/eps``: parameters, optionality, enums,
response fields, decimal-string schemas, status codes, the boundary fields, malformed-input
rejection (never degrading into a broader valid request) and the PIT / trading language audit of
the production code the vertical touches.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import quantforge
from quantforge.core.config import Settings
from quantforge.main import create_app

SRC = Path(quantforge.__file__).resolve().parent
REPO = SRC.parents[3]
ACCESSION = "0000320193-24-000010"
FORBIDDEN_FIELDS = {
    "accepted_at",
    "source_accepted",
    "ingested_at",
    "observed_at",
    "available_at",
    "known_as_of",
    "tradable",
    "created_at",
    "ingestion_run_id",
    "raw_artifact_path",
    "run_id",
    "api_key",
    "contact",
    "user_agent",
}


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    spec: dict[str, Any] = create_app(Settings(_env_file=None)).openapi()
    return spec


def _params(spec: dict[str, Any], path: str) -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in spec["paths"][path]["get"]["parameters"]}


def _walk(node: Any) -> list[str]:
    names: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            names.append(key)
            names += _walk(value)
    elif isinstance(node, list):
        for item in node:
            names += _walk(item)
    return names


def test_the_filings_index_contract(spec: dict[str, Any]) -> None:
    operation = spec["paths"]["/api/v1/filings"]["get"]
    params = _params(spec, "/api/v1/filings")
    assert set(params) == {"limit", "offset", "form", "cik"}
    assert not any(p["required"] for p in params.values()), "every index parameter is optional"
    assert params["limit"]["schema"] | {} == {
        "type": "integer",
        "minimum": 1,
        "maximum": 200,
        "default": 50,
        "title": "Limit",
    }
    assert params["offset"]["schema"]["maximum"] == 1_000_000
    assert params["offset"]["schema"]["default"] == 0
    cik = params["cik"]["schema"]["anyOf"]
    assert {"type": "string", "pattern": "^[0-9]{10}$"} in cik
    assert set(operation["responses"]) >= {"200", "422", "503"}
    page = spec["components"]["schemas"]["FilingIndexPage"]
    assert set(page["properties"]) == {"items", "total", "limit", "offset"}
    assert set(page["required"]) == {"items", "total", "limit", "offset"}
    item = spec["components"]["schemas"]["FilingIndexItem"]
    assert set(item["properties"]) == {
        "accession",
        "company_cik",
        "form",
        "filing_date",
        "report_date",
        "fsds_period_end",
    }
    assert item["properties"]["filing_date"]["format"] == "date"
    assert "date-time" not in json.dumps(item)


def test_the_filing_eps_contract(spec: dict[str, Any]) -> None:
    path = "/api/v1/filings/{accession}/eps"
    operation = spec["paths"][path]["get"]
    params = _params(spec, path)
    assert set(params) == {"accession", "period_class", "eps_kind", "cik"}
    required = {name for name, p in params.items() if p["required"]}
    assert required == {"accession", "period_class", "eps_kind"}, "cik is the only optional one"
    assert params["accession"]["in"] == "path" and params["cik"]["in"] == "query"
    assert {"type": "string", "pattern": "^[0-9]{10}$"} in params["cik"]["schema"]["anyOf"]
    assert "default" not in params["period_class"]["schema"]
    assert "default" not in params["eps_kind"]["schema"]
    assert set(operation["responses"]) >= {"200", "422", "503"}
    components = spec["components"]["schemas"]
    response = components["FilingEpsResponse"]
    assert set(response["properties"]) == {
        "accession",
        "company_cik",
        "form",
        "period_class",
        "eps_kind",
        "descriptive_only",
        "no_point_in_time_claim",
        "boundary_note",
        "current",
        "comparative",
        "change",
    }
    assert response["properties"]["descriptive_only"]["const"] is True
    assert response["properties"]["no_point_in_time_claim"]["const"] is True
    assert "descriptive_only" in response["required"]
    # every financial value is a string-or-null schema; none is a JSON number
    for schema, fields in (
        ("FactEvidence", ["value"]),
        ("EpsChangeSection", ["eps_change", "percentage_change"]),
    ):
        for field in fields:
            options = components[schema]["properties"][field]["anyOf"]
            assert {o.get("type") for o in options} == {"string", "null"}, (schema, field)
    assert "number" not in json.dumps(components["EpsChangeSection"])
    assert components["EpsChangeSection"]["properties"]["percentage_semantics"]["const"] == "ratio"


def test_no_public_filing_schema_declares_an_availability_internal_or_secret_field(
    spec: dict[str, Any],
) -> None:
    components = spec["components"]["schemas"]
    for name in (
        "FilingIndexPage",
        "FilingIndexItem",
        "FilingEpsResponse",
        "CurrentEpsSection",
        "ComparativeEpsSection",
        "EpsChangeSection",
        "FactEvidence",
    ):
        assert not set(components[name]["properties"]) & FORBIDDEN_FIELDS, name


@pytest.mark.parametrize(
    "url",
    [
        # index
        "/api/v1/filings?limit=0",
        "/api/v1/filings?limit=201",
        "/api/v1/filings?limit=abc",
        "/api/v1/filings?offset=-1",
        "/api/v1/filings?offset=1000001",
        "/api/v1/filings?page=2",
        "/api/v1/filings?form=10-q",
        "/api/v1/filings?cik=",
        "/api/v1/filings?cik=320193",
        "/api/v1/filings?cik=0000320193&cik=0000555555",
        "/api/v1/filings?form=10-Q&form=10-K",
        "/api/v1/filings?limit=10&limit=20",
        # detail
        f"/api/v1/filings/{ACCESSION}/eps?eps_kind=DILUTED",
        f"/api/v1/filings/{ACCESSION}/eps?period_class=QUARTER",
        f"/api/v1/filings/{ACCESSION}/eps?period_class=YTD&eps_kind=DILUTED",
        f"/api/v1/filings/{ACCESSION}/eps?period_class=QUARTER&eps_kind=BASIC",
        f"/api/v1/filings/{ACCESSION}/eps?period_class=QUARTER&eps_kind=DILUTED&cik=",
        f"/api/v1/filings/{ACCESSION}/eps?period_class=QUARTER&eps_kind=DILUTED&cik=320193",
        f"/api/v1/filings/{ACCESSION}/eps?period_class=QUARTER&eps_kind=DILUTED&cik=0000320193&cik=0000555555",
        f"/api/v1/filings/{ACCESSION}/eps?period_class=QUARTER&period_class=ANNUAL&eps_kind=DILUTED",
        f"/api/v1/filings/{ACCESSION}/eps?period_class=QUARTER&eps_kind=DILUTED&ticker=AAPL",
        "/api/v1/filings/not-an-accession/eps?period_class=QUARTER&eps_kind=DILUTED",
    ],
)
def test_malformed_input_is_422_and_never_degrades_into_a_broader_valid_request(
    url: str,
) -> None:
    with TestClient(create_app(Settings(_env_file=None))) as client:
        assert client.get(url).status_code == 422, url


def test_a_repeated_parameter_is_a_standard_shaped_422_naming_every_repeat() -> None:
    with TestClient(create_app(Settings(_env_file=None))) as client:
        response = client.get("/api/v1/filings?cik=0000320193&cik=0000555555&limit=1&limit=2")
        single = client.get("/api/v1/filings?limit=abc")
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list), "the same list-of-errors shape as every other 422"
    assert [d["loc"] for d in detail] == [["query", "cik"], ["query", "limit"]]
    assert all({"type", "loc", "msg"} <= set(d) for d in detail)
    assert isinstance(single.json()["detail"], list) and set(detail[0]) <= {
        "type",
        "loc",
        "msg",
        "input",
    }


# ── language audit: PIT and trading wording in the production code of the vertical ──────

PIT_PHRASES = (
    "known at filing",
    "available at acceptance",
    "available when filed",
    "tradable then",
    "tradeable then",
    "point-in-time safe",
    "pit safe",
    "pit-safe",
    "tradable at",
)
VERTICAL_SOURCES = (
    "api/v1/filings.py",
    "api/v1/filing_eps.py",
    "api/v1/filing_eps_presenter.py",
    "schemas/filing_index.py",
    "schemas/filing_eps.py",
    "financials/eps.py",
    "financials/comparative_eps.py",
    "financials/eps_change.py",
    "financials/eps_extraction.py",
    "financials/comparative_eps_extraction.py",
)
TRADING_WORDS = re.compile(r"\b(bullish|bearish|buy|sell|beat|miss)\b", re.IGNORECASE)


def _user_facing_text(path: Path) -> str:
    """String literals (excluding docstrings) and identifiers: what can reach a client."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.ClassDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    parts: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                parts.append(node.value)
        elif isinstance(node, ast.Name):
            parts.append(node.id)
        elif isinstance(node, ast.Attribute):
            parts.append(node.attr)
    return "\n".join(parts)


@pytest.mark.parametrize("relative", VERTICAL_SOURCES)
def test_the_vertical_makes_no_point_in_time_or_trading_claim(relative: str) -> None:
    text = (SRC / relative).read_text(encoding="utf-8").lower()
    for phrase in PIT_PHRASES:
        assert phrase not in text, f"{relative} implies {phrase!r}"
    assert not TRADING_WORDS.search(_user_facing_text(SRC / relative)), relative


def test_the_boundary_wording_is_present_in_the_response_schema_and_the_readme() -> None:
    schema = (SRC / "schemas/filing_eps.py").read_text(encoding="utf-8")
    assert "No point-in-time availability claim" in schema
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    for line in (
        "Descriptive accounting data from SEC FSDS.",
        "No point-in-time availability claim.",
        "Not a trading signal.",
    ):
        assert line in readme, line
