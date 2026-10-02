"""Reusable column groups, shared constants and type factories for domain models.

Mixins exist only to remove mechanical duplication of ``id``/``created_at``
columns. Anything that carries modelling meaning -- identity, price precision,
validity semantics -- is declared explicitly on the model that owns it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

# ─────────────────────────── type factories ──────────────────────────────────
# Types are built by factory rather than shared as module-level instances.
# SQLAlchemy type objects carry per-dialect state, so a single instance reused
# across many columns is a latent source of surprising behaviour.

#: Price/scalar-precision: 20 total digits, 6 decimal places. Covers sub-cent
#: equities through to un-adjusted high-magnitude instruments while staying far
#: inside PostgreSQL's 16383-digit ``NUMERIC`` limit. Surfaced to Python as
#: ``Decimal``; a ``float`` would silently lose exactness.
PRICE_PRECISION = 20
PRICE_SCALE = 6

#: Per-share earnings. Four decimal places is finer than any exchange reports.
EPS_PRECISION = 20
EPS_SCALE = 4

#: Revenue spans cents to hundreds of billions, so more integer digits are
#: needed here than for prices.
MONEY_PRECISION = 24
MONEY_SCALE = 4

#: Column widths.
CURRENCY_LENGTH = 3  # ISO 4217 alphabetic code, e.g. "USD".
COUNTRY_LENGTH = 2  # ISO 3166-1 alpha-2 code, e.g. "US".
MIC_LENGTH = 4  # ISO 10383 Market Identifier Code, e.g. "XNYS".
TIMEZONE_LENGTH = 64  # Longest IANA zone identifier in the tz database.
SHORT_NAME_LENGTH = 128
NAME_LENGTH = 255
DESCRIPTION_LENGTH = 512
IDENTIFIER_VALUE_LENGTH = 64
#: SEC CIK is a fixed 10-digit zero-padded number.
CIK_LENGTH = 10
URL_LENGTH = 512

#: Fiscal-year bounds. Deliberately wide: this rejects data-entry mistakes, it
#: is not a claim about any real company's fiscal calendar.
MIN_FISCAL_YEAR = 1900
MAX_FISCAL_YEAR = 2200

#: Fiscal quarters are 1-based and only exist for 1..4.
MIN_FISCAL_QUARTER = 1
MAX_FISCAL_QUARTER = 4


def price_type() -> sa.Numeric[Decimal]:
    """Return the ``NUMERIC`` type used for prices and close levels."""
    return sa.Numeric(PRICE_PRECISION, PRICE_SCALE)


def eps_type() -> sa.Numeric[Decimal]:
    """Return the ``NUMERIC`` type used for per-share earnings figures."""
    return sa.Numeric(EPS_PRECISION, EPS_SCALE)


def money_type() -> sa.Numeric[Decimal]:
    """Return the ``NUMERIC`` type used for currency amounts such as revenue."""
    return sa.Numeric(MONEY_PRECISION, MONEY_SCALE)


def uuid_type() -> sa.Uuid[uuid.UUID]:
    """Return the UUID type used for every surrogate primary key.

    ``sa.Uuid`` renders as a native ``UUID`` column on PostgreSQL, so the
    column is a true 128-bit identifier rather than a 36-character string.
    """
    return sa.Uuid(as_uuid=True)


def timestamptz() -> sa.DateTime:
    """Return a tz-aware timestamp type, mapping to ``TIMESTAMPTZ`` on PostgreSQL.

    ``DateTime(timezone=True)`` stores an absolute instant plus an offset and
    normalises to UTC on write. A naive ``TIMESTAMP`` stores no offset at all
    and silently misreads daylight-saving transitions, so every instant column
    in this schema uses this factory.
    """
    return sa.DateTime(timezone=True)


def utcnow() -> datetime:
    """Return the current instant as a timezone-aware UTC datetime.

    Every stored instant goes through here so the database is UTC throughout
    and no column is ever written with a naive datetime.
    """
    return datetime.now(UTC)


def new_uuid() -> uuid.UUID:
    """Return a fresh UUID4 primary key.

    Keys are generated in Python rather than by a server-side
    ``gen_random_uuid()`` default. That keeps the identity available to
    application code that needs to reference a row before it is committed -- a
    repository building a small object graph, or a test fixture -- and it makes
    the key algorithm explicit rather than dependent on a server extension.

    Note that SQLAlchemy applies a column ``default`` at *flush* time, not at
    construction time. An un-flushed object therefore still has ``id is None``;
    an id can be assigned explicitly at construction if one is needed earlier.
    """
    return uuid.uuid4()


class UUIDPrimaryKeyMixin:
    """Adds a UUID4 surrogate primary key.

    Tickers, MICs and ISINs are all mutable or reassignable over time, so none
    of them is safe as a key. A surrogate UUID stays stable across a company
    rename, a ticker change and a re-listing on a second venue.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        uuid_type(),
        primary_key=True,
        default=new_uuid,
        # ``sort_order`` only affects physical column order in CREATE TABLE, but
        # having ``id`` first makes \\d output and DDL reviews far easier to read.
        sort_order=-10,
        doc="Surrogate UUID4 primary key; stable across renames and re-listings.",
    )


class CreatedAtMixin:
    """Adds a ``created_at`` insert instant."""

    created_at: Mapped[datetime] = mapped_column(
        timestamptz(),
        nullable=False,
        default=utcnow,
        sort_order=-5,
        doc="When QuantForge first stored this row (UTC, tz-aware).",
    )


class TimestampMixin(CreatedAtMixin):
    """Adds ``created_at`` plus a write-managed ``updated_at``.

    Reference data (companies, instruments, listings, sources) is corrected
    over time and therefore tracks edits. Market *observations* deliberately
    omit ``updated_at``: they are immutable source facts, and a correction
    arrives as a new source-identified row rather than an in-place edit.
    """

    updated_at: Mapped[datetime] = mapped_column(
        timestamptz(),
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        sort_order=-4,
        doc="When this row was last written by QuantForge (UTC, tz-aware).",
    )
