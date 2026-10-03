"""Accession-bound financial facts, and the source artifacts they were extracted from.

Three tables, each answering one question:

``FinancialFactArtifact``
    "Which structured source artifact did QuantForge read?" One row per distinct
    package *content* -- the SEC Financial Statement Data Sets quarterly ZIP, named
    ``2025q3`` and identified by its SHA-256. The bytes live in the raw store, never
    in a row. Two different digests under one package name are a *revision* of the
    package, which ingestion refuses to apply silently (the SEC did reprocess every
    package in December 2024).

``FinancialFactSubmission``
    "What did that artifact say about this accession?" One row per
    ``(artifact, SecFiling)``: the source-stated filing context (fiscal focus,
    fiscal year end, the rounded balance-sheet date, co-registrant list, and the
    source's own acceptance string, verbatim and uninterpreted).

``FinancialFact``
    One numeric fact, exactly as the source stated it, bound to exactly one
    existing :class:`~quantforge.models.filing.SecFiling`. It records the filing
    that reported it; a later accession never mutates it, and an amendment (its own
    accession) is its own set of facts.

What these tables are not
-------------------------
* **Not a point-in-time claim.** There is deliberately no ``observed_at``,
  ``available_at`` or availability basis here. ``SecFiling.accepted_at`` says when
  EDGAR accepted the filing; the bulk FSDS artifact is published later, on a
  quarterly cadence the SEC does not time-stamp per package. Reading ``accepted_at``
  as the moment a *fact in a bulk artifact* became available would be a look-ahead
  error in the optimistic direction, so no such field exists to be misused.
  ``ingested_at`` is when QuantForge stored the row, which is not availability
  evidence either.
* **Not semantic.** Nothing says "EPS", "revenue" or "quarter". A fact is a source
  concept (``tag`` + ``version``), a period end, a duration in quarters, a unit, an
  optional dimension string and an exact value.

Fact identity (the SEC's own key, with the accession resolved to a filing)
--------------------------------------------------------------------------
``(sec_filing_id, tag, version, period_end, duration_quarters, unit, segments,
coreg)`` -- the FSDS NUM unique key. ``value`` is *not* part of it: a differing value
under one identity is a conflict to surface, never a second fact and never a quiet
overwrite. ``tag`` alone is not a concept (the same local name can be defined by
different taxonomies or by an issuer extension); ``version`` -- the taxonomy
release, or for an extension the accession that defined it -- is part of the
identity. ``duration_quarters`` is part of it too: a quarter and a year-to-date
amount of one concept in one accession are two facts and must stay two rows.

``segments`` and ``coreg`` are stored as ``''`` when the source states nothing (a
TSV cannot distinguish NULL from empty). This is *absence of a stated value*, and
is deliberately not labelled "consolidated".
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from quantforge.db.base import Base
from quantforge.models.base import CreatedAtMixin, UUIDPrimaryKeyMixin, timestamptz, utcnow
from quantforge.models.enums import (
    FACT_SOURCE_KIND_LENGTH,
    FinancialFactSourceKind,
    enum_check,
    enum_type,
)
from quantforge.models.filing import SecFiling

__all__ = [
    "ARTIFACT_NAME_LENGTH",
    "FACT_VALUE_PRECISION",
    "FACT_VALUE_SCALE",
    "IDENTITY_TEXT_MAX_BYTES",
    "FinancialFact",
    "FinancialFactArtifact",
    "FinancialFactSubmission",
]

ARTIFACT_NAME_LENGTH = 64
SHA256_HEX_LENGTH = 64
RAW_PATH_LENGTH = 512
TAG_LENGTH = 256
VERSION_LENGTH = 20
UNIT_LENGTH = 20
SEGMENTS_LENGTH = 1024
COREG_LENGTH = 256
#: ``segments`` and ``coreg`` sit in the unique identity index. PostgreSQL bounds a btree entry
#: (about 2.7 KB), so their combined UTF-8 size is bounded well inside that, whatever the text.
IDENTITY_TEXT_MAX_BYTES = 2000
#: FSDS NUM ``value``: NUMERIC(28,4), "not scaled ... limited to four digits to the
#: right of the decimal point". The source states no ``decimals`` attribute, so no
#: precision metadata is stored: the 4-dp cap is a property of the source.
FACT_VALUE_PRECISION = 28
FACT_VALUE_SCALE = 4
FISCAL_PERIOD_LENGTH = 2
FISCAL_YEAR_END_LENGTH = 4
#: ``accepted`` is stored as the source's own text, never parsed. The SEC documents it as
#: ``yyyy-mm-dd hh:mm:ss`` (19), but real FSDS rows (2026q1) carry ``yyyy-mm-dd hh:mm:ss.0`` (21),
#: so the schema must not encode the sampled length. 64 is a conservative bound for a short
#: source-text field (the same width the schema uses for other short provenance strings).
SOURCE_ACCEPTED_LENGTH = 64
ADDITIONAL_CIKS_LENGTH = 120
#: Sanity bounds on a period end, mirroring the listing-history date CHECK.
_DATE_FLOOR = "DATE '1900-01-01'"
_DATE_CEILING = "DATE '2100-12-31'"


class FinancialFactArtifact(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One distinct structured-source package (by content). See the module docstring."""

    __tablename__ = "financial_fact_artifact"
    __table_args__ = (
        sa.UniqueConstraint(
            "source_kind", "artifact_name", "sha256", name="uq_financial_fact_artifact_content"
        ),
        enum_check("source_kind", FinancialFactSourceKind),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_format"),
        sa.CheckConstraint("byte_length > 0", name="byte_length_positive"),
        sa.Index("ix_financial_fact_artifact_source_id", "source_id"),
        sa.Index("ix_financial_fact_artifact_ingestion_run_id", "ingestion_run_id"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("data_source.id", ondelete="RESTRICT"),
        nullable=False,
        doc="The data source that published the artifact (the SEC source).",
    )
    source_kind: Mapped[FinancialFactSourceKind] = mapped_column(
        enum_type(
            FinancialFactSourceKind,
            name="financial_fact_source_kind",
            length=FACT_SOURCE_KIND_LENGTH,
        ),
        nullable=False,
        doc="Which structured source this artifact is (the extraction adapter's contract).",
    )
    artifact_name: Mapped[str] = mapped_column(
        sa.String(ARTIFACT_NAME_LENGTH),
        nullable=False,
        doc="Logical package identity within its kind, e.g. '2025q3'. NOT content identity.",
    )
    sha256: Mapped[str] = mapped_column(
        sa.String(SHA256_HEX_LENGTH),
        nullable=False,
        doc="SHA-256 of the package bytes (content identity). Same name, new digest = revision.",
    )
    byte_length: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    raw_artifact_path: Mapped[str] = mapped_column(
        sa.String(RAW_PATH_LENGTH),
        nullable=False,
        doc="Raw-store path of the preserved package, relative to the store root.",
    )
    member_digests: Mapped[dict[str, Any]] = mapped_column(
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
        doc=(
            "SHA-256 of each package member that was parsed (e.g. sub.txt, num.txt), so a "
            "fact traces to the exact file inside the package."
        ),
    )
    num_has_segments: Mapped[bool] = mapped_column(
        sa.Boolean,
        nullable=False,
        doc=(
            "Whether the NUM file carried the 'segments' column (added by the SEC's December "
            "2024 reprocessing). When False the source documents that only entity-wide or "
            "co-registrant data was compiled."
        ),
    )
    ingestion_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("ingestion_run.id", ondelete="RESTRICT"),
        nullable=True,
        doc="The run that first registered this artifact; never rewritten.",
    )
    ingested_at: Mapped[datetime] = mapped_column(
        timestamptz(),
        nullable=False,
        default=utcnow,
        doc=(
            "When QuantForge registered the artifact (UTC). Not the SEC's publication time, "
            "which the SEC does not state per package."
        ),
    )

    def __repr__(self) -> str:
        return f"FinancialFactArtifact({self.artifact_name!r}, sha256={self.sha256[:12]!r})"


class FinancialFactSubmission(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """What one artifact stated about one accession's filing context."""

    __tablename__ = "financial_fact_submission"
    __table_args__ = (
        sa.UniqueConstraint(
            "artifact_id", "sec_filing_id", name="uq_financial_fact_submission_artifact_filing"
        ),
        # Target of the composite FK on financial_fact: a fact's submission must belong to the
        # same filing the fact is bound to.
        sa.UniqueConstraint("id", "sec_filing_id", name="uq_financial_fact_submission_id_filing"),
        sa.Index("ix_financial_fact_submission_sec_filing_id", "sec_filing_id"),
        sa.CheckConstraint(
            f"period_end >= {_DATE_FLOOR} AND period_end <= {_DATE_CEILING}",
            name="period_end_range",
        ),
        sa.CheckConstraint("coregistrant_count >= 1", name="coregistrant_count_positive"),
    )

    artifact_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("financial_fact_artifact.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sec_filing_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("sec_filing.id", ondelete="RESTRICT"),
        nullable=False,
        doc="The existing filing this accession resolved to (never created from the artifact).",
    )
    period_end: Mapped[date] = mapped_column(
        sa.Date,
        nullable=False,
        doc="The source's balance-sheet date, which the SEC rounds to the nearest month end.",
    )
    fiscal_year: Mapped[int | None] = mapped_column(
        sa.SmallInteger, nullable=True, doc="Filer-stated fiscal-year focus, verbatim."
    )
    fiscal_period: Mapped[str | None] = mapped_column(
        sa.String(FISCAL_PERIOD_LENGTH),
        nullable=True,
        doc="Filer-stated fiscal-period focus (e.g. 'Q2', 'FY'), verbatim. Not interpreted.",
    )
    fiscal_year_end: Mapped[str | None] = mapped_column(
        sa.String(FISCAL_YEAR_END_LENGTH),
        nullable=True,
        doc="Fiscal year end as 'mmdd', rounded by the source to the nearest month end.",
    )
    source_accepted: Mapped[str | None] = mapped_column(
        sa.String(SOURCE_ACCEPTED_LENGTH),
        nullable=True,
        doc=(
            "The artifact's acceptance text, verbatim and uninterpreted (real packages state "
            "'yyyy-mm-dd hh:mm:ss.0'; the timezone is not stated). Never parsed, normalised or "
            "timezone-annotated, and never availability evidence; SecFiling.accepted_at is the "
            "filing's acceptance record."
        ),
    )
    coregistrant_count: Mapped[int] = mapped_column(
        sa.SmallInteger,
        nullable=False,
        doc="The source's 'nciks': registrants in the consolidating entity's submission.",
    )
    additional_ciks: Mapped[str | None] = mapped_column(
        sa.String(ADDITIONAL_CIKS_LENGTH),
        nullable=True,
        doc="The source's 'aciks': co-registrant CIKs, space-delimited, verbatim.",
    )

    artifact: Mapped[FinancialFactArtifact] = relationship(lazy="raise")
    sec_filing: Mapped[SecFiling] = relationship(lazy="raise")

    def __repr__(self) -> str:
        return f"FinancialFactSubmission(artifact_id={self.artifact_id!r}, filing={self.sec_filing_id!r})"


class FinancialFact(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """One source-stated numeric fact bound to one filing. See the module docstring."""

    __tablename__ = "financial_fact"
    __table_args__ = (
        sa.UniqueConstraint(
            "sec_filing_id",
            "tag",
            "version",
            "period_end",
            "duration_quarters",
            "unit",
            "segments",
            "coreg",
            name="uq_financial_fact_identity",
        ),
        sa.ForeignKeyConstraint(
            ["submission_id", "sec_filing_id"],
            ["financial_fact_submission.id", "financial_fact_submission.sec_filing_id"],
            name="fk_financial_fact_submission_same_filing",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"octet_length(segments) + octet_length(coreg) <= {IDENTITY_TEXT_MAX_BYTES}",
            name="identity_text_bytes",
        ),
        sa.CheckConstraint("duration_quarters >= 0", name="duration_quarters_non_negative"),
        sa.CheckConstraint(
            f"period_end >= {_DATE_FLOOR} AND period_end <= {_DATE_CEILING}",
            name="period_end_range",
        ),
        sa.CheckConstraint("tag <> ''", name="tag_not_blank"),
        sa.CheckConstraint("version <> ''", name="version_not_blank"),
        sa.CheckConstraint("unit <> ''", name="unit_not_blank"),
        sa.Index("ix_financial_fact_submission_id", "submission_id"),
        sa.Index("ix_financial_fact_ingestion_run_id", "ingestion_run_id"),
    )

    sec_filing_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("sec_filing.id", ondelete="RESTRICT"),
        nullable=False,
        doc="The filing (accession) that reported this fact. Never re-pointed.",
    )
    submission_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        nullable=False,
        doc=(
            "The artifact-and-filing record this fact was first read from (provenance). Bound "
            "to the same filing as sec_filing_id by a composite foreign key."
        ),
    )
    tag: Mapped[str] = mapped_column(
        sa.String(TAG_LENGTH), nullable=False, doc="Source concept name. Not a concept by itself."
    )
    version: Mapped[str] = mapped_column(
        sa.String(VERSION_LENGTH),
        nullable=False,
        doc=(
            "Taxonomy release of a standard tag (e.g. 'us-gaap/2024'); for an issuer extension, "
            "the accession that defined it. Part of the concept identity."
        ),
    )
    period_end: Mapped[date] = mapped_column(
        sa.Date,
        nullable=False,
        doc=(
            "The source's end date for the value, rounded by FSDS to the nearest month end. "
            "Not mapped to a calendar quarter and not the exact context date."
        ),
    )
    duration_quarters: Mapped[int] = mapped_column(
        sa.SmallInteger,
        nullable=False,
        doc=(
            "FSDS 'qtrs': quarters represented, rounded to a whole number; 0 means a point in "
            "time. 1 is a quarter, 2/3 are year-to-date, 4 is a year -- but nothing here "
            "selects among them."
        ),
    )
    unit: Mapped[str] = mapped_column(
        sa.String(UNIT_LENGTH), nullable=False, doc="Unit of measure verbatim (USD, shares, ...)."
    )
    segments: Mapped[str] = mapped_column(
        sa.String(SEGMENTS_LENGTH),
        nullable=False,
        default="",
        doc="Axis/member string verbatim, '' when the source states none. Not parsed.",
    )
    coreg: Mapped[str] = mapped_column(
        sa.String(COREG_LENGTH),
        nullable=False,
        default="",
        doc=(
            "Co-registrant/parent/other entity verbatim, '' when none. The SEC documents a NULL "
            "coreg as the consolidated entity; QuantForge records only the absence and applies "
            "no such label (interpretation belongs to a later layer)."
        ),
    )
    value: Mapped[Decimal | None] = mapped_column(
        sa.Numeric(FACT_VALUE_PRECISION, FACT_VALUE_SCALE),
        nullable=True,
        doc=(
            "Exact source value, not scaled; the source caps it at four decimal places. NULL "
            "means the source stated no value (the SEC marks NUM value nullable): not zero, "
            "not a missing row, and not a valid numeric observation."
        ),
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
        doc="When QuantForge stored this fact (UTC). Not availability evidence.",
    )

    submission: Mapped[FinancialFactSubmission] = relationship(lazy="raise")

    def __repr__(self) -> str:
        return (
            f"FinancialFact(filing={self.sec_filing_id!r}, {self.tag!r}@{self.version!r}, "
            f"{self.period_end}, qtrs={self.duration_quarters}, {self.unit!r}, {self.value})"
        )
