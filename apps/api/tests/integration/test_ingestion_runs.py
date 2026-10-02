"""Integration tests for the run recorder, against real PostgreSQL.

These assert on the run row as a reader would find it: status, counts, completion
time, and the manifest on disk. The default unit suite covers credential
redaction; what follows is the half that needs a database.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.ingestion.runs import RunRecorder
from quantforge.models.enums import IngestionStatus
from quantforge.models.ingestion import IngestionRun
from quantforge.providers.base import RawCapture

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=5)


@pytest.fixture
def store(tmp_path: Path) -> RawResponseStore:
    return RawResponseStore(root=tmp_path / "raw")


def _capture(body: bytes = b'{"status":"OK","results":[]}') -> RawCapture:
    return RawCapture(
        provider="massive",
        dataset="daily-bars",
        endpoint="/v2/aggs/ticker/AAPL/range/1/day/{start}/{end}",
        request_parameters={"ticker": "AAPL", "start": "2026-09-01", "end": "2026-09-05"},
        body=body,
        retrieved_at=NOW,
        provider_status="OK",
    )


def _recorder(session: Session, store: RawResponseStore, **kwargs: object) -> RunRecorder:
    fields: dict[str, object] = {
        "session": session,
        "store": store,
        "provider": "massive",
        "dataset": "daily-bars",
        "request_parameters": {"ticker": "AAPL"},
        "now": NOW,
    }
    fields.update(kwargs)
    return RunRecorder(**fields)  # type: ignore[arg-type]


def _one(session: Session, **filters: object) -> IngestionRun:
    row = session.scalar(sa.select(IngestionRun).filter_by(**filters))
    assert row is not None, "expected exactly one matching run"
    return row


# ── identity ────────────────────────────────────────────────────────────────


def test_run_id_exists_before_the_run_closes(db_session: Session, store: RawResponseStore) -> None:
    """``record_captures`` runs inside the ``with`` block, so the id must exist.

    Before the flush that assigns it, the manifest would be written with
    ``"run_id": "None"`` and a path ending ``-None``, so a retry would silently
    overwrite the previous run's evidence.
    """
    with _recorder(db_session, store) as run:
        assert run.run_id is not None
        assert run.row.id == run.run_id
        run.record_captures(store.store_all([_capture()]))
        path = run.row.raw_artifact_path
    db_session.commit()

    assert path is not None
    assert "None" not in path


def test_manifest_identifies_the_run_and_lists_every_page(
    db_session: Session, store: RawResponseStore
) -> None:
    """The manifest's own id must match the row that points at it.

    A mismatch means the run references a manifest describing a different
    execution -- worse than having no manifest, because it looks authoritative.
    """
    with _recorder(db_session, store) as run:
        run.record_captures(store.store_all([_capture(b'{"page":1}'), _capture(b'{"page":2}')]))
        run.succeed(LATER)
    db_session.commit()

    row = _one(db_session)
    manifest = json.loads((store.root / str(row.raw_artifact_path)).read_text())
    assert manifest["run_id"] == str(row.id)
    assert manifest["provider"] == "massive"
    assert manifest["dataset"] == "daily-bars"
    assert manifest["artifact_count"] == 2
    digests = [a["sha256"] for a in manifest["artifacts"]]
    assert len(set(digests)) == 2, "two different pages must have two digests"
    assert all(len(d) == 64 for d in digests), "a full sha256, not a prefix"
    assert all(a["byte_length"] > 0 for a in manifest["artifacts"])


def test_manifest_records_the_endpoint_template_not_a_url(
    db_session: Session, store: RawResponseStore
) -> None:
    """The manifest outlives the key that may have been in the query string."""
    with _recorder(db_session, store) as run:
        run.record_captures(store.store_all([_capture(b'{"x":1}')]))
        run.succeed(LATER)
    db_session.commit()

    row = _one(db_session)
    text = (store.root / str(row.raw_artifact_path)).read_text()
    assert "?" not in text
    assert "{start}" in text, "the endpoint template survived, not a concrete URL"


def test_manifest_digest_is_recorded_on_the_row(
    db_session: Session, store: RawResponseStore
) -> None:
    """One column is a commitment to the whole set of pages.

    That is what detects an altered page without walking the manifest.
    """
    with _recorder(db_session, store) as run:
        run.record_captures(store.store_all([_capture()]))
        run.succeed(LATER)
    db_session.commit()

    row = _one(db_session)
    raw = (store.root / str(row.raw_artifact_path)).read_bytes()
    assert row.raw_content_sha256 == hashlib.sha256(raw).hexdigest()
    assert row.raw_artifact_bytes == len(raw)


def test_two_runs_get_separate_manifests(db_session: Session, store: RawResponseStore) -> None:
    """Re-running the same request must not overwrite the earlier evidence."""
    paths: list[str | None] = []
    for _ in range(2):
        with _recorder(db_session, store) as run:
            run.record_captures(store.store_all([_capture()]))
            run.succeed(LATER)
            paths.append(run.row.raw_artifact_path)
        db_session.commit()
    assert len(set(paths)) == 2


def test_a_run_with_no_captures_still_finalises(
    db_session: Session, store: RawResponseStore
) -> None:
    with _recorder(db_session, store) as run:
        run.record_captures([])
        outcome = run.succeed(LATER)
    db_session.commit()

    assert outcome.status is IngestionStatus.SUCCEEDED
    assert outcome.run.raw_artifact_path is None


# ── terminal states ─────────────────────────────────────────────────────────


def test_a_success_is_complete(db_session: Session, store: RawResponseStore) -> None:
    with _recorder(db_session, store) as run:
        run.count_received(10)
        run.count_written(7)
        run.count_skipped(3)
        outcome = run.succeed(LATER)
    db_session.commit()

    assert outcome.status is IngestionStatus.SUCCEEDED
    assert outcome.records_received == 10
    assert outcome.records_written == 7
    assert outcome.records_skipped == 3
    assert outcome.records_failed == 0
    assert outcome.run.completed_at == LATER
    assert outcome.run.error_message is None


def test_a_failure_is_recorded_and_re_raised(db_session: Session, store: RawResponseStore) -> None:
    """A crash must leave a diagnosable row *and* still propagate.

    Swallowing the error would mark a run successful that ingested nothing.
    """
    with (
        pytest.raises(RuntimeError, match="provider exploded"),
        _recorder(db_session, store) as run,
    ):
        run.count_received(5)
        raise RuntimeError("provider exploded")


def test_a_failure_finalises_the_row_as_failed(
    db_session: Session, store: RawResponseStore
) -> None:
    with pytest.raises(RuntimeError), _recorder(db_session, store) as run:
        run.count_received(5)
        run.count_failed(5)
        raise RuntimeError("boom")
    db_session.commit()

    row = _one(db_session, status=IngestionStatus.FAILED)
    assert row.error_type == "RuntimeError"
    assert row.error_message == "boom"
    assert row.completed_at is not None, "a finished run is never left 'running'"
    assert row.records_failed == 5


def test_partial_is_distinct_from_success_and_failure(
    db_session: Session, store: RawResponseStore
) -> None:
    """ "Some symbols failed" must be on the row, not inferred from the counts.

    It is the difference between a refresh that worked and one that quietly
    skipped 40 of 500 symbols.
    """
    with _recorder(db_session, store) as run:
        run.count_received(100)
        run.count_written(60)
        run.count_failed(40)
        outcome = run.partial(LATER)
    db_session.commit()

    assert outcome.status is IngestionStatus.PARTIAL
    assert outcome.run.records_failed == 40


def test_a_running_row_requires_a_null_completion_time(
    db_session: Session, store: RawResponseStore
) -> None:
    """The database enforces the lifecycle, not just the recorder."""
    run = IngestionRun(
        provider="massive",
        dataset="daily-bars",
        status=IngestionStatus.RUNNING,
        started_at=NOW,
        completed_at=LATER,  # illegal: a finished timestamp on a running run
        request_parameters={},
        records_received=0,
        records_written=0,
        records_skipped=0,
        records_failed=0,
    )
    db_session.add(run)
    with pytest.raises(Exception, match="running"):
        db_session.commit()
    db_session.rollback()


def test_the_row_and_its_data_roll_back_together(
    db_session: Session, store: RawResponseStore
) -> None:
    """No committed run may describe data that was rolled back.

    A "succeeded" run whose rows are gone is unrecoverable without a repair job,
    which is why the recorder deliberately never commits.
    """
    recorder = _recorder(db_session, store)
    with recorder as run:
        run.succeed(LATER)
    assert run.row.id is not None
    db_session.rollback()
    assert db_session.scalar(sa.select(IngestionRun).filter_by(id=run.run_id)) is None


# ── what the recorder stores about the request ──────────────────────────────


def test_request_parameters_are_persisted(db_session: Session, store: RawResponseStore) -> None:
    """An operator needs to know what was asked for, not only that something was."""
    with _recorder(
        db_session, store, request_parameters={"ticker": "AAPL", "start": "2026-09-01"}
    ) as run:
        run.succeed(LATER)
    db_session.commit()

    row = _one(db_session)
    assert row.request_parameters == {"ticker": "AAPL", "start": "2026-09-01"}


# ── redaction, as it actually lands in the column ──────────────────────────


@pytest.mark.parametrize(
    ("message", "secret"),
    [
        ("401 from https://api.massive.com/v3/x?apiKey=SUPERSECRET123", "SUPERSECRET123"),
        ("refused https://api.massive.com/v2/y?token=abc123DEF", "abc123DEF"),
        ("api_key=deadbeefcafe rejected", "deadbeefcafe"),
        ("Authorization=Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig", "eyJhbGciOiJIUzI1NiJ9"),
    ],
)
def test_a_credential_never_reaches_the_row(
    db_session: Session, store: RawResponseStore, message: str, secret: str
) -> None:
    """The redaction is not just a helper -- it is what the column contains.

    A run row is read by humans and exported, and neither is scrubbed by a key
    rotation, so this is asserted end to end rather than at the helper.
    """
    with pytest.raises(RuntimeError), _recorder(db_session, store):
        raise RuntimeError(message)
    db_session.commit()

    row = _one(db_session, status=IngestionStatus.FAILED)
    assert secret not in str(row.error_message)
    assert "[redacted]" in str(row.error_message)


def test_a_long_error_message_is_truncated_to_fit_the_column(
    db_session: Session, store: RawResponseStore
) -> None:
    with pytest.raises(RuntimeError), _recorder(db_session, store):
        raise RuntimeError("y" * 5000)
    db_session.commit()

    row = _one(db_session, status=IngestionStatus.FAILED)
    assert len(str(row.error_message)) <= 1000
    assert str(row.error_message).endswith("...")
