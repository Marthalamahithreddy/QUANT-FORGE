"""Capture real research-observation responses for the frontend's pinned contract fixture.

Frontend-owned recipe (the backend is not modified). It re-uses the backend's own integration-test
scenario builders (a real PostgreSQL test database, real HTTP through ``TestClient``) to seed
coherent stored data, calls the real endpoints and writes ``tests/contract/observation.json``.

Run from the backend project directory, with the integration database available:

    cd apps/api
    uv run --frozen pytest ../web/scripts/capture_observation_fixtures.py -q \
        -p tests.integration.conftest --rootdir=. -c pyproject.toml

The output is a CAPTURE, never edited by hand: refresh it by re-running this script. Random ids
differ between runs, which is why every entry stores its own request next to its response.
"""

# ruff: noqa: F811, E402, I001

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tests.fsds_fixture import ACC_10Q, ACC_10QA, ACC_OTHER, CIK_OTHER, build_package
from tests.integration.test_research_observation import (  # noqa: F401
    ACC_8K,
    ACC_8K_PLAIN,
    NOW,
    _eps,
    _event,
    _filing,
    _ingest_facts,
    market,
)

from quantforge.core.config import Settings
from quantforge.db.session import get_session
from quantforge.ingestion.financial_facts import FinancialFactIngestionService
from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.main import create_app
from quantforge.models.benchmark import Benchmark
from quantforge.models.company import Company
from quantforge.models.enums import EarningsTiming, SecFormType

pytestmark = pytest.mark.integration
OUT = Path(__file__).resolve().parents[1] / "tests" / "contract" / "observation.json"
QUARTER = {"eps_period_class": "QUARTER", "eps_kind": "DILUTED"}


def test_capture(db_session: Session, market: dict[str, object], tmp_path: Path) -> None:
    application: FastAPI = create_app(Settings(_env_file=None))
    application.dependency_overrides[get_session] = lambda: db_session
    client = TestClient(application)
    company = market["company"]
    benchmark = market["benchmark"]
    assert isinstance(company, Company) and isinstance(benchmark, Benchmark)

    _filing(db_session, company, ACC_10Q, SecFormType.FORM_10Q)
    _ingest_facts(db_session, tmp_path, (ACC_10Q,))
    _filing(db_session, company, ACC_8K, SecFormType.FORM_8K, items="2.02,9.01")
    _filing(db_session, company, ACC_8K_PLAIN, SecFormType.FORM_8K, items="5.02,12.02")
    _filing(db_session, company, ACC_10QA, SecFormType.FORM_10Q_A)
    package = build_package(tmp_path / "p.zip", nums=[_eps(ACC_10QA, "20240331", "1", "1.5400")])
    FinancialFactIngestionService(
        session=db_session, store=RawResponseStore(root=tmp_path / "raw"), now=NOW
    ).ingest_fsds_package(package, package_name="2024q3", accessions=(ACC_10QA,))
    other = Company(legal_name="Other Co", display_name="Other Co", cik=CIK_OTHER)
    empty = Benchmark(name="Empty Index", symbol="EMPT", currency="USD")
    inactive = Benchmark(name="Retired Index", symbol="RETD", currency="USD", is_active=False)
    db_session.add_all([other, empty, inactive])
    db_session.commit()
    _filing(db_session, other, ACC_OTHER, SecFormType.FORM_10Q)

    bid = str(benchmark.id)
    eligible_at = datetime(2026, 3, 2, 11, 0, tzinfo=UTC)
    plain = _event(db_session, market)  # observed_at NULL: point-in-time refuses
    eligible = _event(db_session, market, observed_at=eligible_at, fiscal_quarter=2)
    unknown_timing = _event(db_session, market, timing=EarningsTiming.UNKNOWN, fiscal_quarter=3)
    missing_eps = _event(
        db_session, market, reported_eps=None, estimated_eps=Decimal("1.0000"), fiscal_quarter=4
    )
    off_calendar = _event(db_session, market, event_date=date(2026, 6, 1), fiscal_year=2025)

    cases: list[tuple[str, Any, dict[str, str]]] = [
        ("default_no_benchmark", plain, {}),
        ("single_horizon", plain, {"horizons": "1"}),
        ("benchmark_selected", plain, {"horizons": "1,2", "benchmark_id": bid}),
        ("custom_horizons_unsorted", plain, {"horizons": "2,1", "benchmark_id": bid}),
        ("inactive_benchmark", plain, {"horizons": "1,2", "benchmark_id": str(inactive.id)}),
        ("benchmark_without_data", plain, {"horizons": "1,2", "benchmark_id": str(empty.id)}),
        ("pit_eligible", eligible, {"horizons": "1,2", "benchmark_id": bid}),
        ("unknown_timing", unknown_timing, {"horizons": "1,2", "benchmark_id": bid}),
        ("missing_reported_eps", missing_eps, {"horizons": "1,2"}),
        ("event_off_calendar", off_calendar, {"horizons": "1,2", "benchmark_id": bid}),
        ("filing_evaluated", plain, {"horizons": "1,2", "benchmark_id": bid, "filing_accession": ACC_10Q, **QUARTER}),
        ("filing_comparative_missing", plain, {"horizons": "1,2", "filing_accession": ACC_10QA, **QUARTER}),
        ("filing_refused_selector", plain, {"horizons": "1,2", "filing_accession": ACC_10Q, "eps_period_class": "ANNUAL", "eps_kind": "DILUTED"}),
        ("filing_d1", plain, {"horizons": "1,2", "filing_accession": ACC_8K, **QUARTER}),
        ("filing_d0_plain_8k", plain, {"horizons": "1,2", "filing_accession": ACC_8K_PLAIN, **QUARTER}),
        ("filing_not_stored", plain, {"horizons": "1,2", "filing_accession": "0000111111-24-000001", **QUARTER}),
        ("filing_cik_mismatch", plain, {"horizons": "1,2", "filing_accession": ACC_OTHER, **QUARTER}),
        ("horizons_beyond_stored_sessions", plain, {"horizons": "1,5,20", "benchmark_id": bid}),
    ]
    out: dict[str, Any] = {
        "_note": "Captured from the real backend by scripts/capture_observation_fixtures.py. Do not edit by hand.",
        "cases": [],
    }
    for name, event, params in cases:
        path = f"/api/v1/research/observations/{event.id}"
        response = client.get(path, params=params)
        record = client.get(f"/api/v1/earnings-events/{event.id}")
        assert response.status_code == 200, (name, response.text)
        out["cases"].append(
            {
                "name": name,
                "event_id": str(event.id),
                "params": params,
                "response": response.json(),
                "event_record": record.json(),
            }
        )
    benchmarks = client.get("/api/v1/benchmarks", params={"limit": 50, "offset": 0})
    assert benchmarks.status_code == 200
    out["benchmarks"] = benchmarks.json()
    unknown = client.get(f"/api/v1/research/observations/{uuid.uuid4()}")
    out["unknown_event"] = {"status": unknown.status_code, "body": unknown.json()}
    rejected = client.get(f"/api/v1/research/observations/{plain.id}", params={"horizons": "0"})
    out["rejected_horizons"] = {"status": rejected.status_code, "body": rejected.json()}
    OUT.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
