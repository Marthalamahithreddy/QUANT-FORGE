"""The provider contract: what a US-equity market-data source must be able to do.

Scope
-----
Deliberately narrow. This protocol covers **US equities and daily bars only** --
ticker reference details and daily OHLCV aggregates. It is not a generic
"financial data provider" abstraction spanning options, forex, crypto, options
chains, corporate actions and fundamentals. Each of those has different paging,
different identifiers and different availability semantics, and a single wide
interface would be a lowest-common-denominator shape that fits none of them
well.

Adding a new asset class means extending this protocol deliberately, once, with
the decisions that asset class actually forces. It does not mean widening this
one until it is unusable.

The layering
------------
::

    Massive API
        -> Massive adapter   (providers/massive)
        -> raw response capture
        -> provider DTOs     (provider-specific, stay in the adapter)
        -> canonical DTOs    (ingestion/canonical.py, provider-independent)
        -> QuantForge models

A provider adapter is the *only* place a vendor's field names, status codes and
JSON shapes are allowed to appear. Everything above it speaks in QuantForge's
vocabulary. That is what makes a second vendor an additive change rather than a
refactor of the storage layer.

Structural typing
-----------------
The one view protocol below (:class:`TickerReferenceView`) is structural, so an
adapter's own DTO class satisfies it without inheriting from anything here. That
keeps provider DTOs genuinely provider-specific -- they are pydantic models in the
adapter's own namespace, not subclasses of a QuantForge base class -- while the
ingestion service still gets a statically checked contract.

Daily bars deliberately have no view protocol. An adapter converts its own DTO into
:class:`~quantforge.ingestion.canonical.CanonicalDailyBar` before returning, so the
bars handed to the ingestion layer are already provider-independent. Offering a
structural ``DailyBarView`` here would be a second, weaker way to say the same
thing, and the weaker one is the one a caller would reach for.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from quantforge.ingestion.canonical import CanonicalDailyBar

__all__ = [
    "DailyBarsResult",
    "MarketDataProvider",
    "RawCapture",
    "TickerDetailsResult",
    "TickerReferenceView",
    "VenueView",
]


@dataclass(frozen=True, slots=True)
class RawCapture:
    """One exact provider response, preserved before anything is interpreted.

    Stored verbatim so that normalisation can be re-derived later, audited, or
    corrected when a provider's semantics are discovered to have been
    misread. The body is the byte string the provider actually sent, not a
    re-serialised model: a re-serialisation would launder the very
    representation errors this exists to catch.

    ``request_parameters`` must never contain a credential. The adapter is
    responsible for that, and the raw store writes whatever it is given to disk
    and into the ingestion run, so a leak here would be persisted.
    """

    #: Stable provider identity that produced this response, e.g. ``"massive"``.
    #: Recorded explicitly rather than inferred from the dataset name: the two
    #: are independent axes, and the raw store needs both to build its path.
    provider: str

    #: Logical dataset within the provider, e.g. ``"reference"`` or
    #: ``"daily-bars"``. Used as the second path segment.
    dataset: str
    #: Path template that was requested, e.g. "/v3/reference/tickers/{ticker}".
    #: A template rather than a concrete URL so a stored artefact does not carry
    #: a query string that could grow a secret.
    endpoint: str
    #: Non-secret request parameters that produced this response.
    request_parameters: Mapping[str, str]
    #: Verbatim response bytes.
    body: bytes
    #: When QuantForge received it, timezone-aware.
    retrieved_at: datetime
    #: Provider's own reported status, when the document carries one.
    provider_status: str | None = None


@runtime_checkable
class TickerReferenceView(Protocol):
    """What QuantForge needs to know about one US equity ticker.

    A structural view, not a shared base class: each adapter implements it with
    its own DTO. Every attribute is optional-with-meaning rather than "whatever
    the provider happened to send" -- ``None`` means *the provider did not supply
    this*, which is a materially different statement from an empty string or a
    zero.

    Declared read-only on purpose. A ``frozen=True`` pydantic model exposes its
    fields as read-only attributes, so a mutable-attribute protocol would reject
    it; and the attribute *names* are canonical rather than the provider's, which
    is what lets an adapter's own DTO satisfy this contract without inheriting
    from anything in this module. The provider's spelling of each field lives in
    that adapter as a pydantic alias.
    """

    @property
    def ticker(self) -> str:
        """Provider's symbol for the instrument, e.g. ``"AAPL"``."""

    @property
    def name(self) -> str | None:
        """Human-readable issuer or instrument name."""

    @property
    def provider_exchange_code(self) -> str | None:
        """The venue code *as the provider spells it*.

        Never assumed to be an ISO 10383 MIC. It is resolved through the
        adapter's explicit mapping table, and an unmapped value is a hard failure
        rather than a guess.
        """

    @property
    def cik(self) -> str | None:
        """SEC Central Index Key, when supplied. A stable issuer identifier."""

    @property
    def composite_figi(self) -> str | None:
        """FIGI identifying the whole company across its securities."""

    @property
    def share_class_figi(self) -> str | None:
        """FIGI identifying this specific share class."""

    @property
    def currency_code(self) -> str | None:
        """ISO 4217 alphabetic trading currency, e.g. ``"USD"``."""

    @property
    def security_type_code(self) -> str | None:
        """Provider's instrument-class code, e.g. ``"CS"`` for common stock."""

    @property
    def is_active(self) -> bool | None:
        """Whether the provider currently considers the ticker trading."""

    @property
    def locale(self) -> str | None:
        """Provider locale, e.g. ``"us"``. Used only to refuse non-US symbols."""


@runtime_checkable
class VenueView(Protocol):
    """A trading venue, as far as converting a timestamp to a session date goes.

    Declared here, in the contract layer, rather than reused from
    :mod:`quantforge.ingestion.canonical`. The direction matters: the ingestion
    layer is downstream of the provider layer, so a provider contract that
    imported a type from ``ingestion`` would invert the dependency and make the
    two impossible to reason about separately.

    Structural, so the canonical :class:`~quantforge.ingestion.canonical.ResolvedVenue`
    -- the value the reference resolver produces and stores -- satisfies it
    without inheriting or importing anything. That is what lets the daily-bar
    call hand its own resolved venue straight to the adapter.
    """

    @property
    def mic(self) -> str:
        """ISO 10383 Market Identifier Code, e.g. ``"XNAS"``."""

    @property
    def name(self) -> str:
        """Human-readable venue name."""

    @property
    def timezone_name(self) -> str:
        """IANA timezone, e.g. ``"America/New_York"``.

        This is the field that decides which calendar day a timestamp belongs
        to, which is why it is part of the contract rather than an optional hint.
        """


@dataclass(frozen=True, slots=True)
class TickerDetailsResult:
    """Outcome of a reference lookup.

    ``reference is None`` means "the provider has no such ticker", which is a
    legitimate empty answer distinct from a failure. Any raw capture obtained on
    the way is still returned so an empty result stays auditable.
    """

    reference: TickerReferenceView | None
    captures: tuple[RawCapture, ...] = ()


@dataclass(frozen=True, slots=True)
class DailyBarsResult:
    """Outcome of a daily-bar range request.

    ``bars`` are canonical bars in ascending session order -- already mapped by the
    adapter, so nothing above this line sees a provider's field names. ``captures``
    covers every page fetched, so a multi-page result is reproducible from disk.
    """

    bars: Sequence[CanonicalDailyBar]
    captures: tuple[RawCapture, ...] = ()


@runtime_checkable
class MarketDataProvider(Protocol):
    """A source of US equity reference data and daily bars.

    Implementations are expected to be safe to use as an async context manager so
    the HTTP connection pool is released deterministically.
    """

    @property
    def name(self) -> str:
        """Stable lowercase provider identity, e.g. ``"massive"``.

        This is what gets stored as ``IngestionRun.provider`` and
        ``DataSource.name``, so it must never change for a given vendor.
        """

    async def get_ticker_details(self, ticker: str) -> TickerDetailsResult:
        """Fetch reference details for one ticker.

        Returns a result whose ``reference`` is ``None`` when the provider has
        no such ticker. Raises :class:`~quantforge.providers.errors.ProviderError`
        for transport, authentication and malformed-response failures.
        """

    async def get_daily_bars(
        self,
        ticker: str,
        start: date,
        end: date,
        *,
        venue: VenueView,
    ) -> DailyBarsResult:
        """Fetch daily aggregates for ``ticker`` over the inclusive date range.

        Implementations must page through the provider's documented pagination
        and return every record, subject to a configured page ceiling.

        ``venue`` is required, not optional. A daily aggregate's timestamp is an
        *instant*, and which calendar day it represents depends on where the
        instrument traded: ``2026-01-02T00:00Z`` is the session of **January 1**
        in New York. An adapter cannot recover the venue from a bars response,
        which carries no exchange code, so making this optional would mean
        either a hard-coded fallback timezone -- silently mis-filing every late
        print by a day -- or a hidden extra request per call. Requiring it forces
        the caller, who has already resolved the listing, to state the venue, and
        makes a wrong session date impossible to produce by omission.
        """
