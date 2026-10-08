"""Shared vocabulary for backing-service connectivity.

The health endpoints, the database layer and the cache layer all describe a
dependency the same way, so the wire contract has exactly one definition and the
infrastructure modules never need to import from ``quantforge.api``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "DatabaseProbe",
    "DependencyCheck",
    "DependencyName",
    "DependencyState",
    "SchemaCheck",
    "SchemaState",
]


class DependencyState(StrEnum):
    """Outcome of a single connectivity probe."""

    OK = "ok"
    UNAVAILABLE = "unavailable"
    #: The service is deliberately not configured (e.g. a blank ``QF_REDIS_URL``): not an outage.
    NOT_CONFIGURED = "not_configured"


class DependencyName(StrEnum):
    """Canonical dependency identifiers used in API payloads."""

    DATABASE = "database"
    REDIS = "redis"


@dataclass(frozen=True, slots=True)
class DependencyCheck:
    """Result of probing one dependency.

    ``detail`` is deliberately restricted to an exception *class name*. Database
    and client library exceptions routinely embed the DSN, the password or a
    server-side message, so surfacing their text through an HTTP response would
    leak credentials. The full detail is logged instead, by the caller.
    """

    name: DependencyName
    state: DependencyState
    detail: str | None = None

    @property
    def is_ok(self) -> bool:
        """Whether the dependency answered successfully."""
        return self.state is DependencyState.OK

    @classmethod
    def ok(cls, name: DependencyName) -> DependencyCheck:
        """Build a successful result."""
        return cls(name=name, state=DependencyState.OK)

    @classmethod
    def failure(cls, name: DependencyName, error: BaseException) -> DependencyCheck:
        """Build a failed result that exposes only the exception type name."""
        return cls(
            name=name,
            state=DependencyState.UNAVAILABLE,
            detail=type(error).__name__,
        )


class SchemaState(StrEnum):
    """Whether the database schema is the one this binary was built for."""

    #: The database's Alembic revision equals this binary's single migration head.
    COMPATIBLE = "compatible"
    #: A revision was read and it is not this binary's head (behind, ahead or foreign).
    MISMATCH = "mismatch"
    #: Compatibility cannot be proven: no version table, no single revision, no single head.
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SchemaCheck:
    """Outcome of comparing the database's Alembic revision with this binary's migration head.

    Only revision identifiers ever appear here (never a DSN, a message or a path), and ``current`` is
    only populated when it looks like a revision identifier at all.
    """

    state: SchemaState
    expected: str | None = None
    current: str | None = None

    @property
    def is_compatible(self) -> bool:
        return self.state is SchemaState.COMPATIBLE


@dataclass(frozen=True, slots=True)
class DatabaseProbe:
    """One database round trip: connectivity and the schema revision read on the same connection."""

    database: DependencyCheck
    schema: SchemaCheck
