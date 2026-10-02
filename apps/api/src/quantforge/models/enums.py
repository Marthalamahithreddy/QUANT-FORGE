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
    FIGI = "FIGI"
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
#: Lifecycle status of an ingestion run; see ``IngestionStatus``.
INGESTION_STATUS_LENGTH = 16
