"""Persisted research record API contract that needs no database (Step 11.0A).

OpenAPI, request validation, error mapping, decimal wire format and structure. The behaviour over
real stored records is in ``tests/integration/test_api_research_records.py``.
"""

from __future__ import annotations

import ast
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import InterfaceError, OperationalError

import quantforge
from quantforge.api.v1 import dependencies
from quantforge.api.v1 import research_records as records_api
from quantforge.api.v1.research_record_presenter import present_detail
from quantforge.core.config import Settings
from quantforge.db.session import get_session
from quantforge.main import create_app
from quantforge.observation import EarningsEventNotFoundError
from quantforge.repositories.research_record import ResearchRecordRepository
from quantforge.reproducibility.baseline import ReproducibilityBaseline
from quantforge.reproducibility.canonical import canonical_text
from quantforge.research_records import (
    RecordIntegrityError,
    ResearchRecordNotFoundError,
    StoredResearchRecord,
)
from quantforge.schemas import research_record as record_schema

SRC = Path(quantforge.__file__).resolve().parent
RID = "7c1b6f0e-5c43-4a6e-9a44-0d9d6f6b2222"
LIST = "/api/v1/research/records"
DETAIL = "/api/v1/research/records/{record_id}"
VERIFY = "/api/v1/research/records/{record_id}/verify"
SECRET = "password=sup3rs3cret"
API_MODULES = (
    "api/v1/research_records.py",
    "api/v1/research_record_presenter.py",
    "schemas/research_record.py",
)


def _app() -> FastAPI:
    return create_app(Settings(_env_file=None))


@pytest.fixture
def client() -> TestClient:
    application = _app()
    application.dependency_overrides[get_session] = lambda: None  # nothing may reach a database
    return TestClient(application, raise_server_exceptions=False)


def _spec() -> dict[str, Any]:
    return _app().openapi()


# ── OpenAPI ──────────────────────────────────────────────────────────────────────────────


def test_openapi_exposes_exactly_three_get_routes_and_no_write_verb() -> None:
    spec = _spec()
    record_paths = {p: ops for p, ops in spec["paths"].items() if "/research/records" in p}
    assert set(record_paths) == {LIST, DETAIL, VERIFY}
    for path, operation in record_paths.items():
        assert set(operation) == {"get"}, f"{path}: no write verb may exist"
    # nothing anywhere under research (or anywhere in the API) is a write operation
    for path, operation in spec["paths"].items():
        assert not {"post", "put", "patch", "delete"} & set(operation), path


def test_openapi_documents_parameters_and_responses() -> None:
    paths = _spec()["paths"]
    listing = paths[LIST]["get"]
    assert {"200", "422", "500", "503"} <= set(listing["responses"])
    params = {p["name"]: p for p in listing["parameters"]}
    assert set(params) == {"limit", "offset"}
    assert params["limit"]["schema"]["maximum"] == record_schema.MAX_LIMIT == 200
    assert params["limit"]["schema"]["minimum"] == 1
    assert params["limit"]["schema"]["default"] == record_schema.DEFAULT_LIMIT == 50
    assert params["offset"]["schema"]["minimum"] == 0
    assert listing["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ResearchRecordPage"
    )

    detail = paths[DETAIL]["get"]
    assert {"200", "404", "422", "500", "503"} <= set(detail["responses"])
    assert [p["name"] for p in detail["parameters"]] == ["record_id"]
    assert detail["parameters"][0]["schema"]["format"] == "uuid"
    assert detail["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ResearchRecordDetail"
    )

    verify = paths[VERIFY]["get"]
    assert {"200", "404", "409", "422", "500", "503"} <= set(verify["responses"])
    assert [p["name"] for p in verify["parameters"]] == ["record_id"]
    assert verify["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/RecordVerificationResponse"
    )


def test_the_wire_schemas_expose_no_canonical_text_and_no_creation_shape() -> None:
    components = _spec()["components"]["schemas"]
    names = {n for n in components if "Record" in n}
    assert names >= {
        "ResearchRecordPage",
        "ResearchRecordSummary",
        "ResearchRecordDetail",
        "RecordVerificationResponse",
    }
    assert not {n for n in names if "Create" in n or "Save" in n or "Update" in n}
    fields = {f for n in names for f in components[n].get("properties", {})}
    assert not {"canonical_manifest", "canonical_result", "canonical_text"} & fields
    assert "updated_at" not in fields
    detail = components["ResearchRecordDetail"]["properties"]
    assert {"manifest", "result", "manifest_fingerprint", "result_fingerprint"} <= set(detail)


# ── validation ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("record_id", ["not-a-uuid", f"{RID}x", "123", "0" * 36])
def test_a_malformed_record_id_is_422_on_get_and_verify(client: TestClient, record_id: str) -> None:
    assert client.get(f"{LIST}/{record_id}").status_code == 422
    assert client.get(f"{LIST}/{record_id}/verify").status_code == 422


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=201",
        "limit=-1",
        "limit=abc",
        "limit=1.5",
        "offset=-1",
        "offset=1000001",
        "offset=x",
        "limit=5&limit=6",
        "offset=1&offset=2",
        "nope=1",
        "limit=5&order=asc",
    ],
)
def test_invalid_list_queries_are_422_and_never_reach_the_database(
    client: TestClient, query: str
) -> None:
    response = client.get(f"{LIST}?{query}")
    assert response.status_code == 422
    assert "detail" in response.json()


@pytest.mark.parametrize("suffix", ["", "/verify"])
@pytest.mark.parametrize("query", ["x=1", "limit=5", f"id={RID}"])
def test_get_and_verify_reject_any_query_parameter(
    client: TestClient, suffix: str, query: str
) -> None:
    assert client.get(f"{LIST}/{RID}{suffix}?{query}").status_code == 422


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
@pytest.mark.parametrize("path", [LIST, f"{LIST}/{RID}", f"{LIST}/{RID}/verify"])
def test_no_write_method_is_routed(client: TestClient, method: str, path: str) -> None:
    assert client.request(method, path, json={}).status_code == 405


# ── error mapping ────────────────────────────────────────────────────────────────────────


class _Recorder:
    """Stands in for a module logger (``caplog`` depends on global logging state)."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def _add(self, message: str, *args: object, **kwargs: object) -> None:
        assert not kwargs.get("exc_info"), "no traceback (driver text) is ever logged"
        self.messages.append(message % args)

    warning = error = _add

    @property
    def text(self) -> str:
        return " ".join(self.messages)


def _raises(error: Exception) -> Any:
    def boom(*_a: object, **_k: object) -> None:
        raise error

    return boom


@pytest.mark.parametrize("error_type", [OperationalError, InterfaceError])
def test_database_unavailable_is_a_generic_503_on_every_route(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(dependencies, "logger", recorder)
    error = error_type("SELECT 1", {}, Exception(SECRET))
    monkeypatch.setattr(ResearchRecordRepository, "list_page", _raises(error))
    monkeypatch.setattr(records_api, "load_research_record", _raises(error))
    monkeypatch.setattr(records_api, "verify_stored_record", _raises(error))
    for url in (LIST, f"{LIST}/{RID}", f"{LIST}/{RID}/verify"):
        response = client.get(url)
        assert response.status_code == 503
        assert response.json() == {"detail": "Database unavailable."}
        assert "sup3rs3cret" not in response.text and "SELECT" not in response.text
    assert "sup3rs3cret" not in recorder.text and "SELECT" not in recorder.text
    assert recorder.text.count(error_type.__name__) == 3


def test_an_unknown_record_is_404_on_get_and_verify(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = ResearchRecordNotFoundError(f"no research record {RID}")
    monkeypatch.setattr(records_api, "load_research_record", _raises(missing))
    monkeypatch.setattr(records_api, "verify_stored_record", _raises(missing))
    for url in (f"{LIST}/{RID}", f"{LIST}/{RID}/verify"):
        response = client.get(url)
        assert response.status_code == 404
        assert response.json() == {"detail": "Research record not found."}


def test_a_corrupt_record_is_a_generic_500_with_no_stored_content(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(records_api, "logger", recorder)
    canonical_fragment = '{"evidence":{"stock":{"bars":[{"close":"101.2500"}]}}}'
    corrupt = RecordIntegrityError(
        f"record {RID}: the stored manifest does not hash to its stored fingerprint"
    )
    monkeypatch.setattr(records_api, "load_research_record", _raises(corrupt))
    monkeypatch.setattr(records_api, "verify_stored_record", _raises(corrupt))
    for url in (f"{LIST}/{RID}", f"{LIST}/{RID}/verify"):
        response = client.get(url)
        assert response.status_code == 500
        assert response.json() == {"detail": "Stored research record failed integrity validation."}
        assert canonical_fragment not in response.text and RID not in response.text
    assert recorder.text.count(RID) >= 2, "the id is logged server-side"


def test_a_missing_event_is_409_on_verify_only_and_other_errors_are_not_masked(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        records_api, "verify_stored_record", _raises(EarningsEventNotFoundError("gone"))
    )
    response = client.get(f"{LIST}/{RID}/verify")
    assert response.status_code == 409
    assert response.json() == {"detail": "The earnings event of this record is no longer stored."}

    monkeypatch.setattr(records_api, "verify_stored_record", _raises(RuntimeError("invariant")))
    monkeypatch.setattr(records_api, "load_research_record", _raises(ValueError("bug")))
    assert client.get(f"{LIST}/{RID}/verify").status_code == 500
    assert client.get(f"{LIST}/{RID}").status_code == 500


# ── decimal wire ─────────────────────────────────────────────────────────────────────────

_EVIDENCE: dict[str, Any] = {
    "event": {"ingestion_run_id": RID},
    "stock": {"bars": [{"ingestion_run_id": RID, "close": "101.2500"}]},
    "benchmark": {"bars": []},
    "filing": {"state": "NOT_REQUESTED", "ingestion_run_id": None},
    "accounting": {"state": "NOT_REQUESTED", "artifacts": []},
}
_MANIFEST: dict[str, Any] = {
    "request": {
        "event_id": RID,
        "horizons": [1, 5],
        "benchmark_id": None,
        "security_id": None,
        "filing_accession": None,
        "eps_period_class": None,
        "eps_kind": None,
        "price_basis": "SPLIT_ADJUSTED",
        "stock_source_id": None,
        "benchmark_source_id": None,
        "modes": ["DESCRIPTIVE", "POINT_IN_TIME"],
    },
    "methodology": {"manifest_schema": "reproducibility-manifest/2"},
    "evidence": _EVIDENCE,
}
_RESULT: dict[str, Any] = {
    "eps": {"reported": "1.5300", "estimated": "1.50", "surprise": "0.0300", "pct": "0.020"},
    "returns": [{"horizon": 1, "return": "0.0100000000", "count": 3}],
    "tiny": "0.0000001",
}


def _stored() -> StoredResearchRecord:
    baseline = ReproducibilityBaseline.from_canonical(
        canonical_text(_MANIFEST), canonical_text(_RESULT)
    )
    return StoredResearchRecord(uuid.UUID(RID), datetime(2026, 10, 4, tzinfo=UTC), baseline)


def _no_float(token: str) -> object:
    raise AssertionError(f"a JSON float {token!r} appeared on the wire")


def test_stored_financial_values_keep_their_exact_string_form_on_the_wire(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(records_api, "load_research_record", lambda *_a, **_k: _stored())
    response = client.get(f"{LIST}/{RID}")
    assert response.status_code == 200, response.text
    body = json.loads(response.text, parse_float=_no_float)  # a float anywhere fails the parse
    assert body["result"] == _RESULT, "values returned exactly as stored, scale preserved"
    assert body["result"]["eps"]["reported"] == "1.5300"
    assert body["result"]["tiny"] == "0.0000001"
    assert body["manifest"]["evidence"]["stock"]["bars"][0]["close"] == "101.2500"
    assert body["request"]["horizons"] == [1, 5]
    assert body["reproducibility_level"] == "COMPLETE" and body["reproducibility_gaps"] == []
    assert body["hash_algorithm"] == "sha256"
    assert body["manifest_fingerprint"] == _stored().baseline.fingerprints.manifest.digest
    assert "canonical" not in response.text.lower()


def test_the_detail_presenter_is_pure_mapping() -> None:
    detail = present_detail(_stored())
    assert detail.record_id == uuid.UUID(RID)
    assert detail.manifest == _MANIFEST and detail.result == _RESULT


# ── structure ────────────────────────────────────────────────────────────────────────────


def test_the_record_api_is_read_only_transport() -> None:
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
        for forbidden in (
            "save_research_record",
            "insert_if_absent",
            "ingestion",
            "providers",
            "sqlalchemy.orm",
            "import sqlalchemy as sa",
        ):
            assert forbidden not in text.replace("sqlalchemy.exc", ""), (relative, forbidden)
        for word in ("BUY", "SELL", "bullish", "bearish", "recommend"):
            assert word not in text, (relative, word)
    decorators = [
        d.func.attr
        for n in ast.walk(ast.parse((SRC / API_MODULES[0]).read_text(encoding="utf-8")))
        if isinstance(n, ast.FunctionDef)
        for d in n.decorator_list
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
    ]
    assert decorators == ["get", "get", "get"]


def test_the_repository_still_has_no_update_or_delete_and_the_new_query_is_select_only() -> None:
    public = {n for n in dir(ResearchRecordRepository) if not n.startswith("_")}
    assert public == {"insert_if_absent", "get", "list_page", "for_manifest", "for_pair"}
    source = (SRC / "repositories" / "research_record.py").read_text(encoding="utf-8")
    assert ".delete(" not in source and "sa.update" not in source and "sa.delete" not in source
    assert "canonical_result" not in source.split("def list_page")[1].split("def for_manifest")[0]


def test_an_unparseable_or_malformed_stored_row_is_a_generic_500_on_the_list(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(records_api, "logger", recorder)
    from sqlalchemy.exc import DataError  # noqa: PLC0415 - local: only this test needs it

    from quantforge.repositories.research_record import (  # noqa: PLC0415
        RecordListPage,
        RecordListRow,
    )

    monkeypatch.setattr(
        ResearchRecordRepository,
        "list_page",
        _raises(DataError("SELECT CAST(...)", {}, Exception(SECRET))),
    )
    response = client.get(LIST)
    assert response.status_code == 500
    assert response.json() == {"detail": "Stored research record failed integrity validation."}
    assert "sup3rs3cret" not in response.text and "sup3rs3cret" not in recorder.text

    broken = RecordListRow(
        record_id=uuid.UUID(RID),
        created_at=datetime(2026, 10, 4, tzinfo=UTC),
        manifest_fingerprint="0" * 64,
        manifest_hash_algorithm="sha256",
        result_fingerprint="1" * 64,
        level=_stored().baseline.level,
        gaps=[],
        request={"horizons": [1]},  # no event_id: not a request object
    )
    monkeypatch.setattr(
        ResearchRecordRepository,
        "list_page",
        lambda *_a, **_k: RecordListPage(rows=[broken], total=1),
    )
    response = client.get(LIST)
    assert response.status_code == 500
    assert response.json() == {"detail": "Stored research record failed integrity validation."}
