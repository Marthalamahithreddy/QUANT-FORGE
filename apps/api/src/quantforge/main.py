"""FastAPI application factory for QuantForge.

Constructing the application performs no I/O. No PostgreSQL connection and no
Redis connection is established while importing this module or calling
:func:`create_app`, so the health surface is serveable with both datastores
stopped. Connections are only opened if a request actually needs them, and the
lifespan handler releases them again on shutdown.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from quantforge.api.router import create_api_router
from quantforge.cache.redis import close_redis
from quantforge.core.config import (
    CORS_ALLOW_HEADERS,
    CORS_ALLOW_METHODS,
    Settings,
    get_settings,
)
from quantforge.db.session import dispose_engines

__all__ = ["app", "create_app"]


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Release pooled connections on shutdown.

    ``application`` is unused but required by the lifespan signature; the
    ``ARG`` rules would otherwise demand a rename that breaks the protocol.
    """
    del application
    try:
        yield
    finally:
        close_redis()
        dispose_engines()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application.

    Args:
        settings: Configuration to bind. When omitted the process-wide
            :func:`~quantforge.core.config.get_settings` instance is used.
    """
    resolved = settings if settings is not None else get_settings()

    interactive = resolved.environment != "production"
    application = FastAPI(
        title="QuantForge API",
        summary="Event-driven equity research and quantitative backtesting platform.",
        version=resolved.app_version,
        debug=resolved.debug,
        docs_url="/docs" if interactive else None,
        redoc_url="/redoc" if interactive else None,
        openapi_url="/openapi.json" if interactive else None,
        lifespan=_lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_credentials=resolved.cors_allow_credentials,
        allow_methods=list(CORS_ALLOW_METHODS),
        allow_headers=list(CORS_ALLOW_HEADERS),
    )
    application.include_router(create_api_router(api_v1_prefix=resolved.api_v1_prefix))

    return application


#: ASGI entrypoint, e.g. ``uvicorn quantforge.main:app``.
app = create_app()
