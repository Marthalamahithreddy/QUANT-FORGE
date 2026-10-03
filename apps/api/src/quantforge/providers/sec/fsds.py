"""Read the SEC Financial Statement Data Sets (FSDS) package: the first fact source adapter.

FSDS is QuantForge's first structured extraction source for accession-bound financial facts.
It is **not**, by itself, a point-in-time source of record: the SEC publishes it as a bulk
quarterly artifact, so a fact in it was available *when the package was published*, which is
later than -- and not stated alongside -- the filing's EDGAR acceptance. Nothing here derives an
availability time.

Source contract (verified against the SEC's current documentation, "Financial Statement Data
Sets", ``https://www.sec.gov/files/financial-statement-data-sets.pdf``)
-----------------------------------------------------------------------------------------------
* A quarterly ZIP holds tab-delimited UTF-8 files ``sub.txt``, ``num.txt``, ``tag.txt`` and
  ``pre.txt``; the first line names the columns in lowercase.
* **SUB** has one row per submission, keyed by ``adsh`` (the dashed accession).
  ``accepted`` is ``yyyy-mm-dd hh:mm:ss`` with no stated timezone. ``period`` and ``fye`` are
  *rounded to the nearest month end*. ``prevrpt`` ("subsequently amended") is knowledge from
  AFTER the filing and is deliberately never read. There is no ``amended`` flag.
* **NUM** has one row per distinct amount on the primary financial statements, keyed by
  ``adsh, tag, version, ddate, qtrs, uom, segments, coreg``. ``ddate`` is the end date rounded to
  the nearest month end; ``qtrs`` is the span in quarters rounded to a whole number and ``0``
  means a point in time; ``value`` is unscaled and "limited to four digits to the right of the
  decimal point" (NUMERIC(28,4)); there is no ``decimals``/precision attribute. ``coreg`` names a
  co-registrant/parent/other entity. ``segments`` (axis/member reporting) was added by the SEC's
  December 2024 reprocessing: an earlier layout has no such column and, per the SEC, compiled
  only entity-wide or co-registrant data.
* TAG and PRE are **not read**. ``version`` equals the accession for an issuer extension, so
  extension detection needs no TAG row, and statement classification / presentation order are
  semantic selection concerns for a later layer.

Scope: forms 10-K, 10-K/A, 10-Q and 10-Q/A (the :class:`~quantforge.models.enums.SecFormType`
members that carry financial statements). Other forms in a package are not read.

Memory and selection
--------------------
Both files are streamed line by line and never held whole. A request names accessions and/or
CIKs (:class:`FsdsSelection`); a row outside the selection is dismissed after reading only its
accession (SUB: also its CIK) column, so it is neither validated nor kept (the stream as a whole
must still decode as strict UTF-8, as the SEC specifies). Selected rows are validated strictly; a rejected selected row is counted and described, never skipped silently.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import IO, Final

from quantforge.ingestion.canonical import CanonicalFactSubmission, CanonicalFinancialFact
from quantforge.models.enums import SecFormType
from quantforge.models.filing import ACCESSION_REGEX
from quantforge.providers.sec.identifiers import normalise_cik

__all__ = [
    "FSDS_FORMS",
    "FsdsNumScan",
    "FsdsPackage",
    "FsdsPackageError",
    "FsdsSelection",
    "FsdsSubmissionScan",
]

#: The forms whose facts this adapter reads. 8-K and other forms are out of scope.
FSDS_FORMS: Final[dict[str, SecFormType]] = {
    form.value: form
    for form in (
        SecFormType.FORM_10K,
        SecFormType.FORM_10K_A,
        SecFormType.FORM_10Q,
        SecFormType.FORM_10Q_A,
    )
}

_SUB_MEMBER: Final = "sub.txt"
_NUM_MEMBER: Final = "num.txt"
_SUB_REQUIRED: Final = (
    "adsh",
    "cik",
    "form",
    "period",
    "fy",
    "fp",
    "fye",
    "filed",
    "accepted",
    "nciks",
    "aciks",
)
_NUM_REQUIRED: Final = ("adsh", "tag", "version", "coreg", "ddate", "qtrs", "uom", "value")
_NUM_OPTIONAL: Final = ("segments",)
_DIGITS: Final = re.compile(r"[0-9]+")
_YYYYMMDD: Final = re.compile(r"[0-9]{8}")
#: Plain decimal text only: no exponent, no ``NaN``/``Infinity``, no digit-group underscores.
_PLAIN_DECIMAL: Final = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
_ACCEPTED_FORMAT: Final = "%Y-%m-%d %H:%M:%S"
_COPY_CHUNK: Final = 1024 * 1024
_MAX_REJECT_SAMPLES: Final = 20
#: What a bad CRC, a truncated or an invalid deflate stream raise while a member is being read.
_CORRUPT_MEMBER: Final = (zipfile.BadZipFile, zlib.error, EOFError)


class FsdsPackageError(ValueError):
    """The package itself is unusable: not a ZIP, a member or required column is missing."""


@dataclass(frozen=True, slots=True)
class FsdsSelection:
    """Which submissions a bounded ingestion wants: explicit accessions and/or CIKs."""

    accessions: frozenset[str] = frozenset()
    ciks: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.accessions and not self.ciks:
            message = "an FSDS selection must name at least one accession or CIK"
            raise ValueError(message)

    def matches(self, accession: str, cik: str) -> bool:
        return accession in self.accessions or cik in self.ciks


@dataclass(slots=True)
class _Rejections:
    """Rejected-row bookkeeping shared by the SUB and NUM scans: a count plus a few samples."""

    rejected_count: int = 0
    rejected_samples: list[str] = field(default_factory=list)

    def reject(self, reason: str) -> None:
        self.rejected_count += 1
        if len(self.rejected_samples) < _MAX_REJECT_SAMPLES:
            self.rejected_samples.append(reason)


@dataclass(slots=True)
class FsdsSubmissionScan(_Rejections):
    """Result of reading SUB for a selection."""

    #: Selected submissions of a supported form, by accession.
    submissions: dict[str, CanonicalFactSubmission] = field(default_factory=dict)
    #: Selected submissions of a form this adapter does not read: accession -> form.
    out_of_scope: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class FsdsNumScan(_Rejections):
    """Counters and rejections accumulated while NUM is streamed."""

    rows_read: int = 0


class _HashingReader(io.RawIOBase):
    """A readable that SHA-256s every byte passing through it."""

    def __init__(self, raw: IO[bytes]) -> None:
        self._raw = raw
        self.digest = hashlib.sha256()

    def readable(self) -> bool:
        return True

    def close(self) -> None:
        try:
            self._raw.close()
        finally:
            super().close()

    def readinto(self, buffer: bytearray | memoryview) -> int:  # type: ignore[override]
        data = self._raw.read(len(buffer))
        count = len(data)
        buffer[:count] = data
        self.digest.update(data)
        return count


class _Member:
    """One ZIP member opened as a stream of text lines, hashed as it is consumed."""

    def __init__(self, archive: zipfile.ZipFile, name: str) -> None:
        try:
            self._hasher = _HashingReader(archive.open(name))
        except (KeyError, zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
            message = f"cannot open {name!r} in the package: {type(exc).__name__}"
            raise FsdsPackageError(message) from exc
        self._text = io.TextIOWrapper(
            io.BufferedReader(self._hasher, _COPY_CHUNK),
            encoding="utf-8-sig",
            errors="strict",
            newline="\n",
        )
        self.name = name

    def lines(self) -> Iterator[list[str]]:
        try:
            for line in self._text:
                yield line.rstrip("\r\n").split("\t")
        except UnicodeDecodeError as exc:
            message = f"{self.name} is not valid UTF-8 ({exc.reason} at byte {exc.start})"
            raise FsdsPackageError(message) from exc
        except _CORRUPT_MEMBER as exc:
            message = f"{self.name} is corrupt inside the package: {type(exc).__name__}"
            raise FsdsPackageError(message) from exc

    def sha256_after_full_read(self) -> str:
        """The member's digest. Drains any unread remainder so it covers the whole member."""
        try:
            while self._hasher.read(_COPY_CHUNK):
                pass
        except _CORRUPT_MEMBER as exc:
            message = f"{self.name} is corrupt inside the package: {type(exc).__name__}"
            raise FsdsPackageError(message) from exc
        return self._hasher.digest.hexdigest()

    def close(self) -> None:
        self._text.close()


def _header_index(
    header: list[str], member: str, required: tuple[str, ...], optional: tuple[str, ...] = ()
) -> dict[str, int]:
    names = [column.strip().lower() for column in header]
    if len(set(names)) != len(names):
        message = f"{member} has duplicate column names"
        raise FsdsPackageError(message)
    index = {name: position for position, name in enumerate(names)}
    missing = [column for column in required if column not in index]
    if missing:
        message = f"{member} is missing required column(s): {', '.join(missing)}"
        raise FsdsPackageError(message)
    return {column: index[column] for column in (*required, *optional) if column in index}


def _date_yyyymmdd(raw: str, name: str) -> date:
    if not _YYYYMMDD.fullmatch(raw):
        message = f"{name} {raw!r} is not yyyymmdd"
        raise ValueError(message)
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
    except ValueError as exc:
        message = f"{name} {raw!r} is not a valid calendar date"
        raise ValueError(message) from exc


def _pad(row: list[str], index: dict[str, int]) -> list[str]:
    """Fill omitted trailing (empty) columns so every indexed column can be read."""
    missing = max(index.values()) + 1 - len(row)
    return [*row, *([""] * missing)] if missing > 0 else row


def _optional(raw: str) -> str | None:
    return raw if raw != "" else None


def _map_submission(
    row: list[str], index: dict[str, int], form: SecFormType
) -> CanonicalFactSubmission:
    def cell(name: str) -> str:
        return row[index[name]]

    accepted = _optional(cell("accepted"))
    if accepted is not None:
        try:
            datetime.strptime(accepted, _ACCEPTED_FORMAT)  # noqa: DTZ007 - format check only
        except ValueError as exc:
            message = f"accepted {accepted!r} is not 'yyyy-mm-dd hh:mm:ss'"
            raise ValueError(message) from exc
    fiscal_year = _optional(cell("fy"))
    if fiscal_year is not None and not _DIGITS.fullmatch(fiscal_year):
        message = f"fy {fiscal_year!r} is not a year"
        raise ValueError(message)
    nciks = cell("nciks")
    if not _DIGITS.fullmatch(nciks):
        message = f"nciks {nciks!r} is not a count"
        raise ValueError(message)
    return CanonicalFactSubmission(
        accession_number=cell("adsh"),
        cik=normalise_cik(cell("cik")),
        form=form,
        filing_date=_date_yyyymmdd(cell("filed"), "filed"),
        period_end=_date_yyyymmdd(cell("period"), "period"),
        coregistrant_count=int(nciks),
        fiscal_year=int(fiscal_year) if fiscal_year is not None else None,
        fiscal_period=_optional(cell("fp")),
        fiscal_year_end=_optional(cell("fye")),
        source_stated_accepted=accepted,
        additional_ciks=_optional(cell("aciks")),
    )


def _map_fact(row: list[str], index: dict[str, int]) -> CanonicalFinancialFact:
    def cell(name: str) -> str:
        return row[index[name]]

    qtrs = cell("qtrs")
    if not _DIGITS.fullmatch(qtrs):
        message = f"qtrs {qtrs!r} is not a non-negative whole number"
        raise ValueError(message)
    raw_value = cell("value").strip()
    value: Decimal | None
    if raw_value == "":
        value = None
    elif _PLAIN_DECIMAL.fullmatch(raw_value):
        value = Decimal(raw_value)  # exact decimal text; never via float
    else:
        message = f"value {raw_value!r} is not plain finite decimal text"
        raise ValueError(message)
    return CanonicalFinancialFact(
        accession_number=cell("adsh"),
        tag=cell("tag"),
        taxonomy_version=cell("version"),
        unit=cell("uom"),
        period_end=_date_yyyymmdd(cell("ddate"), "ddate"),
        duration_quarters=int(qtrs),
        value=value,
        segments=cell("segments") if "segments" in index else "",
        coreg=cell("coreg"),
    )


class FsdsPackage:
    """An open FSDS ZIP. Use as a context manager; reads SUB and NUM as streams.

    ``member_sha256`` is filled in for each member once it has been read in full, so a persisted
    fact can be traced to the exact file inside the package it came from.
    """

    def __init__(self, path: Path) -> None:
        try:
            self._archive = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError) as exc:
            message = f"{path.name!r} is not a readable ZIP package: {type(exc).__name__}"
            raise FsdsPackageError(message) from exc
        names = {info.filename.lower(): info.filename for info in self._archive.infolist()}
        for required in (_SUB_MEMBER, _NUM_MEMBER):
            if required not in names:
                self._archive.close()
                message = f"the package has no {required!r} member"
                raise FsdsPackageError(message)
        self._names = names
        self._members: list[_Member] = []
        self.member_sha256: dict[str, str] = {}
        self.num_has_segments: bool | None = None

    def __enter__(self) -> FsdsPackage:
        return self

    def __exit__(self, *_exc: object) -> None:
        # A consumer that stops early (a conflict mid-stream) must not leak open member streams.
        for member in self._members:
            member.close()
        self._archive.close()

    def _open(self, key: str) -> _Member:
        member = _Member(self._archive, self._names[key])
        self._members.append(member)
        return member

    def scan_submissions(self, selection: FsdsSelection) -> FsdsSubmissionScan:
        """Read SUB; validate and keep only the selected submissions."""
        member = self._open(_SUB_MEMBER)
        scan = FsdsSubmissionScan()
        try:
            rows = member.lines()
            header = next(rows, None)
            if header is None:
                message = "sub.txt is empty"
                raise FsdsPackageError(message)
            index = _header_index(header, _SUB_MEMBER, _SUB_REQUIRED)
            # Trailing columns the source may leave empty (and so omit) are tolerated; anything
            # up to the last non-nullable column must be present.
            required_width = max(position for name, position in index.items() if name != "aciks")
            for line_number, row in enumerate(rows, start=2):
                accession = row[index["adsh"]] if len(row) > index["adsh"] else ""
                try:
                    cik = normalise_cik(row[index["cik"]]) if len(row) > index["cik"] else ""
                except ValueError:
                    cik = ""
                if not selection.matches(accession, cik):
                    continue  # not selected (or cannot be attributed): never validated
                form_text = row[index["form"]] if len(row) > index["form"] else None
                if form_text is not None and form_text not in FSDS_FORMS:
                    # A form this adapter does not read: reported, never validated further.
                    scan.out_of_scope[accession] = form_text
                    continue
                if not ACCESSION_REGEX.fullmatch(accession):
                    scan.reject(
                        f"sub.txt line {line_number}: adsh {accession!r} is not a dashed accession"
                    )
                    continue
                if len(row) <= required_width:
                    scan.reject(
                        f"sub.txt line {line_number} ({accession}): only {len(row)} columns"
                    )
                    continue
                self._take_submission(scan, _pad(row, index), index, line_number, accession)
            self.member_sha256[_SUB_MEMBER] = member.sha256_after_full_read()
        finally:
            member.close()
        return scan

    @staticmethod
    def _take_submission(
        scan: FsdsSubmissionScan,
        row: list[str],
        index: dict[str, int],
        line_number: int,
        accession: str,
    ) -> None:
        if accession in scan.submissions or accession in scan.out_of_scope:
            scan.reject(f"sub.txt line {line_number}: duplicate adsh {accession} (the primary key)")
            return
        try:
            scan.submissions[accession] = _map_submission(
                row, index, FSDS_FORMS[row[index["form"]]]
            )
        except ValueError as exc:
            scan.reject(f"sub.txt line {line_number} ({accession}): {exc}")

    def read_num_layout(self) -> bool:
        """Whether NUM carries the ``segments`` column. Reads only the header line."""
        member = self._open(_NUM_MEMBER)
        try:
            header = next(member.lines(), None)
            if header is None:
                message = "num.txt is empty"
                raise FsdsPackageError(message)
            self.num_has_segments = "segments" in _header_index(
                header, _NUM_MEMBER, _NUM_REQUIRED, _NUM_OPTIONAL
            )
        finally:
            member.close()
        return self.num_has_segments

    def iter_facts(
        self, accessions: frozenset[str], scan: FsdsNumScan
    ) -> Iterator[CanonicalFinancialFact]:
        """Stream NUM, yielding a fact per selected row; rejected rows are counted on ``scan``."""
        member = self._open(_NUM_MEMBER)
        try:
            rows = member.lines()
            header = next(rows, None)
            if header is None:
                message = "num.txt is empty"
                raise FsdsPackageError(message)
            index = _header_index(header, _NUM_MEMBER, _NUM_REQUIRED, _NUM_OPTIONAL)
            self.num_has_segments = "segments" in index
            width = max(index.values())
            adsh_at = index["adsh"]
            for line_number, row in enumerate(rows, start=2):
                if len(row) <= adsh_at or row[adsh_at] not in accessions:
                    continue
                scan.rows_read += 1
                if len(row) <= width:
                    scan.reject(f"num.txt line {line_number}: only {len(row)} columns")
                    continue
                try:
                    yield _map_fact(row, index)
                except ValueError as exc:
                    scan.reject(f"num.txt line {line_number} ({row[adsh_at]}): {exc}")
            self.member_sha256[_NUM_MEMBER] = member.sha256_after_full_read()
        finally:
            member.close()
