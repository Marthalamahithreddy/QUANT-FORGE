# ruff: noqa: F811 - fixtures imported from the observation integration tests
"""Step 11.0A: ``GET /api/v1/research/records[/{id}[/verify]]`` over real stored records, via real HTTP.

Proves: bounded deterministic pagination without N+1, the detail is served only after integrity
validation, verification reuses the stored-baseline verifier and writes nothing, no provider is
contacted, a corrupt record is a generic 500, and financial values stay exact strings.
"""

from __future__ import annotations

import json
import socket
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tests.integration.test_reproducibility import _bump, _pin_runs
from tests.integration.test_research_observation import (  # noqa: F401 - fixtures and helpers
    _event,
    _record_statements,
    market,
)

from quantforge.core.config import Settings
from quantforge.db.session import get_session
from quantforge.main import create_app
from quantforge.models.benchmark import Benchmark
from quantforge.models.earnings import EarningsEvent
from quantforge.models.market import DailyPriceBar
from quantforge.models.research_record import ResearchObservationRecord
from quantforge.reproducibility.canonical import canonical_text, fingerprint_of_text
from quantforge.research_records import save_research_record

pytestmark = pytest.mark.integration

BASE = "/api/v1/research/records"
GUARD = "ALTER TABLE research_observation_record {} TRIGGER USER"
LIST_KEYS = {
    "record_id",
    "created_at",
    "hash_algorithm",
    "manifest_fingerprint",
    "result_fingerprint",
    "reproducibility_level",
    "reproducibility_gaps",
    "request",
}


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    application: FastAPI = create_app(Settings(_env_file=None))
    application.dependency_overrides[get_session] = lambda: db_session
    with TestClient(application, raise_server_exceptions=False) as test_client:
        yield test_client


def _complete_event(db_session: Session, market: dict[str, object]) -> Any:
    event = _event(db_session, market)
    _pin_runs(db_session, event)
    return event


def _save(db_session: Session, event: Any, horizons: list[int], **kwargs: Any) -> uuid.UUID:
    saved = save_research_record(db_session, event.id, horizons=horizons, **kwargs)
    db_session.commit()
    return saved.record.record_id


def _row(db_session: Session, record_id: uuid.UUID) -> ResearchObservationRecord:
    db_session.expire_all()
    row = db_session.get(ResearchObservationRecord, record_id)
    assert row is not None
    return row


def _set_created_at(db_session: Session, stamps: dict[uuid.UUID, datetime]) -> None:
    """Persistence metadata only; the immutability trigger is lifted inside the test's savepoint."""
    db_session.execute(sa.text(GUARD.format("DISABLE")))
    for record_id, stamp in stamps.items():
        db_session.execute(
            sa.text("UPDATE research_observation_record SET created_at = :t WHERE id = :i"),
            {"t": stamp, "i": record_id},
        )
    db_session.execute(sa.text(GUARD.format("ENABLE")))
    db_session.expire_all()


def _no_float(token: str) -> object:
    raise AssertionError(f"a JSON float {token!r} appeared on the wire")


def _snapshot(db_session: Session) -> list[tuple[Any, ...]]:
    db_session.expire_all()
    return [
        tuple(r)
        for r in db_session.execute(
            sa.text("SELECT * FROM research_observation_record ORDER BY id")
        ).all()
    ]


# ── list ─────────────────────────────────────────────────────────────────────────────────


def test_an_empty_store_lists_an_empty_page(client: TestClient) -> None:
    response = client.get(BASE)
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


def test_one_record_lists_a_small_summary_without_canonical_payloads(
    client: TestClient, db_session: Session, market: dict[str, object]
) -> None:
    event = _complete_event(db_session, market)
    record_id = _save(db_session, event, [1, 2])
    row = _row(db_session, record_id)

    response = client.get(BASE)
    assert response.status_code == 200
    body = response.json()
    assert (body["total"], body["limit"], body["offset"]) == (1, 50, 0)
    (item,) = body["items"]
    assert set(item) == LIST_KEYS
    assert item["record_id"] == str(record_id)
    assert item["manifest_fingerprint"] == row.manifest_fingerprint
    assert item["result_fingerprint"] == row.result_fingerprint
    assert item["hash_algorithm"] == "sha256"
    assert item["reproducibility_level"] == "COMPLETE" and item["reproducibility_gaps"] == []
    assert item["request"] == {
        "event_id": str(event.id),
        "horizons": [1, 2],
        "benchmark_id": None,
        "security_id": None,
        "filing_accession": None,
        "eps_period_class": None,
        "eps_kind": None,
    }
    assert datetime.fromisoformat(item["created_at"]).tzinfo is not None
    assert "evidence" not in response.text and "canonical" not in response.text
    assert len(response.content) < 1_500, "a list row is a summary, not the 20 KB payload"
    json.loads(response.text, parse_float=_no_float)


def test_ordering_is_newest_first_with_an_id_tiebreak_and_pages_are_stable(
    client: TestClient, db_session: Session, market: dict[str, object]
) -> None:
    event = _complete_event(db_session, market)
    ids = [_save(db_session, event, horizons) for horizons in ([1], [2], [3], [1, 2], [1, 3])]
    base = datetime(2026, 1, 1, tzinfo=UTC)
    stamps = {
        ids[0]: base,
        ids[1]: base + timedelta(days=1),
        ids[2]: base + timedelta(days=2),
        ids[3]: base + timedelta(days=2),  # ties with ids[2]: the id decides, descending
        ids[4]: base + timedelta(days=3),
    }
    _set_created_at(db_session, stamps)
    expected = sorted(ids, key=lambda i: (stamps[i], i), reverse=True)

    full = client.get(BASE, params={"limit": 200}).json()
    assert [item["record_id"] for item in full["items"]] == [str(i) for i in expected]
    assert client.get(BASE, params={"limit": 200}).json() == full, "repeatable on unchanged data"

    seen: list[str] = []
    for offset in (0, 2, 4):
        page = client.get(BASE, params={"limit": 2, "offset": offset}).json()
        assert (page["total"], page["limit"], page["offset"]) == (5, 2, offset)
        seen.extend(item["record_id"] for item in page["items"])
    assert seen == [str(i) for i in expected], "pages neither repeat nor skip a row"
    assert [
        i["record_id"] for i in client.get(BASE, params={"limit": 2, "offset": 4}).json()["items"]
    ] == [str(expected[4])]

    beyond = client.get(BASE, params={"limit": 2, "offset": 5})
    assert beyond.status_code == 200
    assert beyond.json() == {"items": [], "total": 5, "limit": 2, "offset": 5}


def test_the_maximum_page_is_bounded_and_the_list_issues_two_selects_not_one_per_row(
    client: TestClient, db_session: Session, market: dict[str, object]
) -> None:
    event = _complete_event(db_session, market)
    seed = _save(db_session, event, [1])
    db_session.execute(
        sa.text(GUARD.format("DISABLE"))
    )  # TRUNCATE-free bulk INSERT, not a mutation
    db_session.execute(sa.text(GUARD.format("ENABLE")))
    db_session.execute(
        sa.text(
            """
            INSERT INTO research_observation_record
              (id, created_at, manifest_fingerprint, manifest_hash_algorithm, canonical_manifest,
               result_fingerprint, result_hash_algorithm, canonical_result,
               reproducibility_level, reproducibility_gaps)
            SELECT gen_random_uuid(), now() + (n || ' seconds')::interval,
                   md5(n::text) || md5(n::text || 'm'), manifest_hash_algorithm, canonical_manifest,
                   md5(n::text) || md5(n::text || 'r'), result_hash_algorithm, canonical_result,
                   reproducibility_level, reproducibility_gaps
            FROM research_observation_record, generate_series(1, 249) AS n WHERE id = :seed
            """
        ),
        {"seed": seed},
    )
    db_session.expire_all()

    with _record_statements(db_session) as statements:
        response = client.get(BASE, params={"limit": 200})
    assert response.status_code == 200
    body = response.json()
    assert (len(body["items"]), body["total"]) == (200, 250)
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(statements) == len(selects) == 2, statements  # page + total, whatever the row count
    assert not any("canonical_result" in s for s in statements), "payload columns are never read"
    # the JSONB extraction runs over the paged ids only (a flat query casts EVERY row: 45 ms at 5k)
    assert "JOIN (SELECT research_observation_record.id" in statements[0]
    assert len(response.content) < 200 * 1_500

    with _record_statements(db_session) as small:
        assert client.get(BASE, params={"limit": 1}).status_code == 200
    assert len(small) == 2, "the statement count does not depend on the page size"
    assert client.get(BASE, params={"limit": 201}).status_code == 422


# ── get ──────────────────────────────────────────────────────────────────────────────────


def test_get_returns_the_parsed_baseline_exactly_as_stored_with_one_select(
    client: TestClient, db_session: Session, market: dict[str, object]
) -> None:
    event = _complete_event(db_session, market)
    record_id = _save(db_session, event, [1, 2])
    row = _row(db_session, record_id)
    db_session.expire_all()  # a request has a fresh session: the identity map must not answer it

    with _record_statements(db_session) as statements:
        response = client.get(f"{BASE}/{record_id}")
    assert response.status_code == 200, response.text
    assert len(statements) == 1 and statements[0].lstrip().upper().startswith("SELECT")
    body = json.loads(response.text, parse_float=_no_float)
    assert body["manifest"] == json.loads(row.canonical_manifest)
    assert body["result"] == json.loads(row.canonical_result)
    assert body["record_id"] == str(record_id)
    assert body["manifest_fingerprint"] == fingerprint_of_text(row.canonical_manifest).digest
    assert body["result_fingerprint"] == fingerprint_of_text(row.canonical_result).digest
    assert body["reproducibility_level"] == "COMPLETE" and body["reproducibility_gaps"] == []
    assert body["request"]["event_id"] == str(event.id) and body["request"]["horizons"] == [1, 2]
    assert "canonical_manifest" not in body and "canonical_result" not in body
    # the stored financial values reach the wire as the exact strings that were hashed
    close = body["manifest"]["evidence"]["stock"]["bars"][0]["close"]
    assert isinstance(close, str) and close in row.canonical_manifest


def test_get_a_partial_record_shows_its_gaps(
    client: TestClient, db_session: Session, market: dict[str, object]
) -> None:
    event = _event(db_session, market)  # no ingestion runs: PARTIAL
    benchmark = market["benchmark"]
    assert isinstance(benchmark, Benchmark)
    record_id = _save(db_session, event, [1], benchmark_id=benchmark.id)
    body = client.get(f"{BASE}/{record_id}").json()
    assert body["reproducibility_level"] == "PARTIAL"
    assert set(body["reproducibility_gaps"]) == {
        "EVENT_WITHOUT_INGESTION_RUN",
        "STOCK_BAR_WITHOUT_INGESTION_RUN",
        "BENCHMARK_BARS_WITHOUT_INGESTION_RUN",
    }
    listed = client.get(BASE).json()["items"][0]
    assert listed["reproducibility_gaps"] == body["reproducibility_gaps"]
    assert listed["request"]["benchmark_id"] == str(benchmark.id)


def test_an_unknown_well_formed_id_is_404_and_a_malformed_one_is_422(client: TestClient) -> None:
    unknown = uuid.uuid4()
    assert client.get(f"{BASE}/{unknown}").status_code == 404
    assert client.get(f"{BASE}/{unknown}/verify").status_code == 404
    assert client.get(f"{BASE}/not-a-uuid").status_code == 422
    assert client.get(f"{BASE}/not-a-uuid/verify").status_code == 422


@pytest.mark.parametrize("tamper", ["manifest_text", "result_text", "digest", "level"])
def test_a_corrupt_stored_record_is_a_generic_500_on_get_and_verify_but_still_lists(
    client: TestClient, db_session: Session, market: dict[str, object], tamper: str
) -> None:
    event = _complete_event(db_session, market)
    record_id = _save(db_session, event, [1])
    changes = {
        "manifest_text": "canonical_manifest = canonical_manifest || ' '",
        "result_text": "canonical_result = canonical_result || ' '",
        "digest": "result_fingerprint = repeat('0', 64)",
        "level": "reproducibility_level = 'PARTIAL', reproducibility_gaps = '[\"EVENT_WITHOUT_INGESTION_RUN\"]'::jsonb",
    }
    original = _row(db_session, record_id)
    fragment = original.canonical_result[40:120]
    db_session.execute(sa.text(GUARD.format("DISABLE")))
    db_session.execute(sa.text(f"UPDATE research_observation_record SET {changes[tamper]}"))
    db_session.execute(sa.text(GUARD.format("ENABLE")))
    db_session.expire_all()

    for url in (f"{BASE}/{record_id}", f"{BASE}/{record_id}/verify"):
        response = client.get(url)
        assert response.status_code == 500, (tamper, url)
        assert response.json() == {"detail": "Stored research record failed integrity validation."}
        assert fragment not in response.text and "evidence" not in response.text
        assert str(record_id) not in response.text
    # the list reads stored columns only: it neither hashes nor hides the record
    assert client.get(BASE).json()["total"] == 1


# ── verify ───────────────────────────────────────────────────────────────────────────────


def test_verify_match_for_a_complete_baseline(
    client: TestClient, db_session: Session, market: dict[str, object]
) -> None:
    event = _complete_event(db_session, market)
    record_id = _save(db_session, event, [1, 2])
    response = client.get(f"{BASE}/{record_id}/verify")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "MATCH" and body["baseline"] == "SUPPLIED"
    assert body["record_id"] == str(record_id)
    assert body["reproducibility_level"] == "COMPLETE"
    assert body["changed_sections"] == [] and body["changed_paths"] == []
    assert body["changed_result_paths"] == []
    assert body["expected"] == body["actual"], "same manifest, same result, same level"
    row = _row(db_session, record_id)
    assert body["expected"]["fingerprints"]["manifest"] == row.manifest_fingerprint
    assert body["expected"]["fingerprints"]["result"] == row.result_fingerprint
    assert set(body["expected"]["fingerprints"]) == {
        "request",
        "methodology",
        "evidence",
        "manifest",
        "result",
    }


def test_verify_a_partial_baseline_is_evidence_incomplete(
    client: TestClient, db_session: Session, market: dict[str, object]
) -> None:
    event = _event(db_session, market)
    record_id = _save(db_session, event, [1])
    body = client.get(f"{BASE}/{record_id}/verify").json()
    assert body["status"] == "EVIDENCE_INCOMPLETE"
    assert body["reproducibility_level"] == "PARTIAL"
    assert body["expected"]["reproducibility_gaps"] == body["actual"]["reproducibility_gaps"] != []


def test_verify_reports_manifest_changed_when_consumed_evidence_changes(
    client: TestClient, db_session: Session, market: dict[str, object]
) -> None:
    event = _complete_event(db_session, market)
    record_id = _save(db_session, event, [1])
    stock = json.loads(_row(db_session, record_id).canonical_manifest)["evidence"]["stock"]
    _bump(
        db_session,
        DailyPriceBar,
        listing_id=uuid.UUID(stock["listing_id"]),
        trading_date=datetime.fromisoformat(stock["bars"][1]["session"]).date(),
    )
    body = client.get(f"{BASE}/{record_id}/verify").json()
    assert body["status"] == "MANIFEST_CHANGED"
    assert body["changed_sections"] == ["evidence"]
    assert any(p.startswith("evidence.stock.bars[1].close") for p in body["changed_paths"])
    assert body["changed_result_paths"], "the result differences are explained by path too"
    assert (
        body["expected"]["fingerprints"]["manifest"] != body["actual"]["fingerprints"]["manifest"]
    )
    assert body["expected"]["fingerprints"]["request"] == body["actual"]["fingerprints"]["request"]
    assert (
        body["expected"]["fingerprints"]["evidence"] != body["actual"]["fingerprints"]["evidence"]
    )
    # the baseline itself still serves unchanged
    assert (
        client.get(f"{BASE}/{record_id}").json()["manifest_fingerprint"]
        == (body["expected"]["fingerprints"]["manifest"])
    )


def test_verify_reports_result_changed_when_the_stored_result_differs_from_a_rerun(
    client: TestClient, db_session: Session, market: dict[str, object]
) -> None:
    event = _complete_event(db_session, market)
    record_id = _save(db_session, event, [1])
    row = _row(db_session, record_id)
    altered = json.loads(row.canonical_result)
    altered["event"]["reported_eps"] = "9.9999"
    text = canonical_text(altered)
    db_session.execute(sa.text(GUARD.format("DISABLE")))
    db_session.execute(
        sa.text(
            "UPDATE research_observation_record SET canonical_result = :t, result_fingerprint = :f"
        ),
        {"t": text, "f": fingerprint_of_text(text).digest},
    )
    db_session.execute(sa.text(GUARD.format("ENABLE")))
    db_session.expire_all()

    body = client.get(f"{BASE}/{record_id}/verify").json()
    assert body["status"] == "RESULT_CHANGED"
    assert body["changed_sections"] == [] and body["changed_paths"] == []
    assert body["changed_result_paths"] == ["event.reported_eps"]
    assert (
        body["expected"]["fingerprints"]["manifest"] == body["actual"]["fingerprints"]["manifest"]
    )
    assert body["expected"]["fingerprints"]["result"] != body["actual"]["fingerprints"]["result"]


def test_verify_when_the_events_row_is_gone_is_a_409_not_a_server_error(
    client: TestClient, db_session: Session, market: dict[str, object]
) -> None:
    event = _complete_event(db_session, market)
    record_id = _save(db_session, event, [1])
    db_session.execute(sa.delete(EarningsEvent).where(EarningsEvent.id == event.id))
    db_session.expire_all()
    response = client.get(f"{BASE}/{record_id}/verify")
    assert response.status_code == 409
    assert response.json() == {"detail": "The earnings event of this record is no longer stored."}
    assert client.get(f"{BASE}/{record_id}").status_code == 200, "the baseline is still served"


# ── read-only, provider-free ─────────────────────────────────────────────────────────────


def test_list_get_and_verify_are_select_only_never_touch_the_baseline_and_use_no_network(
    client: TestClient,
    db_session: Session,
    market: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _complete_event(db_session, market)
    record_id = _save(db_session, event, [1])
    before = _snapshot(db_session)
    counts_before: dict[str, int] = {
        table: db_session.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        for table in ("ingestion_run", "daily_price_bar", "benchmark_price_bar", "sec_filing")
    }

    def tripwire(*_a: object, **_k: object) -> None:
        raise AssertionError("network access")

    monkeypatch.setattr(socket.socket, "connect", tripwire)
    monkeypatch.setattr(socket, "create_connection", tripwire)
    allowed = ("SELECT", "WITH", "SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK")

    for url in (BASE, f"{BASE}/{record_id}", f"{BASE}/{record_id}/verify"):
        db_session.expire_all()
        with _record_statements(db_session) as statements:
            assert client.get(url).status_code == 200
        assert statements and all(s.lstrip().upper().startswith(allowed) for s in statements), url
        if url.endswith("/verify"):
            print(f"verify statements: {len(statements)}")  # noqa: T201

    assert _snapshot(db_session) == before, "no verification or read altered the stored record"
    counts_after: dict[str, int] = {
        table: db_session.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        for table in counts_before
    }
    assert counts_after == counts_before, "nothing was ingested or written"


def test_the_observation_endpoint_statement_count_does_not_grow_with_the_horizons(
    client: TestClient, db_session: Session, market: dict[str, object]
) -> None:
    event = _complete_event(db_session, market)
    benchmark = market["benchmark"]
    assert isinstance(benchmark, Benchmark)
    counts: dict[str, int] = {}
    for horizons in ("1", "1,2,3"):
        db_session.expire_all()
        with _record_statements(db_session) as statements:
            response = client.get(
                f"/api/v1/research/observations/{event.id}",
                params={"horizons": horizons, "benchmark_id": str(benchmark.id)},
            )
        assert response.status_code == 200
        counts[horizons] = len(statements)
    print(f"observation statements by horizons: {counts}")  # noqa: T201
    assert counts["1,2,3"] <= counts["1"] <= 15, counts  # bounded; no per-horizon query


def test_a_stored_manifest_the_database_cannot_parse_is_a_generic_500_on_the_list(
    client: TestClient, db_session: Session, market: dict[str, object]
) -> None:
    event = _complete_event(db_session, market)
    record_id = _save(db_session, event, [1])
    db_session.execute(sa.text(GUARD.format("DISABLE")))
    db_session.execute(
        sa.text("UPDATE research_observation_record SET canonical_manifest = 'not json'")
    )
    db_session.execute(sa.text(GUARD.format("ENABLE")))
    db_session.expire_all()
    response = client.get(BASE)
    assert response.status_code == 500
    assert response.json() == {"detail": "Stored research record failed integrity validation."}
    assert "not json" not in response.text
    assert client.get(f"{BASE}/{record_id}").status_code == 500, "get rejects it by hash as well"
