"""Application configuration.

All settings are read from ``QF_``-prefixed environment variables, with an
optional ``.env`` file for local development. Reading configuration performs no
I/O against external services, so importing this module is always safe.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["local", "development", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

#: Origin served by the Next.js dev server. Local development only.
DEFAULT_CORS_ORIGINS: tuple[str, ...] = ("http://localhost:3000",)

CORS_ALLOW_METHODS: tuple[str, ...] = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
CORS_ALLOW_HEADERS: tuple[str, ...] = (
    "Accept",
    "Authorization",
    "Content-Type",
    "X-Requested-With",
)

#: Placeholder loopback DSN. Not a real credential; override in every deployed
#: environment via ``QF_DATABASE_URL`` and keep real secrets out of version control.
DEFAULT_DATABASE_URL = "postgresql+psycopg://quantforge:quantforge@localhost:5432/quantforge"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"

#: Massive (formerly Polygon.io) REST base. Overridable so a test, a mirror or a
#: future regional host can be pointed at without code changes.
DEFAULT_MASSIVE_BASE_URL = "https://api.massive.com"

#: Finnhub REST base, for the earnings-calendar adapter. Massive's own
#: Benzinga earnings endpoint is entitlement-blocked (HTTP 403) under the
#: configured plan, so Finnhub is a second, earnings-only provider -- it does
#: not replace Massive for reference/daily-bar data.
DEFAULT_FINNHUB_BASE_URL = "https://finnhub.io/api/v1"
DEFAULT_SEC_BASE_URL = "https://data.sec.gov"

#: This file's own location: ``apps/api/src/quantforge/core/config.py``.
#: Climbing five parents reaches the repository root -- the directory
#: containing ``apps/``, ``data/`` and the root ``.gitignore``.
_REPO_ROOT = Path(__file__).resolve().parents[5]

#: Raw provider responses land here, outside the package tree, because they are
#: data artefacts rather than application state. Anchored to the repository
#: root rather than left as a bare relative ``Path("data/raw")``: a relative
#: path resolves against the process's current working directory, and both
#: documented ways of running ingestion -- ``cd apps/api && uv run quantforge
#: ...`` (the README's own quick start) and running from the repository root
#: -- must land in the same place. A relative default previously put raw
#: artifacts in ``apps/api/data/raw/`` when invoked the first way, which the
#: root ``.gitignore``'s ``data/raw/*`` entry (anchored to the directory that
#: file lives in, per git's own anchoring rule for a pattern containing a
#: non-trailing slash) does not cover -- risking a real provider response
#: being staged by an unqualified ``git add``.
DEFAULT_INGESTION_RAW_ROOT = _REPO_ROOT / "data" / "raw"


class Settings(BaseSettings):
    """Runtime configuration for the QuantForge API."""

    model_config = SettingsConfigDict(
        env_prefix="QF_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # A validation error must never echo the raw input: it holds the datastore URLs.
        hide_input_in_errors=True,
    )

    # ── Service identity ────────────────────────────────────────────────────
    environment: Environment = "local"
    debug: bool = False
    app_name: str = "quantforge-api"
    app_version: str = "0.1.0"
    api_v1_prefix: str = "/api/v1"

    # ── Datastores ──────────────────────────────────────────────────────────
    # ``repr=False``: both URLs can embed a password, and ``repr(settings)`` must never print one.
    database_url: str = Field(default=DEFAULT_DATABASE_URL, repr=False)
    redis_url: str = Field(default=DEFAULT_REDIS_URL, repr=False)

    # ── CORS ────────────────────────────────────────────────────────────────
    # NoDecode keeps the raw environment string intact so it can be parsed as
    # either a comma-separated list or a JSON array.
    cors_origins: Annotated[list[str], NoDecode] = list(DEFAULT_CORS_ORIGINS)
    cors_allow_credentials: bool = True

    # ── Observability ───────────────────────────────────────────────────────
    log_level: LogLevel = "INFO"

    # ── Market data providers ───────────────────────────────────────────────
    # Every provider setting is optional so that the application, the health
    # probes, the web terminal and the test suite all start with no credentials
    # configured. Only a live provider call requires a key, and it fails with a
    # clear message at that point rather than at process start.
    massive_api_key: SecretStr | None = None
    massive_base_url: str = DEFAULT_MASSIVE_BASE_URL
    #: Total budget for one provider request, applied to every phase of the
    #: connection separately (connect/read/write/pool) so a stalled read cannot
    #: hide behind a generous connect timeout.
    massive_request_timeout_seconds: float = 20.0
    #: Upper bound on attempts for *transient* failures only. Authentication and
    #: validation errors are never retried.
    massive_max_retries: int = 3
    #: Base delay for exponential backoff; the actual wait is this times 2**n with
    #: jitter. Ignored for failures that carry their own ``Retry-After``.
    massive_retry_base_delay_seconds: float = 0.5
    #: Minimum seconds between provider requests. The free tier is aggressively
    #: limited, so pacing happens here instead of being scattered as sleeps
    #: through the ingestion service.
    massive_min_request_interval_seconds: float = 0.2
    #: Hard stop on pagination so a provider that keeps handing back a next page
    #: cannot spin forever.
    massive_max_pages: int = 50
    #: Root of the on-disk raw response store. Outside the package on purpose:
    #: these are data artefacts, not application state.
    ingestion_raw_root: Path = DEFAULT_INGESTION_RAW_ROOT

    #: Earnings-only provider. Optional for the same reason ``massive_api_key``
    #: is: every other part of the application, including the rest of
    #: ingestion, works with no Finnhub key configured at all.
    finnhub_api_key: SecretStr | None = None
    finnhub_base_url: str = DEFAULT_FINNHUB_BASE_URL

    #: SEC EDGAR needs no key, but its fair-access policy requires every request to
    #: declare who is making it in the ``User-Agent`` (an application name plus a
    #: contact, e.g. ``"MyApp admin@example.com"``). Supplied by the operator via
    #: ``QF_SEC_USER_AGENT``; deliberately never defaulted or hard-coded, so no
    #: personal contact lives in source. Optional so every other part of the
    #: application starts without it; only a live SEC call requires it.
    #: A ``SecretStr`` because the contact is typically a personal email: it must not
    #: appear in a settings repr, dump or log.
    sec_user_agent: SecretStr | None = None
    sec_base_url: str = DEFAULT_SEC_BASE_URL
    sec_request_timeout_seconds: float = Field(default=20.0, gt=0)
    #: Transient-failure attempts only (a 429/403 from the SEC is a signal to slow
    #: down, not to hammer), so the default is small.
    sec_max_retries: int = Field(default=2, ge=0, le=5)
    #: Minimum seconds between requests. The SEC allows at most 10 requests per
    #: second per user; 0.25 s (4/s) stays well inside that, and the client raises any value
    #: below 0.2 s to 0.2 s (a 2x margin) -- clamped there, not rejected here, so a
    #: stale setting can never stop the rest of the application starting.
    sec_min_request_interval_seconds: float = Field(default=0.25, gt=0, le=60)

    @field_validator("massive_base_url", "finnhub_base_url", "sec_base_url")
    @classmethod
    def _validate_provider_base_url(cls, value: str) -> str:
        """Reject a non-HTTP base URL early, and strip a trailing slash.

        The trailing slash matters: it is concatenated with endpoint paths, so
        leaving it in would produce a doubled separator in every request URL.
        """
        stripped = value.strip()
        if not stripped.startswith(("http://", "https://")):
            message = f"provider base URL must be an http(s) URL, got {value!r}."
            raise ValueError(message)
        return stripped.rstrip("/")

    @field_validator("sec_user_agent", mode="before")
    @classmethod
    def _blank_user_agent_is_unset(cls, value: object) -> object:
        """Treat an empty or whitespace-only identity as absent (``QF_SEC_USER_AGENT=``)."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("massive_api_key", "finnhub_api_key", mode="before")
    @classmethod
    def _blank_api_key_is_unset(cls, value: object) -> object:
        """Treat an empty or whitespace-only key as absent.

        ``QF_MASSIVE_API_KEY=``/``QF_FINNHUB_API_KEY=`` in a ``.env`` file is
        the documented way to say "not configured". Treating that as a real
        key would turn a missing credential into a confusing 401 from the
        provider instead of a local, actionable error.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "massive_request_timeout_seconds",
        "massive_retry_base_delay_seconds",
        "massive_min_request_interval_seconds",
    )
    @classmethod
    def _reject_non_positive_duration(cls, value: float) -> float:
        if value <= 0:
            message = f"QF_MASSIVE_*_SECONDS must be greater than zero, got {value!r}."
            raise ValueError(message)
        return value

    @field_validator("massive_max_retries", "massive_max_pages")
    @classmethod
    def _reject_non_positive_count(cls, value: int) -> int:
        if value < 0:
            message = f"provider limits must not be negative, got {value!r}."
            raise ValueError(message)
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _coerce_cors_origins(cls, value: object) -> object:
        """Accept a comma-separated string, a JSON array, or a real sequence."""
        if not isinstance(value, str):
            return value

        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("["):
            # JSON array form. Decoded here because NoDecode deliberately stops
            # pydantic-settings from parsing complex fields for us.
            try:
                decoded: object = json.loads(raw)
            except json.JSONDecodeError as exc:
                message = f"QF_CORS_ORIGINS is not valid JSON: {exc}"
                raise ValueError(message) from exc
            return decoded
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    @field_validator("api_v1_prefix")
    @classmethod
    def _normalise_api_prefix(cls, value: str) -> str:
        """Guarantee a single leading slash and no trailing slash."""
        stripped = value.strip().strip("/")
        return f"/{stripped}" if stripped else "/"

    @model_validator(mode="after")
    def _reject_debug_in_production(self) -> Self:
        """Debug mode serves tracebacks (and so SQL, paths and values) in 500 responses."""
        if self.debug and self.environment == "production":
            message = "QF_DEBUG must be false when QF_ENVIRONMENT=production."
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def _reject_wildcard_with_credentials(self) -> Self:
        """A wildcard origin cannot be combined with credentialed CORS.

        The CORS specification forbids ``Access-Control-Allow-Origin: *``
        alongside ``Access-Control-Allow-Credentials: true``, and browsers
        reject such responses. Failing loudly at construction beats shipping a
        silently broken policy.
        """
        if self.cors_allow_credentials and "*" in self.cors_origins:
            message = (
                "QF_CORS_ORIGINS must not contain '*' while credentials are enabled "
                "(QF_CORS_ALLOW_CREDENTIALS=true); list explicit origins instead."
            )
            raise ValueError(message)
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
