"""Read-only benchmark discovery endpoint (Step 11.0B).

``GET /api/v1/benchmarks`` answers one question: which benchmarks can a caller explicitly select?
Their ids feed ``benchmark_id`` of the research observation endpoint. It is a selector surface, not
research: it chooses, recommends and defaults nothing, infers nothing from a company or security,
reads no price level and computes no return. There is no detail, search, create, update or delete.

Active and inactive benchmarks are both listed (``is_active`` says which). Order: ``name`` then
``id`` (names are not unique), so offset pages never repeat or skip a row on unchanged data. ``total``
and the page are two reads in the request transaction; a concurrent insert between them can make
them disagree by that row.

HTTP: ``200`` always for a valid request (no benchmarks, or an offset past the end, is ``200`` with
``items = []``), ``422`` for an unknown, repeated or out-of-range parameter, ``503`` only when the
database is unavailable. Read-only: the request session is rolled back, never committed; no provider.
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
from quantforge.repositories.benchmark import BenchmarkRepository
from quantforge.schemas.benchmark_index import (
    BenchmarkIndexItem,
    BenchmarkIndexPage,
    BenchmarkIndexQuery,
)

router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])


@router.get(
    "",
    response_model=BenchmarkIndexPage,
    responses=DATABASE_UNAVAILABLE,
    dependencies=[Depends(reject_repeated_query_params)],
    summary="Benchmarks a caller can explicitly select",
    description=(
        "Stored benchmarks by name, then id, active and inactive alike (see is_active). Each id can "
        "be passed as benchmark_id to the research observation. No benchmark is chosen, "
        "recommended or defaulted, and no price level or return is exposed."
    ),
)
def list_benchmarks(
    session: SessionDep, params: Annotated[BenchmarkIndexQuery, Query()]
) -> BenchmarkIndexPage:
    repository = BenchmarkRepository(session)
    try:
        rows = repository.list_identities(limit=params.limit, offset=params.offset)
        total = repository.count()
    except (OperationalError, InterfaceError) as exc:
        raise database_unavailable(exc, what="Benchmark index read") from exc
    return BenchmarkIndexPage(
        items=[
            BenchmarkIndexItem(
                id=row.id,
                name=row.name,
                symbol=row.symbol,
                currency=row.currency,
                is_active=row.is_active,
            )
            for row in rows
        ],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


__all__ = ["router"]
