"""Step 12D: the liveness contract, what readiness never touches, probe logging, timeouts, guards.

Liveness answers only "can this process answer HTTP?"; readiness looks at PostgreSQL and the schema
revision (gating) and Redis (reported, non-gating) and never at a provider. These tests pin those
boundaries both at runtime (a dependency that explodes when touched) and structurally (what the
modules are allowed to import), so a convenient future edit cannot quietly cross them.
"""

from __future__ import annotations

import ast
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from pydantic import ValidationError
from redis.exceptions import ConnectionError as RedisConnectionError

from quantforge.api.v1 import ready as ready_module
from quantforge.cache import redis as redis_module
from quantforge.cache.redis import ping_redis
from quantforge.core.config import KNOWN_DEVELOPMENT_DATABASE_LOGINS, Settings, get_settings
from quantforge.core.dependencies import DependencyName, DependencyState, SchemaState
from quantforge.db import schema as schema_module
from quantforge.db import session as session_module
from quantforge.db.schema import check_schema
from quantforge.db.session import (
    connect_arguments,
    get_engine,
    get_session_factory,
    probe_database,
)
from quantforge.main import create_app
from quantforge.providers.errors import ProviderConfigurationError
from quantforge.providers.sec.client import SecConfig
from tests.test_db import _StubConnection, _StubEngine  # the shared recording stubs
from tests.test_readiness import PROBE_OK  # a healthy database + exact schema

SRC = Path(session_module.__file__).resolve().parents[1]
UNREACHABLE_DSN = "postgresql+psycopg://quantforge:sup3rs3cret-do-not-leak@127.0.0.1:1/quantforge"

PRODUCTION: dict[str, Any] = {
    "environment": "production",
    "database_url": "postgresql+psycopg://qf_app:prod-secret-not-real@db.internal:5432/quantforge",
    "cors_origins": ["https://app.example.test"],
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


# ── liveness ─────────────────────────────────────────────────────────────────


def test_liveness_never_touches_a_datastore_a_migration_script_or_the_raw_root(
    monkeypatch: pytest.MonkeyPatch, make_settings: Callable[..., Settings], tmp_path: Path
) -> None:
    def explode(*_args: object, **_kwargs: object) -> None:
        message = "liveness touched a dependency"
        raise AssertionError(message)

    for module, name in (
        (session_module, "get_engine"),
        (session_module, "probe_database"),
        (session_module, "ping_database"),
        (redis_module, "get_redis"),
        (redis_module, "ping_redis"),
        (ready_module, "probe_database"),
        (ready_module, "ping_redis"),
        (schema_module, "expected_head"),
        (schema_module, "check_schema"),
    ):
        monkeypatch.setattr(module, name, explode)
    settings = make_settings(
        database_url=UNREACHABLE_DSN, ingestion_raw_root=tmp_path / "does" / "not" / "exist"
    )

    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/health").status_code == 200
    assert not (tmp_path / "does").exists(), "liveness must not create or read the raw root"


def test_the_liveness_module_imports_nothing_it_could_probe_with() -> None:
    allowed = {"__future__", "typing", "fastapi", "pydantic", "quantforge.core.config"}
    assert _imports(SRC / "api" / "v1" / "health.py") <= allowed


# ── readiness: what it never touches ─────────────────────────────────────────


def test_readiness_never_constructs_an_outbound_http_client(
    monkeypatch: pytest.MonkeyPatch, make_settings: Callable[..., Settings]
) -> None:
    """No provider can be asked: a request on a real network transport (the only way) fails the test."""

    def forbidden(*_args: object, **_kwargs: object) -> None:
        message = "readiness tried to reach an external service"
        raise AssertionError(message)

    # The test client has its own in-process transport, so only a REAL network transport trips this.
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", forbidden)
    settings = make_settings(database_url=UNREACHABLE_DSN, redis_url="redis://127.0.0.1:1/0")

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 503  # the unreachable local database, and nothing else was tried


@pytest.mark.parametrize(
    "relative",
    ["api/v1/ready.py", "db/session.py", "db/schema.py", "cache/redis.py", "core/dependencies.py"],
)
def test_readiness_code_cannot_reach_a_provider_or_the_ingestion_layer(relative: str) -> None:
    forbidden = ("quantforge.providers", "quantforge.ingestion", "httpx", "httpx2", "requests")
    imported = _imports(SRC / relative)
    assert not [name for name in imported if name.startswith(forbidden)], relative


def test_the_schema_checker_can_only_observe() -> None:
    """It reads the version table and the script directory; it has no way to migrate or stamp."""
    imported = _imports(SRC / "db" / "schema.py")
    assert "alembic.command" not in imported and "alembic.config" not in imported
    source = (SRC / "db" / "schema.py").read_text()
    for verb in (
        "upgrade(",
        "downgrade(",
        "stamp(",
        "INSERT",
        "UPDATE",
        "DELETE",
        "CREATE",
        "DROP",
    ):
        assert verb not in source, verb


def test_the_probe_issues_only_read_only_statements(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _StubEngine()
    monkeypatch.setattr(session_module, "get_engine", lambda *_: engine)

    probe_database(Settings(_env_file=None))

    statements = " ".join(engine.connection.statements).upper()
    assert "SELECT 1" in statements and "ALEMBIC_VERSION" in statements
    for verb in ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP", "TRUNCATE"):
        assert verb not in statements


# ── probe logging ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _probe_loggers_enabled() -> None:
    """Alembic's env.py runs ``logging.fileConfig``, which DISABLES every existing logger for the rest
    of the process once an integration test has migrated; re-enable the ones under test."""
    for name in ("quantforge.db.session", "quantforge.cache.redis"):
        logging.getLogger(name).disabled = False


def _records(caplog: pytest.LogCaptureFixture, logger: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == logger]


def test_an_unreachable_database_is_one_concise_line_without_a_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    probe = probe_database(Settings(_env_file=None, database_url=UNREACHABLE_DSN))

    assert probe.database.state is DependencyState.UNAVAILABLE
    (record,) = _records(caplog, "quantforge.db.session")
    assert record.levelno == logging.WARNING
    assert record.exc_info is None, "an expected outage must not print a traceback"
    text = record.getMessage()
    assert "OperationalError" in text
    for leaked in ("sup3rs3cret", "127.0.0.1", "postgresql", "quantforge:"):
        assert leaked not in text


class _Raises:
    def __init__(self, error: BaseException) -> None:
        self._error = error

    def ping(self) -> object:
        raise self._error


def test_a_down_redis_is_one_concise_line_without_a_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    check = ping_redis(
        client=_Raises(RedisConnectionError("Error 111 connecting to secret-host:6379"))
    )

    assert check.state is DependencyState.UNAVAILABLE and check.detail == "ConnectionError"
    (record,) = _records(caplog, "quantforge.cache.redis")
    assert record.exc_info is None
    assert "secret-host" not in record.getMessage()


@pytest.mark.parametrize(
    "message",
    [
        "auth failed password='Sup3rS3cret pw' for host",
        'auth failed password="Sup3rS3cret pw" for host',
        '{"api_key": "Sup3rS3cretKey99"}',
        "client_secret=Sup3rS3cretKey99 rejected",
    ],
)
def test_an_expected_outage_log_never_carries_the_error_text_whatever_it_contains(
    caplog: pytest.LogCaptureFixture, message: str
) -> None:
    caplog.set_level(logging.DEBUG)
    check = ping_redis(client=_Raises(RedisConnectionError(message)))

    assert check.detail == "ConnectionError"
    (record,) = _records(caplog, "quantforge.cache.redis")
    assert record.exc_info is None
    assert "Sup3rS3cret" not in record.getMessage()


def test_an_unexpected_programming_error_keeps_its_traceback(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real defects are not hidden: a non-connectivity exception is logged with its traceback."""
    caplog.set_level(logging.DEBUG)
    check = ping_redis(client=_Raises(ZeroDivisionError("bug")))
    assert check.state is DependencyState.UNAVAILABLE
    (redis_record,) = _records(caplog, "quantforge.cache.redis")
    assert redis_record.exc_info is not None

    def broken(*_args: object) -> None:
        message = "bug in the engine wiring"
        raise ValueError(message)

    monkeypatch.setattr(session_module, "get_engine", broken)
    probe = probe_database(Settings(_env_file=None))
    assert probe.database.state is DependencyState.UNAVAILABLE
    (db_record,) = _records(caplog, "quantforge.db.session")
    assert db_record.exc_info is not None


def test_the_readiness_docstring_makes_no_claim_the_code_does_not_keep() -> None:
    """The old docstring promised redaction that was never implemented; now it only promises class names."""
    doc = session_module.probe_database.__doc__ or ""
    assert "redact" not in doc.lower()
    assert "CLASS name" in doc


# ── redis: not configured ────────────────────────────────────────────────────


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_redis_url_means_not_configured_and_is_not_an_outage(blank: str) -> None:
    settings = Settings(_env_file=None, redis_url=blank)

    assert settings.redis_url is None
    check = ping_redis(settings)
    assert check.state is DependencyState.NOT_CONFIGURED
    assert check.name is DependencyName.REDIS
    assert check.is_ok is False and check.detail is None
    with pytest.raises(RuntimeError, match="not configured"):
        redis_module.get_redis(settings)


# ── database timeouts ────────────────────────────────────────────────────────


def test_timeouts_are_wired_into_the_engine_as_driver_arguments(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    real = sa.create_engine

    def spy(url: str, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return real(url, **kwargs)

    monkeypatch.setattr("quantforge.db.session.create_engine", spy)
    get_engine(make_settings(db_connect_timeout_seconds=7, db_statement_timeout_seconds=90))

    assert captured["connect_args"] == {
        "connect_timeout": 7,
        "options": "-c statement_timeout=90000",
    }
    assert captured["pool_pre_ping"] is True


def test_the_defaults_are_conservative_and_bounded(settings: Settings) -> None:
    assert (settings.db_connect_timeout_seconds, settings.db_statement_timeout_seconds) == (5, 60)
    assert connect_arguments(settings) == {
        "connect_timeout": 5,
        "options": "-c statement_timeout=60000",
    }


def test_the_engine_is_rebuilt_when_a_timeout_changes(
    make_settings: Callable[..., Settings],
) -> None:
    first = get_engine(make_settings(db_statement_timeout_seconds=30))
    same = get_engine(make_settings(db_statement_timeout_seconds=30))
    other = get_engine(make_settings(db_statement_timeout_seconds=45))

    assert first is same and other is not first


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("db_connect_timeout_seconds", 0),
        ("db_connect_timeout_seconds", -1),
        ("db_connect_timeout_seconds", 61),
        ("db_statement_timeout_seconds", 0),
        ("db_statement_timeout_seconds", -5),
        ("db_statement_timeout_seconds", 3601),
    ],
)
def test_timeouts_must_be_positive_and_bounded(
    make_settings: Callable[..., Settings], field: str, value: int
) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{field: value})


def test_timeouts_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QF_DB_CONNECT_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("QF_DB_STATEMENT_TIMEOUT_SECONDS", "120")
    resolved = Settings(_env_file=None)
    assert (resolved.db_connect_timeout_seconds, resolved.db_statement_timeout_seconds) == (3, 120)


# ── production configuration guards ──────────────────────────────────────────


def test_development_defaults_stay_convenient_outside_production() -> None:
    for environment in ("local", "development", "staging"):
        resolved = Settings(_env_file=None, environment=environment)
        assert resolved.database_url.endswith("@localhost:5432/quantforge")
        assert resolved.cors_origins == ["http://localhost:3000"]


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+psycopg://quantforge:quantforge@localhost:5432/quantforge",
        "postgresql+psycopg://quantforge:quantforge@db.internal:5432/quantforge",
        "postgresql://quantforge:local-dev-only-not-a-secret@db.internal/quantforge",
    ],
)
def test_production_refuses_a_development_database_login(database_url: str) -> None:
    with pytest.raises(ValidationError, match="development database login") as raised:
        Settings(_env_file=None, **{**PRODUCTION, "database_url": database_url})
    assert "local-dev-only-not-a-secret" not in str(raised.value), "the value is never echoed"


def test_production_refuses_the_untouched_default_database() -> None:
    with pytest.raises(ValidationError, match="development database login"):
        Settings(_env_file=None, environment="production", cors_origins=["https://a.test"])


def test_production_accepts_an_explicit_database() -> None:
    assert Settings(_env_file=None, **PRODUCTION).environment == "production"


def test_the_known_development_logins_are_exactly_the_documented_ones() -> None:
    assert {
        ("quantforge", "quantforge"),
        ("quantforge", "local-dev-only-not-a-secret"),
    } == KNOWN_DEVELOPMENT_DATABASE_LOGINS


@pytest.mark.parametrize("origins", [None, [], ["*"], ["https://a.test", "*"]])
def test_production_requires_explicit_non_wildcard_cors_origins(origins: Any) -> None:
    kwargs = {key: value for key, value in PRODUCTION.items() if key != "cors_origins"}
    if origins is not None:
        kwargs["cors_origins"] = origins
    with pytest.raises(ValidationError, match="QF_CORS_ORIGINS"):
        Settings(_env_file=None, **kwargs)


def test_production_cors_origin_from_the_environment_counts_as_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QF_CORS_ORIGINS", "https://app.example.test")
    kwargs = {key: value for key, value in PRODUCTION.items() if key != "cors_origins"}
    assert Settings(_env_file=None, **kwargs).cors_origins == ["https://app.example.test"]


def test_serving_requires_no_provider_key_no_sec_identity_and_no_redis() -> None:
    """Only what serves persisted data is required; ingestion-side settings are checked where used."""
    resolved = Settings(_env_file=None, **PRODUCTION, redis_url="")
    assert resolved.massive_api_key is None and resolved.finnhub_api_key is None
    assert resolved.sec_user_agent is None and resolved.redis_url is None
    with TestClient(create_app(resolved)) as client:
        assert client.get("/health").status_code == 200


def test_the_sec_identity_is_required_only_when_a_sec_request_is_made() -> None:
    config = SecConfig(user_agent=None, base_url="https://data.sec.gov")
    with pytest.raises(ProviderConfigurationError, match="QF_SEC_USER_AGENT"):
        config.require_user_agent()


def test_the_sec_identity_and_provider_keys_never_appear_in_readiness(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(
        sec_user_agent="QuantForge ops private.person@example.org",
        massive_api_key="massive-key-do-not-leak",
        finnhub_api_key="finnhub-key-do-not-leak",
        database_url=UNREACHABLE_DSN,
    )
    with TestClient(create_app(settings)) as client:
        body = client.get("/api/v1/health/ready").text + client.get("/health").text
    for secret in ("private.person", "massive-key", "finnhub-key", "sup3rs3cret"):
        assert secret not in body


# ── Step 12D review fixes ────────────────────────────────────────────────────


def test_a_malformed_redis_url_is_reported_unavailable_and_never_a_500(
    make_settings: Callable[..., Settings],
) -> None:
    """Redis must not be able to break readiness, not even with a URL the client refuses to parse."""
    settings = make_settings(redis_url="notaurl")
    application = create_app(settings)
    application.dependency_overrides[ready_module.check_database] = lambda: PROBE_OK
    application.dependency_overrides[get_settings] = lambda: settings
    with TestClient(application) as client:
        response = client.get("/api/v1/health/ready")

    assert response.status_code == 200 and response.json()["status"] == "ready"
    assert response.json()["dependencies"]["redis"] == {"state": "unavailable", "gating": False}
    assert response.json()["detail"] == {"redis": "ValueError"}


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+psycopg://quantforge:quantf%6Frge@db.internal/q",  # percent-encoded password
        "postgresql+psycopg://%71uantforge:quantforge@db.internal/q",  # percent-encoded role
        "postgresql+psycopg://quantforge@db.internal/q",  # the dev role, password from the environment
        "postgresql+psycopg://quantforge:@db.internal/q",  # empty password
        "postgresql+psycopg://quantforge@db.internal/q?password=quantforge",  # password as a parameter
        "postgresql://quantforge:local-dev-only-not-a-secret@db.internal/q",
    ],
)
def test_production_refuses_every_spelling_of_the_development_login(database_url: str) -> None:
    with pytest.raises(ValidationError, match="development database login") as raised:
        Settings(_env_file=None, **{**PRODUCTION, "database_url": database_url})
    for fragment in ("quantf%6Frge", "db.internal", "local-dev-only-not-a-secret"):
        assert fragment not in str(raised.value)


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql+psycopg://qf_app:prod-secret-not-real@db.internal/q",
        "postgresql+psycopg://qf_app@db.internal/q",  # another role: its password may come from PGPASSWORD
        "postgresql+psycopg://quantforge:a-real-production-password@db.internal/q",
    ],
)
def test_production_accepts_a_real_login(database_url: str) -> None:
    resolved = Settings(_env_file=None, **{**PRODUCTION, "database_url": database_url})
    assert resolved.environment == "production"


def test_a_malformed_database_url_fails_production_startup_without_echoing_it() -> None:
    with pytest.raises(ValidationError, match="not a valid database URL") as raised:
        Settings(_env_file=None, **{**PRODUCTION, "database_url": "not a url sup3rs3cret"})
    assert "sup3rs3cret" not in str(raised.value)


def test_options_and_connect_timeout_already_in_the_url_are_respected(
    make_settings: Callable[..., Settings],
) -> None:
    url = "postgresql+psycopg://u:p@h/db?connect_timeout=30&options=-c%20search_path%3Dfoo&sslmode=require"
    arguments = connect_arguments(make_settings(database_url=url))

    assert "connect_timeout" not in arguments, "the operator's explicit URL value stands"
    assert arguments["options"] == "-c search_path=foo -c statement_timeout=60000"


def test_a_statement_timeout_already_in_the_url_is_not_overridden(
    make_settings: Callable[..., Settings],
) -> None:
    url = "postgresql+psycopg://u:p@h/db?options=-c%20statement_timeout%3D5000"
    assert "options" not in connect_arguments(make_settings(database_url=url))


def test_the_session_factory_follows_the_engine_when_the_limits_change(
    make_settings: Callable[..., Settings],
) -> None:
    first = get_session_factory(make_settings(db_statement_timeout_seconds=30))
    same = get_session_factory(make_settings(db_statement_timeout_seconds=30))
    changed = get_session_factory(make_settings(db_statement_timeout_seconds=45))

    assert first is same and changed is not first
    assert changed.kw["bind"] is get_engine(make_settings(db_statement_timeout_seconds=45))


def test_waiting_for_a_pooled_connection_is_bounded_by_the_connect_timeout(
    make_settings: Callable[..., Settings],
) -> None:
    assert get_engine(make_settings(db_connect_timeout_seconds=7)).pool.timeout() == 7  # type: ignore[attr-defined]


def test_a_revision_with_a_trailing_newline_is_not_echoed() -> None:
    check = check_schema(_StubConnection(["0013\n"]))  # type: ignore[arg-type]
    assert check.state is SchemaState.MISMATCH and check.current is None
