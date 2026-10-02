"""Shared vocabulary for backing-service connectivity.

The health endpoints, the database layer and the cache layer all describe a
dependency the same way, so the wire contract has exactly one definition and the
infrastructure modules never need to import from ``quantforge.api``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = ["DependencyCheck", "DependencyName", "DependencyState"]


class DependencyState(StrEnum):
    """Outcome of a single connectivity probe."""

    OK = "ok"
    UNAVAILABLE = "unavailable"


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
