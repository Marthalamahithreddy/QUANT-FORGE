"""Unit tests for SEC acceptance-time parsing, the submissions mapper and the canonical filing.

No network and no database: the SEC's own semantics are what is under test.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from quantforge.ingestion.canonical import CanonicalSecFiling
from quantforge.models.enums import SecFormType
from quantforge.models.filing import ITEMS_LENGTH, PRIMARY_DOCUMENT_LENGTH
from quantforge.providers.errors import ProviderPayloadError
from quantforge.providers.sec.mapper import (
    acceptance_anomalies,
    map_recent_filings,
    parse_acceptance_datetime,
)

_CIK = "0000320193"


def _utc(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


# ── acceptance time: Eastern wall clock, never a fixed offset, never trusted "Z" ──


def test_a_winter_acceptance_is_read_as_eastern_standard_time() -> None:
    accepted = parse_acceptance_datetime("2024-01-16T16:30:22.000Z")

    assert accepted == _utc(2024, 1, 16, 21, 30, 22)  # EST is UTC-5


def test_a_summer_acceptance_is_read_as_eastern_daylight_time() -> None:
    accepted = parse_acceptance_datetime("2024-07-16T16:30:22.000Z")

    assert accepted == _utc(2024, 7, 16, 20, 30, 22)  # EDT is UTC-4


def test_the_trailing_z_is_not_trusted_as_utc() -> None:
    as_if_utc = _utc(2024, 7, 16, 16, 30, 22)
    accepted = parse_acceptance_datetime("2024-07-16T16:30:22Z")

    assert accepted != as_if_utc
    assert accepted - as_if_utc == timedelta(hours=4)


def test_the_same_wall_time_maps_to_different_instants_either_side_of_dst() -> None:
    before = parse_acceptance_datetime("2024-03-08T10:00:00")  # Fri EST
    after = parse_acceptance_datetime("2024-03-11T10:00:00")  # Mon EDT
    before_fall = parse_acceptance_datetime("2024-11-01T10:00:00")
    after_fall = parse_acceptance_datetime("2024-11-04T10:00:00")

    assert before == _utc(2024, 3, 8, 15, 0, 0)
    assert after == _utc(2024, 3, 11, 14, 0, 0)
    assert before_fall == _utc(2024, 11, 1, 14, 0, 0)  # still EDT
    assert after_fall == _utc(2024, 11, 4, 15, 0, 0)  # EST


def test_fractional_seconds_and_a_missing_z_are_accepted() -> None:
    a = parse_acceptance_datetime("2024-01-16T09:00:01.123")
    b = parse_acceptance_datetime("2024-01-16T09:00:01")

    assert b == _utc(2024, 1, 16, 14, 0, 1)
    assert a - b == timedelta(milliseconds=123), "the sub-second part is kept, never truncated"


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("2024-11-03T01:30:00", "ambiguous"),  # the repeated hour when DST ends
        ("2024-03-10T02:30:00", "non-existent"),  # the skipped hour when DST starts
    ],
)
def test_a_daylight_saving_transition_hour_is_rejected(raw: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_acceptance_datetime(raw)


@pytest.mark.parametrize(
    "raw",
    ["", "2024-01-16", "20240116163022", "2024-01-16 16:30:22", "2024-01-16T16:30:22+01:00", "x"],
)
def test_an_unrecognised_format_is_rejected_not_guessed(raw: str) -> None:
    with pytest.raises(ValueError, match="not a recognised"):
        parse_acceptance_datetime(raw)


def test_an_impossible_calendar_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="not a valid calendar time"):
        parse_acceptance_datetime("2024-02-30T10:00:00")


# ── plausibility is reported, never enforced ────────────────────────────────


def _anomalies(raw: str, filing_date: date) -> tuple[str, ...]:
    return acceptance_anomalies(parse_acceptance_datetime(raw), filing_date)


def test_a_normal_acceptance_has_no_anomalies() -> None:
    assert _anomalies("2024-07-16T16:30:22", date(2024, 7, 16)) == ()
    assert _anomalies("2024-07-12T18:15:00", date(2024, 7, 15)) == ()  # Friday evening -> Monday


def test_an_acceptance_after_the_cutoff_is_a_valid_instant_not_an_error() -> None:
    """The 17:30 cut-off decides the filing DATE; EDGAR still accepts later in its hours."""
    for raw in ("2024-07-16T17:30:01", "2024-07-16T19:45:00", "2024-07-16T21:59:59"):
        parse_acceptance_datetime(raw)  # never raises
    assert _anomalies("2024-07-16T19:45:00", date(2024, 7, 17)) == ()


def test_the_cutoff_boundary_is_after_1730_not_at_1730() -> None:
    assert _anomalies("2024-07-16T17:30:00", date(2024, 7, 16)) == ()  # "after" 5:30 only
    assert _anomalies("2024-07-16T17:30:01", date(2024, 7, 17)) == ()


def test_a_same_day_date_after_the_cutoff_is_flagged_but_still_a_fact() -> None:
    notes = _anomalies("2024-07-16T17:45:00", date(2024, 7, 16))

    assert notes and "after 17:30" in notes[0]
    parse_acceptance_datetime("2024-07-16T17:45:00")  # the instant itself is not in question


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
            "2024-05-03T16:30:22.000Z",
            "2024-05-02T16:31:00.000Z",
            "2024-05-01T18:00:00.000Z",
            "2024-12-02T09:15:00.000Z",
            "2024-11-01T06:01:36.000Z",
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
            "2024-05-03T16:30:22.000Z",
            "",
            "2024-05-01T18:00:00.000Z",
            "2024-12-02T09:15:00.000Z",
            "2024-11-01T06:01:36.000Z",
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
            "2024-05-03T16:30:22.000Z",
            "2024-02-30T10:00:00.000Z",
            "2024-05-01T18:00:00.000Z",
            "2024-12-02T09:15:00.000Z",
            "2024-11-01T06:01:36.000Z",
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
    for raw in ("9999-12-31T23:00:00.000Z", "0001-01-01T00:00:00Z", "1992-12-31T10:00:00"):
        with pytest.raises(ValueError, match=r"predates EDGAR|out of range"):
            parse_acceptance_datetime(raw)


def test_an_acceptance_implausibly_earlier_than_the_filing_date_is_rejected() -> None:
    document = _document(
        acceptanceDateTime=[
            "2019-01-01T10:00:00.000Z",
            "2024-05-02T16:31:00.000Z",
            "2024-05-01T18:00:00.000Z",
            "2024-12-02T09:15:00.000Z",
            "2024-11-01T06:01:36.000Z",
        ]
    )

    filings, rejected = map_recent_filings(document, cik=_CIK)

    assert len(rejected) == 1 and "implausibly early" in rejected[0]
    assert len(filings) == 3


def test_a_late_acceptance_after_the_cutoff_is_never_rejected() -> None:
    """Valid after-17:30 acceptances (dated the next business day) all map."""
    document = _document(
        acceptanceDateTime=[
            "2024-05-02T17:31:05.000Z",
            "2024-05-01T21:59:59.000Z",
            "2024-04-30T19:00:00.000Z",
            "2024-11-29T20:00:00.000Z",
            "2024-10-31T18:30:00.000Z",
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
        acceptanceDateTime=["2024-05-04T03:00:00.000Z"] * n,  # a Saturday, 03:00 Eastern
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
