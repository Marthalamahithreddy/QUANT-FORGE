"""Map the SEC submissions JSON to canonical filings -- and read acceptance time honestly.

Source: ``https://data.sec.gov/submissions/CIK##########.json``. Its ``filings.recent``
object is columnar (parallel arrays: ``accessionNumber``, ``filingDate``,
``reportDate``, ``acceptanceDateTime``, ``form``, ``items``, ``primaryDocument``).
Only the supported forms are mapped; any other form is skipped, not failed.

Acceptance time: what is and is not established
-----------------------------------------------
**Official, for a different representation.** The SEC's EDGAR Public Dissemination
Service specification defines Acceptance Time as the "Time (EST) at which the
submission was accepted by EDGAR" (``HHMMSS``), and EDGAR's operating rules (6 a.m.
-10 p.m., the 5:30 p.m. filing cut-off) are stated in Eastern time. That describes
the SGML header / index-page clock, not this JSON.

**Not documented: the submissions JSON field.** No SEC document found defines
``acceptanceDateTime`` here -- not its timezone, not the trailing ``Z``, not the
``.000`` precision. The official semantics are *unresolved*.

**Empirical rule (not an official claim).** The bounded step-10.7A live check (fetched
2026-10-04) compared 11 accessions (10-Q, 10-K, 10-K/A; January/February standard time
and April/May daylight time) against the same accession's EDGAR index "Accepted" time
and SGML header ``<ACCEPTANCE-DATETIME>``. In 11 of 11 the JSON value, read as **UTC**
(``Z`` as ISO 8601 writes it), equals the index/header value read as
**America/New_York** wall clock, to the second. Seven representative pairs (both
offsets, 10-Q/10-K/10-K/A, one crossing UTC midnight) are pinned in the tests. :func:`parse_submissions_acceptance_datetime` applies exactly
that rule, to this JSON only. It requires the explicit ``Z`` marker and refuses a
value without one rather than assign it a zone. If SEC ever documents or changes the field, revisit.

**History.** Before 10.7A.1 this module read the JSON digits as America/New_York wall
clock and converted to UTC. That was wrong: the instants came out 5 hours (standard
time) or 4 hours (daylight time) *later* than the acceptance the paired EDGAR
representations show. The dev database was observed empty at that check (it could not
be reached when the fix landed), so no affected stored row is currently known. If a row
written by the old parser ever exists, re-ingesting that accession now raises
``SecFilingConflictError`` (stored metadata is never overwritten): delete that row's
data deliberately and re-ingest; there is no automatic backfill.

**What the value is.** Filing-acceptance *metadata* only. It is neither
``filing_public_at`` (EDGAR can disseminate later: a filing accepted after 5:30 p.m.
Eastern is normally not disseminated until the next business day, and SEC publishes
no first-availability timestamp), nor ``disclosure_observed_at``, nor
``known_as_of``, nor a tradable time. ``SEC_FILING_ACCEPTANCE`` stays *unapproved*
for point-in-time use (see ``quantforge.research.availability``). The FSDS
``accepted`` text is a different source representation and is never read here.

Plausibility is *reported*, never enforced
------------------------------------------
:func:`acceptance_anomalies` compares an accepted time with facts the SEC states
only as *normal* behaviour -- EDGAR's 06:00-22:00 Eastern hours, Monday-Friday,
and that a filing accepted after 17:30 Eastern "normally" is dated a later
business day (the cut-off decides the filing **date**; EDGAR still accepts later
in its hours). Exceptions exist (extended cut-offs for some forms, post-acceptance
filing-date corrections, older operating hours), so a mismatch is a warning, not
grounds to drop a source fact. Only what makes the instant itself unknowable --
an unrecognised format (including a missing ``Z``) or an impossible calendar
time -- is rejected. The Eastern conversion left in :func:`acceptance_anomalies` is
only for that plausibility report, never for reading the instant.

Nothing here says the acceptance instant is the first public disclosure of the
content, and nothing synthesises a time from a date: a record without a usable
``acceptanceDateTime`` is rejected.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime, time
from typing import Any, Final
from zoneinfo import ZoneInfo

from quantforge.ingestion.canonical import CanonicalSecFiling
from quantforge.models.enums import SecFormType
from quantforge.providers.errors import ProviderPayloadError

__all__ = ["acceptance_anomalies", "map_recent_filings", "parse_submissions_acceptance_datetime"]

logger = logging.getLogger(__name__)

EASTERN: Final = ZoneInfo("America/New_York")

#: EDGAR's documented acceptance window (Eastern wall clock).
_EDGAR_OPEN: Final = time(6, 0, 0)
_EDGAR_CLOSE: Final = time(22, 0, 0)
#: Filings accepted AFTER this Eastern time (5:30 p.m.) normally are dated a later business day.
_FILING_DATE_CUTOFF: Final = time(17, 30, 0)
#: A same-day cut-off filing can be dated up to a long weekend later.
_MAX_FILING_DATE_LAG_DAYS: Final = 5
_EDGAR_FIRST_YEAR: Final = 1993
#: A batch where most records look abnormal points at a systematic misreading (e.g. the
#: timezone), not at noise: the whole response is then refused (see map_recent_filings).
_SYSTEMATIC_ANOMALY_FRACTION: Final = 0.5
_SYSTEMATIC_ANOMALY_MIN_RECORDS: Final = 5

#: The submissions-JSON shape observed live: ``2026-01-30T11:01:32.000Z``. The ``Z`` is required.
_SUBMISSIONS_ACCEPTANCE = re.compile(
    r"(?P<y>[0-9]{4})-(?P<mo>[0-9]{2})-(?P<d>[0-9]{2})T"
    r"(?P<h>[0-9]{2}):(?P<mi>[0-9]{2}):(?P<s>[0-9]{2})(?:\.(?P<frac>[0-9]{1,6}))?Z"
)
_SUPPORTED = {form.value: form for form in SecFormType}


def parse_submissions_acceptance_datetime(raw: str) -> datetime:
    """Return the UTC instant for a submissions-JSON ``acceptanceDateTime``, or raise ``ValueError``.

    Applies the *empirically validated* rule from the module docstring: the value
    carries an explicit ``Z`` and is read as UTC. This is a representation rule
    checked against paired EDGAR index/header values, **not** an official SEC
    definition of the field, and it says nothing about public availability. Seconds
    and any stated fractional seconds are kept; none are invented.

    A value without the ``Z``, with any other offset, with surrounding whitespace, with a
    non-ASCII or non-digit character, or naming an
    impossible calendar time is rejected -- nothing is coerced. Plausibility against
    EDGAR's hours and filing-date rule is reported separately by
    :func:`acceptance_anomalies`, never enforced here.
    """
    match = _SUBMISSIONS_ACCEPTANCE.fullmatch(raw)
    if match is None:
        message = (
            f"acceptanceDateTime {raw!r} is not a recognised submissions timestamp "
            "(expected YYYY-MM-DDTHH:MM:SS[.fff]Z)"
        )
        raise ValueError(message)
    try:
        instant = datetime(
            int(match["y"]),
            int(match["mo"]),
            int(match["d"]),
            int(match["h"]),
            int(match["mi"]),
            int(match["s"]),
            int((match["frac"] or "").ljust(6, "0")),  # keep the sub-second part
            tzinfo=UTC,
        )
    except ValueError as exc:
        message = f"acceptanceDateTime {raw!r} is not a valid calendar time: {exc}"
        raise ValueError(message) from exc
    if instant.year < _EDGAR_FIRST_YEAR:
        message = f"acceptanceDateTime {raw!r} predates EDGAR ({_EDGAR_FIRST_YEAR})"
        raise ValueError(message)
    return instant


def acceptance_anomalies(accepted_at: datetime, filing_date: date) -> tuple[str, ...]:
    """Describe where an acceptance deviates from EDGAR's *normal* behaviour (never rejects).

    Reported as warnings so a systematic mis-reading of the timezone stays visible
    without discarding legitimate source facts (see the module docstring). The
    Eastern conversion here compares an already-known instant with EDGAR's stated
    Eastern rules; it is not how the instant is read.
    """
    wall = accepted_at.astimezone(EASTERN)
    notes: list[str] = []
    if wall.weekday() >= 5:  # noqa: PLR2004 - Saturday/Sunday
        notes.append("accepted on a weekend (EDGAR normally accepts Monday-Friday)")
    if not _EDGAR_OPEN <= wall.time() <= _EDGAR_CLOSE:
        notes.append("accepted outside EDGAR's normal 06:00-22:00 Eastern hours")
    lag = (filing_date - wall.date()).days
    if lag < 0:
        notes.append(f"filing date {filing_date} precedes the acceptance date")
    elif wall.time() > _FILING_DATE_CUTOFF and lag == 0:
        notes.append(
            "accepted after 17:30 Eastern yet dated the same day (normally the next business day)"
        )
    elif wall.time() <= _FILING_DATE_CUTOFF and lag != 0:
        notes.append("accepted by 17:30 Eastern yet dated a different day (normally the same day)")
    elif lag > _MAX_FILING_DATE_LAG_DAYS:
        notes.append(f"filing date is {lag} days after acceptance")
    return tuple(notes)


def _is_timezone_sensitive(note: str) -> bool:
    """Only anomalies a timezone misreading would produce feed the systematic tripwire."""
    return any(marker in note for marker in ("weekend", "06:00-22:00", "17:30"))


def _require_not_implausibly_early(
    accepted_at: datetime, filing_date: date, raw_accepted: str
) -> None:
    """Reject the one direction that would claim availability before the filing could exist.

    An acceptance implausibly earlier than the filing date is corrupt. A later
    acceptance, or any after-17:30 acceptance, is never rejected here.
    """
    early_by = (filing_date - accepted_at.astimezone(EASTERN).date()).days
    if early_by > _MAX_FILING_DATE_LAG_DAYS:
        message = (
            f"acceptanceDateTime {raw_accepted!r} is {early_by} days before the filing date "
            f"{filing_date}; refusing an implausibly early availability"
        )
        raise ValueError(message)


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

    accessions = _column(recent, "accessionNumber", _length(recent), required=True)
    count = len(accessions)
    forms = _column(recent, "form", count, required=True)
    filing_dates = _column(recent, "filingDate", count, required=True)
    accepted = _column(recent, "acceptanceDateTime", count, required=True)
    report_dates = _column(recent, "reportDate", count, required=False)
    documents = _column(recent, "primaryDocument", count, required=False)
    items = _column(recent, "items", count, required=False)

    filings: list[CanonicalSecFiling] = []
    rejected: list[str] = []
    anomalous = 0
    for index in range(count):
        form = _SUPPORTED.get(str(forms[index]))
        if form is None:
            continue
        accession = str(accessions[index])
        try:
            filing_date = date.fromisoformat(str(filing_dates[index]))
            raw_accepted = accepted[index]
            if not isinstance(raw_accepted, str) or not raw_accepted.strip():
                message = "no acceptanceDateTime (a time is never synthesised from a date)"
                raise ValueError(message)
            accepted_at = parse_submissions_acceptance_datetime(raw_accepted)
            _require_not_implausibly_early(accepted_at, filing_date, raw_accepted)
            candidate = CanonicalSecFiling(
                cik=cik,
                accession_number=accession,
                form=form,
                filing_date=filing_date,
                accepted_at=accepted_at,
                report_date=_optional_date(report_dates[index]),
                primary_document=_optional_text(documents[index]),
                items=_optional_text(items[index]),
            )
            notes = acceptance_anomalies(accepted_at, filing_date)
            # Logged only after the record validated, so the identifier is well-formed.
            for note in notes:
                logger.warning(
                    "SEC filing %s (%s): %s", candidate.accession_number, form.value, note
                )
            anomalous += any(_is_timezone_sensitive(note) for note in notes)
            filings.append(candidate)
        except ValueError as exc:
            rejected.append(f"{accession} ({form.value}): {exc}")
    if (
        len(filings) >= _SYSTEMATIC_ANOMALY_MIN_RECORDS
        and anomalous / len(filings) > _SYSTEMATIC_ANOMALY_FRACTION
    ):
        message = (
            f"{anomalous} of {len(filings)} filings deviate from EDGAR's normal acceptance "
            "pattern; this looks like a systematic misreading of acceptanceDateTime (its "
            "timezone) rather than noise, so the response is refused"
        )
        raise ProviderPayloadError(message)
    filings.sort(key=lambda filing: (filing.accepted_at, filing.accession_number))
    return filings, rejected


def _length(recent: Mapping[str, Any]) -> int:
    accessions = recent.get("accessionNumber")
    if not isinstance(accessions, list):
        message = "submissions 'filings.recent.accessionNumber' must be an array"
        raise ProviderPayloadError(message)
    return len(accessions)
