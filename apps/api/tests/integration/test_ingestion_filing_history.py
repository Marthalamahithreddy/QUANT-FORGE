"""Bounded historical filing identity against real PostgreSQL (no SEC access).

A real :class:`SecClient` talks to a mock transport serving SEC-shaped parent and history
files, so the whole chain -- raw artifact, mapper, canonical filing, ``SecFiling`` -- runs.
Covers provenance, idempotence, changed bytes, conflicts, fail-closed behaviour, the
statement count, and the central proof: an older FSDS accession binds only after its
history file is ingested. Nothing here is availability: no availability field exists.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session
from tests.fsds_fixture import build_package, num_row, sub_row
from tests.sec_history_fixture import (
    CIK,
    H1_8K,
    H1_10K,
    H1_10Q_A,
    H1_FORM4,
    H2_10K,
    H2_10Q,
    H2_PLACEHOLDER,
    H2_PLACEHOLDER_EDT,
    HISTORY_1,
    HISTORY_2,
    R_10K_A,
    R_10Q,
    R_FORM4,
    R_OVERLAP,
    Recorder,
    encode,
    file_entry,
    history,
    parent,
    synthetic_rows,
)

from quantforge.core.config import Settings
from quantforge.db.session import get_session
from quantforge.ingestion.canonical import CanonicalSecFiling
from quantforge.ingestion.filing_history import (
    FilingHistoryIngestionService,
    FilingHistoryReport,
    InvalidHistoryRequestError,
)
from quantforge.ingestion.filings import (
    SecFilingConflictError,
    SecFilingIngestor,
    UnknownRegistrantError,
)
from quantforge.ingestion.financial_facts import (
    FinancialFactIngestionService,
    MissingSecFilingError,
)
from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.ingestion.sources import get_or_create_data_source
from quantforge.main import create_app
from quantforge.models.company import Company
from quantforge.models.enums import IngestionStatus, ProviderType, SecFormType
from quantforge.models.filing import SecFiling
from quantforge.models.financial_fact import FinancialFact, FinancialFactSubmission
from quantforge.models.ingestion import IngestionRun
from quantforge.providers.errors import ProviderNotFoundError, ProviderPayloadError
from quantforge.providers.sec.client import SecClient, SecConfig

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
CONTACT = "tester@example.com"
UA = f"QuantForgeTest {CONTACT}"
_HARNESS = ("SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")

H1 = [H1_10K, H1_10Q_A, H1_8K, H1_FORM4]
H2 = [H2_10K, H2_10Q, H2_PLACEHOLDER, H2_PLACEHOLDER_EDT]


def _routes(
    recent: list[Any] | None = None, h1: list[Any] | None = None, h2: list[Any] | None = None
) -> dict[str, bytes]:
    recent = [R_10Q, R_10K_A, R_FORM4, R_OVERLAP] if recent is None else recent
    h1 = H1 if h1 is None else h1
    h2 = H2 if h2 is None else h2
    doc = parent(recent, [file_entry(HISTORY_1, h1), file_entry(HISTORY_2, h2)])
    return {
        f"/submissions/CIK{CIK}.json": encode(doc),
        f"/submissions/{HISTORY_1}": encode(history(h1)),
        f"/submissions/{HISTORY_2}": encode(history(h2)),
    }


@pytest.fixture
def company(db_session: Session) -> Company:
    row = Company(legal_name="Example Filer Inc.", display_name="Example Filer", cik=CIK)
    db_session.add(row)
    db_session.commit()
    return row


async def _run(
    session: Session,
    routes: dict[str, bytes],
    tmp_path: Path,
    *,
    accessions: tuple[str, ...] = (),
    max_history_files: int = 20,
    now: datetime = NOW,
) -> FilingHistoryReport:
    recorder = Recorder(routes)
    async with SecClient(
        SecConfig(user_agent=UA, base_url="https://data.sec.gov"),
        transport=recorder.transport,
        now=lambda: now,
        sleep=_no_sleep,
    ) as client:
        service = FilingHistoryIngestionService(
            session=session, provider=client, store=RawResponseStore(tmp_path / "raw"), now=now
        )
        report = await service.ingest_filing_history(
            CIK, accessions=accessions, max_history_files=max_history_files
        )
    session.commit()
    return report


async def _no_sleep(_delay: float) -> None:
    return None


async def _failed(
    session: Session, routes: dict[str, bytes], tmp_path: Path, **kwargs: Any
) -> BaseException:
    with pytest.raises(Exception) as caught:
        await _run(session, routes, tmp_path, **kwargs)
    session.commit()  # what the CLI commits: the durable failed run, never domain rows
    return caught.value


def _snapshot(session: Session) -> list[tuple[Any, ...]]:
    return [
        (r.id, r.accession_number, r.form, r.filing_date, r.report_date, r.accepted_at,
         r.primary_document, r.items, r.ingested_at, r.created_at, r.ingestion_run_id)
        for r in session.scalars(sa.select(SecFiling).order_by(SecFiling.accession_number))
    ]  # fmt: skip


def _files(root: Path, dataset: str) -> list[Path]:
    return sorted(
        p
        for p in (root / "raw" / "sec-edgar" / dataset).rglob("*.json")
        if p.is_file() and not p.name.endswith(".meta.json")
    )


# ── first run: identity and provenance ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_recent_and_history_filings_are_stored_as_identity_with_no_acceptance_instant(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    report = await _run(db_session, _routes(), tmp_path)

    rows = {r.accession_number: r for r in db_session.scalars(sa.select(SecFiling))}
    assert set(rows) == {
        R_10Q[0], R_10K_A[0], R_OVERLAP[0],  # recent (Form 4 is not a supported form)
        H1_10Q_A[0], H1_8K[0],  # history 1 (the 10-K is also in recent)
        H2_10K[0], H2_10Q[0],  # history 2
        H2_PLACEHOLDER[0], H2_PLACEHOLDER_EDT[0],  # date-only acceptance text: identity still stored
    }  # fmt: skip
    assert all(r.company_id == company.id for r in rows.values())
    assert rows[H1_10Q_A[0]].form is SecFormType.FORM_10Q_A  # an amendment stays its own row
    assert rows[R_10K_A[0]].form is SecFormType.FORM_10K_A
    assert all(r.accepted_at is None for r in rows.values()), "bulk ingestion never sets it"
    assert rows[H1_8K[0]].items == "2.02,9.01"
    assert rows[H2_10K[0]].filing_date.year == 2014

    result = report.result
    assert (report.inserted, report.matched_existing) == (
        9,
        0,
    )  # the identical overlap is merged before storage
    assert (result.recent_rows_seen, result.recent_filings_parsed) == (4, 3)
    assert (result.history_files_discovered, result.history_files_fetched) == (2, 2)
    assert (result.history_rows_seen, result.history_filings_parsed) == (8, 7)
    assert result.overlap_identical == 1 and len(result.rejected) == 0
    assert report.outcome.status is IngestionStatus.SUCCEEDED
    run = db_session.query(IngestionRun).one()
    assert (run.provider, run.dataset) == ("sec-edgar", "sec-filing-history")
    assert (run.records_written, run.records_skipped, run.records_failed) == (9, 0, 0)
    assert run.request_parameters["cik"] == CIK
    assert run.request_parameters["scope"] == "all-referenced-history-files"
    assert all(r.ingestion_run_id == run.id for r in rows.values())


@pytest.mark.anyio
async def test_no_availability_or_fsds_field_is_created_by_identity_ingestion(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    await _run(db_session, _routes(), tmp_path)

    columns = {c.name for c in SecFiling.__table__.columns}
    assert not columns & {"observed_at", "observed_at_basis", "known_as_of", "filing_public_at"}
    assert db_session.query(FinancialFactSubmission).count() == 0
    assert db_session.query(FinancialFact).count() == 0
    assert all(r.accepted_at != r.ingested_at for r in db_session.scalars(sa.select(SecFiling))), (
        "acceptance is never the retrieval time"
    )


@pytest.mark.anyio
async def test_every_artifact_keeps_url_digest_length_retrieval_time_and_cik(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    routes = _routes()
    await _run(db_session, routes, tmp_path)

    run = db_session.query(IngestionRun).one()
    store = RawResponseStore(tmp_path / "raw")
    assert run.raw_artifact_path is not None
    manifest_bytes = store.read(run.raw_artifact_path)
    assert hashlib.sha256(manifest_bytes).hexdigest() == run.raw_content_sha256
    assert len(manifest_bytes) == run.raw_artifact_bytes
    manifest = json.loads(manifest_bytes)
    assert manifest["artifact_count"] == 3
    by_endpoint = {a["endpoint"]: a for a in manifest["artifacts"]}
    assert set(by_endpoint) == {
        "/submissions/CIK{cik}.json",
        f"/submissions/{HISTORY_1}",
        f"/submissions/{HISTORY_2}",
    }
    for endpoint, body in (
        (f"/submissions/{HISTORY_1}", routes[f"/submissions/{HISTORY_1}"]),
        (f"/submissions/{HISTORY_2}", routes[f"/submissions/{HISTORY_2}"]),
        ("/submissions/CIK{cik}.json", routes[f"/submissions/CIK{CIK}.json"]),
    ):
        entry = by_endpoint[endpoint]
        assert entry["sha256"] == hashlib.sha256(body).hexdigest()
        assert entry["byte_length"] == len(body)
        assert entry["retrieved_at"] == NOW.isoformat()  # QuantForge's retrieval time
        assert store.read(entry["path"]) == body
    sidecar = store.metadata(by_endpoint[f"/submissions/{HISTORY_1}"]["path"])
    assert sidecar["request_parameters"] == {"cik": CIK, "file": HISTORY_1}
    assert (sidecar["provider"], sidecar["dataset"]) == ("sec-edgar", "submissions-history")


@pytest.mark.anyio
async def test_the_contact_identity_is_never_stored_anywhere(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    await _run(db_session, _routes(), tmp_path)

    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert CONTACT.encode() not in path.read_bytes(), path
    for run in db_session.scalars(sa.select(IngestionRun)):
        assert CONTACT not in json.dumps(run.request_parameters) + (run.error_message or "")


# ── idempotence, changed bytes, conflicts ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_refetching_identical_bytes_is_idempotent_and_creates_no_duplicate_blob(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    await _run(db_session, _routes(), tmp_path)
    before = _snapshot(db_session)
    blobs = {d: _files(tmp_path, d) for d in ("submissions", "submissions-history")}

    later = NOW + timedelta(days=1)
    report = await _run(db_session, _routes(), tmp_path, now=later)

    assert (report.inserted, report.matched_existing) == (0, 9)
    assert _snapshot(db_session) == before, "no metadata drift, no changed acceptance instant"
    assert {d: _files(tmp_path, d) for d in blobs} == blobs, "identical bytes are deduplicated"
    assert db_session.query(IngestionRun).count() == 2
    assert all(r.ingested_at == NOW for r in db_session.scalars(sa.select(SecFiling))), (
        "a re-fetch never rewrites a stored row"
    )


@pytest.mark.anyio
async def test_changed_bytes_under_the_same_url_are_kept_beside_the_old_artifact(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    await _run(db_session, _routes(), tmp_path)
    before = _snapshot(db_session)
    old_files = _files(tmp_path, "submissions-history")
    old_bodies = {p: p.read_bytes() for p in old_files}
    routes = _routes()
    # Same rows, different bytes (the SEC documents no immutability for these files).
    routes[f"/submissions/{HISTORY_2}"] = json.dumps(history(H2), indent=1).encode()

    report = await _run(db_session, routes, tmp_path, now=NOW + timedelta(days=1))

    assert report.inserted == 0
    assert _snapshot(db_session) == before
    new_files = _files(tmp_path, "submissions-history")
    assert len(new_files) == len(old_files) + 1, "a new revision, not an overwrite"
    for path, body in old_bodies.items():
        assert path.read_bytes() == body, "an existing content-addressed blob is never mutated"
    for path in new_files:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == path.name.removesuffix(".json")


@pytest.mark.anyio
async def test_a_changed_json_acceptance_value_for_a_stored_accession_changes_nothing(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    """The SEC returned a different value for the same accession three days apart (11.2G)."""
    await _run(db_session, _routes(), tmp_path)
    before = _snapshot(db_session)
    changed = (*H1_8K[:4], "2021-01-27T22:31:18.000Z", *H1_8K[5:])
    routes = _routes(h1=[H1_10K, H1_10Q_A, changed, H1_FORM4])

    report = await _run(db_session, routes, tmp_path, now=NOW + timedelta(days=1))

    assert (report.inserted, report.matched_existing) == (0, 9)
    assert _snapshot(db_session) == before


@pytest.mark.anyio
async def test_a_header_established_acceptance_survives_bulk_reingestion(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    await _run(db_session, _routes(), tmp_path)
    row = db_session.scalar(sa.select(SecFiling).where(SecFiling.accession_number == H1_8K[0]))
    assert row is not None and row.accepted_at is None
    established = datetime(2021, 1, 27, 21, 31, 18, tzinfo=UTC)
    row.accepted_at = established  # as the EDGAR-header enrichment would have set it
    db_session.commit()

    report = await _run(db_session, _routes(), tmp_path, now=NOW + timedelta(days=1))

    assert (report.inserted, report.matched_existing) == (0, 9)
    db_session.refresh(row)
    assert row.accepted_at == established, "bulk identity never erases stronger evidence"


@pytest.mark.anyio
async def test_recent_and_history_disagreeing_about_one_accession_fails_and_stores_nothing(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    disagreeing = (*R_OVERLAP[:5], "other.htm", *R_OVERLAP[6:])
    routes = _routes(recent=[R_10Q, disagreeing])

    error = await _failed(db_session, routes, tmp_path)

    assert isinstance(error, ProviderPayloadError) and "silently preferred" in str(error)
    assert db_session.query(SecFiling).count() == 0
    assert db_session.query(IngestionRun).filter_by(status=IngestionStatus.FAILED).count() == 1
    assert _files(tmp_path, "submissions-rejected"), "the evidence is kept apart from accepted data"


@pytest.mark.anyio
async def test_a_history_file_that_cannot_be_fetched_fails_closed_with_no_partial_rows(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    routes = _routes()
    del routes[f"/submissions/{HISTORY_2}"]  # the mock answers 404

    error = await _failed(db_session, routes, tmp_path)

    assert isinstance(error, ProviderNotFoundError)
    assert db_session.query(SecFiling).count() == 0, "nothing partial is reported as coverage"
    run = db_session.query(IngestionRun).one()
    assert run.status is IngestionStatus.FAILED and run.records_written == 0


@pytest.mark.anyio
async def test_every_record_rejected_is_a_failed_run_not_a_success(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    only = [("not-an-accession-1", *H2_10K[1:]), ("not-an-accession-2", *H2_10Q[1:])]
    routes = {
        f"/submissions/CIK{CIK}.json": encode(parent([], [file_entry(HISTORY_2, only)])),
        f"/submissions/{HISTORY_2}": encode(history(only)),
    }

    error = await _failed(db_session, routes, tmp_path)

    assert type(error).__name__ == "AllFilingRecordsRejectedError"
    assert db_session.query(SecFiling).count() == 0


@pytest.mark.anyio
async def test_an_unknown_registrant_makes_no_request_and_stores_nothing(
    db_session: Session, tmp_path: Path
) -> None:
    recorder = Recorder(_routes())
    async with SecClient(
        SecConfig(user_agent=UA, base_url="https://data.sec.gov"),
        transport=recorder.transport,
        sleep=_no_sleep,
    ) as client:
        service = FilingHistoryIngestionService(
            session=db_session, provider=client, store=RawResponseStore(tmp_path / "raw"), now=NOW
        )
        with pytest.raises(UnknownRegistrantError):
            await service.ingest_filing_history(CIK)

    assert recorder.paths == []


# ── scope ────────────────────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_targeted_run_stores_the_resolved_accession_and_reports_unresolved_honestly(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    report = await _run(
        db_session, _routes(), tmp_path, accessions=(H2_10K[0], "0000320193-14-999999")
    )

    assert report.result.unresolved_accessions == ("0000320193-14-999999",)
    assert report.result.history_files_fetched == 1 and report.result.history_files_skipped == 1
    assert report.outcome.status is IngestionStatus.PARTIAL
    assert not report.complete_for_requested_scope
    stored = {r.accession_number for r in db_session.scalars(sa.select(SecFiling))}
    assert H2_10K[0] in stored and H1_8K[0] not in stored, "the skipped file was never read"
    run = db_session.query(IngestionRun).one()
    assert run.request_parameters["scope"] == "accessions"
    assert run.request_parameters["accessions"] == [H2_10K[0], "0000320193-14-999999"]


@pytest.mark.anyio
async def test_a_target_already_in_recent_succeeds_without_any_history_request(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    report = await _run(db_session, _routes(), tmp_path, accessions=(R_10Q[0],))

    assert (
        report.outcome.status is IngestionStatus.SUCCEEDED and report.complete_for_requested_scope
    )
    assert report.result.history_files_fetched == 0
    assert _files(tmp_path, "submissions-history") == []


@pytest.mark.anyio
async def test_the_file_bound_is_enforced_before_any_history_artifact_is_stored(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    error = await _failed(db_session, _routes(), tmp_path, max_history_files=1)

    assert type(error).__name__ == "HistoryScopeError"
    assert db_session.query(SecFiling).count() == 0
    assert _files(tmp_path, "submissions-history") == []


# ── scale: statements do not grow with rows ──────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_registrant_with_many_history_files_does_not_cost_statements_per_filing(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    def routes_for(years: tuple[int, ...], per_file: int) -> dict[str, bytes]:
        entries: list[Any] = []
        out: dict[str, bytes] = {}
        for i, year in enumerate(years, start=1):
            name = f"CIK{CIK}-submissions-{i:03d}.json"
            rows = synthetic_rows(year, per_file)
            entries.append(file_entry(name, rows))
            out[f"/submissions/{name}"] = encode(history(rows))
        out[f"/submissions/CIK{CIK}.json"] = encode(parent([R_10Q], entries))
        return out

    async def count(routes: dict[str, bytes]) -> tuple[int, FilingHistoryReport]:
        reports: list[FilingHistoryReport] = []
        engine = db_session.get_bind()
        seen: list[str] = []

        def capture(_c: Any, _cur: Any, statement: str, *_a: Any) -> None:
            seen.append(statement)

        event.listen(engine, "before_cursor_execute", capture)
        try:
            reports.append(await _run(db_session, routes, tmp_path))
        finally:
            event.remove(engine, "before_cursor_execute", capture)
        statements = len([s for s in seen if not s.lstrip().upper().startswith(_HARNESS)])
        return statements, reports[0]

    await _run(db_session, routes_for((2009,), 1), tmp_path)  # warm-up: data source row etc.
    small, small_report = await count(routes_for((2010, 2011), 20))
    large, large_report = await count(routes_for((2012, 2013, 2014, 2015), 100))

    assert small_report.inserted == 40 and large_report.inserted == 400  # R_10Q: the warm-up
    assert large == small, f"statements grew with rows: {small} -> {large}"
    assert large < 40, large
    rerun, rerun_report = await count(routes_for((2012, 2013, 2014, 2015), 100))
    assert (rerun_report.inserted, rerun_report.matched_existing) == (0, 401)
    assert rerun <= large


# ── the central proof: an older FSDS accession binds only after history identity ─────────


def _old_package(tmp_path: Path) -> Path:
    accession = H2_10K[0]
    return build_package(
        tmp_path / "2014q4.zip",
        subs=[
            sub_row(
                adsh=accession,
                cik="320193",
                form="10-K",
                period="20140927",
                fy="2014",
                fp="FY",
                filed="20141027",
                accepted="2014-10-27 16:15:00.0",
            )
        ],
        nums=[
            num_row(
                adsh=accession,
                tag="EarningsPerShareDiluted",
                version="us-gaap/2014",
                ddate="20140927",
                qtrs="4",
                uom="USD",
                value="6.4500",
            )
        ],
    )


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    application: FastAPI = create_app(Settings(_env_file=None))
    application.dependency_overrides[get_session] = lambda: db_session
    with TestClient(application) as test_client:
        yield test_client


@pytest.mark.anyio
async def test_an_older_fsds_accession_binds_only_after_bounded_history_ingestion(
    company: Company, db_session: Session, tmp_path: Path, client: TestClient
) -> None:
    package = _old_package(tmp_path)
    facts = FinancialFactIngestionService(
        session=db_session, store=RawResponseStore(tmp_path / "raw"), now=NOW
    )

    # Before: the accession is outside `filings.recent`, so no SecFiling exists to bind to.
    with pytest.raises(MissingSecFilingError):
        facts.ingest_fsds_package(package, package_name="2014q4", accessions=[H2_10K[0]], ciks=())
    db_session.commit()  # the durable failed run
    assert db_session.query(SecFiling).count() == 0
    assert db_session.query(FinancialFact).count() == 0

    # Identity ingestion for ONE explicit CIK and ONE target accession.
    report = await _run(db_session, _routes(), tmp_path, accessions=(H2_10K[0],))
    assert report.result.history_files_fetched == 1
    assert db_session.query(FinancialFact).count() == 0, "identity ingestion never ingests facts"
    assert db_session.query(FinancialFactSubmission).count() == 0

    # After: the same FSDS request now binds, with unchanged fact semantics.
    result = facts.ingest_fsds_package(
        package, package_name="2014q4", accessions=[H2_10K[0]], ciks=()
    )
    db_session.commit()
    assert result.bound_accessions == (H2_10K[0],) and result.unbound_accessions == ()
    filing = db_session.scalar(sa.select(SecFiling).where(SecFiling.accession_number == H2_10K[0]))
    assert filing is not None and filing.form is SecFormType.FORM_10K
    submission = db_session.scalar(sa.select(FinancialFactSubmission))
    assert submission is not None
    assert submission.source_accepted == "2014-10-27 16:15:00.0", (
        "FSDS text stays verbatim FSDS text"
    )
    assert filing.accepted_at is None, "bulk identity never sets the acceptance instant"
    assert db_session.query(FinancialFact).count() == 1

    # Read path: the existing endpoints serve the bound older accession with unchanged shape.
    listing = client.get("/api/v1/filings", params={"cik": CIK})
    assert listing.status_code == 200
    items = {item["accession"]: item for item in listing.json()["items"]}
    assert set(items[H2_10K[0]]) == {
        "accession", "company_cik", "form", "filing_date", "report_date", "fsds_period_end",
    }  # fmt: skip
    detail = client.get(
        f"/api/v1/filings/{H2_10K[0]}/eps",
        params={"cik": CIK, "period_class": "ANNUAL", "eps_kind": "DILUTED"},
    )
    assert detail.status_code == 200, detail.text


# ── the batched writer and request validation ────────────────────────────────────────────


def _canonical(**overrides: Any) -> CanonicalSecFiling:
    fields: dict[str, Any] = {
        "cik": CIK,
        "accession_number": "0000320193-22-000108",
        "form": SecFormType.FORM_10K,
        "filing_date": date(2022, 10, 28),
        "accepted_at": datetime(2022, 10, 28, 20, 42, 27, tzinfo=UTC),
    }
    fields.update(overrides)
    return CanonicalSecFiling(**fields)


def _ingestor(db_session: Session, company: Company) -> tuple[SecFilingIngestor, Any]:
    source = get_or_create_data_source(
        db_session, name="sec-edgar", provider_type=ProviderType.REGULATORY_FILING
    )
    db_session.flush()
    return SecFilingIngestor(db_session, now=NOW), source


def test_one_accession_twice_in_a_batch_is_one_filing_when_identical(
    company: Company, db_session: Session
) -> None:
    ingestor, source = _ingestor(db_session, company)

    written, skipped = ingestor.ingest(
        [_canonical(), _canonical()], company_id=company.id, source_id=source.id
    )

    assert (written, skipped) == (1, 1) and db_session.query(SecFiling).count() == 1


def test_one_accession_twice_in_a_batch_with_different_metadata_is_a_conflict(
    company: Company, db_session: Session
) -> None:
    ingestor, source = _ingestor(db_session, company)
    other = _canonical(primary_document="other.htm")

    with pytest.raises(SecFilingConflictError) as caught:
        ingestor.ingest([_canonical(), other], company_id=company.id, source_id=source.id)

    assert set(caught.value.differences) == {"primary_document"}
    assert db_session.query(SecFiling).count() == 0, "a conflicting batch inserts nothing"


def test_a_stored_conflict_in_a_later_row_prevents_earlier_rows_of_the_batch_being_inserted(
    company: Company, db_session: Session
) -> None:
    ingestor, source = _ingestor(db_session, company)
    ingestor.ingest([_canonical()], company_id=company.id, source_id=source.id)
    fresh = _canonical(
        accession_number="0000320193-22-000200",
        accepted_at=datetime(2022, 10, 1, 15, 0, tzinfo=UTC),
        filing_date=date(2022, 10, 1),
    )
    changed = _canonical(primary_document="other.htm")

    with pytest.raises(SecFilingConflictError):
        ingestor.ingest([fresh, changed], company_id=company.id, source_id=source.id)

    assert db_session.query(SecFiling).count() == 1


@pytest.mark.anyio
@pytest.mark.parametrize("bound", [0, 101, -1])
async def test_an_out_of_range_file_bound_is_refused_before_anything_happens(
    company: Company, db_session: Session, tmp_path: Path, bound: int
) -> None:
    with pytest.raises(InvalidHistoryRequestError):
        await _run(db_session, _routes(), tmp_path, max_history_files=bound)

    assert db_session.query(IngestionRun).count() == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "accessions",
    [("320193-22-108",), ("0000320193-22-00010",), (f"0000320193-22-{n:06d}" for n in range(201))],
)
async def test_malformed_or_excessive_target_accessions_are_refused(
    company: Company, db_session: Session, tmp_path: Path, accessions: Any
) -> None:
    with pytest.raises(InvalidHistoryRequestError):
        await _run(db_session, _routes(), tmp_path, accessions=tuple(accessions))

    assert db_session.query(IngestionRun).count() == 0


@pytest.mark.anyio
async def test_a_located_but_unsupported_target_makes_the_run_partial(
    company: Company, db_session: Session, tmp_path: Path
) -> None:
    report = await _run(db_session, _routes(), tmp_path, accessions=(R_FORM4[0],))

    assert report.result.unresolved_accessions == ()
    assert report.result.located_not_stored == (R_FORM4[0],)
    assert report.outcome.status is IngestionStatus.PARTIAL
    assert not report.complete_for_requested_scope
    stored = {r.accession_number for r in db_session.scalars(sa.select(SecFiling))}
    assert R_FORM4[0] not in stored
