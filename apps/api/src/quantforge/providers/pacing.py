"""Provider-side request pacing and bounded retry.

Both live here, at the edge, rather than in the ingestion service. The service
asks for "the daily bars for AAPL in January" and has no idea whether that
required one request or four, whether the provider was throttling, or how long
a backoff took. Keeping HTTP mechanics below the service boundary is what lets
the service's tests run with no clock and no sleeping at all.

Determinism
-----------
Every source of nondeterminism is injected:

* ``sleep`` is a callable, so a test asserts on the delays that *would* have
  happened without spending wall-clock time.
* ``jitter`` is a callable returning a multiplier, so backoff is exactly
  predictable in a test and still randomised in production.
* ``monotonic`` is a callable, so rate-limit pacing is a pure function of the
  injected clock.

Nothing here calls ``time.sleep`` or ``random`` directly.
"""

from __future__ import annotations

import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

__all__ = ["BackoffPolicy", "RateLimiter", "Sleeper"]

Sleeper = Callable[[float], Awaitable[None]]

#: Default jitter draws a multiplier from this range, so concurrent workers
#: sharing a rate limit do not retry in lockstep.
DEFAULT_JITTER_RANGE: tuple[float, float] = (0.5, 1.5)


class RateLimiter:
    """Minimum spacing between outbound provider requests.

    Two separate concerns, deliberately combined because both are "do not send
    yet":

    * a steady minimum interval, so a tight ingestion loop cannot outrun the
      free tier;
    * a ``penalise`` window set by a ``429`` or ``Retry-After``, which holds
      every subsequent request back until the provider's own cooldown expires.

    The limiter holds no lock across the sleep, so two concurrent coroutines
    will each take a slot; serialising them here would be a larger behavioural
    change than the free tier requires, and the provider's rate-limit response
    remains the real backstop.
    """

    __slots__ = ("_blocked_until", "_last_slot", "_min_interval", "_monotonic")

    def __init__(
        self,
        min_interval: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """Args:
        min_interval: Minimum seconds between two requests. Zero disables pacing.
        monotonic: Injectable monotonic clock, in seconds.
        """
        self._min_interval = max(0.0, min_interval)
        self._monotonic = monotonic
        self._blocked_until = 0.0
        # None means "no request has been released yet". Spacing is measured
        # between consecutive releases, so the first request is never delayed;
        # seeding this to the construction time would charge the caller for a
        # request that has not happened.
        self._last_slot: float | None = None

    def penalise(self, seconds: float) -> None:
        """Hold all further requests for ``seconds``.

        Called when a provider answers 429 or sends a ``Retry-After``. A
        non-positive value is ignored rather than clearing an existing hold,
        because a provider may send ``Retry-After: 0`` to mean "no backoff",
        not "lift the backoff I just applied".
        """
        if seconds <= 0:
            return
        self._blocked_until = max(self._blocked_until, self._monotonic() + seconds)

    def delay_for_next_request(self) -> float:
        """Seconds the caller must wait before sending, zero if it may send now."""
        now = self._monotonic()
        blocked_remaining = self._blocked_until - now
        if self._last_slot is None:
            return max(0.0, blocked_remaining)
        return max(0.0, blocked_remaining, self._min_interval - (now - self._last_slot))

    async def acquire(self, sleep: Sleeper) -> float:
        """Wait until a request may be sent, and record that it now may be.

        Returns the delay that was actually awaited, which is what makes the
        pacing behaviour assertable in a test.
        """
        delay = self.delay_for_next_request()
        if delay > 0:
            await sleep(delay)
        # Re-read the clock after any wait, so the next spacing measurement
        # starts from when the request was actually released rather than from
        # when it was queued.
        self._last_slot = self._monotonic()
        return delay


@dataclass(frozen=True, slots=True)
class BackoffPolicy:
    """Bounded exponential backoff with jitter.

    Attempts are counted from 1. ``delay_for(attempt=1)`` is the first wait
    after the first failure.
    """

    max_retries: int
    base_delay: float
    max_delay: float = 30.0
    jitter_range: tuple[float, float] = DEFAULT_JITTER_RANGE

    def delay_for(
        self,
        attempt: int,
        *,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> float:
        """Return the delay before retry number ``attempt`` (1-based).

        Exponential in ``base_delay``, clamped to ``max_delay`` *before* jitter is
        applied, so a large attempt count cannot produce a multi-minute wait even
        though the multiplier is nominally unbounded.
        """
        if attempt < 1:
            message = f"attempt must be 1-based, got {attempt}"
            raise ValueError(message)
        low, high = self.jitter_range
        if high < low:
            message = f"jitter range is inverted: {low} > {high}"
            raise ValueError(message)
        exponential = self.base_delay * (2 ** (attempt - 1))
        clamped = min(exponential, self.max_delay)
        factor: float = float(jitter(low, high))
        return float(max(0.0, factor * clamped))

    def should_retry(self, attempt: int) -> bool:
        """Whether another attempt is permitted after ``attempt`` failures.

        ``max_retries`` counts *retries*, so the total number of requests is at
        most ``max_retries + 1``.
        """
        return attempt <= self.max_retries


def parse_retry_after(value: str | None) -> float | None:
    """Interpret a ``Retry-After`` header expressed in seconds.

    Only the delta-seconds form is honoured. The HTTP-date form is legal in
    RFC 9110 but a provider computing it against *its own* clock is exactly the
    ambiguity that produces a wrong wait, and no free-tier API documented for
    this milestone emits it. Returning ``None`` means "fall back to backoff",
    which is safe.
    """
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except (TypeError, ValueError):  # fmt: skip
        return None
    if seconds < 0:
        return None
    return seconds


def retryable_statuses() -> frozenset[int]:
    """HTTP statuses worth retrying.

    429 and 5xx only. 408 is included because a gateway timeout is transient by
    definition. 4xx generally is not: retrying a 400 or 404 cannot change the
    answer, and retrying 401/403 risks throttling a key that is merely
    misconfigured.
    """
    return frozenset({408, 429, 500, 502, 503, 504})
