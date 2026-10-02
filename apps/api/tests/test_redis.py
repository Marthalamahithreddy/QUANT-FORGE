"""Redis infrastructure tests.

Every test here runs offline: the client is built lazily and never dialled, and
the connectivity probe is driven through an injected stub. Nothing in this module
requires a Redis server.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from redis import Redis

from quantforge.cache import redis as redis_module
from quantforge.cache.redis import (
    DEFAULT_SOCKET_TIMEOUT_SECONDS,
    close_redis,
    get_redis,
    ping_redis,
)
from quantforge.core.config import Settings
from quantforge.core.dependencies import DependencyName, DependencyState
from quantforge.main import create_app

UNREACHABLE_REDIS_URL = "redis://127.0.0.1:1/0"


class _StubClient:
    """Minimal stand-in exposing only what the probe uses."""

    def __init__(self, reply: object = "PONG", error: BaseException | None = None) -> None:
        self.reply = reply
        self.error = error
        self.calls = 0

    def ping(self) -> object:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.reply


def test_app_construction_creates_no_client(
    make_settings: Callable[..., Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _explode(*args: object, **kwargs: object) -> None:
        message = "Redis.from_url must not be called while building the application"
        raise AssertionError(message)

    monkeypatch.setattr(Redis, "from_url", staticmethod(_explode))

    create_app(make_settings())


def test_health_route_creates_no_client(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Liveness must stay independent of Redis."""

    def _explode(*args: object, **kwargs: object) -> None:
        message = "Redis.from_url must not be called while serving /health"
        raise AssertionError(message)

    monkeypatch.setattr(Redis, "from_url", staticmethod(_explode))

    assert client.get("/health").status_code == 200


def test_get_redis_is_lazy_and_does_not_connect(
    make_settings: Callable[..., Settings],
) -> None:
    """Building a client must not open a socket."""
    client = get_redis(make_settings(redis_url=UNREACHABLE_REDIS_URL))

    assert isinstance(client, Redis)
    assert client.ping is not None


def test_client_is_cached_across_calls(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(redis_url=UNREACHABLE_REDIS_URL)

    assert get_redis(settings) is get_redis(settings)


def test_client_rebuilt_when_url_changes(make_settings: Callable[..., Settings]) -> None:
    first = get_redis(make_settings(redis_url=UNREACHABLE_REDIS_URL))
    second = get_redis(make_settings(redis_url="redis://127.0.0.1:2/0"))

    assert first is not second


def test_client_decodes_responses_and_bounds_socket_waits(
    make_settings: Callable[..., Settings],
) -> None:
    client = get_redis(make_settings(redis_url=UNREACHABLE_REDIS_URL))
    connection_kwargs = client.connection_pool.connection_kwargs

    assert connection_kwargs["decode_responses"] is True
    assert connection_kwargs["socket_connect_timeout"] == DEFAULT_SOCKET_TIMEOUT_SECONDS
    assert connection_kwargs["socket_timeout"] == DEFAULT_SOCKET_TIMEOUT_SECONDS


def test_close_redis_releases_the_client(
    make_settings: Callable[..., Settings],
) -> None:
    get_redis(make_settings(redis_url=UNREACHABLE_REDIS_URL))

    close_redis()

    assert redis_module._registry._client is None
    assert redis_module._registry._url is None


def test_close_redis_is_safe_when_uninitialised() -> None:
    close_redis()
    close_redis()


def test_ping_reports_ok_for_a_pong_reply() -> None:
    result = ping_redis(client=_StubClient(reply="PONG"))

    assert result.is_ok is True
    assert result.state is DependencyState.OK
    assert result.name is DependencyName.REDIS
    assert result.detail is None


def test_ping_accepts_bytes_reply() -> None:
    """A client without decode_responses answers with bytes."""
    assert ping_redis(client=_StubClient(reply=b"PONG")).is_ok is True


def test_ping_accepts_true_reply() -> None:
    assert ping_redis(client=_StubClient(reply=True)).is_ok is True


def test_ping_probes_once() -> None:
    stub = _StubClient()

    ping_redis(client=stub)

    assert stub.calls == 1


def test_ping_reports_unavailable_on_connection_error() -> None:
    result = ping_redis(client=_StubClient(error=ConnectionError("refused")))

    assert result.is_ok is False
    assert result.state is DependencyState.UNAVAILABLE
    assert result.name is DependencyName.REDIS


def test_ping_detail_is_the_exception_type_name_only() -> None:
    """The detail must never carry a message that could contain a credential."""
    result = ping_redis(client=_StubClient(error=ConnectionError("password=hunter2")))

    assert result.detail == "ConnectionError"
    assert "hunter2" not in (result.detail or "")


def test_ping_reports_unavailable_on_unexpected_reply() -> None:
    result = ping_redis(client=_StubClient(reply="NOPE"))

    assert result.is_ok is False
    assert result.detail == "UnexpectedReply"


def test_ping_never_raises_on_an_unanticipated_error_type() -> None:
    """An error the probe has never seen must still degrade to 'unavailable'."""

    class _ExoticError(Exception):
        pass

    result = ping_redis(client=_StubClient(error=_ExoticError("boom")))

    assert result.is_ok is False
    assert result.detail == "_ExoticError"
