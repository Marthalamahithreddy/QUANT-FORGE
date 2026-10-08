"""Step 12E: the release smoke's database fingerprint (scripts/validation/state_fingerprint.py).

Run against a REAL PostgreSQL scratch database, because what it relies on -- row digests, the
insert/update/delete write counters, and a READ ONLY snapshot -- is PostgreSQL behaviour that a mock
would only echo. The fingerprint must (1) be identical when nothing was written, (2) change when a row
is inserted, updated or deleted, (3) pin immutable evidence in full, and (4) itself be unable to write.
"""

from __future__ import annotations

import importlib.util
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from tests.integration.conftest import _create_database, _drop_database

from quantforge.core.config import get_settings

pytestmark = pytest.mark.integration

SCRATCH = "qf_fingerprint_scratch"
SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "validation" / "state_fingerprint.py"

_spec = importlib.util.spec_from_file_location("state_fingerprint", SCRIPT)
assert _spec is not None and _spec.loader is not None
state_fingerprint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(state_fingerprint)

SETUP = """
CREATE TABLE earnings_event (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), headline text NOT NULL);
CREATE TABLE research_observation_record (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), manifest_fingerprint text NOT NULL,
    result_fingerprint text NOT NULL, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE financial_fact_artifact (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), sha256 text NOT NULL,
    byte_length bigint NOT NULL, raw_artifact_path text NOT NULL);
CREATE TABLE ingestion_run (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), raw_content_sha256 text, raw_artifact_path text,
    raw_artifact_bytes integer, status text NOT NULL);
INSERT INTO earnings_event (headline) VALUES ('Q3 results'), ('Q4 results');
INSERT INTO research_observation_record (manifest_fingerprint, result_fingerprint)
    VALUES ('m1', 'r1'), ('m2', 'r2');
INSERT INTO financial_fact_artifact (sha256, byte_length, raw_artifact_path) VALUES ('aa', 10, 'a/b.zip');
INSERT INTO ingestion_run (raw_content_sha256, raw_artifact_path, raw_artifact_bytes, status)
    VALUES ('bb', 'r/x.json', 5, 'SUCCEEDED');
"""


@pytest.fixture
def engine(integration_engine: Engine, postgres_admin_url: str) -> Iterator[Engine]:
    _drop_database(postgres_admin_url, SCRATCH)
    _create_database(postgres_admin_url, SCRATCH)
    url = integration_engine.url.set(database=SCRATCH).render_as_string(hide_password=False)
    scratch = sa.create_engine(url, poolclass=sa.pool.NullPool)
    with scratch.begin() as connection:
        for statement in SETUP.strip().split(";\n"):
            if statement.strip():
                connection.execute(sa.text(statement))
    yield scratch
    scratch.dispose()
    _drop_database(postgres_admin_url, SCRATCH)


def snapshot(engine: Engine) -> dict[str, Any]:
    with engine.connect() as connection:
        out: dict[str, Any] = state_fingerprint.fingerprint(connection)
        connection.rollback()
    return out


def write(engine: Engine, statement: str) -> None:
    with engine.begin() as connection:
        connection.execute(sa.text(statement))


def eventually_different(engine: Engine, before: dict[str, Any], table: str, key: str) -> bool:
    """The cumulative write counters are flushed by PostgreSQL with a short delay: poll, bounded."""
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if snapshot(engine)[table][key] != before[table][key]:
            return True
        time.sleep(0.25)
    return False


def test_two_snapshots_with_no_write_between_them_are_identical(engine: Engine) -> None:
    first = snapshot(engine)
    time.sleep(1.2)  # past the statistics flush interval: still nothing to flush
    assert snapshot(engine) == first
    assert set(first) >= {
        "earnings_event",
        "research_observation_record",
        "financial_fact_artifact",
        "ingestion_run",
    }
    assert first["earnings_event"]["rows"] == 2


def test_a_read_only_workload_moves_neither_digests_nor_write_counters(engine: Engine) -> None:
    """What a smoke run is: only SELECTs against the stored data."""
    first = snapshot(engine)
    with engine.connect() as connection:
        for _ in range(20):
            connection.execute(sa.text("SELECT * FROM earnings_event")).all()
            connection.execute(sa.text("SELECT count(*) FROM research_observation_record")).scalar()
    time.sleep(1.2)
    assert snapshot(engine) == first


def test_an_insert_changes_rows_keys_and_the_write_counter(engine: Engine) -> None:
    before = snapshot(engine)
    write(engine, "INSERT INTO earnings_event (headline) VALUES ('injected')")
    after = snapshot(engine)
    assert after["earnings_event"]["rows"] == 3
    assert after["earnings_event"]["keys"] != before["earnings_event"]["keys"]
    assert eventually_different(engine, before, "earnings_event", "writes")


def test_an_update_that_keeps_the_row_count_changes_the_digest_and_the_counter(
    engine: Engine,
) -> None:
    """The failure the old four-total check could not see: same count, different content."""
    before = snapshot(engine)
    write(engine, "UPDATE earnings_event SET headline = 'tampered' WHERE headline = 'Q3 results'")
    after = snapshot(engine)
    assert after["earnings_event"]["rows"] == before["earnings_event"]["rows"]
    assert (after["earnings_event"]["head"], after["earnings_event"]["tail"]) != (
        before["earnings_event"]["head"],
        before["earnings_event"]["tail"],
    )
    assert eventually_different(engine, before, "earnings_event", "writes")


def test_a_delete_is_seen(engine: Engine) -> None:
    before = snapshot(engine)
    write(engine, "DELETE FROM earnings_event WHERE headline = 'Q4 results'")
    after = snapshot(engine)
    assert (
        after["earnings_event"]["rows"] == 1
        and after["earnings_event"]["keys"] != before["earnings_event"]["keys"]
    )


@pytest.mark.parametrize(
    ("table", "statement"),
    [
        (
            "research_observation_record",
            "UPDATE research_observation_record SET result_fingerprint = 'forged' WHERE manifest_fingerprint = 'm1'",
        ),
        ("financial_fact_artifact", "UPDATE financial_fact_artifact SET sha256 = 'cc'"),
        ("ingestion_run", "UPDATE ingestion_run SET raw_content_sha256 = 'dd'"),
    ],
)
def test_immutable_evidence_is_pinned_in_full(engine: Engine, table: str, statement: str) -> None:
    """Saved records, raw-artifact references and ingestion runs carry a digest of their evidence columns."""
    before = snapshot(engine)
    write(engine, statement)
    assert snapshot(engine)[table]["immutable"] != before[table]["immutable"]


def test_a_change_beyond_the_row_windows_is_seen_by_the_write_counter_only(engine: Engine) -> None:
    """The documented limit: the digests cover bounded windows; the counters cover the whole table."""
    write(
        engine,
        "INSERT INTO earnings_event (headline) SELECT 'bulk ' || g FROM generate_series(1, 400) g",
    )
    with engine.connect() as connection:
        middle = connection.execute(
            sa.text("SELECT id FROM earnings_event ORDER BY id OFFSET 200 LIMIT 1")
        ).scalar()
    time.sleep(1.2)
    before = snapshot(engine)
    write(engine, f"UPDATE earnings_event SET headline = 'quietly changed' WHERE id = '{middle}'")
    after = snapshot(engine)
    assert after["earnings_event"]["head"] == before["earnings_event"]["head"]
    assert after["earnings_event"]["tail"] == before["earnings_event"]["tail"]
    assert eventually_different(engine, before, "earnings_event", "writes")


def test_a_very_large_table_is_not_scanned_to_count_it(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Above the threshold the exact count is skipped; the write counters and digests still pin the table."""
    monkeypatch.setattr(state_fingerprint, "EXACT_COUNT_BELOW", 1)
    time.sleep(1.2)  # the planner's live-tuple estimate is a statistic: let it be flushed
    entry = snapshot(engine)["earnings_event"]
    assert entry["rows"] is None
    assert {"writes", "head", "tail", "keys"} <= set(entry)


def test_the_fingerprint_transaction_cannot_write(engine: Engine) -> None:
    with engine.connect() as connection:
        state_fingerprint.fingerprint(connection)
        with pytest.raises(sa.exc.InternalError, match="read-only transaction"):
            connection.execute(
                sa.text("INSERT INTO earnings_event (headline) VALUES ('not allowed')")
            )
        connection.rollback()
    assert snapshot(engine)["earnings_event"]["rows"] == 2


def test_the_script_names_a_failure_by_class_only_and_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(
        "QF_DATABASE_URL", "postgresql+psycopg://secretuser:secretpass@127.0.0.1:1/none"
    )
    monkeypatch.setenv("QF_DB_CONNECT_TIMEOUT_SECONDS", "1")
    get_settings.cache_clear()
    try:
        assert state_fingerprint.main() == 1
    finally:
        get_settings.cache_clear()
    captured = capsys.readouterr()
    assert captured.err.strip() == "state fingerprint failed: OperationalError"
    assert "secret" not in captured.err + captured.out
    assert captured.out == ""
