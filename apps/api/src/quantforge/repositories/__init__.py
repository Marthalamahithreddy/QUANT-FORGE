"""Explicit persistence repositories for the financial domain.

There is intentionally **no** ``BaseRepository``. A generic CRUD wrapper would
reduce these classes to ``save``/``find_by(**kwargs)``, which hides the two
things that actually matter in this domain:

* the half-open ``[valid_from, valid_to)`` interval logic that makes
  point-in-time ticker resolution correct, and
* the inclusive session-range convention that keeps the two fact tables from
  drifting into different off-by-one behaviours.

Each repository exposes only the queries the domain needs, bound to a
caller-supplied :class:`~sqlalchemy.orm.Session` so it composes with the
caller's transaction and never commits on its own.
"""

from __future__ import annotations

from quantforge.repositories.benchmark import BenchmarkRepository
from quantforge.repositories.company import CompanyRepository
from quantforge.repositories.earnings import EarningsRepository
from quantforge.repositories.market import MarketDataRepository
from quantforge.repositories.security import (
    ExchangeRepository,
    ListingRepository,
    SecurityIdentifierRepository,
    SecurityRepository,
)

__all__ = [
    "BenchmarkRepository",
    "CompanyRepository",
    "EarningsRepository",
    "ExchangeRepository",
    "ListingRepository",
    "MarketDataRepository",
    "SecurityIdentifierRepository",
    "SecurityRepository",
]
