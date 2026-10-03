"""Read-only filing EPS endpoint: current EPS, comparative EPS and EPS change for one accession.

A transport surface over the existing financial-domain layers (:mod:`quantforge.financials`); it
selects, computes and classifies nothing itself. It reads already-ingested facts only: no SEC or
provider call, no write (the request session is rolled back, never committed), no cache.

HTTP semantics: ``200`` for every valid request, **including domain refusals** (``NOT_INGESTED``,
``MISSING``, ``WRONG_UNIT``, ``COMPARATIVE_PERIOD_MISMATCH``, ...: they are observations, not missing
resources); ``422`` for a malformed accession or a missing/invalid ``period_class`` / ``eps_kind``
(both are required, nothing is defaulted or inferred from the form); ``503`` only when the database
is unavailable. ``GET /filings`` (see :mod:`quantforge.api.v1.filings`) lists the accessions.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query
from sqlalchemy.exc import InterfaceError, OperationalError

from quantforge.api.v1.dependencies import DATABASE_UNAVAILABLE, SessionDep, database_unavailable
from quantforge.api.v1.filing_eps_presenter import present_filing_eps
from quantforge.financials.comparative_eps_extraction import extract_comparative_eps_observation
from quantforge.financials.eps_change import compute_eps_change
from quantforge.models.filing import ACCESSION_PATTERN
from quantforge.schemas.filing_eps import FilingEpsQuery, FilingEpsResponse

router = APIRouter(prefix="/filings", tags=["filings"])


@router.get(
    "/{accession}/eps",
    response_model=FilingEpsResponse,
    responses=DATABASE_UNAVAILABLE,
    summary="Current EPS, comparative EPS and EPS change for one filing",
    description=(
        "Descriptive accounting data from SEC FSDS for one accession; no point-in-time availability "
        "claim. period_class and eps_kind are required. Every status, including refusals, is returned "
        "with HTTP 200. Financial values are fixed-point strings; percentage_change is a ratio "
        "(0.5 = +50%) defined only for a positive comparative. The comparative is 'comparative EPS "
        "as presented in this filing', not the originally reported prior-year EPS."
    ),
)
def get_filing_eps(
    session: SessionDep,
    accession: Annotated[
        str,
        Path(
            pattern=ACCESSION_PATTERN,
            description="Dashed EDGAR accession, e.g. 0000320193-24-000123.",
        ),
    ],
    params: Annotated[FilingEpsQuery, Query()],
) -> FilingEpsResponse:
    try:
        comparative = extract_comparative_eps_observation(
            session, accession, period_class=params.period_class, eps_kind=params.eps_kind
        )
    except (OperationalError, InterfaceError) as exc:
        raise database_unavailable(exc, what="Filing EPS read") from exc
    return present_filing_eps(comparative, compute_eps_change(comparative))


__all__ = ["router"]
