"""Errors raised by a market-data provider adapter.

One hierarchy for every provider, so the ingestion service can decide what is
worth retrying, what is worth reporting to a human, and what is a permanent
refusal, without knowing which vendor it is talking to.

Translation happens at the adapter boundary. A caller never sees an
``httpx2`` exception, an HTTP status code, or a provider error string: those are
adapter details, and letting them escape would make the ingestion service
vendor-aware for no benefit.

Secret hygiene
--------------
No exception in this module stores a request URL with a query string, a header
dictionary, or a credential. Provider keys are sent in a header, and the
constructors here take only a status code and a short, already-redacted
message. That keeps an accidental ``str(exc)`` in a log line from becoming a
credential disclosure.
"""

from __future__ import annotations

__all__ = [
    "ProviderAuthenticationError",
    "ProviderConfigurationError",
    "ProviderError",
    "ProviderNotFoundError",
    "ProviderPayloadError",
    "ProviderRateLimitError",
    "ProviderResponseError",
    "ProviderServerError",
    "ProviderTransportError",
]


class ProviderError(Exception):
    """Base class for every failure originating in a provider adapter."""


class ProviderConfigurationError(ProviderError):
    """The adapter cannot run because it is missing required configuration.

    Raised for a missing API key. This is deliberately *not* the same as an
    authentication failure: nothing was attempted, so there is nothing to retry
    and the fix is local configuration rather than a vendor problem.
    """


class ProviderAuthenticationError(ProviderError):
    """The provider rejected the credential (HTTP 401 or 403).

    Never retried. Repeating a request that failed authentication only burns
    rate-limit budget and can get an otherwise-valid key throttled.
    """

    def __init__(
        self, status_code: int, message: str = "provider rejected the credentials"
    ) -> None:
        super().__init__(f"{message} (HTTP {status_code})")
        self.status_code = status_code


class ProviderNotFoundError(ProviderError):
    """The provider has no such resource (HTTP 404).

    For a ticker this is a real answer -- the symbol does not exist -- not a
    transient fault, so it is never retried.
    """

    def __init__(self, resource: str, message: str = "not found") -> None:
        super().__init__(f"{resource}: {message} (HTTP 404)")
        self.resource = resource


class ProviderRateLimitError(ProviderError):
    """The rate limit was still exhausted after the configured retries.

    Carries the provider's ``Retry-After`` hint when it sent one, so a caller can
    report when the next attempt is reasonable instead of just "try again".
    """

    def __init__(self, retry_after: float | None = None) -> None:
        hint = f"; Retry-After suggested {retry_after:g}s" if retry_after is not None else ""
        super().__init__(f"provider rate limit exhausted after retries{hint}")
        self.retry_after = retry_after


class ProviderServerError(ProviderError):
    """The provider failed with a 5xx that survived every retry."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"provider server error (HTTP {status_code}) after retries")
        self.status_code = status_code


class ProviderTransportError(ProviderError):
    """The request never produced a response: timeout, DNS, reset, TLS.

    The likely cause of a timeout is kept as a coarse label rather than the
    exception itself, because driver exception text can embed the request URL
    and, on some transports, a fragment of the request line.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"provider transport failure: {reason}")
        self.reason = reason


class ProviderResponseError(ProviderError):
    """A response arrived but could not be understood.

    Covers a truncated body, HTML served where JSON was expected, and a
    top-level document that is not the shape the endpoint documents. The body
    itself is deliberately *not* attached: it may be large, and a server error
    page can echo request details.
    """

    def __init__(self, message: str) -> None:
        super().__init__(f"malformed provider response: {message}")


class ProviderPayloadError(ProviderError):
    """One record inside an otherwise valid document failed validation.

    Distinct from :class:`ProviderResponseError` so a partially-usable response
    can be reported per record rather than failing the whole page.
    """

    def __init__(self, message: str, *, identifier: str | None = None) -> None:
        where = f" [{identifier}]" if identifier else ""
        super().__init__(f"malformed provider record{where}: {message}")
        self.identifier = identifier
