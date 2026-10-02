"""Tests for the earnings provider contract.

The contract is the second boundary of the earnings milestone: a provider
adapter converts its own DTOs to canonical events and the ingestion pipeline
never sees either the vendor's names or the money values the vendor laundered.
These tests pin the shape of that boundary without a provider existing yet.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from quantforge.ingestion.canonical import CanonicalEarningsEvent
from quantforge.models.enums import EarningsTiming
from quantforge.providers.base import RawCapture
from quantforge.providers.earnings import EarningsProvider, EarningsResult


def _event() -> CanonicalEarningsEvent:
    return CanonicalEarningsEvent(
        ticker="AAPL",
        fiscal_year=2026,
        fiscal_quarter=1,
        event_date=date(2026, 5, 1),
        event_timestamp=datetime(2026, 5, 1, 20, 30, tzinfo=UTC),
        timing=EarningsTiming.AFTER_MARKET,
        reported_eps=Decimal("1.52"),
        estimated_eps=Decimal("1.43"),
        reported_revenue=Decimal("123456789.00"),
        estimated_revenue=Decimal("120000000.00"),
        currency="USD",
    )


def _capture() -> RawCapture:
    return RawCapture(
        provider="fake-earnings",
        dataset="earnings",
        endpoint="/v1/earnings/{ticker}",
        request_parameters={"date": "2026-05-01"},
        body=b'{"c":1}',
        retrieved_at=datetime(2026, 5, 1, 21, 0, tzinfo=UTC),
    )


class _ConformingAdapter:
    @property
    def name(self) -> str:
        return "fake-earnings"

    async def get_earnings(
        self,
        ticker: str,
        start: date,
        end: date,
    ) -> EarningsResult:
        return EarningsResult(events=[_event()], captures=(_capture(),))


def test_a_conforming_adapter_satisfies_the_protocol() -> None:
    """The contract is structural, so an adapter needs no inheritance."""
    assert isinstance(_ConformingAdapter(), EarningsProvider)


def test_missing_get_earnings_does_not_satisfy_the_protocol() -> None:
    class _NoMethod:
        @property
        def name(self) -> str:
            return "broken"

    assert not isinstance(_NoMethod(), EarningsProvider)


def test_missing_name_does_not_satisfy_the_protocol() -> None:
    class _NoName:
        async def get_earnings(self, ticker: str, start: date, end: date) -> EarningsResult:
            return EarningsResult(events=[])

    assert not isinstance(_NoName(), EarningsProvider)


def test_earnings_result_carries_canonical_events_and_captures() -> None:
    event = _event()
    capture = _capture()
    result = EarningsResult(events=[event], captures=(capture,))
    assert list(result.events) == [event]
    assert result.captures == (capture,)


def test_result_events_are_canonical_not_provider_dto() -> None:
    """The result type structurally prevents a vendor DTO crossing the boundary."""
    result = EarningsResult(events=[_event()])
    for event in result.events:
        assert isinstance(event, CanonicalEarningsEvent)


@pytest.mark.anyio
async def test_a_stub_adapter_returns_canonical_events() -> None:
    adapter = _ConformingAdapter()
    result = await adapter.get_earnings("AAPL", date(2026, 5, 1), date(2026, 5, 1))
    assert adapter.name == "fake-earnings"
    assert [e.ticker for e in result.events] == ["AAPL"]
    assert result.events[0].reported_eps == Decimal("1.52")
    assert len(result.captures) == 1
