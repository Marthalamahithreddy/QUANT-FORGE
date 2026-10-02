"""Top-level API router assembly."""

from __future__ import annotations

from fastapi import APIRouter

from quantforge.api.v1.health import router as health_router
from quantforge.api.v1.router import router as v1_router


def create_api_router(*, api_v1_prefix: str) -> APIRouter:
    """Assemble the full API surface.

    Exposes the health probe twice on purpose:

    * unversioned at ``/health``, for container and orchestrator probes that
      should not move when the API is versioned;
    * versioned at ``{api_v1_prefix}/health``, so clients pin the same contract
      they pin for every other endpoint.
    """
    api_router = APIRouter()
    api_router.include_router(health_router)
    api_router.include_router(v1_router, prefix=api_v1_prefix)
    return api_router
