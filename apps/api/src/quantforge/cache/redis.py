"""Cache and queue infrastructure backed by Redis.

Importing this module never opens a socket: :func:`get_redis` defers client
construction until first use, and ``Redis.from_url`` itself only prepares a
connection pool. Call :func:`close_redis` to release the pool, typically on
application shutdown.

No task queue (Celery) is wired up at this milestone.
"""

from __future__ import annotations

import logging
from typing import Protocol

from redis import Redis
from redis.exceptions import RedisError

from quantforge.core.config import Settings, get_settings
from quantforge.core.dependencies import DependencyCheck, DependencyName, DependencyState

logger = logging.getLogger(__name__)

#: Seconds allowed for establishing a connection and for each command. Kept short
#: so a dead Redis surfaces in the readiness probe instead of hanging it.
DEFAULT_SOCKET_TIMEOUT_SECONDS = 2.0


class Pingable(Protocol):
    """The only part of the Redis client the readiness probe depends on."""

    def ping(self) -> object:
        """Issue ``PING``."""
        ...


class _RedisRegistry:
    """Holds the process-wide client, rebuilt when the configured URL changes."""

    def __init__(self) -> None:
        self._client: Redis | None = None
        self._url: str | None = None

    def client(self, redis_url: str) -> Redis:
        """Return the client for ``redis_url``, creating it if required."""
        if self._client is None or self._url != redis_url:
            self.close()
            self._client = Redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=DEFAULT_SOCKET_TIMEOUT_SECONDS,
                socket_timeout=DEFAULT_SOCKET_TIMEOUT_SECONDS,
            )
            self._url = redis_url
        return self._client

    def close(self) -> None:
        """Close the pooled connections and drop cached state."""
        if self._client is not None:
            try:
                self._client.close()
            except RedisError:  # pragma: no cover - defensive only
                logger.warning("Closing the Redis connection pool failed.", exc_info=True)
        self._client = None
        self._url = None


_registry = _RedisRegistry()


def _resolve_redis_url(settings: Settings | None) -> str:
    resolved = settings if settings is not None else get_settings()
    return resolved.redis_url


def get_redis(settings: Settings | None = None) -> Redis:
    """Return the process-wide :class:`redis.Redis` client.

    Construction is lazy and does not dial the server.
    """
    return _registry.client(_resolve_redis_url(settings))


def close_redis() -> None:
    """Release the pooled connections. Intended for shutdown and for tests."""
    _registry.close()


def ping_redis(
    settings: Settings | None = None,
    client: Pingable | None = None,
) -> DependencyCheck:
    """Probe Redis with ``PING``.

    Args:
        settings: Configuration to use. Defaults to the process-wide settings.
        client: Optional pre-built client, so tests can inject a stub instead of
            constructing a real one.

    Returns:
        A :class:`~quantforge.core.dependencies.DependencyCheck` that never
        raises. On failure the detail is the exception type name only.
    """
    redis_client: Pingable = client if client is not None else get_redis(settings)
    try:
        response = redis_client.ping()
    except Exception as exc:  # any client error means the dependency is unavailable
        logger.warning("Redis readiness probe failed.", exc_info=True)
        return DependencyCheck.failure(DependencyName.REDIS, exc)

    # A successful PING answers b"PONG" or "PONG" depending on decode_responses.
    if response not in (b"PONG", "PONG", True):
        logger.warning("Redis readiness probe returned an unexpected reply: %r", response)
        return DependencyCheck(
            name=DependencyName.REDIS,
            state=DependencyState.UNAVAILABLE,
            detail="UnexpectedReply",
        )

    return DependencyCheck.ok(DependencyName.REDIS)
