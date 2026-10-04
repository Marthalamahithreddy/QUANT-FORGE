"""Read-only index of stored filings that have FSDS financial-fact submission data.

A navigation surface for ``GET /api/v1/filings/{accession}/eps``: it lists stored records only
(no SEC or provider call, no write; the request session is rolled back, never committed) and calls
no EPS selector, comparative, change or availability logic. It is not "all SEC filings", not a
historical-availability view and not a list of tradable filings.

Eligibility: a stored ``SecFiling`` with at least one ``FinancialFactSubmission``. EPS need not be
available; the detail endpoint reports that honestly.

Row identity is the stored filing ``(company, accession)`` (``uq_sec_filing_company_accession``): a
joint accession stored under two registrants is two rows, and a filing with several FSDS
submissions is still one row (the submissions are aggregated per filing before joining).

Order: ``filing_date`` DESC, accession DESC, CIK DESC (nulls last), then the row id: a total order,
so offset pages never repeat or skip a row on unchanged data. It never uses an acceptance or
ingestion time. A joint accession listed twice has an ambiguous detail
(``/filings/{accession}/eps`` is keyed by accession alone and reports ``AMBIGUOUS`` when facts sit
under both registrants); the index does not hide that. ``total`` and the page are read in one request transaction; a concurrent ingest
between the two reads can make them disagree by the rows it added.

HTTP: ``200`` always for a valid request (an empty index or an offset past the end is ``200`` with
``items = []``), ``422`` for invalid parameters, ``503`` only when the database is unavailable.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.exc import InterfaceError, OperationalError

from quantforge.api.v1.dependencies import (
    DATABASE_UNAVAILABLE,
    SessionDep,
    database_unavailable,
    reject_repeated_query_params,
)
from quantforge.repositories.financial_fact import FinancialFactRepository
from quantforge.schemas.filing_index import FilingIndexItem, FilingIndexPage, FilingIndexQuery

router = APIRouter(prefix="/filings", tags=["filings"])


@router.get(
    "",
    response_model=FilingIndexPage,
    responses=DATABASE_UNAVAILABLE,
    dependencies=[Depends(reject_repeated_query_params)],
    summary="Index of stored filings with FSDS financial-fact data",
    description=(
        "Stored filings that have at least one FSDS submission, newest filing date first. Not all "
        "SEC filings, not historically available filings and not tradable filings. filing_date and "
        "report_date are source metadata; fsds_period_end is null when a filing's submissions "
        "disagree. form and cik are exact filters combined with AND."
    ),
)
def list_filings(
    session: SessionDep, params: Annotated[FilingIndexQuery, Query()]
) -> FilingIndexPage:
    try:
        result = FinancialFactRepository(session).filing_index(
            limit=params.limit, offset=params.offset, form=params.form, cik=params.cik
        )
    except (OperationalError, InterfaceError) as exc:
        raise database_unavailable(exc, what="Filings index read") from exc
    return FilingIndexPage(
        items=[
            FilingIndexItem(
                accession=row.accession_number,
                company_cik=row.company_cik,
                form=row.form,
                filing_date=row.filing_date,
                report_date=row.report_date,
                fsds_period_end=row.fsds_period_end,
            )
            for row in result.rows
        ],
        total=result.total,
        limit=params.limit,
        offset=params.offset,
    )


__all__ = ["router"]
