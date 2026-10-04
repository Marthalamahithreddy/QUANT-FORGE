"""SEC filing metadata: one row per (company, accession).

A filing is **not** an earnings event. ``SecFiling`` records only what the SEC
says about a submission -- its identity, form, dates and acceptance time -- so
that "what filing existed by time T" is answerable. It carries no EPS, no
surprise, no signal, no session and no price; an ``8-K`` is merely a filing and
nothing here says it reports earnings.

Identity
--------
The SEC **accession number** is the immutable identity of a submission, stored
normalised with dashes (``0000320193-24-000123``: a 10-digit filer id, a 2-digit
year, a 6-digit sequence). The uniqueness key is ``(company_id,
accession_number)`` and not the accession alone, because one joint submission
legitimately appears under several registrants' CIKs. The accession's leading
filer id is *not* the registrant's CIK (it can be a filing agent), so a CIK is
never derived from it; ``company.cik`` is the bridge, set elsewhere.

Amendments (``10-K/A``, ``10-Q/A``, ``8-K/A``) are separate accessions and
separate rows, each keeping its own ``accepted_at``; nothing is merged or
overwritten, so the point-in-time question "which version existed by T" stays
answerable.

Timestamps
----------
``accepted_at`` is the acceptance instant the SEC submissions JSON states, as a UTC
``TIMESTAMPTZ``, read under an empirically validated rule (see
``quantforge.providers.sec.mapper``; the field's official definition is unresolved).
It is acceptance metadata: it proves the filing was accepted by EDGAR then. It does **not** prove first public disclosure (a press release can
precede an ``8-K`` by up to four business days) or when any market participant
learned of the content, and it is deliberately not named ``first_public_at``.
``filing_date`` is the date the SEC deems the filing made (a filing accepted
after 5:30 p.m. Eastern is dated the next business day), a different fact from
the acceptance instant. ``report_date`` is the source's "report date"
(period end for periodic reports, the event date for an ``8-K``); it can be
absent.

Like the other fact tables there is no ``updated_at``: a filing row is a source
fact, never edited in place.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from quantforge.db.base import Base
from quantforge.models.base import CreatedAtMixin, UUIDPrimaryKeyMixin, timestamptz, utcnow
from quantforge.models.enums import SEC_FORM_LENGTH, SecFormType, enum_check, enum_type

__all__ = ["SecFiling"]

#: ``0000320193-24-000123``: 10-digit filer id, 2-digit year, 6-digit sequence.
ACCESSION_PATTERN = r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$"
ACCESSION_REGEX = re.compile(ACCESSION_PATTERN)
ACCESSION_LENGTH = 20
#: EDGAR electronic filing began in 1993; matches the ``filing_date_edgar_era`` CHECK.
EDGAR_ERA_START = date(1993, 1, 1)
PRIMARY_DOCUMENT_LENGTH = 255
ITEMS_LENGTH = 256


class SecFiling(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One SEC submission's metadata for one registrant. See the module docstring."""

    __tablename__ = "sec_filing"
    __table_args__ = (
        sa.UniqueConstraint(
            "company_id", "accession_number", name="uq_sec_filing_company_accession"
        ),
        enum_check("form", SecFormType),
        sa.CheckConstraint(
            f"accession_number ~ '{ACCESSION_PATTERN}'", name="accession_number_format"
        ),
        sa.CheckConstraint("filing_date >= DATE '1993-01-01'", name="filing_date_edgar_era"),
        sa.Index("ix_sec_filing_company_id_filing_date", "company_id", "filing_date"),
        sa.Index("ix_sec_filing_source_id", "source_id"),
        sa.Index("ix_sec_filing_ingestion_run_id", "ingestion_run_id"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("company.id", ondelete="RESTRICT"),
        nullable=False,
        doc="The registrant, resolved through company.cik (never guessed from a name).",
    )
    accession_number: Mapped[str] = mapped_column(
        sa.String(ACCESSION_LENGTH),
        nullable=False,
        doc="SEC accession number, dashed (the immutable identity of the submission).",
    )
    form: Mapped[SecFormType] = mapped_column(
        enum_type(SecFormType, name="sec_form_type", length=SEC_FORM_LENGTH),
        nullable=False,
        doc="Form type exactly as the SEC states it; amendments are their own members.",
    )
    filing_date: Mapped[date] = mapped_column(
        sa.Date,
        nullable=False,
        doc="The date the SEC deems the filing made (not the acceptance instant).",
    )
    report_date: Mapped[date | None] = mapped_column(
        sa.Date,
        nullable=True,
        doc="The source's report date (period end, or event date for an 8-K), if stated.",
    )
    accepted_at: Mapped[datetime] = mapped_column(
        timestamptz(),
        nullable=False,
        doc=(
            "When EDGAR accepted the submission (UTC). Proves acceptance by EDGAR, "
            "not first public disclosure."
        ),
    )
    primary_document: Mapped[str | None] = mapped_column(
        sa.String(PRIMARY_DOCUMENT_LENGTH),
        nullable=True,
        doc="File name of the primary document, as stated. Never downloaded here.",
    )
    items: Mapped[str | None] = mapped_column(
        sa.String(ITEMS_LENGTH),
        nullable=True,
        doc=(
            "The source's 8-K item codes verbatim (e.g. '2.02,9.01'), so a later slice "
            "can classify a filing without re-fetching. Not interpreted here."
        ),
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("data_source.id", ondelete="RESTRICT"),
        nullable=False,
        doc="Provider that reported this filing (the SEC source).",
    )
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("ingestion_run.id", ondelete="RESTRICT"),
        nullable=True,
        doc="Run that wrote this row; set on insert, never rewritten.",
    )
    ingested_at: Mapped[datetime] = mapped_column(
        timestamptz(),
        nullable=False,
        default=utcnow,
        doc="When QuantForge received and stored this filing (UTC). Not availability evidence.",
    )

    def __repr__(self) -> str:
        return f"SecFiling(company_id={self.company_id!r}, accession={self.accession_number!r})"
