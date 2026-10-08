"""Liveness endpoints.

Liveness answers exactly one question -- "is this process alive enough to answer HTTP?" -- and so
touches nothing else: no PostgreSQL, no Redis, no Alembic, no provider, no filesystem evidence. If
the process can serve this request, the answer is yes, whatever state its dependencies are in. Whether
the service can do useful work is the readiness probe's question (:mod:`quantforge.api.v1.ready`).
The payload reports only what this process itself can attest to; a structural test pins that this
module imports no datastore, cache, provider or migration code.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from quantforge.core.config import Settings, get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Liveness payload for the API process itself."""

    status: Literal["ok"] = Field(description="Liveness status of this API process.")
    service: str = Field(description="Logical service name.")
    version: str = Field(description="Deployed application version.")
    environment: str = Field(description="Deployment environment name.")


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
    description=(
        "Reports that the API process is answering HTTP. Queries nothing: it asserts "
        "nothing about any backing service (see the readiness probe)."
    ),
)
def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    """Return the health of the API process."""
    return HealthResponse(
        status="ok",
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
    )
