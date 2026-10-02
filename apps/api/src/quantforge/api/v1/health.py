"""Health endpoints.

The health payload reports only what this process can actually attest to.
PostgreSQL and Redis status fields are deliberately absent: neither service is
configured or running at the foundation milestone, and reporting a fabricated
dependency status would be worse than reporting nothing.
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
        "Reports that the API process is serving requests. Does not assert the "
        "health of any backing datastore."
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
