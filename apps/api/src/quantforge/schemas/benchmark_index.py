"""Public schemas for ``GET /api/v1/benchmarks``: which benchmarks a caller can explicitly select.

A discovery surface for the ``benchmark_id`` of ``GET /api/v1/research/observations/{event_id}``. It
lists stored benchmark IDENTITY only: it never chooses, recommends or defaults a benchmark (the
research endpoints keep requiring an explicit ``benchmark_id``), infers one from a company or
security, or carries any level, return or other financial value. Inactive benchmarks are listed
with ``is_active=false`` (a saved observation may reference one). No source id, ingestion run,
description, timestamp or price data is exposed.
"""

from __future__ import annotations

import uuid
from typing import Final

from pydantic import BaseModel, Field

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "MAX_OFFSET",
    "BenchmarkIndexItem",
    "BenchmarkIndexPage",
    "BenchmarkIndexQuery",
]

# The same bounds as the earnings, filing and record lists, so clients paginate one way everywhere.
DEFAULT_LIMIT: Final = 50
MAX_LIMIT: Final = 200
MAX_OFFSET: Final = 1_000_000


class BenchmarkIndexQuery(BaseModel):
    """Pagination only. Unknown parameters are rejected; there is no search or filter."""

    model_config = {"extra": "forbid"}

    limit: int = Field(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT)
    offset: int = Field(0, ge=0, le=MAX_OFFSET)


class BenchmarkIndexItem(BaseModel):
    id: uuid.UUID = Field(description="Pass this as benchmark_id to the research observation.")
    name: str = Field(description="Full name of the reference series. Not unique.")
    symbol: str | None = Field(
        description="Vendor symbol, descriptive only (not unique); may be null."
    )
    currency: str | None = Field(description="ISO 4217 code the series is quoted in; may be null.")
    is_active: bool = Field(
        description="False once the series is no longer used; kept so history stays understandable."
    )


class BenchmarkIndexPage(BaseModel):
    items: list[BenchmarkIndexItem]
    total: int = Field(description="Stored benchmarks (active and inactive), before pagination.")
    limit: int
    offset: int
