"""Streaming a local package into the raw store: content-addressed, verified, secret-free."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from quantforge.ingestion import raw_store
from quantforge.ingestion.raw_store import RawResponseStore, RawStoreError

PAYLOAD = b"PK\x03\x04" + b"quantforge" * 1000


def _write(tmp_path: Path, name: str = "2024q2.zip", payload: bytes = PAYLOAD) -> Path:
    path = tmp_path / "incoming" / name
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(payload)
    return path


def test_a_file_is_stored_under_its_own_sha256_with_a_sidecar(tmp_path: Path) -> None:
    store = RawResponseStore(tmp_path / "raw")
    artifact = store.store_file(
        _write(tmp_path), provider="sec-edgar", dataset="sec-fsds", key_segment="2024q2"
    )
    digest = hashlib.sha256(PAYLOAD).hexdigest()
    assert artifact.sha256 == digest
    assert artifact.byte_length == len(PAYLOAD)
    assert artifact.relative_path == f"sec-edgar/sec-fsds/2024q2/{digest}.zip"
    assert artifact.deduplicated is False
    assert store.path_of(artifact.relative_path).read_bytes() == PAYLOAD
    meta = json.loads(
        (tmp_path / "raw" / f"sec-edgar/sec-fsds/2024q2/{digest}.meta.json").read_text()
    )
    assert meta["sha256"] == digest
    assert meta["origin"] == "local_file"


def test_the_sidecar_never_records_the_local_path(tmp_path: Path) -> None:
    store = RawResponseStore(tmp_path / "raw")
    source = _write(tmp_path)
    artifact = store.store_file(
        source, provider="sec-edgar", dataset="sec-fsds", key_segment="2024q2"
    )
    sidecar = (tmp_path / "raw" / artifact.relative_path).with_suffix(".meta.json")
    text = sidecar.read_text()
    assert str(tmp_path) not in text
    assert "incoming" not in text


def test_an_identical_file_is_deduplicated_and_leaves_no_stray_temp_files(tmp_path: Path) -> None:
    store = RawResponseStore(tmp_path / "raw")
    first = store.store_file(
        _write(tmp_path), provider="sec-edgar", dataset="sec-fsds", key_segment="2024q2"
    )
    second = store.store_file(
        _write(tmp_path), provider="sec-edgar", dataset="sec-fsds", key_segment="2024q2"
    )
    assert second.deduplicated is True
    assert second.relative_path == first.relative_path
    leftovers = [
        p.name
        for p in (tmp_path / "raw/sec-edgar/sec-fsds/2024q2").iterdir()
        if p.name.endswith(".tmp")
    ]
    assert leftovers == []


def test_changed_bytes_under_the_same_key_are_a_second_artifact_never_an_overwrite(
    tmp_path: Path,
) -> None:
    store = RawResponseStore(tmp_path / "raw")
    old = store.store_file(
        _write(tmp_path), provider="sec-edgar", dataset="sec-fsds", key_segment="2024q2"
    )
    new = store.store_file(
        _write(tmp_path, payload=PAYLOAD + b"x"),
        provider="sec-edgar",
        dataset="sec-fsds",
        key_segment="2024q2",
    )
    assert old.relative_path != new.relative_path
    assert store.path_of(old.relative_path).read_bytes() == PAYLOAD  # the old evidence is intact
    assert store.path_of(new.relative_path).read_bytes() == PAYLOAD + b"x"


def test_a_tampered_stored_file_is_a_loud_failure(tmp_path: Path) -> None:
    store = RawResponseStore(tmp_path / "raw")
    artifact = store.store_file(
        _write(tmp_path), provider="sec-edgar", dataset="sec-fsds", key_segment="2024q2"
    )
    store.path_of(artifact.relative_path).write_bytes(b"tampered")
    with pytest.raises(RawStoreError, match="does not match its content-addressed"):
        store.store_file(
            _write(tmp_path), provider="sec-edgar", dataset="sec-fsds", key_segment="2024q2"
        )


def test_an_empty_file_and_an_oversized_file_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RawResponseStore(tmp_path / "raw")
    with pytest.raises(RawStoreError, match="empty"):
        store.store_file(
            _write(tmp_path, payload=b""),
            provider="sec-edgar",
            dataset="sec-fsds",
            key_segment="2024q2",
        )
    monkeypatch.setattr(raw_store, "MAX_PACKAGE_BYTES", 10)
    with pytest.raises(RawStoreError, match="package limit"):
        store.store_file(
            _write(tmp_path), provider="sec-edgar", dataset="sec-fsds", key_segment="2024q2"
        )
    leftovers = list((tmp_path / "raw").rglob("*.tmp"))
    assert leftovers == []


@pytest.mark.parametrize("key", ["../escape", "a/b", ".hidden", ""])
def test_unsafe_key_segments_are_rejected(tmp_path: Path, key: str) -> None:
    store = RawResponseStore(tmp_path / "raw")
    with pytest.raises(RawStoreError, match="unsafe"):
        store.store_file(
            _write(tmp_path), provider="sec-edgar", dataset="sec-fsds", key_segment=key
        )


def test_unknown_providers_and_suffixes_are_rejected(tmp_path: Path) -> None:
    store = RawResponseStore(tmp_path / "raw")
    with pytest.raises(RawStoreError, match="not an allowed"):
        store.store_file(
            _write(tmp_path), provider="evil", dataset="sec-fsds", key_segment="2024q2"
        )
    with pytest.raises(RawStoreError, match="suffix"):
        store.store_file(
            _write(tmp_path),
            provider="sec-edgar",
            dataset="sec-fsds",
            key_segment="2024q2",
            suffix="/x",
        )


def test_a_missing_source_file_is_a_store_error(tmp_path: Path) -> None:
    store = RawResponseStore(tmp_path / "raw")
    with pytest.raises(RawStoreError, match="cannot copy"):
        store.store_file(
            tmp_path / "nope.zip", provider="sec-edgar", dataset="sec-fsds", key_segment="2024q2"
        )
