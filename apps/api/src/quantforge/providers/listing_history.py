"""The listing-history provider contract: dated ticker snapshots and ticker-change events.

Separate from the reference, daily-bar and split protocols. An adapter maps its payloads into the
canonical contracts before returning, so nothing above this line sees a provider's field names, and
it only PARSES source facts: it never derives intervals (reconciliation does).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from quantforge.ingestion.canonical import CanonicalTickerEvents, CanonicalTickerSnapshot
from quantforge.providers.base import RawCapture

__all__ = ["ListingHistoryProvider", "TickerEventsResult", "TickerSnapshotsResult"]


@dataclass(frozen=True, slots=True)
class TickerSnapshotsResult:
    """Outcome of one dated-snapshot fetch.

    ``snapshots`` are mapped records of supported instrument types. ``unsupported`` counts
    records of instrument types QuantForge does not model (they are not failures).
    ``rejected`` holds one reason per record of a supported type that could not be mapped;
    a caller treats any rejection as disqualifying. ``captures`` is every raw page.
    """

    snapshots: Sequence[CanonicalTickerSnapshot]
    unsupported: int = 0
    rejected: tuple[str, ...] = ()
    captures: tuple[RawCapture, ...] = ()


@dataclass(frozen=True, slots=True)
class TickerEventsResult:
    """Outcome of one ticker-events fetch (an EXPERIMENTAL provider endpoint)."""

    events: CanonicalTickerEvents | None
    rejected: tuple[str, ...] = ()
    captures: tuple[RawCapture, ...] = ()


@runtime_checkable
class ListingHistoryProvider(Protocol):
    """A source of dated ticker snapshots and ticker-change events."""

    @property
    def name(self) -> str:
        """Stable provider identity, persisted as ``DataSource.name``."""

    async def get_ticker_snapshots(
        self, ticker: str, on_date: date, *, active: bool = True
    ) -> TickerSnapshotsResult:
        """Records for ``ticker`` as the provider lists it on ``on_date``."""

    async def get_ticker_events(self, ticker: str) -> TickerEventsResult:
        """Ticker-change events for the entity currently represented by ``ticker``."""
