"""On-disk preservation of raw provider responses.

Why on disk and not in PostgreSQL
---------------------------------
A raw payload is an opaque blob whose only purpose is to be re-readable later:
to re-derive a normalisation when a provider's semantics turn out to have been
misread, and to settle a disagreement about what the provider actually said.
That is a file-shaped job, not a row-shaped one. Keeping megabytes of JSON in
``BYTEA`` would bloat every backup, defeat ``SELECT``-able provenance, and make
the hot tables harder to reason about. Revision 0003 stores the *path and hash*
in PostgreSQL, which is what makes a file trustworthy; the bytes stay on disk.

Layout
------
::

    data/raw/massive/
        reference/
            AAPL/
                9f2c1a4b...<64 hex chars>.json
                9f2c1a4b...<64 hex chars>.meta.json
        daily-bars/
            AAPL/
                1d3e5f7a...<64 hex chars>.json
                1d3e5f7a...<64 hex chars>.meta.json

Deterministic, self-verifying, collision-free
--------------------------------------------
The filename is the full SHA-256 of the body, and nothing else. Three properties
follow, each of which the timestamp-then-hash-prefix naming did not have:

* **Re-fetching unchanged data costs nothing.** A second run over the same range
  produces the same bytes, hence the same name, hence a no-op. Re-ingesting a
  decade of daily bars every night stops growing the store, which is the whole
  point of capturing before interpreting.
* **The name is a claim you can check without the database.** ``sha256sum`` of
  the file must equal its own filename. A truncated or edited artifact is
  detectable by anyone with a shell, without trusting a row, a manifest, or this
  code.
* **No collision is possible**, because the entire address space is the digest.

The retrieval time is *not* in the filename: the digest alone would make an
operator's directory listing unreadable, so the timestamp lives in the key
segment (``20260928T031920Z-<run-id>`` for run manifests), which keeps the
listing chronological while an identical digest at the same instant stays a
no-op and a retried ingestion never fills the directory with copies. Every run
that fetched the artifact is recorded in ``ingestion_run``, so "when was this
first seen" is answerable from the database even though the path encodes no
timestamp of its own.

Safety
------
* The provider and dataset segments come from a fixed allowlist, never from
  caller input.
* The ticker segment is sanitised, because it reaches this module from a CLI
  argument. ``../../etc/passwd`` cannot escape: a segment containing a path
  separator, a ``..``, a NUL or a leading dot is rejected outright rather than
  escaped, so a traversal attempt is a loud failure and not a quiet relocation.
* Writes go to a temporary file in the destination directory and are then
  ``os.replace``d into place, which is atomic on POSIX. A reader therefore sees
  either no file or a complete one, never a half-written body, even if the
  process is killed mid-write.

Secret hygiene
--------------
The metadata sidecar records the endpoint *template* and the non-secret request
parameters. The API key is never a query parameter, so it is not among them; the
sidecar additionally passes everything through the same segment sanitiser so a
parameter value can never become a filename. A unit test asserts the configured
key value appears in neither the path, the metadata, nor the body.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import IO

from quantforge.providers.base import RawCapture

__all__ = [
    "MAX_PACKAGE_BYTES",
    "RawArtifact",
    "RawResponseStore",
    "RawStoreError",
    "StoredCapture",
]

logger = logging.getLogger(__name__)

#: A path segment is only ever these characters. Anything else -- a separator, a
#: ``..``, a NUL, a leading dot, a backslash -- is a rejection, not an escape.
_SAFE_SEGMENT = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")

#: Providers allowed as a top-level segment. Kept explicit so a provider name
#: that arrives from configuration cannot become a directory verbatim.
#: ``finnhub`` is earnings-only (Massive's own earnings endpoint is
#: entitlement-blocked); it does not replace Massive for reference/daily-bar
#: data, which stays under ``massive``.
_ALLOWED_PROVIDERS = frozenset({"finnhub", "massive", "sec-edgar"})

#: Hex characters of a digest shown in a log line. Identifiers, not provenance:
#: the full 64-character digest is what names a file and what is persisted, and
#: the truncated form exists only to keep log lines readable.
_BODY_DIGEST_CHARS = 12

#: Cap on a single artifact. A daily range for one ticker is kilobytes; a
#: runaway response is a bug or an attack, and writing it unbounded would fill
#: the disk. Checked before the write so nothing partial is left behind.
_MAX_ARTIFACT_BYTES = 256 * 1024 * 1024

#: Cap for a bulk package copied from a local file (e.g. an SEC Financial Statement Data Sets
#: quarterly ZIP, ~60-125 MB today). Larger than a response cap because a package is a
#: deliberate operator-supplied file, but still bounded.
MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024


class RawStoreError(RuntimeError):
    """The raw response could not be preserved."""


@dataclass(frozen=True, slots=True)
class RawArtifact:
    """Where an artifact landed and what it contains.

    ``path`` is relative to the store root so it can be recorded in
    ``IngestionRun.raw_artifact_path`` without embedding whatever absolute
    directory the deployment happens to use.
    """

    relative_path: str
    sha256: str
    byte_length: int
    #: True when an identical file was already present and left untouched.
    deduplicated: bool


def sha256_hex(payload: bytes) -> str:
    """Return the SHA-256 hex digest of ``payload``.

    ``hashlib.sha256`` over the exact bytes. Never Python's built-in ``hash()``,
    which is randomised per process and therefore useless for provenance: two
    runs of the same payload would not agree, and nothing could be verified
    against a stored digest later.
    """
    return hashlib.sha256(payload).hexdigest()


def _safe_segment(value: str, *, kind: str) -> str:
    """Validate one path segment, rejecting anything unsafe.

    Raises:
        RawStoreError: If the segment is empty, too long, starts with a dot, or
            contains anything outside ``[A-Za-z0-9._-]``. Traversal attempts land
            here rather than being sanitised away.
    """
    if not _SAFE_SEGMENT.match(value):
        raise RawStoreError(
            f"unsafe {kind} segment {value!r}: expected 1-64 characters matching "
            "[A-Za-z0-9][A-Za-z0-9._-]* with no path separators"
        )
    return value


@dataclass(frozen=True, slots=True)
class StoredCapture:
    """A capture together with where it landed.

    Paired here rather than by the caller zipping two sequences: a run manifest
    needs both the response's own metadata (when it was retrieved, which endpoint
    produced it) and where it was written, and a positional pairing is exactly
    the kind of thing that silently misaligns.
    """

    capture: RawCapture
    artifact: RawArtifact


class RawResponseStore:
    """Persists :class:`RawCapture` objects under a provider-scoped tree.

    Usage::

        store = RawResponseStore(Path("data/raw"))
        artifact = store.store(capture)
    """

    __slots__ = ("_root",)

    def __init__(self, root: Path) -> None:
        """Args:
        root: Directory that will contain ``<provider>/<dataset>/...``. Created on
            first write, not here, so constructing a store has no side effects.
        """
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def store(self, capture: RawCapture) -> RawArtifact:
        """Write one capture and return where it landed.

        The write is atomic, so the returned path never refers to a partial file.
        Re-storing identical bytes at an identical instant is a no-op.
        """
        provider = _safe_segment(capture.provider.strip().lower(), kind="provider")
        if provider not in _ALLOWED_PROVIDERS:
            raise RawStoreError(
                f"provider {provider!r} is not an allowed raw-store provider "
                f"(allowed: {', '.join(sorted(_ALLOWED_PROVIDERS))})"
            )
        dataset = _safe_segment(capture.dataset.strip(), kind="dataset")
        key = self._key_segment(capture.request_parameters)
        digest = sha256_hex(capture.body)
        directory = self._root / provider / dataset / key
        target = directory / f"{digest}.json"
        meta_target = directory / f"{digest}.meta.json"

        if _stored_unchanged(target, digest, len(capture.body)):
            logger.debug("raw artifact already stored: %s", target)
            return RawArtifact(
                relative_path=_relative(target, self._root),
                sha256=digest,
                byte_length=len(capture.body),
                deduplicated=True,
            )

        if len(capture.body) > _MAX_ARTIFACT_BYTES:
            raise RawStoreError(
                f"raw response of {len(capture.body)} bytes exceeds the "
                f"{_MAX_ARTIFACT_BYTES} byte artifact limit"
            )

        _atomic_write(target, capture.body)
        _atomic_write(meta_target, _metadata_bytes(capture, digest))
        logger.info(
            "stored raw %s response (%d bytes, sha256=%s)",
            dataset,
            len(capture.body),
            digest[:_BODY_DIGEST_CHARS],
        )
        return RawArtifact(
            relative_path=_relative(target, self._root),
            sha256=digest,
            byte_length=len(capture.body),
            deduplicated=False,
        )

    def write_bytes(
        self,
        payload: bytes,
        *,
        provider: str,
        dataset: str,
        key_segment: str,
    ) -> RawArtifact:
        """Write a locally-generated artifact (e.g. a run manifest).

        Separate from :meth:`store` because this is not a provider response: there
        is no capture to describe, and fabricating one -- an endpoint, request
        parameters, a provider status -- would put invented provenance into a file
        whose entire purpose is to be authoritative about what was stored. The
        caller names the dataset explicitly instead.

        Content-addressed and atomic, exactly like :meth:`store`, so an unchanged
        manifest is not rewritten and a partial one is never visible.

        Takes no retrieval time: like every other artifact its name is the content
        digest. A manifest embeds its own ``run_id``, so two different runs still
        get different manifests, and an identical one is a genuine duplicate.
        """
        safe_provider = _safe_segment(provider.strip().lower(), kind="provider")
        if safe_provider not in _ALLOWED_PROVIDERS:
            message = (
                f"provider {safe_provider!r} is not an allowed raw-store provider "
                f"(allowed: {', '.join(sorted(_ALLOWED_PROVIDERS))})"
            )
            raise RawStoreError(message)
        safe_dataset = _safe_segment(dataset.strip(), kind="dataset")
        safe_key = _safe_segment(key_segment.strip(), kind="key")
        digest = sha256_hex(payload)
        directory = self._root / safe_provider / safe_dataset / safe_key
        target = directory / f"{digest}.json"

        if _stored_unchanged(target, digest, len(payload)):
            return RawArtifact(
                relative_path=_relative(target, self._root),
                sha256=digest,
                byte_length=len(payload),
                deduplicated=True,
            )
        if len(payload) > _MAX_ARTIFACT_BYTES:
            message = (
                f"artifact of {len(payload)} bytes exceeds the {_MAX_ARTIFACT_BYTES} byte limit"
            )
            raise RawStoreError(message)

        _atomic_write(target, payload)
        logger.info(
            "stored %s artifact (%d bytes, sha256=%s)",
            safe_dataset,
            len(payload),
            digest[:_BODY_DIGEST_CHARS],
        )
        return RawArtifact(
            relative_path=_relative(target, self._root),
            sha256=digest,
            byte_length=len(payload),
            deduplicated=False,
        )

    def store_file(
        self,
        source: Path,
        *,
        provider: str,
        dataset: str,
        key_segment: str,
        suffix: str = ".zip",
    ) -> RawArtifact:
        """Preserve a local file (e.g. a bulk package) without reading it into memory.

        Streams ``source`` in chunks, hashing as it copies, into a temporary file beside
        the destination, then atomically renames it to ``<sha256><suffix>``. Content-
        addressed exactly like :meth:`store`: an identical file already present is left
        untouched (after being re-verified against its own name), and a file at that name
        whose content does not match its digest is a loud failure, never an overwrite.

        The sidecar records the digest, size and the key segment only. The source's local
        path is deliberately NOT recorded: it can embed a username or a directory layout,
        and it is not provenance (the digest is).
        """
        safe_provider = _safe_segment(provider.strip().lower(), kind="provider")
        if safe_provider not in _ALLOWED_PROVIDERS:
            message = (
                f"provider {safe_provider!r} is not an allowed raw-store provider "
                f"(allowed: {', '.join(sorted(_ALLOWED_PROVIDERS))})"
            )
            raise RawStoreError(message)
        safe_dataset = _safe_segment(dataset.strip(), kind="dataset")
        safe_key = _safe_segment(key_segment.strip(), kind="key")
        if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
            message = f"unsafe artifact suffix {suffix!r}"
            raise RawStoreError(message)
        directory = self._root / safe_provider / safe_dataset / safe_key
        try:
            directory.mkdir(parents=True, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 - closed in `finally` below
                mode="wb", dir=directory, prefix=".incoming.", suffix=".tmp", delete=False
            )
        except OSError as exc:
            raise RawStoreError(f"cannot prepare raw artifact directory: {exc}") from exc
        temporary = Path(handle.name)
        try:
            digest, length = _copy_and_hash(source, handle, max_bytes=MAX_PACKAGE_BYTES)
            target = directory / f"{digest}{suffix}"
            if target.exists():
                temporary.unlink(missing_ok=True)
                if _hash_file(target) != digest:
                    message = (
                        f"existing file {target.name!r} does not match its content-addressed "
                        "SHA-256 name; refusing to overwrite or ignore the mismatch"
                    )
                    raise RawStoreError(message)
                return RawArtifact(
                    relative_path=_relative(target, self._root),
                    sha256=digest,
                    byte_length=length,
                    deduplicated=True,
                )
            temporary.replace(target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        meta = {
            "provider": safe_provider,
            "dataset": safe_dataset,
            "key": safe_key,
            "sha256": digest,
            "byte_length": length,
            "origin": "local_file",
        }
        _atomic_write(
            directory / f"{digest}.meta.json", json.dumps(meta, indent=2, sort_keys=True).encode()
        )
        logger.info(
            "stored %s package (%d bytes, sha256=%s)",
            safe_dataset,
            length,
            digest[:_BODY_DIGEST_CHARS],
        )
        return RawArtifact(
            relative_path=_relative(target, self._root),
            sha256=digest,
            byte_length=length,
            deduplicated=False,
        )

    def path_of(self, relative_path: str) -> Path:
        """The on-disk path of a stored artifact (re-validated to lie inside the store)."""
        return self._resolve(relative_path)

    def store_all(self, captures: Iterable[RawCapture]) -> list[StoredCapture]:
        """Store several captures, preserving order.

        Order is preserved because it is the page order the provider returned,
        which is what a reader needs in order to follow a paged response.
        """
        return [StoredCapture(capture=c, artifact=self.store(c)) for c in captures]

    def read(self, relative_path: str) -> bytes:
        """Read back a stored artifact by its relative path.

        The path is re-validated, so a value read out of the database cannot be
        used to read outside the store.
        """
        target = self._resolve(relative_path)
        try:
            return target.read_bytes()
        except OSError as exc:
            raise RawStoreError(f"cannot read raw artifact {relative_path!r}: {exc}") from exc

    def metadata(self, relative_path: str) -> dict[str, object]:
        """Read the metadata sidecar for a stored artifact."""
        target = self._resolve(relative_path).with_suffix(".meta.json")
        # ``.json`` -> ``.meta.json`` needs care: with_suffix replaces the last
        # suffix, so the stem is the timestamp-hash prefix and this is correct.
        if not target.exists():
            raise RawStoreError(f"no metadata sidecar for {relative_path!r}")
        return dict(json.loads(target.read_text(encoding="utf-8")))

    def _resolve(self, relative_path: str) -> Path:
        """Join a stored relative path onto the root, refusing to escape it.

        ``Path.resolve()`` plus an ``is_relative_to`` check is the reliable form:
        it defeats ``..``, absolute paths and symlink tricks in one step.
        """
        candidate = (self._root / relative_path).resolve()
        root = self._root.resolve()
        if not candidate.is_relative_to(root):
            message = f"path {relative_path!r} escapes the raw store root"
            raise RawStoreError(message)
        return candidate

    @staticmethod
    def _key_segment(request_parameters: Mapping[str, str]) -> str:
        """Derive a safe directory name from the non-secret request parameters.

        A ticker is the meaningful grouping, so it is preferred; otherwise a
        short digest of the whole parameter set is used, which keeps grouping
        stable without letting a parameter value reach the filesystem unchecked.
        """
        ticker = request_parameters.get("ticker")
        if ticker:
            return _safe_segment(ticker, kind="ticker")
        material = json.dumps(request_parameters, sort_keys=True, separators=(",", ":"))
        return f"params-{sha256_hex(material.encode())[:16]}"


def _copy_and_hash(source: Path, handle: IO[bytes], *, max_bytes: int) -> tuple[str, int]:
    """Copy ``source`` into the open ``handle`` in chunks; return ``(sha256, length)``."""
    digest = hashlib.sha256()
    length = 0
    try:
        with handle, source.open("rb") as reader:
            while chunk := reader.read(_COPY_CHUNK_BYTES):
                length += len(chunk)
                if length > max_bytes:
                    message = f"file exceeds the {max_bytes} byte package limit"
                    raise RawStoreError(message)
                digest.update(chunk)
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise RawStoreError(f"cannot copy {source.name!r} into the raw store: {exc}") from exc
    if length == 0:
        message = f"{source.name!r} is empty"
        raise RawStoreError(message)
    return digest.hexdigest(), length


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        while chunk := reader.read(_COPY_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(target: Path, root: Path) -> str:
    return target.resolve().relative_to(root.resolve()).as_posix()


def _stored_unchanged(target: Path, digest: str, byte_length: int) -> bool:
    """Whether ``target`` already holds exactly the content it is named for.

    These files are content-addressed: the whole point of the name is that it
    commits to a payload, so a file at that name whose content does not hash to
    it is corruption or tampering. Rewriting over it would quietly discard a
    record; ignoring it would accept a lie as provenance. Both are wrong, so the
    mismatch raises instead of being papered over. The size pre-check keeps the
    common no-op dedup path on stable snapshots cheap without ever substituting
    for the hash, which remains the witness.
    """
    if not target.exists():
        return False
    if target.stat().st_size != byte_length or sha256_hex(target.read_bytes()) != digest:
        message = (
            f"existing file {target.name!r} does not match its content-addressed "
            "SHA-256 name; refusing to overwrite or ignore the mismatch"
        )
        raise RawStoreError(message)
    return True


def _metadata_bytes(capture: RawCapture, digest: str) -> bytes:
    """Render the sidecar.

    Every request parameter value is sanitised before it is written, so a value
    that somehow carried a path separator or a credential-shaped string is
    rejected rather than persisted.
    """
    parameters = {
        key: _safe_segment(value, kind=f"request parameter {key!r}")
        for key, value in sorted(capture.request_parameters.items())
    }
    payload: dict[str, object] = {
        "provider": capture.provider,
        "dataset": capture.dataset,
        "endpoint": capture.endpoint,
        "request_parameters": parameters,
        "retrieved_at": capture.retrieved_at.astimezone(UTC).isoformat(),
        "sha256": digest,
        "byte_length": len(capture.body),
        "provider_status": capture.provider_status,
    }
    return json.dumps(payload, indent=2, sort_keys=True).encode()


def _atomic_write(target: Path, payload: bytes) -> None:
    """Write ``payload`` to ``target`` atomically.

    The temporary file is created in the destination directory so the final
    ``Path.replace`` stays within one filesystem, which is what makes it atomic.
    A crash therefore leaves a stray ``.tmp`` file at worst, never a truncated
    artifact that a later run would treat as complete.
    """
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 - closed in `finally` below
            mode="wb",
            dir=target.parent,
            prefix=f".{target.stem}.",
            suffix=".tmp",
            delete=False,
        )
        temporary = Path(handle.name)
        try:
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise RawStoreError(f"cannot write raw artifact {target.name!r}: {exc}") from exc
