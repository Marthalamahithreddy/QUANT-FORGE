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
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy.orm import Session, SessionTransaction

from quantforge.ingestion.raw_store import RawArtifact, RawResponseStore, StoredCapture
from quantforge.models.enums import IngestionStatus
from quantforge.models.ingestion import IngestionRun

__all__ = ["FAILED_RUN_ID", "RecordFailure", "RunOutcome", "RunRecorder", "redact_text"]

#: Error messages are truncated to fit ``ingestion_run.error_message``. The same
#: bound as the column, named once so the two cannot drift.
_MAX_ERROR_MESSAGE = 1000

#: Only the first characters of a message are ever stored, so only they are redacted: a pathological message
#: (megabytes of hyphenated words) would otherwise make the e-mail and URL rules quadratic. Generous, because
#: redaction can shorten text (a path becomes ``[path]``) before the 1000-character bound applies.
_MAX_REDACT_INPUT = 5000

#: Query strings are dropped wholesale. This is the coarse, always-on rule: a
#: provider that authenticates via query parameter is the common case, and no
#: allowlist of "safe" parameters is worth the risk of one being added wrongly. A bare ``?`` (the end of a
#: question) carries nothing and is left alone, because the CLI now prints its error lines through this rule.
_QUERY_STRING = re.compile(r"\?[^\s\"'<>]+")

#: Credentials that also appear outside a URL -- in a header dump, a repr, a libpq connection string, a
#: JSON body, a provider SDK's own error text. Redacted by NAME wherever they appear. The name may follow
#: any non-alphanumeric (``client_secret``, ``x-api-key``, ``refresh_token``; not ``secretary`` or ``tokens``)
#: and be quoted (``"api_key"``); the value may
#: be a bare run (commas and semicolons included: a password may contain them), ``'single-quoted with spaces'`` or ``"double-quoted, with \\"escapes\\""``.
_SECRET_NAME = (
    r"(?:api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|id[_-]?token|token|secret"
    r"|password|passwd|pwd|authorization|auth)"
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?P<name>[\"']?" + _SECRET_NAME + r"[\"']?)(?P<sep>\s*[=:]\s*)"
    r"(?P<value>\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'|(?:\\.|[^\s\"'}\]])+)"
)

#: A bare bearer/basic token, with no ``name=`` to key off.
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")

#: Credentials embedded in a URL's authority (``postgresql://user:pass@host``): a database DSN or a
#: proxy URL can appear in a driver's error text.
_URL_USERINFO = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)[^/\s]*@")

#: An e-mail address: the SEC contact identity travels in a User-Agent and must never be persisted.
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")

#: Absolute filesystem paths under the usual roots (the raw-store root, a home directory): they
#: reveal the machine's layout and are of no use to the reader of a run row.
_ABSOLUTE_PATH = re.compile(
    r"(?<![\w/.-])(?:file://)?/(?:Users|home|var|tmp|private|opt|srv|mnt|root|etc|data|Volumes"
    r"|Library|usr|app|workspace|work)/[^\s,;\"'<>)\]}]+"
)

_REDACTED = "[redacted]"


def _safe_error_text(exc: BaseException) -> str:
    """Reduce an exception to text that is safe to persist forever.

    The ``IngestionRun`` row outlives the process, the run's raw artifacts outlive
    the row, and neither is scrubbed by a credential rotation. So a message is only
    stored after a query string, an API-key-shaped assignment and a bearer token
    have been replaced -- truncation alone is not enough, because a credential in
    the first 1000 characters is still a credential.

    Redaction is deliberately lossy, and so are the log lines that report a failure to record a failure:
    they carry the exception class and this same redacted text, never a traceback (a traceback repeats the
    raw message), so no sink holds a credential the database row does not.
    """
    return _redact(str(exc).strip())


def _redact(raw: str) -> str:
    """The redaction rules, applied to any text about to be persisted."""
    # Userinfo first: a password may itself contain '?', which the query rule would otherwise cut at.
    text = _URL_USERINFO.sub(lambda m: f"{m.group('scheme')}{_REDACTED}@", raw[:_MAX_REDACT_INPUT])
    text = _QUERY_STRING.sub(f"?{_REDACTED}", text)
    text = _EMAIL.sub(_REDACTED, text)
    text = _ABSOLUTE_PATH.sub("[path]", text)
    # Bearer/basic first. Running the assignment rule first would consume the bare
    # word "Bearer" as the *value* of "Authorization=", orphaning the token that
    # follows it into a message that then looks redacted and is not.
    text = _BEARER.sub(lambda m: f"{m.group(1)} {_REDACTED}", text)
    return _SECRET_ASSIGNMENT.sub(lambda m: f"{m.group('name')}{m.group('sep')}{_REDACTED}", text)


def redact_text(raw: str) -> str:
    """The shared redaction, for any text a command prints about a failure (not truncated)."""
    return _redact(raw)


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


@dataclass(frozen=True, slots=True)
class RecordFailure:
    """One record that failed inside an otherwise finished run, already redacted.

    A ``partial`` run says only *how many* records failed; this carries *why* for the first of them, so
    an operator reading the run row can tell a typo from an outage. The text is redacted when the
    failure is created, so nothing downstream can persist an unredacted copy. This is deliberately not
    a rejection table: a short summary on the existing ``error_message`` column is the whole feature.
    """

    label: str
    error_type: str
    reason: str

    @classmethod
    def of(cls, label: str, exc: BaseException) -> RecordFailure:
        return cls(label=label, error_type=type(exc).__name__[:64], reason=_safe_error_text(exc))


def partial_error_summary(failures: Sequence[RecordFailure], failed_count: int) -> str | None:
    """``"N record(s) failed; first: <label>: <reason>"``, redacted and capped, or ``None``.

    ``failed_count`` is the run's own tally (it can exceed ``len(failures)``). The first failure is the
    first in the service's deterministic processing order, never a set's iteration order, and the text
    is capped to the ``error_message`` column exactly like a failed run's.
    """
    if failed_count < 1 or not failures:
        return None
    first = failures[0]
    text = _redact(f"{failed_count} record(s) failed; first: {first.label}: {first.reason}")
    if len(text) > _MAX_ERROR_MESSAGE:
        text = text[: _MAX_ERROR_MESSAGE - 3] + "..."
    return text


#: ``Session.info`` key holding the id of the failed run recorded in the session (set with
#: :data:`FAILED_RUN_RECORDED`), so the CLI can tell the operator which run failed.
FAILED_RUN_ID: Final = "quantforge.ingestion.failed_run_id"

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

    __slots__ = ("_now", "_pending", "_run", "_savepoint", "_session", "_store", "_stored")

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
        #: Every artifact kept for this run so far (accepted and rejected), in the order kept. The
        #: manifest always lists all of them: a later call never replaces an earlier one's entries.
        self._stored: list[StoredCapture] = []
        #: Artifacts kept since the manifest was last written.
        self._pending = False
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
        session.info.pop(FAILED_RUN_ID, None)
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
                self._write_pending_manifest()
                self._session.flush()
            except BaseException as flush_error:
                self._roll_back_and_record_failure(savepoint, flush_error)
                raise
            if savepoint is not None:
                savepoint.commit()
            return
        # The failure path: the bytes already kept (accepted and rejected) must still be reachable
        # from the failed row. Best effort -- it must never replace the error being reported.
        try:
            self._write_pending_manifest()
        except Exception as manifest_error:
            logger.error(
                "could not write the manifest of the evidence kept by a failing run: %s: %s",
                type(manifest_error).__name__,
                _safe_error_text(manifest_error),
            )
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
        except Exception as recording_error:
            # Recording the failure must never replace the error the operator
            # needs. The session is left for the caller to roll back (the
            # FAILED_RUN_RECORDED marker is only set on success below).
            logger.error(
                "could not record the failed ingestion run: %s: %s",
                type(recording_error).__name__,
                _safe_error_text(recording_error),
            )

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
        self._session.info[FAILED_RUN_ID] = self._run.id

    # ── Artifacts ──────────────────────────────────────────────────────────

    def record_captures(self, stored: Sequence[StoredCapture]) -> None:
        """Keep these artifacts and write the run's manifest now.

        A single-capture run still gets a manifest rather than pointing straight
        at the payload file. One code path means the run row is never
        ambiguously "a file" in one case and "a list of files" in another, which
        is the kind of inconsistency that is cheap to introduce and expensive to
        read back a year later.
        """
        self.add_artifacts(stored)
        self._write_pending_manifest()

    def add_artifacts(self, stored: Sequence[StoredCapture]) -> None:
        """Keep artifacts whose bytes are already durable; the manifest is written at exit.

        The bytes were written (content-addressed, atomically) before this is called, so they are
        durable the moment the caller has them. Only the manifest -- the run's pointer to them -- is
        deferred, and it is written however the block ends (success, partial or failure), covering
        every artifact kept so far in the order kept. That is what lets a service retain a batch's
        evidence one item at a time without a manifest write per item.
        """
        if not stored:
            return
        self._stored.extend(stored)
        self._pending = True

    def _write_pending_manifest(self) -> None:
        if not self._pending or not self._stored:
            return
        stored = self._stored
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
        self._pending = False

    def record_file_artifacts(self, artifacts: Sequence[tuple[str, RawArtifact]]) -> None:
        """Manifest of locally supplied files (bulk packages), keyed by an endpoint-like label.

        A local file was not *retrieved* by QuantForge, so each entry says ``origin:
        local_file`` and carries ``retrieved_at: null``; the manifest's own ``retrieved_at`` is
        the run's start (when QuantForge received the file). Nothing invents a download time.
        """
        if not artifacts:
            return
        manifest = {
            "run_id": str(self._run.id),
            "provider": self._run.provider,
            "dataset": self._run.dataset,
            "retrieved_at": self._now.isoformat(),
            "artifact_count": len(artifacts),
            "artifacts": [
                {
                    "endpoint": label,
                    "path": artifact.relative_path,
                    "sha256": artifact.sha256,
                    "byte_length": artifact.byte_length,
                    "retrieved_at": None,
                    "origin": "local_file",
                    "provider_status": None,
                    "deduplicated": artifact.deduplicated,
                }
                for label, artifact in artifacts
            ],
        }
        payload = json.dumps(manifest, indent=2, sort_keys=True).encode()
        written = self._store.write_bytes(
            payload,
            provider=self._run.provider,
            dataset=f"{self._run.dataset}-manifest",
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

    def partial(self, completed_at: datetime, failures: Sequence[RecordFailure] = ()) -> RunOutcome:
        """Finish with some records written and some rejected.

        With ``failures`` the row also records *why*: ``error_message`` becomes ``"N record(s)
        failed; first: <reason>"`` (redacted, capped) and ``error_type`` the first failure's class.
        Without them the row is exactly what it was before this existed.
        """
        outcome = self._finish(IngestionStatus.PARTIAL, completed_at)
        summary = partial_error_summary(failures, self._run.records_failed)
        if summary is not None:
            self._run.error_type = failures[0].error_type
            self._run.error_message = summary
        return outcome

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
