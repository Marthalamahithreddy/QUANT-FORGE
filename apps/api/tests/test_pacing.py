"""Unit tests for request pacing and bounded backoff.

Pacing is the adapter's first line of defence against the free tier, and backoff
is the second. Both are validated without a clock and without a provider: every
source of nondeterminism in :mod:`quantforge.providers.pacing` is injectable,
so these tests assert against the delays that *would* have happened.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from quantforge.providers.pacing import (
    BackoffPolicy,
    RateLimiter,
    parse_retry_after,
    retryable_statuses,
)

#: Monotonic clock reads from this counter, so a test advances time explicitly.
Clock = Callable[[], float]


def _make_clock(start: float = 0.0) -> tuple[Clock, list[float]]:
    ticks = [start]

    def clock() -> float:
        return ticks[0]

    return clock, ticks


async def _no_sleep(_seconds: float) -> None:
    return None


@pytest.mark.anyio
async def test_first_request_is_never_delayed() -> None:
    """Spacing is between consecutive releases; the first request is free."""
    delays: list[float] = []
    clock, _ticks = _make_clock()

    async def record(seconds: float) -> None:
        delays.append(seconds)

    limiter = RateLimiter(min_interval=2.0, monotonic=clock)
    assert await limiter.acquire(record) == 0.0
    assert delays == []


@pytest.mark.anyio
async def test_second_request_is_held_to_the_minimum_interval() -> None:
    clock, ticks = _make_clock()
    awaited: list[float] = []

    async def record(seconds: float) -> None:
        awaited.append(seconds)
        ticks[0] += seconds

    limiter = RateLimiter(min_interval=2.0, monotonic=clock)
    await limiter.acquire(record)

    second_delay = await limiter.acquire(record)
    assert second_delay == pytest.approx(2.0)
    assert awaited == [2.0]


@pytest.mark.anyio
async def test_spacing_is_measured_from_release_not_from_queue() -> None:
    """A request released 1s ago must wait out the remaining 1s, not 2s anew."""
    clock, ticks = _make_clock()
    awaited: list[float] = []
    limiter = RateLimiter(min_interval=2.0, monotonic=clock)

    async def record(seconds: float) -> None:
        awaited.append(seconds)
        ticks[0] += seconds

    await limiter.acquire(record)
    ticks[0] += 1.0  # a second passes between the two requests
    assert await limiter.acquire(record) == pytest.approx(1.0)


def test_penalise_blocks_every_request_until_it_expires() -> None:
    clock, ticks = _make_clock()
    limiter = RateLimiter(min_interval=0.0, monotonic=clock)

    limiter.penalise(10.0)
    assert limiter.delay_for_next_request() == pytest.approx(10.0)

    ticks[0] = 3.0
    assert limiter.delay_for_next_request() == pytest.approx(7.0)

    ticks[0] = 10.0
    assert limiter.delay_for_next_request() == pytest.approx(0.0)


def test_penalise_ignores_nonpositive_hold() -> None:
    """``Retry-After: 0`` must not clear an existing hold, and -1 is nonsense."""
    clock, _ticks = _make_clock()
    limiter = RateLimiter(min_interval=0.0, monotonic=clock)

    limiter.penalise(0.0)
    limiter.penalise(-1.0)
    assert limiter.delay_for_next_request() == 0.0

    limiter.penalise(5.0)
    limiter.penalise(0.0)
    assert limiter.delay_for_next_request() == pytest.approx(5.0)


def test_penalise_extends_rather_than_shrinks_an_existing_hold() -> None:
    clock, _ticks = _make_clock()
    limiter = RateLimiter(min_interval=0.0, monotonic=clock)

    limiter.penalise(8.0)
    limiter.penalise(2.0)
    assert limiter.delay_for_next_request() == pytest.approx(8.0)


# ── backoff policy ──────────────────────────────────────────────────────────


def test_backoff_is_exponential_within_the_cap() -> None:
    fixed = lambda _low, _high: 1.0  # noqa: E731 - jitter pinned to one for exactness
    policy = BackoffPolicy(max_retries=5, base_delay=0.5, max_delay=3.0)

    assert policy.delay_for(1, jitter=fixed) == pytest.approx(0.5)
    assert policy.delay_for(2, jitter=fixed) == pytest.approx(1.0)
    assert policy.delay_for(3, jitter=fixed) == pytest.approx(2.0)


def test_backoff_is_capped_before_jitter() -> None:
    """A large attempt count must not produce a multi-minute wait via jitter."""
    fixed = lambda _low, _high: 1.5  # noqa: E731
    policy = BackoffPolicy(max_retries=20, base_delay=1.0, max_delay=4.0)

    # Un-jittered exponential at attempt 6 would be 32s; the cap is 4s.
    assert policy.delay_for(6, jitter=fixed) == pytest.approx(6.0)


def test_backoff_clamps_a_negative_jitter_factor() -> None:
    policy = BackoffPolicy(max_retries=3, base_delay=1.0, max_delay=8.0)
    assert policy.delay_for(1, jitter=lambda _low, _high: -5.0) == 0.0


def test_backoff_rejects_zero_attempt_and_inverted_jitter() -> None:
    policy = BackoffPolicy(max_retries=3, base_delay=1.0, jitter_range=(2.0, 1.0))
    with pytest.raises(ValueError, match="1-based"):
        policy.delay_for(0, jitter=lambda _low, _high: 1.0)
    with pytest.raises(ValueError, match="inverted"):
        policy.delay_for(1, jitter=lambda _low, _high: 1.0)


def test_should_retry_counts_retries_not_requests() -> None:
    policy = BackoffPolicy(max_retries=2, base_delay=0.5)
    assert policy.should_retry(1) is True
    assert policy.should_retry(2) is True
    assert policy.should_retry(3) is False


# ── retry-after parsing ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("3.5", 3.5),
        (" 2 ", 2.0),
        ("0", 0.0),
        (None, None),
        # The HTTP-date form is deliberately unsupported: computing a wait against
        # the provider's clock is exactly the ambiguity that produces a wrong hold.
        ("Wed, 21 Oct 2026 07:28:00 GMT", None),
        ("not-a-number", None),
        ("-3", None),
    ],
)
def test_parse_retry_after(header: str | None, expected: float | None) -> None:
    result = parse_retry_after(header)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_retryable_statuses_cover_transient_only() -> None:
    assert retryable_statuses() == frozenset({408, 429, 500, 502, 503, 504})
    assert 400 not in retryable_statuses()
    assert 401 not in retryable_statuses()
    assert 404 not in retryable_statuses()
    assert 200 not in retryable_statuses()
