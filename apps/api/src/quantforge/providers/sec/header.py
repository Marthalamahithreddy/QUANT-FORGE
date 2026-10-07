"""Parse the official EDGAR filing header: the only approved source of ``accepted_at``.

Source: ``https://www.sec.gov/Archives/edgar/data/<cik>/<accession-no-dashes>/<accession>.hdr.sgml``.
Its ``<ACCEPTANCE-DATETIME>`` is ``YYYYMMDDHHMMSS`` and the SEC's dissemination specification states
the acceptance time as Eastern time ("EST", read as America/New_York wall clock with daylight
saving; every header compared in step 11.2G is consistent with that). The conversion to UTC uses the
IANA zone, never a fixed offset, and fails closed on a wall time the zone cannot place uniquely
(an ambiguous or non-existent time around a daylight-saving change): nothing is guessed.

This value is filing-acceptance provenance only. It is not first public disclosure, an earnings
``observed_at``, ``known_as_of``, a session or tradability evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Final
from zoneinfo import ZoneInfo

from quantforge.models.filing import ACCESSION_REGEX
from quantforge.providers.errors import ProviderPayloadError

__all__ = ["FilingHeader", "header_path", "parse_filing_header"]

EASTERN: Final = ZoneInfo("America/New_York")
_ACCEPTANCE = re.compile(r"[0-9]{14}")
_DATE = re.compile(r"[0-9]{8}")
_CIK = re.compile(r"[0-9]{1,10}")
_TAG = re.compile(r"^<(?P<tag>[A-Z0-9-]+)>(?P<value>[^\r\n<]*)\r?$", re.MULTILINE)
_MAX_HEADER_BYTES: Final = 1_000_000
_EDGAR_FIRST_YEAR: Final = 1993


@dataclass(frozen=True, slots=True)
class FilingHeader:
    """What the header states about one submission (identity cross-check fields plus the instant)."""

    accession_number: str
    form: str
    filing_date: date
    ciks: tuple[str, ...]
    accepted_at: datetime
    #: The header's own text, verbatim (``YYYYMMDDHHMMSS``), for audit.
    acceptance_text: str


def header_path(cik: str, accession_number: str) -> str:
    """The Archives path of one accession's header; ``cik`` is the 10-digit zero-padded CIK."""
    if not ACCESSION_REGEX.fullmatch(accession_number):
        message = f"accession must look like 0000320193-24-000123, got {accession_number!r}"
        raise ValueError(message)
    return (
        f"/Archives/edgar/data/{int(cik)}/{accession_number.replace('-', '')}/"
        f"{accession_number}.hdr.sgml"
    )


def eastern_wall_clock_to_utc(text: str) -> datetime:
    """``YYYYMMDDHHMMSS`` as Eastern wall clock -> aware UTC; refuses an ambiguous/missing time."""
    if not _ACCEPTANCE.fullmatch(text):
        message = f"ACCEPTANCE-DATETIME {text!r} is not 14 digits (YYYYMMDDHHMMSS)"
        raise ProviderPayloadError(message)
    try:
        naive = datetime.strptime(text, "%Y%m%d%H%M%S")  # noqa: DTZ007 - localised just below
    except ValueError as exc:
        message = f"ACCEPTANCE-DATETIME {text!r} is not a valid calendar time"
        raise ProviderPayloadError(message) from exc
    if naive.year < _EDGAR_FIRST_YEAR:
        message = f"ACCEPTANCE-DATETIME {text!r} predates EDGAR"
        raise ProviderPayloadError(message)
    first = naive.replace(tzinfo=EASTERN, fold=0)
    second = naive.replace(tzinfo=EASTERN, fold=1)
    if first.utcoffset() != second.utcoffset():
        # A repeated (fall back) or skipped (spring forward) wall time: the zone cannot place it.
        message = (
            f"ACCEPTANCE-DATETIME {text!r} falls in a daylight-saving transition in "
            "America/New_York (ambiguous or non-existent); not guessed"
        )
        raise ProviderPayloadError(message)
    return first.astimezone(UTC)


def parse_filing_header(body: bytes) -> FilingHeader:
    """Parse one header document, or raise :class:`ProviderPayloadError`.

    Requires the ``<SEC-HEADER>`` marker, exactly one each of ``ACCEPTANCE-DATETIME``,
    ``ACCESSION-NUMBER``, ``TYPE`` and ``FILING-DATE``, and at least one ``CIK``. A missing or
    repeated field is refused; nothing falls back to another source.
    """
    if len(body) > _MAX_HEADER_BYTES:
        message = "filing header is implausibly large"
        raise ProviderPayloadError(message)
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        message = "filing header is not UTF-8 text"
        raise ProviderPayloadError(message) from exc
    if not text.lstrip().startswith("<SEC-HEADER>"):
        message = "document does not start with <SEC-HEADER>; not an EDGAR filing header"
        raise ProviderPayloadError(message)
    found: dict[str, list[str]] = {}
    for match in _TAG.finditer(text):
        found.setdefault(match["tag"], []).append(match["value"].strip())

    def single(tag: str) -> str:
        values = found.get(tag, [])
        if len(values) != 1 or not values[0]:
            message = f"filing header must state exactly one non-empty <{tag}>, found {len(values)}"
            raise ProviderPayloadError(message)
        return values[0]

    accession = single("ACCESSION-NUMBER")
    if not ACCESSION_REGEX.fullmatch(accession):
        message = f"header ACCESSION-NUMBER {accession!r} is not a dashed accession"
        raise ProviderPayloadError(message)
    filing_date_text = single("FILING-DATE")
    if not _DATE.fullmatch(filing_date_text):
        message = f"header FILING-DATE {filing_date_text!r} is not YYYYMMDD"
        raise ProviderPayloadError(message)
    try:
        filing_date = datetime.strptime(filing_date_text, "%Y%m%d").date()  # noqa: DTZ007
    except ValueError as exc:
        message = f"header FILING-DATE {filing_date_text!r} is not a valid date"
        raise ProviderPayloadError(message) from exc
    ciks = tuple(dict.fromkeys(value for value in found.get("CIK", []) if value))
    if not ciks:
        message = "filing header states no <CIK>"
        raise ProviderPayloadError(message)
    if not all(_CIK.fullmatch(value) for value in ciks):
        message = "filing header states a <CIK> that is not 1-10 digits"
        raise ProviderPayloadError(message)
    acceptance_text = single("ACCEPTANCE-DATETIME")
    return FilingHeader(
        accession_number=accession,
        form=single("TYPE"),
        filing_date=filing_date,
        ciks=tuple(value.zfill(10) for value in ciks),
        accepted_at=eastern_wall_clock_to_utc(acceptance_text),
        acceptance_text=acceptance_text,
    )
