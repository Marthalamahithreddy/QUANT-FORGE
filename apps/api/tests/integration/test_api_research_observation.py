"""``GET /api/v1/research/observations/{event_id}`` over real stored data, via real HTTP.

Proves: the response mirrors the domain observation (no reinterpretation), domain refusals are HTTP
200 sections, 404 only for an unknown event, exact string decimals, the request is echoed, and a GET
only reads from stored data (no write SQL, no network).
"""

# ruff: noqa: F811 - fixtures imported from the assembler integration tests

from __future__ import annotations

import json
import socket
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tests.fsds_fixture import ACC_10Q, ACC_10QA, ACC_OTHER, CIK_OTHER, build_package
from tests.integration.test_research_observation import (  # noqa: F401 - fixtures and helpers
    ACC_8K,
    ACC_8K_PLAIN,
    HORIZONS,
    NOW,
    _eps,
    _event,
    _filing,
    _ingest_facts,
    _record_statements,
    market,
)

from quantforge.api.v1.research_observation_presenter import present_research_observation
from quantforge.core.config import Settings
from quantforge.db.session import get_session
from quantforge.financials import EpsKind, EpsPeriodClass
from quantforge.ingestion.financial_facts import FinancialFactIngestionService
from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.main import create_app
from quantforge.models.benchmark import Benchmark
from quantforge.models.company import Company
from quantforge.models.enums import SecFormType
from quantforge.observation import assemble_research_observation

pytestmark = pytest.mark.integration

FORBIDDEN_KEYS = {
    "accepted_at",
    "known_at",
    "known_as_of",
    "available_at",
    "disclosed_at",
    "tradable_at",
    "known_gaps",
    "signal",
    "score",
}
QUARTER = {"eps_period_class": "QUARTER", "eps_kind": "DILUTED"}


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    application: FastAPI = create_app(Settings(_env_file=None))
    application.dependency_overrides[get_session] = lambda: db_session
    with TestClient(application) as test_client:
        yield test_client


def _get(
    client: TestClient, event_id: uuid.UUID, market: dict[str, object], **params: Any
) -> dict[str, Any]:
    benchmark = market["benchmark"]
    assert isinstance(benchmark, Benchmark)
    query: dict[str, Any] = {"horizons": "1,2", "benchmark_id": str(benchmark.id)}
    query.update(params)
    response = client.get(f"/api/v1/research/observations/{event_id}", params=query)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


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


def _floats(node: Any) -> list[float]:
    if isinstance(node, float):
        return [node]
    if isinstance(node, dict):
        return [f for v in node.values() for f in _floats(v)]
    if isinstance(node, list):
        return [f for v in node for f in _floats(v)]
    return []


def _seed_filing(db_session: Session, market: dict[str, object], tmp_path: Path) -> None:
    company = market["company"]
    assert isinstance(company, Company)
    _filing(db_session, company, ACC_10Q, SecFormType.FORM_10Q)
    _ingest_facts(db_session, tmp_path, (ACC_10Q,))


def test_happy_path_full_observation_over_http(
    client: TestClient, db_session: Session, market: dict[str, object], tmp_path: Path
) -> None:
    _seed_filing(db_session, market, tmp_path)
    event = _event(db_session, market)

    body = _get(client, event.id, market, filing_accession=ACC_10Q, **QUARTER)

    assert list(body) == [
        "identity",
        "parameters",
        "event",
        "filing_link",
        "disclosure_evidence",
        "accounting",
        "descriptive",
        "point_in_time",
        "provenance",
        "refusals",
    ]
    assert body["identity"]["event_id"] == str(event.id)
    assert body["identity"]["listing_resolution"]["status"] == "RESOLVED"
    assert body["parameters"] == {
        "horizons": [1, 2],
        "price_basis": "SPLIT_ADJUSTED",
        "security_id": None,
        "benchmark_id": body["identity"]["benchmark_id"],
        "filing_accession": ACC_10Q,
        "eps_period_class": "QUARTER",
        "eps_kind": "DILUTED",
    }
    assert body["event"]["reported_eps"] == "1.2000"
    assert body["event"]["fingerprint"] == event.fingerprint
    study = body["descriptive"]["study"]
    assert body["descriptive"]["state"] == "AVAILABLE"
    assert [r["return_value"] for r in study["stock_returns"]["forward_returns"]] == ["0.1", "0.2"]
    assert [r["benchmark_return"] for r in study["benchmark"]["returns"]] == ["0.025", "0.05"]
    assert [r["abnormal_return"] for r in study["abnormal_returns"]] == ["0.075", "0.15"]
    assert study["eps_surprise"]["absolute_surprise"] == "0.2000"
    assert body["filing_link"]["status"] == "CALLER_SUPPLIED_CIK_CONSISTENT"
    assert body["filing_link"]["period_verification"] == "PERIOD_UNVERIFIED"
    assert body["disclosure_evidence"]["level"] == "D0_FILING_EXISTS"
    assert body["accounting"]["state"] == "EVALUATED"
    eps = body["accounting"]["eps"]
    assert eps["current"]["fact"]["value"] == "1.5300"
    assert eps["comparative"]["fact"]["value"] == "1.5200"
    assert eps["change"]["eps_change"] == "0.0100"
    assert [a["role"] for a in body["provenance"]["accounting_artifacts"]] == [
        "current",
        "comparative",
    ]
    assert body["provenance"]["event_fingerprint"] == event.fingerprint
    assert not _keys(body) & FORBIDDEN_KEYS
    assert _floats(body) == [], "no JSON number may be a float"


def test_the_response_is_exactly_the_domain_observation(
    client: TestClient, db_session: Session, market: dict[str, object], tmp_path: Path
) -> None:
    _seed_filing(db_session, market, tmp_path)
    event = _event(db_session, market)
    body = _get(client, event.id, market, filing_accession=ACC_10Q, **QUARTER)
    benchmark = market["benchmark"]
    assert isinstance(benchmark, Benchmark)
    observation = assemble_research_observation(
        db_session,
        event.id,
        horizons=HORIZONS,
        benchmark_id=benchmark.id,
        filing_accession=ACC_10Q,
        eps_period_class=EpsPeriodClass.QUARTER,
        eps_kind=EpsKind.DILUTED,
    )
    assert body == json.loads(present_research_observation(observation).model_dump_json())
    # refusals mirror the domain's derived tuple, in order
    assert [(r["scope"], r["code"], r["horizon"]) for r in body["refusals"]] == [
        (r.scope.value, r.code, r.horizon) for r in observation.refusals
    ]


def test_pit_refused_is_http_200_and_descriptive_values_survive(
    client: TestClient, db_session: Session, market: dict[str, object]
) -> None:
    event = _event(db_session, market)  # observed_at NULL
    body = _get(client, event.id, market)
    assert body["point_in_time"] == {
        "state": "REFUSED",
        "eligibility": "INELIGIBLE_NO_AVAILABILITY_EVIDENCE",
        "study": None,
    }
    assert body["descriptive"]["state"] == "AVAILABLE"
    assert (
        body["descriptive"]["study"]["stock_returns"]["forward_returns"][0]["return_value"] == "0.1"
    )
    assert ("POINT_IN_TIME", "INELIGIBLE_NO_AVAILABILITY_EVIDENCE") in {
        (r["scope"], r["code"]) for r in body["refusals"]
    }


def test_an_eligible_event_carries_the_pit_study(
    client: TestClient, db_session: Session, market: dict[str, object]
) -> None:
    event = _event(db_session, market, observed_at=datetime(2026, 3, 2, 11, 0, tzinfo=UTC))
    body = _get(client, event.id, market)
    assert body["point_in_time"]["state"] == "EVIDENCE_ELIGIBLE"
    assert body["point_in_time"]["study"]["research_mode"] == "POINT_IN_TIME"
    assert body["descriptive"]["study"]["research_mode"] == "DESCRIPTIVE"
    assert body["event"]["observed_at_basis"] == "SOURCE_PUBLICATION"


def test_no_filing_is_not_linked_and_defaults_are_echoed(
    client: TestClient, db_session: Session, market: dict[str, object], tmp_path: Path
) -> None:
    _seed_filing(db_session, market, tmp_path)  # exists but must never be auto-linked
    event = _event(db_session, market)
    response = client.get(f"/api/v1/research/observations/{event.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["filing_link"]["status"] == "NOT_LINKED"
    assert body["filing_link"]["accession_number"] is None
    assert body["accounting"] == {"state": "NOT_REQUESTED", "eps": None}
    assert body["disclosure_evidence"]["level"] == "NONE"
    assert body["parameters"]["horizons"] == [1, 5, 20]
    assert body["parameters"]["benchmark_id"] is None
    assert body["parameters"]["price_basis"] == "SPLIT_ADJUSTED"
    assert body["descriptive"]["study"]["benchmark"]["benchmark_id"] is None


def test_unstored_accession_is_a_200_section_not_a_404(
    client: TestClient, db_session: Session, market: dict[str, object]
) -> None:
    event = _event(db_session, market)
    body = _get(client, event.id, market, filing_accession="0000111111-24-000001", **QUARTER)
    assert body["filing_link"]["status"] == "FILING_NOT_STORED"
    assert body["accounting"]["state"] == "LINK_REFUSED"
    assert body["accounting"]["eps"] is None
    assert body["descriptive"]["state"] == "AVAILABLE"


def test_cik_mismatch_borrows_nothing(
    client: TestClient, db_session: Session, market: dict[str, object]
) -> None:
    other = Company(legal_name="Other Co", display_name="Other Co", cik=CIK_OTHER)
    db_session.add(other)
    db_session.commit()
    _filing(db_session, other, ACC_OTHER, SecFormType.FORM_10Q)
    event = _event(db_session, market)
    body = _get(client, event.id, market, filing_accession=ACC_OTHER, **QUARTER)
    assert body["filing_link"]["status"] == "CIK_MISMATCH_REFUSED"
    assert body["filing_link"]["form"] is None
    assert body["accounting"] == {"state": "LINK_REFUSED", "eps": None}
    assert body["disclosure_evidence"]["level"] == "NONE"
    assert body["descriptive"]["state"] == "AVAILABLE"


def test_accounting_refusal_is_preserved_exactly(
    client: TestClient, db_session: Session, market: dict[str, object], tmp_path: Path
) -> None:
    company = market["company"]
    assert isinstance(company, Company)
    _filing(db_session, company, ACC_10QA, SecFormType.FORM_10Q_A)
    package = build_package(tmp_path / "p.zip", nums=[_eps(ACC_10QA, "20240331", "1", "1.5400")])
    FinancialFactIngestionService(
        session=db_session, store=RawResponseStore(root=tmp_path / "raw"), now=NOW
    ).ingest_fsds_package(package, package_name="2024q2", accessions=(ACC_10QA,))
    db_session.commit()
    event = _event(db_session, market)
    body = _get(client, event.id, market, filing_accession=ACC_10QA, **QUARTER)
    eps = body["accounting"]["eps"]
    assert body["accounting"]["state"] == "EVALUATED"
    assert eps["comparative"]["status"] == "COMPARATIVE_MISSING"
    assert eps["change"]["status"] == "INPUT_UNAVAILABLE" and eps["change"]["eps_change"] is None
    assert eps["form"] == "10-Q/A", "the exact amendment accession, never the original"
    assert ("ACCOUNTING", "COMPARATIVE_MISSING") in {
        (r["scope"], r["code"]) for r in body["refusals"]
    }


def test_d0_and_d1_serialise_exactly_and_d1_does_not_upgrade_pit(
    client: TestClient, db_session: Session, market: dict[str, object]
) -> None:
    company = market["company"]
    assert isinstance(company, Company)
    _filing(db_session, company, ACC_8K, SecFormType.FORM_8K, items="2.02,9.01")
    _filing(db_session, company, ACC_8K_PLAIN, SecFormType.FORM_8K, items="5.02,12.02")
    event = _event(db_session, market)
    d1 = _get(client, event.id, market, filing_accession=ACC_8K, **QUARTER)
    d0 = _get(client, event.id, market, filing_accession=ACC_8K_PLAIN, **QUARTER)
    assert d1["disclosure_evidence"] == {
        "level": "D1_ITEM_2_02_DECLARED",
        "accession_number": ACC_8K,
        "form": "8-K",
        "items": "2.02,9.01",
    }
    assert d0["disclosure_evidence"]["level"] == "D0_FILING_EXISTS"
    assert d1["point_in_time"]["state"] == "REFUSED"
    assert set(d1["disclosure_evidence"]) == {"level", "accession_number", "form", "items"}


def test_benchmark_without_data_is_a_200_refusal(
    client: TestClient, db_session: Session, market: dict[str, object]
) -> None:
    empty = Benchmark(name="Empty Index", symbol="EMPT", currency="USD")
    db_session.add(empty)
    db_session.commit()
    event = _event(db_session, market)
    response = client.get(
        f"/api/v1/research/observations/{event.id}",
        params={"horizons": "1,2", "benchmark_id": str(empty.id)},
    )
    assert response.status_code == 200
    body = response.json()
    assert {r["status"] for r in body["descriptive"]["study"]["benchmark"]["returns"]} == {
        "MISSING_ANCHOR_PRICE"
    }
    assert body["parameters"]["benchmark_id"] == str(empty.id)
    assert body["descriptive"]["state"] == "AVAILABLE"


def test_unknown_well_formed_event_is_404_without_internals(client: TestClient) -> None:
    response = client.get(f"/api/v1/research/observations/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Event not found."}


def test_request_response_consistency(
    client: TestClient, db_session: Session, market: dict[str, object], tmp_path: Path
) -> None:
    _seed_filing(db_session, market, tmp_path)
    event = _event(db_session, market)
    body = _get(
        client,
        event.id,
        market,
        horizons="2,1",
        filing_accession=ACC_10Q,
        eps_period_class="QUARTER",
        eps_kind="BASIC_AND_DILUTED",
    )
    assert body["parameters"]["horizons"] == [1, 2], "echoed in the effective ascending order"
    assert [
        r["horizon"] for r in body["descriptive"]["study"]["stock_returns"]["forward_returns"]
    ] == [1, 2]
    assert body["parameters"]["eps_kind"] == "BASIC_AND_DILUTED"
    assert body["accounting"]["eps"]["eps_kind"] == "BASIC_AND_DILUTED"
    assert body["accounting"]["eps"]["accession"] == ACC_10Q
    assert body["filing_link"]["accession_number"] == body["parameters"]["filing_accession"]
    assert body["identity"]["filing_accession"] == ACC_10Q


def test_tiny_negative_zero_and_repeating_decimals_stay_exact_strings(
    client: TestClient, db_session: Session, market: dict[str, object]
) -> None:
    event = _event(
        db_session,
        market,
        reported_eps=Decimal("0.0000001"),
        estimated_eps=Decimal("-3.0000"),
    )
    body = _get(client, event.id, market)
    surprise = body["descriptive"]["study"]["eps_surprise"]
    assert body["event"]["reported_eps"] == "0.0000001"
    assert body["event"]["estimated_eps"] == "-3.0000"
    for key in ("reported_eps", "estimated_eps", "absolute_surprise", "surprise_ratio"):
        assert surprise[key] is None or (
            isinstance(surprise[key], str) and "e" not in surprise[key].lower()
        )
    assert Decimal(surprise["absolute_surprise"]) == Decimal("0.0000001") - Decimal("-3.0000")
    assert _floats(body) == []


def test_read_only_provider_free_and_its_sql_cost_is_recorded(
    client: TestClient,
    db_session: Session,
    market: dict[str, object],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_filing(db_session, market, tmp_path)
    refused = _event(db_session, market)
    eligible = _event(
        db_session, market, fiscal_quarter=2, observed_at=datetime(2026, 3, 2, 11, 0, tzinfo=UTC)
    )

    def tripwire(*_a: object, **_k: object) -> None:
        raise AssertionError("network access during an observation request")

    monkeypatch.setattr(socket.socket, "connect", tripwire)
    monkeypatch.setattr(socket, "create_connection", tripwire)

    counts: dict[str, int] = {}
    for name, event in (("pit_refused", refused), ("pit_eligible", eligible)):
        db_session.expire_all()
        with _record_statements(db_session) as statements:
            _get(client, event.id, market, filing_accession=ACC_10Q, **QUARTER)
        counts[name] = len(statements)
        allowed = ("SELECT", "WITH", "SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK")
        writes = [s for s in statements if not s.lstrip().upper().startswith(allowed)]
        assert writes == [], writes
    print(f"endpoint statement counts: {counts}")  # noqa: T201
    assert counts["pit_refused"] < 40 and counts["pit_eligible"] < 80


def test_serialisation_issues_no_queries_and_is_fast(
    db_session: Session, market: dict[str, object], tmp_path: Path
) -> None:
    _seed_filing(db_session, market, tmp_path)
    event = _event(db_session, market, observed_at=datetime(2026, 3, 2, 11, 0, tzinfo=UTC))
    benchmark = market["benchmark"]
    assert isinstance(benchmark, Benchmark)
    observation = assemble_research_observation(
        db_session,
        event.id,
        horizons=HORIZONS,
        benchmark_id=benchmark.id,
        filing_accession=ACC_10Q,
        eps_period_class=EpsPeriodClass.QUARTER,
        eps_kind=EpsKind.DILUTED,
    )
    with _record_statements(db_session) as statements:
        started = time.perf_counter()
        for _ in range(20):
            present_research_observation(observation).model_dump_json()
        elapsed = (time.perf_counter() - started) / 20
    assert statements == [], "presentation must not query"
    assert elapsed < 0.1, f"one presentation took {elapsed:.4f}s"
