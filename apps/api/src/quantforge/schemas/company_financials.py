"""Public schemas for ``GET /api/v1/companies`` and ``GET /api/v1/companies/{cik}/financials``.

Transport types over :mod:`quantforge.financials.company_financials`; they carry no financial rule.
Every financial ``Decimal`` is a fixed-point JSON string, never a number. The response is
descriptive accounting data from stored SEC FSDS facts: it makes no point-in-time claim.

The EDGAR acceptance instant is deliberately absent (``tests/test_accepted_at_firewall.py``);
``filing_date`` and ``report_date`` are source metadata and ``package_stored_at`` is when QuantForge
stored the FSDS package: none of them says when a figure was known. ``source_url`` is a link DERIVED from the CIK and accession for the reader to open; QuantForge
never requested it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from quantforge.financials.eps import EpsPeriodClass
from quantforge.financials.eps_change import (
    EpsChangeStatus,
    EpsTransition,
    PercentageUnavailableReason,
)
from quantforge.financials.period_metrics import PointStatus
from quantforge.models.company import CIK_PATTERN
from quantforge.models.enums import SecFormType
from quantforge.models.filing import ACCESSION_PATTERN
from quantforge.schemas.common import DecimalText
from quantforge.schemas.filing_index import DEFAULT_LIMIT, MAX_LIMIT, MAX_OFFSET

__all__ = [
    "BOUNDARY_NOTE",
    "ChangeSection",
    "CompanyDirectoryItem",
    "CompanyDirectoryPage",
    "CompanyDirectoryQuery",
    "CompanyFinancialsResponse",
    "CompanySummary",
    "FactSource",
    "FilingFinancialsSection",
    "MetricSection",
    "PointSection",
]

BOUNDARY_NOTE: Final = (
    "Descriptive accounting data from stored SEC FSDS facts. The prior-year figure is the "
    "comparative as presented in the same filing and may be restated. No point-in-time "
    "availability claim: nothing here says when a figure was known or that it was tradable."
)
MetricId = Literal["REVENUE", "NET_INCOME", "EPS_DILUTED"]


class CompanyDirectoryQuery(BaseModel):
    model_config = {"extra": "forbid"}

    q: str | None = Field(
        None,
        max_length=80,
        description="Name substring, exact current ticker or CIK (all matched case-insensitively).",
    )
    limit: int = Field(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    offset: int = Field(0, ge=0, le=MAX_OFFSET)


class CompanyDirectoryItem(BaseModel):
    cik: str = Field(pattern=CIK_PATTERN)
    name: str
    tickers: list[str] = Field(description="Current tickers (open listings), sorted.")
    stored_filings: int = Field(description="Stored filing rows; not every filing at the SEC.")
    filings_with_financial_facts: int


class CompanyDirectoryPage(BaseModel):
    items: list[CompanyDirectoryItem]
    total: int
    limit: int
    offset: int


class CompanySummary(BaseModel):
    cik: str = Field(pattern=CIK_PATTERN)
    name: str
    tickers: list[str]


class FactSource(BaseModel):
    """One stored fact exactly as selected; no ingestion or availability fields."""

    fact_id: uuid.UUID
    tag: str
    taxonomy_version: str
    unit: str
    period_end: date
    qtrs: int
    value: DecimalText | None = Field(description="Exact string; null if the source stated none.")


class PointSection(BaseModel):
    status: PointStatus
    reason: str
    fact: FactSource | None = Field(description="Present only for AVAILABLE and NULL_VALUE.")


class ChangeSection(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    status: EpsChangeStatus
    absolute: DecimalText | None = Field(description="current - prior, exact.")
    transition: EpsTransition | None
    ratio: DecimalText | None = Field(
        description="(current - prior) / prior as a RATIO string (0.5 = +50%), positive prior only."
    )
    ratio_unavailable_reason: PercentageUnavailableReason | None


class MetricSection(BaseModel):
    metric: MetricId
    current: PointSection
    prior: PointSection
    change: ChangeSection


class FilingFinancialsSection(BaseModel):
    accession: str = Field(pattern=ACCESSION_PATTERN)
    form: SecFormType
    period_class: EpsPeriodClass
    period_end: date | None = Field(description="Null when the filing's submissions disagree.")
    fiscal_year: int | None
    fiscal_period: str | None
    filing_date: date
    report_date: date | None
    package_stored_at: datetime = Field(
        description="When QuantForge stored the FSDS package (UTC): storage provenance only."
    )
    source_artifact: str = Field(description="The FSDS package the facts were stored from.")
    source_artifact_sha256: str
    source_url: str = Field(description="Derived EDGAR filing-index link; never fetched here.")
    metrics: list[MetricSection]


class CompanyFinancialsResponse(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    company: CompanySummary
    descriptive_only: Literal[True] = True
    no_point_in_time_claim: Literal[True] = True
    boundary_note: str = BOUNDARY_NOTE
    filings: list[FilingFinancialsSection]
