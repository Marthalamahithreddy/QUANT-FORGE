"""Read-only research observation endpoint (Step 10.8B).

A transport surface over :func:`quantforge.observation.assemble_research_observation`; it implements
no research. Request validation -> assembler -> serialisation.

HTTP semantics: ``200`` for every valid request for an existing event, **including every domain
refusal** (point-in-time refused, filing not stored, CIK mismatch, accounting or benchmark refusal,
missing prices): those are sections of the observation, never HTTP errors. ``404`` only when the
well-formed event id is not stored. ``422`` for malformed ids/enums/horizons/accession, a repeated
or unknown query parameter, or a filing/EPS-selector dependency violation. ``503`` only when the
database is unavailable. Anything else (an invariant failure) is an unmasked ``500``.

Read-only: the request session is rolled back, never committed; no provider is called; nothing is
persisted. A filing is linked only when the caller supplies its accession.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import InterfaceError, OperationalError

from quantforge.api.v1.dependencies import (
    DATABASE_UNAVAILABLE,
    SessionDep,
    database_unavailable,
    reject_repeated_query_params,
)
from quantforge.api.v1.research_observation_presenter import present_research_observation
from quantforge.observation import EarningsEventNotFoundError, assemble_research_observation
from quantforge.research.event_returns import DEFAULT_HORIZONS
from quantforge.schemas.research_observation import ObservationQuery, ResearchObservationResponse

router = APIRouter(prefix="/research", tags=["research"])

_RESPONSES = {
    **DATABASE_UNAVAILABLE,
    status.HTTP_404_NOT_FOUND: {"description": "No such earnings event."},
}


@router.get(
    "/observations/{event_id}",
    response_model=ResearchObservationResponse,
    responses=_RESPONSES,
    dependencies=[Depends(reject_repeated_query_params)],
    summary="One research observation for one earnings event",
    description=(
        "Descriptive market and accounting evidence plus the point-in-time verdict for one stored "
        "earnings event. Domain refusals are sections of the response (HTTP 200), not errors. "
        "A filing is linked only when filing_accession is supplied (with eps_period_class and "
        "eps_kind); nothing is inferred. Financial values are fixed-point strings. Not a signal, "
        "forecast or recommendation."
    ),
)
def get_research_observation(
    event_id: uuid.UUID,
    session: SessionDep,
    params: Annotated[ObservationQuery, Query()],
) -> ResearchObservationResponse:
    try:
        observation = assemble_research_observation(
            session,
            event_id,
            horizons=params.effective_horizons() or DEFAULT_HORIZONS,
            benchmark_id=params.benchmark_id,
            security_id=params.security_id,
            filing_accession=params.filing_accession,
            eps_period_class=params.eps_period_class,
            eps_kind=params.eps_kind,
        )
    except EarningsEventNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Event not found."
        ) from exc
    except (OperationalError, InterfaceError) as exc:
        raise database_unavailable(exc, what="Research observation read") from exc
    return present_research_observation(observation)


__all__ = ["router"]
