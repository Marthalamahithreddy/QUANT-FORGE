"""Unit tests for SEC acceptance-time parsing, the submissions mapper and the canonical filing.

No network and no database: the SEC's own semantics are what is under test.
"""

from __future__ import annotations

import ast
import logging
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quantforge.ingestion.canonical import CanonicalSecFiling
from quantforge.models.enums import AvailabilityBasis, SecFormType
from quantforge.models.filing import ITEMS_LENGTH, PRIMARY_DOCUMENT_LENGTH
from quantforge.providers.errors import ProviderPayloadError
from quantforge.providers.sec import mapper as mapper_module
from quantforge.providers.sec.mapper import (
    acceptance_anomalies,
    map_recent_filings,
    parse_submissions_acceptance_datetime,
)
from quantforge.research.availability import APPROVED_AVAILABILITY_BASES

_CIK = "0000320193"


def _utc(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


# ── submissions-JSON acceptance: empirically validated UTC reading (NOT an official claim) ──
#
# Pinned, sanitised pairs from the bounded 10.7A live check (accession, form, the
# submissions-JSON value, the SAME accession's EDGAR index "Accepted" wall clock). The SGML
# header ``<ACCEPTANCE-DATETIME>`` matched the index value to the second in every pair.
# Eastern wall clock follows daylight time, so January/February (UTC-5) and April/May (UTC-4)
# both appear. Only what the regression needs: no payload, no issuer data, no contact.
_EASTERN = ZoneInfo("America/New_York")

#: (accession, form, submissions JSON value, EDGAR index wall clock, header, old-bug shift hours)
# Provenance: bounded 10.7A live check, fetched 2026-10-04 (11 accessions compared; 7 pinned).
_PAIRS = [
    (
        "0000320193-26-000006",
        "10-Q",
        "2026-01-30T11:01:32.000Z",
        "2026-01-30 06:01:32",
        "20260130060132",
        5,
    ),
    (  # accepted late evening Eastern: the JSON UTC date is the NEXT day
        "0001628280-26-003952",
        "10-K",
        "2026-01-29T01:55:03.000Z",
        "2026-01-28 20:55:03",
        "20260128205503",
        5,
    ),
    (
        "0001045810-26-000021",
        "10-K",
        "2026-02-25T21:42:19.000Z",
        "2026-02-25 16:42:19",
        "20260225164219",
        5,
    ),
    (
        "0000320193-26-000013",
        "10-Q",
        "2026-05-01T10:01:00.000Z",
        "2026-05-01 06:01:00",
        "20260501060100",
        4,
    ),
    (
        "0001193125-26-191507",
        "10-Q",
        "2026-04-29T20:06:24.000Z",
        "2026-04-29 16:06:24",
        "20260429160624",
        4,
    ),
    (  # an amendment is its own accession with its own acceptance
        "0001104659-26-053166",
        "10-K/A",
        "2026-04-30T21:13:17.000Z",
        "2026-04-30 17:13:17",
        "20260430171317",
        4,
    ),
    (
        "0001628280-26-026673",
        "10-Q",
        "2026-04-23T01:43:00.000Z",
        "2026-04-22 21:43:00",
        "20260422214300",
        4,
    ),
]
_PAIR_IDS = [pair[0] for pair in _PAIRS]


def _edgar_wall_clock_instant(index_accepted: str) -> datetime:
    """The paired EDGAR representation as an instant: Eastern wall clock, DST by zone rules."""
    wall = datetime.strptime(index_accepted, "%Y-%m-%d %H:%M:%S")  # noqa: DTZ007 - localised next
    return wall.replace(tzinfo=_EASTERN).astimezone(UTC)


def _old_eastern_reading(raw: str) -> datetime:
    """What the pre-10.7A.1 parser did: the JSON digits taken as America/New_York wall clock."""
    wall = datetime.strptime(raw.removesuffix("Z"), "%Y-%m-%dT%H:%M:%S.%f")  # noqa: DTZ007
    return wall.replace(tzinfo=_EASTERN).astimezone(UTC)


@pytest.mark.parametrize(
    ("accession", "form", "raw", "index", "header", "shift"), _PAIRS, ids=_PAIR_IDS
)
def test_the_json_value_equals_the_paired_edgar_acceptance(
    accession: str, form: str, raw: str, index: str, header: str, shift: int
) -> None:
    assert header == index.replace("-", "").replace(":", "").replace(" ", "")
    assert parse_submissions_acceptance_datetime(raw) == _edgar_wall_clock_instant(index)


@pytest.mark.parametrize(
    ("accession", "form", "raw", "index", "header", "shift"), _PAIRS, ids=_PAIR_IDS
)
def test_the_old_eastern_reading_of_the_json_digits_is_the_observed_4_or_5_hour_error(
    accession: str, form: str, raw: str, index: str, header: str, shift: int
) -> None:
    """Pins the exact old defect so it cannot return: +5h in standard time, +4h in daylight time."""
    correct = parse_submissions_acceptance_datetime(raw)
    old = _old_eastern_reading(raw)

    assert old != correct
    assert old - correct == timedelta(hours=shift)
    assert old > _edgar_wall_clock_instant(index), "the old reading claimed a LATER acceptance"


def test_the_pinned_pairs_cover_both_daylight_regimes_and_each_form() -> None:
    offsets = {
        _edgar_wall_clock_instant(index).astimezone(_EASTERN).utcoffset()
        for *_, index, _h, _s in _PAIRS
    }
    assert offsets == {timedelta(hours=-5), timedelta(hours=-4)}, "never a single fixed offset"
    assert {pair[1] for pair in _PAIRS} >= {"10-Q", "10-K", "10-K/A"}
    assert {pair[5] for pair in _PAIRS} == {4, 5}


def test_the_z_is_read_as_utc_so_the_instant_is_never_localised() -> None:
    accepted = parse_submissions_acceptance_datetime("2024-07-16T16:30:22Z")

    assert accepted == _utc(2024, 7, 16, 16, 30, 22)
    assert accepted.utcoffset() == timedelta(0) and accepted.tzinfo is UTC


def test_eastern_dst_transition_hours_are_ordinary_utc_instants() -> None:
    """The old parser rejected these digits as Eastern gap/fold hours; as UTC they are plain."""
    assert parse_submissions_acceptance_datetime("2024-11-03T05:30:00Z") == _utc(2024, 11, 3, 5, 30)
    assert parse_submissions_acceptance_datetime("2024-03-10T07:30:00Z") == _utc(2024, 3, 10, 7, 30)


def test_seconds_and_stated_fractions_are_kept_and_none_are_invented() -> None:
    whole = parse_submissions_acceptance_datetime("2024-01-16T09:00:01Z")
    millis = parse_submissions_acceptance_datetime("2024-01-16T09:00:01.123Z")
    zero = parse_submissions_acceptance_datetime("2024-01-16T09:00:01.000Z")

    assert whole == _utc(2024, 1, 16, 9, 0, 1) == zero
    assert whole.microsecond == 0 and zero.microsecond == 0
    assert millis - whole == timedelta(milliseconds=123), (
        "the sub-second part is kept, never truncated"
    )
    assert (
        parse_submissions_acceptance_datetime("2024-01-16T09:00:01.123456Z").microsecond == 123456
    )


@pytest.mark.parametrize(
    "raw",
    [
        "",  # empty
        "2024-01-16",  # a date is never promoted to an instant
        "20240116163022",  # the SGML-header digits are another representation: not read here
        "2024-01-16 16:30:22",  # the index-page wall clock is another representation: not read here
        "2024-01-16 06:02:00.0",  # an FSDS-shaped value is never run through this parser
        "2024-01-16T16:30:22",  # no Z: no zone is assigned
        "2024-01-16T16:30:22.000",  # no Z, with a fraction
        "2024-01-16T16:30:22+00:00",  # a numeric offset is not the observed representation
        "2024-01-16T16:30:22+01:00",
        "2024-01-16T16:30:22-05:00",
        "2024-01-16T16:30:22 UTC",
        "2024-01-16T16:30:22.0000000Z",  # more than six fractional digits
        "2024-01-16T16:30Z",  # wrong length: no seconds
        "2024-1-16T16:30:22Z",  # wrong length: unpadded month
        "2024-01-16T1a:30:22Z",  # non-digit
        " 2024-01-16T16:30:22Z",  # surrounding whitespace is not stripped away
        "2024-01-16T16:30:22Z\n",
        "\uff12\uff10\uff12\uff14-01-16T16:30:22Z",  # non-ASCII (fullwidth) digits
        "x",
    ],
)
def test_an_unrecognised_representation_is_rejected_not_guessed(raw: str) -> None:
    with pytest.raises(ValueError, match="not a recognised"):
        parse_submissions_acceptance_datetime(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "2024-02-30T10:00:00Z",  # impossible date
        "2023-02-29T10:00:00Z",  # not a leap year
        "2024-13-01T10:00:00Z",  # impossible month
        "2024-01-16T24:00:00Z",  # impossible time
        "2024-01-16T10:60:00Z",
        "2024-01-16T10:00:60Z",
    ],
)
def test_an_impossible_calendar_time_is_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match="not a valid calendar time"):
        parse_submissions_acceptance_datetime(raw)


def test_a_far_future_instant_is_parsed_not_a_crash_and_is_flagged_as_an_anomaly() -> None:
    """Later-than-the-filing-date is never rejected here; it surfaces as a warning."""
    accepted = parse_submissions_acceptance_datetime("9999-12-31T23:00:00.000Z")

    assert accepted == _utc(9999, 12, 31, 23)
    assert acceptance_anomalies(accepted, date(2024, 1, 16))


def test_the_old_generic_parser_name_is_gone() -> None:
    """A name that hid a source contract must not survive: the parser is submissions-specific."""
    assert not hasattr(mapper_module, "parse_acceptance_datetime")


def test_the_mapper_gives_the_pinned_pairs_their_edgar_acceptance_instants() -> None:
    chosen = [_PAIRS[0], _PAIRS[3], _PAIRS[5]]  # standard-time 10-Q, daylight-time 10-Q, 10-K/A
    document = {
        "cik": "320193",
        "filings": {
            "recent": {
                "accessionNumber": [pair[0] for pair in chosen],
                "form": [pair[1] for pair in chosen],
                "filingDate": ["2026-01-30", "2026-05-01", "2026-04-30"],
                "acceptanceDateTime": [pair[2] for pair in chosen],
            }
        },
    }

    filings, rejected = map_recent_filings(document, cik=_CIK)

    assert rejected == []
    by_accession = {f.accession_number: f.accepted_at for f in filings}
    for accession, _form, _raw, index, _header, _shift in chosen:
        assert by_accession[accession] == _edgar_wall_clock_instant(index)


# ── the corrected instant is acceptance metadata, never availability ────────


def test_correcting_the_acceptance_instant_approves_nothing_for_point_in_time_use() -> None:
    assert AvailabilityBasis.SEC_FILING_ACCEPTANCE not in APPROVED_AVAILABILITY_BASES
    assert set(APPROVED_AVAILABILITY_BASES) == {AvailabilityBasis.SOURCE_PUBLICATION}


def test_the_mapper_and_canonical_filing_carry_no_availability_or_execution_field() -> None:
    tree = ast.parse(Path(mapper_module.__file__ or "").read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not {name for name in imported if "availability" in name or "research" in name}
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert not names & {"AvailabilityBasis", "observed_at", "observed_at_basis", "known_as_of"}
    assert not set(CanonicalSecFiling.__dataclass_fields__) & {
        "observed_at",
        "observed_at_basis",
        "filing_public_at",
        "known_as_of",
    }


# ── plausibility is reported, never enforced (the anomaly rules are stated in Eastern) ──


def _anomalies(eastern_wall: str, filing_date: date) -> tuple[str, ...]:
    """Anomalies for an acceptance stated as EASTERN wall clock (EDGAR's own rules are Eastern)."""
    instant = datetime.fromisoformat(eastern_wall).replace(tzinfo=_EASTERN).astimezone(UTC)
    return acceptance_anomalies(instant, filing_date)


def test_a_normal_acceptance_has_no_anomalies() -> None:
    assert _anomalies("2024-07-16T16:30:22", date(2024, 7, 16)) == ()
    assert _anomalies("2024-07-12T18:15:00", date(2024, 7, 15)) == ()  # Friday evening -> Monday


def test_an_acceptance_after_the_cutoff_is_a_valid_instant_not_an_error() -> None:
    """The 17:30 cut-off decides the filing DATE; EDGAR still accepts later in its hours."""
    for raw in ("2024-07-16T21:30:01Z", "2024-07-16T23:45:00Z", "2024-07-17T01:59:59Z"):
        parse_submissions_acceptance_datetime(raw)  # never raises
    assert _anomalies("2024-07-16T19:45:00", date(2024, 7, 17)) == ()


def test_the_cutoff_boundary_is_after_1730_not_at_1730() -> None:
    assert _anomalies("2024-07-16T17:30:00", date(2024, 7, 16)) == ()  # "after" 5:30 only
    assert _anomalies("2024-07-16T17:30:01", date(2024, 7, 17)) == ()


def test_a_same_day_date_after_the_cutoff_is_flagged_but_still_a_fact() -> None:
    notes = _anomalies("2024-07-16T17:45:00", date(2024, 7, 16))

    assert notes and "after 17:30" in notes[0]
    parse_submissions_acceptance_datetime("2024-07-16T21:45:00Z")  # the instant is not in question


def test_a_different_day_date_before_the_cutoff_is_flagged_not_rejected() -> None:
    assert "by 17:30" in _anomalies("2024-07-16T16:00:00", date(2024, 7, 17))[0]


def test_a_filing_date_before_acceptance_is_flagged() -> None:
    assert "precedes" in _anomalies("2024-07-16T16:00:00", date(2024, 7, 15))[0]


def test_weekend_and_out_of_hours_acceptances_are_flagged_not_rejected() -> None:
    assert "weekend" in _anomalies("2024-01-13T10:00:00", date(2024, 1, 13))[0]
    assert any("06:00-22:00" in n for n in _anomalies("2024-01-16T05:59:59", date(2024, 1, 16)))
    assert any("06:00-22:00" in n for n in _anomalies("2024-01-16T22:00:01", date(2024, 1, 16)))
    assert _anomalies("2024-01-16T06:00:00", date(2024, 1, 16)) == ()


def test_an_implausibly_late_filing_date_is_flagged() -> None:
    assert "days after" in _anomalies("2024-07-16T18:00:00", date(2024, 8, 16))[-1]


# ── the mapper ──────────────────────────────────────────────────────────────


def _document(**columns: list[object]) -> dict[str, object]:
    base: dict[str, list[object]] = {
        "accessionNumber": [
            "0000320193-24-000010",
            "0000320193-24-000011",
            "0000320193-24-000012",
            "0000320193-24-000013",
            "0000320193-24-000014",
        ],
        "form": ["10-Q", "8-K", "4", "10-K/A", "10-K"],
        "filingDate": ["2024-05-03", "2024-05-02", "2024-05-01", "2024-12-02", "2024-11-01"],
        "reportDate": ["2024-03-30", "2024-05-02", "", "2024-09-28", "2024-09-28"],
        "acceptanceDateTime": [
            "2024-05-03T20:30:22.000Z",
            "2024-05-02T20:31:00.000Z",
            "2024-05-01T20:00:00.000Z",
            "2024-12-02T14:15:00.000Z",
            "2024-11-01T10:01:36.000Z",
        ],
        "primaryDocument": ["q.htm", "k8.htm", "x.xml", "ka.htm", "k.htm"],
        "items": ["", "2.02,9.01", "", "", ""],
    }
    base.update(columns)
    return {"cik": "320193", "filings": {"recent": base}}


def test_supported_forms_are_mapped_and_others_are_skipped() -> None:
    filings, rejected = map_recent_filings(_document(), cik=_CIK)

    assert rejected == []
    assert [f.form for f in filings] == [
        SecFormType.FORM_8K,
        SecFormType.FORM_10Q,
        SecFormType.FORM_10K,
        SecFormType.FORM_10K_A,
    ]  # Form 4 is not supported; ascending accepted_at
    assert all(f.cik == _CIK for f in filings)


def test_an_8k_is_just_a_filing_with_its_item_codes_preserved_verbatim() -> None:
    filings, _ = map_recent_filings(_document(), cik=_CIK)
    eight_k = next(f for f in filings if f.form is SecFormType.FORM_8K)

    assert eight_k.items == "2.02,9.01"
    assert not hasattr(eight_k, "reported_eps")
    assert not hasattr(eight_k, "observed_at")


def test_an_amendment_is_a_separate_accession_with_its_own_acceptance_time() -> None:
    filings, _ = map_recent_filings(_document(), cik=_CIK)
    original = next(f for f in filings if f.form is SecFormType.FORM_10K)
    amendment = next(f for f in filings if f.form is SecFormType.FORM_10K_A)

    assert original.accession_number != amendment.accession_number
    assert amendment.accepted_at > original.accepted_at
    assert amendment.form.is_amendment and not original.form.is_amendment


def test_dates_are_dates_and_an_empty_report_date_is_none() -> None:
    filings, _ = map_recent_filings(_document(), cik=_CIK)
    eight_k = next(f for f in filings if f.form is SecFormType.FORM_8K)
    quarterly = next(f for f in filings if f.form is SecFormType.FORM_10Q)

    assert quarterly.filing_date == date(2024, 5, 3)
    assert quarterly.report_date == date(2024, 3, 30)
    assert eight_k.report_date == date(2024, 5, 2)
    assert quarterly.primary_document == "q.htm"
    assert type(quarterly.filing_date) is date

    blank, _ = map_recent_filings(
        _document(reportDate=["", "", "", "", ""], items=["", "", "", "", ""]), cik=_CIK
    )
    assert all(f.report_date is None and f.items is None for f in blank)


def test_a_record_without_an_acceptance_time_is_rejected_never_synthesised() -> None:
    document = _document(
        acceptanceDateTime=[
            "2024-05-03T20:30:22.000Z",
            "",
            "2024-05-01T20:00:00.000Z",
            "2024-12-02T14:15:00.000Z",
            "2024-11-01T10:01:36.000Z",
        ]
    )

    filings, rejected = map_recent_filings(document, cik=_CIK)

    assert len(rejected) == 1
    assert "0000320193-24-000011" in rejected[0] and "never synthesised" in rejected[0]
    assert all(f.accession_number != "0000320193-24-000011" for f in filings)
    assert len(filings) == 3  # the other supported records still map


def test_an_unusual_filing_date_is_kept_as_a_source_fact_and_warned_about() -> None:
    document = _document(
        filingDate=["2024-05-03", "2024-05-03", "2024-05-01", "2024-12-02", "2024-11-01"]
    )
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("quantforge.providers.sec.mapper")
    handler = _Capture(level=logging.WARNING)
    logger.addHandler(handler)
    previous_disabled, logger.disabled = logger.disabled, False
    try:
        filings, rejected = map_recent_filings(document, cik=_CIK)
    finally:
        logger.removeHandler(handler)
        logger.disabled = previous_disabled

    assert rejected == [] and len(filings) == 4
    assert any("0000320193-24-000011" in r.getMessage() for r in records)


def test_an_unparseable_acceptance_is_rejected_and_named_without_dropping_the_rest() -> None:
    document = _document(
        acceptanceDateTime=[
            "2024-05-03T20:30:22.000Z",
            "2024-02-30T10:00:00.000Z",
            "2024-05-01T20:00:00.000Z",
            "2024-12-02T14:15:00.000Z",
            "2024-11-01T10:01:36.000Z",
        ]
    )

    filings, rejected = map_recent_filings(document, cik=_CIK)

    assert len(rejected) == 1 and "0000320193-24-000011 (8-K)" in rejected[0]
    assert len(filings) == 3


@pytest.mark.parametrize(
    "document",
    [
        [],
        {"cik": "320193", "filings": {}},
        {"cik": "320193", "filings": []},
        {"cik": "320193", "filings": "x"},
        {"cik": "320193", "filings": {"recent": []}},
        {"cik": "320193", "filings": {"recent": {"form": []}}},
    ],
)
def test_a_malformed_envelope_is_a_payload_error(document: object) -> None:
    with pytest.raises(ProviderPayloadError):
        map_recent_filings(document, cik=_CIK)


def test_a_column_of_the_wrong_length_is_a_payload_error() -> None:
    with pytest.raises(ProviderPayloadError, match="length"):
        map_recent_filings(_document(form=["10-Q"]), cik=_CIK)


# ── the canonical filing contract ───────────────────────────────────────────

_GOOD = {
    "cik": _CIK,
    "accession_number": "0000320193-24-000010",
    "form": SecFormType.FORM_10Q,
    "filing_date": date(2024, 5, 3),
    "accepted_at": _utc(2024, 5, 3, 20, 30, 22),
}


def _filing(**overrides: object) -> CanonicalSecFiling:
    return CanonicalSecFiling(**{**_GOOD, **overrides})  # type: ignore[arg-type]


def test_a_valid_filing_is_accepted() -> None:
    assert _filing().form is SecFormType.FORM_10Q


@pytest.mark.parametrize("cik", ["320193", "032019300000", "ABCDEFGHIJ", ""])
def test_a_cik_must_be_exactly_ten_digits(cik: str) -> None:
    with pytest.raises(ValueError, match="cik"):
        _filing(cik=cik)


@pytest.mark.parametrize(
    "accession", ["000032019324000010", "0000320193-24-00001", "0000320193-2024-000010", ""]
)
def test_an_accession_must_be_dashed_ten_two_six(accession: str) -> None:
    with pytest.raises(ValueError, match="accession_number"):
        _filing(accession_number=accession)


def test_accepted_at_must_be_aware_utc() -> None:
    with pytest.raises(ValueError, match="UTC"):
        _filing(accepted_at=datetime(2024, 5, 3, 16, 30))  # noqa: DTZ001 - deliberately naive
    with pytest.raises(ValueError, match="UTC"):
        _filing(accepted_at=datetime(2024, 5, 3, 16, 30, tzinfo=timezone(timedelta(hours=-4))))


def test_form_and_dates_are_type_checked() -> None:
    with pytest.raises(ValueError, match="form"):
        _filing(form="10-Q")
    with pytest.raises(ValueError, match="plain date"):
        _filing(filing_date=datetime(2024, 5, 3, tzinfo=UTC))
    with pytest.raises(ValueError, match="plain date"):
        _filing(report_date=datetime(2024, 3, 30, tzinfo=UTC))
    with pytest.raises(ValueError, match="non-blank"):
        _filing(primary_document="  ")


def test_canonical_filing_respects_the_stored_limits() -> None:
    with pytest.raises(ValueError, match="predates EDGAR"):
        _filing(filing_date=date(1990, 1, 2))
    with pytest.raises(ValueError, match="stored limit"):
        _filing(primary_document="x" * 256)
    with pytest.raises(ValueError, match="stored limit"):
        _filing(items="9" * (ITEMS_LENGTH + 1))
    _filing(primary_document="x" * PRIMARY_DOCUMENT_LENGTH, items="9" * ITEMS_LENGTH)


def test_a_record_breaking_a_stored_limit_is_rejected_not_fatal_to_the_batch() -> None:
    document = _document(primaryDocument=["q.htm", "y" * 300, "x.xml", "ka.htm", "k.htm"])

    filings, rejected = map_recent_filings(document, cik=_CIK)

    assert len(rejected) == 1 and "0000320193-24-000011" in rejected[0]
    assert len(filings) == 3


def test_a_document_that_does_not_identify_its_registrant_is_refused() -> None:
    document = _document()
    del document["cik"]

    with pytest.raises(ProviderPayloadError, match="does not state its own"):
        map_recent_filings(document, cik=_CIK)


def test_a_document_for_another_registrant_is_refused() -> None:
    document = _document()
    document["cik"] = 789019

    with pytest.raises(ProviderPayloadError, match="wrong registrant"):
        map_recent_filings(document, cik=_CIK)


def test_extreme_years_are_rejected_not_crashes() -> None:
    for raw in ("0001-01-01T00:00:00Z", "1992-12-31T10:00:00Z"):
        with pytest.raises(ValueError, match="predates EDGAR"):
            parse_submissions_acceptance_datetime(raw)


def test_an_acceptance_implausibly_earlier_than_the_filing_date_is_rejected() -> None:
    document = _document(
        acceptanceDateTime=[
            "2019-01-01T10:00:00.000Z",
            "2024-05-02T20:31:00.000Z",
            "2024-05-01T20:00:00.000Z",
            "2024-12-02T14:15:00.000Z",
            "2024-11-01T10:01:36.000Z",
        ]
    )

    filings, rejected = map_recent_filings(document, cik=_CIK)

    assert len(rejected) == 1 and "implausibly early" in rejected[0]
    assert len(filings) == 3


def test_a_late_acceptance_after_the_cutoff_is_never_rejected() -> None:
    """Valid after-17:30 acceptances (dated the next business day) all map."""
    document = _document(
        acceptanceDateTime=[
            "2024-05-02T21:31:05.000Z",
            "2024-05-02T01:59:59.000Z",
            "2024-04-30T23:00:00.000Z",
            "2024-11-30T01:00:00.000Z",
            "2024-10-31T22:30:00.000Z",
        ],
        filingDate=["2024-05-03", "2024-05-02", "2024-05-01", "2024-12-02", "2024-11-01"],
    )

    filings, rejected = map_recent_filings(document, cik=_CIK)

    assert rejected == [] and len(filings) == 4  # Form 4 is skipped


def test_a_batch_that_is_mostly_abnormal_is_refused_as_a_systematic_misreading() -> None:
    """If the timezone were misread, most records would deviate: refuse the response."""
    n = 6
    document = _document(
        accessionNumber=[f"0000320193-24-0001{i:02d}" for i in range(n)],
        form=["10-Q"] * n,
        filingDate=["2024-05-03"] * n,
        reportDate=[""] * n,
        acceptanceDateTime=["2024-05-04T09:00:00.000Z"] * n,  # a Saturday, 05:00 Eastern
        primaryDocument=["q.htm"] * n,
        items=[""] * n,
    )

    with pytest.raises(ProviderPayloadError, match="systematic misreading"):
        map_recent_filings(document, cik=_CIK)


def test_a_few_abnormal_records_among_normal_ones_are_not_systematic() -> None:
    filings, rejected = map_recent_filings(_document(), cik=_CIK)

    assert rejected == [] and len(filings) == 4


def test_a_sub_second_after_the_cutoff_is_after_the_cutoff() -> None:
    assert any("after 17:30" in n for n in _anomalies("2024-07-16T17:30:00.900", date(2024, 7, 16)))
