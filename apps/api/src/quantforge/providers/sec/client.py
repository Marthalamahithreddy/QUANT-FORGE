"""Async client for the SEC submissions API, scoped to filing metadata.

One endpoint: ``GET /submissions/CIK##########.json`` on ``data.sec.gov``. No key
exists; instead the SEC's fair-access policy requires a declared ``User-Agent``
(an application name plus a contact) and at most 10 requests per second. This
client therefore (a) refuses to run without a configured identity, (b) paces
requests well under the limit, and (c) retries only genuinely transient failures
a bounded number of times -- a 403/429 from the SEC means *slow down*, so it is
treated as a rate signal, never hammered.

Only this slice's needs are implemented: no filing documents, no XBRL, no
generic SEC SDK. ``filings.recent`` is mapped; older pages listed under
``filings.files`` are not fetched yet (a documented limitation: the recent block
covers roughly the latest year or 1,000 filings).
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final, Self

import httpx2 as httpx
from pydantic import SecretStr

from quantforge.providers.base import RawCapture
from quantforge.providers.errors import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderNotFoundError,
    ProviderPayloadError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderServerError,
    ProviderTransportError,
)
from quantforge.providers.filings import FilingsResult
from quantforge.providers.json_decimal import decode_json_document
from quantforge.providers.pacing import (
    BackoffPolicy,
    RateLimiter,
    Sleeper,
    parse_retry_after,
    retryable_statuses,
)
from quantforge.providers.sec.mapper import map_recent_filings

__all__ = ["PROVIDER_NAME", "SecClient", "SecConfig", "normalise_cik"]

logger = logging.getLogger(__name__)

#: Stable provider identity: ``DataSource.name``, ``IngestionRun.provider`` and the
#: raw-store top-level directory. Must never change.
PROVIDER_NAME: Final[str] = "sec-edgar"
_SUBMISSIONS_PATH: Final[str] = "/submissions/CIK{cik}.json"
_MAX_HONOURED_RETRY_AFTER: Final[float] = 60.0
_TRANSIENT_TRANSPORT_ERRORS: Final[tuple[type[Exception], ...]] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)
#: The SEC's ceiling is 10 requests/second; pace at no more than 5 for a safety margin.
MIN_REQUEST_INTERVAL_FLOOR: Final[float] = 0.2
HTTP_OK: Final[int] = 200
HTTP_FORBIDDEN: Final[int] = 403
HTTP_NOT_FOUND: Final[int] = 404
HTTP_TOO_MANY_REQUESTS: Final[int] = 429
HTTP_SERVER_ERROR_FLOOR: Final[int] = 500
_CIK_DIGITS = re.compile(r"^[0-9]{1,10}$")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def normalise_cik(value: str) -> str:
    """Return ``value`` as a zero-padded 10-digit CIK, or raise ``ValueError``."""
    candidate = value.strip()
    if not _CIK_DIGITS.fullmatch(candidate):
        message = f"CIK must be 1-10 digits, got {value!r}"
        raise ValueError(message)
    padded = candidate.zfill(10)
    if int(padded) == 0:
        message = "CIK must not be zero"
        raise ValueError(message)
    return padded


class SecConfig:
    """Everything the client needs. The identity is required, never defaulted."""

    __slots__ = (
        "base_url",
        "max_retries",
        "min_request_interval",
        "request_timeout",
        "retry_base_delay",
        "user_agent",
    )

    def __init__(  # noqa: PLR0913 - one settings-derived field per limit
        self,
        *,
        user_agent: SecretStr | str | None,
        base_url: str,
        request_timeout: float = 20.0,
        max_retries: int = 2,
        retry_base_delay: float = 1.0,
        min_request_interval: float = 0.25,
    ) -> None:
        raw = user_agent.get_secret_value() if isinstance(user_agent, SecretStr) else user_agent
        self.user_agent = raw.strip() if raw and raw.strip() else None
        self.base_url = base_url.rstrip("/")
        self.request_timeout = request_timeout
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        # The SEC allows at most 10 requests/second; the floor is enforced here, where
        # requests are paced, not only in Settings.
        self.min_request_interval = max(min_request_interval, MIN_REQUEST_INTERVAL_FLOOR)

    def __repr__(self) -> str:
        # The identity may contain a personal contact, so it is never printed.
        return (
            f"SecConfig(base_url={self.base_url!r}, user_agent="
            f"{'<set>' if self.user_agent else None}, "
            f"request_timeout={self.request_timeout!r}, max_retries={self.max_retries!r})"
        )

    def require_user_agent(self) -> str:
        """Return the declared identity, or fail with an actionable local error."""
        agent = self.user_agent
        if agent is not None and not (agent.isascii() and agent.isprintable()):
            message = (
                "QF_SEC_USER_AGENT must be printable ASCII (no control characters or "
                "non-ASCII letters): it is sent as an HTTP header"
            )
            raise ProviderConfigurationError(message)
        if agent is None or ("@" not in agent and "://" not in agent):
            message = (
                "QF_SEC_USER_AGENT is not configured with a contact. The SEC requires every "
                "request to declare who is making it, e.g. QF_SEC_USER_AGENT='MyApp "
                "admin@example.com' (an application name plus an email or URL); every other "
                "part of the application works without it."
            )
            raise ProviderConfigurationError(message)
        return agent


class SecClient:
    """Fetch SEC filing metadata. Use as ``async with SecClient(config) as client``."""

    def __init__(  # noqa: PLR0913 - every collaborator is independently injectable
        self,
        config: SecConfig,
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
            max_retries=config.max_retries, base_delay=config.retry_base_delay
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
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
            headers={"Accept": "application/json"},
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
        await self._client.aclose()

    async def get_filings(self, cik: str) -> FilingsResult:
        """Fetch and map the ``filings.recent`` metadata for one registrant."""
        padded = normalise_cik(cik)
        path = _SUBMISSIONS_PATH.format(cik=padded)
        body = await self._get_bytes(path)
        capture = RawCapture(
            provider=PROVIDER_NAME,
            dataset="submissions",
            endpoint=_SUBMISSIONS_PATH,
            request_parameters={"cik": padded},
            body=body,
            retrieved_at=self._now(),
        )
        try:
            try:
                document = decode_json_document(body)
            except (ValueError, RecursionError) as exc:
                raise ProviderResponseError(
                    f"undecodable SEC response: {type(exc).__name__}"
                ) from exc
            filings, rejected = map_recent_filings(document, cik=padded)
        except (ProviderPayloadError, ProviderResponseError) as exc:
            exc.captures = (capture,)  # a malformed response is evidence worth keeping
            raise
        return FilingsResult(filings=filings, rejected=tuple(rejected), captures=(capture,))

    async def _get_bytes(self, path: str) -> bytes:
        # Resolved per call and sent as a header; never part of a URL or a capture.
        headers = {"User-Agent": self._config.require_user_agent()}
        attempt = 0
        while True:
            attempt += 1
            await self._limiter.acquire(self._sleep)
            try:
                response = await self._client.get(path, headers=headers)
            except _TRANSIENT_TRANSPORT_ERRORS as exc:
                if not self._backoff.should_retry(attempt):
                    raise ProviderTransportError(type(exc).__name__) from exc
                await self._sleep(self._backoff.delay_for(attempt, jitter=self._jitter))
                continue
            except httpx.HTTPError as exc:
                raise ProviderTransportError(type(exc).__name__) from exc

            if response.status_code == HTTP_OK:
                return response.content
            if response.status_code == HTTP_NOT_FOUND:
                raise ProviderNotFoundError(f"CIK {path}", "no submissions document")

            retry_after = _bounded_retry_after(response.headers.get("Retry-After"))
            if response.status_code == HTTP_FORBIDDEN:
                # The SEC answers an undeclared/invalid User-Agent (or a client that
                # exceeded its rate threshold) with 403. Retrying cannot fix either,
                # so it is never retried: surfaced as a configuration problem.
                raise ProviderAuthenticationError(
                    response.status_code,
                    "the SEC refused the request: check QF_SEC_USER_AGENT (an application "
                    "name plus a contact) and stay under 10 requests/second",
                )
            if response.status_code == HTTP_TOO_MANY_REQUESTS:
                self._limiter.penalise(retry_after or self._backoff.delay_for(attempt))
                if self._backoff.should_retry(attempt):
                    await self._sleep(
                        retry_after
                        if retry_after is not None
                        else self._backoff.delay_for(attempt, jitter=self._jitter)
                    )
                    continue
                raise ProviderRateLimitError(retry_after)
            if response.status_code in retryable_statuses() and self._backoff.should_retry(attempt):
                await self._sleep(
                    retry_after
                    if retry_after is not None
                    else self._backoff.delay_for(attempt, jitter=self._jitter)
                )
                continue
            if response.status_code >= HTTP_SERVER_ERROR_FLOOR:
                raise ProviderServerError(response.status_code)
            message = f"the SEC rejected the request (HTTP {response.status_code})"
            raise ProviderResponseError(message)


def _bounded_retry_after(header: str | None) -> float | None:
    parsed = parse_retry_after(header)
    return None if parsed is None else min(parsed, _MAX_HONOURED_RETRY_AFTER)
