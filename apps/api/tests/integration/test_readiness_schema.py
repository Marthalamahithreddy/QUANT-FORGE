"""Step 12D: readiness against a REAL PostgreSQL whose Alembic revision is behind, ahead, missing, or exact.

A scratch database per case holds only an ``alembic_version`` table, so the probe's verdict depends on
the revision and nothing else. The migrated test database proves the other direction: after the real
``alembic upgrade head`` the binary's expected head and the database's revision are the same value, so
the derivation cannot drift from what Alembic actually writes.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from tests.integration.conftest import (
    _create_database,
    _drop_database,
    _upgrade_test_database,
)

from quantforge.api.v1.ready import check_redis
from quantforge.core.config import Settings, get_settings
from quantforge.core.dependencies import DependencyCheck, DependencyName, SchemaState
from quantforge.db.schema import expected_head
from quantforge.db.session import dispose_engines, get_engine, probe_database
from quantforge.main import create_app

pytestmark = pytest.mark.integration

PATH = "/api/v1/health/ready"
SCRATCH = "qf_readiness_scratch"
MIGRATED = "qf_readiness_migrated"
HEAD = expected_head()


def _settings(engine: Engine, database: str | None = None, **overrides: Any) -> Settings:
    url = engine.url if database is None else engine.url.set(database=database)
    return Settings(
        _env_file=None, database_url=url.render_as_string(hide_password=False), **overrides
    )


@pytest.fixture
def scratch(
    integration_engine: Engine, postgres_admin_url: str
) -> Iterator[Callable[..., Settings]]:
    """Build a scratch database holding the given ``alembic_version`` rows (or no table at all)."""
    _drop_database(postgres_admin_url, SCRATCH)

    def build(rows: list[str] | None, **overrides: Any) -> Settings:
        dispose_engines()
        _drop_database(postgres_admin_url, SCRATCH)
        _create_database(postgres_admin_url, SCRATCH)
        settings = _settings(integration_engine, SCRATCH, **overrides)
        if rows is not None:
            engine = sa.create_engine(settings.database_url, poolclass=sa.pool.NullPool)
            try:
                with engine.begin() as connection:
                    connection.execute(sa.text("CREATE TABLE alembic_version (version_num text)"))
                    for row in rows:
                        connection.execute(
                            sa.text("INSERT INTO alembic_version VALUES (:v)"), {"v": row}
                        )
            finally:
                engine.dispose()
        return settings

    yield build
    dispose_engines()
    _drop_database(postgres_admin_url, SCRATCH)


def _ready(settings: Settings, redis: DependencyCheck | None = None) -> tuple[int, dict[str, Any]]:
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings
    application.dependency_overrides[check_redis] = lambda: (
        redis or DependencyCheck.ok(DependencyName.REDIS)
    )
    with TestClient(application) as client:
        response = client.get(PATH)
    return response.status_code, response.json()


@pytest.fixture(scope="module")
def migrated(integration_engine: Engine, postgres_admin_url: str) -> Iterator[Settings]:
    """A database brought to head by the REAL Alembic run, in its own database.

    Not the shared test database: other integration tests TRUNCATE every table there, including
    ``alembic_version``, so its stamp is not reliable between tests.
    """
    _drop_database(postgres_admin_url, MIGRATED)
    _create_database(postgres_admin_url, MIGRATED)
    settings = _settings(integration_engine, MIGRATED)
    _upgrade_test_database(settings.database_url)
    yield settings
    dispose_engines()
    _drop_database(postgres_admin_url, MIGRATED)


def test_the_expected_head_is_the_revision_the_real_migration_writes(migrated: Settings) -> None:
    """Derivation from the migration scripts equals what ``alembic upgrade head`` actually stamped."""
    engine = sa.create_engine(migrated.database_url, poolclass=sa.pool.NullPool)
    try:
        with engine.connect() as connection:
            stamped = connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar()
    finally:
        engine.dispose()
    assert HEAD is not None and stamped == HEAD

    code, payload = _ready(migrated)
    assert code == 200 and payload["status"] == "ready"
    assert payload["schema"] == {
        "state": "compatible",
        "expected": HEAD,
        "current": HEAD,
        "gating": True,
    }


def test_an_exact_match_is_ready(scratch: Callable[..., Settings]) -> None:
    assert HEAD is not None
    code, payload = _ready(scratch([HEAD]))
    assert (code, payload["status"], payload["schema"]["state"]) == (200, "ready", "compatible")


@pytest.mark.parametrize("current", ["0013", "0001", "0000"])
def test_a_database_behind_the_binary_is_not_ready(
    scratch: Callable[..., Settings], current: str
) -> None:
    code, payload = _ready(scratch([current]))

    assert code == 503 and payload["status"] == "not_ready"
    assert payload["dependencies"]["database"]["state"] == "ok"
    assert payload["schema"] == {
        "state": "mismatch",
        "expected": HEAD,
        "current": current,
        "gating": True,
    }


@pytest.mark.parametrize("current", ["0015", "9999", "future_revision"])
def test_a_database_ahead_of_the_binary_is_not_ready(
    scratch: Callable[..., Settings], current: str
) -> None:
    """Forward compatibility is never assumed."""
    code, payload = _ready(scratch([current]))
    assert code == 503
    assert payload["schema"]["state"] == "mismatch" and payload["schema"]["current"] == current


def test_a_database_with_no_version_table_is_not_ready_and_says_so_generically(
    scratch: Callable[..., Settings],
) -> None:
    code, payload = _ready(scratch(None))

    assert code == 503
    assert payload["dependencies"]["database"]["state"] == "ok"
    assert payload["schema"] == {
        "state": "unknown",
        "expected": HEAD,
        "current": None,
        "gating": True,
    }
    assert "alembic_version" not in str(payload) and "relation" not in str(payload)


@pytest.mark.parametrize("rows", [[], ["0014", "0013"]])
def test_no_revision_or_several_revisions_cannot_prove_compatibility(
    scratch: Callable[..., Settings], rows: list[str]
) -> None:
    assert HEAD is not None
    code, payload = _ready(scratch(rows))
    assert code == 503 and payload["schema"]["state"] == "unknown"
    assert payload["schema"]["current"] is None


def test_an_odd_revision_string_is_not_echoed(scratch: Callable[..., Settings]) -> None:
    code, payload = _ready(scratch(["/Users/me/secret path; DROP TABLE x"]))
    assert code == 503 and payload["schema"]["state"] == "mismatch"
    assert payload["schema"]["current"] is None
    assert "Users" not in str(payload)


def test_redis_down_does_not_change_a_database_that_is_otherwise_ready(
    scratch: Callable[..., Settings],
) -> None:
    assert HEAD is not None
    down = DependencyCheck.failure(DependencyName.REDIS, ConnectionError("refused"))
    code, payload = _ready(scratch([HEAD]), redis=down)
    assert code == 200 and payload["status"] == "ready"
    assert payload["dependencies"]["redis"] == {"state": "unavailable", "gating": False}


def test_the_probe_observes_and_never_repairs(
    scratch: Callable[..., Settings], integration_engine: Engine
) -> None:
    """Not ready stays not ready: no stamp, no upgrade, no table created by the probe."""
    settings = scratch(["0013"])
    for _ in range(3):
        _ready(settings)
    engine = sa.create_engine(settings.database_url, poolclass=sa.pool.NullPool)
    try:
        with engine.connect() as connection:
            versions = connection.execute(sa.text("SELECT version_num FROM alembic_version")).all()
            tables = sa.inspect(connection).get_table_names()
    finally:
        engine.dispose()
    assert [row[0] for row in versions] == ["0013"]
    assert tables == ["alembic_version"]


def test_the_probe_reports_a_compatible_schema_through_the_public_function(
    migrated: Settings,
) -> None:
    probe = probe_database(migrated)
    assert probe.database.is_ok and probe.schema.state is SchemaState.COMPATIBLE


def test_the_statement_timeout_is_enforced_by_the_server(integration_engine: Engine) -> None:
    """A hung query frees its connection: a 1 s cap stops a 5 s sleep long before it ends."""
    settings = _settings(integration_engine, db_statement_timeout_seconds=1)
    started = time.monotonic()
    with (
        pytest.raises(sa.exc.OperationalError) as raised,
        get_engine(settings).connect() as connection,
    ):
        connection.execute(sa.text("SELECT pg_sleep(5)"))
    assert time.monotonic() - started < 4
    assert "statement timeout" in str(raised.value.orig).lower()


def test_the_configured_statement_timeout_is_what_the_connection_reports(
    integration_engine: Engine,
) -> None:
    settings = _settings(integration_engine, db_statement_timeout_seconds=42)
    with get_engine(settings).connect() as connection:
        assert connection.execute(sa.text("SHOW statement_timeout")).scalar() == "42s"
