"""Read-only company directory and per-company financials built from stored SEC FSDS facts.

``GET /companies`` is a search over stored companies (never a provider). ``GET
/companies/{cik}/financials`` reports, per stored 10-Q/10-K-family filing, revenue, net income and
diluted EPS beside the prior-year comparative as presented in the same filing. Selection and
arithmetic live in :mod:`quantforge.financials`; this module only reads and maps. No write (the
request session is rolled back), no external call.

HTTP: ``200`` for every valid request, including a company with no usable facts (each figure then
carries its own refusal status); ``404`` only when no company is stored under the CIK; ``422`` for
invalid parameters; ``503`` only when the database is unavailable.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.exc import InterfaceError, OperationalError

from quantforge.api.v1.dependencies import (
    DATABASE_UNAVAILABLE,
    SessionDep,
    database_unavailable,
    reject_repeated_query_params,
)
from quantforge.financials.company_financials import (
    FilingFinancials,
    MetricComparison,
    build_company_financials,
)
from quantforge.financials.eps import EpsInputFact
from quantforge.financials.period_metrics import MetricPoint
from quantforge.models.company import CIK_PATTERN
from quantforge.repositories.company import CompanyRepository
from quantforge.schemas.company_financials import (
    ChangeSection,
    CompanyDirectoryItem,
    CompanyDirectoryPage,
    CompanyDirectoryQuery,
    CompanyFinancialsResponse,
    CompanySummary,
    FactSource,
    FilingFinancialsSection,
    MetricSection,
    PointSection,
)

router = APIRouter(prefix="/companies", tags=["companies"])


def _edgar_index_url(cik: str, accession: str) -> str:
    """The EDGAR filing-index page for the filing: derived text, not a request."""
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"
        f"{accession}-index.htm"
    )


def _fact(fact: EpsInputFact) -> FactSource:
    return FactSource(
        fact_id=fact.fact_id,
        tag=fact.tag,
        taxonomy_version=fact.taxonomy_version,
        unit=fact.unit,
        period_end=fact.period_end,
        qtrs=fact.duration_quarters,
        value=fact.value,
    )


def _point(point: MetricPoint) -> PointSection:
    return PointSection(
        status=point.status,
        reason=point.reason,
        fact=None if point.fact is None else _fact(point.fact),
    )


def _metric(comparison: MetricComparison) -> MetricSection:
    change = comparison.change
    return MetricSection(
        metric=comparison.metric,
        current=_point(comparison.current),
        prior=_point(comparison.prior),
        change=ChangeSection(
            status=change.status,
            absolute=change.absolute,
            transition=change.transition,
            ratio=change.ratio,
            ratio_unavailable_reason=change.ratio_unavailable_reason,
        ),
    )


def _filing(cik: str, item: FilingFinancials) -> FilingFinancialsSection:
    filing = item.filing
    ends = filing.submission_period_ends
    artifact = filing.artifacts[0]
    return FilingFinancialsSection(
        accession=filing.accession_number,
        form=filing.form,
        period_class=item.period_class,
        period_end=ends[0] if len(ends) == 1 else None,
        fiscal_year=filing.fiscal_year,
        fiscal_period=filing.fiscal_period,
        filing_date=filing.filing_date,
        report_date=filing.report_date,
        package_stored_at=filing.package_stored_at,
        source_artifact=artifact[0],
        source_artifact_sha256=artifact[1],
        source_url=_edgar_index_url(cik, filing.accession_number),
        metrics=[_metric(c) for c in item.comparisons],
    )


@router.get(
    "",
    response_model=CompanyDirectoryPage,
    responses=DATABASE_UNAVAILABLE,
    dependencies=[Depends(reject_repeated_query_params)],
    summary="Search stored companies",
    description=(
        "Stored companies that have a SEC CIK, by name. q matches a name substring, an exact "
        "current ticker or a CIK. Counts are of stored filings only, not of filings at the SEC."
    ),
)
def list_companies(
    session: SessionDep, params: Annotated[CompanyDirectoryQuery, Query()]
) -> CompanyDirectoryPage:
    try:
        page = CompanyRepository(session).directory(
            query=params.q, limit=params.limit, offset=params.offset
        )
    except (OperationalError, InterfaceError) as exc:
        raise database_unavailable(exc, what="Company directory read") from exc
    return CompanyDirectoryPage(
        items=[
            CompanyDirectoryItem(
                cik=row.cik,
                name=row.name,
                tickers=list(row.tickers),
                stored_filings=row.stored_filings,
                filings_with_financial_facts=row.filings_with_facts,
            )
            for row in page.rows
        ],
        total=page.total,
        limit=params.limit,
        offset=params.offset,
    )


@router.get(
    "/{cik}/financials",
    response_model=CompanyFinancialsResponse,
    responses={
        **DATABASE_UNAVAILABLE,
        status.HTTP_404_NOT_FOUND: {"description": "No such company."},
    },
    dependencies=[Depends(reject_repeated_query_params)],
    summary="Revenue, net income and diluted EPS per stored filing, with the prior-year comparative",
    description=(
        "For each stored 10-Q/10-K-family filing with FSDS facts, newest period first: the current "
        "and prior-year (as presented in that filing) figure, the exact change and its ratio, with "
        "the source filing and package. Every refusal is returned as a status with HTTP 200."
    ),
)
def get_company_financials(
    session: SessionDep,
    cik: Annotated[str, Path(pattern=CIK_PATTERN, description="Ten-digit zero-padded CIK.")],
) -> CompanyFinancialsResponse:
    try:
        repository = CompanyRepository(session)
        company = repository.get_by_cik(cik)
        if company is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail="No company is stored under this CIK."
            )
        tickers = repository.current_tickers(company.id)
        history = build_company_financials(session, company)
    except (OperationalError, InterfaceError) as exc:
        raise database_unavailable(exc, what="Company financials read") from exc
    return CompanyFinancialsResponse(
        company=CompanySummary(cik=cik, name=company.display_name, tickers=list(tickers)),
        total_filings=history.total_filings,
        filings=[_filing(cik, item) for item in history.filings],
    )


__all__ = ["router"]
