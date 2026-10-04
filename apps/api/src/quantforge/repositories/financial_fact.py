"""Deterministic reads over accession-bound financial facts.

Reads only. Writing is the ingestion service's job, inside a run, so every row has provenance.

What this layer will NOT do
---------------------------
It filters; it never *chooses*. It does not decide which fact is "the" EPS, "the quarter", the
"comparable prior period" or which of two units to prefer, and it never collapses a quarter and a
year-to-date amount of one concept (they differ in ``duration_quarters`` and both are returned).
A concept is always addressed by ``tag`` **and** ``version``: asking for a tag alone would merge
standard taxonomies with issuer extensions of the same local name. Facts of an amendment (its own
accession) are never merged into the original's, and no query reads a later accession to adjust an
earlier one -- there is no "latest" anywhere here.

Every list is ordered by the full fact identity, so the same database always yields the same order.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from quantforge.models.company import Company
from quantforge.models.enums import FinancialFactSourceKind, SecFormType
from quantforge.models.filing import ACCESSION_PATTERN, SecFiling
from quantforge.models.financial_fact import (
    FinancialFact,
    FinancialFactArtifact,
    FinancialFactSubmission,
)

__all__ = [
    "FactTrace",
    "FilingFactContext",
    "FilingIndexResult",
    "FilingIndexRow",
    "FinancialFactRepository",
    "is_extension_version",
]

_ACCESSION = re.compile(ACCESSION_PATTERN)
_ORDER = (
    FinancialFact.tag,
    FinancialFact.version,
    FinancialFact.period_end,
    FinancialFact.duration_quarters,
    FinancialFact.unit,
    FinancialFact.segments,
    FinancialFact.coreg,
)


@dataclass(frozen=True, slots=True)
class FactTrace:
    """Where one fact came from, with the distinct time concepts kept distinct.

    ``filing_accepted_at`` is EDGAR's acceptance of the filing. ``artifact_ingested_at`` is when
    QuantForge stored the package. Neither is when the fact became *available* in the bulk artifact,
    which the SEC does not state per package; there is deliberately no field claiming it.
    """

    fact_id: uuid.UUID
    accession_number: str
    company_cik: str | None
    form: str
    filing_accepted_at: datetime
    source_kind: FinancialFactSourceKind
    artifact_name: str
    artifact_sha256: str
    artifact_raw_path: str
    member_digests: dict[str, str]
    artifact_ingested_at: datetime
    ingestion_run_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class FilingFactContext:
    """One stored filing of an accession, with the period ends its FSDS submissions state.

    Plain stored facts, no interpretation. An empty ``submission_period_ends`` means no financial
    facts were ingested for this filing. No acceptance or ingestion time is carried: this exists to
    scope a semantic read, not to say when anything was known.
    """

    sec_filing_id: uuid.UUID
    accession_number: str
    company_cik: str | None
    form: SecFormType
    submission_period_ends: tuple[date, ...]


@dataclass(frozen=True, slots=True)
class FilingIndexRow:
    """One stored filing that has FSDS submission data: plain stored metadata, nothing derived.

    Identity is the stored ``SecFiling`` row, i.e. ``(company, accession)``: one joint accession
    stored under two registrants is two rows. No internal id, acceptance or ingestion time is
    carried: ``filing_date`` and ``report_date`` are source metadata, never availability evidence.
    ``fsds_period_end`` is the period every one of the filing's FSDS submissions agree on, or
    ``None`` when they disagree (no latest/earliest/first is ever chosen).
    """

    accession_number: str
    company_cik: str | None
    form: SecFormType
    filing_date: date
    report_date: date | None
    fsds_period_end: date | None


@dataclass(frozen=True, slots=True)
class FilingIndexResult:
    """A page of index rows and the total number of logical rows matching the filters."""

    rows: tuple[FilingIndexRow, ...]
    total: int


class FinancialFactRepository:
    """Queries over ``financial_fact`` and its provenance."""

    __slots__ = ("_session",)

    def __init__(self, session: Session) -> None:
        self._session = session

    def filing_index(
        self, *, limit: int, offset: int, form: SecFormType | None = None, cik: str | None = None
    ) -> FilingIndexResult:
        """Stored filings that have at least one FSDS submission, newest filing date first.

        Eligibility is an EXISTS test and the period is a per-filing correlated aggregate, so a
        filing with several submissions is still exactly one row: ``total`` and the page both
        count logical filings.
        ``form`` (exact stored form, no amendment folding) and ``cik`` combine with AND. The order
        is total: ``filing_date`` DESC, accession DESC, CIK DESC (NULLs last), then the row id as a
        final tie-break. It never uses an acceptance or ingestion time.
        """
        submissions = FinancialFactSubmission
        # Eligibility is EXISTS (never a join), so a filing with many submissions stays one row.
        eligible = sa.exists().where(submissions.sec_filing_id == SecFiling.id)
        # period_end is NOT NULL, so DISTINCT counts real dates only; disagreement is NULL.
        fsds_period_end = (
            sa.select(
                sa.case(
                    (
                        sa.func.count(sa.distinct(submissions.period_end)) == 1,
                        sa.func.min(submissions.period_end),
                    ),
                    else_=sa.null(),
                )
            )
            .where(submissions.sec_filing_id == SecFiling.id)
            .correlate(SecFiling)
            .scalar_subquery()
        )
        filters: list[sa.ColumnElement[bool]] = [eligible]
        if form is not None:
            filters.append(SecFiling.form == form)
        if cik is not None:
            filters.append(Company.cik == cik)
        total = self._session.scalar(
            sa.select(sa.func.count())
            .select_from(SecFiling)
            .join(Company, Company.id == SecFiling.company_id)
            .where(*filters)
        )
        rows = self._session.execute(
            sa.select(
                SecFiling.accession_number,
                Company.cik.label("company_cik"),
                SecFiling.form,
                SecFiling.filing_date,
                SecFiling.report_date,
                fsds_period_end.label("fsds_period_end"),
            )
            .join(Company, Company.id == SecFiling.company_id)
            .where(*filters)
            .order_by(
                SecFiling.filing_date.desc(),
                SecFiling.accession_number.desc(),
                Company.cik.desc().nulls_last(),
                SecFiling.id,
            )
            .limit(limit)
            .offset(offset)
        ).all()
        return FilingIndexResult(
            rows=tuple(
                FilingIndexRow(
                    accession_number=row.accession_number,
                    company_cik=row.company_cik,
                    form=row.form,
                    filing_date=row.filing_date,
                    report_date=row.report_date,
                    fsds_period_end=row.fsds_period_end,
                )
                for row in rows
            ),
            total=total or 0,
        )

    def filing_fact_contexts(
        self, accession_number: str, *, company_cik: str | None = None
    ) -> list[FilingFactContext]:
        """Every stored filing row of the accession (a joint filing can have several), by id.

        With ``company_cik`` only the filing of the company with that exact CIK is considered (a
        CIK is unique to one company, so at most one row). Nothing falls back to the other
        registrants of the accession: an unmatched CIK yields no context.
        """
        statement = (
            sa.select(SecFiling.id, SecFiling.accession_number, SecFiling.form, Company.cik)
            .join(Company, Company.id == SecFiling.company_id)
            .where(SecFiling.accession_number == accession_number)
        )
        if company_cik is not None:
            statement = statement.where(Company.cik == company_cik)
        filings = self._session.execute(statement.order_by(SecFiling.id)).all()
        if not filings:
            return []
        period_ends: dict[uuid.UUID, set[date]] = {row.id: set() for row in filings}
        for filing_id, period_end in self._session.execute(
            sa.select(
                FinancialFactSubmission.sec_filing_id, FinancialFactSubmission.period_end
            ).where(FinancialFactSubmission.sec_filing_id.in_(list(period_ends)))
        ):
            period_ends[filing_id].add(period_end)
        return [
            FilingFactContext(
                sec_filing_id=row.id,
                accession_number=row.accession_number,
                company_cik=row.cik,
                form=row.form,
                submission_period_ends=tuple(sorted(period_ends[row.id])),
            )
            for row in filings
        ]

    def facts_for_filing_tag(self, sec_filing_id: uuid.UUID, tag: str) -> list[FinancialFact]:
        """Every stored fact of one filing with this exact tag: all versions, periods, durations,
        units and dimensions. No selection; identity order."""
        return list(
            self._session.scalars(
                sa.select(FinancialFact)
                .where(FinancialFact.sec_filing_id == sec_filing_id, FinancialFact.tag == tag)
                .order_by(*_ORDER)
            )
        )

    def facts_for_accession(self, accession_number: str) -> list[FinancialFact]:
        """Every stored fact the accession reported, in identity order. No selection."""
        return list(
            self._session.scalars(
                sa.select(FinancialFact)
                .join(SecFiling, SecFiling.id == FinancialFact.sec_filing_id)
                .where(SecFiling.accession_number == accession_number)
                .order_by(*_ORDER)
            )
        )

    def facts_for_concept(  # noqa: PLR0913 - one keyword per identity component
        self,
        accession_number: str,
        *,
        tag: str,
        version: str,
        period_end: date | None = None,
        duration_quarters: int | None = None,
        unit: str | None = None,
    ) -> list[FinancialFact]:
        """Facts of one concept (``tag`` AND ``version``) reported by one accession.

        The optional arguments only narrow. With none given, every period, duration, unit,
        dimension and co-registrant variant is returned -- a quarter and a year-to-date amount
        alike -- because choosing among them is a later layer's job.
        """
        query = (
            sa.select(FinancialFact)
            .join(SecFiling, SecFiling.id == FinancialFact.sec_filing_id)
            .where(
                SecFiling.accession_number == accession_number,
                FinancialFact.tag == tag,
                FinancialFact.version == version,
            )
            .order_by(*_ORDER)
        )
        if period_end is not None:
            query = query.where(FinancialFact.period_end == period_end)
        if duration_quarters is not None:
            query = query.where(FinancialFact.duration_quarters == duration_quarters)
        if unit is not None:
            query = query.where(FinancialFact.unit == unit)
        return list(self._session.scalars(query))

    def trace(self, fact_id: uuid.UUID) -> FactTrace | None:
        """Provenance of one fact: filing, package, member digests, run. ``None`` if absent."""
        row = self._session.execute(
            sa.select(
                FinancialFact.id,
                FinancialFact.ingestion_run_id,
                SecFiling.accession_number,
                SecFiling.form,
                SecFiling.accepted_at,
                Company.cik,
                FinancialFactArtifact.source_kind,
                FinancialFactArtifact.artifact_name,
                FinancialFactArtifact.sha256,
                FinancialFactArtifact.raw_artifact_path,
                FinancialFactArtifact.member_digests,
                FinancialFactArtifact.ingested_at,
            )
            .join(SecFiling, SecFiling.id == FinancialFact.sec_filing_id)
            .join(Company, Company.id == SecFiling.company_id)
            .join(
                FinancialFactSubmission, FinancialFactSubmission.id == FinancialFact.submission_id
            )
            .join(
                FinancialFactArtifact,
                FinancialFactArtifact.id == FinancialFactSubmission.artifact_id,
            )
            .where(FinancialFact.id == fact_id)
        ).one_or_none()
        if row is None:
            return None
        return FactTrace(
            fact_id=row.id,
            accession_number=row.accession_number,
            company_cik=row.cik,
            form=row.form.value,
            filing_accepted_at=row.accepted_at,
            source_kind=row.source_kind,
            artifact_name=row.artifact_name,
            artifact_sha256=row.sha256,
            artifact_raw_path=row.raw_artifact_path,
            member_digests=dict(row.member_digests),
            artifact_ingested_at=row.ingested_at,
            ingestion_run_id=row.ingestion_run_id,
        )


def is_extension_version(version: str) -> bool:
    """Whether a fact's ``version`` is an accession, i.e. the concept is an issuer extension.

    FSDS: ``version`` is the taxonomy release for a standard tag and the accession that defined it
    otherwise (``custom = 1`` is stated to be "technically redundant" with that). Reported so a
    caller can tell an extension from a standard concept; nothing maps the extension to a standard
    one.
    """
    return _ACCESSION.fullmatch(version) is not None
