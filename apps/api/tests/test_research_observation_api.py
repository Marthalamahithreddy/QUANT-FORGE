"""Research observation API contract that needs no database (Step 10.8B).

OpenAPI, request validation, enum parity, decimal wire format, error mapping and structure. The
behaviour over real stored data is in ``tests/integration/test_api_research_observation.py``.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import re
import uuid
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.exc import InterfaceError, OperationalError

import quantforge
from quantforge.api.v1 import dependencies, research_observations
from quantforge.core.config import Settings
from quantforge.db.session import get_session
from quantforge.financials.comparative_eps import ComparativeEpsStatus
from quantforge.financials.eps import EpsKind, EpsPeriodClass, EpsStatus
from quantforge.financials.eps_change import (
    EpsChangeStatus,
    EpsTransition,
    PercentageUnavailableReason,
)
from quantforge.identity.listing_history import Evidence
from quantforge.main import create_app
from quantforge.models.enums import AvailabilityBasis, EarningsTiming, PriceBasis, SecFormType
from quantforge.observation import EarningsEventNotFoundError
from quantforge.observation.models import (
    AccountingState,
    DisclosureEvidence,
    DisclosureEvidenceLevel,
    EventSection,
    FilingLink,
    FilingLinkStatus,
    ObservationIdentity,
    ObservationParameters,
    ObservationProvenance,
    PeriodVerification,
    PointInTimeState,
    RefusalScope,
    SectionState,
)
from quantforge.research.abnormal_returns import AbnormalReturnStatus
from quantforge.research.availability import PointInTimeEligibility, ResearchMode
from quantforge.research.benchmark_returns import BenchmarkReturnStatus
from quantforge.research.calendar import AlignmentStatus
from quantforge.research.eps_surprise import EpsSurpriseStatus
from quantforge.research.listing_resolution import ListingResolution, ListingResolutionStatus
from quantforge.research.returns import ForwardReturnStatus
from quantforge.schemas import research_observation as obs_schema
from quantforge.schemas.common import fixed_point
from quantforge.schemas.research_observation import ForwardReturnOut

SRC = Path(quantforge.__file__).resolve().parent
ROUTE = "/api/v1/research/observations/{event_id}"
EVENT = "5b0f5f0e-5c43-4a6e-9a44-0d9d6f6b1111"
ACCESSION = "0000320193-24-000010"
API_MODULES = (
    "api/v1/research_observations.py",
    "api/v1/research_observation_presenter.py",
    "schemas/research_observation.py",
)


def _app() -> FastAPI:
    return create_app(Settings(_env_file=None))


def _spec() -> dict[str, Any]:
    return _app().openapi()


def test_openapi_exposes_only_a_get_with_the_documented_responses() -> None:
    spec = _spec()
    research_paths = {p: ops for p, ops in spec["paths"].items() if "/research/observations" in p}
    assert set(research_paths) == {ROUTE}
    operation = research_paths[ROUTE]
    assert set(operation) == {"get"}, "no write verb may exist"
    get = operation["get"]
    assert {"200", "404", "422", "503"} <= set(get["responses"])
    ref = get["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/ResearchObservationResponse")
    params = {p["name"]: p for p in get["parameters"]}
    assert set(params) == {
        "event_id",
        "horizons",
        "benchmark_id",
        "security_id",
        "filing_accession",
        "eps_period_class",
        "eps_kind",
    }
    assert params["event_id"]["in"] == "path" and params["event_id"]["required"] is True
    assert params["event_id"]["schema"]["format"] == "uuid"
    for name in ("horizons", "benchmark_id", "security_id", "filing_accession"):
        assert params[name]["required"] is False and params[name]["in"] == "query"
    assert "default" not in json.dumps(
        [params[n]["schema"] for n in ("benchmark_id", "security_id", "filing_accession")]
    )
    assert "pattern" in json.dumps(params["filing_accession"]["schema"])
    # no spec-level write operation anywhere for research
    assert not [op for op in ("post", "put", "patch", "delete") if op in operation]


def _components() -> dict[str, Any]:
    return dict(_spec()["components"]["schemas"])


def _walk_properties(components: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (schema, name, prop)
        for schema, definition in components.items()
        for name, prop in definition.get("properties", {}).items()
    ]


def test_no_response_schema_property_is_a_json_number_except_integer_counts() -> None:
    components = _components()
    response_schemas = {
        name
        for name in components
        if name.endswith(("Out", "Response", "Section", "Evidence"))
        and name not in {"ValidationError", "HTTPValidationError"}
    }
    assert "ResearchObservationResponse" in response_schemas
    for schema, name, prop in _walk_properties(components):
        if schema not in response_schemas:
            continue
        flat = json.dumps(prop)
        assert '"number"' not in flat, (schema, name)


@pytest.mark.parametrize(
    ("schema", "fields"),
    [
        ("EventOut", ["reported_eps", "estimated_eps"]),
        (
            "EpsSurpriseOut",
            ["reported_eps", "estimated_eps", "absolute_surprise", "surprise_ratio"],
        ),
        ("ForwardReturnOut", ["anchor_close", "target_close", "return_value"]),
        (
            "BenchmarkReturnOut",
            ["anchor_close", "target_close", "benchmark_return"],
        ),
        ("AbnormalReturnOut", ["abnormal_return"]),
    ],
)
def test_every_financial_decimal_field_is_a_string_on_the_wire(
    schema: str, fields: list[str]
) -> None:
    properties = _components()[schema]["properties"]
    for field in fields:
        types = {o.get("type") for o in properties[field].get("anyOf", [properties[field]])}
        assert types == {"string", "null"}, (schema, field, types)


#: schema component name -> the domain enum it must mirror exactly (no renaming at the API layer).
ENUM_PARITY: dict[str, type[StrEnum]] = {
    "ResearchMode": ResearchMode,
    "PointInTimeEligibility": PointInTimeEligibility,
    "PointInTimeState": PointInTimeState,
    "SectionState": SectionState,
    "AccountingState": AccountingState,
    "FilingLinkStatus": FilingLinkStatus,
    "PeriodVerification": PeriodVerification,
    "DisclosureEvidenceLevel": DisclosureEvidenceLevel,
    "RefusalScope": RefusalScope,
    "ListingResolutionStatus": ListingResolutionStatus,
    "Evidence": Evidence,
    "AlignmentStatus": AlignmentStatus,
    "ForwardReturnStatus": ForwardReturnStatus,
    "BenchmarkReturnStatus": BenchmarkReturnStatus,
    "AbnormalReturnStatus": AbnormalReturnStatus,
    "EpsSurpriseStatus": EpsSurpriseStatus,
    "EarningsTiming": EarningsTiming,
    "AvailabilityBasis": AvailabilityBasis,
    "PriceBasis": PriceBasis,
    "SecFormType": SecFormType,
    "EpsPeriodClass": EpsPeriodClass,
    "EpsKind": EpsKind,
    "EpsStatus": EpsStatus,
    "ComparativeEpsStatus": ComparativeEpsStatus,
    "EpsChangeStatus": EpsChangeStatus,
    "EpsTransition": EpsTransition,
    "PercentageUnavailableReason": PercentageUnavailableReason,
}


@pytest.mark.parametrize("name", sorted(ENUM_PARITY))
def test_every_exposed_enum_matches_its_domain_enum_exactly(name: str) -> None:
    components = _components()
    assert name in components, f"{name} is not exposed"
    assert components[name]["enum"] == [member.value for member in ENUM_PARITY[name]]


def test_every_enum_in_the_response_schema_is_covered_by_the_parity_table() -> None:
    components = _components()
    reachable: set[str] = set()
    pending = ["ResearchObservationResponse"]
    while pending:
        name = pending.pop()
        if name in reachable:
            continue
        reachable.add(name)
        pending.extend(
            ref.rsplit("/", 1)[1]
            for ref in re.findall(r'"\$ref": "([^"]+)"', json.dumps(components[name]))
        )
    enums = {name for name in reachable if "enum" in components[name]}
    assert enums and enums <= set(ENUM_PARITY), enums - set(ENUM_PARITY)


def test_the_response_contains_no_acceptance_availability_or_roadmap_field() -> None:
    forbidden = {
        "accepted_at",
        "known_at",
        "known_as_of",
        "available_at",
        "disclosed_at",
        "tradable_at",
        "known_gaps",
        "signal",
        "score",
        "recommendation",
    }
    names = {name for _s, name, _p in _walk_properties(_components())}
    assert not names & forbidden


@pytest.mark.parametrize(
    "value",
    [
        "0",
        "-0.5",
        "0.1000",
        "0.0000001",
        "123456789012345678.9012",
        "-1E+3",
        "0.33333333333333333333",
    ],
)
def test_decimal_wire_is_fixed_point_text_never_a_number_or_exponent(value: str) -> None:
    decimal = Decimal(value)
    payload = ForwardReturnOut(
        horizon=1,
        status=ForwardReturnStatus.AVAILABLE,
        anchor_session=None,
        anchor_close=decimal,
        target_session=None,
        target_close=None,
        return_value=decimal,
        reason="r",
    ).model_dump_json()
    parsed = json.loads(payload)
    assert parsed["return_value"] == fixed_point(decimal)
    assert isinstance(parsed["return_value"], str)
    assert "e" not in parsed["return_value"].lower()
    assert Decimal(parsed["return_value"]) == decimal


# ── request validation: 422 before any database access ───────────────────────────────────

URL = f"/api/v1/research/observations/{EVENT}"
BAD_QUERIES = [
    "horizons=0",
    "horizons=-1",
    "horizons=1,1",
    "horizons=1,,5",
    "horizons=1.5",
    "horizons=a",
    "horizons=1,5,20,30,40,50,60,70,80,90,100",
    "horizons=253",
    "horizons=1&horizons=5",
    "horizons=%201",
    "benchmark_id=nope",
    "security_id=1234",
    "filing_accession=0000320193-24-0000",
    f"filing_accession={ACCESSION}",
    f"filing_accession={ACCESSION}&eps_period_class=QUARTER",
    f"filing_accession={ACCESSION}&eps_kind=DILUTED",
    "eps_period_class=QUARTER&eps_kind=DILUTED",
    "eps_kind=DILUTED",
    f"filing_accession={ACCESSION}&eps_period_class=MONTHLY&eps_kind=DILUTED",
    f"filing_accession={ACCESSION}&eps_period_class=QUARTER&eps_kind=basic",
    f"filing_accession={ACCESSION}&eps_period_class=QUARTER&eps_kind=DILUTED&eps_kind=DILUTED",
    "mode=POINT_IN_TIME",
    "stock_source_id=" + EVENT,
    "unknown=1",
]


@pytest.mark.parametrize("query", BAD_QUERIES)
def test_invalid_requests_are_422(query: str) -> None:
    with TestClient(_app()) as client:
        assert client.get(f"{URL}?{query}").status_code == 422, query


def test_a_malformed_event_id_is_422_and_other_verbs_are_not_allowed() -> None:
    with TestClient(_app()) as client:
        assert client.get("/api/v1/research/observations/not-a-uuid").status_code == 422
        for verb in ("post", "put", "patch", "delete"):
            assert getattr(client, verb)(URL).status_code == 405


def test_a_valid_request_passes_validation_and_reaches_the_assembler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake(_session: object, event_id: uuid.UUID, **kwargs: Any) -> None:
        seen.update(kwargs, event_id=event_id)
        raise EarningsEventNotFoundError(str(event_id))

    monkeypatch.setattr(research_observations, "assemble_research_observation", fake)
    application = _app()
    application.dependency_overrides[get_session] = lambda: None
    with TestClient(application) as client:
        response = client.get(
            URL,
            params={
                "horizons": "20,1,5",
                "benchmark_id": EVENT,
                "filing_accession": ACCESSION,
                "eps_period_class": "ANNUAL",
                "eps_kind": "BASIC_AND_DILUTED",
            },
        )
        assert response.status_code == 404 and response.json() == {"detail": "Event not found."}
        seen_default: dict[str, Any] = {}
        seen_default.update(seen)
        client.get(URL)
    assert seen_default["horizons"] == (1, 5, 20), "deterministic ascending order"
    assert seen_default["eps_period_class"] is EpsPeriodClass.ANNUAL
    assert seen_default["eps_kind"] is EpsKind.BASIC_AND_DILUTED
    assert seen["horizons"] == (1, 5, 20), "the research default when absent"
    assert seen["filing_accession"] is None and seen["benchmark_id"] is None


def test_database_failure_is_a_generic_503_and_an_invariant_failure_is_an_unmasked_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged: list[str] = []

    class _Recorder:
        def warning(self, message: str, *args: object, **kwargs: object) -> None:
            assert not kwargs.get("exc_info")
            logged.append(message % args)

    monkeypatch.setattr(dependencies, "logger", _Recorder())
    application = _app()
    application.dependency_overrides[get_session] = lambda: None

    for error in (
        OperationalError("SELECT 1", {}, Exception("password=sup3rs3cret")),
        InterfaceError("SELECT 1", {}, Exception("password=sup3rs3cret")),
    ):

        def boom(*_a: object, _e: Exception = error, **_k: object) -> None:
            raise _e

        monkeypatch.setattr(research_observations, "assemble_research_observation", boom)
        with TestClient(application) as client:
            response = client.get(URL)
        assert response.status_code == 503
        assert response.json() == {"detail": "Database unavailable."}
        assert "sup3rs3cret" not in response.text and "SELECT" not in response.text
    assert logged == [
        "Research observation read failed: OperationalError",
        "Research observation read failed: InterfaceError",
    ]

    def invariant(*_a: object, **_k: object) -> None:
        raise RuntimeError("the current fact has no resolvable artifact")

    monkeypatch.setattr(research_observations, "assemble_research_observation", invariant)
    with TestClient(application, raise_server_exceptions=False) as client:
        assert client.get(URL).status_code == 500


# ── structure: serialisation only ────────────────────────────────────────────────────────


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("relative", API_MODULES)
def test_the_api_surface_does_no_arithmetic_float_conversion_or_provider_access(
    relative: str,
) -> None:
    tree = ast.parse((SRC / relative).read_text(encoding="utf-8"))
    arithmetic = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.BinOp)
        and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow))
        and not isinstance(n.left, (ast.Constant, ast.Tuple, ast.List, ast.Dict))
        and not (isinstance(n.left, ast.Name) and n.left.id.isupper())
    ]
    assert arithmetic == [], "the API only serialises"
    calls = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert not calls & {"float", "round", "sum"}
    forbidden = (
        "quantforge.providers",
        "quantforge.ingestion",
        "quantforge.signals",
        "quantforge.backtest",
        "quantforge.market",
        "httpx",
        "requests",
        "urllib",
        "socket",
    )
    offenders = {
        n
        for n in _imports(SRC / relative)
        if any(n == f or n.startswith(f"{f}.") for f in forbidden)
    }
    assert offenders == set()
    text = (SRC / relative).read_text(encoding="utf-8")
    for token in (".commit(", ".add(", ".flush(", ".delete("):
        assert token not in text
    attributes = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "known_gaps" not in attributes, "the internal roadmap list is not a public field"


def test_every_domain_field_is_exposed_or_deliberately_excluded() -> None:
    """Drift guard: a new domain field must be added to the schema or named here as excluded."""
    pairs: list[tuple[type, type[BaseModel], set[str]]] = [
        (ObservationIdentity, obs_schema.IdentityOut, set()),
        # pins are not discoverable through any public endpoint; both modes are always evaluated
        (
            ObservationParameters,
            obs_schema.ObservationParametersOut,
            {"stock_source_id", "benchmark_source_id", "modes"},
        ),
        (EventSection, obs_schema.EventOut, set()),
        (FilingLink, obs_schema.FilingLinkOut, set()),
        (DisclosureEvidence, obs_schema.DisclosureEvidenceOut, set()),
        (ListingResolution, obs_schema.ListingResolutionOut, set()),
        (
            ObservationProvenance,
            obs_schema.ProvenanceOut,
            {"known_gaps", "accounting_artifact_sha256"},  # exposed as accounting_artifacts
        ),
    ]
    for domain, out, excluded in pairs:
        domain_fields = {f.name for f in dataclasses.fields(domain)} - excluded
        assert domain_fields <= set(out.model_fields), (
            domain.__name__,
            domain_fields - set(out.model_fields),
        )
