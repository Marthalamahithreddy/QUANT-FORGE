"""Pydantic schemas for the financial domain read/create boundaries.

These are the only shapes QuantForge would ever put on the wire. ORM models are
never returned from a route directly: doing so would couple the HTTP contract
to the persistence schema, leak internals, and make a column rename a breaking
API change.

No HTTP routes are registered at this milestone. The schemas exist so the
persistence layer has an explicit, validated boundary and so a later API surface
is a thin projection over these types rather than an afterthought.
"""

from __future__ import annotations

from quantforge.schemas.benchmark import (
    BenchmarkCreate,
    BenchmarkPriceBarCreate,
    BenchmarkPriceBarRead,
    BenchmarkRead,
)
from quantforge.schemas.company import CompanyCreate, CompanyRead
from quantforge.schemas.earnings import EarningsEventCreate, EarningsEventRead
from quantforge.schemas.market import DailyPriceBarCreate, DailyPriceBarRead
from quantforge.schemas.provenance import DataSourceCreate, DataSourceRead
from quantforge.schemas.security import (
    ExchangeCreate,
    ExchangeRead,
    ListingCreate,
    ListingRead,
    SecurityCreate,
    SecurityIdentifierCreate,
    SecurityIdentifierRead,
    SecurityRead,
)

__all__ = [
    "BenchmarkCreate",
    "BenchmarkPriceBarCreate",
    "BenchmarkPriceBarRead",
    "BenchmarkRead",
    "CompanyCreate",
    "CompanyRead",
    "DailyPriceBarCreate",
    "DailyPriceBarRead",
    "DataSourceCreate",
    "DataSourceRead",
    "EarningsEventCreate",
    "EarningsEventRead",
    "ExchangeCreate",
    "ExchangeRead",
    "ListingCreate",
    "ListingRead",
    "SecurityCreate",
    "SecurityIdentifierCreate",
    "SecurityIdentifierRead",
    "SecurityRead",
]
