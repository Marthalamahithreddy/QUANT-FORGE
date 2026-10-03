"""The stock-split provider contract: what a source of historical splits must do.

Separate from the market-data, earnings and filing protocols: a split is a
share-count fact with its own identity (the provider's split id), its own date
semantics (the effective date, not an availability time) and no price. An adapter
maps its payload into :class:`~quantforge.ingestion.canonical.CanonicalStockSplit`
before returning, so nothing above this line sees a provider's field names.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from quantforge.ingestion.canonical import CanonicalStockSplit
from quantforge.providers.base import RawCapture

__all__ = ["SplitProvider", "SplitsResult"]


@dataclass(frozen=True, slots=True)
class SplitsResult:
    """Outcome of one complete split-history fetch for one ticker.

    ``splits`` is every record the provider returned for the ticker (any date),
    mapped and in ascending ``(execution_date, provider_split_id)`` order.
    ``rejected`` holds one reason per record that could not be mapped: such a
    record is counted as failed, never guessed at, and a caller that wants to
    claim completeness must treat any rejection as disqualifying. ``captures`` is
    every raw page fetched, so the result is reproducible from disk.
    """

    splits: Sequence[CanonicalStockSplit]
    rejected: tuple[str, ...] = ()
    captures: tuple[RawCapture, ...] = ()


@runtime_checkable
class SplitProvider(Protocol):
    """A source of historical stock splits."""

    @property
    def name(self) -> str:
        """Stable provider identity, persisted as ``DataSource.name``."""

    async def get_stock_splits(self, ticker: str) -> SplitsResult:
        """Fetch every split the provider holds for ``ticker``, across all pages."""
