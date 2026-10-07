"""Capture real saved-research-record responses for the frontend's pinned contract fixture.

Frontend-owned recipe (the backend is not modified). It re-uses the backend's own integration-test
scenario builders (a real PostgreSQL test database, real HTTP through ``TestClient``) to persist
immutable records through the backend's own save service, then calls the three real read endpoints
and writes ``tests/contract/records.json``.

Nothing here saves through HTTP (no such endpoint exists); records are persisted with the backend's
``save_research_record`` exactly as the backend's own tests do. The frontend never saves.

Run from the backend project directory, with the integration database available:

    cd apps/api
    uv run --frozen pytest ../web/scripts/capture_record_fixtures.py -q \
        -p tests.integration.conftest --rootdir=. -c pyproject.toml

The output is a CAPTURE, never edited by hand: refresh it by re-running this script. Random ids
differ between runs, which is why every entry stores its own ids next to its responses.

Order matters and is deliberate: partial baselines are saved BEFORE ingestion runs are pinned, the
list is captured before any record is made unreadable, and the evidence-changing mutation comes
last (it moves a bar every record of the listing consumed).
"""

# ruff: noqa: F811, E402, I001

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tests.fsds_fixture import ACC_10Q, ACC_10QA
from tests.integration.test_reproducibility import QUARTER, _bump, _run
from tests.integration.test_research_observation import (  # noqa: F401
    ACC_8K,
    _event,
    _filing,
    _ingest_facts,
    market,
)

from quantforge.core.config import Settings
from quantforge.db.session import get_session
from quantforge.main import create_app
from quantforge.models.benchmark import Benchmark, BenchmarkPriceBar
from quantforge.models.company import Company
from quantforge.models.earnings import EarningsEvent
from quantforge.models.enums import SecFormType
from quantforge.models.filing import SecFiling
from quantforge.models.market import DailyPriceBar
from quantforge.models.research_record import ResearchObservationRecord
from quantforge.reproducibility.canonical import canonical_text, fingerprint_of_text
from quantforge.research_records import save_research_record
from quantforge.financials import EpsKind, EpsPeriodClass

pytestmark = pytest.mark.integration
OUT = Path(__file__).resolve().parents[1] / "tests" / "contract" / "records.json"
BASE = "/api/v1/research/records"
GUARD = "ALTER TABLE research_observation_record {} TRIGGER USER"
QUARTER_KW: dict[str, Any] = {"eps_period_class": EpsPeriodClass.QUARTER, "eps_kind": EpsKind.DILUTED}


@contextmanager
def immutability_lifted(db_session: Session) -> Iterator[None]:
    """Lifts the record table's immutability trigger for a fixture-only tamper, always restoring it."""
    db_session.execute(sa.text(GUARD.format("DISABLE")))
    try:
        yield
    finally:
        db_session.execute(sa.text(GUARD.format("ENABLE")))


def test_capture(db_session: Session, market: dict[str, object], tmp_path: Path) -> None:
    application: FastAPI = create_app(Settings(_env_file=None))
    application.dependency_overrides[get_session] = lambda: db_session
    client = TestClient(application, raise_server_exceptions=False)
    company = market["company"]
    benchmark = market["benchmark"]
    assert isinstance(company, Company) and isinstance(benchmark, Benchmark)

    out: dict[str, Any] = {
        "_note": "Captured from the real backend by scripts/capture_record_fixtures.py. Do not edit by hand.",
        "records": [],
    }

    def get(path: str, **params: Any) -> dict[str, Any]:
        db_session.expire_all()
        response = client.get(path, params=params or None)
        try:
            body = response.json()
        except ValueError:
            body = None
        return {"status": response.status_code, "body": body}

    out["empty_list"] = get(BASE)
    out["benchmarks"] = get("/api/v1/benchmarks")

    counter = {"n": 0}

    def event(**overrides: Any) -> EarningsEvent:
        counter["n"] += 1
        return _event(db_session, market, fiscal_year=2000 + counter["n"], **overrides)

    def save(name: str, ev: EarningsEvent, horizons: list[int], **kwargs: Any) -> dict[str, Any]:
        saved = save_research_record(db_session, ev.id, horizons=horizons, **kwargs)
        db_session.commit()
        entry = {
            "name": name,
            "record_id": str(saved.record.record_id),
            "event_id": str(ev.id),
            "created": saved.created,
        }
        out["records"].append(entry)
        return entry

    def capture(entry: dict[str, Any], *, verify: bool = True) -> None:
        entry["detail"] = get(f"{BASE}/{entry['record_id']}")
        if verify:
            entry["verify"] = get(f"{BASE}/{entry['record_id']}/verify")
        entry["event_record"] = get(f"/api/v1/earnings-events/{entry['event_id']}")
        # What "Re-run this request live" asks the workspace for: the same event with the request's
        # own parameters, captured here so a fixture-backed browser run can render the live study.
        request = entry["detail"]["body"]["request"]
        params: dict[str, Any] = {"horizons": ",".join(str(h) for h in request["horizons"])}
        if request["benchmark_id"] is not None:
            params["benchmark_id"] = request["benchmark_id"]
        if request["filing_accession"] is not None:
            params["filing_accession"] = request["filing_accession"]
            params["eps_period_class"] = request["eps_period_class"]
            params["eps_kind"] = request["eps_kind"]
        entry["rerun_observation"] = {
            "params": params,
            **get(f"/api/v1/research/observations/{entry['event_id']}", **params),
        }

    # ── PARTIAL baselines: saved before anything is pinned ───────────────────────────────
    p_bench = save("partial_benchmark", event(), [1, 2], benchmark_id=benchmark.id)
    capture(p_bench)

    filing_q = _filing(db_session, company, ACC_10Q, SecFormType.FORM_10Q)
    _ingest_facts(db_session, tmp_path, (ACC_10Q,))
    p_filing = save(
        "partial_filing_accounting", event(), [1, 2], filing_accession=ACC_10Q, **QUARTER_KW
    )
    capture(p_filing)

    _filing(db_session, company, ACC_10QA, SecFormType.FORM_10Q_A)  # stored, no facts ingested
    p_unread = save(
        "partial_accounting_not_ingested", event(), [1], filing_accession=ACC_10QA, **QUARTER_KW
    )
    capture(p_unread)

    # ── pin every ingestion run, so COMPLETE baselines can exist ─────────────────────────
    run = _run(db_session)
    db_session.execute(sa.update(DailyPriceBar).values(ingestion_run_id=run.id))
    db_session.execute(sa.update(BenchmarkPriceBar).values(ingestion_run_id=run.id))
    db_session.execute(sa.update(SecFiling).values(ingestion_run_id=run.id))
    db_session.commit()

    def complete_event(**overrides: Any) -> EarningsEvent:
        ev = event(**overrides)
        ev.ingestion_run_id = run.id
        db_session.commit()
        return ev

    c_plain = save("complete_plain", complete_event(), [1, 2])
    capture(c_plain)
    c_bench = save("complete_benchmark", complete_event(), [1, 2], benchmark_id=benchmark.id)
    capture(c_bench)
    c_filing = save(
        "complete_filing_accounting",
        complete_event(),
        [1, 2],
        benchmark_id=benchmark.id,
        filing_accession=ACC_10Q,
        **QUARTER_KW,
    )
    capture(c_filing)
    c_pit = save(
        "complete_pit_eligible",
        complete_event(observed_at=datetime(2026, 3, 2, 11, 0, tzinfo=UTC)),
        [1, 2],
        benchmark_id=benchmark.id,
    )
    capture(c_pit)
    c_default = save("complete_default_horizons", complete_event(), [1, 5, 20])
    capture(c_default)
    del filing_q

    # ── RESULT_CHANGED: a stored baseline whose recorded result differs from a rerun ─────
    rc = save("result_changed", complete_event(), [1])
    row = db_session.get(ResearchObservationRecord, uuid.UUID(rc["record_id"]))
    assert row is not None
    altered = json.loads(row.canonical_result)
    altered["event"]["reported_eps"] = "9.9999"
    text = canonical_text(altered)
    with immutability_lifted(db_session):
        db_session.execute(
            sa.text(
                "UPDATE research_observation_record SET canonical_result = :t, result_fingerprint = :f "
                "WHERE id = :i"
            ),
            {"t": text, "f": fingerprint_of_text(text).digest, "i": row.id},
        )
    db_session.commit()
    capture(rc)

    # ── 409: the record's event row is gone; and the two corrupt-record cases ────────────
    gone = save("event_missing", complete_event(), [1])
    corrupt_manifest = save("corrupt_manifest", complete_event(), [1])
    corrupt_digest = save("corrupt_digest", complete_event(), [1])
    manifest_changed = save("manifest_changed", complete_event(), [1])

    # ── lists: after every record exists, before any is made unreadable ──────────────────
    out["lists"] = {
        "default": get(BASE),
        "limit_20": get(BASE, limit=20, offset=0),
        "limit_3_offset_0": get(BASE, limit=3, offset=0),
        "limit_3_offset_3": get(BASE, limit=3, offset=3),
        "limit_3_offset_9": get(BASE, limit=3, offset=9),
        "past_end": get(BASE, limit=20, offset=400),
        "limit_rejected": get(BASE, limit=201),
    }

    for entry in (gone, corrupt_manifest, corrupt_digest, manifest_changed):
        entry["detail"] = get(f"{BASE}/{entry['record_id']}")
        entry["event_record"] = get(f"/api/v1/earnings-events/{entry['event_id']}")

    db_session.execute(sa.delete(EarningsEvent).where(EarningsEvent.id == uuid.UUID(gone["event_id"])))
    db_session.commit()
    gone["verify"] = get(f"{BASE}/{gone['record_id']}/verify")
    gone["event_record_after"] = get(f"/api/v1/earnings-events/{gone['event_id']}")

    with immutability_lifted(db_session):
        db_session.execute(
            sa.text(
                "UPDATE research_observation_record SET canonical_manifest = canonical_manifest || ' ' "
                "WHERE id = :i"
            ),
            {"i": uuid.UUID(corrupt_manifest["record_id"])},
        )
        db_session.execute(
            sa.text(
                "UPDATE research_observation_record SET result_fingerprint = repeat('0', 64) WHERE id = :i"
            ),
            {"i": uuid.UUID(corrupt_digest["record_id"])},
        )
    db_session.commit()
    for entry in (corrupt_manifest, corrupt_digest):
        entry["detail_after"] = get(f"{BASE}/{entry['record_id']}")
        entry["verify"] = get(f"{BASE}/{entry['record_id']}/verify")

    # ── MANIFEST_CHANGED: a consumed stock bar moved (last: it affects the whole listing) ─
    stock = json.loads(
        db_session.get(ResearchObservationRecord, uuid.UUID(manifest_changed["record_id"])).canonical_manifest  # type: ignore[union-attr]
    )["evidence"]["stock"]
    _bump(
        db_session,
        DailyPriceBar,
        listing_id=uuid.UUID(stock["listing_id"]),
        trading_date=datetime.fromisoformat(stock["bars"][1]["session"]).date(),
    )
    manifest_changed["verify"] = get(f"{BASE}/{manifest_changed['record_id']}/verify")

    unknown = uuid.uuid4()
    out["unknown_record"] = {
        "record_id": str(unknown),
        "detail": get(f"{BASE}/{unknown}"),
        "verify": get(f"{BASE}/{unknown}/verify"),
    }
    out["malformed_id"] = {"detail": get(f"{BASE}/not-a-uuid"), "verify": get(f"{BASE}/not-a-uuid/verify")}
    OUT.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
