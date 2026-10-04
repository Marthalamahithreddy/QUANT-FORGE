"""Benchmark discovery API contract that needs no database (Step 11.0B).

OpenAPI, request validation, error mapping and the structural exposure guard. The behaviour over
real stored benchmarks is in ``tests/integration/test_api_benchmarks.py``.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import InterfaceError, OperationalError

import quantforge
from quantforge.api.v1 import benchmarks as benchmarks_api
from quantforge.api.v1 import dependencies
from quantforge.core.config import Settings
from quantforge.db.session import get_session
from quantforge.main import create_app
from quantforge.repositories.benchmark import BenchmarkRepository
from quantforge.schemas import benchmark_index

SRC = Path(quantforge.__file__).resolve().parent
URL = "/api/v1/benchmarks"
SECRET = "password=sup3rs3cret"
API_MODULES = ("api/v1/benchmarks.py", "schemas/benchmark_index.py")
FIELDS = {"id", "name", "symbol", "currency", "is_active"}


def _app() -> FastAPI:
    return create_app(Settings(_env_file=None))


@pytest.fixture
def client() -> TestClient:
    application = _app()
    application.dependency_overrides[get_session] = lambda: None  # nothing may reach a database
    return TestClient(application, raise_server_exceptions=False)


# ── OpenAPI ──────────────────────────────────────────────────────────────────────────────


def test_openapi_exposes_exactly_one_get_with_the_documented_responses() -> None:
    spec = _app().openapi()
    paths = {p: ops for p, ops in spec["paths"].items() if "/benchmarks" in p}
    assert set(paths) == {URL}, "no detail route"
    assert set(paths[URL]) == {"get"}, "no write verb"
    get = paths[URL]["get"]
    assert {"200", "422", "503"} <= set(get["responses"])
    assert get["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/BenchmarkIndexPage"
    )
    params = {p["name"]: p for p in get["parameters"]}
    assert set(params) == {"limit", "offset"}, "discovery only: no search or filter"
    assert params["limit"]["schema"]["default"] == 50 == benchmark_index.DEFAULT_LIMIT
    assert params["limit"]["schema"]["maximum"] == 200 == benchmark_index.MAX_LIMIT
    assert params["limit"]["schema"]["minimum"] == 1
    assert params["offset"]["schema"]["minimum"] == 0
    assert params["offset"]["schema"]["maximum"] == benchmark_index.MAX_OFFSET
    for path, operation in spec["paths"].items():
        assert not {"post", "put", "patch", "delete"} & set(operation), path


def test_the_response_schema_exposes_only_the_five_identity_fields() -> None:
    components = _app().openapi()["components"]["schemas"]
    assert set(components["BenchmarkIndexItem"]["properties"]) == FIELDS
    assert set(components["BenchmarkIndexPage"]["properties"]) == {
        "items",
        "total",
        "limit",
        "offset",
    }
    assert set(benchmark_index.BenchmarkIndexItem.model_fields) == FIELDS
    # nothing numeric but the pagination counters: no level, return or other financial value
    for name, field in benchmark_index.BenchmarkIndexItem.model_fields.items():
        assert field.annotation in (uuid.UUID, str, str | None, bool), name
    names = set(components["BenchmarkIndexItem"]["properties"])
    for forbidden in ("source", "run", "price", "level", "close", "return", "description"):
        assert not any(forbidden in name for name in names), forbidden
    for forbidden in ("default", "recommended", "primary", "created", "updated", "secret", "key"):
        assert not any(forbidden in name for name in names), forbidden


# ── validation ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=-1",
        "limit=201",
        "limit=abc",
        "limit=1.5",
        "offset=-1",
        "offset=1000001",
        "offset=x",
        "limit=5&limit=6",
        "offset=1&offset=2",
        "nope=1",
        "name=S%26P",
        "active=true",
        "limit=5&order=desc",
    ],
)
def test_invalid_unknown_or_repeated_parameters_are_422_and_never_reach_the_database(
    client: TestClient, query: str
) -> None:
    response = client.get(f"{URL}?{query}")
    assert response.status_code == 422
    assert "detail" in response.json()


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
@pytest.mark.parametrize("path", [URL, f"{URL}/{uuid.uuid4()}"])
def test_no_write_method_and_no_detail_route(client: TestClient, method: str, path: str) -> None:
    assert client.request(method, path, json={}).status_code in (404, 405)
    assert client.get(f"{URL}/{uuid.uuid4()}").status_code == 404


# ── error mapping ────────────────────────────────────────────────────────────────────────


class _Recorder:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def warning(self, message: str, *args: object, **kwargs: object) -> None:
        assert not kwargs.get("exc_info"), "no traceback (driver text) is ever logged"
        self.messages.append(message % args)


def _raises(error: Exception) -> Any:
    def boom(*_a: object, **_k: object) -> None:
        raise error

    return boom


@pytest.mark.parametrize("error_type", [OperationalError, InterfaceError])
def test_only_database_unavailability_is_a_generic_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(dependencies, "logger", recorder)
    monkeypatch.setattr(
        BenchmarkRepository,
        "list_identities",
        _raises(error_type("SELECT 1", {}, Exception(SECRET))),
    )
    response = client.get(URL)
    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable."}
    assert "sup3rs3cret" not in response.text and "SELECT" not in response.text
    assert recorder.messages == [f"Benchmark index read failed: {error_type.__name__}"]

    # the count read fails the same way
    monkeypatch.setattr(BenchmarkRepository, "list_identities", lambda *_a, **_k: [])
    monkeypatch.setattr(
        BenchmarkRepository, "count", _raises(error_type("SELECT 1", {}, Exception(SECRET)))
    )
    assert client.get(URL).status_code == 503


def test_an_invariant_error_is_not_disguised_as_a_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(BenchmarkRepository, "list_identities", _raises(RuntimeError("invariant")))
    assert client.get(URL).status_code == 500
    monkeypatch.setattr(BenchmarkRepository, "list_identities", _raises(ValueError("bug")))
    assert client.get(URL).status_code == 500


# ── structure: discovery only ────────────────────────────────────────────────────────────


def test_the_benchmark_api_is_read_only_transport_with_no_default_inference() -> None:
    for relative in API_MODULES:
        text = (SRC / relative).read_text(encoding="utf-8")
        tree = ast.parse(text)
        calls = {
            (n.func.id if isinstance(n.func, ast.Name) else n.func.attr)
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))
        }
        assert calls.isdisjoint(
            {"float", "Decimal", "add", "commit", "flush", "delete", "execute", "insert", "update"}
        ), (relative, calls)
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
            n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
        }
        assert not names & {"source_id", "ingestion_run_id", "price_bars", "list_price_bars"}
        imported = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
            a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
        }
        for module in imported:
            for forbidden in (
                "ingestion",
                "providers",
                "research",
                "observation",
                "reproducibility",
                "sqlalchemy.orm",
                "financials",
            ):
                assert forbidden not in module, (relative, module)
        assert "sqlalchemy" not in imported - {"sqlalchemy.exc"}, relative
        for word in (
            "default_benchmark",
            "recommended_benchmark",
            "primary_benchmark",
            "BUY",
            "SELL",
            "bullish",
        ):
            assert word not in text, (relative, word)
    decorators = [
        d.func.attr
        for n in ast.walk(ast.parse((SRC / API_MODULES[0]).read_text(encoding="utf-8")))
        if isinstance(n, ast.FunctionDef)
        for d in n.decorator_list
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
    ]
    assert decorators == ["get"]


def test_the_router_is_registered_once_and_the_module_is_a_leaf() -> None:
    assert benchmarks_api.router.prefix == "/benchmarks"
    assert [r.path for r in benchmarks_api.router.routes] == ["/benchmarks"]  # type: ignore[attr-defined]
