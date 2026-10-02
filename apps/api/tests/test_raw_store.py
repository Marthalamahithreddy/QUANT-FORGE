"""Unit tests for the raw response store.

The store's contract is that what it writes can be proven to be what arrived, so
the tests concentrate on the three ways that can go wrong: the bytes change, the
file is not atomic, or a caller-supplied key escapes the root.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from pathlib import Path

import pytest

from quantforge.ingestion.raw_store import RawResponseStore, RawStoreError
from quantforge.providers.base import RawCapture

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)
BODY = b'{"results":[{"c":123.45}],"status":"OK"}'


@pytest.fixture
def store(tmp_path: Path) -> RawResponseStore:
    return RawResponseStore(root=tmp_path / "raw")


def _capture(**overrides: object) -> RawCapture:
    defaults: dict[str, object] = {
        "provider": "massive",
        "dataset": "daily-bars",
        "endpoint": "v2/aggs/ticker/AAPL/range/1/day",
        "request_parameters": {"ticker": "AAPL", "start": "2026-09-01", "end": "2026-09-05"},
        "body": BODY,
        "retrieved_at": NOW,
        "provider_status": "OK",
    }
    defaults.update(overrides)
    return RawCapture(**defaults)  # type: ignore[arg-type]


# ── provider allowlist ──────────────────────────────────────────────────────


def test_massive_is_an_allowed_provider(store: RawResponseStore) -> None:
    artifact = store.store(_capture(provider="massive"))
    assert artifact.relative_path.startswith("massive/")


def test_finnhub_is_an_allowed_provider(store: RawResponseStore) -> None:
    """Regression pin: the Finnhub earnings adapter (Step 7.2C) must be able
    to store raw artifacts under its own namespace. Before this, the
    provider allowlist only knew about ``massive``, and every Finnhub
    capture failed with ``RawStoreError`` -- caught by actually running the
    adapter through the real ingestion pipeline, not by reasoning about it.
    """
    artifact = store.store(_capture(provider="finnhub", dataset="earnings"))
    assert artifact.relative_path.startswith("finnhub/")


def test_an_unlisted_provider_is_rejected(store: RawResponseStore) -> None:
    """The allowlist exists specifically so a provider name cannot become an
    arbitrary directory; it must still refuse anything not on it."""
    with pytest.raises(RawStoreError, match="not an allowed raw-store provider"):
        store.store(_capture(provider="some-other-vendor"))


# ── fidelity ────────────────────────────────────────────────────────────────


def test_body_is_stored_verbatim(store: RawResponseStore) -> None:
    """The artifact must be the bytes that arrived, not a re-serialisation.

    A body with irregular whitespace and a numeric literal that no re-encoding
    would reproduce is used deliberately: if the store re-encoded, the bytes would
    differ and this would fail.
    """
    awkward = b'{"status" : "OK" ,\n "results":  [ {"c": 1.100000000000000005} ]}'
    artifact = store.store(_capture(body=awkward))

    assert (store.root / artifact.relative_path).read_bytes() == awkward
    assert artifact.sha256 == hashlib.sha256(awkward).hexdigest()
    assert artifact.byte_length == len(awkward)


def test_identical_body_is_deduplicated_not_rewritten(store: RawResponseStore) -> None:
    """Same bytes must land on one file.

    Re-fetching a range that has not changed is the common case, and storing a
    second copy per run would multiply the store by the number of runs for no
    added evidence.
    """
    first = store.store(_capture())
    second = store.store(_capture(retrieved_at=datetime(2026, 9, 28, 9, 0, tzinfo=UTC)))

    assert first.relative_path == second.relative_path
    assert first.sha256 == second.sha256
    assert first.deduplicated is False
    assert second.deduplicated is True


def test_deduplication_verifies_the_content_hash(store: RawResponseStore) -> None:
    """A same-length-but-different body at a content-addressed name is corruption.

    Size is a cheap pre-check, but the SHA-256 in the name is the witness. A file
    that exists under a digest name whose content does not hash to that digest
    must raise rather than be silently overwritten or accepted.
    """
    first = store.store(_capture())
    target = store.root / first.relative_path
    tampered = b"x" + BODY[1:]
    assert len(tampered) == len(BODY)
    target.write_bytes(tampered)

    with pytest.raises(RawStoreError, match="SHA-256 name"):
        store.store(_capture())


def test_deduplication_verifies_a_different_length_too(store: RawResponseStore) -> None:
    """A truncated or extended body must also be caught, not size-masked."""
    first = store.store(_capture())
    (store.root / first.relative_path).write_bytes(BODY[:-4])

    with pytest.raises(RawStoreError, match="SHA-256 name"):
        store.store(_capture())


def test_metadata_sidecar_never_contains_the_api_key(store: RawResponseStore) -> None:
    """Request parameters a key-shaped value reaches get rejected, not persisted.

    The key is a header, so it should never be a request parameter -- but if a
    value shaped like a credential ever appeared, it must be refused at the store
    boundary rather than written into a file that lives forever.
    """
    fake_key = "Bearer sk-live-" + secrets.token_hex(16)
    with pytest.raises(RawStoreError, match="request parameter"):
        store.store(_capture(request_parameters={"ticker": "AAPL", "apiKey": fake_key}))


def test_metadata_sidecar_is_written_alongside_the_artifact(store: RawResponseStore) -> None:
    artifact = store.store(_capture())
    metadata = store.metadata(artifact.relative_path)
    assert metadata["sha256"] == artifact.sha256
    assert metadata["byte_length"] == artifact.byte_length
    assert metadata["provider"] == "massive"


def test_metadata_for_a_missing_artifact_is_an_error(store: RawResponseStore) -> None:
    with pytest.raises(RawStoreError, match="no metadata sidecar"):
        store.metadata("massive/daily-bars/AAPL" + "/" + ("ab" * 32) + ".json")


def test_differing_body_for_same_key_gets_its_own_file(store: RawResponseStore) -> None:
    """A changed response must not overwrite the earlier evidence.

    Content addressing is what prevents this: a different body is a different
    digest, so the earlier capture remains readable.
    """
    first = store.store(_capture(body=b'{"c":1}'))
    second = store.store(_capture(body=b'{"c":2}'))

    assert first.relative_path != second.relative_path
    assert (store.root / first.relative_path).read_bytes() == b'{"c":1}'
    assert (store.root / second.relative_path).read_bytes() == b'{"c":2}'


def test_read_returns_the_exact_bytes(store: RawResponseStore) -> None:
    artifact = store.store(_capture())
    assert store.read(artifact.relative_path) == BODY


# ── path safety ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "key",
    [
        "../../etc/passwd",
        "..",
        "AAPL/../../../escape",
        "AAPL/subdir",
        ".",
    ],
)
def test_unsafe_keys_are_refused(store: RawResponseStore, key: str) -> None:
    """A key segment must not be able to address anything outside the store.

    The ticker reaches this code from a provider response, so it is untrusted
    input; ``../`` in it must be rejected rather than normalised.
    """
    with pytest.raises(RawStoreError):
        store.store(_capture(request_parameters={"ticker": key}))


def test_read_refuses_to_escape_the_root(store: RawResponseStore) -> None:
    """A path from the database must be re-validated on the way back in.

    The relative path round-trips through the database, so a tampered row must not
    become an arbitrary file read.
    """
    with pytest.raises(RawStoreError):
        store.read("../../../etc/passwd")


def test_store_root_is_created(store: RawResponseStore) -> None:
    store.store(_capture())
    assert store.root.is_dir()


# ── no partial files ────────────────────────────────────────────────────────


def test_write_is_atomic_and_leaves_no_temporary_files(store: RawResponseStore) -> None:
    """A crash mid-write must not leave a truncated artifact that looks valid.

    The write goes to a temporary file and is renamed into place, so a reader sees
    either the whole file or no file.
    """
    store.store(_capture())

    files = [p for p in store.root.rglob("*") if p.is_file()]
    assert files, "expected the artifact to exist"
    # A leftover .tmp is the signature of a non-atomic write.
    assert not [p for p in files if p.suffix in {".tmp", ".partial"}]


def test_no_temporary_files_remain_after_a_failed_write(
    store: RawResponseStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure during the write must not leak its temporary file.

    Otherwise a long-running ingestion job accumulates debris in the evidence
    directory, which is the one place nobody wants unexplained files.
    """

    def exploding_replace(self: Path, target: Path) -> Path:
        message = "disk full"
        raise OSError(message)

    monkeypatch.setattr(Path, "replace", exploding_replace)
    with pytest.raises(RawStoreError, match="disk full"):
        store.store(_capture())

    leftovers = [p for p in store.root.rglob("*") if p.is_file()]
    assert leftovers == []


# ── locally-generated artifacts ─────────────────────────────────────────────


def test_write_bytes_names_its_own_dataset(store: RawResponseStore) -> None:
    """A manifest is not a provider response and must not pretend to be one.

    It gets an explicit dataset and no fabricated endpoint, because the file's
    purpose is to be authoritative about what it contains.
    """
    written = store.write_bytes(
        b'{"run_id":"x"}',
        provider="massive",
        dataset="daily-bars-manifest",
        key_segment="20260927T120000Z-abc",
    )
    assert written.relative_path.startswith("massive/daily-bars-manifest/")
    assert store.read(written.relative_path) == b'{"run_id":"x"}'


# ── bulk ────────────────────────────────────────────────────────────────────


def test_store_all_preserves_page_order(store: RawResponseStore) -> None:
    """Order is the provider's page order, which a reader needs to follow a range."""
    captures = [
        _capture(body=f'{{"page":{i}}}'.encode(), request_parameters={"ticker": f"P{i}"})
        for i in range(3)
    ]
    stored = store.store_all(captures)

    assert [s.capture.body for s in stored] == [c.body for c in captures]
    assert len({s.artifact.relative_path for s in stored}) == 3
