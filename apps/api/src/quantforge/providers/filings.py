"""The filing-metadata provider contract: what a source of SEC filings must do.

Separate from the market-data and earnings protocols: a filing has its own
identity (an accession), its own timestamp semantics (acceptance by EDGAR, which
is not first public disclosure) and no price or EPS. An adapter maps its own
payload into :class:`~quantforge.ingestion.canonical.CanonicalSecFiling` before
returning, so nothing above this line sees a provider's field names.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from quantforge.ingestion.canonical import CanonicalSecFiling
from quantforge.providers.base import RawCapture

__all__ = ["FilingProvider", "FilingsResult"]


@dataclass(frozen=True, slots=True)
class FilingsResult:
    """Outcome of one filing-metadata fetch for one registrant.

    ``filings`` are canonical filings of the supported forms, in ascending
    ``accepted_at`` order. ``rejected`` holds one human-readable reason per
    supported-form record that could not be mapped (a malformed or inconsistent
    timestamp, say): such records are counted as failed, never guessed at.
    ``captures`` is every raw response fetched, so the result is reproducible
    from disk.
    """

    filings: Sequence[CanonicalSecFiling]
    rejected: tuple[str, ...] = ()
    captures: tuple[RawCapture, ...] = ()


@runtime_checkable
class FilingProvider(Protocol):
    """A source of SEC filing metadata."""

    @property
    def name(self) -> str:
        """Stable provider identity, persisted as ``DataSource.name``."""

    async def get_filings(self, cik: str) -> FilingsResult:
        """Fetch filing metadata for the registrant with this 10-digit CIK."""
