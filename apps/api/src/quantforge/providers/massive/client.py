"""The Massive (formerly Polygon.io) REST adapter.

Scope
-----
US equity reference data and daily aggregates. Nothing else. The adapter is the
only module in the codebase that knows about ``/v2/aggs`` or ``primary_exchange``.

Transport behaviour
-------------------
* **Timeouts** are set explicitly and separately for connect, read, write and
  pool, rather than relying on httpx's all-or-nothing default. A generous
  connect budget is fine; a generous read budget on a stalled socket is not.
* **Retries** are bounded, and only for genuinely transient outcomes: 429, the
  5xx family, 408, and transport failures. 400/401/403/404 are never retried,
  because a repeated request cannot change the answer and retrying 401 risks
  throttling a key that is merely misconfigured.
* **Retry-After** wins over backoff when the provider sends it, capped so a
  hostile or buggy value cannot stall ingestion for hours.
* **Pacing** is enforced by a :class:`~quantforge.providers.pacing.RateLimiter`
  before every request, so the free tier is not hammered by a tight loop.
* **Pagination** follows the provider's ``next_url`` up to a configurable page
  ceiling, and detects a page that repeats, which is the signature of a cursor
  the provider failed to advance.

Secret handling
---------------
The API key is sent as an ``Authorization: Bearer`` header and never appears in a
URL, a query string, a log line, or an exception. It is held in a
:class:`SecretStr`-like wrapper whose ``__repr__`` is redacted, and every error
constructed here passes only a status code and a static message upward.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any, Final, NamedTuple, Self

import httpx2 as httpx
from pydantic import SecretStr

from quantforge.providers.base import (
    DailyBarsResult,
    RawCapture,
    TickerDetailsResult,
    VenueView,
)
from quantforge.providers.errors import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderNotFoundError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderServerError,
    ProviderTransportError,
)
from quantforge.providers.json_decimal import JsonValue, decode_json_document
from quantforge.providers.massive.dto import MassiveDailyBarDTO, MassiveTickerDetailsDTO
from quantforge.providers.massive.exchanges import VenueIdentity, resolve_venue
from quantforge.providers.massive.mapper import (
    to_canonical_daily_bar,
)
from quantforge.providers.pacing import (
    BackoffPolicy,
    RateLimiter,
    Sleeper,
    parse_retry_after,
    retryable_statuses,
)

__all__ = ["PROVIDER_NAME", "MassiveClient", "MassiveConfig"]

logger = logging.getLogger(__name__)

#: Stable provider identity, persisted as ``DataSource.name`` and
#: ``IngestionRun.provider``. Must never change: changing it would orphan every
#: row already attributed to this vendor.
PROVIDER_NAME: Final[str] = "massive"

#: Path templates, recorded in raw artefacts in place of concrete URLs so a
#: stored file can never accumulate a query string that grows a secret.
_TICKER_DETAILS_PATH: Final[str] = "/v3/reference/tickers/{ticker}"
_DAILY_BARS_PATH: Final[str] = "/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}"

#: Identifies the client to the provider and to anyone reading provider logs.
_USER_AGENT: Final[str] = "QuantForge/0.1 (+https://github.com/quantforge)"

#: The provider's documented maximum for the aggregate range endpoint. Sent
#: explicitly rather than relying on the default so the value is visible here if
#: it ever needs to change.
_DAILY_BARS_PAGE_LIMIT: Final[int] = 50_000

#: Ceiling on how long a provider-supplied ``Retry-After`` may park ingestion.
#: A provider asking for an hour is treated as a service problem, not a pacing
#: instruction, and backoff is used instead.
_MAX_HONOURED_RETRY_AFTER: Final[float] = 60.0

#: Transport failures worth another attempt. A protocol error is not: it means
#: the peer spoke something other than HTTP, and repeating will not fix it.
_TRANSIENT_TRANSPORT_ERRORS: Final[tuple[type[Exception], ...]] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)


#: HTTP statuses with specific handling, named so the branches below read as
#: intent rather than as bare numbers.
HTTP_OK: Final[int] = 200
HTTP_UNAUTHORIZED: Final[int] = 401
HTTP_FORBIDDEN: Final[int] = 403
HTTP_NOT_FOUND: Final[int] = 404
HTTP_TOO_MANY_REQUESTS: Final[int] = 429

#: Lowest status treated as a server-side fault. Everything at or above this is
#: a provider problem rather than a bad request, and so is retryable.
HTTP_SERVER_ERROR_FLOOR: Final[int] = 500

_AUTH_STATUSES: Final[frozenset[int]] = frozenset({HTTP_UNAUTHORIZED, HTTP_FORBIDDEN})

#: No real US ticker approaches this. A longer value is a malformed symbol, and
#: rejecting it before the request keeps a nonsense string out of the URL and out
#: of the raw-store path.
_MAX_TICKER_LENGTH: Final[int] = 32


def _utc_now() -> datetime:
    """Default clock. Named so a test can tell it apart from an injected one."""
    return datetime.now(UTC)


class MassiveConfig:
    """Everything the client needs, with the key wrapped so it cannot be printed.

    Deliberately a plain class rather than a pydantic model: it is constructed
    once at the composition root, and a settings model here would be a second
    source of truth for the same values.
    """

    __slots__ = (
        "api_key",
        "base_url",
        "max_pages",
        "max_retries",
        "min_request_interval",
        "request_timeout",
        "retry_base_delay",
    )

    def __init__(  # noqa: PLR0913 - one settings-derived field per provider limit; splitting them up would not simplify anything
        self,
        *,
        api_key: SecretStr | str | None,
        base_url: str,
        request_timeout: float = 20.0,
        max_retries: int = 3,
        max_pages: int = 50,
        retry_base_delay: float = 0.5,
        min_request_interval: float = 0.0,
    ) -> None:
        """Args:
        retry_base_delay: Base delay for :class:`~quantforge.providers.pacing.BackoffPolicy`,
            unless the caller injects its own policy into :class:`MassiveClient`.
        min_request_interval: Minimum seconds between requests for
            :class:`~quantforge.providers.pacing.RateLimiter`, unless the caller
            injects its own limiter. ``0.0`` -- the default here, not the
            deployed default -- disables pacing entirely, which matters for
            tests that construct a bare config with no rate limit in mind.
        """
        self.api_key = api_key if isinstance(api_key, SecretStr) else _wrap(api_key)
        self.base_url = base_url.rstrip("/")
        self.request_timeout = request_timeout
        self.max_retries = max_retries
        self.max_pages = max_pages
        self.retry_base_delay = retry_base_delay
        self.min_request_interval = min_request_interval

    def __repr__(self) -> str:
        # Redacted explicitly rather than relying on SecretStr alone, so that
        # even a future refactor to a plain string cannot leak it into a log.
        return (
            f"MassiveConfig(base_url={self.base_url!r}, api_key={self.api_key!r}, "
            f"request_timeout={self.request_timeout!r}, max_retries={self.max_retries!r}, "
            f"max_pages={self.max_pages!r}, retry_base_delay={self.retry_base_delay!r}, "
            f"min_request_interval={self.min_request_interval!r})"
        )

    def require_api_key(self) -> str:
        """Return the key, or fail with an actionable local error.

        Called at request time rather than at construction so that importing the
        application, serving ``/health``, and running the test suite all work with
        no credential configured at all.
        """
        if self.api_key is None:
            message = (
                "QF_MASSIVE_API_KEY is not configured. Set it to a Massive API key to "
                "run ingestion; every other part of the application works without it."
            )
            raise ProviderConfigurationError(message)
        return self.api_key.get_secret_value()


def _wrap(value: str | None) -> SecretStr | None:
    if value is None or not value.strip():
        return None
    return SecretStr(value.strip())


class MassiveClient:
    """Async client for the Massive REST API, scoped to US equities.

    Usage::

        async with MassiveClient(config) as client:
            details = await client.get_ticker_details("AAPL")

    The ``async with`` form is preferred: it releases the connection pool on
    exit, which matters for a CLI that opens one client and exits.
    """

    def __init__(  # noqa: PLR0913 - every collaborator below is independently injectable
        self,
        config: MassiveConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        rate_limiter: RateLimiter | None = None,
        backoff: BackoffPolicy | None = None,
        sleep: Sleeper | None = None,
        jitter: Callable[[float, float], float] = random.uniform,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        """Args:
        config: Credentials and limits. The key may be absent.
        transport: Injected for tests. ``None`` uses a real pooled connection.
        rate_limiter: Injected pacing. ``None`` disables spacing.
        backoff: Injected retry policy. ``None`` derives one from ``config``.
        sleep: Injected awaitable sleep, so tests never spend wall-clock time.
        jitter: Injected ``(low, high) -> factor`` for backoff.
        now: Injected clock used for ``retrieved_at``.
        """
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

    async def get_ticker_details(self, ticker: str) -> TickerDetailsResult:
        """Fetch reference details for one ticker.

        Returns a result whose ``reference`` is ``None`` for a symbol the
        provider does not have. A 404 is an answer, not a failure, so it is
        translated into an empty result rather than raised.
        """
        symbol = _normalise_ticker(ticker)
        params: dict[str, str] = {"ticker": symbol}
        captures: list[RawCapture] = []
        try:
            response = await self._get_json(
                _TICKER_DETAILS_PATH.format(ticker=symbol), params=params, dataset="reference"
            )
        except ProviderNotFoundError:
            return TickerDetailsResult(reference=None, captures=tuple(captures))
        captures.append(
            _capture(
                dataset="reference",
                endpoint=_TICKER_DETAILS_PATH,
                request_parameters=params,
                response=response,
                retrieved_at=self._now(),
            )
        )
        return TickerDetailsResult(
            reference=_parse_ticker_details(response.document, symbol),
            captures=tuple(captures),
        )

    async def get_daily_bars(
        self,
        ticker: str,
        start: date,
        end: date,
        *,
        venue: VenueView,
    ) -> DailyBarsResult:
        """Fetch daily aggregates for the inclusive range ``[start, end]``.

        Pages through ``next_url`` when the provider uses it. The returned bars
        are sorted by session date so downstream comparison is order-independent.

        ``venue`` is required -- see
        :meth:`~quantforge.providers.base.MarketDataProvider.get_daily_bars` for
        why it cannot be defaulted. A bars response carries no exchange code, so
        this is the only place the timezone can come from.
        """
        symbol = _normalise_ticker(ticker)
        if end < start:
            message = f"end date {end} precedes start date {start}"
            raise ValueError(message)

        path = _DAILY_BARS_PATH.format(ticker=symbol, start=start.isoformat(), end=end.isoformat())
        params: dict[str, str] = {
            "ticker": symbol,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "adjusted": "true",
        }

        captures: list[RawCapture] = []
        bars: list[MassiveDailyBarDTO] = []
        seen_pages: set[str] = set()
        next_path: str | None = path
        next_params: Mapping[str, str] = params

        # `_` because the page number is not needed: loop detection uses the
        # path, and the ceiling is the loop bound itself.
        for _ in range(1, self._config.max_pages + 1):
            if next_path is None:  # pragma: no cover - defensive; set below
                break
            if next_path in seen_pages:
                message = (
                    f"provider pagination loop: {next_path!r} was already fetched. "
                    "Refusing to request it again."
                )
                raise ProviderResponseError(message)
            seen_pages.add(next_path or "")

            page = await self._get_json(next_path, params=next_params, dataset="daily-bars")
            captures.append(
                _capture(
                    dataset="daily-bars",
                    endpoint=_DAILY_BARS_PATH,
                    request_parameters=params,
                    response=page,
                    retrieved_at=self._now(),
                )
            )

            results = _results_of(page.document)
            if results is None:
                break
            for element in results:
                bars.append(MassiveDailyBarDTO.from_provider(element, identifier=symbol))

            following = _next_url_of(page.document)
            if following is None:
                break
            next_path = following
            # A next_url carries its own query string; re-sending the original
            # params would duplicate them.
            next_params = {}
        else:
            message = (
                f"stopped after {self._config.max_pages} pages without exhausting the "
                "provider's pagination; raise QF_MASSIVE_MAX_PAGES if the range is "
                "legitimately larger"
            )
            raise ProviderResponseError(message)

        # Sorted by session date so a caller cannot depend on the provider's
        # ordering, and so two runs over the same range produce byte-identical
        # input to the writer.
        resolved = [to_canonical_daily_bar(bar, venue=venue, identifier=symbol) for bar in bars]
        resolved.sort(key=lambda bar: bar.trading_session_date)
        return DailyBarsResult(bars=resolved, captures=tuple(captures))

    # ── Internals ──────────────────────────────────────────────────────────

    def _auth_headers(self) -> dict[str, str]:
        """Per-request auth header.

        Built per request rather than set once on the client so that a client
        constructed without a key still exists (the application must boot), and
        so the credential never sits in a shared header mapping that a diagnostic
        dump of the client could print.
        """
        return {"Authorization": f"Bearer {self._config.require_api_key()}"}

    async def _get_json(
        self,
        path: str,
        *,
        params: Mapping[str, str],
        dataset: str,
    ) -> _Response:
        """Issue a GET, retrying transient failures, and decode it exactly.

        Returns both the decoded document and the exact bytes it came from. Both
        are needed: the document to interpret, and the bytes to preserve. Raises a
        :class:`~quantforge.providers.errors.ProviderError` subclass for
        everything else.
        """
        # Resolved once per call and sent as a header, never as a query parameter:
        # a URL can end up in a proxy log, a traceback, or a raw artefact, and a
        # header cannot.
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
                # Anything else in the hierarchy is a protocol-level failure, not
                # a blip: repeating it would not help.
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
    """Upper-case and validate a symbol before it reaches a URL or a path.

    The same routine guards the raw store's path construction, so a symbol
    containing ``/`` or ``..`` can never escape the artifact directory.
    """
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


class _Response(NamedTuple):
    """A decoded provider response together with the bytes it was decoded from.

    Both are kept because they answer different questions. ``document`` is what
    the provider *meant*; ``raw`` is what the provider *sent*. Mapping needs the
    first, and the raw store's whole purpose -- proving later exactly what was
    received -- needs the second.
    """

    document: JsonValue
    raw: bytes


def _decode(response: httpx.Response) -> JsonValue:
    """Decode a 200 body with exact decimals."""
    try:
        return decode_json_document(response.content)
    except ValueError as exc:
        raise ProviderResponseError(str(exc)) from exc


def _results_of(document: JsonValue) -> Sequence[JsonValue] | None:
    """Extract the ``results`` array, or ``None`` when the document has none.

    An empty ``results`` is a legitimate answer (no bars in range) and yields an
    empty list, which the caller treats as "stop paging". A *missing* ``results``
    on a 200 is different: it usually means an error envelope arrived with a
    success status, so it is reported rather than silently read as empty.
    """
    if not isinstance(document, dict):
        message = f"expected an object at the top level, got {type(document).__name__}"
        raise ProviderResponseError(message)
    results = document.get("results")
    if results is None:
        # A successful response always carries a `results` member, empty if there
        # is nothing to report. Its absence therefore means this is not the
        # response we asked for -- an error envelope delivered with a 200, or a
        # changed API shape. Reading it as "no data" would drop the range with no
        # error and no rows, which is the one failure a data pipeline cannot be
        # allowed to make silently.
        detail = _error_detail_of(document)
        message = f"provider response has no 'results' member{detail}"
        raise ProviderResponseError(message)
    if not isinstance(results, list):
        message = f"'results' must be an array, got {type(results).__name__}"
        raise ProviderResponseError(message)
    return results


def _error_detail_of(document: JsonValue) -> str:
    """Describe an error envelope, if the document is one.

    Used only to make a failure message actionable -- which field disagreed -- and
    never to decide success on its own. A status of ``OK`` still gets no
    exemption: the member is missing, and that is the finding.
    """
    if not isinstance(document, dict):
        return ""
    parts: list[str] = []
    status = document.get("status")
    if isinstance(status, str) and status.upper() not in {"OK", "SUCCESS"}:
        parts.append(f"status={status!r}")
    error = document.get("error") or document.get("message")
    if isinstance(error, str) and error:
        parts.append(f"error={error!r}")
    return f" ({', '.join(parts)})" if parts else ""


def _next_url_of(document: JsonValue) -> str | None:
    """Return the provider's next-page path, if it supplied one.

    Only the path and query are used, and the host is discarded: following a
    provider-supplied absolute URL verbatim would let a compromised or
    misconfigured response redirect authenticated requests -- including the API
    key -- at an arbitrary host. The path is resolved against our own base URL.
    """
    if not isinstance(document, dict):
        return None
    next_url = document.get("next_url")
    if next_url is None:
        return None
    if not isinstance(next_url, str):
        message = f"'next_url' must be a string, got {type(next_url).__name__}"
        raise ProviderResponseError(message)
    stripped = next_url.strip()
    if not stripped:
        return None
    parsed = httpx.URL(stripped)
    path = parsed.path
    query = parsed.query.decode("ascii")
    return f"{path}?{query}" if query else path


def _parse_ticker_details(document: JsonValue, symbol: str) -> MassiveTickerDetailsDTO:
    """Validate the ``results`` object of a ticker-details response."""
    if not isinstance(document, dict):
        message = f"expected an object at the top level, got {type(document).__name__}"
        raise ProviderResponseError(message)
    results = document.get("results")
    if results is None:
        message = f"ticker details for {symbol} returned no 'results'"
        raise ProviderResponseError(message)
    if not isinstance(results, dict):
        message = f"ticker details 'results' must be an object, got {type(results).__name__}"
        raise ProviderResponseError(message)
    try:
        return MassiveTickerDetailsDTO.model_validate(results)
    except Exception as exc:
        message = f"invalid ticker details for {symbol}: {exc}"
        raise ProviderResponseError(message) from exc


def _capture(
    *,
    dataset: str,
    endpoint: str,
    request_parameters: Mapping[str, str],
    response: _Response,
    retrieved_at: datetime,
) -> RawCapture:
    """Build a raw capture from the exact bytes that were received.

    The body is ``response.content`` verbatim. It is deliberately *not* a
    re-encoding of the decoded document, and this is worth being explicit about
    because the two look equivalent and are not:

    * whitespace and key order are part of the provider's actual transmission, and
      a re-encoding normalises both away, so the stored bytes would not be the
      bytes that arrived;
    * the SHA-256 in the raw store is a claim about the evidence. If that claim
      were computed over a re-encoding, it would attest to something the provider
      never sent, and there would be no way to detect a provider-side change in
      serialisation -- which is precisely the kind of change worth detecting.

    Interpreting the document and preserving the document are separate jobs, and
    only the first one is allowed to transform the bytes.
    """
    document = response.document
    status: str | None = None
    if isinstance(document, dict):
        raw_status = document.get("status")
        if isinstance(raw_status, str):
            status = raw_status
    return RawCapture(
        provider=PROVIDER_NAME,
        dataset=dataset,
        endpoint=endpoint,
        request_parameters=dict(request_parameters),
        body=response.raw,
        retrieved_at=retrieved_at,
        provider_status=status,
    )


def _bounded_retry_after(header: str | None) -> float | None:
    """Interpret ``Retry-After`` in seconds, refusing implausibly long waits."""
    parsed = parse_retry_after(header)
    if parsed is None:
        return None
    return min(parsed, _MAX_HONOURED_RETRY_AFTER)


def _raise_for_status(status_code: int, dataset: str) -> Any:
    """Translate a non-retryable status into a typed provider error.

    Returns ``NoReturn``; the annotation keeps mypy from assuming a fall-through
    into the success path.
    """
    if status_code in _AUTH_STATUSES:
        raise ProviderAuthenticationError(status_code)
    if status_code == HTTP_NOT_FOUND:
        raise ProviderNotFoundError(dataset)
    if status_code == HTTP_TOO_MANY_REQUESTS:
        raise ProviderRateLimitError()
    if status_code >= HTTP_SERVER_ERROR_FLOOR:
        raise ProviderServerError(status_code)
    # 400 and anything else unexpected: a permanent refusal from the provider.
    message = f"provider rejected the {dataset} request (HTTP {status_code})"
    raise ProviderResponseError(message)


def venue_for(provider_code: str | None) -> VenueIdentity:
    """Resolve a venue through the adapter's mapping table.

    Re-exported so the resolution service can obtain a venue identity without
    importing the adapter's internals or the mapping module directly.
    """
    return resolve_venue(provider_code)
