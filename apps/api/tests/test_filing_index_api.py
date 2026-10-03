"""``GET /api/v1/filings`` without a database: contract, validation, OpenAPI and structure.

The populated behaviour (join multiplication, totals, order, filters, disagreement) is covered over
real stored rows in ``tests/integration/test_api_filing_index.py``.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

import quantforge
from quantforge.core.config import Settings
from quantforge.db.session import get_session
from quantforge.main import create_app
from quantforge.models.enums import SecFormType
from quantforge.schemas.filing_index import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MAX_OFFSET,
    FilingIndexItem,
    FilingIndexPage,
    FilingIndexQuery,
)

SRC = Path(quantforge.__file__).resolve().parent
ROUTE_FILES = ("api/v1/filings.py", "schemas/filing_index.py")
FORBIDDEN_FIELDS = {
    "accepted_at",
    "source_accepted",
    "ingested_at",
    "observed_at",
    "available_at",
    "known_as_of",
    "tradable",
    "created_at",
    "id",
    "ingestion_run_id",
    "raw_artifact_path",
    "ticker",
    "company_name",
    "exchange",
}


@pytest.fixture
def client() -> Iterator[TestClient]:
    application: FastAPI = create_app(Settings(_env_file=None))

    def _no_database() -> Iterator[None]:
        yield None  # the handler never runs for a rejected request, so it is never used

    application.dependency_overrides[get_session] = _no_database
    with TestClient(application) as test_client:
        yield test_client


# ── query model ───────────────────────────────────────────────────────────────────────────


def test_the_query_defaults_and_bounds_match_the_documented_contract() -> None:
    query = FilingIndexQuery()
    assert (query.limit, query.offset, query.form, query.cik) == (DEFAULT_LIMIT, 0, None, None)
    assert (DEFAULT_LIMIT, MAX_LIMIT, MAX_OFFSET) == (50, 200, 1_000_000)
    assert FilingIndexQuery(limit=200, offset=1_000_000).limit == 200


@pytest.mark.parametrize(
    "bad",
    [
        {"limit": 0},
        {"limit": -1},
        {"limit": 201},
        {"offset": -1},
        {"offset": 1_000_001},
        {"form": "10-q"},
        {"form": "10-Q "},
        {"form": "S-1"},
        {"cik": "320193"},
        {"cik": "00003201930"},
        {"cik": " 0000320193"},
        {"cik": "000032019X"},
        {"unknown": "x"},
    ],
)
def test_invalid_query_values_are_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        FilingIndexQuery(**bad)


def test_every_stored_form_is_an_exact_filter_value() -> None:
    for form in SecFormType:
        assert FilingIndexQuery(form=form.value).form is form


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=-1",
        "limit=201",
        "limit=abc",
        "offset=-1",
        "offset=1000001",
        "offset=abc",
        "form=10-q",
        "cik=123",
        "cik=abc",
        "extra=1",
        "ticker=AAPL",
    ],
)
def test_http_rejects_invalid_parameters_with_422(client: TestClient, query: str) -> None:
    assert client.get(f"/api/v1/filings?{query}").status_code == 422


# ── response schema ───────────────────────────────────────────────────────────────────────


def test_the_response_exposes_only_the_six_index_fields() -> None:
    assert set(FilingIndexItem.model_fields) == {
        "accession",
        "company_cik",
        "form",
        "filing_date",
        "report_date",
        "fsds_period_end",
    }
    assert set(FilingIndexPage.model_fields) == {"items", "total", "limit", "offset"}
    assert not set(FilingIndexItem.model_fields) & FORBIDDEN_FIELDS


def test_an_item_serialises_dates_as_iso_dates_and_nullable_fields_as_null() -> None:
    item = FilingIndexItem(
        accession="0000320193-24-000010",
        company_cik="0000320193",
        form=SecFormType.FORM_10Q_A,
        filing_date=date(2024, 5, 3),
        report_date=None,
        fsds_period_end=None,
    )
    assert item.model_dump(mode="json") == {
        "accession": "0000320193-24-000010",
        "company_cik": "0000320193",
        "form": "10-Q/A",
        "filing_date": "2024-05-03",
        "report_date": None,
        "fsds_period_end": None,
    }


# ── OpenAPI ───────────────────────────────────────────────────────────────────────────────


def _operation(client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    spec = client.get("/openapi.json").json()
    return spec["paths"]["/api/v1/filings"]["get"], spec["components"]["schemas"]


def test_openapi_documents_parameters_constraints_responses_and_date_types(
    client: TestClient,
) -> None:
    operation, schemas = _operation(client)
    parameters = {p["name"]: p for p in operation["parameters"]}
    assert set(parameters) == {"limit", "offset", "form", "cik"}
    limit = parameters["limit"]["schema"]
    assert (limit["default"], limit["minimum"], limit["maximum"]) == (50, 1, 200)
    offset = parameters["offset"]["schema"]
    assert (offset["default"], offset["minimum"], offset["maximum"]) == (0, 0, 1_000_000)
    assert not any(parameters[name]["required"] for name in parameters)
    assert any(r.get("pattern") == r"^[0-9]{10}$" for r in parameters["cik"]["schema"]["anyOf"])
    assert "10-Q/A" in str(parameters["form"]["schema"])
    assert {"200", "422", "503"} <= set(operation["responses"])
    item = schemas["FilingIndexItem"]["properties"]
    assert item["filing_date"] == {
        "type": "string",
        "format": "date",
        "title": "Filing Date",
        "description": item["filing_date"]["description"],
    }
    for name in ("report_date", "fsds_period_end"):
        assert {"type": "string", "format": "date"} in item[name]["anyOf"]
    assert not any("date-time" in str(item[name]) for name in item)
    assert set(schemas["FilingIndexPage"]["required"]) == {"items", "total", "limit", "offset"}


def test_the_eps_detail_route_is_still_registered_alongside_the_index(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/filings/{accession}/eps" in paths and "/api/v1/filings" in paths


# ── structure ─────────────────────────────────────────────────────────────────────────────


def _imports(relative: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse((SRC / relative).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("relative", ROUTE_FILES)
def test_the_index_has_no_provider_network_financial_semantics_or_availability_dependency(
    relative: str,
) -> None:
    banned = (
        "httpx",
        "requests",
        "urllib",
        "socket",
        "quantforge.providers",
        "quantforge.research",
        "quantforge.financials",
        "quantforge.ingestion",
        "quantforge.identity",
    )
    offenders = {n for n in _imports(relative) if any(n.startswith(b) for b in banned)}
    assert offenders == set()


def _code_names(tree: ast.AST) -> set[str]:
    """Every identifier, attribute and string constant used by code, excluding docstrings."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.ClassDef) and ast.get_docstring(
            node
        ):
            node.body = node.body[1:]
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
    return names


def test_the_router_holds_no_sql_and_the_repository_never_sorts_or_reads_acceptance() -> None:
    router = ast.parse((SRC / "api/v1/filings.py").read_text(encoding="utf-8"))
    assert not {n for n in _imports("api/v1/filings.py") if n.startswith("sqlalchemy")} - {
        "sqlalchemy.exc"
    }, "the router must not build queries"
    assert not _code_names(router) & {"select", "execute", "commit", "accepted_at", "ingested_at"}
    repository = ast.parse((SRC / "repositories/financial_fact.py").read_text(encoding="utf-8"))
    method = next(
        n
        for n in ast.walk(repository)
        if isinstance(n, ast.FunctionDef) and n.name == "filing_index"
    )
    forbidden = {"accepted_at", "source_accepted", "ingested_at", "created_at", "add", "commit"}
    assert not _code_names(method) & forbidden
