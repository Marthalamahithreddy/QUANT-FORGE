"""Filing EPS API contract that needs no database: OpenAPI, serialization, presenter, structure.

The HTTP behaviour over real stored facts is covered in ``tests/integration/test_api_filing_eps.py``.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import quantforge
from quantforge.api.v1.earnings_events import EpsSurprise
from quantforge.core.config import Settings
from quantforge.financials.comparative_eps import ComparativeEpsObservation
from quantforge.financials.eps import EpsFactObservation
from quantforge.financials.eps_change import compute_eps_change
from quantforge.main import create_app
from quantforge.research.eps_surprise import EpsSurpriseStatus
from quantforge.schemas.common import fixed_point
from quantforge.schemas.filing_eps import BOUNDARY_NOTE, FilingEpsResponse
from tests.test_comparative_eps import current_fact, prior_fact, select

from quantforge.api.v1.filing_eps_presenter import (  # isort: skip
    _evidence_from_current,
    present_filing_eps,
)

SRC = Path(quantforge.__file__).resolve().parent
ROUTE = "/api/v1/filings/{accession}/eps"
FORBIDDEN_KEYS = {
    "accepted_at",
    "source_accepted",
    "source_stated_accepted",
    "ingested_at",
    "observed_at",
    "available_at",
    "known_as_of",
    "tradable",
    "signal",
    "score",
    "growth",
    "created_at",
    "ingestion_run_id",
    "raw_artifact_path",
}


def _app() -> FastAPI:
    return create_app(Settings(_env_file=None))


def _keys(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            found.add(key)
            found |= _keys(value)
    elif isinstance(node, list):
        for item in node:
            found |= _keys(item)
    return found


def _present(current: str, comparative: str) -> FilingEpsResponse:
    observation: ComparativeEpsObservation = select(
        [current_fact(value=Decimal(current)), prior_fact(value=Decimal(comparative))]
    )
    return present_filing_eps(observation, compute_eps_change(observation))


# ── fixed-point Decimal serialization ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.5300", "1.5300"),  # the Decimal's own exponent (trailing zeros) is preserved
        ("0", "0"),
        ("-0.2500", "-0.2500"),
        ("1E-7", "0.0000001"),
        ("2.5E+3", "2500"),
        ("-1E-10", "-0.0000000001"),
        ("999999999999999999999999.9999", "999999999999999999999999.9999"),
    ],
)
def test_fixed_point_text_never_uses_an_exponent_and_never_rounds(
    value: str, expected: str
) -> None:
    assert fixed_point(Decimal(value)) == expected
    assert not re.search("[eE]", fixed_point(Decimal(value)))


def test_every_financial_value_in_the_json_is_a_fixed_point_string() -> None:
    payload = json.loads(_present("1.5300", "1.5200").model_dump_json())
    assert payload["current"]["fact"]["value"] == "1.5300"
    assert payload["comparative"]["fact"]["value"] == "1.5200"
    assert payload["change"]["eps_change"] == "0.0100"
    assert (
        payload["change"]["percentage_change"]
        == "0.0065789473684210526315789473684210526315789473684211"
    )
    assert payload["change"]["percentage_semantics"] == "ratio"
    for value in (
        payload["current"]["fact"]["value"],
        payload["comparative"]["fact"]["value"],
        payload["change"]["eps_change"],
        payload["change"]["percentage_change"],
    ):
        assert isinstance(value, str) and not re.search("[eE]", value)
    assert isinstance(payload["current"]["fact"]["qtrs"], int), (
        "qtrs is legitimate integer metadata"
    )


@pytest.mark.parametrize(
    ("current", "comparative"),
    [
        ("0.0002", "0.0001"),
        ("999999999999999999999999.9999", "0.0001"),
        ("-999999999999999999999999.9999", "999999999999999999999999.9999"),
        ("0.0001", "-0.0001"),
        ("2", "3"),
    ],
)
def test_extreme_values_never_serialise_with_exponent_notation_or_as_numbers(
    current: str, comparative: str
) -> None:
    payload = json.loads(_present(current, comparative).model_dump_json())
    for section, field in (
        ("current", "fact"),
        ("comparative", "fact"),
    ):
        value = payload[section][field]["value"]
        assert isinstance(value, str) and not re.search("[eE]", value)
    for name in ("eps_change", "percentage_change"):
        value = payload["change"][name]
        assert value is None or (isinstance(value, str) and not re.search("[eE]", value))


# ── presenter: a pure mapping of the domain observations ─────────────────────────────────


def test_the_presenter_copies_domain_values_without_recomputing_them() -> None:
    observation = select([current_fact(value=Decimal("1.50")), prior_fact(value=Decimal("1.00"))])
    change = compute_eps_change(observation)
    response = present_filing_eps(observation, change)
    assert response.change.eps_change == change.eps_change
    assert response.change.transition is change.transition
    assert response.change.percentage_change == change.percentage_change
    assert response.comparative.status is observation.status
    assert response.comparative.reason == observation.reason
    assert response.current.reason == observation.current.reason
    assert response.comparative.fact is not None
    assert response.comparative.fact.fact_id == observation.comparative_fact_id
    assert response.descriptive_only
    assert response.no_point_in_time_claim
    assert response.boundary_note == BOUNDARY_NOTE


def test_the_presenter_refuses_an_observation_pair_that_do_not_belong_together() -> None:
    first = select([current_fact(), prior_fact()])
    other = select([current_fact(), prior_fact()])
    with pytest.raises(ValueError, match="computed from this comparative"):
        present_filing_eps(first, compute_eps_change(other))


def test_refusals_keep_their_precise_status_reason_and_carry_no_invented_evidence() -> None:
    observation = select([current_fact(), prior_fact(unit="CAD")])
    payload = json.loads(
        present_filing_eps(observation, compute_eps_change(observation)).model_dump_json()
    )
    assert payload["comparative"]["status"] == "COMPARATIVE_WRONG_UNIT"
    assert "CAD" in payload["comparative"]["reason"]
    assert payload["comparative"]["fact"] is None
    assert payload["change"] == {
        "status": "INPUT_UNAVAILABLE",
        "eps_change": None,
        "transition": None,
        "percentage_change": None,
        "percentage_semantics": "ratio",
        "percentage_unavailable_reason": None,
    }
    assert (
        payload["current"]["status"] == "AVAILABLE"
        and payload["current"]["fact"]["value"] == "1.5300"
    )


def test_the_response_has_no_availability_internal_or_trading_field_anywhere() -> None:
    for pair in (("1.5", "1.0"), ("-1", "-2"), ("1", "0")):
        payload = json.loads(_present(*pair).model_dump_json())
        assert not _keys(payload) & FORBIDDEN_KEYS


# ── OpenAPI ──────────────────────────────────────────────────────────────────────────────


def test_openapi_documents_required_enum_query_parameters_and_string_decimals() -> None:
    spec = _app().openapi()
    operation = spec["paths"]["/api/v1/filings/{accession}/eps"]["get"]
    params = {p["name"]: p for p in operation["parameters"]}
    assert params["accession"]["in"] == "path" and params["accession"]["required"] is True
    assert params["accession"]["schema"]["pattern"]
    for name in ("period_class", "eps_kind"):
        assert params[name]["in"] == "query" and params[name]["required"] is True
        assert "default" not in params[name]["schema"]
    components = spec["components"]["schemas"]
    assert set(_enum_values(components, params["period_class"]["schema"])) == {"QUARTER", "ANNUAL"}
    assert set(_enum_values(components, params["eps_kind"]["schema"])) == {
        "DILUTED",
        "BASIC_AND_DILUTED",
    }
    for schema, fields in (
        ("FactEvidence", ["value"]),
        ("EpsChangeSection", ["eps_change", "percentage_change"]),
    ):
        for field in fields:
            types = _types(components[schema]["properties"][field])
            assert types == {"string", "null"}, (schema, field, types)
    assert components["FactEvidence"]["properties"]["qtrs"]["type"] == "integer"
    for name in (
        "FilingEpsResponse",
        "CurrentEpsSection",
        "ComparativeEpsSection",
        "EpsChangeSection",
    ):
        assert name in components
    assert components["FilingEpsResponse"]["properties"]["descriptive_only"]["const"] is True
    assert components["FilingEpsResponse"]["properties"]["no_point_in_time_claim"]["const"] is True
    assert "number" not in json.dumps(components["EpsChangeSection"])


def _types(prop: dict[str, Any]) -> set[str]:
    options = prop.get("anyOf", [prop])
    return {o["type"] for o in options if "type" in o}


def _enum_values(components: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    if "$ref" in schema:
        schema = components[schema["$ref"].rsplit("/", 1)[1]]
    return list(schema["enum"])


# ── validation (never reaches the database) ──────────────────────────────────────────────

VALID_ACCESSION = "0000320193-24-000010"


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?period_class=QUARTER",
        "?eps_kind=DILUTED",
        "?period_class=MONTHLY&eps_kind=DILUTED",
        "?period_class=QUARTER&eps_kind=BASIC",
        "?period_class=quarter&eps_kind=DILUTED",
        "?period_class=QUARTER&eps_kind=DILUTED&unknown=1",
    ],
)
def test_missing_invalid_or_extra_query_parameters_are_422_with_no_defaults(query: str) -> None:
    with TestClient(_app()) as client:
        assert client.get(f"/api/v1/filings/{VALID_ACCESSION}/eps{query}").status_code == 422


@pytest.mark.parametrize(
    "accession",
    [
        "abc",
        "000032019324000010",
        "0000320193-24-00001",
        "0000320193-24-0000100",
        " 0000320193-24-000010",
        "0000320193-2024-000010",
    ],
)
def test_a_malformed_accession_is_422(accession: str) -> None:
    with TestClient(_app()) as client:
        response = client.get(
            f"/api/v1/filings/{accession}/eps",
            params={"period_class": "QUARTER", "eps_kind": "DILUTED"},
        )
    assert response.status_code == 422


# ── structure: thin, no financial semantics, no provider, no write ───────────────────────

API_MODULES = ("api/v1/filing_eps.py", "api/v1/filing_eps_presenter.py", "schemas/filing_eps.py")


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


@pytest.mark.parametrize("relative", API_MODULES)
def test_the_filing_eps_surface_calls_no_provider_and_imports_no_ingestion_or_signal_code(
    relative: str,
) -> None:
    forbidden = (
        "quantforge.providers",
        "quantforge.ingestion",
        "quantforge.signals",
        "quantforge.research",
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


@pytest.mark.parametrize("relative", API_MODULES)
def test_the_filing_eps_surface_contains_no_financial_semantics_or_arithmetic(
    relative: str,
) -> None:
    text = (SRC / relative).read_text(encoding="utf-8")
    tree = ast.parse(text)
    for token in (
        "duration_quarters ==",
        "month_distance",
        "classify_transition",
        "is_generic_fact",
        "quantize",
        ".subtract(",
        ".divide(",
        "Decimal(",
        "float(",
        "abs(",
        ".commit(",
        ".add(",
        ".execute(",
        ".delete(",
    ):
        assert token not in text, f"{relative} must not contain {token!r}"
    ops = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Sub, ast.Div))
    ]
    assert ops == [], "the API layer performs no arithmetic"
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }
    assert not names & {
        "accepted_at",
        "source_accepted",
        "ingested_at",
        "observed_at",
        "utcnow",
        "now",
    }


def test_the_router_only_composes_the_extraction_and_change_functions() -> None:
    names = _imports(SRC / "api/v1/filing_eps.py")
    assert "quantforge.financials.comparative_eps_extraction" in names
    assert "quantforge.financials.eps_change" in names
    assert not any(n.startswith("quantforge.repositories") for n in names)
    assert "sqlalchemy" not in names, "no query construction in the router"


def test_openapi_marks_the_boundary_and_semantics_constants_required_and_documents_503() -> None:
    spec = _app().openapi()
    components = spec["components"]["schemas"]
    assert {"descriptive_only", "no_point_in_time_claim", "boundary_note"} <= set(
        components["FilingEpsResponse"]["required"]
    )
    assert "meaning" in components["ComparativeEpsSection"]["required"]
    assert "percentage_semantics" in components["EpsChangeSection"]["required"]
    responses = spec["paths"]["/api/v1/filings/{accession}/eps"]["get"]["responses"]
    assert {"200", "422", "503"} <= set(responses)


def test_the_public_wire_enum_values_are_pinned() -> None:
    components = _app().openapi()["components"]["schemas"]
    wire = {
        name: set(components[name]["enum"])
        for name in (
            "EpsStatus",
            "ComparativeEpsStatus",
            "EpsChangeStatus",
            "EpsTransition",
            "PercentageUnavailableReason",
            "EpsPeriodClass",
            "EpsKind",
        )
    }
    assert wire["EpsStatus"] == {
        "AVAILABLE",
        "NOT_INGESTED",
        "UNSUPPORTED_FORM_PERIOD",
        "MISSING",
        "EXTENSION_ONLY",
        "WRONG_UNIT",
        "DIMENSIONAL_ONLY",
        "COREG_ONLY",
        "NULL_VALUE",
        "AMBIGUOUS",
    }
    assert wire["ComparativeEpsStatus"] == {
        "AVAILABLE",
        "CURRENT_UNAVAILABLE",
        "COMPARATIVE_MISSING",
        "COMPARATIVE_PERIOD_MISMATCH",
        "COMPARATIVE_DIMENSIONAL_ONLY",
        "COMPARATIVE_COREG_ONLY",
        "COMPARATIVE_WRONG_UNIT",
        "COMPARATIVE_NULL_VALUE",
        "COMPARATIVE_AMBIGUOUS",
    }
    assert wire["EpsChangeStatus"] == {"AVAILABLE", "INPUT_UNAVAILABLE"}
    assert wire["EpsTransition"] == {
        "POSITIVE_INCREASED",
        "POSITIVE_DECREASED",
        "POSITIVE_UNCHANGED",
        "LOSS_NARROWED",
        "LOSS_WIDENED",
        "LOSS_UNCHANGED",
        "LOSS_TO_PROFIT",
        "PROFIT_TO_LOSS",
        "ZERO_TO_PROFIT",
        "ZERO_TO_LOSS",
        "PROFIT_TO_ZERO",
        "LOSS_TO_ZERO",
        "ZERO_UNCHANGED",
    }
    assert wire["PercentageUnavailableReason"] == {"ZERO_COMPARATIVE", "NEGATIVE_COMPARATIVE"}
    assert wire["EpsPeriodClass"] == {"QUARTER", "ANNUAL"}
    assert wire["EpsKind"] == {"DILUTED", "BASIC_AND_DILUTED"}


def test_a_partial_current_fact_fails_loudly_instead_of_dropping_evidence() -> None:
    observation = select([current_fact(), prior_fact()])
    partial = object.__new__(EpsFactObservation)  # bypasses the constructor's all-or-none check
    for field in dataclasses.fields(observation.current):
        object.__setattr__(partial, field.name, getattr(observation.current, field.name))
    object.__setattr__(partial, "unit", None)
    with pytest.raises(RuntimeError, match="partial fact"):
        _evidence_from_current(partial)


def test_the_shared_decimal_text_keeps_the_earnings_api_contract() -> None:
    """DecimalText moved to schemas/common.py: the earnings API's public contract is unchanged."""
    spec = _app().openapi()["components"]["schemas"]
    for field in ("reported_eps", "estimated_eps", "reported_revenue", "estimated_revenue"):
        assert _types(spec["EarningsEventItem"]["properties"][field]) == {"string", "null"}
    for field in ("absolute", "ratio"):
        assert _types(spec["EpsSurprise"]["properties"][field]) == {"string", "null"}
    surprise = EpsSurprise(
        absolute=Decimal("0.0100"), ratio=None, status=EpsSurpriseStatus.AVAILABLE, reason="r"
    )
    assert surprise.model_dump(mode="json")["absolute"] == "0.0100", "trailing zeros kept"
    assert surprise.model_dump(mode="json")["ratio"] is None, "null stays null"
