"""Map the SEC submissions JSON to canonical filings -- and read acceptance time honestly.

Source: ``https://data.sec.gov/submissions/CIK##########.json``. Its ``filings.recent``
object is columnar (parallel arrays: ``accessionNumber``, ``filingDate``,
``reportDate``, ``acceptanceDateTime``, ``form``, ``items``, ``primaryDocument``).
Only the supported forms are mapped; any other form is skipped, not failed.

Acceptance time: what is and is not established
-----------------------------------------------
The SEC's EDGAR Public Dissemination Service specification defines Acceptance
Time as the "Time (EST) at which the submission was accepted by EDGAR"
(``HHMMSS``), and EDGAR's operating rules (6 a.m.-10 p.m., the 5:30 p.m. filing
cut-off) are stated in Eastern time. The submissions JSON renders the same moment
as ``2024-02-02T16:30:22.000Z``, and **no SEC document found defines that ``Z``
as UTC**. It is therefore *not* trusted as UTC: the digits are read as an
**America/New_York wall-clock time** (prevailing Eastern, so daylight saving is
applied by the timezone rules, never by a fixed offset) and converted to UTC.

That reading is not certain: the spec's literal "EST" could mean fixed UTC-5 all
year, which would shift summer instants by an hour. It cannot be settled offline,
so the instant is stored as read and ``SEC_FILING_ACCEPTANCE`` stays *unapproved*
for point-in-time use (see ``quantforge.research.availability``) until it has been
checked against live SEC data.

Plausibility is *reported*, never enforced
------------------------------------------
:func:`acceptance_anomalies` compares an accepted time with facts the SEC states
only as *normal* behaviour -- EDGAR's 06:00-22:00 Eastern hours, Monday-Friday,
and that a filing accepted after 17:30 Eastern "normally" is dated a later
business day (the cut-off decides the filing **date**; EDGAR still accepts later
in its hours). Exceptions exist (extended cut-offs for some forms, post-acceptance
filing-date corrections, older operating hours), so a mismatch is a warning, not
grounds to drop a source fact. Only what makes the instant itself unknowable --
an unrecognised format, an impossible calendar time, a daylight-saving gap or
fold -- is rejected.

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

__all__ = ["acceptance_anomalies", "map_recent_filings", "parse_acceptance_datetime"]

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

_ACCEPTANCE = re.compile(
    r"^(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})T(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})"
    r"(?:\.(?P<frac>\d{1,6}))?Z?$"
)
_SUPPORTED = {form.value: form for form in SecFormType}


def parse_acceptance_datetime(raw: str) -> datetime:
    """Return the UTC instant for an EDGAR acceptance string, or raise ``ValueError``.

    The digits are read as America/New_York wall-clock time (the trailing ``Z``, if
    present, is not treated as a UTC marker -- see the module docstring). An
    unrecognised format, an impossible calendar time, and an ambiguous or
    non-existent local time (a daylight-saving transition hour) are rejected;
    plausibility against EDGAR's hours and filing-date rule is reported separately
    by :func:`acceptance_anomalies`, never enforced here.
    """
    match = _ACCEPTANCE.fullmatch(raw.strip())
    if match is None:
        message = f"acceptanceDateTime {raw!r} is not a recognised EDGAR timestamp"
        raise ValueError(message)
    try:
        # Deliberately naive: this is the *local* Eastern wall time, localised below.
        wall = datetime(  # noqa: DTZ001
            int(match["y"]),
            int(match["mo"]),
            int(match["d"]),
            int(match["h"]),
            int(match["mi"]),
            int(match["s"]),
            int((match["frac"] or "").ljust(6, "0")),  # keep the sub-second part
        )
    except ValueError as exc:
        message = f"acceptanceDateTime {raw!r} is not a valid calendar time: {exc}"
        raise ValueError(message) from exc

    if wall.year < _EDGAR_FIRST_YEAR:
        message = f"acceptanceDateTime {raw!r} predates EDGAR ({_EDGAR_FIRST_YEAR})"
        raise ValueError(message)
    first = wall.replace(tzinfo=EASTERN, fold=0)
    try:
        instant = first.astimezone(UTC)
    except OverflowError as exc:
        message = f"acceptanceDateTime {raw!r} is out of range"
        raise ValueError(message) from exc
    if instant.astimezone(EASTERN).replace(tzinfo=None) != wall:
        message = f"acceptanceDateTime {raw!r} is a non-existent Eastern local time (DST gap)"
        raise ValueError(message)
    if first.utcoffset() != wall.replace(tzinfo=EASTERN, fold=1).utcoffset():
        message = f"acceptanceDateTime {raw!r} is an ambiguous Eastern local time (DST fold)"
        raise ValueError(message)

    return instant


def acceptance_anomalies(accepted_at: datetime, filing_date: date) -> tuple[str, ...]:
    """Describe where an acceptance deviates from EDGAR's *normal* behaviour (never rejects).

    Reported as warnings so a systematic mis-reading of the timezone stays visible
    without discarding legitimate source facts (see the module docstring).
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
            accepted_at = parse_acceptance_datetime(raw_accepted)
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
