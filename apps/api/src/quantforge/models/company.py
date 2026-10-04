"""The issuer: the real-world organisation that creates securities.

``Company`` is the anchor of the instrument graph. It holds identity and
business classification, and deliberately holds **no ticker**: a ticker belongs
to a *listing* of a *security* on an *exchange*, and all three can change
independently of the issuer.
"""

from __future__ import annotations

import re
from datetime import date
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from quantforge.db.base import Base
from quantforge.models.base import (
    CIK_LENGTH,
    COUNTRY_LENGTH,
    DESCRIPTION_LENGTH,
    NAME_LENGTH,
    URL_LENGTH,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

if TYPE_CHECKING:
    from quantforge.models.earnings import EarningsEvent
    from quantforge.models.security import Security

__all__ = ["CIK_PATTERN", "CIK_REGEX", "Company"]

#: A CIK exactly as stored: ten digits, zero-padded (matches the ``cik_zero_padded_10`` CHECK).
CIK_PATTERN = r"^[0-9]{10}$"
CIK_REGEX = re.compile(CIK_PATTERN)


class Company(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A legal issuer, e.g. the corporation behind a listed share.

    Separation of concerns encoded here:

    * A company issues one or more :class:`~quantforge.models.security.Security`
      rows (ordinary vs. preferred vs. an ADR, for example).
    * A security trades on one or more
      :class:`~quantforge.models.security.Listing` rows, each with its own
      ticker and validity period.
    * A company is not a ticker, a security, or a venue, and nothing in this
      schema joins on a code to stand in for any of them.

    Field-width and constraint decisions:

    * ``legal_name``/``display_name`` are 255 characters, comfortably above the
      longest registrable names in practice while keeping the btree index on
      ``display_name`` compact.
    * ``country_code`` is ISO 3166-1 alpha-2, constrained to exactly two
      uppercase letters, so ``"United States"`` and ``"us"`` are rejected by
      the database rather than silently normalised downstream.
    * ``founded_date`` is a ``DATE``: an incorporation date is a calendar fact
      with no time or zone component, and a timestamp would imply a precision we
      do not have. It is *not* constrained against ``CURRENT_DATE`` because
      PostgreSQL forbids non-``IMMUTABLE`` expressions in a ``CHECK``, and
      because "not in the future" is a moving target that a stored constraint
      could not re-evaluate anyway. The application layer checks it instead.

    Neither name is unique. Distinct legal entities genuinely share names
    across jurisdictions -- several unrelated issuers are called
    "Banco Santander" -- so enforcing uniqueness here would reject real data
    during ingestion. Identity is the UUID.
    """

    __tablename__ = "company"
    __table_args__ = (
        sa.CheckConstraint(
            "country_code IS NULL OR country_code ~ '^[A-Z]{2}$'",
            name="country_code_iso3166_alpha2",
        ),
        sa.CheckConstraint(
            "cik IS NULL OR cik ~ '^[0-9]{10}$'",
            name="cik_zero_padded_10",
        ),
        # A CIK identifies exactly one SEC registrant, forever. Making it unique
        # is what lets ingestion resolve "is this the company I already have?"
        # without ever comparing names -- the difference between a safe
        # get-or-create and a fuzzy merge that can attach one issuer's price
        # history to another. Partial, because most rows have no CIK at all and
        # NULLs must not collide.
        sa.Index(
            "uq_company_cik",
            "cik",
            unique=True,
            postgresql_where=sa.text("cik IS NOT NULL"),
        ),
        sa.Index("ix_company_display_name", "display_name"),
        sa.Index("ix_company_country_code", "country_code"),
    )

    legal_name: Mapped[str] = mapped_column(
        sa.String(NAME_LENGTH),
        nullable=False,
        doc="Registered legal name of the issuer.",
    )
    cik: Mapped[str | None] = mapped_column(
        sa.String(CIK_LENGTH),
        nullable=True,
        doc=(
            "SEC Central Index Key, zero-padded to 10 digits. The stable issuer "
            "identity used to resolve a company across providers; null when no "
            "regulatory identifier is available. Deliberately NOT unique on "
            "legal_name: distinct entities in different jurisdictions can share a "
            "name, and a name is not a market identifier."
        ),
    )
    display_name: Mapped[str] = mapped_column(
        sa.String(NAME_LENGTH),
        nullable=False,
        doc="Name used for display and search; may differ from the legal name.",
    )
    description: Mapped[str | None] = mapped_column(
        sa.String(DESCRIPTION_LENGTH),
        nullable=True,
        doc="Free-text business description; nullable because rarely sourced.",
    )
    sector: Mapped[str | None] = mapped_column(
        sa.String(NAME_LENGTH),
        nullable=True,
        doc="Coarse classification, e.g. 'Information Technology'.",
    )
    industry: Mapped[str | None] = mapped_column(
        sa.String(NAME_LENGTH),
        nullable=True,
        doc="Finer classification within the sector.",
    )
    country_code: Mapped[str | None] = mapped_column(
        sa.String(COUNTRY_LENGTH),
        nullable=True,
        doc="ISO 3166-1 alpha-2 code of the issuer's domicile.",
    )
    website: Mapped[str | None] = mapped_column(
        sa.String(URL_LENGTH),
        nullable=True,
        doc="Issuer's public website.",
    )
    founded_date: Mapped[date | None] = mapped_column(
        sa.Date,
        nullable=True,
        doc="Date of incorporation or founding, as a calendar date.",
    )

    securities: Mapped[list[Security]] = relationship(
        back_populates="company",
        # ``selectin`` rather than the lazy default: a company's instruments are
        # almost always wanted alongside the company, and a per-row lazy load is
        # the classic N+1 in exactly this domain.
        lazy="selectin",
        order_by="Security.created_at",
        doc="Instruments issued by this company.",
    )
    earnings_events: Mapped[list[EarningsEvent]] = relationship(
        back_populates="company",
        lazy="select",
        order_by="EarningsEvent.event_date",
        doc="Reported corporate/earnings events for this company.",
    )

    def __repr__(self) -> str:
        return f"Company(id={self.id!r}, legal_name={self.legal_name!r})"
