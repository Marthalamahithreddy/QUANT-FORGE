"""Readiness endpoint.

Distinct from the liveness probe in :mod:`quantforge.api.v1.health`: liveness
answers "is this process serving requests?" and must not touch a datastore, while
readiness answers "can this process serve real traffic?" and therefore does.

Each dependency is probed independently and a failure never propagates as an
exception. The response identifies which dependency is down and the HTTP status
is ``503 Service Unavailable`` so orchestrators can act on it.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field

from quantforge.cache.redis import ping_redis
from quantforge.core.config import Settings, get_settings
from quantforge.core.dependencies import DependencyCheck, DependencyName, DependencyState
from quantforge.db.session import ping_database

router = APIRouter(tags=["health"])

DependencyStateLiteral = Literal["ok", "unavailable"]


class DependencyReadiness(BaseModel):
    """Per-dependency readiness, reported for every dependency on every call."""

    database: DependencyStateLiteral = Field(description="PostgreSQL connectivity.")
    redis: DependencyStateLiteral = Field(description="Redis connectivity.")


class ReadinessResponse(BaseModel):
    """Readiness payload including the outcome for each backing service."""

    status: Literal["ready", "not_ready"] = Field(
        description="'ready' only when every dependency is reachable."
    )
    service: str = Field(description="Logical service name.")
    dependencies: DependencyReadiness = Field(description="State of each dependency.")
    detail: dict[str, str] | None = Field(
        default=None,
        description=(
            "For each unavailable dependency, the exception class name. Never "
            "contains a message, DSN, password or stack trace."
        ),
    )


def check_database(settings: Annotated[Settings, Depends(get_settings)]) -> DependencyCheck:
    """FastAPI dependency wrapping the PostgreSQL probe."""
    return ping_database(settings)


def check_redis(settings: Annotated[Settings, Depends(get_settings)]) -> DependencyCheck:
    """FastAPI dependency wrapping the Redis probe."""
    return ping_redis(settings)


DatabaseProbe = Annotated[DependencyCheck, Depends(check_database)]
RedisProbe = Annotated[DependencyCheck, Depends(check_redis)]


def _to_state(check: DependencyCheck) -> DependencyStateLiteral:
    return DependencyState.OK.value if check.is_ok else DependencyState.UNAVAILABLE.value


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description=(
        "Reports whether PostgreSQL and Redis are reachable. Returns 200 when "
        "both are available and 503 otherwise, always with a typed body naming "
        "the failing dependency."
    ),
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse},
    },
)
def readiness(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    database: DatabaseProbe,
    redis: RedisProbe,
) -> ReadinessResponse:
    """Return the readiness of the API and its backing services."""
    checks = (database, redis)
    is_ready = all(check.is_ok for check in checks)

    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    unavailable = {
        check.name.value: check.detail or DependencyState.UNAVAILABLE.value
        for check in checks
        if not check.is_ok
    }

    return ReadinessResponse(
        status="ready" if is_ready else "not_ready",
        service=settings.app_name,
        dependencies=DependencyReadiness(
            database=_to_state(database),
            redis=_to_state(redis),
        ),
        detail=unavailable or None,
    )


__all__ = [
    "DependencyName",
    "DependencyReadiness",
    "ReadinessResponse",
    "check_database",
    "check_redis",
    "readiness",
    "router",
]
