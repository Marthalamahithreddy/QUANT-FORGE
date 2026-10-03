"""The instrument graph: securities, venues, listings and external identifiers.

This module holds the four tables that make up QuantForge's security master.
Keeping them together is deliberate: they are one tightly-coupled aggregate
(distinct companies -> securities -> listings on venues) and splitting them
across modules would create import cycles for no navigational benefit.

The layering, and why each level exists
--------------------------------------
``Exchange``   -- a venue, identified by its ISO 10383 Market Identifier Code.
``Company``    -- the issuer (see :mod:`quantforge.models.company`).
``Security``   -- an instrument the issuer created, e.g. one class of shares.
``Listing``    -- that instrument trading under a given ticker on a given venue.
``SecurityIdentifier`` -- external codes (ISIN/CUSIP/...) naming the instrument.

A ticker is a property of a ``Listing`` over a time interval, never of a
company and never of a security globally. The same instrument can trade as
``AAPL`` on Nasdaq, as ``AAPL.MI`` on Borsa Italiana and as an unsponsored
ADR line on another venue, and any of those tickers can be retired and reused
by a completely different issuer years later.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from quantforge.db.base import Base
from quantforge.models.base import (
    COUNTRY_LENGTH,
    CURRENCY_LENGTH,
    IDENTIFIER_VALUE_LENGTH,
    MIC_LENGTH,
    NAME_LENGTH,
    SHORT_NAME_LENGTH,
    TIMEZONE_LENGTH,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from quantforge.models.enums import (
    IDENTIFIER_TYPE_LENGTH,
    SECURITY_TYPE_LENGTH,
    IdentifierType,
    SecurityType,
    enum_check,
    enum_type,
)

if TYPE_CHECKING:
    from quantforge.models.company import Company
    from quantforge.models.market import DailyPriceBar

__all__ = ["Exchange", "Listing", "Security", "SecurityIdentifier"]

#: Upper bound on a ticker symbol. Generous enough for decorated forms such as
#: "BRK.B" or "SAP.DE" while staying far below the 255-character name width.
TICKER_LENGTH = 32


class Exchange(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A trading venue, identified by its MIC.

    The MIC is the identity, not the display name: venues are renamed and
    rebranded (and vendors spell the same name differently), whereas a MIC is
    assigned once by ISO 10383 and is stable for the life of the venue.

    ``timezone`` stores an IANA zone name such as ``"America/New_York"``, not a
    fixed UTC offset. A venue's local clock offset changes with daylight
    saving, so a stored ``"-05:00"`` would be wrong for half the year. Session
    boundaries, auction times and anything else venue-local derive from this
    string at read time.

    The IANA name itself is not database-validated: membership in
    ``pg_timezone_names`` is a catalog lookup, not an immutable expression, so
    PostgreSQL will not accept it inside a ``CHECK`` constraint. The schema
    layer validates it against the standard library's ``zoneinfo``.
    """

    __tablename__ = "exchange"
    __table_args__ = (
        sa.CheckConstraint(
            "country_code IS NULL OR country_code ~ '^[A-Z]{2}$'",
            name="country_code_iso3166_alpha2",
        ),
        sa.CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'",
            name="currency_iso4217",
        ),
        sa.CheckConstraint(
            "timezone <> ''",
            name="timezone_not_blank",
        ),
        # ISO 10383 segment codes are exactly four upper-case alphanumerics. Without
        # this the column would accept 'xint' as a distinct venue from 'XINT',
        # which is precisely the case a case-insensitive lookup cannot rescue:
        # two rows would claim the same venue and `get_by_mic` could only ever
        # return one of them.
        sa.CheckConstraint(
            "mic ~ '^[A-Z0-9]{4}$'",
            name="mic_iso10383",
        ),
        sa.Index("ix_exchange_country_code", "country_code"),
    )

    mic: Mapped[str] = mapped_column(
        sa.String(MIC_LENGTH),
        nullable=False,
        unique=True,
        doc="ISO 10383 Market Identifier Code, e.g. 'XNYS'. Globally unique.",
    )
    name: Mapped[str] = mapped_column(
        sa.String(NAME_LENGTH),
        nullable=False,
        doc="Venue's full name; not an identity and not unique.",
    )
    acronym: Mapped[str | None] = mapped_column(
        sa.String(SHORT_NAME_LENGTH),
        nullable=True,
        doc="Common short form, e.g. 'NYSE'. Nullable; purely presentational.",
    )
    country_code: Mapped[str | None] = mapped_column(
        sa.String(COUNTRY_LENGTH),
        nullable=True,
        doc="ISO 3166-1 alpha-2 code of the venue's jurisdiction.",
    )
    timezone: Mapped[str] = mapped_column(
        sa.String(TIMEZONE_LENGTH),
        nullable=False,
        doc="IANA zone name, e.g. 'America/New_York'. Not a fixed UTC offset.",
    )
    currency: Mapped[str | None] = mapped_column(
        sa.String(CURRENCY_LENGTH),
        nullable=True,
        doc="ISO 4217 code the venue primarily quotes in; nullable if mixed.",
    )
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean,
        nullable=False,
        default=True,
        server_default=sa.true(),
        doc="False for a venue no longer covered; retained so history resolves.",
    )

    listings: Mapped[list[Listing]] = relationship(
        back_populates="exchange",
        lazy="select",
        doc="Listings of instruments on this venue.",
    )

    def __repr__(self) -> str:
        return f"Exchange(mic={self.mic!r}, name={self.name!r})"


class Security(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An instrument issued by a company.

    A ``Security`` is *not* a ticker. It is the thing a listing points at, and
    it outlives any particular ticker: an issuer that renames its share class
    from "Class A Common Stock" to "Common Stock" keeps the same ``Security``
    row and closes one ``Listing`` interval while opening another.

    ``security_type`` distinguishes instruments at the *instrument* level
    because that changes what the data means: an ADR is a different claim on the
    issuer than the primary share, and a fund share is not equity at all. It is
    a short, extensible vocabulary rather than a set of columns, so new
    instrument kinds do not require new tables or a rewrite of the price path.

    ``(company_id, name)`` is unique. Two instruments of the same issuer sharing
    an identical name is effectively always a duplicate-ingestion artefact, and
    catching it in the database is cheaper than reconciling it later. This is one
    of the constraints most worth revisiting before bulk ingestion (see the
    milestone report).

    No separate index on ``company_id``: the unique constraint's btree already
    has ``company_id`` as its leading column and serves company lookups.
    """

    __tablename__ = "security"
    __table_args__ = (
        enum_check("security_type", SecurityType),
        sa.UniqueConstraint(
            "company_id",
            "name",
            name="uq_security_company_id_name",
        ),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name="currency_iso4217",
        ),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("company.id", ondelete="RESTRICT"),
        nullable=False,
        doc="Issuer that created this instrument.",
    )
    security_type: Mapped[SecurityType] = mapped_column(
        enum_type(SecurityType, name="security_type", length=SECURITY_TYPE_LENGTH),
        nullable=False,
        doc="Instrument class; drives how prices and events should be interpreted.",
    )
    name: Mapped[str] = mapped_column(
        sa.String(NAME_LENGTH),
        nullable=False,
        doc="Instrument name, e.g. 'Class A Common Stock'.",
    )
    currency: Mapped[str] = mapped_column(
        sa.String(CURRENCY_LENGTH),
        nullable=False,
        doc="ISO 4217 code the instrument is denominated in.",
    )
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean,
        nullable=False,
        default=True,
        server_default=sa.true(),
        doc="False once delisted/withdrawn; retained so history still resolves.",
    )

    company: Mapped[Company] = relationship(back_populates="securities", lazy="joined")
    listings: Mapped[list[Listing]] = relationship(
        back_populates="security",
        lazy="select",
        order_by="Listing.valid_from",
        doc="Venue listings for this instrument, ordered by start of validity.",
    )
    identifiers: Mapped[list[SecurityIdentifier]] = relationship(
        back_populates="security",
        lazy="select",
        doc="External codes naming this instrument.",
    )

    def __repr__(self) -> str:
        return f"Security(id={self.id!r}, name={self.name!r}, type={self.security_type!r})"


class Listing(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An instrument trading under a ticker on one venue, over a time interval.

    Ticker history is the reason this table exists. A ticker is a *label on a
    venue at a time*, and treating it as an attribute of the security would lose
    both the history and the fact that two venues can use different tickers for
    the same instrument.

    Half-open interval semantics
    ---------------------------
    Validity is ``[valid_from, valid_to)``: ``valid_from`` is inclusive and
    ``valid_to`` is exclusive. A listing that starts 2020-01-02 and ends
    2023-04-10 covers 2020-01-02 through 2023-04-09 inclusive. The benefit is
    that two consecutive periods can share an endpoint -- a ticker change on
    2023-04-10 makes ``valid_to = 2023-04-10`` for the old listing and
    ``valid_from = 2023-04-10`` for the new one with no gap and no ambiguity
    about which one owned that day. A closed form ``[from, to]`` would force
    those boundaries to collide.

    ``valid_to IS NULL`` therefore means "still listed": the interval is open
    and extends to the present. ``valid_to <= valid_from`` is rejected.

    Uniqueness, and what is deliberately not enforced
    ------------------------------------------------
    * ``UNIQUE (exchange_id, ticker, valid_from)`` blocks a duplicated listing
      record for the same period on the same venue.
    * A partial ``UNIQUE (exchange_id, ticker) WHERE valid_to IS NULL`` blocks
      two simultaneously-current listings sharing a ticker on one venue, which
      would make ticker resolution ambiguous.
    * A partial ``UNIQUE (security_id) WHERE is_primary AND valid_to IS NULL``
      blocks two current primary listings for one instrument, which would leave
      "the" primary venue undefined.

    A general non-overlap guarantee across all ticker periods would need a
    PostgreSQL ``EXCLUDE USING gist`` constraint with a ``daterange`` column.
    That is the right tool, but it forces ``btree_gist`` and a range type onto
    this table, which is more machinery than this milestone warrants. The
    constraints above catch the realistic corruption cases; residual overlap is
    caught by the schema layer's interval validation and by a reconciliation
    check when ingestion lands. Revisit before bulk ticker history lands.

    ``ticker`` is stored upper-case, enforced by a ``CHECK``. Without that,
    ``aapl`` and ``AAPL`` would resolve to two different listings and price
    joins would silently split. Mixed-case venue conventions are a known
    deviation from this canonicalisation and are listed for revisit.
    """

    __tablename__ = "listing"
    __table_args__ = (
        # Half-open interval: valid_to is exclusive, so it must be strictly
        # greater than valid_from. NULL valid_to means "still listed".
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="listing_interval_ordered",
        ),
        sa.CheckConstraint("ticker <> ''", name="ticker_not_blank"),
        sa.CheckConstraint("ticker = upper(ticker)", name="ticker_canonical_upper"),
        sa.UniqueConstraint(
            "exchange_id",
            "ticker",
            "valid_from",
            name="uq_listing_exchange_id_ticker_valid_from",
        ),
        # At most one currently-active listing per (venue, ticker).
        sa.Index(
            "uq_listing_current_exchange_ticker",
            "exchange_id",
            "ticker",
            unique=True,
            postgresql_where=sa.text("valid_to IS NULL"),
        ),
        # At most one currently-active primary listing per security.
        sa.Index(
            "uq_listing_current_primary_security",
            "security_id",
            unique=True,
            postgresql_where=sa.text("is_primary AND valid_to IS NULL"),
        ),
        sa.Index("ix_listing_security_id", "security_id"),
    )

    security_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("security.id", ondelete="RESTRICT"),
        nullable=False,
        doc="Instrument being listed.",
    )
    exchange_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("exchange.id", ondelete="RESTRICT"),
        nullable=False,
        doc="Venue the instrument trades on.",
    )
    ticker: Mapped[str] = mapped_column(
        sa.String(TICKER_LENGTH),
        nullable=False,
        doc="Venue-specific symbol, canonicalised to upper case.",
    )
    valid_from: Mapped[date] = mapped_column(
        sa.Date,
        nullable=False,
        doc=(
            "Inclusive first day this OPERATIONAL record covers. For rows created by reference "
            "ingestion it is the date QuantForge FIRST OBSERVED the listing -- not the historical "
            "listing start -- so it proves nothing about earlier dates. Source-stated history lives "
            "in ListingHistoryFact and is combined with this row only by quantforge.identity."
        ),
    )
    valid_to: Mapped[date | None] = mapped_column(
        sa.Date,
        nullable=True,
        doc="Exclusive last day; NULL means still listed (interval open-ended).",
    )
    is_primary: Mapped[bool] = mapped_column(
        sa.Boolean,
        nullable=False,
        default=False,
        server_default=sa.false(),
        doc="True for the instrument's main/most liquid venue listing.",
    )

    security: Mapped[Security] = relationship(back_populates="listings", lazy="joined")
    exchange: Mapped[Exchange] = relationship(back_populates="listings", lazy="joined")
    price_bars: Mapped[list[DailyPriceBar]] = relationship(
        back_populates="listing",
        lazy="select",
        order_by="DailyPriceBar.trading_date",
        doc="Daily observations recorded under this listing identity.",
    )

    def __repr__(self) -> str:
        return (
            f"Listing(ticker={self.ticker!r}, "
            f"valid_from={self.valid_from!r}, valid_to={self.valid_to!r})"
        )


class SecurityIdentifier(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """An external code (ISIN, CUSIP, SEDOL, FIGI) naming a security.

    Kept in its own table rather than as columns on ``Security`` because no
    instrument has every scheme, an instrument can hold several codes of the
    same scheme across time (a CUSIP changes when an issuer reorganises), and
    the set of schemes grows as more markets are covered.

    ``UNIQUE (identifier_type, identifier_value)`` is intentional: an ISIN or
    CUSIP identifies exactly one instrument globally, which is what makes
    identifier-first lookup the natural ingestion join key. The consequence is
    that a re-used code must be resolved by closing the old interval before
    opening the new one, not inserted in parallel -- the right behaviour, since
    silently dual-owning one ISIN is precisely the corruption this schema exists
    to prevent.

    Validity is half-open ``[valid_from, valid_to)`` like
    :class:`Listing`, and both endpoints are nullable: an identifier whose
    effective dates the source never disclosed is legitimately open-ended at
    both ends. That is why the interval check tolerates a null ``valid_from`` --
    but if ``valid_to`` is present, ``valid_from`` must be too, otherwise the
    interval is unbounded below and the ordering rule is unverifiable.
    """

    __tablename__ = "security_identifier"
    __table_args__ = (
        enum_check("identifier_type", IdentifierType),
        sa.CheckConstraint(
            "valid_to IS NULL OR (valid_from IS NOT NULL AND valid_to > valid_from)",
            name="identifier_interval_ordered",
        ),
        sa.CheckConstraint("identifier_value <> ''", name="identifier_value_not_blank"),
        sa.UniqueConstraint(
            "identifier_type",
            "identifier_value",
            name="uq_security_identifier_type_value",
        ),
        # One FIGI code names one security however its LEVEL is labelled: the same literal under
        # ``FIGI`` (legacy, level unknown) and ``FIGI_SHARE_CLASS`` must not point at two securities.
        sa.Index(
            "uq_security_identifier_figi_family_value",
            "identifier_value",
            unique=True,
            postgresql_where=sa.text("identifier_type IN ('FIGI', 'FIGI_SHARE_CLASS')"),
        ),
        sa.Index("ix_security_identifier_security_id", "security_id"),
        sa.Index("ix_security_identifier_source_id", "source_id"),
    )

    security_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("security.id", ondelete="RESTRICT"),
        nullable=False,
        doc="Instrument this code names.",
    )
    identifier_type: Mapped[IdentifierType] = mapped_column(
        enum_type(IdentifierType, name="identifier_type", length=IDENTIFIER_TYPE_LENGTH),
        nullable=False,
        doc="Identifier scheme, e.g. ISIN.",
    )
    identifier_value: Mapped[str] = mapped_column(
        sa.String(IDENTIFIER_VALUE_LENGTH),
        nullable=False,
        doc="The code itself, e.g. 'US0378331005'.",
    )
    valid_from: Mapped[date | None] = mapped_column(
        sa.Date,
        nullable=True,
        doc="Inclusive first day of validity; NULL when the source gave none.",
    )
    valid_to: Mapped[date | None] = mapped_column(
        sa.Date,
        nullable=True,
        doc="Exclusive last day; NULL when the code is still in use.",
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("data_source.id", ondelete="RESTRICT"),
        nullable=True,
        doc="Provider that asserted this identifier; NULL when unattributed.",
    )

    security: Mapped[Security] = relationship(back_populates="identifiers", lazy="joined")

    def __repr__(self) -> str:
        return f"SecurityIdentifier(type={self.identifier_type!r}, value={self.identifier_value!r})"
