"""The Finnhub REST adapter -- earnings calendar only.

Scope
-----
One endpoint, ``GET /calendar/earnings``, and nothing else. This adapter does
not implement :class:`~quantforge.providers.base.MarketDataProvider`; Massive
remains the reference/daily-bar provider. Finnhub exists solely to satisfy
:class:`~quantforge.providers.earnings.EarningsProvider`, because Massive's
own earnings endpoint (Benzinga) returns HTTP 403 under the configured plan.

Transport behaviour
--------------------
Deliberately mirrors the Massive adapter's discipline rather than inventing a
second convention:

* **Retries** are bounded, and only for genuinely transient outcomes: 429, the
  5xx family, 408, and transport failures. 401/403/404 are never retried.
* **Retry-After** wins over backoff when Finnhub sends it, capped the same way
  Massive's is.
* **Pacing** is enforced by a :class:`~quantforge.providers.pacing.RateLimiter`
  before every request, reusing the same collaborator Massive uses rather than
  a second pacing mechanism.

Secret handling
----------------
The API key is sent as an ``X-Finnhub-Token`` header -- documented by Finnhub
as the secure alternative to its own ``?token=`` query-parameter form, and the
only form consistent with this codebase's rule that a credential never enters
a URL, a query string, a log line or an exception. It is held in a
:class:`SecretStr` wrapper whose ``__repr__`` is redacted.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from typing import Final, NamedTuple, Self

import httpx2 as httpx
from pydantic import SecretStr

from quantforge.providers.base import RawCapture
from quantforge.providers.earnings import EarningsResult
from quantforge.providers.errors import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderServerError,
    ProviderTransportError,
)
from quantforge.providers.finnhub.dto import FinnhubEarningsCalendarResponse
from quantforge.providers.finnhub.mapper import to_canonical_earnings_event
from quantforge.providers.json_decimal import JsonValue, decode_json_document
from quantforge.providers.pacing import (
    BackoffPolicy,
    RateLimiter,
    Sleeper,
    parse_retry_after,
    retryable_statuses,
)

__all__ = ["PROVIDER_NAME", "FinnhubClient", "FinnhubConfig"]

logger = logging.getLogger(__name__)

#: Stable provider identity, persisted as ``DataSource.name`` and
#: ``IngestionRun.provider``. Must never change.
PROVIDER_NAME: Final[str] = "finnhub"

_EARNINGS_CALENDAR_PATH: Final[str] = "/calendar/earnings"

_USER_AGENT: Final[str] = "QuantForge/0.1 (+https://github.com/quantforge)"

#: Ceiling on how long a provider-supplied ``Retry-After`` may park ingestion,
#: matching the Massive adapter's own cap.
_MAX_HONOURED_RETRY_AFTER: Final[float] = 60.0

_TRANSIENT_TRANSPORT_ERRORS: Final[tuple[type[Exception], ...]] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)

HTTP_OK: Final[int] = 200
HTTP_UNAUTHORIZED: Final[int] = 401
HTTP_FORBIDDEN: Final[int] = 403
HTTP_TOO_MANY_REQUESTS: Final[int] = 429
HTTP_SERVER_ERROR_FLOOR: Final[int] = 500

_AUTH_STATUSES: Final[frozenset[int]] = frozenset({HTTP_UNAUTHORIZED, HTTP_FORBIDDEN})

_MAX_TICKER_LENGTH: Final[int] = 32


def _utc_now() -> datetime:
    """Default clock. Named so a test can tell it apart from an injected one."""
    return datetime.now(UTC)


def _wrap(value: str | None) -> SecretStr | None:
    if value is None or not value.strip():
        return None
    return SecretStr(value.strip())


class FinnhubConfig:
    """Everything the client needs, with the key wrapped so it cannot be printed.

    Deliberately a plain class, like ``MassiveConfig``: constructed once at
    the composition root, not a second settings model competing with
    ``Settings``.
    """

    __slots__ = (
        "api_key",
        "base_url",
        "max_retries",
        "min_request_interval",
        "request_timeout",
        "retry_base_delay",
    )

    def __init__(  # noqa: PLR0913 - one settings-derived field per provider limit
        self,
        *,
        api_key: SecretStr | str | None,
        base_url: str,
        request_timeout: float = 20.0,
        max_retries: int = 3,
        retry_base_delay: float = 0.5,
        min_request_interval: float = 0.25,
    ) -> None:
        self.api_key = api_key if isinstance(api_key, SecretStr) else _wrap(api_key)
        self.base_url = base_url.rstrip("/")
        self.request_timeout = request_timeout
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.min_request_interval = min_request_interval

    def __repr__(self) -> str:
        return (
            f"FinnhubConfig(base_url={self.base_url!r}, api_key={self.api_key!r}, "
            f"request_timeout={self.request_timeout!r}, max_retries={self.max_retries!r}, "
            f"retry_base_delay={self.retry_base_delay!r}, "
            f"min_request_interval={self.min_request_interval!r})"
        )

    def require_api_key(self) -> str:
        """Return the key, or fail with an actionable local error."""
        if self.api_key is None:
            message = (
                "QF_FINNHUB_API_KEY is not configured. Set it to a Finnhub API key to "
                "ingest earnings from Finnhub; every other part of the application, "
                "including Massive-based ingestion, works without it."
            )
            raise ProviderConfigurationError(message)
        return self.api_key.get_secret_value()


class _Response(NamedTuple):
    document: JsonValue
    raw: bytes


class FinnhubClient:
    """Async client for Finnhub's earnings calendar, scoped to one endpoint.

    Usage::

        async with FinnhubClient(config) as client:
            result = await client.get_earnings("AAPL", start, end)
    """

    def __init__(  # noqa: PLR0913 - every collaborator below is independently injectable
        self,
        config: FinnhubConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        rate_limiter: RateLimiter | None = None,
        backoff: BackoffPolicy | None = None,
        sleep: Sleeper | None = None,
        jitter: Callable[[float, float], float] = random.uniform,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._config = config
        self._now = now
        self._sleep: Sleeper = sleep if sleep is not None else asyncio.sleep
        self._jitter: Callable[[float, float], float] = jitter
        self._backoff = backoff or BackoffPolicy(
            max_retries=config.max_retries,
            base_delay=config.retry_base_delay,
        )
        self._limiter = rate_limiter or RateLimiter(config.min_request_interval)
        timeout = httpx.Timeout(
            connect=config.request_timeout,
            read=config.request_timeout,
            write=config.request_timeout,
            pool=config.request_timeout,
        )
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            timeout=timeout,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            transport=transport,
            follow_redirects=False,
        )

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying connection pool."""
        await self._client.aclose()

    # ── Public operations ──────────────────────────────────────────────────

    async def get_earnings(self, ticker: str, start: date, end: date) -> EarningsResult:
        """Fetch earnings-calendar entries for ``ticker`` over the inclusive range.

        Finnhub's ``/calendar/earnings`` is not paginated, so this is a single
        request. A symbol Finnhub knows nothing about, or that has no earnings
        in range, is a legitimate empty result, not an error.
        """
        symbol = _normalise_ticker(ticker)
        if end < start:
            message = f"end date {end} precedes start date {start}"
            raise ValueError(message)

        params: dict[str, str] = {
            "symbol": symbol,
            "from": start.isoformat(),
            "to": end.isoformat(),
        }
        response = await self._get_json(_EARNINGS_CALENDAR_PATH, params=params, dataset="earnings")
        capture = _capture(
            dataset="earnings",
            endpoint=_EARNINGS_CALENDAR_PATH,
            request_parameters=params,
            response=response,
            retrieved_at=self._now(),
        )

        envelope = _parse_earnings_calendar(response.document, symbol)
        events = [
            to_canonical_earnings_event(entry, identifier=symbol)
            for entry in envelope.earnings_calendar
        ]
        events.sort(key=lambda event: event.event_date)
        return EarningsResult(events=events, captures=(capture,))

    # ── Internals ──────────────────────────────────────────────────────────

    def _auth_headers(self) -> dict[str, str]:
        """Per-request auth header.

        Built per request, not set once on the client, for the same reason
        the Massive adapter does this: a client constructed without a key
        still exists (the application must boot), and the credential never
        sits in a shared header mapping a diagnostic dump could print.
        """
        return {"X-Finnhub-Token": self._config.require_api_key()}

    async def _get_json(
        self,
        path: str,
        *,
        params: Mapping[str, str],
        dataset: str,
    ) -> _Response:
        """Issue a GET, retrying transient failures, and decode it exactly."""
        headers = self._auth_headers()
        attempt = 0

        while True:
            attempt += 1
            await self._limiter.acquire(self._sleep)
            try:
                response = await self._client.get(
                    path, params=dict(params) or None, headers=headers
                )
            except _TRANSIENT_TRANSPORT_ERRORS as exc:
                if not self._backoff.should_retry(attempt):
                    raise ProviderTransportError(type(exc).__name__) from exc
                delay = self._backoff.delay_for(attempt, jitter=self._jitter)
                logger.warning(
                    "provider transport error on %s (%s); retry %d in %.2fs",
                    dataset,
                    type(exc).__name__,
                    attempt,
                    delay,
                )
                await self._sleep(delay)
                continue
            except httpx.HTTPError as exc:
                raise ProviderTransportError(type(exc).__name__) from exc

            if response.status_code == HTTP_OK:
                return _Response(document=_decode(response), raw=response.content)

            retry_after = _bounded_retry_after(response.headers.get("Retry-After"))
            if response.status_code == HTTP_TOO_MANY_REQUESTS:
                self._limiter.penalise(retry_after or self._backoff.delay_for(attempt))
            if response.status_code in retryable_statuses() and self._backoff.should_retry(attempt):
                delay = (
                    retry_after
                    if retry_after is not None
                    else self._backoff.delay_for(attempt, jitter=self._jitter)
                )
                logger.warning(
                    "provider returned %d for %s; retry %d in %.2fs",
                    response.status_code,
                    dataset,
                    attempt,
                    delay,
                )
                await self._sleep(delay)
                continue

            _raise_for_status(response.status_code, dataset)


# ── Module-level decoding helpers ───────────────────────────────────────────


def _normalise_ticker(ticker: str) -> str:
    candidate = ticker.strip().upper()
    if not candidate:
        message = "ticker must not be blank"
        raise ValueError(message)
    if not all(char.isalnum() or char in ".-" for char in candidate):
        message = f"ticker {ticker!r} contains characters that are not allowed"
        raise ValueError(message)
    if len(candidate) > _MAX_TICKER_LENGTH:
        message = f"ticker {ticker!r} is implausibly long"
        raise ValueError(message)
    return candidate


def _decode(response: httpx.Response) -> JsonValue:
    """Decode a 200 body with exact decimals."""
    try:
        return decode_json_document(response.content)
    except ValueError as exc:
        raise ProviderResponseError(str(exc)) from exc


def _parse_earnings_calendar(document: JsonValue, symbol: str) -> FinnhubEarningsCalendarResponse:
    """Validate the decoded envelope for one earnings-calendar request."""
    if not isinstance(document, dict):
        message = f"expected an object at the top level, got {type(document).__name__}"
        raise ProviderResponseError(message)
    try:
        return FinnhubEarningsCalendarResponse.model_validate(document)
    except Exception as exc:
        message = f"invalid earnings calendar for {symbol}: {exc}"
        raise ProviderResponseError(message) from exc


def _capture(
    *,
    dataset: str,
    endpoint: str,
    request_parameters: Mapping[str, str],
    response: _Response,
    retrieved_at: datetime,
) -> RawCapture:
    """Build a raw capture from the exact bytes that were received."""
    return RawCapture(
        provider=PROVIDER_NAME,
        dataset=dataset,
        endpoint=endpoint,
        request_parameters=dict(request_parameters),
        body=response.raw,
        retrieved_at=retrieved_at,
    )


def _bounded_retry_after(header: str | None) -> float | None:
    parsed = parse_retry_after(header)
    if parsed is None:
        return None
    return min(parsed, _MAX_HONOURED_RETRY_AFTER)


def _raise_for_status(status_code: int, dataset: str) -> None:
    """Translate a non-retryable status into a typed provider error."""
    if status_code in _AUTH_STATUSES:
        raise ProviderAuthenticationError(status_code)
    if status_code == HTTP_TOO_MANY_REQUESTS:
        raise ProviderRateLimitError()
    if status_code >= HTTP_SERVER_ERROR_FLOOR:
        raise ProviderServerError(status_code)
    message = f"provider rejected the {dataset} request (HTTP {status_code})"
    raise ProviderResponseError(message)
