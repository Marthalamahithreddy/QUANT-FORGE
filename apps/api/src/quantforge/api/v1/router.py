"""Aggregate router for API version 1.

The liveness probe is mounted at both ``/health`` and ``{prefix}/health``; the
readiness probe is versioned only, because it is an API contract that may gain
fields and orchestrators pin it explicitly.
"""

from __future__ import annotations

from fastapi import APIRouter

from quantforge.api.v1.benchmarks import router as benchmarks_router
from quantforge.api.v1.earnings_events import router as earnings_events_router
from quantforge.api.v1.filing_eps import router as filing_eps_router
from quantforge.api.v1.filings import router as filings_router
from quantforge.api.v1.health import router as health_router
from quantforge.api.v1.ready import router as ready_router
from quantforge.api.v1.research_observations import router as research_observations_router
from quantforge.api.v1.research_records import router as research_records_router

router = APIRouter()
router.include_router(health_router)
router.include_router(ready_router)
router.include_router(earnings_events_router)
router.include_router(benchmarks_router)
router.include_router(filing_eps_router)
router.include_router(filings_router)
router.include_router(research_observations_router)
router.include_router(research_records_router)
