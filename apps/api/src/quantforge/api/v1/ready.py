"""Readiness endpoint.

Distinct from the liveness probe in :mod:`quantforge.api.v1.health`: liveness answers "is this
process answering HTTP?" and touches nothing; readiness answers "can this binary safely serve its
validated application contract against its required local infrastructure?" and therefore looks.

What gates readiness (HTTP 200 only when BOTH hold, otherwise 503 ``not_ready``):

* **database** -- PostgreSQL answers a bounded query;
* **schema** -- the database's Alembic revision equals this binary's single migration head, exactly.
  Behind, ahead, foreign, missing or ambiguous revisions are all "not ready": a binary is never
  assumed forward- or backward-compatible with a schema it was not built for.

What is reported but does NOT gate:

* **redis** -- ``ok`` / ``unavailable`` / ``not_configured``, with ``gating: false``. No validated
  production serving path uses Redis today, so its outage must not take the persisted read-only
  product out of rotation. RULE FOR THE FUTURE: a feature that makes Redis part of serving must make
  its status gating in that same change, together with the tests that prove it.

What is never probed: Massive, Finnhub and the SEC. Provider availability is unrelated to serving
what is already stored; it is the business of the ingestion commands, which fail on their own.

The probe observes and never repairs: it does not migrate, stamp or write. The response carries
state names, revision identifiers and exception class names -- never a URL, host, user, password,
SQL, path or message.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field

from quantforge.cache.redis import ping_redis
from quantforge.core.config import Settings, get_settings
from quantforge.core.dependencies import (
    DatabaseProbe,
    DependencyCheck,
    DependencyName,
    DependencyState,
    SchemaCheck,
)
from quantforge.db.session import probe_database

router = APIRouter(tags=["health"])

DatabaseStateLiteral = Literal["ok", "unavailable"]
RedisStateLiteral = Literal["ok", "unavailable", "not_configured"]
SchemaStateLiteral = Literal["compatible", "mismatch", "unknown"]


class DatabaseReadiness(BaseModel):
    """PostgreSQL connectivity. Gates readiness."""

    state: DatabaseStateLiteral = Field(description="PostgreSQL connectivity.")
    gating: Literal[True] = Field(
        default=True, description="An unavailable database makes the service not ready."
    )


class RedisReadiness(BaseModel):
    """Redis state. Reported for operators; never gates readiness."""

    state: RedisStateLiteral = Field(
        description="Redis connectivity, or that it is not configured."
    )
    gating: Literal[False] = Field(
        default=False,
        description="Redis does not affect readiness: no serving path requires it.",
    )


class DependencyReadiness(BaseModel):
    """Per-dependency readiness, reported for every dependency on every call."""

    database: DatabaseReadiness
    redis: RedisReadiness


class SchemaReadiness(BaseModel):
    """Whether the database schema is the migration head this binary expects. Gates readiness."""

    state: SchemaStateLiteral = Field(
        description="'compatible' only when the database revision equals the expected head exactly."
    )
    expected: str | None = Field(
        description="The single Alembic head of this binary; null when it cannot be established."
    )
    current: str | None = Field(
        description="The database's single Alembic revision; null when it cannot be read."
    )
    gating: Literal[True] = Field(
        default=True, description="Any state other than 'compatible' makes the service not ready."
    )


class ReadinessResponse(BaseModel):
    """Readiness payload: the verdict, and the state of everything that was looked at."""

    # The wire name is `schema`; the Python attribute avoids shadowing BaseModel.schema.
    model_config = ConfigDict(populate_by_name=True)

    status: Literal["ready", "not_ready"] = Field(
        description="'ready' only when the database is reachable AND the schema is compatible."
    )
    service: str = Field(description="Logical service name.")
    dependencies: DependencyReadiness = Field(description="State of each backing service.")
    schema_state: SchemaReadiness = Field(
        alias="schema", description="Migration-head compatibility of the database."
    )
    detail: dict[str, str] | None = Field(
        default=None,
        description=(
            "For each unavailable dependency, the exception class name. Never "
            "contains a message, DSN, password or stack trace."
        ),
    )


def check_database(settings: Annotated[Settings, Depends(get_settings)]) -> DatabaseProbe:
    """FastAPI dependency wrapping the PostgreSQL + schema probe."""
    return probe_database(settings)


def check_redis(settings: Annotated[Settings, Depends(get_settings)]) -> DependencyCheck:
    """FastAPI dependency wrapping the Redis probe."""
    return ping_redis(settings)


DatabaseProbeDep = Annotated[DatabaseProbe, Depends(check_database)]
RedisProbe = Annotated[DependencyCheck, Depends(check_redis)]


def _schema(check: SchemaCheck) -> SchemaReadiness:
    return SchemaReadiness(
        state=check.state.value,
        expected=check.expected,
        current=check.current,
    )


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    response_model_by_alias=True,
    summary="Readiness probe",
    description=(
        "Reports whether this binary can serve its application contract: PostgreSQL reachable and "
        "its schema exactly the expected migration head. Redis is reported but does not gate. "
        "Returns 200 when ready and 503 otherwise, always with a typed body."
    ),
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse},
    },
)
def readiness(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    database: DatabaseProbeDep,
    redis: RedisProbe,
) -> ReadinessResponse:
    """Return the readiness of the API against its required infrastructure."""
    is_ready = database.database.is_ok and database.schema.is_compatible

    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    detail = {
        check.name.value: check.detail or DependencyState.UNAVAILABLE.value
        for check in (database.database, redis)
        if check.state is DependencyState.UNAVAILABLE
    }

    return ReadinessResponse(
        status="ready" if is_ready else "not_ready",
        service=settings.app_name,
        dependencies=DependencyReadiness(
            database=DatabaseReadiness(state="ok" if database.database.is_ok else "unavailable"),
            redis=RedisReadiness(state=redis.state.value),
        ),
        schema_state=_schema(database.schema),
        detail=detail or None,
    )


__all__ = [
    "DependencyName",
    "DependencyReadiness",
    "ReadinessResponse",
    "SchemaReadiness",
    "check_database",
    "check_redis",
    "readiness",
    "router",
]
