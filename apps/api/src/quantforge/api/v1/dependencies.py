"""Shared request dependencies and error mapping for the read-only v1 routes."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.orm import Session

from quantforge.db.session import get_session

logger = logging.getLogger(__name__)

__all__ = ["DATABASE_UNAVAILABLE", "SessionDep", "database_unavailable"]

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
