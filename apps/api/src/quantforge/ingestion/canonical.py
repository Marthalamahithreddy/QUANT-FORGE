"""Provider-independent ingestion values.

Why these exist
---------------
An adapter's DTOs answer "what did Massive send?". These answer "what is
QuantForge trying to store?". The two are not the same question, and conflating
them is what turns a provider integration into a refactor of the storage layer.

The separation buys a specific, testable property: everything in this module is
free of vendor vocabulary. No ``o``/``h``/``l``/``c``, no ``primary_exchange``,
no ``T``, no ``vw``. A second vendor's adapter produces these same types, so the
reference resolver, the daily-bar writer and the future earnings writer cannot
know or care which vendor supplied the data. That is the whole point -- a test
for the daily-bar writer runs with no HTTP client and no vendor anywhere in
sight.

Validation lives here rather than in the models
-----------------------------------------------
These are frozen dataclasses constructed *only* by an adapter's mapper, so
validation is cheapest and clearest at construction. The ORM models then receive
values already known to be well-formed, and the database ``CHECK`` constraints
remain the final authority for anything arriving by another route.

The invariants asserted below are the ones where a silent wrong value would
survive all the way into a backtest: a high below the low, a non-positive price,
an unsorted bar, a timezone-naive timestamp. Each of those produces plausible
numbers that are simply incorrect, which is far more dangerous than a crash.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from quantforge.models.corporate_action import PROVIDER_SPLIT_ID_LENGTH, PROVIDER_TICKER_LENGTH
from quantforge.models.enums import (
    AvailabilityBasis,
    EarningsTiming,
    PriceBasis,
    SecFormType,
    StockSplitAdjustmentType,
)
from quantforge.models.filing import (
    ACCESSION_REGEX,
    EDGAR_ERA_START,
    ITEMS_LENGTH,
    PRIMARY_DOCUMENT_LENGTH,
)
from quantforge.models.financial_fact import (
    ADDITIONAL_CIKS_LENGTH,
    COREG_LENGTH,
    FACT_VALUE_PRECISION,
    FACT_VALUE_SCALE,
    FISCAL_PERIOD_LENGTH,
    FISCAL_YEAR_END_LENGTH,
    IDENTITY_TEXT_MAX_BYTES,
    SEGMENTS_LENGTH,
    SOURCE_ACCEPTED_LENGTH,
    TAG_LENGTH,
    UNIT_LENGTH,
    VERSION_LENGTH,
)

__all__ = [
    "CanonicalDailyBar",
    "CanonicalEarningsEvent",
    "CanonicalEquityReference",
    "CanonicalFactSubmission",
    "CanonicalFinancialFact",
    "CanonicalSecFiling",
    "CanonicalStockSplit",
    "CanonicalTickerChange",
    "CanonicalTickerEvents",
    "CanonicalTickerSnapshot",
    "ReferenceResolution",
    "ResolvedVenue",
]

#: A listed equity's share count and price data imply a positive price. A zero or
#: negative close is a data error, not a real print: no venue reports it, and
#: accepting one would corrupt any return calculation that touches the row.
_MIN_POSITIVE_PRICE: Final[Decimal] = Decimal("1e-9")

#: A SEC CIK is a fixed-width, zero-padded 10-digit number. Repeated here rather
#: than imported so the canonical layer's invariant does not depend on the model
#: module's column widths.
_CIK_LENGTH: Final[int] = 10

#: Upper bound on a single US equity price. This is a gross-corruption
#: backstop, not a units check: it catches a field holding a market cap, a
#: cumulative index level, or a garbage value. It deliberately does *not* claim
#: to catch a cents-as-dollars error, because 199.99 stored as 19999 is still
#: under this bound -- no single price ceiling can distinguish that from a
#: legitimately expensive stock, and pretending otherwise would be a false
#: guarantee. Sits far above any real listing, including the multi-thousand
#: prints that pre-split adjusted series can contain.
_MAX_PLAUSIBLE_PRICE: Final[Decimal] = Decimal("100000000")

#: Fiscal-year bound, mirroring the ``fiscal_year_range`` CHECK constraint on
#: ``earnings_event``. Repeated locally, as the price bounds above are, so the
#: canonical invariant does not depend on a model module's column declarations.
_MIN_FISCAL_YEAR: Final[int] = 1900
_MAX_FISCAL_YEAR: Final[int] = 2200

#: Fiscal-quarter bound, mirroring the ``fiscal_quarter_range`` CHECK
#: constraint. Nullable and not convertible: a non-quarterly filer has no
#: quarter, and inventing one would corrupt calendar joins.
_MIN_FISCAL_QUARTER: Final[int] = 1
_MAX_FISCAL_QUARTER: Final[int] = 4

#: An ISO 4217 alphabetic currency code is exactly three letters.
_CURRENCY_CODE_LENGTH: Final[int] = 3


def _require_blank_free(value: str, message: str) -> None:
    if not value.strip():
        raise ValueError(message)


def _require_int(value: object, field_name: str) -> None:
    if not isinstance(value, int):
        message = f"{field_name} must be an int, got {type(value).__name__}"
        raise ValueError(message)


def _require_range(value: int, low: int, high: int, field_name: str) -> None:
    if not low <= value <= high:
        message = f"{field_name} must be between {low} and {high}, got {value}"
        raise ValueError(message)


def _require_aware(instant: datetime, field_name: str) -> None:
    # A plain ``date`` is not an instant (and has no tzinfo): a date-only value is
    # never accepted where an instant is required, nor promoted to midnight.
    if not isinstance(instant, datetime):
        raise ValueError(f"{field_name} must be a datetime instant, got {type(instant).__name__}")
    if instant.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _require_member(value: object, enum_cls: type[StrEnum], field_name: str) -> None:
    if not isinstance(value, enum_cls):
        message = f"{field_name} must be an {enum_cls.__name__} member, got {value!r}"
        raise ValueError(message)


def _require_optional_decimal(value: object, field_name: str) -> None:
    if value is None:
        return
    if not isinstance(value, Decimal):
        message = f"{field_name} must be a Decimal, got {type(value).__name__}"
        raise ValueError(message)
    if not value.is_finite():
        message = f"{field_name} must be finite, got {value}"
        raise ValueError(message)


def _require_currency(value: str) -> None:
    if not (len(value) == _CURRENCY_CODE_LENGTH and value.isalpha() and value.isupper()):
        message = f"currency must be an ISO 4217 code like 'USD', got {value!r}"
        raise ValueError(message)


@dataclass(frozen=True, slots=True)
class ResolvedVenue:
    """A provider's venue code resolved to a real, storable exchange.

    Created only by an adapter that has an explicit, curated mapping. There is no
    code path that produces a :class:`ResolvedVenue` from a vendor string
    without passing through that mapping, which is what makes "unknown exchange"
    a hard failure instead of a row with a garbage MIC.
    """

    #: ISO 10383 Market Identifier Code. The value stored in ``Exchange.mic``.
    mic: str

    #: Human-readable venue name.
    name: str

    #: IANA timezone, used to turn a bar's timestamp into a session date.
    timezone_name: str

    def __post_init__(self) -> None:
        # The zone is resolved eagerly so a typo in an adapter's exchange table
        # surfaces when the mapping is written, not weeks later when a bar's
        # session date silently comes out one day wrong. A venue's timezone is
        # the single authority for that conversion, so it has to be a name the
        # platform can actually resolve.
        try:
            ZoneInfo(self.timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            message = f"timezone {self.timezone_name!r} is not a known IANA zone name"
            raise ValueError(message) from exc


@dataclass(frozen=True, slots=True)
class CanonicalEquityReference:
    """A US-listed common or preferred equity, as QuantForge understands it.

    Constructed by an adapter's mapper, which is responsible for *normalising*
    the incoming values: upper-casing the ticker, zero-padding the CIK, collapsing
    blanks to ``None``, resolving the venue, and mapping the instrument class into
    QuantForge's vocabulary. This class does not repeat that work, because
    normalising in two places is how the two drift apart.

    What it *does* enforce is the identity rule, because that one is a correctness
    requirement rather than a formatting preference: a CIK that is not ten digits
    cannot match any real issuer, and storing it would create a row that silently
    fails to join forever.
    """

    #: Upper-case symbol exactly as the venue lists it, e.g. ``"AAPL"``.
    ticker: str

    #: QuantForge instrument class, e.g. ``EQUITY`` / ``PREFERRED_EQUITY``.
    security_type: str

    #: Resolved venue, or ``None`` only if the adapter was configured to allow an
    #: unmapped venue -- which this milestone does not, so in practice this is
    #: always populated. Typed optional so the "venue is genuinely unknown" case
    #: can be represented for a future non-listed instrument class.
    venue: ResolvedVenue | None

    #: Issuer display name, when supplied.
    company_name: str | None = None

    #: Zero-padded 10-digit SEC CIK. The preferred stable issuer key.
    cik: str | None = None

    #: FIGI for the whole issuer, across all its securities.
    composite_figi: str | None = None

    #: FIGI for this specific share class.
    share_class_figi: str | None = None

    #: ISO 4217 alphabetic trading currency.
    currency_code: str | None = None

    #: Whether the provider currently considers the symbol trading.
    is_active: bool | None = None

    def __post_init__(self) -> None:
        if not self.ticker.strip():
            message = "canonical reference requires a non-blank ticker"
            raise ValueError(message)
        if not self.security_type.strip():
            message = "canonical reference requires a security type"
            raise ValueError(message)
        if self.cik is not None and (not self.cik.isdigit() or len(self.cik) != _CIK_LENGTH):
            message = f"CIK must be {_CIK_LENGTH} digits, got {self.cik!r}"
            raise ValueError(message)

    @property
    def identity_key(self) -> str:
        """The key this reference should be resolved under.

        Deliberately CIK-first. A ticker is not an identity -- symbols are
        reused across issuers over time, and a reused ticker would otherwise
        merge two companies' entire price histories. The FIGI is the fallback
        because it is a global instrument identifier. If neither exists the
        caller must not fall back to the ticker; that is an explicit failure, and
        the resolver is where it is enforced.
        """
        if self.cik is not None:
            return f"cik:{self.cik}"
        if self.share_class_figi is not None:
            return f"figi:{self.share_class_figi}"
        if self.composite_figi is not None:
            return f"cfigi:{self.composite_figi}"
        message = (
            f"{self.ticker} has no CIK and no FIGI; it cannot be resolved to a "
            "stable identity without risking a merge of two distinct issuers"
        )
        raise ValueError(message)


def _require_finite_positive_vwap(vwap: Decimal) -> None:
    """The only checks a provider-reported vwap gets; see ``CanonicalDailyBar.__post_init__``."""
    if not vwap.is_finite():
        message = f"vwap must be finite, got {vwap}"
        raise ValueError(message)
    if vwap <= 0:
        message = f"vwap must be positive, got {vwap}"
        raise ValueError(message)
    # The same plausibility backstop the OHLC prices get (provider-independent): it catches a
    # wrong-field vwap (turnover instead of volume) without asserting any containment in [low, high].
    if vwap > _MAX_PLAUSIBLE_PRICE:
        message = f"vwap {vwap} is implausibly large (> {_MAX_PLAUSIBLE_PRICE})"
        raise ValueError(message)


@dataclass(frozen=True, slots=True)
class CanonicalDailyBar:
    """One daily OHLCV aggregate, ready to persist.

    ``trading_session_date`` is already a ``date``, already derived through the
    venue's timezone. The writer must not re-derive it: a second conversion is a
    second chance to disagree with the first, and the disagreement would be
    invisible because both results are plausible dates.

    ``observed_at`` is ``None`` unless the provider supplied a genuine statement
    of when this data became available. Daily aggregates are not stamped with
    that by most vendors, and inventing one -- typically "the session's close" --
    would claim a knowledge that makes a point-in-time backtest quietly wrong.
    "When we ingested it" is a different fact and has its own column.
    """

    trading_session_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int | None
    #: Which split-adjustment basis every price on this bar is in. Required, with
    #: no default, so a writer cannot persist a bar without knowing it: the
    #: persisted row's uniqueness key and conflict comparison both include it.
    price_basis: PriceBasis
    observed_at: datetime | None = None
    #: Provider-reported volume-weighted average price for the aggregate window,
    #: when supplied. Carried through rather than recomputed, so a stored value
    #: always matches what the provider published. Its trade population is
    #: provider-defined: it is not a regular-session VWAP and is not asserted to
    #: lie within ``[low, high]`` (see ``__post_init__``).
    vwap: Decimal | None = None
    #: Trade count, when supplied. Useful later as a liquidity proxy.
    trades: int | None = None

    def __post_init__(self) -> None:
        _require_member(self.price_basis, PriceBasis, "price_basis")
        for field in ("open", "high", "low", "close"):
            price: Decimal = getattr(self, field)
            if not price.is_finite():
                message = f"{field} must be finite, got {price}"
                raise ValueError(message)
            if price <= _MIN_POSITIVE_PRICE:
                message = f"{field} must be positive, got {price}"
                raise ValueError(message)
            if price > _MAX_PLAUSIBLE_PRICE:
                message = (
                    f"{field}={price} exceeds the gross-corruption price bound; "
                    "check that this field is a per-share price and not a market "
                    "cap, index level or cumulative figure"
                )
                raise ValueError(message)

        # The low must not exceed the high. Enforced as ``low > high`` rather
        # than via a min/max sweep so a genuinely inverted bar is *rejected*
        # instead of being silently "corrected" by swapping the two values --
        # quietly repairing bad data hides that the data was bad.
        if self.low > self.high:
            message = f"low {self.low} exceeds high {self.high}"
            raise ValueError(message)
        # The open and close are prints made *within* the session, so they cannot
        # sit outside the session's own range. Enforced rather than trusted: a bar
        # whose open is above its high is a parse error upstream, and accepting it
        # yields a chart that cannot be drawn and a return that is quietly wrong.
        for field in ("open", "close"):
            price = getattr(self, field)
            if not self.low <= price <= self.high:
                message = f"{field}={price} is outside the session range [{self.low}, {self.high}]"
                raise ValueError(message)
        # vwap is the PROVIDER-REPORTED volume-weighted average price for the
        # aggregate window. The trade population it is computed over is defined
        # by the provider, not here, so it is deliberately NOT asserted to lie
        # within [low, high] and is NOT labelled a regular-session VWAP: Massive
        # documents that a trade may update a bar's volume without updating its
        # high/low, and the observed NKE 2026-10-01 bar has vw below its low
        # (docs/market-data-repair-design.md section 3). Only what holds for any
        # price is enforced -- finite and positive, mirroring the DB's
        # ``vwap_positive`` CHECK so a bad value fails here, not at the write.
        if self.vwap is not None:
            _require_finite_positive_vwap(self.vwap)
        if self.volume is not None and self.volume < 0:
            message = f"volume must not be negative, got {self.volume}"
            raise ValueError(message)
        if self.trades is not None and self.trades < 0:
            message = f"trades must not be negative, got {self.trades}"
            raise ValueError(message)
        if self.observed_at is not None and self.observed_at.tzinfo is None:
            message = "observed_at must be timezone-aware"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class CanonicalEarningsEvent:
    """One earnings fact for an issuer, provider-independent, ready to persist.

    Point-in-time semantics are load-bearing here, so they are pinned on the
    type rather than left to the writer:

    * ``event_date`` is the provider-supported calendar date of the event,
      exactly as the source stated it, and is **always required**. Every
      earnings-calendar source QuantForge has integrated states at least this
      much; it is the one fact guaranteed to exist.
    * ``event_timestamp`` is the exact instant the event occurred *or is
      scheduled*, when -- and only when -- the source genuinely states one.
      ``None`` is the honest answer otherwise, and it is never derived from
      ``event_date`` or ``timing``: a calendar date plus "after market close"
      is not an instant, and manufacturing midnight, 09:30 or 16:00 out of
      that pair would assert a precision the source never gave. When present,
      it must be timezone-aware, for the same reason a naive instant cannot
      be stored in a ``TIMESTAMPTZ`` column without guessing a zone.
    * ``timing`` is a coarse, provider-declared session classification. It
      does not imply or require an exact timestamp -- a source can state
      "after market close" without stating *when*, and that is still real
      information worth keeping, not a reason to fabricate one.
    * ``observed_at`` is when the source made the information available. It is
      ``None`` unless the source genuinely stated when, and it is never
      manufactured: "we do not know" and "it was observed at X" are different
      facts with different research value. Inventing a value -- say the session's
      close -- would make a point-in-time study quietly wrong.
      It must be a genuine sub-day instant. A provider that states only a
      *date* must leave it ``None``: normalising a date to midnight UTC would
      satisfy ``observed_at < session_open`` in
      :mod:`quantforge.backtest.execution` without proving any intraday
      availability. The stored type cannot tell the two apart, so this
      requirement is enforced here, at the provider mapper, by convention and
      review -- a mapper that cannot name a real instant returns ``None``.
    * ``ingested_at`` (when QuantForge received the event) is deliberately
      **not** a field here. It is assigned by the ingestion service at write
      time and only then, so a fetch that is validated and re-stored later is
      stamped with the moment of the storage decision rather than an earlier
      parse.
    * ``timing`` is the session phase the source *declared* and nothing more.
      It is never inferred from the wall-clock hour of ``event_timestamp``, and
      ``UNKNOWN`` is the honest answer when the source does not declare one.
      Whether an event was actionable, and when, is a derivable question, not a
      stored fact.

    Reported and estimated figures are carried side by side and never combined.
    EPS surprise and revenue surprise are computed later in the analytics layer,
    where the methodology is versioned and re-runnable; freezing an answer into a
    fact row would discard its inputs. Negative EPS and negative revenue are
    legitimately present in loss-making quarters and restatements, so no
    non-negativity bound is applied.

    ``ticker`` is the provider-independent symbol the observation belongs to. It
    is *not* a stored identity: the future earnings service resolves it to a
    ``company_id`` through the reference resolver, exactly as
    :class:`CanonicalEquityReference` does for daily bars. Identity and
    deduaplication across sources are the ingestion service's job, not this
    type's; the intended policy is documented alongside the earnings milestones.
    """

    #: Provider-independent symbol, e.g. ``"AAPL"``. Resolved to a company by
    #: the earnings ingestion service, never stored as-is.
    ticker: str

    #: The issuer's fiscal year the result covers -- not the calendar year.
    fiscal_year: int

    #: The issuer's fiscal quarter 1-4, or ``None`` for non-quarterly/annual
    #: filers. Never invented from a calendar date.
    fiscal_quarter: int | None

    #: The provider-supported calendar date of the event, exactly as the
    #: source stated it. Always required; never derived from a fiscal period.
    event_date: date

    #: The session phase the source declared, typed. Never inferred from the
    #: clock, and ``None`` is not an option -- ``UNKNOWN`` is the negative space.
    timing: EarningsTiming

    #: The exact instant the event occurred or is scheduled, timezone-aware,
    #: *only* when the source genuinely states one. ``None`` otherwise -- never
    #: derived from ``event_date`` or ``timing``. Not snapped to any session
    #: boundary when present.
    event_timestamp: datetime | None = None

    #: Reported diluted/basic EPS as published, exactly as a Decimal. May be
    #: negative (a loss).
    reported_eps: Decimal | None = None

    #: Consensus/market EPS estimate as published. May be negative.
    estimated_eps: Decimal | None = None

    #: Reported revenue as published. May be negative after restatement.
    reported_revenue: Decimal | None = None

    #: Consensus/market revenue estimate as published.
    estimated_revenue: Decimal | None = None

    #: ISO 4217 code for the figures, if the source stated it. ``None`` means
    #: the source did not state a currency, not "assume USD".
    currency: str | None = None

    #: When the source indicated this event became public, if genuinely known.
    #: Never manufactured; ``None`` means undisclosed. Present only together with
    #: ``observed_at_basis`` -- a timestamp must say what it proves.
    observed_at: datetime | None = None

    #: What ``observed_at`` proves (:class:`AvailabilityBasis`). ``None`` exactly
    #: when ``observed_at`` is ``None``. A mapper may only choose
    #: ``SOURCE_PUBLICATION`` for a field its provider documents as the
    #: observation's publication instant; a date-only value, a record-modified
    #: time or a BMO/AMC tag is never an ``observed_at``.
    observed_at_basis: AvailabilityBasis | None = None

    def __post_init__(self) -> None:
        _require_blank_free(self.ticker, "canonical earnings event requires a non-blank ticker")
        _require_int(self.fiscal_year, "fiscal_year")
        _require_range(self.fiscal_year, _MIN_FISCAL_YEAR, _MAX_FISCAL_YEAR, "fiscal_year")
        if self.fiscal_quarter is not None:
            _require_int(self.fiscal_quarter, "fiscal_quarter")
            _require_range(
                self.fiscal_quarter,
                _MIN_FISCAL_QUARTER,
                _MAX_FISCAL_QUARTER,
                "fiscal_quarter",
            )
        if isinstance(self.event_date, datetime) or not isinstance(self.event_date, date):
            # datetime is a `date` subclass, so it is rejected explicitly: a
            # calendar date must not silently carry a hidden time-of-day.
            message = f"event_date must be a plain date, got {type(self.event_date).__name__}"
            raise ValueError(message)
        if self.event_timestamp is not None:
            _require_aware(self.event_timestamp, "event_timestamp")
        _require_member(self.timing, EarningsTiming, "timing")
        for field_name in (
            "reported_eps",
            "estimated_eps",
            "reported_revenue",
            "estimated_revenue",
        ):
            _require_optional_decimal(getattr(self, field_name), field_name)
        if self.currency is not None:
            _require_currency(self.currency)
        if self.observed_at is not None:
            _require_aware(self.observed_at, "observed_at")
        if (self.observed_at is None) != (self.observed_at_basis is None):
            message = "observed_at and observed_at_basis must be set together or both be None"
            raise ValueError(message)
        if self.observed_at_basis is not None:
            _require_member(self.observed_at_basis, AvailabilityBasis, "observed_at_basis")


@dataclass(frozen=True, slots=True)
class ReferenceResolution:
    """The outcome of resolving one reference to stored entities.

    Returned rather than written in place so a caller can inspect what would
    happen -- and so a dry-run mode can report it without mutating anything.
    """

    #: The reference that was resolved.
    reference: CanonicalEquityReference

    #: Resolved listing, i.e. the tradable symbol on a venue.
    listing_id: uuid.UUID

    #: Resolved security, i.e. the instrument.
    security_id: uuid.UUID

    #: Resolved company, i.e. the issuer.
    company_id: uuid.UUID

    #: The key the company was matched under, e.g. ``"cik:0000320193"``. Recorded
    #: so an operator can see *why* two references were considered the same
    #: issuer, which is the question to ask when a match turns out to be wrong.
    matched_on: str

    #: True when new rows were created, False when existing ones were reused.
    created: bool


_CIK_PATTERN = re.compile(r"^[0-9]{10}$")
_ACCESSION_PATTERN = ACCESSION_REGEX


@dataclass(frozen=True, slots=True)
class CanonicalSecFiling:
    """One SEC submission's metadata, provider-independent and ready to persist.

    Source facts only: no EPS, no surprise, no signal direction, no session, no
    price and no execution eligibility. An ``8-K`` is a filing; nothing here says
    it reports earnings.

    * ``accession_number`` is the immutable identity, dashed
      (``0000320193-24-000123``). It is *not* the registrant's CIK -- its
      leading filer id can be a filing agent -- so ``cik`` is carried
      separately as the registrant the metadata was fetched for.
    * ``accepted_at`` is the acceptance instant the source states, UTC (the
      submissions JSON is read under an empirically validated rule, see
      ``providers.sec.mapper``). It is acceptance metadata: not first public
      disclosure, not public availability, not a known-as-of time.
    * ``filing_date`` (the date the SEC deems the filing made) and
      ``report_date`` (the source's report date) are dates, never instants, and
      neither is ever promoted to an ``accepted_at``.
    """

    cik: str
    accession_number: str
    form: SecFormType
    filing_date: date
    accepted_at: datetime
    report_date: date | None = None
    primary_document: str | None = None
    #: The source's 8-K item codes verbatim, e.g. ``"2.02,9.01"``; ``None`` if absent.
    items: str | None = None

    def __post_init__(self) -> None:
        if not _CIK_PATTERN.fullmatch(self.cik):
            message = f"cik must be exactly 10 digits (zero-padded), got {self.cik!r}"
            raise ValueError(message)
        if not _ACCESSION_PATTERN.fullmatch(self.accession_number):
            message = f"accession_number must look like 0000320193-24-000123, got {self.accession_number!r}"
            raise ValueError(message)
        _require_member(self.form, SecFormType, "form")
        if type(self.filing_date) is not date:
            message = f"filing_date must be a plain date, got {type(self.filing_date).__name__}"
            raise ValueError(message)
        if self.report_date is not None and type(self.report_date) is not date:
            message = f"report_date must be a plain date, got {type(self.report_date).__name__}"
            raise ValueError(message)
        if self.accepted_at.tzinfo is None or self.accepted_at.utcoffset() != timedelta(0):
            message = f"accepted_at must be a timezone-aware UTC datetime, got {self.accepted_at!r}"
            raise ValueError(message)
        if self.filing_date < EDGAR_ERA_START:
            message = f"filing_date {self.filing_date} predates EDGAR ({EDGAR_ERA_START})"
            raise ValueError(message)
        for field_name, limit in (
            ("primary_document", PRIMARY_DOCUMENT_LENGTH),
            ("items", ITEMS_LENGTH),
        ):
            value = getattr(self, field_name)
            if value is None:
                continue
            if not value.strip():
                message = f"{field_name} must be None or non-blank"
                raise ValueError(message)
            if len(value) > limit:
                message = f"{field_name} is longer than the stored limit of {limit} characters"
                raise ValueError(message)


@dataclass(frozen=True, slots=True)
class CanonicalStockSplit:
    """One provider-stated share-count event, provider-independent and ready to resolve.

    Orientation is explicit rather than inherited from a vendor's names:
    ``old_shares`` is the number of shares before, ``new_shares`` the number after,
    so the holder's share count is multiplied by ``new_shares / old_shares``. A
    2-for-1 split is ``old_shares=1, new_shares=2``; a 1-for-10 reverse split is
    ``old_shares=10, new_shares=1``. Direction is *not* assumed (``new_shares`` may
    be smaller), and a fractional ratio (a stock dividend, 1 -> 1.027) is valid.

    ``execution_date`` is the calendar date the split takes effect (all trading on
    it is already adjusted). It is not an availability time: no announcement or
    publication instant exists in the source, so none is carried. ``ticker`` is how
    the provider named the issuer; it is resolved to a security through the listing
    valid on ``execution_date`` and is never an identity.

    Nothing here adjusts a price, a share count or a cost basis.
    """

    provider_split_id: str
    ticker: str
    execution_date: date
    old_shares: Decimal
    new_shares: Decimal
    adjustment_type: StockSplitAdjustmentType

    def __post_init__(self) -> None:
        for name, limit in (
            ("provider_split_id", PROVIDER_SPLIT_ID_LENGTH),
            ("ticker", PROVIDER_TICKER_LENGTH),
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                message = f"{name} must be a non-blank string without surrounding whitespace"
                raise ValueError(message)
            if len(value) > limit:
                message = f"{name} is longer than the stored limit of {limit} characters"
                raise ValueError(message)
        if self.ticker != self.ticker.upper():
            message = f"ticker must be upper-case, got {self.ticker!r}"
            raise ValueError(message)
        if type(self.execution_date) is not date:
            message = (
                f"execution_date must be a plain date, got {type(self.execution_date).__name__}"
            )
            raise ValueError(message)
        for name in ("old_shares", "new_shares"):
            value = getattr(self, name)
            if not isinstance(value, Decimal):
                message = f"{name} must be a Decimal, got {type(value).__name__}"
                raise ValueError(message)
            if not value.is_finite() or value <= 0:
                message = f"{name} must be a finite number greater than zero, got {value}"
                raise ValueError(message)
        _require_member(self.adjustment_type, StockSplitAdjustmentType, "adjustment_type")
        # Orientation is the central risk of this contract, so the provider's own
        # classification is checked against the ratio it sent. A contradiction means
        # one of them is wrong (or the two are swapped), and which one cannot be told;
        # storing either as a fact would make the guard's answer depend on a guess.
        # A ratio of exactly 1 changes no share count and is not a split.
        if self.new_shares == self.old_shares:
            message = f"old_shares and new_shares are both {self.old_shares}: no share-count change"
            raise ValueError(message)
        shrinks = self.new_shares < self.old_shares
        if shrinks != (self.adjustment_type is StockSplitAdjustmentType.REVERSE_SPLIT):
            message = (
                f"adjustment_type {self.adjustment_type.value} contradicts the ratio "
                f"{self.old_shares} -> {self.new_shares}"
            )
            raise ValueError(message)


#: An OpenFIGI identifier is 12 characters: ``BBG`` + 8 alphanumerics + a check digit.
_FIGI_PATTERN: Final = re.compile(r"BBG[A-Z0-9]{9}")


def _require_ticker(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        message = f"{field_name} must be a non-blank string without surrounding whitespace"
        raise ValueError(message)
    if value != value.upper():
        message = f"{field_name} must be upper-case, got {value!r}"
        raise ValueError(message)
    if len(value) > PROVIDER_TICKER_LENGTH:
        message = f"{field_name} is longer than the stored limit of {PROVIDER_TICKER_LENGTH}"
        raise ValueError(message)


_SANE_DATES: Final = (date(1900, 1, 1), date(2100, 12, 31))


def _require_plain_date(value: object, field_name: str) -> None:
    if type(value) is not date:
        message = f"{field_name} must be a plain date, got {type(value).__name__}"
        raise ValueError(message)
    if not _SANE_DATES[0] <= value <= _SANE_DATES[1]:
        message = f"{field_name} {value} is outside {_SANE_DATES[0]}..{_SANE_DATES[1]}"
        raise ValueError(message)


@dataclass(frozen=True, slots=True)
class CanonicalTickerSnapshot:
    """One provider record from a DATED ticker listing: "as of ``as_of_date``, this ticker ...".

    It proves what the provider says for that one date and nothing around it: two snapshots do
    not make a continuous interval. ``as_of_date`` is the request's date parameter, which the
    provider documents as the point in time the ticker was available; the response states no
    date of its own. ``primary_mic`` is the provider's primary exchange as returned and is NOT
    documented as of ``as_of_date``: it is kept on this record (the raw response keeps it too)
    but is never persisted as a listing fact and never used as venue evidence by the resolver
    (see ``quantforge.providers.massive.mapper.SNAPSHOT_ATTRIBUTES_ARE_AS_OF_DATE``).
    The identifiers establish WHICH instrument was returned, not that they were in effect on
    ``as_of_date``. ``share_class_figi`` is the security-level anchor; ``composite_figi`` is
    exchange-composite level (a different thing, never substituted); ``cik`` is registrant
    (company) identity, never security identity. ``delisted_date`` is the provider's "last date
    the asset was traded" (inclusive), present only for delisted tickers.
    """

    as_of_date: date
    ticker: str
    active: bool
    share_class_figi: str | None = None
    composite_figi: str | None = None
    cik: str | None = None
    primary_mic: str | None = None
    delisted_date: date | None = None

    def __post_init__(self) -> None:
        _require_plain_date(self.as_of_date, "as_of_date")
        _require_ticker(self.ticker, "ticker")
        stated_active: object = self.active
        if not isinstance(stated_active, bool):
            message = f"active must be a bool, got {type(stated_active).__name__}"
            raise ValueError(message)
        for name in ("share_class_figi", "composite_figi"):
            value = getattr(self, name)
            if value is not None and not _FIGI_PATTERN.fullmatch(value):
                message = f"{name} must look like BBG followed by 9 alphanumerics, got {value!r}"
                raise ValueError(message)
        if self.cik is not None and not _CIK_PATTERN.fullmatch(self.cik):
            message = f"cik must be exactly 10 digits (zero-padded), got {self.cik!r}"
            raise ValueError(message)
        if self.primary_mic is not None and not re.fullmatch(r"[A-Z0-9]{4}", self.primary_mic):
            message = f"primary_mic must be a 4-character MIC, got {self.primary_mic!r}"
            raise ValueError(message)
        if self.delisted_date is not None:
            _require_plain_date(self.delisted_date, "delisted_date")


@dataclass(frozen=True, slots=True)
class CanonicalTickerChange:
    """A provider ticker-change event: ``ticker`` became the symbol on ``effective_date``.

    The date's exact meaning (first trading day of the new symbol) is the documented intent of
    Massive's experimental ``ticker_change`` event but is NOT yet verified against live data; it
    is treated as an inclusive start. It says nothing about venue, continuity of trading, or
    identity beyond "the entity the provider attributes the event list to".
    """

    ticker: str
    effective_date: date

    def __post_init__(self) -> None:
        _require_ticker(self.ticker, "ticker")
        _require_plain_date(self.effective_date, "effective_date")


@dataclass(frozen=True, slots=True)
class CanonicalTickerEvents:
    """The ticker-change events the provider returned for one queried identifier.

    ``requested_ticker`` is the symbol that was asked about: the provider returns events for the
    entity CURRENTLY represented by it. ``changes`` are in ascending date order.
    """

    requested_ticker: str
    changes: tuple[CanonicalTickerChange, ...]
    name: str | None = None

    def __post_init__(self) -> None:
        _require_ticker(self.requested_ticker, "requested_ticker")
        dates = [change.effective_date for change in self.changes]
        if dates != sorted(dates):
            message = "changes must be in ascending date order"
            raise ValueError(message)


# ── Accession-bound financial facts ─────────────────────────────────────────────────────

#: A concept local name is an XML NCName: letters, digits, ``_``, ``-`` and ``.``, not starting
#: with a digit, ``-`` or ``.``. ASCII only here so the identity index key stays bounded.
_TAG_PATTERN: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
#: A taxonomy release (``us-gaap/2024``, ``dei/2023``) or, for an extension, the accession that
#: defined it.
_VERSION_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
_MAX_FACT_INTEGER_DIGITS: Final = FACT_VALUE_PRECISION - FACT_VALUE_SCALE
_FACT_VALUE_QUANTUM: Final = Decimal(1).scaleb(-FACT_VALUE_SCALE)
_FISCAL_YEAR_END_PATTERN: Final = re.compile(r"[0-9]{4}")
_FISCAL_PERIOD_PATTERN: Final = re.compile(r"[A-Z0-9]{2}")
#: Single-valued ``qtrs`` ceiling. The source rounds to whole quarters and 0 is a point in time;
#: a longer cumulative span exists (inception-to-date) but is bounded by the column width.
_MAX_DURATION_QUARTERS: Final = 1000
_MAX_COREGISTRANTS: Final = 10_000
#: Control and surrogate characters are never part of source text; everything else (non-ASCII
#: letters, no-break space, format characters) is stored as stated.
_FORBIDDEN_TEXT_CATEGORIES: Final = frozenset({"Cc", "Cs"})


def _require_printable_ascii(value: object, field_name: str, limit: int) -> None:
    if not isinstance(value, str):
        message = f"{field_name} must be a string, got {type(value).__name__}"
        raise ValueError(message)
    if len(value) > limit:
        message = f"{field_name} is longer than the stored limit of {limit} characters"
        raise ValueError(message)
    if not (value.isascii() and value.isprintable()):
        message = f"{field_name} must be printable ASCII, got {value!r}"
        raise ValueError(message)
    if value != value.strip():
        message = f"{field_name} must not have surrounding whitespace, got {value!r}"
        raise ValueError(message)


def _require_free_text(value: object, field_name: str, limit: int) -> None:
    """Verbatim source text: any printable characters, no surrounding whitespace, bounded.

    Unlike a concept ``tag`` (an ASCII local name), a dimension or co-registrant string is source
    free text and may legitimately carry non-ASCII letters; it is stored as the source stated it.
    """
    if not isinstance(value, str):
        message = f"{field_name} must be a string, got {type(value).__name__}"
        raise ValueError(message)
    if len(value) > limit:
        message = f"{field_name} is longer than the stored limit of {limit} characters"
        raise ValueError(message)
    if any(unicodedata.category(char) in _FORBIDDEN_TEXT_CATEGORIES for char in value):
        message = f"{field_name} must not contain control characters, got {value!r}"
        raise ValueError(message)
    if value != value.strip():
        message = f"{field_name} must not have surrounding whitespace, got {value!r}"
        raise ValueError(message)


@dataclass(frozen=True, slots=True)
class CanonicalFinancialFact:
    """One numeric fact exactly as a structured source stated it, bound to an accession.

    Source-agnostic: nothing here is FSDS vocabulary and nothing here names a financial concept.
    It preserves *source concept semantics* and leaves every selection to a later layer.

    * ``accession_number`` is the filing that reported the fact (the filing identity).
    * ``tag`` + ``taxonomy_version`` are the concept identity. A tag alone is not a concept: the
      same local name can be defined by different taxonomies or by an issuer extension, and an
      extension must never be treated as the standard concept it resembles. ``taxonomy_version``
      is the taxonomy release for a standard tag and, for an extension, the accession that
      defined it.
    * ``period_end`` is the source's period end as stated (the SEC rounds it to a month end);
      it is never mapped to a calendar quarter.
    * ``duration_quarters`` is the span in whole quarters; 0 is a point in time. A quarter and a
      year-to-date amount of one concept are different facts.
    * ``unit`` is verbatim (``USD``, ``CAD``, ``shares``, ``pure``). Units are never
      converted or merged.
    * ``segments`` and ``coreg`` carry the source's dimension and co-registrant text verbatim
      (non-ASCII allowed, bounded in bytes because they sit in the identity index), ``""`` when the source states none. ``""`` is absence of a stated value; it is NOT the label
      "consolidated".
    * ``value`` is an exact ``Decimal`` that fits NUMERIC(28,4) *without rounding* (a source
      value with more precision is refused, never quantised), or ``None`` when the source states
      none.
    """

    accession_number: str
    tag: str
    taxonomy_version: str
    unit: str
    period_end: date
    duration_quarters: int
    value: Decimal | None
    segments: str = ""
    coreg: str = ""

    def __post_init__(self) -> None:
        if not _ACCESSION_PATTERN.fullmatch(self.accession_number):
            message = (
                f"accession_number must look like 0000320193-24-000123, "
                f"got {self.accession_number!r}"
            )
            raise ValueError(message)
        _require_printable_ascii(self.tag, "tag", TAG_LENGTH)
        if not _TAG_PATTERN.fullmatch(self.tag):
            message = f"tag must be an XBRL local name, got {self.tag!r}"
            raise ValueError(message)
        _require_printable_ascii(self.taxonomy_version, "taxonomy_version", VERSION_LENGTH)
        if not _VERSION_PATTERN.fullmatch(self.taxonomy_version):
            message = f"taxonomy_version is not a taxonomy release or accession: {self.taxonomy_version!r}"
            raise ValueError(message)
        _require_printable_ascii(self.unit, "unit", UNIT_LENGTH)
        if not self.unit:
            message = "unit must not be blank"
            raise ValueError(message)
        _require_plain_date(self.period_end, "period_end")
        if type(self.duration_quarters) is not int:
            message = (
                f"duration_quarters must be an int, got {type(self.duration_quarters).__name__}"
            )
            raise ValueError(message)
        _require_range(self.duration_quarters, 0, _MAX_DURATION_QUARTERS, "duration_quarters")
        _require_free_text(self.segments, "segments", SEGMENTS_LENGTH)
        _require_free_text(self.coreg, "coreg", COREG_LENGTH)
        key_bytes = len(self.segments.encode()) + len(self.coreg.encode())
        if key_bytes > IDENTITY_TEXT_MAX_BYTES:
            # Both are part of the unique identity index, whose entries PostgreSQL bounds.
            message = (
                f"segments + coreg are {key_bytes} bytes; the identity index allows "
                f"{IDENTITY_TEXT_MAX_BYTES}"
            )
            raise ValueError(message)
        if self.value is not None:
            _require_fact_value(self.value)


def _require_fact_value(value: object) -> None:
    if not isinstance(value, Decimal):
        # A float (or an int/str that was never parsed as exact decimal text) is refused: the
        # source's digits are the value.
        message = f"value must be a Decimal or None, got {type(value).__name__}"
        raise ValueError(message)
    if not value.is_finite():
        message = f"value must be finite, got {value}"
        raise ValueError(message)
    fits = value.adjusted() < _MAX_FACT_INTEGER_DIGITS and value == value.quantize(
        _FACT_VALUE_QUANTUM
    )
    if not fits:
        # More integer digits, or more fractional digits, than NUMERIC(28,4) holds exactly.
        message = (
            f"value {value} does not fit NUMERIC({FACT_VALUE_PRECISION},{FACT_VALUE_SCALE}) exactly"
        )
        raise ValueError(message)


@dataclass(frozen=True, slots=True)
class CanonicalFactSubmission:
    """What a structured source stated about one filing, alongside its facts.

    ``form``, ``filing_date`` and ``cik`` exist to be *checked against the stored filing*, never
    to create one. ``source_stated_accepted`` is the source's own acceptance text, verbatim and
    never parsed into an instant: the source does not state its timezone, and it is not
    availability evidence (``SecFiling.accepted_at`` is the filing's acceptance record).
    """

    accession_number: str
    cik: str
    form: SecFormType
    filing_date: date
    period_end: date
    coregistrant_count: int
    fiscal_year: int | None = None
    fiscal_period: str | None = None
    fiscal_year_end: str | None = None
    source_stated_accepted: str | None = None
    additional_ciks: str | None = None

    def __post_init__(self) -> None:
        if not _ACCESSION_PATTERN.fullmatch(self.accession_number):
            message = f"accession_number must look like 0000320193-24-000123, got {self.accession_number!r}"
            raise ValueError(message)
        if not _CIK_PATTERN.fullmatch(self.cik):
            message = f"cik must be exactly 10 digits (zero-padded), got {self.cik!r}"
            raise ValueError(message)
        _require_member(self.form, SecFormType, "form")
        _require_plain_date(self.filing_date, "filing_date")
        _require_plain_date(self.period_end, "period_end")
        _require_int(self.coregistrant_count, "coregistrant_count")
        _require_range(self.coregistrant_count, 1, _MAX_COREGISTRANTS, "coregistrant_count")
        if self.fiscal_year is not None:
            _require_int(self.fiscal_year, "fiscal_year")
            _require_range(self.fiscal_year, _MIN_FISCAL_YEAR, _MAX_FISCAL_YEAR, "fiscal_year")
        if self.fiscal_period is not None and not _FISCAL_PERIOD_PATTERN.fullmatch(
            self.fiscal_period
        ):
            message = (
                f"fiscal_period must be {FISCAL_PERIOD_LENGTH} upper-case alphanumerics, "
                f"got {self.fiscal_period!r}"
            )
            raise ValueError(message)
        if self.fiscal_year_end is not None and not _FISCAL_YEAR_END_PATTERN.fullmatch(
            self.fiscal_year_end
        ):
            message = f"fiscal_year_end must be {FISCAL_YEAR_END_LENGTH} digits (mmdd), got {self.fiscal_year_end!r}"
            raise ValueError(message)
        if self.source_stated_accepted is not None:
            # Verbatim source text, deliberately NOT a datetime grammar: the SEC documents
            # 'yyyy-mm-dd hh:mm:ss' but real rows state 'yyyy-mm-dd hh:mm:ss.0', and the timezone
            # is not stated. Only non-empty, bounded, printable ASCII without surrounding space.
            if not self.source_stated_accepted:
                message = "source_stated_accepted must not be empty (use None when absent)"
                raise ValueError(message)
            _require_printable_ascii(
                self.source_stated_accepted, "source_stated_accepted", SOURCE_ACCEPTED_LENGTH
            )
        if self.additional_ciks is not None:
            _require_printable_ascii(
                self.additional_ciks, "additional_ciks", ADDITIONAL_CIKS_LENGTH
            )
