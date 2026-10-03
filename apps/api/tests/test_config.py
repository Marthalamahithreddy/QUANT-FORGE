"""Configuration and settings-parsing tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from quantforge.core.config import CORS_ALLOW_HEADERS, CORS_ALLOW_METHODS, Settings


def test_local_development_defaults(settings: Settings) -> None:
    assert settings.environment == "local"
    assert settings.debug is False
    assert settings.app_name == "quantforge-api"
    assert settings.api_v1_prefix == "/api/v1"
    assert settings.cors_origins == ["http://localhost:3000"]
    assert settings.cors_allow_credentials is True
    assert settings.log_level == "INFO"


def test_default_datastore_urls_are_loopback_placeholders(settings: Settings) -> None:
    """Defaults must be local placeholders, never real credentials."""
    assert settings.database_url == (
        "postgresql+psycopg://quantforge:quantforge@localhost:5432/quantforge"
    )
    assert settings.redis_url == "redis://localhost:6379/0"
    assert "@localhost" in settings.database_url


def test_environment_variables_use_qf_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QF_ENVIRONMENT", "staging")
    monkeypatch.setenv("QF_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("QF_DATABASE_URL", "postgresql+psycopg://u:p@db.internal:5432/qf")
    monkeypatch.setenv("QF_REDIS_URL", "redis://cache.internal:6379/1")

    resolved = Settings(_env_file=None)

    assert resolved.environment == "staging"
    assert resolved.log_level == "DEBUG"
    assert resolved.database_url == "postgresql+psycopg://u:p@db.internal:5432/qf"
    assert resolved.redis_url == "redis://cache.internal:6379/1"


def test_unprefixed_variables_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert Settings(_env_file=None).environment == "local"


def test_invalid_environment_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QF_ENVIRONMENT", "not-a-real-environment")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_invalid_log_level_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QF_LOG_LEVEL", "TRACE")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


# ── CORS parsing ────────────────────────────────────────────────────────────


def test_cors_origins_parsed_from_comma_separated_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "QF_CORS_ORIGINS",
        "http://localhost:3000, https://app.quantforge.test ,http://localhost:4321",
    )

    assert Settings(_env_file=None).cors_origins == [
        "http://localhost:3000",
        "https://app.quantforge.test",
        "http://localhost:4321",
    ]


def test_cors_origins_parsed_from_json_array(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QF_CORS_ORIGINS", '["https://a.test", "https://b.test"]')

    assert Settings(_env_file=None).cors_origins == ["https://a.test", "https://b.test"]


def test_cors_origins_reject_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QF_CORS_ORIGINS", '["https://a.test", ')

    with pytest.raises(ValidationError, match="not valid JSON"):
        Settings(_env_file=None)


@pytest.mark.parametrize("raw", ["", "   "])
def test_blank_cors_origins_become_empty_list(
    make_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    monkeypatch.setenv("QF_CORS_ORIGINS", raw)
    assert Settings(_env_file=None).cors_origins == []


def test_cors_origins_accept_sequence(make_settings: Callable[..., Settings]) -> None:
    resolved = make_settings(cors_origins=["https://a.test", "https://b.test"])
    assert resolved.cors_origins == ["https://a.test", "https://b.test"]


def test_cors_origins_trailing_comma_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QF_CORS_ORIGINS", "https://a.test,,")
    assert Settings(_env_file=None).cors_origins == ["https://a.test"]


# ── Wildcard rejection ──────────────────────────────────────────────────────


def test_wildcard_rejected_when_credentials_enabled(
    make_settings: Callable[..., Settings],
) -> None:
    with pytest.raises(ValidationError, match="must not contain"):
        make_settings(cors_origins=["*"])


def test_wildcard_rejected_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QF_CORS_ORIGINS", "*")

    with pytest.raises(ValidationError, match="must not contain"):
        Settings(_env_file=None)


def test_wildcard_rejected_when_embedded_in_a_list(
    make_settings: Callable[..., Settings],
) -> None:
    with pytest.raises(ValidationError, match="must not contain"):
        make_settings(cors_origins=["https://a.test", "*"])


def test_wildcard_allowed_when_credentials_disabled(
    make_settings: Callable[..., Settings],
) -> None:
    resolved = make_settings(cors_origins=["*"], cors_allow_credentials=False)

    assert resolved.cors_origins == ["*"]
    assert resolved.cors_allow_credentials is False


def test_similar_origin_is_not_treated_as_wildcard(
    make_settings: Callable[..., Settings],
) -> None:
    """A subdomain wildcard is a legitimate explicit origin, not ``*``."""
    resolved = make_settings(cors_origins=["https://*.quantforge.test"])
    assert resolved.cors_origins == ["https://*.quantforge.test"]


# ── API prefix normalisation ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/api/v1", "/api/v1"),
        ("api/v1", "/api/v1"),
        ("/api/v1/", "/api/v1"),
        ("  /api/v1/  ", "/api/v1"),
        ("/custom", "/custom"),
    ],
)
def test_api_v1_prefix_is_normalised(
    make_settings: Callable[..., Settings], raw: str, expected: str
) -> None:
    assert make_settings(api_v1_prefix=raw).api_v1_prefix == expected


# ── CORS method/header policy constants ─────────────────────────────────────


def test_cors_policy_constants_are_explicit() -> None:
    assert CORS_ALLOW_METHODS == ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
    assert "Content-Type" in CORS_ALLOW_HEADERS
    assert "Authorization" in CORS_ALLOW_HEADERS
    assert "*" not in CORS_ALLOW_METHODS
    assert "*" not in CORS_ALLOW_HEADERS


def test_default_origins_are_loopback_only(settings: Settings) -> None:
    assert all(origin.startswith("http://localhost") for origin in settings.cors_origins)


def test_ingestion_raw_root_default_is_an_absolute_repository_path(settings: Settings) -> None:
    """The default must not be a bare relative path.

    A relative ``Path("data/raw")`` resolves against whatever the process's
    current working directory happens to be when it is later opened, not
    against a fixed project location -- see
    ``test_ingestion_raw_root_default_is_independent_of_cwd`` for why that is
    a real defect, not a hypothetical one.
    """
    assert settings.ingestion_raw_root.is_absolute()
    assert settings.ingestion_raw_root.name == "raw"
    assert settings.ingestion_raw_root.parent.name == "data"


def test_ingestion_raw_root_default_is_independent_of_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The default raw-store root must resolve the same way regardless of CWD.

    Both of the ways ingestion is actually invoked are expected to behave
    identically: ``cd apps/api && uv run quantforge ...`` (the README's own
    quick start) and running from the repository root. Before this default
    was anchored to the repository root rather than left as a relative
    ``Path("data/raw")``, the first of those two put raw artifacts under
    ``apps/api/data/raw/`` -- a location the root ``.gitignore``'s
    ``data/raw/*`` entry does not cover, since a pattern containing a
    non-trailing slash is anchored to the directory the ``.gitignore`` file
    itself lives in. This test proves the fix by actually changing the
    process's working directory to an unrelated location and confirming the
    resolved default does not move.
    """
    from_repo_root = Settings(_env_file=None).ingestion_raw_root

    monkeypatch.chdir(tmp_path)
    from_elsewhere = Settings(_env_file=None).ingestion_raw_root

    assert from_elsewhere == from_repo_root
    assert from_elsewhere.is_absolute()


def test_ingestion_raw_root_default_points_at_the_repository_data_directory(
    settings: Settings,
) -> None:
    """The resolved default must actually be ``<repo root>/data/raw``.

    Walking up from this test file (``apps/api/tests/test_config.py``) three
    levels reaches the repository root that owns ``data/`` and the root
    ``.gitignore``. Computed independently of ``quantforge.core.config``'s
    own anchor, so a bug in that anchor's arithmetic cannot cancel out
    against this assertion.
    """
    repo_root = Path(__file__).resolve().parents[3]
    assert settings.ingestion_raw_root == repo_root / "data" / "raw"
    assert (repo_root / "data").is_dir()


def test_ingestion_raw_root_is_overridable_via_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    override = tmp_path / "custom-raw-root"
    monkeypatch.setenv("QF_INGESTION_RAW_ROOT", str(override))

    resolved = Settings(_env_file=None)

    assert resolved.ingestion_raw_root == override


# ── SEC identity (never defaulted, never hard-coded) ─────────────────────────


def test_the_sec_identity_is_unset_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QF_SEC_USER_AGENT", raising=False)

    assert Settings(_env_file=None).sec_user_agent is None


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_sec_identity_is_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    monkeypatch.setenv("QF_SEC_USER_AGENT", blank)

    assert Settings(_env_file=None).sec_user_agent is None


def test_a_configured_sec_identity_and_defaults_are_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QF_SEC_USER_AGENT", "MyApp admin@example.com")

    settings = Settings(_env_file=None)

    assert settings.sec_user_agent is not None
    assert settings.sec_user_agent.get_secret_value() == "MyApp admin@example.com"
    assert "admin@example.com" not in repr(settings), "the contact is a secret"
    assert settings.sec_base_url == "https://data.sec.gov"
    assert settings.sec_min_request_interval_seconds <= 0.5  # well under the 10 req/s limit


def test_no_personal_contact_is_hard_coded_in_the_sec_adapter_source() -> None:
    from pathlib import Path  # noqa: PLC0415

    root = Path(__file__).resolve().parents[1] / "src" / "quantforge"
    for path in (*root.glob("providers/sec/*.py"), root / "core" / "config.py"):
        text = path.read_text()
        assert "gmail.com" not in text and "mahith" not in text.lower(), path
