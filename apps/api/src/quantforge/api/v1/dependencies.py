"""Shared request dependencies and error mapping for the read-only v1 routes."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.orm import Session

from quantforge.db.session import get_session

logger = logging.getLogger(__name__)

__all__ = [
    "DATABASE_UNAVAILABLE",
    "SessionDep",
    "database_unavailable",
    "reject_repeated_query_params",
]

#: A request-scoped session; ``get_session`` rolls back and never commits, so a route cannot persist.
SessionDep = Annotated[Session, Depends(get_session)]

#: The documented 503 response, for the route's OpenAPI ``responses``.
DATABASE_UNAVAILABLE: dict[int | str, dict[str, Any]] = {
    status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "The database is unavailable."}
}


def database_unavailable(exc: OperationalError | InterfaceError, *, what: str) -> HTTPException:
    """The generic 503 for an unavailable database.

    Only the exception CLASS NAME is logged (no traceback, no message): driver messages can embed
    the DSN, host or credentials, and nothing of them reaches the response either.
    """
    logger.warning("%s failed: %s", what, type(exc).__name__)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable."
    )


def reject_repeated_query_params(request: Request) -> None:
    """422 when any query parameter is repeated, in FastAPI's own validation-error shape.

    Every parameter of the filing endpoints is a single value, but the framework silently keeps the
    LAST of a repeated scalar (``cik=A&cik=B`` would resolve registrant B). For an identity
    qualifier that is an arbitrary choice, so a repeat is rejected instead of resolved. The body is
    the same list-of-errors structure as every other 422 (``loc``, ``msg``, ``type``).
    """
    seen: set[str] = set()
    repeated: list[str] = []
    for key, _value in request.query_params.multi_items():
        if key in seen and key not in repeated:
            repeated.append(key)
        seen.add(key)
    if repeated:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[
                {
                    "type": "repeated_parameter",
                    "loc": ["query", key],
                    "msg": f"Query parameter {key!r} must not be repeated.",
                }
                for key in repeated
            ],
        )
