"""Map the SEC submissions JSON to canonical filing IDENTITY (never an acceptance instant).

Source: ``https://data.sec.gov/submissions/CIK##########.json``. Its ``filings.recent``
object is columnar (parallel arrays: ``accessionNumber``, ``filingDate``,
``reportDate``, ``acceptanceDateTime``, ``form``, ``items``, ``primaryDocument``), and a
history file is the same arrays without the wrapper. Only the supported forms are mapped; any
other form is skipped, not failed.

What this module maps, and what it deliberately does not
--------------------------------------------------------
**Identity:** accession, form, filing date, report date, primary document and items. A record
is refused (never the whole response) when one of those is malformed.

**``acceptanceDateTime`` is never converted to an instant.** The SEC does not document the
field: not its timezone, not the trailing ``Z``, not its precision. Step 11.2G compared the
JSON with the official EDGAR header ``<ACCEPTANCE-DATETIME>`` (Eastern wall clock) for nine
accessions of four registrants: the literal-``Z`` instant equalled the header instant for some,
and differed from it by exactly one Eastern UTC offset for others, depending on the registrant's
document, and the same accession changed between two retrievals three days apart. No row-local
rule recovers the true instant, and a value that happens to look right is not evidence that it
is. So ``CanonicalSecFiling.accepted_at`` is always ``None`` from this source; the instant comes
only from the filing header, on demand, per accession (``providers.sec.header``). The exact JSON
text stays auditable in the retained raw artifact. The field is therefore *not required*: if the
array is present it must have the length of the others, and each value must be text or null (any
other type is a malformed record); its content is neither parsed nor judged, so a placeholder, an
odd hour or a missing ``Z`` cannot block storing a valid identity.

History of the old rule: step 10.7A read a ``Z``-suffixed value as UTC after 11 of 11 live pairs
matched the EDGAR header. That was true when observed; it stopped being true for some registrants
afterwards. See ``docs/sec-acceptance-time-design.md``.

**Order:** results are in deterministic filing-date order, ties broken by accession. The
accession is only a tie-breaker; it carries no within-day temporal meaning, and this is not
acceptance order.

Nothing here says when a filing became public or tradable, and nothing synthesises a time.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import date
from typing import Any

from quantforge.ingestion.canonical import CanonicalSecFiling
from quantforge.models.enums import SecFormType
from quantforge.providers.errors import ProviderPayloadError

__all__ = [
    "map_history_filings",
    "map_recent_filings",
    "recent_row_count",
]

logger = logging.getLogger(__name__)

_SUPPORTED = {form.value: form for form in SecFormType}


def _column(recent: Mapping[str, Any], name: str, length: int, *, required: bool) -> list[Any]:
    values = recent.get(name)
    if values is None:
        if required:
            message = f"submissions 'filings.recent' has no {name!r} array"
            raise ProviderPayloadError(message)
        return [None] * length
    if not isinstance(values, list) or len(values) != length:
        message = f"submissions 'filings.recent.{name}' must be an array of length {length}"
        raise ProviderPayloadError(message)
    return values


def _optional_date(raw: Any) -> date | None:
    if raw is None or raw == "":
        return None
    return date.fromisoformat(str(raw))


def _optional_text(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def recent_row_count(document: Any) -> int:
    """Number of rows (all forms) in ``filings.recent``; ``0`` if the shape is not usable."""
    filings_block = document.get("filings") if isinstance(document, dict) else None
    recent = filings_block.get("recent") if isinstance(filings_block, dict) else None
    accessions = recent.get("accessionNumber") if isinstance(recent, dict) else None
    return len(accessions) if isinstance(accessions, list) else 0


def map_history_filings(
    document: Any, *, cik: str
) -> tuple[list[CanonicalSecFiling], list[str], int]:
    """Map one ``filings.files`` history file to canonical filings: ``(filings, rejected, rows)``.

    A history file is a *flat* object of the same parallel arrays (no ``filings.recent``
    wrapper), and does not necessarily state its own ``cik`` -- its attribution comes
    from the parent response that named it and from the CIK embedded in its file name,
    both checked by the caller. A document that does carry a ``filings`` wrapper is the
    parent shape, not a history file, and is refused rather than guessed at. ``rows`` is
    the number of rows of every form in the file.
    """
    if not isinstance(document, dict):
        message = f"expected an object at the top level, got {type(document).__name__}"
        raise ProviderPayloadError(message)
    if "filings" in document:
        message = "a history file must be a flat set of arrays, not a 'filings' wrapper"
        raise ProviderPayloadError(message)
    stated_cik = document.get("cik")
    if stated_cik is not None and str(stated_cik).strip().zfill(10) != cik:
        message = (
            f"history file is for CIK {str(stated_cik).strip().zfill(10)}, not the requested "
            f"{cik}; refusing to attach it to the wrong registrant"
        )
        raise ProviderPayloadError(message)
    rows = _length(document)
    filings, rejected = _map_columns(document, cik=cik)
    return filings, rejected, rows


def map_recent_filings(document: Any, *, cik: str) -> tuple[list[CanonicalSecFiling], list[str]]:
    """Map ``filings.recent`` to canonical filings of the supported forms.

    Returns ``(filings, rejected)``: ``rejected`` has one reason per
    supported-form record that failed validation (its accession is named). The
    envelope itself being malformed raises :class:`ProviderPayloadError`.
    """
    if not isinstance(document, dict):
        message = f"expected an object at the top level, got {type(document).__name__}"
        raise ProviderPayloadError(message)
    stated_cik = document.get("cik")
    if stated_cik is None:
        message = "submissions document does not state its own 'cik'; it cannot be attributed"
        raise ProviderPayloadError(message)
    if str(stated_cik).strip().zfill(10) != cik:
        message = (
            f"submissions document is for CIK {str(stated_cik).strip().zfill(10)}, "
            f"not the requested {cik}; refusing to attach it to the wrong registrant"
        )
        raise ProviderPayloadError(message)
    filings_block = document.get("filings")
    recent = filings_block.get("recent") if isinstance(filings_block, dict) else None
    if not isinstance(recent, dict):
        message = "submissions document has no 'filings.recent' object"
        raise ProviderPayloadError(message)

    return _map_columns(recent, cik=cik)


def _map_columns(
    recent: Mapping[str, Any], *, cik: str
) -> tuple[list[CanonicalSecFiling], list[str]]:
    accessions = _column(recent, "accessionNumber", _length(recent), required=True)
    count = len(accessions)
    forms = _column(recent, "form", count, required=True)
    filing_dates = _column(recent, "filingDate", count, required=True)
    # Present for schema integrity only: never parsed, never judged (see the module docstring).
    acceptance_text = _column(recent, "acceptanceDateTime", count, required=False)
    report_dates = _column(recent, "reportDate", count, required=False)
    documents = _column(recent, "primaryDocument", count, required=False)
    items = _column(recent, "items", count, required=False)

    filings: list[CanonicalSecFiling] = []
    rejected: list[str] = []
    for index in range(count):
        form = _SUPPORTED.get(str(forms[index]))
        if form is None:
            continue
        accession = str(accessions[index])
        try:
            _require_acceptance_text_or_null(acceptance_text[index])
            filings.append(
                CanonicalSecFiling(
                    cik=cik,
                    accession_number=accession,
                    form=form,
                    filing_date=date.fromisoformat(str(filing_dates[index])),
                    accepted_at=None,
                    report_date=_optional_date(report_dates[index]),
                    primary_document=_optional_text(documents[index]),
                    items=_optional_text(items[index]),
                )
            )
        except ValueError as exc:
            rejected.append(f"{accession} ({form.value}): {exc}")
    filings.sort(key=lambda filing: (filing.filing_date, filing.accession_number))
    return filings, rejected


def _require_acceptance_text_or_null(value: Any) -> None:
    """Schema integrity only: the source value is text or null. Its content is not judged."""
    if value is None:
        return
    if not isinstance(value, str):
        message = "acceptanceDateTime must be text or null (it is never read as a time)"
        raise ValueError(message)


def _length(recent: Mapping[str, Any]) -> int:
    accessions = recent.get("accessionNumber")
    if not isinstance(accessions, list):
        message = "submissions 'filings.recent.accessionNumber' must be an array"
        raise ProviderPayloadError(message)
    return len(accessions)
