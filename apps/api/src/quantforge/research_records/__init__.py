"""Persisted research records (Step 10.9B): immutable baselines for rerun verification.

No public API, no frontend, no provider; the only write is one INSERT per saved record.
"""

from __future__ import annotations

from quantforge.research_records.service import (
    RecordIntegrityError,
    ResearchRecordNotFoundError,
    ResultDivergenceError,
    SavedRecord,
    StoredResearchRecord,
    load_research_record,
    save_research_record,
    verify_stored_record,
)

__all__ = [
    "RecordIntegrityError",
    "ResearchRecordNotFoundError",
    "ResultDivergenceError",
    "SavedRecord",
    "StoredResearchRecord",
    "load_research_record",
    "save_research_record",
    "verify_stored_record",
]
