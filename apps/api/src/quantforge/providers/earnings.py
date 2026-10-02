"""The earnings-data provider contract: what a source of corporate earnings must do.

Scope
-----
Deliberately narrow, and deliberately separate from
:class:`~quantforge.providers.base.MarketDataProvider`. Earnings have their own
paging, their own identity questions (an issuer's fiscal period, not a venue's
session) and their own point-in-time semantics, so a source that knows earnings
must not be forced to also know reference data or daily bars. Capabilities stay
separable: a provider can implement one protocol without the other, and a new
capability is an added protocol, not a widened one.

The layering
------------
::

    provider REST API
        -> provider adapter          (provider-specific, e.g. providers/massive)
        -> raw response capture      (verbatim bytes, stored before interpreting)
        -> provider DTOs             (provider-specific, stay in the adapter)
        -> provider-specific mapper  (adhoc per provider; not in this milestone)
        -> CanonicalEarningsEvent    (ingestion/canonical.py, provider-independent)
        -> future earnings service   (resolves ticker, deduplicates, stores)
        -> EarningsEvent             (the persisted fact)

The contract below ends at :class:`EarningsResult` containing canonical events.
An adapter is responsible for mapping its own DTOs into
:class:`~quantforge.ingestion.canonical.CanonicalEarningsEvent` before
returning, so nothing above this line ever sees a provider's field names or
JSON shapes. This milestone ships the protocol and the result type only; no
adapter implements it yet.

Range semantics
---------------
``get_earnings`` ranges over the event's *date* -- the calendar day the event
belongs to on the issuer's reporting calendar -- not a storage time. Which wall
clock begins or ends that day is decided by the adapter, because it must match
how the source itself reports dates; the contract only requires that a second
call over the same inclusive range return the same observations.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from quantforge.ingestion.canonical import CanonicalEarningsEvent
from quantforge.providers.base import RawCapture

__all__ = ["EarningsProvider", "EarningsResult"]


@dataclass(frozen=True, slots=True)
class EarningsResult:
    """Outcome of an earnings range request.

    ``events`` are already canonical, provider-independent events in ascending
    chronological order, mapped by the adapter. ``captures`` covers every
    response fetched on the way, so a multi-page result is reproducible from
    disk and an empty result stays auditable.
    """

    events: Sequence[CanonicalEarningsEvent]
    captures: tuple[RawCapture, ...] = ()


@runtime_checkable
class EarningsProvider(Protocol):
    """A source of corporate earnings observations.

    Implementations are expected to be safe to use as an async context manager,
    so any HTTP connection pool is released deterministically. The contract is
    structural: an adapter's own class satisfies it without inheriting from
    anything in this module. A provider DTO in the adapter satisfies the return
    type by being *converted* to :class:`CanonicalEarningsEvent` inside the
    adapter; no vendor vocabulary is allowed to cross this line.
    """

    @property
    def name(self) -> str:
        """Stable lowercase provider identity, e.g. ``"massive"``.

        This is what gets stored as ``IngestionRun.provider`` and
        ``DataSource.name``, so it must never change for a given vendor.
        """

    async def get_earnings(
        self,
        ticker: str,
        start: date,
        end: date,
    ) -> EarningsResult:
        """Fetch earnings observations for ``ticker`` over the inclusive range.

        The range is over the event's date (see the module docstring); the
        adapter owns the day boundary. Providers that cannot page by date are
        expected to fetch within the range and filter.

        Raises :class:`~quantforge.providers.errors.ProviderError` for
        transport, authentication and malformed-response failures. A ticker the
        source knows nothing about is an empty result, not an error.
        """
