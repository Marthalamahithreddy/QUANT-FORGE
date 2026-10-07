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
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.ingestion.runs import FAILED_RUN_RECORDED, RecordFailure, RunRecorder
from quantforge.ingestion.sources import get_or_create_data_source
from quantforge.models.company import Company
from quantforge.models.enums import IngestionStatus, ProviderType
from quantforge.models.ingestion import IngestionRun
from quantforge.models.provenance import DataSource
from quantforge.models.security import Exchange
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


def test_a_failed_block_rolls_back_its_writes_but_keeps_the_failed_run(
    db_session: Session, store: RawResponseStore
) -> None:
    """The run row outlives the failure; the domain row written inside the block does not."""
    company_count = db_session.query(Company).count()
    with pytest.raises(RuntimeError, match="boom"), _recorder(db_session, store) as run:
        db_session.add(Company(legal_name="Rolled Back Co", display_name="Rolled Back"))
        db_session.flush()
        run.count_written(1)
        raise RuntimeError("boom")
    db_session.commit()

    assert db_session.query(Company).count() == company_count
    row = _one(db_session, status=IngestionStatus.FAILED)
    assert row.records_written == 0
    assert row.error_message == "boom"


def test_a_failed_block_keeps_the_manifest_pointer(
    db_session: Session, store: RawResponseStore
) -> None:
    with pytest.raises(RuntimeError), _recorder(db_session, store) as run:
        run.record_captures(store.store_all([_capture()]))
        raise RuntimeError("after fetch")
    db_session.commit()

    row = _one(db_session, status=IngestionStatus.FAILED)
    assert row.raw_artifact_path
    assert row.raw_content_sha256


def test_rolling_back_the_outer_transaction_still_discards_a_failed_run(
    db_session: Session, store: RawResponseStore
) -> None:
    """Durability needs the caller's commit; a caller that rolls back discards everything."""
    with pytest.raises(RuntimeError), _recorder(db_session, store) as run:
        raise RuntimeError("x")
    db_session.rollback()

    assert db_session.scalar(sa.select(IngestionRun).filter_by(id=run.run_id)) is None


def test_a_failure_at_the_final_flush_is_recorded_and_the_original_error_surfaces(
    db_session: Session, store: RawResponseStore
) -> None:
    """A constraint violation found only when the block's writes flush is still a failed run."""
    with pytest.raises(Exception, match="mic"), _recorder(db_session, store):
        db_session.add(Exchange(mic="bad", name="Bad MIC", timezone="UTC"))  # CHECK fails on flush
    db_session.commit()

    row = _one(db_session, status=IngestionStatus.FAILED)
    assert row.error_type


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


def test_the_failed_run_marker_is_set_only_when_the_failure_was_recorded(
    db_session: Session, store: RawResponseStore
) -> None:
    with pytest.raises(RuntimeError), _recorder(db_session, store):
        raise RuntimeError("x")

    assert db_session.info[FAILED_RUN_RECORDED] is True


def test_no_marker_is_set_for_a_successful_run(
    db_session: Session, store: RawResponseStore
) -> None:
    with _recorder(db_session, store) as run:
        run.succeed(LATER)

    assert FAILED_RUN_RECORDED not in db_session.info


def test_a_failure_while_recording_never_replaces_the_original_error(
    db_session: Session, store: RawResponseStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _recorder(db_session, store)

    def broken_fail(_self: RunRecorder, _exc: BaseException) -> None:
        raise OSError("connection dropped while recording")

    monkeypatch.setattr(RunRecorder, "fail", broken_fail)
    with pytest.raises(RuntimeError, match="the real provider error"), recorder:
        raise RuntimeError("the real provider error")

    assert FAILED_RUN_RECORDED not in db_session.info


def test_a_data_source_race_inside_the_block_does_not_lose_the_run(
    db_session: Session, store: RawResponseStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """get_or_create_data_source runs inside the recorder's SAVEPOINT; its lost-race
    recovery must not roll back the run row or close the recorder's savepoint."""
    db_session.add(DataSource(name="raced-source", provider_type=ProviderType.MARKET_DATA_VENDOR))
    db_session.flush()
    real = db_session.scalar
    calls = {"n": 0}

    def stale_first(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        return None if calls["n"] == 1 else real(*args, **kwargs)

    monkeypatch.setattr(db_session, "scalar", stale_first)
    with _recorder(db_session, store) as run:
        source = get_or_create_data_source(db_session, name="raced-source")
        run.succeed(LATER)
    db_session.commit()

    assert source.name == "raced-source"
    row = _one(db_session, id=run.run_id)
    assert row.status is IngestionStatus.SUCCEEDED


# ── Step 12C: durable partial reasons, and a cumulative manifest ─────────────


def test_a_partial_run_records_why_on_the_existing_columns(
    db_session: Session, store: RawResponseStore
) -> None:
    with _recorder(db_session, store) as run:
        run.count_received(3)
        run.count_written(2)
        run.count_failed(1)
        run.partial(NOW, [RecordFailure.of("MSFT", ValueError("locale 'global' is not 'us'"))])

    row = _one(db_session, status=IngestionStatus.PARTIAL)
    assert row.error_type == "ValueError"
    assert row.error_message == "1 record(s) failed; first: MSFT: locale 'global' is not 'us'"


def test_a_partial_run_without_reasons_and_a_success_have_no_error(
    db_session: Session, store: RawResponseStore
) -> None:
    with _recorder(db_session, store) as run:
        run.count_failed(1)
        run.partial(NOW)
    with _recorder(db_session, store, dataset="other") as run:
        run.succeed(NOW)

    for row in db_session.scalars(sa.select(IngestionRun)):
        assert row.error_type is None and row.error_message is None, row.status


def test_a_failed_run_still_records_the_exception_exactly_as_before(
    db_session: Session, store: RawResponseStore
) -> None:
    with pytest.raises(RuntimeError), _recorder(db_session, store):
        message = "boom at https://x/y?apiKey=SECRET123"
        raise RuntimeError(message)

    row = _one(db_session, status=IngestionStatus.FAILED)
    assert row.error_type == "RuntimeError"
    assert row.error_message == "boom at https://x/y?[redacted]"


def test_kept_artifacts_accumulate_into_one_manifest_written_at_exit(
    db_session: Session, store: RawResponseStore
) -> None:
    first, second = _capture(b'{"a":1}'), _capture(b'{"b":2}')
    with _recorder(db_session, store) as run:
        run.add_artifacts(store.store_all([first]))
        run.add_artifacts(store.store_all([second]))
        assert run.row.raw_artifact_path is None, "the manifest is written when the block ends"
        run.succeed(NOW)

    row = _one(db_session, status=IngestionStatus.SUCCEEDED)
    assert row.raw_artifact_path
    manifest = json.loads((store.root / row.raw_artifact_path).read_bytes())
    assert manifest["artifact_count"] == 2
    bodies = [(store.root / a["path"]).read_bytes() for a in manifest["artifacts"]]
    assert bodies == [b'{"a":1}', b'{"b":2}']


def test_artifacts_kept_before_a_failure_are_still_listed_by_the_failed_run(
    db_session: Session, store: RawResponseStore
) -> None:
    with pytest.raises(RuntimeError), _recorder(db_session, store) as run:
        run.add_artifacts(store.store_all([_capture(b'{"kept":true}')]))
        message = "later failure"
        raise RuntimeError(message)

    row = _one(db_session, status=IngestionStatus.FAILED)
    manifest = json.loads((store.root / (row.raw_artifact_path or "")).read_bytes())
    assert [(store.root / a["path"]).read_bytes() for a in manifest["artifacts"]] == [
        b'{"kept":true}'
    ]


def test_record_captures_after_add_artifacts_lists_both(
    db_session: Session, store: RawResponseStore
) -> None:
    with _recorder(db_session, store) as run:
        run.add_artifacts(store.store_all([_capture(b'{"a":1}')]))
        run.record_captures(store.store_all([_capture(b'{"b":2}')]))
        run.succeed(NOW)

    row = _one(db_session, status=IngestionStatus.SUCCEEDED)
    manifest = json.loads((store.root / (row.raw_artifact_path or "")).read_bytes())
    assert manifest["artifact_count"] == 2
