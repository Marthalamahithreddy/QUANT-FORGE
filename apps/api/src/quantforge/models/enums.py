"""Closed vocabularies for the financial domain, and how they reach PostgreSQL.

Why ``VARCHAR`` + ``CHECK`` rather than a native PostgreSQL ``ENUM`` type
---------------------------------------------------------------------
A native ``ENUM`` is the obvious choice and is faster to compare, but it is the
single worst type to extend in a research system:

* **Values can be added but never removed.** There is no ``ALTER TYPE ... DROP
  VALUE``. Once a mistyped or abandoned value has been written, the vocabulary
  is permanent and every future migration carries it.
* **Renaming is impossible** without recreating the type and every dependent
  column.
* **Adding a value requires a hand-written migration.** Alembic autogenerate
  does not detect value-set changes on an ``ENUM``, so the drift between the
  Python enum and the database type is invisible until a write fails at
  runtime.
* **Two enum types with overlapping values are unrelated types.** A value added
  to one cannot be read as the other without an explicit cast.

A ``VARCHAR`` column with a named ``CHECK`` constraint gets the correctness
benefit -- the database still rejects a value the application does not know --
with none of the lock-in: widening the ``CHECK`` is an ordinary constraint
change, the value set is greppable, and the column participates in normal
indexing and comparison.

The trade-off we accept: SQLAlchemy autogenerate also does not diff ``CHECK``
constraint bodies, so **adding an enum member requires a hand-written
migration** (a single ``op.execute`` altering the constraint). That is a
deliberate, reviewable step rather than a silent one, and it is called out in
``apps/api/README.md``.

Validation therefore happens in three places, deliberately:
1. Python ``StrEnum`` membership (attribute access, exhaustive ``match``).
2. SQLAlchemy ``validate_strings=True`` on assignment.
3. A database ``CHECK`` constraint -- the authority, since a row can arrive
   from a raw SQL script, a ``COPY`` or a future ingestion worker.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

import sqlalchemy as sa


class SecurityType(StrEnum):
    """What kind of instrument a :class:`~quantforge.models.security.Security` is.

    Deliberately a *type* of instrument rather than a listing attribute. A
    common share and an ADR of the same issuer are different securities with
    different identifiers and different venues, so instrument class belongs
    here and never on the company.

    Membership is intentionally short. Derivatives are *not* modelled yet; when
    they are, contracts, strikes and expiries will need their own tables
    hanging off a security rather than new members of a growing enum.
    """

    EQUITY = "EQUITY"
    PREFERRED_EQUITY = "PREFERRED_EQUITY"
    DEPOSITORY_RECEIPT = "DEPOSITORY_RECEIPT"
    ETF = "ETF"
    MUTUAL_FUND = "MUTUAL_FUND"
    BOND = "BOND"
    OTHER = "OTHER"


class IdentifierType(StrEnum):
    """External identifier schemes carried by :class:`SecurityIdentifier`.

    These are the codes a counterparty, custodian or data vendor actually uses
    to name an instrument. They are kept in their own table rather than as
    columns on ``Security`` because (a) no instrument has every scheme, (b) an
    instrument can hold several of the same scheme across time, and (c) the
    set of schemes grows as new markets are covered.
    """

    ISIN = "ISIN"
    CUSIP = "CUSIP"
    SEDOL = "SEDOL"
    #: Legacy, level UNSPECIFIED. An OpenFIGI identifier exists at several levels (venue,
    #: exchange-composite, share class) that name different things; a row of this type does
    #: not say which. Rows written by the Massive reference adapter were relabelled
    #: ``FIGI_SHARE_CLASS`` by migration 0010 (that adapter has only ever stored the share-class
    #: FIGI); any other row stays here and is never trusted as a share-class anchor.
    FIGI = "FIGI"
    #: The OpenFIGI SHARE-CLASS FIGI: one code for one share class across every venue and
    #: ticker. This is the security-level identity anchor (a ticker is not, and the
    #: composite FIGI is exchange-composite level, not share-class level).
    FIGI_SHARE_CLASS = "FIGI_SHARE_CLASS"
    OTHER = "OTHER"


class EarningsTiming(StrEnum):
    """When in the trading session an earnings event occurred.

    This records only what the source stated. It is *not* a tradability flag:
    whether the event is actionable is derived later from the event timestamp,
    the venue calendar and the security's listing, because a "before market"
    release at 07:00 and one at 09:31 are both ``BEFORE_MARKET`` yet have very
    different information content.
    """

    BEFORE_MARKET = "BEFORE_MARKET"
    DURING_MARKET = "DURING_MARKET"
    AFTER_MARKET = "AFTER_MARKET"
    UNKNOWN = "UNKNOWN"


class IngestionStatus(StrEnum):
    """Terminal and non-terminal states of one ingestion run.

    ``running`` is non-terminal, and is exactly what a crashed or killed process
    leaves behind -- which is why ``started_at`` and ``completed_at`` are separate
    columns rather than one nullable end time, and why a stale ``running`` row is
    a meaningful thing to find and alert on.

    ``partial`` is a real state, not a euphemism. A range containing a few
    malformed records, where everything valid was still written, is a different
    operational fact from both clean success and total failure. Collapsing it into
    either would destroy the distinction an operator actually acts on.
    """

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


class PriceBasis(StrEnum):
    """Which split-adjustment basis one stored price row is expressed in.

    Each ``DailyPriceBar`` / ``BenchmarkPriceBar`` row has exactly ONE basis and
    every price field on it (open, high, low, close, vwap, ...) belongs to that
    basis. The same listing/session/source may hold one row per basis. The
    names describe meaning, not a vendor flag, so they survive a provider
    change; the vendor translation lives in the provider adapter.

    ``AS_TRADED`` is deliberately not called "raw": ``raw`` already means the
    untouched provider payload bytes in this codebase's provenance model, and
    an as-traded *price* is parsed, validated data.
    """

    #: The provider's OHLC adjusted for stock splits only -- NOT adjusted for
    #: dividends. Continuous across splits, so a close-to-close return has no
    #: spurious split jump. This is QuantForge's research basis (Step 8
    #: returns, abnormal returns, event studies) and the basis of every row
    #: that existed before price-basis was recorded (Massive ``adjusted=true``).
    SPLIT_ADJUSTED = "SPLIT_ADJUSTED"

    #: The provider's OHLC NOT adjusted for stock splits: the prices as they
    #: printed on the day. The candidate basis for historical execution and
    #: accounting, because a real fill happened at the as-traded price. A split
    #: inside a holding period still requires a share-count adjustment, which
    #: QuantForge does not implement yet (no corporate-action engine).
    AS_TRADED = "AS_TRADED"


#: The one basis every research query must select explicitly. A named constant
#: so "research uses split-adjusted bars" is greppable and cannot drift between
#: modules; research code never relies on row order or "the only row".
RESEARCH_PRICE_BASIS: Final = PriceBasis.SPLIT_ADJUSTED


class AvailabilityBasis(StrEnum):
    """What an ``observed_at`` timestamp *proves* -- separate from whether it may be used.

    A bare timestamp cannot say whether it is a provider's own statement of
    when a result was published, the acceptance time of a regulatory filing, or
    something weaker, and those prove different things. This names the evidence;
    :data:`quantforge.research.availability.APPROVED_AVAILABILITY_BASES` decides
    which kinds QuantForge will treat as point-in-time availability. A member
    existing here never makes it tradable.

    ``observed_at`` and ``observed_at_basis`` are NULL together or present
    together (a database CHECK): a timestamp without a stated meaning, or a
    meaning with no timestamp, is never stored. A date-only value is not an
    instant and is never stored in ``observed_at``.
    """

    #: The source itself states an exact instant at which *this observation* was
    #: published. The only way a provider timestamp may be recorded as
    #: availability: a mapper may use it only for a field its documentation
    #: defines that way -- never a record-modified/updated time, a scheduled
    #: time, a date normalised to midnight, or an inference from BMO/AMC.
    SOURCE_PUBLICATION = "SOURCE_PUBLICATION"

    #: The time EDGAR accepted a specific SEC submission (accession). Proves the
    #: filing existed on EDGAR by about then -- NOT that it was the first public
    #: disclosure (a press release may precede an 8-K by up to four business
    #: days) and NOT that the market learned of it at that instant. Defined so
    #: filing evidence is named exactly; not yet approved for point-in-time use.
    SEC_FILING_ACCEPTANCE = "SEC_FILING_ACCEPTANCE"


class SecFormType(StrEnum):
    """The SEC form types QuantForge ingests as filing metadata.

    Deliberately narrow: only the forms a filing-based earnings analysis could
    need. An ``8-K`` is merely a filing -- it can report many unrelated events,
    and nothing here says it contains earnings. Amendments are their own
    members because each amendment is a separate accession with its own
    acceptance time.
    """

    FORM_10K = "10-K"
    FORM_10K_A = "10-K/A"
    FORM_10Q = "10-Q"
    FORM_10Q_A = "10-Q/A"
    FORM_8K = "8-K"
    FORM_8K_A = "8-K/A"

    @property
    def is_amendment(self) -> bool:
        return self.value.endswith("/A")


class FinancialFactSourceKind(StrEnum):
    """The structured source an accession-bound financial fact was extracted from.

    The fact model is accession-bound, not FSDS-bound: a later source (for example
    a filing-specific inline-XBRL extractor) adds a member here without replacing
    the core tables. A member naming a source says nothing about whether its facts
    are point-in-time safe.
    """

    #: The SEC Financial Statement Data Sets: quarterly bulk ZIPs of numeric facts
    #: from the primary financial statements. QuantForge's first structured
    #: extraction source -- not, by itself, a point-in-time source of record.
    SEC_FSDS = "SEC_FSDS"


class ProviderType(StrEnum):
    """The kind of organisation behind a :class:`DataSource`.

    Distinguishing a regulatory filing from a market-data vendor from a broker
    API is what lets a later ingestion layer apply different trust,
    reconciliation and licensing policies per source. ``MANUAL`` exists so an
    operator-entered figure is never silently attributed to a feed.
    """

    REGULATORY_FILING = "REGULATORY_FILING"
    EXCHANGE_FEED = "EXCHANGE_FEED"
    MARKET_DATA_VENDOR = "MARKET_DATA_VENDOR"
    BROKER_API = "BROKER_API"
    MANUAL = "MANUAL"
    OTHER = "OTHER"


class StockSplitAdjustmentType(StrEnum):
    """How a provider classifies a share-count-changing corporate action.

    Mirrors Massive's ``adjustment_type`` (``forward_split``, ``reverse_split``,
    ``stock_dividend``), upper-cased to this module's vocabulary convention. All
    three change the number of shares a holder owns, so all three are treated as
    share-count events by the corporate-action guard. An unrecognised provider
    value is rejected at the boundary, never mapped onto the nearest member.
    """

    FORWARD_SPLIT = "FORWARD_SPLIT"
    REVERSE_SPLIT = "REVERSE_SPLIT"
    STOCK_DIVIDEND = "STOCK_DIVIDEND"


class ListingFactKind(StrEnum):
    """What a :class:`~quantforge.models.listing_history.ListingHistoryFact` states.

    Every kind is a statement about ONE date; intervals are never stored here, they are
    derived later by an explicit, deterministic reconciliation.
    """

    #: "As of ``effective_date`` the source says this security traded under this ticker."
    #: Proves that single date only -- two observations do not make a continuous interval.
    TICKER_OBSERVED = "TICKER_OBSERVED"
    #: "The provider states the ticker became this security's symbol on ``effective_date``" (a
    #: ticker-change event; inclusive start PER THE PROVIDER CONTRACT as interpreted by the Massive
    #: adapter -- that meaning is provider-unverified, see ``TICKER_EVENT_DATE_SEMANTICS_VERIFIED``). Proves the era's start, and that the previous ticker ended;
    #: it does NOT prove the same exchange or uninterrupted trading.
    TICKER_STARTED = "TICKER_STARTED"
    #: "The security last traded under this ticker on ``effective_date``" (inclusive).
    LAST_TRADED = "LAST_TRADED"


def enum_type(enum_cls: type[StrEnum], *, name: str, length: int) -> sa.Enum:
    """Return a non-native :class:`sqlalchemy.Enum` for ``enum_cls``.

    ``native_enum=False`` renders ``VARCHAR(length)`` instead of a PostgreSQL
    ``ENUM``. ``create_constraint`` is deliberately **False**: SQLAlchemy's
    generated constraint is emitted *twice* by ``alembic revision
    --autogenerate`` -- once from the column type and once from the table's
    constraint list -- producing two identical ``CHECK``s under two different
    names. The constraint is therefore declared explicitly via
    :func:`enum_check`, which keeps exactly one, under a stable name.

    ``validate_strings=True`` still rejects a bad value on ORM assignment with a
    Python-side error rather than deferring to a constraint violation at flush
    time, so nothing is lost by turning the automatic constraint off.
    """
    return sa.Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=False,
        validate_strings=True,
        length=length,
        values_callable=lambda cls: [member.value for member in cls],
    )


def enum_check(column: str, enum_cls: type[StrEnum]) -> sa.CheckConstraint:
    """Return the ``CHECK`` constraint restricting ``column`` to ``enum_cls``.

    The value list is derived from the Python enum, so the database vocabulary
    can never drift from the application's. The constraint is named by the
    metadata convention (``ck_<table>_<name>``), which gives a later migration a
    stable handle for widening the set.

    Values are emitted as quoted literals. Every member of the vocabularies in
    this module is a bare upper-case identifier, so there is no quoting or
    escaping hazard; a member requiring it would fail loudly here rather than
    produce a subtly wrong constraint.
    """
    literals = ", ".join(f"'{member.value}'" for member in enum_cls)
    return sa.CheckConstraint(f"{column} IN ({literals})", name=f"{column}_enum_values")


SECURITY_TYPE_LENGTH = 32
IDENTIFIER_TYPE_LENGTH = 32
EARNINGS_TIMING_LENGTH = 32
PROVIDER_TYPE_LENGTH = 32
PRICE_BASIS_LENGTH = 32
AVAILABILITY_BASIS_LENGTH = 32
SEC_FORM_LENGTH = 16
FACT_SOURCE_KIND_LENGTH = 16
SPLIT_ADJUSTMENT_TYPE_LENGTH = 16
LISTING_FACT_KIND_LENGTH = 16
#: Lifecycle status of an ingestion run; see ``IngestionStatus``.
INGESTION_STATUS_LENGTH = 16
