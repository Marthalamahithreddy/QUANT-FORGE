"""Public schemas for ``GET /api/v1/filings``: an index of stored filings that have FSDS data.

The index exists so a client can navigate into ``GET /api/v1/filings/{accession}/eps``. It is NOT
all SEC filings, NOT a history of what was available when, and NOT a list of tradable filings: it
lists the filings QuantForge has stored AND for which it ingested at least one FSDS financial-fact
submission. A listed filing may still have no usable EPS; the detail endpoint explains that.

Row identity is the stored filing, ``(company, accession)``: one joint accession stored under two
registrants is two rows. ``filing_date`` and ``report_date`` are source metadata dates, never
availability, acceptance or first-public evidence. No financial value, ticker, company name,
acceptance/ingestion time, internal id or storage path is exposed.
"""

from __future__ import annotations

from datetime import date
from typing import Final

from pydantic import BaseModel, Field

from quantforge.models.enums import SecFormType
from quantforge.models.filing import ACCESSION_PATTERN

__all__ = [
    "CIK_QUERY_PATTERN",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "MAX_OFFSET",
    "FilingIndexItem",
    "FilingIndexPage",
    "FilingIndexQuery",
]

# The same bounds as the earnings list, so clients paginate one way everywhere.
DEFAULT_LIMIT: Final = 50
MAX_LIMIT: Final = 200
MAX_OFFSET: Final = 1_000_000
#: A CIK exactly as stored: ten digits, zero-padded (the ``company.cik`` CHECK).
CIK_QUERY_PATTERN: Final = r"^[0-9]{10}$"


class FilingIndexQuery(BaseModel):
    """Query parameters. Unknown parameters are rejected; both filters are exact and AND-ed."""

    model_config = {"extra": "forbid"}

    limit: int = Field(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    offset: int = Field(0, ge=0, le=MAX_OFFSET)
    form: SecFormType | None = Field(
        None, description="Exact stored form: '10-Q' does not include '10-Q/A'."
    )
    cik: str | None = Field(
        None, pattern=CIK_QUERY_PATTERN, description="Ten-digit zero-padded registrant CIK."
    )


class FilingIndexItem(BaseModel):
    accession: str = Field(
        pattern=ACCESSION_PATTERN, description="The stored dashed accession, exactly."
    )
    company_cik: str | None = Field(
        description="The registrant's ten-digit zero-padded CIK as stored (null if none is stored)."
    )
    form: SecFormType = Field(description="The stored form; never inferred from facts.")
    filing_date: date = Field(
        description="The SEC filing date (source metadata). Not availability or acceptance."
    )
    report_date: date | None = Field(
        description="The source's report date if stated; never derived from FSDS."
    )
    fsds_period_end: date | None = Field(
        description=(
            "The period end that all of this filing's FSDS submissions state; null when they "
            "disagree (no latest, earliest or first is chosen)."
        )
    )


class FilingIndexPage(BaseModel):
    items: list[FilingIndexItem]
    total: int = Field(description="Logical filings matching the filters, before pagination.")
    limit: int
    offset: int
