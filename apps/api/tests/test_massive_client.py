"""Tests for the Massive adapter.

Three concerns, in order of how badly they would hurt if wrong:

* **bytes** -- what gets captured must be what arrived, because the raw store's
  SHA-256 is a claim about evidence;
* **decimals** -- prices must never pass through a binary float;
* **failure classification** -- a 404 is an answer, a 500 is a retry, a 401 is a
  configuration problem, and conflating them produces both silent data loss and
  pointless retry storms.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx2 as httpx
import pytest

from quantforge.ingestion.canonical import ResolvedVenue
from quantforge.models.enums import PriceBasis
from quantforge.providers.base import RawCapture
from quantforge.providers.errors import (
    ProviderAuthenticationError,
    ProviderBasisMismatchError,
    ProviderConfigurationError,
    ProviderPayloadError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderServerError,
    ProviderTransportError,
)
from quantforge.providers.massive.client import MassiveClient, MassiveConfig
from quantforge.providers.massive.exchanges import UnsupportedExchangeError, resolve_venue
from quantforge.providers.pacing import BackoffPolicy, RateLimiter, Sleeper

XNAS = ResolvedVenue(mic="XNAS", name="NasdaqGS", timezone_name="America/New_York")

TICKER_BODY = json.dumps(
    {
        "status": "OK",
        "results": {
            "ticker": "AAPL",
            "name": "Apple Inc.",
            "cik": "0000320193",
            "active": True,
            "primary_exchange": "XNAS",
            "type": "CS",
            "currency_name": "usd",
            "share_class_figi": "BBG000B9XRY4",
        },
    }
).encode()


async def _no_sleep(_seconds: float) -> None:
    return None


def record_sleep(target: list[float]) -> Callable[[float], Any]:
    """Return a sleep that records its argument rather than spending time."""

    async def recorder(seconds: float) -> None:
        target.append(seconds)

    return recorder


def _client(handler: Any, *, sleep: Sleeper = _no_sleep, **config: Any) -> MassiveClient:
    return MassiveClient(
        MassiveConfig(api_key="test-key-0123456789", base_url="https://api.massive.com", **config),
        transport=httpx.MockTransport(handler),
        sleep=sleep,
    )


def _json(payload: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(payload).encode())


# ── exact bytes ─────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_capture_holds_the_exact_response_bytes() -> None:
    """Whitespace, key order and numeric literals must survive capture untouched.

    If the adapter re-encoded the decoded document, a re-encoding would normalise
    all three away, and the stored SHA-256 would attest to bytes the provider
    never sent.
    """
    raw = b'{"status" : "OK" ,\n "results":  {"ticker":"AAPL", "cik":"0000320193"}}'
    client = _client(lambda _r: httpx.Response(200, content=raw))

    result = await client.get_ticker_details("AAPL")

    assert len(result.captures) == 1
    assert result.captures[0].body == raw
    # And the data is still usable, so preserving the bytes cost nothing.
    assert result.reference is not None
    assert result.reference.cik == "0000320193"


@pytest.mark.anyio
async def test_capture_records_endpoint_template_and_not_the_url() -> None:
    """The manifest must never be able to carry a query string.

    The key is sent as a header, so it is not in the URL either -- but the
    capture's endpoint is stored forever, so it is asserted to be the template.
    """
    client = _client(lambda _r: _json({"status": "OK", "results": {"ticker": "AAPL"}}))
    result = await client.get_ticker_details("AAPL")
    capture = result.captures[0]

    assert "?" not in capture.endpoint
    assert capture.request_parameters == {"ticker": "AAPL"}
    assert "test-key-0123456789" not in json.dumps(dict(capture.request_parameters))


# ── decimals ────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_prices_are_decimals_not_floats() -> None:
    """A price that a float cannot represent exactly must stay exact.

    ``0.1 + 0.2 != 0.3`` in binary floating point, and a provider that sends
    ``123.45000000000000284``-style values would be laundered into stored garbage
    by a float round-trip.
    """
    # Written as literal bytes, not via json.dumps: a Python float literal would
    # be rounded to 123.45 before it ever reached the wire, which would make the
    # test pass for the wrong reason.
    body = (
        b'{"status":"OK","results":[{"t":1756944000000,'
        b'"o":123.45000000000000284,"h":124,"l":122,"c":123.45,"v":1000}]}'
    )
    client = _client(lambda _r: httpx.Response(200, content=body))

    result = await client.get_daily_bars(
        "AAPL",
        date(2026, 9, 1),
        date(2026, 9, 5),
        venue=XNAS,
        price_basis=PriceBasis.SPLIT_ADJUSTED,
    )

    bar = result.bars[0]
    assert isinstance(bar.open, Decimal)
    assert str(bar.open) == "123.45000000000000284"
    assert isinstance(bar.close, Decimal)


@pytest.mark.anyio
async def test_live_wire_shape_parses_with_whole_share_volume() -> None:
    """Sanitized regression from the real ``/v2/aggs`` response (2026-09-28).

    The live provider keys the aggregate timestamp with lowercase ``t`` (not
    ``T``) and delivers whole-share volume as a number carrying a fractional
    tail from server-side float summation, e.g. ``39269147.943173``. The
    storage contract is whole shares, so the value is rounded to the nearest
    whole share at the adapter boundary. All prices stay exact ``Decimal``.
    """
    body = (
        b'{"status":"OK","results":['
        b'{"v":39269147.943173,"vw":333.6407,"o":334.79,"c":333.08,'
        b'"h":335.5,"l":331.34,"t":1789358400000,"n":1032294},'
        b'{"v":31748183.086569,"vw":330.4877,"o":330.135,"c":331.34,'
        b'"h":331.78,"l":328.35,"t":1789444800000,"n":688356}]'
        b"}"
    )
    client = _client(lambda _r: httpx.Response(200, content=body))

    result = await client.get_daily_bars(
        "AAPL",
        date(2026, 9, 14),
        date(2026, 9, 15),
        venue=XNAS,
        price_basis=PriceBasis.SPLIT_ADJUSTED,
    )

    first, second = result.bars
    assert [b.trading_session_date for b in (first, second)] == [
        date(2026, 9, 14),
        date(2026, 9, 15),
    ]
    # 39269147.943173 rounds up to the nearest whole share; the second bar's
    # .086569 tail rounds down.
    assert (first.volume, second.volume) == (39269148, 31748183)
    assert first.trades == 1032294
    assert first.vwap == Decimal("333.6407")
    assert first.open == Decimal("334.79")
    assert first.close == Decimal("333.08")


@pytest.mark.anyio
async def test_session_date_uses_the_venue_timezone() -> None:
    """A 00:00Z timestamp is the previous session in New York.

    This is the conversion that decides which row a price lands on, so it is
    pinned here rather than trusted to the mapper.
    """
    # 2025-09-04T00:00:00Z. In New York that is 2025-09-03T20:00, so the session
    # it names is the 3rd.
    body = b'{"status":"OK","results":[{"t":1756944000000,"o":1,"h":2,"l":0.5,"c":1.5,"v":10}]}'
    client = _client(lambda _r: httpx.Response(200, content=body))

    result = await client.get_daily_bars(
        "AAPL",
        date(2025, 9, 1),
        date(2025, 9, 5),
        venue=XNAS,
        price_basis=PriceBasis.SPLIT_ADJUSTED,
    )
    assert result.bars[0].trading_session_date == date(2025, 9, 3)


@pytest.mark.anyio
async def test_session_date_flips_at_local_midnight_not_utc_midnight() -> None:
    """The calendar day turns at 00:00 in New York, which is 04:00/05:00 UTC.

    ``2025-09-04T03:59:59Z`` is still ``2025-09-03T23:59 EDT`` and must file to
    the 3rd; a print one second later files to the 4th.
    """
    prev = _ms(datetime(2025, 9, 4, 3, 59, 59, tzinfo=UTC))
    curr = _ms(datetime(2025, 9, 4, 4, 0, 0, tzinfo=UTC))
    body = _json(
        {
            "status": "OK",
            "results": [
                {"t": prev, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10},
                {"t": curr, "o": 2, "h": 3, "l": 1, "c": 2.5, "v": 20},
            ],
        }
    )
    client = _client(lambda _r: body)
    result = await client.get_daily_bars(
        "AAPL",
        date(2025, 9, 3),
        date(2025, 9, 4),
        venue=XNAS,
        price_basis=PriceBasis.SPLIT_ADJUSTED,
    )
    assert [b.trading_session_date for b in result.bars] == [
        date(2025, 9, 3),
        date(2025, 9, 4),
    ]


@pytest.mark.anyio
async def test_session_date_is_stable_across_the_fall_back_transition() -> None:
    """A bar printed over the DST rollover must keep its calendar date.

    ``2025-11-02`` is the fall-back date. ``05:30:00Z`` is ``01:30 EDT`` (before
    the change) and ``07:00:00Z`` is ``02:00 EST`` (after it): both are the 2nd in
    New York, and neither may drift into the 1st or the 3rd.
    """
    before = _ms(datetime(2025, 11, 2, 5, 30, 0, tzinfo=UTC))
    after = _ms(datetime(2025, 11, 2, 7, 0, 0, tzinfo=UTC))
    body = _json(
        {
            "status": "OK",
            "results": [
                {"t": before, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10},
                {"t": after, "o": 2, "h": 3, "l": 1, "c": 2.5, "v": 20},
            ],
        }
    )
    client = _client(lambda _r: body)
    result = await client.get_daily_bars(
        "AAPL",
        date(2025, 11, 2),
        date(2025, 11, 2),
        venue=XNAS,
        price_basis=PriceBasis.SPLIT_ADJUSTED,
    )
    assert [b.trading_session_date for b in result.bars] == [date(2025, 11, 2)] * 2


def _ms(instant: datetime) -> int:
    """Epoch milliseconds for a UTC instant, so tests never hand-write millis."""
    return int(instant.timestamp() * 1000)


# ── failure classification ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_unknown_ticker_is_an_empty_answer_not_an_error() -> None:
    """A 404 means "no such symbol", which must not raise.

    Raising would turn a routine reference refresh over a mixed basket into a
    failed run, and would make the caller distinguish 'missing' by catching an
    exception.
    """
    client = _client(lambda _r: httpx.Response(404, content=b'{"status":"NOT_FOUND"}'))
    result = await client.get_ticker_details("NOSUCH")
    assert result.reference is None


@pytest.mark.anyio
async def test_auth_failure_is_classified_as_authentication() -> None:
    """A 401 must be distinguishable from a transport blip.

    The CLI treats it as a configuration problem -- do not retry, tell the
    operator to set the key.
    """
    client = _client(lambda _r: httpx.Response(401, content=b'{"status":"UNAUTHORIZED"}'))
    with pytest.raises(ProviderAuthenticationError):
        await client.get_ticker_details("AAPL")


@pytest.mark.anyio
async def test_forbidden_is_authentication_not_transient() -> None:
    """403 is the same class of problem as 401 and must never be retried."""
    client = _client(lambda _r: httpx.Response(403, content=b'{"status":"FORBIDDEN"}'))
    with pytest.raises(ProviderAuthenticationError):
        await client.get_ticker_details("AAPL")


@pytest.mark.anyio
async def test_rate_limit_is_classified_as_rate_limit() -> None:
    client = _client(lambda _r: httpx.Response(429, content=b"{}"), max_retries=0)
    with pytest.raises(ProviderRateLimitError):
        await client.get_ticker_details("AAPL")


@pytest.mark.anyio
async def test_rate_limit_retry_uses_retry_after_when_given() -> None:
    """``Retry-After`` must win over the calculated backoff on a 429."""
    retried: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, content=b"{}", headers={"Retry-After": "2.5"})

    client = _client(handler, max_retries=1, sleep=record_sleep(retried))
    with pytest.raises(ProviderRateLimitError):
        await client.get_ticker_details("AAPL")
    assert retried[0] == pytest.approx(2.5), "the retry wait must honour Retry-After"
    assert len(retried) >= 2, "the 429 must also hold the retry attempt itself"


@pytest.mark.anyio
async def test_rate_limit_retry_after_is_capped() -> None:
    """A hostile ``Retry-After`` must not stall ingestion for hours."""
    awaited: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, content=b"{}", headers={"Retry-After": "3600"})

    client = _client(handler, max_retries=1, sleep=record_sleep(awaited))
    with pytest.raises(ProviderRateLimitError):
        await client.get_ticker_details("AAPL")
    assert awaited[0] == pytest.approx(60.0), "wait must be capped at 60s"


@pytest.mark.anyio
async def test_bad_request_is_not_retried() -> None:
    """A 400 is a permanent refusal; retrying it cannot change the answer."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(400, content=b'{"status":"ERROR"}')

    client = _client(handler)
    with pytest.raises(ProviderResponseError):
        await client.get_ticker_details("AAPL")
    assert len(calls) == 1


@pytest.mark.anyio
async def test_server_error_is_retried_then_raised() -> None:
    """A 5xx is transient, so it is retried before being given up on."""
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, content=b'{"status":"UNAVAILABLE"}')

    client = _client(handler, max_retries=2)
    with pytest.raises(ProviderServerError):
        await client.get_ticker_details("AAPL")
    assert len(calls) == 3  # initial attempt plus two retries


@pytest.mark.parametrize("status", [500, 502, 504])
@pytest.mark.anyio
async def test_other_server_failures_are_retried_then_raised(status: int) -> None:
    """500 and the other 5xx families must follow the same retry path as 503."""
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(status, content=b"{}")

    client = _client(handler, max_retries=1)
    with pytest.raises(ProviderServerError):
        await client.get_ticker_details("AAPL")
    assert len(calls) == 2


@pytest.mark.anyio
async def test_transport_timeout_is_a_transport_failure() -> None:
    """A read timeout must surface as a transport error, not a 500 or a hang.

    The timeout is exercised through MockTransport, which applies the configured
    timeouts -- so this also proves the client installed the explicit per-phase
    timeouts rather than the all-or-nothing default.
    """

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    client = _client(handler, max_retries=0)
    with pytest.raises(ProviderTransportError, match="ReadTimeout"):
        await client.get_ticker_details("AAPL")


@pytest.mark.anyio
async def test_protocol_error_is_not_classified_as_transient() -> None:
    """A peer that does not speak HTTP should fail immediately, not retry."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ProtocolError("malformed HTTP")

    calls: list[int] = []

    async def counting_sleep(_seconds: float) -> None:
        calls.append(1)

    client = _client(handler, sleep=counting_sleep, max_retries=3)
    with pytest.raises(ProviderTransportError):
        await client.get_ticker_details("AAPL")
    assert calls == [], "a protocol error must never be retried"


@pytest.mark.anyio
async def test_malformed_success_envelope_is_refused() -> None:
    """A 200 whose body is an error envelope must not read as empty.

    Treating a missing ``results`` as "no data" would silently drop a range, which
    is the worst possible failure: no error, no rows, no explanation.
    """
    client = _client(lambda _r: _json({"status": "ERROR", "error": "universe not loaded"}))
    with pytest.raises(ProviderResponseError):
        await client.get_daily_bars(
            "AAPL",
            date(2026, 9, 1),
            date(2026, 9, 5),
            venue=XNAS,
            price_basis=PriceBasis.SPLIT_ADJUSTED,
        )


@pytest.mark.anyio
async def test_unparseable_json_is_refused() -> None:
    client = _client(lambda _r: httpx.Response(200, content=b"not json at all"))
    with pytest.raises(ProviderResponseError):
        await client.get_ticker_details("AAPL")


@pytest.mark.anyio
async def test_results_of_the_wrong_type_is_refused() -> None:
    client = _client(lambda _r: _json({"status": "OK", "results": {"not": "a list"}}))
    with pytest.raises(ProviderResponseError):
        await client.get_daily_bars(
            "AAPL",
            date(2026, 9, 1),
            date(2026, 9, 5),
            venue=XNAS,
            price_basis=PriceBasis.SPLIT_ADJUSTED,
        )


# ── pagination ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_pagination_follows_next_url_and_captures_every_page() -> None:
    """Every page must be captured, and paging must stop when the provider stops.

    A multi-year range is hundreds of pages; capturing only the first would make
    the run unreproducible from the evidence on disk.
    """
    pages = [
        {
            "status": "OK",
            "results": [{"t": 1_757_164_800_000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 1}],
            "next_url": "/v2/aggs/ticker/AAPL/range/1/day/2026-09-01/2026-09-05?page=2",
        },
        {
            "status": "OK",
            "results": [{"t": 1_757_251_200_000, "o": 2, "h": 3, "l": 1, "c": 2.5, "v": 2}],
        },
    ]
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            request.url.path + (f"?{request.url.query.decode()}" if request.url.query else "")
        )
        return _json(pages[len(seen) - 1])

    client = _client(handler)
    result = await client.get_daily_bars(
        "AAPL",
        date(2026, 9, 1),
        date(2026, 9, 5),
        venue=XNAS,
        price_basis=PriceBasis.SPLIT_ADJUSTED,
    )

    assert len(result.bars) == 2
    assert len(result.captures) == 2, "each page must be captured"
    assert "page=2" in seen[1]


@pytest.mark.anyio
async def test_bars_are_sorted_by_session_date() -> None:
    """A caller must not depend on the provider's ordering.

    Two runs over the same range must present the writer with identical input, or
    an unchanged re-run would look like a change.
    """
    late = 1_757_251_200_000
    early = 1_757_164_800_000
    body = _json(
        {
            "status": "OK",
            "results": [
                {"t": late, "o": 2, "h": 3, "l": 1, "c": 2.5, "v": 2},
                {"t": early, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 1},
            ],
        }
    )
    client = _client(lambda _r: body)
    result = await client.get_daily_bars(
        "AAPL",
        date(2026, 9, 1),
        date(2026, 9, 5),
        venue=XNAS,
        price_basis=PriceBasis.SPLIT_ADJUSTED,
    )
    assert [b.trading_session_date for b in result.bars] == sorted(
        b.trading_session_date for b in result.bars
    )


@pytest.mark.anyio
async def test_pagination_loop_is_refused() -> None:
    """A provider pointing at a page it already served must not spin forever."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return _json(
            {
                "status": "OK",
                "results": [{"t": 1_757_164_800_000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 1}],
                "next_url": "/v2/aggs/ticker/AAPL/range/1/day/2026-09-01/2026-09-05?page=2",
            }
        )

    client = _client(handler)
    with pytest.raises(ProviderResponseError, match=r"pagination loop"):
        await client.get_daily_bars(
            "AAPL",
            date(2026, 9, 1),
            date(2026, 9, 5),
            venue=XNAS,
            price_basis=PriceBasis.SPLIT_ADJUSTED,
        )


@pytest.mark.anyio
async def test_page_ceiling_is_enforced() -> None:
    """A range larger than the ceiling must fail loudly, not truncate silently."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return _json(
            {
                "status": "OK",
                "results": [{"t": 1_757_164_800_000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 1}],
                "next_url": "/v2/aggs/ticker/AAPL/range/1/day/2026-09-01/2026-09-05?page=next",
            }
        )

    client = _client(handler, max_pages=2)
    with pytest.raises(ProviderResponseError, match=r"max_pages|without exhausting"):
        await client.get_daily_bars(
            "AAPL",
            date(2026, 9, 1),
            date(2026, 9, 5),
            venue=XNAS,
            price_basis=PriceBasis.SPLIT_ADJUSTED,
        )


@pytest.mark.anyio
async def test_next_url_cannot_redirect_the_api_key_to_another_host() -> None:
    """A provider-supplied next_url must not be followed to an arbitrary host.

    The next page is requested with the API-key header attached. Following an
    absolute URL from the response would hand that key to whoever chose the host.
    """
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        first = len(hosts) == 1
        return _json(
            {
                "status": "OK",
                "results": [{"t": 1_757_164_800_000, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 1}],
                "next_url": "https://evil.invalid/steal?page=2" if first else None,
            }
        )

    client = _client(handler)
    await client.get_daily_bars(
        "AAPL",
        date(2026, 9, 1),
        date(2026, 9, 5),
        venue=XNAS,
        price_basis=PriceBasis.SPLIT_ADJUSTED,
    )
    assert set(hosts) == {"api.massive.com"}, "the key must never leave the configured host"


# ── configuration ───────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_missing_key_fails_before_any_request() -> None:
    """A missing key is a local error, not a 401 round trip."""
    calls: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=b"{}")

    config = MassiveConfig(api_key=None, base_url="https://api.massive.com")
    MassiveClient(config, transport=httpx.MockTransport(record))
    with pytest.raises(ProviderConfigurationError, match=r"QF_MASSIVE_API_KEY"):
        config.require_api_key()
    assert calls == []


def test_repr_never_shows_the_key() -> None:
    """A repr ends up in tracebacks and logs, so it must not carry the key."""
    client = MassiveClient(
        MassiveConfig(api_key="super-secret-value", base_url="https://api.massive.com")
    )
    assert "super-secret-value" not in repr(client)


def test_config_pacing_defaults_disable_rate_limiting_but_keep_a_real_backoff() -> None:
    """``MassiveConfig``'s own defaults, not the deployed ``Settings`` defaults.

    ``min_request_interval`` defaults to ``0.0`` here specifically so a config
    built with no pacing in mind (as most of this file's tests do) does not
    silently start throttling. ``retry_base_delay`` still defaults to a real
    value, because a client with no retry delay at all is not a sane default
    for anything that talks to a real API.
    """
    config = MassiveConfig(api_key="k", base_url="https://api.massive.com")
    assert config.min_request_interval == 0.0
    assert config.retry_base_delay == 0.5


def test_configured_pacing_reaches_the_rate_limiter_and_backoff() -> None:
    """``QF_MASSIVE_MIN_REQUEST_INTERVAL_SECONDS``/``..._RETRY_BASE_DELAY_SECONDS``.

    Reproduces the drift this pins: those two ``Settings`` fields used to be
    validated but never actually reached ``MassiveClient`` -- the client always
    ran with no pacing and a hard-coded backoff, regardless of configuration.
    Asserted on the client's actual collaborators, not merely on the config
    object, so a future refactor that stops reading these fields at
    construction would be caught here too.
    """
    config = MassiveConfig(
        api_key="k",
        base_url="https://api.massive.com",
        retry_base_delay=1.75,
        min_request_interval=0.8,
    )
    client = MassiveClient(config)

    assert isinstance(client._limiter, RateLimiter)
    assert client._limiter._min_interval == 0.8
    assert isinstance(client._backoff, BackoffPolicy)
    assert client._backoff.base_delay == 1.75
    assert client._backoff.max_retries == config.max_retries


def test_an_injected_rate_limiter_and_backoff_still_override_config() -> None:
    """Explicit collaborators -- what every other test in this file relies
    on to avoid real sleeps -- must still win over whatever the config says.
    """
    config = MassiveConfig(
        api_key="k",
        base_url="https://api.massive.com",
        retry_base_delay=9.0,
        min_request_interval=9.0,
    )
    injected_limiter = RateLimiter(0.0)
    injected_backoff = BackoffPolicy(max_retries=1, base_delay=0.0)

    client = MassiveClient(config, rate_limiter=injected_limiter, backoff=injected_backoff)

    assert client._limiter is injected_limiter
    assert client._backoff is injected_backoff


def test_unmapped_exchange_code_is_refused() -> None:
    """An unknown venue code must not be silently treated as Nasdaq.

    The venue decides the timezone, which decides the session date. Guessing would
    misdate every bar for that instrument, so an unrecognised code has to be an
    error rather than a default.
    """
    with pytest.raises(UnsupportedExchangeError):
        resolve_venue("ZZZZ")


# ── price basis: request mapping, stamping, response validation ─────────────

_ONE_BAR = {"t": 1_756_944_000_000, "o": 100, "h": 105, "l": 99, "c": 104, "v": 1000}


def _bars_document(**extra: object) -> dict[str, object]:
    return {"status": "OK", "results": [_ONE_BAR], **extra}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("basis", "expected_parameter"),
    [(PriceBasis.SPLIT_ADJUSTED, "true"), (PriceBasis.AS_TRADED, "false")],
)
async def test_price_basis_maps_to_the_adjusted_request_parameter(
    basis: PriceBasis, expected_parameter: str
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["adjusted"])
        return _json(_bars_document())

    result = await _client(handler).get_daily_bars(
        "AAPL", date(2026, 9, 1), date(2026, 9, 5), venue=XNAS, price_basis=basis
    )

    assert seen == [expected_parameter]
    assert result.captures[0].request_parameters["adjusted"] == expected_parameter


@pytest.mark.anyio
@pytest.mark.parametrize("basis", list(PriceBasis))
async def test_every_returned_bar_carries_the_requested_basis(basis: PriceBasis) -> None:
    echoed = basis is PriceBasis.SPLIT_ADJUSTED
    client = _client(lambda _r: _json(_bars_document(adjusted=echoed)))

    result = await client.get_daily_bars(
        "AAPL", date(2026, 9, 1), date(2026, 9, 5), venue=XNAS, price_basis=basis
    )

    assert [bar.price_basis for bar in result.bars] == [basis]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("basis", "stated"),
    [(PriceBasis.SPLIT_ADJUSTED, False), (PriceBasis.AS_TRADED, True)],
)
async def test_a_response_basis_that_contradicts_the_request_is_refused(
    basis: PriceBasis, stated: bool
) -> None:
    client = _client(lambda _r: _json(_bars_document(adjusted=stated)))

    with pytest.raises(ProviderResponseError, match="refusing to ingest"):
        await client.get_daily_bars(
            "AAPL", date(2026, 9, 1), date(2026, 9, 5), venue=XNAS, price_basis=basis
        )


@pytest.mark.anyio
@pytest.mark.parametrize("basis", list(PriceBasis))
@pytest.mark.parametrize("missing_or_odd", ["absent", "string", "null"])
async def test_an_absent_or_non_boolean_adjusted_field_does_not_block_ingestion(
    basis: PriceBasis, missing_or_odd: str
) -> None:
    extra: dict[str, object] = {}
    if missing_or_odd == "string":
        extra["adjusted"] = "true"
    elif missing_or_odd == "null":
        extra["adjusted"] = None
    client = _client(lambda _r: _json(_bars_document(**extra)))

    result = await client.get_daily_bars(
        "AAPL", date(2026, 9, 1), date(2026, 9, 5), venue=XNAS, price_basis=basis
    )

    assert len(result.bars) == 1


@pytest.mark.anyio
async def test_a_mismatch_on_a_later_page_is_also_refused() -> None:
    pages = iter(
        [
            _bars_document(adjusted=True, next_url="https://api.massive.com/v2/aggs/next?cursor=a"),
            _bars_document(adjusted=False),
        ]
    )
    client = _client(lambda _r: _json(next(pages)))

    with pytest.raises(ProviderResponseError, match="refusing to ingest"):
        await client.get_daily_bars(
            "AAPL",
            date(2026, 9, 1),
            date(2026, 9, 5),
            venue=XNAS,
            price_basis=PriceBasis.SPLIT_ADJUSTED,
        )


@pytest.mark.anyio
async def test_a_next_url_that_drops_the_adjusted_parameter_is_pinned() -> None:
    """The provider's next_url is not trusted to carry the basis: it is added back."""
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("adjusted"))
        if len(seen) == 1:
            return _json(
                _bars_document(
                    next_url="https://api.massive.com/v2/aggs/ticker/AAPL/range/1/day/a/b?cursor=c1"
                )
            )
        return _json(_bars_document())

    result = await _client(handler).get_daily_bars(
        "AAPL",
        date(2026, 9, 1),
        date(2026, 9, 5),
        venue=XNAS,
        price_basis=PriceBasis.AS_TRADED,
    )

    assert seen == ["false", "false"]
    assert {bar.price_basis for bar in result.bars} == {PriceBasis.AS_TRADED}


@pytest.mark.anyio
async def test_a_next_url_carrying_a_different_basis_is_refused() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _json(
            _bars_document(
                next_url="https://api.massive.com/v2/aggs/ticker/AAPL/range/1/day/a/b?cursor=c&adjusted=true"
            )
        )

    with pytest.raises(ProviderResponseError, match="refusing to follow"):
        await _client(handler).get_daily_bars(
            "AAPL",
            date(2026, 9, 1),
            date(2026, 9, 5),
            venue=XNAS,
            price_basis=PriceBasis.AS_TRADED,
        )


@pytest.mark.anyio
async def test_a_next_url_with_the_matching_basis_is_followed_unchanged() -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("adjusted"))
        if len(seen) == 1:
            return _json(
                _bars_document(next_url="https://api.massive.com/v2/aggs/n?cursor=c1&adjusted=true")
            )
        return _json(_bars_document())

    await _client(handler).get_daily_bars(
        "AAPL",
        date(2026, 9, 1),
        date(2026, 9, 5),
        venue=XNAS,
        price_basis=PriceBasis.SPLIT_ADJUSTED,
    )

    assert seen == ["true", "true"]


@pytest.mark.anyio
async def test_a_basis_mismatch_error_carries_the_offending_raw_capture() -> None:
    body = json.dumps(_bars_document(adjusted=False)).encode()
    client = _client(lambda _r: httpx.Response(200, content=body))

    with pytest.raises(ProviderBasisMismatchError) as caught:
        await client.get_daily_bars(
            "AAPL",
            date(2026, 9, 1),
            date(2026, 9, 5),
            venue=XNAS,
            price_basis=PriceBasis.SPLIT_ADJUSTED,
        )

    captures = caught.value.captures
    assert [capture.body for capture in captures] == [body], "the offending bytes are evidence"
    assert "test-key-0123456789" not in json.dumps(
        [dict(capture.request_parameters) for capture in captures]
    )
    assert isinstance(caught.value, ProviderResponseError), "still the same failure class"


@pytest.mark.anyio
async def test_a_next_page_mismatch_carries_every_page_fetched_so_far() -> None:
    pages = iter(
        [
            _bars_document(adjusted=True, next_url="https://api.massive.com/v2/aggs/n?cursor=c"),
            _bars_document(adjusted=False),
        ]
    )
    client = _client(lambda _r: _json(next(pages)))

    with pytest.raises(ProviderBasisMismatchError) as caught:
        await client.get_daily_bars(
            "AAPL",
            date(2026, 9, 1),
            date(2026, 9, 5),
            venue=XNAS,
            price_basis=PriceBasis.SPLIT_ADJUSTED,
        )

    assert len(caught.value.captures) == 2


# ── NKE regression: a provider vwap outside the bar's own range (11.2C/11.2E) ──

NYSE = ResolvedVenue(mic="XNYS", name="New York Stock Exchange", timezone_name="America/New_York")

#: The observed ``/v2/aggs`` record for NKE 2026-10-01 (adjusted=true) and the three sessions after
#: it, exact values. The 2026-10-01 ``vw`` (34.8298) is BELOW that day's low (35.02): Massive's
#: documented aggregate rules let a trade update volume without updating the bar's high/low, and
#: nothing in the contract places ``vw`` inside [low, high]. See
#: docs/production-validation-design.md section 20 and docs/market-data-repair-design.md section 3.
NKE_AGGREGATES: dict[str, object] = {
    "ticker": "NKE",
    "queryCount": 4,
    "resultsCount": 4,
    "adjusted": True,
    "status": "OK",
    "results": [
        {
            "v": 112413524.118262,
            "vw": 34.8298,
            "o": 35.45,
            "c": 35.15,
            "h": 36.235,
            "l": 35.02,
            "t": 1790827200000,
            "n": 560588,
        },
        {
            "v": 142717008.024328,
            "vw": 33.1565,
            "o": 32.553,
            "c": 33.87,
            "h": 33.97,
            "l": 31.97,
            "t": 1790913600000,
            "n": 693009,
        },
        {
            "v": 69574914.414271,
            "vw": 33.4753,
            "o": 33.82,
            "c": 33.96,
            "h": 34.12,
            "l": 32.76,
            "t": 1791172800000,
            "n": 348365,
        },
        {
            "v": 50148121.392537,
            "vw": 34.2191,
            "o": 33.7,
            "c": 34.61,
            "h": 34.62,
            "l": 33.5,
            "t": 1791259200000,
            "n": 256686,
        },
    ],
}


@pytest.mark.anyio
async def test_the_observed_nke_aggregate_with_vwap_below_the_low_is_accepted_exactly() -> None:
    """The four observed NKE sessions parse whole: a provider ``vw`` is not asserted in-range.

    Before 11.2E the canonical bar rejected the 2026-10-01 record (``vw`` 34.8298 < low 35.02)
    and the whole range failed. ``vw`` is now carried as the provider reported it.
    """
    client = _client(lambda _r: _json(NKE_AGGREGATES))

    result = await client.get_daily_bars(
        "NKE",
        date(2026, 10, 1),
        date(2026, 10, 6),
        venue=NYSE,
        price_basis=PriceBasis.SPLIT_ADJUSTED,
    )

    assert [bar.trading_session_date for bar in result.bars] == [
        date(2026, 10, 1),
        date(2026, 10, 2),
        date(2026, 10, 5),
        date(2026, 10, 6),
    ]
    first = result.bars[0]
    assert first.vwap == Decimal("34.8298")
    assert first.vwap < first.low
    assert (first.open, first.high, first.low, first.close) == (
        Decimal("35.45"),
        Decimal("36.235"),
        Decimal("35.02"),
        Decimal("35.15"),
    )
    assert (first.volume, first.trades) == (112413524, 560588)
    assert {bar.price_basis for bar in result.bars} == {PriceBasis.SPLIT_ADJUSTED}
    assert [bar.vwap for bar in result.bars] == [
        Decimal("34.8298"),
        Decimal("33.1565"),
        Decimal("33.4753"),
        Decimal("34.2191"),
    ]
    assert len(result.captures) == 1


# ── raw evidence on failed bar responses (Option C: the range fails, the bytes are kept) ──


def _assert_no_credential_in(captures: Sequence[RawCapture]) -> None:
    """The key travels as a header only; no capture may carry it or an Authorization value."""
    for capture in captures:
        rendered = repr(capture.request_parameters) + repr(capture.endpoint)
        assert "test-key-0123456789" not in rendered
        assert "Bearer" not in rendered
        assert "Authorization" not in rendered
        assert "?" not in capture.endpoint


_BAD_OHLC_BAR = {"t": 1_757_164_800_000, "o": 110, "h": 105, "l": 99, "c": 104, "v": 1}


@pytest.mark.anyio
async def test_an_invalid_bar_payload_error_carries_the_response_bytes() -> None:
    """A row failing canonical validation fails the range, but its response is evidence."""
    body = json.dumps({"status": "OK", "results": [_ONE_BAR, _BAD_OHLC_BAR]}).encode()
    client = _client(lambda _r: httpx.Response(200, content=body))

    with pytest.raises(ProviderPayloadError, match="open=110 is outside") as caught:
        await client.get_daily_bars(
            "AAPL", date(2026, 9, 1), date(2026, 9, 5), venue=XNAS, price_basis=PriceBasis.AS_TRADED
        )

    captures = caught.value.captures
    assert [capture.body for capture in captures] == [body]
    assert [capture.dataset for capture in captures] == ["daily-bars"]
    assert captures[0].endpoint == "/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}"
    assert dict(captures[0].request_parameters) == {
        "ticker": "AAPL",
        "start": "2026-09-01",
        "end": "2026-09-05",
        "adjusted": "false",
    }
    _assert_no_credential_in(captures)


@pytest.mark.anyio
async def test_a_malformed_dto_row_payload_error_carries_the_response_bytes() -> None:
    body = json.dumps({"status": "OK", "results": [{"t": "not-a-time", "o": 1}]}).encode()
    client = _client(lambda _r: httpx.Response(200, content=body))

    with pytest.raises(ProviderPayloadError) as caught:
        await client.get_daily_bars(
            "AAPL", date(2026, 9, 1), date(2026, 9, 5), venue=XNAS, price_basis=PriceBasis.AS_TRADED
        )

    assert [capture.body for capture in caught.value.captures] == [body]


@pytest.mark.anyio
async def test_a_missing_results_response_error_carries_the_response_bytes() -> None:
    body = json.dumps({"status": "ERROR", "error": "universe not loaded"}).encode()
    client = _client(lambda _r: httpx.Response(200, content=body))

    with pytest.raises(ProviderResponseError, match="no 'results' member") as caught:
        await client.get_daily_bars(
            "AAPL",
            date(2026, 9, 1),
            date(2026, 9, 5),
            venue=XNAS,
            price_basis=PriceBasis.SPLIT_ADJUSTED,
        )

    assert [capture.body for capture in caught.value.captures] == [body]
    _assert_no_credential_in(caught.value.captures)


@pytest.mark.anyio
async def test_a_failure_on_page_two_carries_both_pages() -> None:
    """The evidence for a mid-pagination failure is every page received, not just the last."""
    first = json.dumps(
        _bars_document(next_url="https://api.massive.com/v2/aggs/ticker/AAPL/range/1/day/a/b?c=2")
    ).encode()
    second = json.dumps({"status": "OK", "results": [_BAD_OHLC_BAR]}).encode()
    bodies = iter([first, second])
    client = _client(lambda _r: httpx.Response(200, content=next(bodies)))

    with pytest.raises(ProviderPayloadError) as caught:
        await client.get_daily_bars(
            "AAPL",
            date(2026, 9, 1),
            date(2026, 9, 5),
            venue=XNAS,
            price_basis=PriceBasis.SPLIT_ADJUSTED,
        )

    assert [capture.body for capture in caught.value.captures] == [first, second]
    _assert_no_credential_in(caught.value.captures)


@pytest.mark.anyio
async def test_a_missing_results_on_page_two_carries_both_pages() -> None:
    first = json.dumps(
        _bars_document(next_url="https://api.massive.com/v2/aggs/ticker/AAPL/range/1/day/a/b?c=2")
    ).encode()
    second = json.dumps({"status": "ERROR"}).encode()
    bodies = iter([first, second])
    client = _client(lambda _r: httpx.Response(200, content=next(bodies)))

    with pytest.raises(ProviderResponseError) as caught:
        await client.get_daily_bars(
            "AAPL",
            date(2026, 9, 1),
            date(2026, 9, 5),
            venue=XNAS,
            price_basis=PriceBasis.SPLIT_ADJUSTED,
        )

    assert [capture.body for capture in caught.value.captures] == [first, second]


@pytest.mark.anyio
async def test_an_undecodable_200_still_carries_its_bytes_as_evidence() -> None:
    """The bytes are captured before the decode failure can destroy them."""
    body = b"<html>upstream proxy error</html>"
    client = _client(lambda _r: httpx.Response(200, content=body))

    with pytest.raises(ProviderResponseError) as caught:
        await client.get_daily_bars(
            "AAPL",
            date(2026, 9, 1),
            date(2026, 9, 5),
            venue=XNAS,
            price_basis=PriceBasis.SPLIT_ADJUSTED,
        )

    captures = caught.value.captures
    assert [capture.body for capture in captures] == [body]
    assert captures[0].dataset == "daily-bars"
    assert captures[0].endpoint == "/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}"
    assert dict(captures[0].request_parameters) == {
        "ticker": "AAPL",
        "start": "2026-09-01",
        "end": "2026-09-05",
        "adjusted": "true",
    }
    assert captures[0].provider_status is None
    _assert_no_credential_in(captures)


@pytest.mark.anyio
async def test_an_undecodable_page_two_carries_both_pages() -> None:
    first = json.dumps(
        _bars_document(next_url="https://api.massive.com/v2/aggs/ticker/AAPL/range/1/day/a/b?c=2")
    ).encode()
    second = b'{"status":"OK","results":[{"t":'
    bodies = iter([first, second])
    client = _client(lambda _r: httpx.Response(200, content=next(bodies)))

    with pytest.raises(ProviderResponseError) as caught:
        await client.get_daily_bars(
            "AAPL",
            date(2026, 9, 1),
            date(2026, 9, 5),
            venue=XNAS,
            price_basis=PriceBasis.SPLIT_ADJUSTED,
        )

    assert [capture.body for capture in caught.value.captures] == [first, second]


@pytest.mark.anyio
async def test_an_undecodable_200_on_other_endpoints_still_fails_without_captures() -> None:
    """Only the daily-bars path keeps undecodable bytes; reference behaviour is unchanged."""
    client = _client(lambda _r: httpx.Response(200, content=b"not json at all"))

    with pytest.raises(ProviderResponseError) as caught:
        await client.get_ticker_details("AAPL")

    assert caught.value.captures == ()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "error"),
    [(500, ProviderServerError), (429, ProviderRateLimitError), (401, ProviderAuthenticationError)],
)
async def test_a_non_200_bar_response_carries_no_captures(
    status: int, error: type[Exception]
) -> None:
    """A non-200 body is never captured; there is no evidence to attach."""
    client = _client(
        lambda _r: httpx.Response(status, content=b'{"echo":"anything"}'), max_retries=0
    )

    with pytest.raises(error) as caught:
        await client.get_daily_bars(
            "AAPL",
            date(2026, 9, 1),
            date(2026, 9, 5),
            venue=XNAS,
            price_basis=PriceBasis.SPLIT_ADJUSTED,
        )

    assert getattr(caught.value, "captures", ()) == ()


@pytest.mark.anyio
async def test_a_transport_failure_on_bars_carries_no_captures() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = _client(handler, max_retries=0)

    with pytest.raises(ProviderTransportError) as caught:
        await client.get_daily_bars(
            "AAPL",
            date(2026, 9, 1),
            date(2026, 9, 5),
            venue=XNAS,
            price_basis=PriceBasis.SPLIT_ADJUSTED,
        )

    assert caught.value.captures == ()
