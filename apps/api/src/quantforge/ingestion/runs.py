"""The ``IngestionRun`` lifecycle, and the raw-artifact manifest beside it.

Why a run recorder rather than ad-hoc writes
--------------------------------------------
Ingestion is the part of a research system that fails quietly. Without a run
row, these four states are indistinguishable in the output: the market was
closed, the provider returned nothing, the request was never sent, and the
request failed and nobody noticed. Every one of them looks like "no data". A run
row makes each a separate, queryable fact, and it is what an operator reads to
find out which of the four it was.

The artifact manifest
---------------------
``IngestionRun.raw_artifact_path`` is a single column, but a paged range response
is *many* files -- one per page, and a multi-year range is routinely hundreds of
pages. Recording only the first would make a re-run unreproducible, and
recording the last would be arbitrary.

So the pages are recorded in a manifest: a small JSON file listing every capture
with its path, byte length and SHA-256. The run points at the manifest, whose own
hash is recorded in ``raw_content_sha256``. What that buys:

* the manifest's digest is a Merkle-style commitment to the whole set, so a
  single column still detects if any page on disk was altered;
* the individual page hashes remain available, so one altered page can be
  identified rather than merely detected;
* the run row stays one row per execution regardless of page count.

The manifest is written through the same atomic path as the pages themselves, so
a crash cannot leave a run pointing at a half-written manifest.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Final

from sqlalchemy.orm import Session, SessionTransaction

from quantforge.ingestion.raw_store import RawResponseStore, StoredCapture
from quantforge.models.enums import IngestionStatus
from quantforge.models.ingestion import IngestionRun

__all__ = ["RunOutcome", "RunRecorder"]

#: Error messages are truncated to fit ``ingestion_run.error_message``. The same
#: bound as the column, named once so the two cannot drift.
_MAX_ERROR_MESSAGE = 1000

#: Query strings are dropped wholesale. This is the coarse, always-on rule: a
#: provider that authenticates via query parameter is the common case, and no
#: allowlist of "safe" parameters is worth the risk of one being added wrongly.
_QUERY_STRING = re.compile(r"\?[^\s\"'<>]*")

#: Credentials that also appear outside a URL -- in a header dump, a repr, a
#: provider SDK's own error text. Redacted by parameter name wherever they appear.
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|apikey|access[_-]?token|token|secret|password|passwd"
    r"|authorization|auth)\b(\s*[=:]\s*)(?P<value>[^\s,;\"'}\]]+)"
)

#: A bare bearer/basic token, with no ``name=`` to key off.
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")

_REDACTED = "[redacted]"


def _safe_error_text(exc: BaseException) -> str:
    """Reduce an exception to text that is safe to persist forever.

    The ``IngestionRun`` row outlives the process, the run's raw artifacts outlive
    the row, and neither is scrubbed by a credential rotation. So a message is only
    stored after a query string, an API-key-shaped assignment and a bearer token
    have been replaced -- truncation alone is not enough, because a credential in
    the first 1000 characters is still a credential.

    Redaction is deliberately lossy. An operator who needs the exact text can read
    the traceback from the logs, where it is already written with the same
    redaction applied; storing the unredacted copy in the database to save them one
    log line would defeat the point of the exercise.
    """
    text = _QUERY_STRING.sub(f"?{_REDACTED}", str(exc).strip())
    # Bearer/basic first. Running the assignment rule first would consume the bare
    # word "Bearer" as the *value* of "Authorization=", orphaning the token that
    # follows it into a message that then looks redacted and is not.
    text = _BEARER.sub(lambda m: f"{m.group(1)} {_REDACTED}", text)
    return _SECRET_ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", text)


class RunOutcome:
    """Counts from a finished run, for the CLI to report.

    Not a dataclass because it is a thin read-only view over the run row the
    caller already has, and duplicating the fields would let the two drift.
    """

    __slots__ = ("run",)

    def __init__(self, run: IngestionRun) -> None:
        self.run = run

    @property
    def status(self) -> IngestionStatus:
        return self.run.status

    @property
    def records_received(self) -> int:
        return self.run.records_received

    @property
    def records_written(self) -> int:
        return self.run.records_written

    @property
    def records_skipped(self) -> int:
        return self.run.records_skipped

    @property
    def records_failed(self) -> int:
        return self.run.records_failed

    def __str__(self) -> str:
        return (
            f"{self.run.provider}/{self.run.dataset} {self.run.status.value}: "
            f"received={self.records_received} written={self.records_written} "
            f"skipped={self.records_skipped} failed={self.records_failed}"
        )


#: ``Session.info`` key set when a failed run has been recorded in the session. A
#: caller that wants the failure to be durable commits only when this is set, so
#: it never commits arbitrary pending state on the strength of "an exception
#: happened".
FAILED_RUN_RECORDED: Final = "quantforge.ingestion.failed_run_recorded"

logger = logging.getLogger("quantforge.ingestion.runs")


class RunRecorder:
    """Starts an :class:`IngestionRun`, records its artifacts, finalises it.

    Bound to the caller's session and never commits. That is deliberate: the run
    row and the data it describes must land in the same transaction, or a crash
    between them leaves a "succeeded" run with no rows, or rows with no run.

    Use as a context manager. Everything done inside the ``with`` block runs in
    a SAVEPOINT:

    * **Success:** the savepoint is released and the run row, finalised as
      succeeded/partial, commits together with the data when the caller commits.
    * **Failure:** only the savepoint is rolled back, so the domain writes made
      inside the block disappear while the run row -- created before the
      savepoint -- survives, finalised as ``failed`` with the redacted
      exception. The exception is then re-raised unchanged. The caller must
      still ``commit()`` for the failed run to become durable (rolling back the
      outer transaction discards the run row too, by design: that is the
      caller choosing to discard everything).

    A write the block needs to roll back on failure therefore has to happen
    *inside* the block, not before it.
    """

    __slots__ = ("_now", "_run", "_savepoint", "_session", "_store")

    def __init__(  # noqa: PLR0913 - every collaborator is a distinct dependency
        self,
        *,
        session: Session,
        store: RawResponseStore,
        provider: str,
        dataset: str,
        request_parameters: dict[str, Any],
        now: datetime,
    ) -> None:
        """
        Args:
            session: The caller's session. Nothing is committed here.
            store: Raw store the captures were written to.
            provider: Stable provider identity, matching ``DataSource.name``.
            dataset: Logical job name, e.g. ``"reference"`` or ``"daily-bars"``.
            request_parameters: Non-secret parameters of the request. Callers
                must not put a credential here; it is persisted in the run row
                and would outlive the key.
            now: Injected clock, so the run's timing is testable.
        """
        self._session = session
        self._store = store
        self._now = now
        self._savepoint: SessionTransaction | None = None
        self._run = IngestionRun(
            provider=provider,
            dataset=dataset,
            status=IngestionStatus.RUNNING,
            started_at=now,
            request_parameters=request_parameters,
            records_received=0,
            records_written=0,
            records_skipped=0,
            records_failed=0,
        )
        # A marker left by an earlier failed run on this session must not be read
        # as describing this one.
        session.info.pop(FAILED_RUN_RECORDED, None)
        session.add(self._run)
        # Flushed immediately so ``run_id`` and the manifest key segment are real
        # values rather than None. Both are used while the run is still open --
        # ``record_captures`` runs before ``__exit__`` -- so deferring this to exit
        # would write a manifest labelled "run_id": "None" and a path ending
        # "-None", overwriting a different run's manifest on any retry.
        #
        # This does not commit. If the caller rolls back the *outer* transaction,
        # the run row disappears with the data it was describing. A failure
        # inside the ``with`` block is different: it rolls back only the
        # savepoint opened in ``__enter__``, which is after this flush.
        session.flush()

    @property
    def run_id(self) -> uuid.UUID:
        """The run's id. Flushed on construction, so this is never ``None``."""
        return self._run.id

    @property
    def row(self) -> IngestionRun:
        """The underlying run row.

        Exposed so collaborators can attribute their own rows to the run -- the
        daily-bar writer stores ``ingestion_run_id`` on what it writes -- without
        re-querying by id, which would be a second source of truth for "which run
        was this".
        """
        return self._run

    def __enter__(self) -> RunRecorder:
        self._savepoint = self._session.begin_nested()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> None:
        savepoint = self._savepoint
        self._savepoint = None
        if exc is None:
            try:
                self._session.flush()
            except BaseException as flush_error:
                self._roll_back_and_record_failure(savepoint, flush_error)
                raise
            if savepoint is not None:
                savepoint.commit()
            return
        self._roll_back_and_record_failure(savepoint, exc)

    def _roll_back_and_record_failure(
        self, savepoint: SessionTransaction | None, exc: BaseException
    ) -> None:
        """Discard the block's domain writes, then keep a durable ``failed`` run row.

        Evidence about what was fetched is carried across the rollback: the raw
        artifacts are already on disk, so the manifest pointer and the
        received/skipped/failed counts (facts about the fetch) stay on the
        failed row. ``records_written`` returns to zero, because nothing the
        block wrote survived.
        """
        try:
            self._record_failure(savepoint, exc)
        except Exception:
            # Recording the failure must never replace the error the operator
            # needs. The session is left for the caller to roll back (the
            # FAILED_RUN_RECORDED marker is only set on success below).
            logger.exception("could not record the failed ingestion run; original error follows")

    def _record_failure(self, savepoint: SessionTransaction | None, exc: BaseException) -> None:
        manifest = (
            self._run.raw_artifact_path,
            self._run.raw_content_sha256,
            self._run.raw_artifact_bytes,
        )
        counts = (
            self._run.records_received,
            self._run.records_skipped,
            self._run.records_failed,
        )
        if savepoint is not None:
            # Also required when the flush itself failed: SQLAlchemy leaves the
            # savepoint inactive-pending-rollback and the session unusable until
            # it is rolled back.
            savepoint.rollback()
        (
            self._run.raw_artifact_path,
            self._run.raw_content_sha256,
            self._run.raw_artifact_bytes,
        ) = manifest
        (
            self._run.records_received,
            self._run.records_skipped,
            self._run.records_failed,
        ) = counts
        self.fail(exc)
        self._session.flush()
        self._session.info[FAILED_RUN_RECORDED] = True

    # ── Artifacts ──────────────────────────────────────────────────────────

    def record_captures(self, stored: Sequence[StoredCapture]) -> None:
        """Persist a manifest of the captured responses and point the run at it.

        A single-capture run still gets a manifest rather than pointing straight
        at the payload file. One code path means the run row is never
        ambiguously "a file" in one case and "a list of files" in another, which
        is the kind of inconsistency that is cheap to introduce and expensive to
        read back a year later.
        """
        if not stored:
            return
        manifest = {
            "run_id": str(self._run.id),
            "provider": self._run.provider,
            "dataset": self._run.dataset,
            "retrieved_at": self._now.isoformat(),
            "artifact_count": len(stored),
            "artifacts": [
                {
                    # The path template, not a concrete URL, so the manifest can
                    # never carry a query string a credential hid in.
                    "endpoint": item.capture.endpoint,
                    "path": item.artifact.relative_path,
                    "sha256": item.artifact.sha256,
                    "byte_length": item.artifact.byte_length,
                    "retrieved_at": item.capture.retrieved_at.isoformat(),
                    "provider_status": item.capture.provider_status,
                    "deduplicated": item.artifact.deduplicated,
                }
                for item in stored
            ],
        }
        payload = json.dumps(manifest, indent=2, sort_keys=True).encode()
        written = self._store.write_bytes(
            payload,
            provider=self._run.provider,
            dataset=f"{self._run.dataset}-manifest",
            # Keyed by run id so two runs of the same request never share a
            # manifest path and overwrite each other's evidence.
            key_segment=f"{self._run.started_at:%Y%m%dT%H%M%SZ}-{self._run.id}",
        )
        self._run.raw_artifact_path = written.relative_path
        self._run.raw_content_sha256 = written.sha256
        self._run.raw_artifact_bytes = written.byte_length

    # ── Counting ───────────────────────────────────────────────────────────

    def count_received(self, count: int) -> None:
        self._run.records_received = count

    def count_written(self, count: int) -> None:
        self._run.records_written = count

    def count_skipped(self, count: int) -> None:
        self._run.records_skipped = count

    def count_failed(self, count: int) -> None:
        self._run.records_failed = count

    # ── Completion ─────────────────────────────────────────────────────────

    def succeed(self, completed_at: datetime) -> RunOutcome:
        return self._finish(IngestionStatus.SUCCEEDED, completed_at)

    def partial(self, completed_at: datetime) -> RunOutcome:
        """Finish with some records written and some rejected."""
        return self._finish(IngestionStatus.PARTIAL, completed_at)

    def fail(self, exc: BaseException) -> RunOutcome:
        """Finish as ``failed``, recording the exception type and a safe message.

        The message is redacted and then truncated. Redaction first, because a
        credential inside the retained prefix is still a credential: truncating a
        URL that begins with ``?apiKey=`` keeps the key and discards the part that
        would have identified it as sensitive.
        """
        message = _safe_error_text(exc)
        if len(message) > _MAX_ERROR_MESSAGE:
            message = message[: _MAX_ERROR_MESSAGE - 3] + "..."
        self._run.error_type = type(exc).__name__[:64]
        self._run.error_message = message or None
        return self._finish(IngestionStatus.FAILED, self._now)

    def _finish(self, status: IngestionStatus, completed_at: datetime) -> RunOutcome:
        self._run.finalize(
            status=status,
            completed_at=completed_at,
            records_received=self._run.records_received,
            records_written=self._run.records_written,
            records_skipped=self._run.records_skipped,
            records_failed=self._run.records_failed,
        )
        return RunOutcome(self._run)
