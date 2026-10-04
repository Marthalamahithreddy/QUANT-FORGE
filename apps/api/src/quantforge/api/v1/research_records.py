"""Read-only persisted research record endpoints (Step 11.0A).

A transport surface over the Step 10.9B record services; it implements no research and exposes no
way to create, change or delete a record (saving stays an internal service).

* ``GET /research/records``: a bounded page of stored baselines, newest first, without the canonical
  payloads. Rows are summaries of stored columns and are not re-hashed (see the repository).
* ``GET /research/records/{record_id}``: one baseline, parsed, after the load service has re-hashed
  the stored canonical text and re-derived its level and gaps.
* ``GET /research/records/{record_id}/verify``: re-runs the stored computation against the CURRENT
  stored evidence and reports how it compares. A GET on purpose: like the observation endpoint it
  computes but is safe and idempotent (SELECT only, nothing is persisted, no verification history),
  and the API exposes no write verb for research.

HTTP semantics: ``200``; ``404`` only for a well-formed record id that is not stored (a malformed id
is ``422``); ``409`` for verify when the record's earnings event is no longer stored (the record
exists, so not a 404, and nothing can be compared); ``422`` for an unknown or repeated query
parameter or an out-of-range limit/offset; ``503`` only when the database is unavailable (generic
body, never driver text). A stored record that fails integrity validation (a hash, canonical form or
level/gap mismatch) is a server invariant failure: a generic ``500`` whose body never carries any
canonical content. Read-only: the request session is rolled back, never committed; no provider call.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy.exc import DataError, InterfaceError, OperationalError

from quantforge.api.v1.dependencies import (
    DATABASE_UNAVAILABLE,
    SessionDep,
    database_unavailable,
    reject_repeated_query_params,
)
from quantforge.api.v1.research_record_presenter import (
    present_detail,
    present_summary_row,
    present_verification,
)
from quantforge.observation import EarningsEventNotFoundError
from quantforge.repositories.research_record import ResearchRecordRepository
from quantforge.research_records import (
    RecordIntegrityError,
    ResearchRecordNotFoundError,
    load_research_record,
    verify_stored_record,
)
from quantforge.schemas.research_record import (
    NoQuery,
    RecordVerificationResponse,
    ResearchRecordDetail,
    ResearchRecordListQuery,
    ResearchRecordPage,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/research/records", tags=["research"])

_INTEGRITY_DETAIL = "Stored research record failed integrity validation."
_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"description": "No such research record."}
}
_INTEGRITY: dict[int | str, dict[str, Any]] = {
    status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": _INTEGRITY_DETAIL}
}


def _integrity_failure(record: uuid.UUID | None, reason: object) -> HTTPException:
    """A generic 500. The reason (never canonical content) is logged, not returned."""
    logger.error("research record %s failed integrity validation: %s", record or "list", reason)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=_INTEGRITY_DETAIL
    )


@contextmanager
def _record_errors(record_id: uuid.UUID, *, what: str) -> Iterator[None]:
    """The one mapping of record-service failures to HTTP: 404, generic 500, generic 503."""
    try:
        yield
    except ResearchRecordNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Research record not found."
        ) from exc
    except RecordIntegrityError as exc:
        raise _integrity_failure(record_id, exc) from exc
    except (OperationalError, InterfaceError) as exc:
        raise database_unavailable(exc, what=what) from exc


@router.get(
    "",
    response_model=ResearchRecordPage,
    responses={**DATABASE_UNAVAILABLE, **_INTEGRITY},
    dependencies=[Depends(reject_repeated_query_params)],
    summary="Saved research records",
    description=(
        "Stored baselines of completed research computations, newest saved first. Each item "
        "identifies the record, its fingerprints, its derived reproducibility level and gaps, and "
        "what was asked; the stored manifest and result are served by the detail endpoint. "
        "created_at is when the baseline was saved, not a data availability time. Items are "
        "summaries of stored columns and are not re-hashed here; get and verify validate integrity. "
        "total and the page are separate reads: a record saved between them can shift offset pages."
    ),
)
def list_research_records(
    session: SessionDep, params: Annotated[ResearchRecordListQuery, Query()]
) -> ResearchRecordPage:
    try:
        page = ResearchRecordRepository(session).list_page(limit=params.limit, offset=params.offset)
    except (OperationalError, InterfaceError) as exc:
        raise database_unavailable(exc, what="Research record list read") from exc
    except DataError as exc:  # a stored manifest the database cannot parse: never a client error
        raise _integrity_failure(None, type(exc).__name__) from exc
    try:
        items = [present_summary_row(row) for row in page.rows]
    except ValidationError as exc:  # a stored column or request object of the wrong shape
        raise _integrity_failure(None, f"{exc.error_count()} invalid summary field(s)") from exc
    return ResearchRecordPage(
        items=items, total=page.total, limit=params.limit, offset=params.offset
    )


@router.get(
    "/{record_id}",
    response_model=ResearchRecordDetail,
    responses={**DATABASE_UNAVAILABLE, **_NOT_FOUND, **_INTEGRITY},
    dependencies=[Depends(reject_repeated_query_params)],
    summary="One saved research record",
    description=(
        "The immutable stored baseline: fingerprints, derived level and gaps, and the parsed "
        "manifest and result (financial values are fixed-point strings). The fingerprints are "
        "re-verified against the stored canonical text before the response is returned."
    ),
)
def get_research_record(
    record_id: uuid.UUID, session: SessionDep, _params: Annotated[NoQuery, Query()]
) -> ResearchRecordDetail:
    with _record_errors(record_id, what="Research record read"):
        stored = load_research_record(session, record_id)
    return present_detail(stored)


@router.get(
    "/{record_id}/verify",
    response_model=RecordVerificationResponse,
    responses={
        **DATABASE_UNAVAILABLE,
        **_NOT_FOUND,
        **_INTEGRITY,
        status.HTTP_409_CONFLICT: {"description": "The record's earnings event is not stored."},
    },
    dependencies=[Depends(reject_repeated_query_params)],
    summary="Re-verify one saved research record against current stored evidence",
    description=(
        "Re-runs the stored computation (the baseline's own request) and compares it with the "
        "baseline. MATCH means current stored evidence reproduces the baseline; it does not prove "
        "that unpinned external source bytes still exist. Read-only: nothing is saved and the "
        "baseline is never changed."
    ),
)
def verify_research_record(
    record_id: uuid.UUID, session: SessionDep, _params: Annotated[NoQuery, Query()]
) -> RecordVerificationResponse:
    with _record_errors(record_id, what="Research record verification"):
        try:
            report = verify_stored_record(session, record_id)
        except EarningsEventNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The earnings event of this record is no longer stored.",
            ) from exc
    return present_verification(record_id, report)


__all__ = ["router"]
