"""Tests for the Finnhub adapter's HTTP behaviour.

Mirrors ``test_massive_client.py``'s structure and coverage: exact-bytes
capture, Decimal exactness, failure classification, retry/backoff/pacing, and
credential redaction. No test here depends on Finnhub being reachable.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

import httpx2 as httpx
import pytest

from quantforge.providers.errors import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderServerError,
    ProviderTransportError,
)
from quantforge.providers.finnhub.client import FinnhubClient, FinnhubConfig
from quantforge.providers.pacing import Sleeper

EARNINGS_BODY = json.dumps(
    {
        "earningsCalendar": [
            {
                "symbol": "AAPL",
                "date": "2026-05-01",
                "quarter": 2,
                "year": 2026,
                "epsActual": 1.52,
                "epsEstimate": 1.43,
                "revenueActual": 123456789,
                "revenueEstimate": 120000000,
                "hour": "amc",
            }
        ]
    }
).encode()


async def _no_sleep(_seconds: float) -> None:
    return None


def record_sleep(target: list[float]) -> Callable[[float], Any]:
    async def recorder(seconds: float) -> None:
        target.append(seconds)

    return recorder


def _client(handler: Any, *, sleep: Sleeper = _no_sleep, **config: Any) -> FinnhubClient:
    return FinnhubClient(
        FinnhubConfig(
            api_key="test-key-0123456789", base_url="https://finnhub.io/api/v1", **config
        ),
        transport=httpx.MockTransport(handler),
        sleep=sleep,
    )


# ── the happy path ───────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_complete_response_maps_to_one_canonical_event() -> None:
    client = _client(lambda _r: httpx.Response(200, content=EARNINGS_BODY))
    result = await client.get_earnings("AAPL", date(2026, 1, 1), date(2026, 12, 31))

    assert len(result.events) == 1
    event = result.events[0]
    assert event.ticker == "AAPL"
    assert event.event_date == date(2026, 5, 1)
    assert event.event_timestamp is None
    assert event.reported_eps == Decimal("1.52")
    assert event.reported_revenue == Decimal("123456789")
    assert len(result.captures) == 1


@pytest.mark.anyio
async def test_the_capture_holds_the_exact_response_bytes() -> None:
    """Re-encoding the decoded document would normalise away whitespace and
    key order, and the stored SHA-256 would attest to bytes Finnhub never
    sent."""
    raw = b'{"earningsCalendar":[{"symbol":"AAPL","date":"2026-05-01","year":2026}]}'
    client = _client(lambda _r: httpx.Response(200, content=raw))
    result = await client.get_earnings("AAPL", date(2026, 1, 1), date(2026, 12, 31))
    assert result.captures[0].body == raw


@pytest.mark.anyio
async def test_an_empty_calendar_is_a_legitimate_empty_result() -> None:
    """No earnings in range is a real answer, not a failure."""
    client = _client(lambda _r: httpx.Response(200, content=b'{"earningsCalendar":[]}'))
    result = await client.get_earnings("ZZZZ", date(2026, 1, 1), date(2026, 12, 31))
    assert list(result.events) == []
    assert len(result.captures) == 1


@pytest.mark.anyio
async def test_a_range_ending_before_it_starts_is_rejected_locally() -> None:
    client = _client(lambda _r: httpx.Response(200, content=b'{"earningsCalendar":[]}'))
    with pytest.raises(ValueError, match="precedes"):
        await client.get_earnings("AAPL", date(2026, 12, 31), date(2026, 1, 1))


@pytest.mark.anyio
async def test_the_request_carries_the_symbol_and_date_range() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b'{"earningsCalendar":[]}')

    client = _client(handler)
    await client.get_earnings("aapl", date(2026, 1, 1), date(2026, 3, 31))
    query = dict(httpx.QueryParams(seen[0].url.query))
    assert query == {"symbol": "AAPL", "from": "2026-01-01", "to": "2026-03-31"}


# ── authentication and configuration ────────────────────────────────────────


def test_missing_key_fails_before_any_request() -> None:
    calls: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=b"{}")

    config = FinnhubConfig(api_key=None, base_url="https://finnhub.io/api/v1")
    FinnhubClient(config, transport=httpx.MockTransport(record))
    with pytest.raises(ProviderConfigurationError, match="QF_FINNHUB_API_KEY"):
        config.require_api_key()
    assert calls == []


@pytest.mark.anyio
async def test_auth_failure_is_classified_as_authentication() -> None:
    client = _client(lambda _r: httpx.Response(401, content=b'{"error":"Invalid API key"}'))
    with pytest.raises(ProviderAuthenticationError):
        await client.get_earnings("AAPL", date(2026, 1, 1), date(2026, 1, 31))


@pytest.mark.anyio
async def test_forbidden_is_authentication_not_transient() -> None:
    client = _client(lambda _r: httpx.Response(403, content=b'{"error":"forbidden"}'))
    with pytest.raises(ProviderAuthenticationError):
        await client.get_earnings("AAPL", date(2026, 1, 1), date(2026, 1, 31))


def test_repr_never_shows_the_key() -> None:
    client = FinnhubClient(
        FinnhubConfig(api_key="super-secret-value", base_url="https://finnhub.io/api/v1")
    )
    assert "super-secret-value" not in repr(client)
    assert "super-secret-value" not in repr(client._config)


@pytest.mark.anyio
async def test_the_key_is_sent_as_a_header_never_a_query_parameter() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b'{"earningsCalendar":[]}')

    client = _client(handler)
    await client.get_earnings("AAPL", date(2026, 1, 1), date(2026, 1, 31))
    assert seen[0].headers["X-Finnhub-Token"] == "test-key-0123456789"
    assert "test-key-0123456789" not in str(seen[0].url)


# ── rate limiting and retries ───────────────────────────────────────────────


@pytest.mark.anyio
async def test_rate_limit_is_classified_as_rate_limit() -> None:
    client = _client(lambda _r: httpx.Response(429, content=b"{}"), max_retries=0)
    with pytest.raises(ProviderRateLimitError):
        await client.get_earnings("AAPL", date(2026, 1, 1), date(2026, 1, 31))


@pytest.mark.anyio
async def test_rate_limit_retry_uses_retry_after_when_given() -> None:
    retried: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, content=b"{}", headers={"Retry-After": "2.5"})

    client = _client(handler, max_retries=1, sleep=record_sleep(retried))
    with pytest.raises(ProviderRateLimitError):
        await client.get_earnings("AAPL", date(2026, 1, 1), date(2026, 1, 31))
    assert retried[0] == pytest.approx(2.5)


@pytest.mark.anyio
async def test_server_error_is_retried_then_raised() -> None:
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, content=b"{}")

    client = _client(handler, max_retries=2)
    with pytest.raises(ProviderServerError):
        await client.get_earnings("AAPL", date(2026, 1, 1), date(2026, 1, 31))
    assert len(calls) == 3


@pytest.mark.anyio
async def test_bad_request_is_not_retried() -> None:
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, content=b"{}")

    client = _client(handler)
    with pytest.raises(ProviderResponseError):
        await client.get_earnings("AAPL", date(2026, 1, 1), date(2026, 1, 31))
    assert len(calls) == 1


@pytest.mark.anyio
async def test_transport_timeout_is_a_transport_failure() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    client = _client(handler, max_retries=0)
    with pytest.raises(ProviderTransportError, match="ReadTimeout"):
        await client.get_earnings("AAPL", date(2026, 1, 1), date(2026, 1, 31))


@pytest.mark.anyio
async def test_malformed_json_body_is_refused() -> None:
    client = _client(lambda _r: httpx.Response(200, content=b"not json at all"))
    with pytest.raises(ProviderResponseError):
        await client.get_earnings("AAPL", date(2026, 1, 1), date(2026, 1, 31))


@pytest.mark.anyio
async def test_a_non_object_top_level_document_is_refused() -> None:
    client = _client(lambda _r: httpx.Response(200, content=b"[1, 2, 3]"))
    with pytest.raises(ProviderResponseError):
        await client.get_earnings("AAPL", date(2026, 1, 1), date(2026, 1, 31))


@pytest.mark.anyio
async def test_pacing_is_enforced_before_each_request() -> None:
    """The rate limiter's minimum interval must actually gate requests, not
    just exist unused."""
    delays: list[float] = []

    client = _client(
        lambda _r: httpx.Response(200, content=b'{"earningsCalendar":[]}'),
        sleep=record_sleep(delays),
        min_request_interval=5.0,
    )
    await client.get_earnings("AAPL", date(2026, 1, 1), date(2026, 1, 31))
    await client.get_earnings("AAPL", date(2026, 1, 1), date(2026, 1, 31))
    # The first request is never delayed (nothing to space it from yet); the
    # second must wait close to the full configured interval, minus whatever
    # real wall-clock time the first call itself took.
    assert any(delay > 4.0 for delay in delays), delays
