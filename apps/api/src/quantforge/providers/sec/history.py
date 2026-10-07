"""SEC submissions history files: discovery, naming and bounded selection (identity only).

Source contract (what is official, what is observed)
----------------------------------------------------
Officially, ``filings.recent`` holds "at least one year's of filing or to 1,000
(whichever is more) of the most recent filings", and if the entity has more,
``filings.files`` "will contain an array of additional JSON files and the date range
for the filings each one contains." That is all the SEC documents. Everything below
is **observed**, not documented, and is re-verified at run time rather than trusted:

* each ``filings.files`` entry looks like ``{"name": "CIK0000320193-submissions-001.json",
  "filingCount": 1259, "filingFrom": "1994-01-26", "filingTo": "2015-09-08"}``;
* the file is served from the same host as the parent, at ``/submissions/<name>``;
* it is a *flat* object of the same parallel arrays as ``filings.recent``;
* no ``ETag`` / ``Last-Modified`` is sent, and nothing documents whether a history
  file is ever rewritten -- so its bytes are treated as **mutable** and are kept by
  SHA-256 (a changed body under the same URL is a new artifact, never an overwrite).

A file URL is never constructed from memory: the *name* comes from the parent
response, must match the one expected pattern for **this** CIK, and is the only thing
appended to the fixed ``/submissions/`` path on the configured SEC host. A name for
another CIK, a path separator, a query string or an absolute URL is refused, so a
parent response cannot steer the client to another registrant or another host.

Nothing here says when a filing became public: history membership, the file's date
range and ``retrieved_at`` are identity/provenance facts, not availability evidence.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Final

from quantforge.ingestion.canonical import CanonicalSecFiling
from quantforge.providers.errors import ProviderError, ProviderPayloadError

__all__ = [
    "HISTORY_PATH_PREFIX",
    "MAX_HISTORY_FILES_CEILING",
    "HistoryFileRef",
    "HistoryScopeError",
    "accession_year",
    "count_mismatch",
    "merge_filings",
    "parse_history_file_refs",
    "raw_accessions",
    "select_history_files",
]

#: The only path under which history files are requested.
HISTORY_PATH_PREFIX: Final[str] = "/submissions/"
_HISTORY_FILE_NAME = re.compile(r"CIK(?P<cik>[0-9]{10})-submissions-(?P<sequence>[0-9]{3})\.json")
#: EDGAR began in 1993; a two-digit accession year >= this maps to 19yy, below it to 20yy.
_EDGAR_FIRST_TWO_DIGIT_YEAR: Final = 93
_ACCESSION_YEAR_TOLERANCE: Final = 1
#: The most history-file requests any one run may be allowed, whoever calls it.
MAX_HISTORY_FILES_CEILING: Final = 100


@dataclass(frozen=True, slots=True)
class HistoryFileRef:
    """One history file the parent response names. Nothing is fetched by this type."""

    name: str
    filing_count: int | None
    filing_from: date | None
    filing_to: date | None

    @property
    def path(self) -> str:
        """The request path (never a URL: the host is the client's configured SEC host)."""
        return f"{HISTORY_PATH_PREFIX}{self.name}"


def accession_year(accession_number: str) -> int:
    """The year encoded in a dashed accession (``0000320193-24-000010`` -> 2024)."""
    two_digits = int(accession_number[11:13])
    return (1900 if two_digits >= _EDGAR_FIRST_TWO_DIGIT_YEAR else 2000) + two_digits


def _optional_date(value: object, *, field: str, name: str) -> date | None:
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        message = f"history file entry {name!r} has an invalid {field}: {value!r}"
        raise ProviderPayloadError(message) from exc


def _optional_count(value: object, *, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        message = f"history file entry {name!r} has an invalid filingCount: {value!r}"
        raise ProviderPayloadError(message)
    return value


def parse_history_file_refs(document: Any, *, cik: str) -> tuple[HistoryFileRef, ...]:
    """The history files a parent submissions response names for this CIK.

    Absent or empty ``filings.files`` means the registrant has no history files (every
    filing is in ``recent``). A malformed entry, a duplicate, or a name that is not
    exactly ``CIK<this cik>-submissions-NNN.json`` raises :class:`ProviderPayloadError`.
    """
    filings_block = document.get("filings") if isinstance(document, dict) else None
    entries = filings_block.get("files") if isinstance(filings_block, dict) else None
    if entries is None:
        return ()
    if not isinstance(entries, list):
        message = "submissions 'filings.files' must be an array"
        raise ProviderPayloadError(message)
    refs: list[HistoryFileRef] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            message = "a 'filings.files' entry must be an object with a text 'name'"
            raise ProviderPayloadError(message)
        name: str = entry["name"]
        match = _HISTORY_FILE_NAME.fullmatch(name)
        if match is None:
            message = (
                f"history file name {name!r} is not of the form CIK##########-submissions-###.json; "
                "refusing to build a request from it"
            )
            raise ProviderPayloadError(message)
        if match["cik"] != cik:
            message = (
                f"history file {name!r} belongs to CIK {match['cik']}, not the requested {cik}; "
                "refusing to follow another registrant's file"
            )
            raise ProviderPayloadError(message)
        if name in seen:
            message = f"history file {name!r} is listed more than once"
            raise ProviderPayloadError(message)
        seen.add(name)
        refs.append(
            HistoryFileRef(
                name=name,
                filing_count=_optional_count(entry.get("filingCount"), name=name),
                filing_from=_optional_date(entry.get("filingFrom"), field="filingFrom", name=name),
                filing_to=_optional_date(entry.get("filingTo"), field="filingTo", name=name),
            )
        )
    return tuple(refs)


def _could_contain(ref: HistoryFileRef, accession_number: str) -> bool:
    """Whether the file's stated date range could hold this accession.

    The accession year is the submission year, which can differ by one from the filing
    date's year around a year boundary, hence the tolerance. A file with no stated range
    can never be excluded.
    """
    if ref.filing_from is None or ref.filing_to is None:
        return True
    year = accession_year(accession_number)
    return (
        ref.filing_from.year - _ACCESSION_YEAR_TOLERANCE
        <= year
        <= ref.filing_to.year + _ACCESSION_YEAR_TOLERANCE
    )


def select_history_files(
    refs: Sequence[HistoryFileRef], *, unresolved: Iterable[str] | None
) -> tuple[tuple[HistoryFileRef, ...], tuple[HistoryFileRef, ...]]:
    """Split ``refs`` into ``(to_fetch, skipped_by_scope)``.

    ``unresolved is None`` is the untargeted scope: every referenced file is fetched.
    With target accessions, only files whose stated range could hold one of them are
    fetched. A target missing from ``recent`` is **not** presumed absent: it simply
    selects the files that might contain it.
    """
    if unresolved is None:
        return tuple(refs), ()
    targets = tuple(unresolved)
    selected = tuple(ref for ref in refs if any(_could_contain(ref, acc) for acc in targets))
    skipped = tuple(ref for ref in refs if ref not in selected)
    return selected, skipped


def raw_accessions(columns: object) -> frozenset[str]:
    """Every accession named in a columnar block, supported form or not.

    Used only to tell "located but not storable" (an unsupported form, or a record the
    mapper rejected) from "not found in what was fetched" for a requested accession.
    """
    values = columns.get("accessionNumber") if isinstance(columns, dict) else None
    if not isinstance(values, list):
        return frozenset()
    return frozenset(str(value) for value in values)


def count_mismatch(ref: HistoryFileRef, rows: int) -> str | None:
    """A reason when the file's row count disagrees with its stated ``filingCount``."""
    if ref.filing_count is not None and ref.filing_count != rows:
        return (
            f"history file {ref.name!r} has {rows} rows but the parent response states "
            f"filingCount={ref.filing_count}; refusing to treat it as complete"
        )
    return None


class HistoryScopeError(ProviderError):
    """The requested history scope exceeds the explicit bound; nothing further is fetched."""


def merge_filings(
    sources: Sequence[tuple[str, Sequence[CanonicalSecFiling]]],
) -> tuple[list[CanonicalSecFiling], int]:
    """Merge filings from several representations: ``(merged, identical_overlap_count)``.

    The same ``(cik, accession)`` seen twice with identical canonical metadata is one
    filing (counted as overlap). Differing metadata raises :class:`ProviderPayloadError`
    naming both sources and the fields -- no source (recent, history, newer, older) is
    silently preferred, and an amendment is a different accession so never merges.
    """
    merged: dict[str, tuple[str, CanonicalSecFiling]] = {}
    overlap = 0
    for label, filings in sources:
        for filing in filings:
            held = merged.get(filing.accession_number)
            if held is None:
                merged[filing.accession_number] = (label, filing)
                continue
            held_label, held_filing = held
            if held_filing != filing:
                fields = sorted(
                    name
                    for name in (
                        "cik",
                        "form",
                        "filing_date",
                        "report_date",
                        "primary_document",
                        "items",
                    )
                    if getattr(held_filing, name) != getattr(filing, name)
                )
                message = (
                    f"accession {filing.accession_number} appears in {held_label!r} and "
                    f"{label!r} with different metadata ({', '.join(fields)}); neither is "
                    "silently preferred"
                )
                raise ProviderPayloadError(message)
            overlap += 1
    ordered = sorted(
        (filing for _, filing in merged.values()),
        key=lambda f: (f.filing_date, f.accession_number),
    )
    return ordered, overlap
