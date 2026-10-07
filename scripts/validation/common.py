"""Harness plumbing for the 11.2B capability probe. Orchestration only.

No provider parsing, no business rules and no canonical writes live here. It builds the existing
clients with a counting transport, points the existing services at an isolated validation
database, and never prints a credential.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx2 as httpx
from sqlalchemy.engine import make_url

from quantforge.core.config import DEFAULT_DATABASE_URL, Settings, get_settings
from quantforge.ingestion.raw_store import RawResponseStore

ROOT = Path(__file__).resolve().parents[2]
ART = Path(__file__).resolve().parent / "artifacts"
LEDGER = ART / "ledger-11.2E.json"  # 11.2B/C ledger kept separately as ledger.json (11 of 12)
RAW_ROOT = ART / "raw"
VALIDATION_DB = "quantforge_validation"
HARD_CAP = 8


class BudgetExceeded(Exception):  # noqa: N818 - plain Exception on purpose: clients must not retry or wrap it
    """Raised BEFORE a request that would exceed the ceiling is sent."""


def _load() -> dict:
    ART.mkdir(parents=True, exist_ok=True)
    if LEDGER.exists():
        return json.loads(LEDGER.read_text())
    return {"cap": HARD_CAP, "total": 0, "by_provider": {}, "requests": []}


def ledger() -> dict:
    return _load()


class CountingTransport(httpx.AsyncBaseTransport):
    """Counts every outbound attempt (retries and pagination included); fails closed."""

    def __init__(self, provider: str) -> None:
        self._provider = provider
        self._inner = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        led = _load()
        if led["total"] + 1 > led["cap"]:
            msg = f"request ceiling {led['cap']} reached; refusing to send"
            raise BudgetExceeded(msg)
        led["total"] += 1
        led["by_provider"][self._provider] = led["by_provider"].get(self._provider, 0) + 1
        entry = {
            "n": led["total"],
            "provider": self._provider,
            "method": request.method,
            "host": request.url.host,
            "path": request.url.path,
            "query_names": sorted(dict(request.url.params).keys()),
            "at": datetime.now(UTC).isoformat(),
            "status": None,
        }
        led["requests"].append(entry)
        LEDGER.write_text(json.dumps(led, indent=1))
        started = time.monotonic()
        try:
            response = await self._inner.handle_async_request(request)
        except Exception:
            entry["status"] = "transport-error"
            LEDGER.write_text(json.dumps(led, indent=1))
            raise
        entry["status"] = response.status_code
        entry["seconds"] = round(time.monotonic() - started, 3)
        LEDGER.write_text(json.dumps(led, indent=1))
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


def validation_settings() -> Settings:
    """Settings whose database is the isolated validation DB (credentials never printed)."""
    # The dev database is reached through the application's default URL: the URL in .env does not
    # authenticate against the running container (observed), while the default does.
    base = get_settings()
    url = make_url(DEFAULT_DATABASE_URL).set(database=VALIDATION_DB)
    return base.model_copy(update={"database_url": url.render_as_string(hide_password=False)})


def raw_store() -> RawResponseStore:
    RAW_ROOT.mkdir(parents=True, exist_ok=True)
    return RawResponseStore(RAW_ROOT)


def present(value: object) -> str:
    return "CONFIGURED" if value is not None and str(getattr(value, "get_secret_value", lambda: value)()).strip() else "MISSING"


def drop_validation_db() -> None:
    """Drop ONLY the isolated validation database (never the dev one) so it can be rebuilt by migrations."""
    for sql in (
        f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='{VALIDATION_DB}' AND pid<>pg_backend_pid()",
        f"DROP DATABASE IF EXISTS {VALIDATION_DB}",
    ):
        subprocess.run(  # noqa: S603
            ["docker", "exec", "quantforge-postgres", "sh", "-c",  # noqa: S607
             f'psql -U "$POSTGRES_USER" -d postgres -c "{sql}"'],
            check=True, capture_output=True, text=True,
        )


def create_db_and_migrate() -> None:
    """Create the isolated DB inside the container (no credentials read) and run normal migrations."""
    sql = f"CREATE DATABASE {VALIDATION_DB}"
    check = subprocess.run(  # noqa: S603
        ["docker", "exec", "quantforge-postgres", "sh", "-c",  # noqa: S607
         'psql -U "$POSTGRES_USER" -d postgres -tAc '
         f"\"SELECT 1 FROM pg_database WHERE datname='{VALIDATION_DB}'\""],
        capture_output=True, text=True, check=True,
    )
    if check.stdout.strip() != "1":
        subprocess.run(  # noqa: S603
            ["docker", "exec", "quantforge-postgres", "sh", "-c",  # noqa: S607
             f'psql -U "$POSTGRES_USER" -d postgres -c "{sql}"'],
            check=True, capture_output=True, text=True,
        )
    env = dict(os.environ)
    env["QF_DATABASE_URL"] = validation_settings().database_url
    done = subprocess.run(  # noqa: S603
        ["uv", "run", "alembic", "upgrade", "head"],  # noqa: S607
        cwd=ROOT / "apps" / "api", env=env, capture_output=True, text=True,
    )
    print("alembic exit:", done.returncode)
    print((done.stdout + done.stderr).strip().splitlines()[-3:])
