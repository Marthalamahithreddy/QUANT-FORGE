"""The FSDS adapter: streaming, selection, strict validation, exact Decimal, qtrs/period preserved."""

from __future__ import annotations

import hashlib
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from quantforge.ingestion.canonical import CanonicalFactSubmission, CanonicalFinancialFact
from quantforge.models.enums import SecFormType
from quantforge.providers.sec.fsds import (
    FsdsNumScan,
    FsdsPackage,
    FsdsPackageError,
    FsdsSelection,
)
from tests.fsds_fixture import (
    ACC_10K,
    ACC_10Q,
    ACC_10QA,
    ACC_20F,
    ACC_OTHER,
    CIK_MAIN,
    NUM_COLUMNS_NEW,
    SUB_COLUMNS,
    build_package,
    default_nums,
    default_subs,
    num_row,
    sub_row,
)

ALL_THREE = frozenset({ACC_10Q, ACC_10K, ACC_10QA})


def _facts(
    path: Path, accessions: frozenset[str] = ALL_THREE
) -> tuple[list[CanonicalFinancialFact], FsdsNumScan]:
    scan = FsdsNumScan()
    with FsdsPackage(path) as package:
        return list(package.iter_facts(accessions, scan)), scan


def _by_key(
    facts: list[CanonicalFinancialFact],
) -> dict[tuple[str, str, str, int, str, str], CanonicalFinancialFact]:
    return {
        (
            f.accession_number,
            f.tag,
            f.taxonomy_version,
            f.duration_quarters,
            f.unit,
            f.segments + "|" + f.coreg,
        ): f
        for f in facts
    }


# ── SUB ─────────────────────────────────────────────────────────────────────────────────


def test_a_valid_sub_row_is_mapped_with_source_fields_verbatim(tmp_path: Path) -> None:
    path = build_package(tmp_path / "2024q2.zip")
    with FsdsPackage(path) as package:
        scan = package.scan_submissions(FsdsSelection(accessions=frozenset({ACC_10Q})))
    submission = scan.submissions[ACC_10Q]
    assert scan.rejected_count == 0
    assert submission.cik == CIK_MAIN  # the unpadded source CIK is normalised to 10 digits
    assert submission.form is SecFormType.FORM_10Q
    assert submission.filing_date == date(2024, 5, 3)
    # The balance-sheet date is the source's month-end-rounded value, not the 52/53-week date.
    assert submission.period_end == date(2024, 3, 31)
    assert (submission.fiscal_year, submission.fiscal_period, submission.fiscal_year_end) == (
        2024,
        "Q2",
        "0930",
    )
    # The acceptance string is carried verbatim as TEXT: it is never turned into an instant.
    assert submission.source_stated_accepted == "2024-05-03 16:30:22"
    assert submission.coregistrant_count == 1
    assert submission.additional_ciks is None


def test_selection_by_cik_and_accession_and_unselected_rows_are_not_even_validated(
    tmp_path: Path,
) -> None:
    subs = default_subs()
    # A row of an unselected registrant whose fields are garbage: it must not matter.
    subs.append(sub_row(adsh="0000777777-24-000001", cik="777777", form="10-Q", period="garbage"))
    path = build_package(tmp_path / "2024q2.zip", subs=subs)
    with FsdsPackage(path) as package:
        by_cik = package.scan_submissions(FsdsSelection(ciks=frozenset({CIK_MAIN})))
    assert set(by_cik.submissions) == ALL_THREE
    assert by_cik.rejected_count == 0
    assert by_cik.out_of_scope == {}
    with FsdsPackage(path) as package:
        by_accession = package.scan_submissions(FsdsSelection(accessions=frozenset({ACC_OTHER})))
    assert set(by_accession.submissions) == {ACC_OTHER}


def test_forms_outside_the_supported_set_are_reported_not_read(tmp_path: Path) -> None:
    path = build_package(tmp_path / "2024q2.zip")
    with FsdsPackage(path) as package:
        scan = package.scan_submissions(FsdsSelection(accessions=frozenset({ACC_20F})))
    assert scan.submissions == {}
    assert scan.out_of_scope == {ACC_20F: "20-F"}


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"period": "20240230"}, "valid calendar date"),
        ({"period": "2024-03-31"}, "yyyymmdd"),
        ({"filed": "20241301"}, "valid calendar date"),
        ({"accepted": "2024-05-03T16:30:22"}, "yyyy-mm-dd hh:mm:ss"),
        ({"accepted": "2024-13-03 16:30:22"}, "yyyy-mm-dd hh:mm:ss"),
        ({"fy": "FY24"}, "not a year"),
        ({"nciks": "x"}, "not a count"),
        ({"nciks": "0"}, "coregistrant_count"),
    ],
)
def test_invalid_sub_rows_are_rejected_with_a_reason(
    tmp_path: Path, overrides: dict[str, str], fragment: str
) -> None:
    subs = [
        sub_row(
            adsh=ACC_10Q,
            cik="320193",
            form="10-Q",
            period="20240331",
            filed="20240503",
            accepted="2024-05-03 16:30:22",
        )
    ]
    subs[0].update(overrides)
    path = build_package(tmp_path / "2024q2.zip", subs=subs)
    with FsdsPackage(path) as package:
        scan = package.scan_submissions(FsdsSelection(accessions=frozenset({ACC_10Q})))
    assert scan.rejected_count == 1
    assert fragment in scan.rejected_samples[0]
    assert ACC_10Q in scan.rejected_samples[0]
    assert scan.submissions == {}


def test_a_duplicate_accession_in_sub_is_rejected(tmp_path: Path) -> None:
    subs = default_subs()
    subs.append(dict(subs[0]))
    path = build_package(tmp_path / "2024q2.zip", subs=subs)
    with FsdsPackage(path) as package:
        scan = package.scan_submissions(FsdsSelection(accessions=frozenset({ACC_10Q})))
    assert scan.rejected_count == 1
    assert "duplicate adsh" in scan.rejected_samples[0]


def test_accessions_are_taken_in_the_dashed_form_only(tmp_path: Path) -> None:
    subs = [
        sub_row(
            adsh="000032019324000010",
            cik="320193",
            form="10-Q",
            period="20240331",
            filed="20240503",
            accepted="2024-05-03 16:30:22",
        )
    ]
    path = build_package(tmp_path / "2024q2.zip", subs=subs)
    with FsdsPackage(path) as package:
        scan = package.scan_submissions(FsdsSelection(ciks=frozenset({CIK_MAIN})))
    # An undashed value is not an accession: it is not guessed into one.
    assert scan.submissions == {}


def test_the_previously_amended_flag_is_never_read(tmp_path: Path) -> None:
    """``prevrpt`` encodes knowledge from AFTER the filing; no column or field carries it."""

    fields = set(CanonicalFactSubmission.__slots__)
    assert not {name for name in fields if "prev" in name or "amend" in name}


# ── NUM ─────────────────────────────────────────────────────────────────────────────────


def test_every_selected_num_row_becomes_one_fact_and_only_selected_accessions(
    tmp_path: Path,
) -> None:
    path = build_package(tmp_path / "2024q2.zip")
    facts, scan = _facts(path)
    assert scan.rejected_count == 0
    assert scan.rows_read == len(facts) == 14  # 12 + 1 + 1: 10-Q, 10-K and the amendment
    assert {f.accession_number for f in facts} == ALL_THREE


def test_values_are_exact_decimals_including_negative_and_high_precision(tmp_path: Path) -> None:
    facts, _ = _facts(build_package(tmp_path / "2024q2.zip"))
    by = _by_key(facts)
    loss = by[(ACC_10Q, "NetIncomeLoss", "us-gaap/2024", 1, "USD", "|")]
    assert loss.value == Decimal("-1234567.0000")
    assets = by[(ACC_10Q, "Assets", "us-gaap/2024", 0, "USD", "|")]
    assert assets.value == Decimal("12345678901234567890.1234")
    assert isinstance(assets.value, Decimal)
    # Not a float round trip: a float cannot represent this value.
    assert str(assets.value) == "12345678901234567890.1234"


def test_quarter_and_year_to_date_of_one_concept_stay_two_facts(tmp_path: Path) -> None:
    """The Q2/Q3 tripwire at the parser: qtrs=1 and qtrs=2 are never collapsed."""
    facts, _ = _facts(build_package(tmp_path / "2024q2.zip"))
    eps = [
        f
        for f in facts
        if f.accession_number == ACC_10Q
        and f.tag == "EarningsPerShareDiluted"
        and f.taxonomy_version == "us-gaap/2024"
        and f.period_end == date(2024, 3, 31)
    ]
    assert sorted((f.duration_quarters, f.value) for f in eps) == [
        (1, Decimal("1.5300")),
        (2, Decimal("3.9100")),
    ]


def test_instant_quarter_ytd_and_annual_durations_are_preserved_verbatim(tmp_path: Path) -> None:
    facts, _ = _facts(build_package(tmp_path / "2024q2.zip"))
    assert {f.duration_quarters for f in facts} == {0, 1, 2, 4}
    assert {f.duration_quarters for f in facts if f.tag == "Assets"} == {0}


def test_period_end_is_the_sources_month_end_never_a_calendar_quarter(tmp_path: Path) -> None:
    facts, _ = _facts(build_package(tmp_path / "2024q2.zip"))
    # A September-year-end filer's fiscal Q2 ends 2024-03-31: the same date as a calendar Q1.
    # Nothing here labels it either way; the date is carried exactly.
    ten_q = [
        f for f in facts if f.accession_number == ACC_10Q and f.period_end == date(2024, 3, 31)
    ]
    assert ten_q
    assert not hasattr(ten_q[0], "fiscal_quarter")
    assert not hasattr(ten_q[0], "calendar_quarter")


def test_units_are_preserved_and_never_converted(tmp_path: Path) -> None:
    facts, _ = _facts(build_package(tmp_path / "2024q2.zip"))
    assert {f.unit for f in facts} == {"USD", "USD/shares", "shares", "pure"}


def test_custom_concepts_are_kept_under_their_defining_accession(tmp_path: Path) -> None:
    facts, _ = _facts(build_package(tmp_path / "2024q2.zip"))
    extension = [f for f in facts if f.tag == "ServicesRevenueAdjusted"]
    assert len(extension) == 1
    assert extension[0].taxonomy_version == ACC_10Q  # the accession that defined the tag
    assert extension[0].value == Decimal("23000000000.0000")


def test_same_tag_in_different_taxonomy_releases_stays_distinct(tmp_path: Path) -> None:
    facts, _ = _facts(build_package(tmp_path / "2024q2.zip"))
    versions = {
        f.taxonomy_version
        for f in facts
        if f.accession_number == ACC_10Q
        and f.tag == "EarningsPerShareDiluted"
        and f.duration_quarters == 1
        and f.period_end == date(2024, 3, 31)
    }
    assert versions == {"us-gaap/2024", "us-gaap/2023"}


def test_dimensions_and_coregistrant_are_carried_verbatim_never_labelled(tmp_path: Path) -> None:
    facts, _ = _facts(build_package(tmp_path / "2024q2.zip"))
    revenues = {
        (f.segments, f.coreg): f.value
        for f in facts
        if f.accession_number == ACC_10Q and f.tag == "Revenues"
    }
    assert revenues == {
        ("", ""): Decimal("90000000000.0000"),
        ("ProductOrServiceAxis=IPhoneMember;", ""): Decimal("45000000000.0000"),
        ("", "GuarantorSubsidiaryMember"): Decimal("1000.0000"),
    }
    assert not any(hasattr(f, "consolidated") for f in facts)


def test_a_legacy_layout_without_segments_is_read_with_no_segment_claim(tmp_path: Path) -> None:
    path = build_package(tmp_path / "2023q4.zip", with_segments=False)
    facts, scan = _facts(path)
    assert scan.rejected_count == 0
    assert all(f.segments == "" for f in facts)
    with FsdsPackage(path) as package:
        assert package.read_num_layout() is False
    with FsdsPackage(build_package(tmp_path / "2025q1.zip")) as package:
        assert package.read_num_layout() is True


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"value": "1.23456"}, "does not fit"),
        ({"value": "NaN"}, "plain finite decimal"),
        ({"value": "Infinity"}, "plain finite decimal"),
        ({"value": "1e5"}, "plain finite decimal"),
        ({"value": "1_000"}, "plain finite decimal"),
        ({"value": "12,345"}, "plain finite decimal"),
        ({"value": "123456789012345678901234567.0"}, "does not fit"),
        ({"qtrs": "-1"}, "qtrs"),
        ({"qtrs": "one"}, "qtrs"),
        ({"qtrs": "1.5"}, "qtrs"),
        ({"ddate": "20240231"}, "valid calendar date"),
        ({"ddate": "2024-03-31"}, "yyyymmdd"),
        ({"uom": ""}, "unit must not be blank"),
        ({"tag": ""}, "tag"),
        ({"tag": "Has Space"}, "tag"),
        ({"version": ""}, "taxonomy_version"),
        ({"segments": " padded "}, "surrounding whitespace"),
        ({"coreg": "x" * 257}, "longer than"),
    ],
)
def test_malformed_selected_num_rows_are_rejected_not_skipped_silently(
    tmp_path: Path, overrides: dict[str, str], fragment: str
) -> None:
    row = num_row(
        adsh=ACC_10Q,
        tag="Assets",
        version="us-gaap/2024",
        ddate="20240331",
        qtrs="0",
        value="1.0000",
    )
    row.update(overrides)
    facts, scan = _facts(build_package(tmp_path / "2024q2.zip", nums=[row]), frozenset({ACC_10Q}))
    assert facts == []
    assert scan.rejected_count == 1
    assert fragment in scan.rejected_samples[0]


def test_an_empty_value_is_none_not_zero(tmp_path: Path) -> None:
    row = num_row(
        adsh=ACC_10Q, tag="Assets", version="us-gaap/2024", ddate="20240331", qtrs="0", value=""
    )
    facts, scan = _facts(build_package(tmp_path / "2024q2.zip", nums=[row]), frozenset({ACC_10Q}))
    assert scan.rejected_count == 0
    assert facts[0].value is None


def test_a_short_selected_row_is_rejected(tmp_path: Path) -> None:
    path = build_package(
        tmp_path / "2024q2.zip",
        extra_members={
            "num.txt": (
                "adsh\ttag\tversion\tddate\tqtrs\tuom\tsegments\tcoreg\tvalue\tfootnote\n"
                f"{ACC_10Q}\tAssets\tus-gaap/2024\n"
            ).encode()
        },
    )
    facts, scan = _facts(path, frozenset({ACC_10Q}))
    assert facts == []
    assert scan.rejected_count == 1
    assert "columns" in scan.rejected_samples[0]


def test_a_bad_row_of_an_unselected_accession_is_ignored(tmp_path: Path) -> None:
    nums = [
        *default_nums(),
        num_row(
            adsh=ACC_OTHER, tag="Assets", version="us-gaap/2024", ddate="bad", qtrs="x", value="NaN"
        ),
    ]
    facts, scan = _facts(build_package(tmp_path / "2024q2.zip", nums=nums))
    assert scan.rejected_count == 0
    assert all(f.accession_number in ALL_THREE for f in facts)


def test_member_digests_cover_the_whole_member_once_it_is_read(tmp_path: Path) -> None:
    path = build_package(tmp_path / "2024q2.zip")
    with zipfile.ZipFile(path) as archive:
        expected_sub = hashlib.sha256(archive.read("sub.txt")).hexdigest()
        expected_num = hashlib.sha256(archive.read("num.txt")).hexdigest()
    with FsdsPackage(path) as package:
        package.scan_submissions(FsdsSelection(accessions=frozenset({ACC_10Q})))
        list(package.iter_facts(frozenset({ACC_10Q}), FsdsNumScan()))
        assert package.member_sha256 == {"sub.txt": expected_sub, "num.txt": expected_num}


# ── package structure ───────────────────────────────────────────────────────────────────


def test_not_a_zip_is_a_package_error(tmp_path: Path) -> None:
    path = tmp_path / "2024q2.zip"
    path.write_bytes(b"this is not a zip file")
    with pytest.raises(FsdsPackageError, match="not a readable ZIP"):
        FsdsPackage(path)


@pytest.mark.parametrize("missing", ["sub.txt", "num.txt"])
def test_a_missing_member_is_a_package_error(tmp_path: Path, missing: str) -> None:
    path = build_package(tmp_path / "2024q2.zip", omit=[missing])
    with pytest.raises(FsdsPackageError, match=missing):
        FsdsPackage(path)


def test_tag_and_pre_are_not_required_or_read(tmp_path: Path) -> None:
    path = build_package(tmp_path / "2024q2.zip", omit=["tag.txt", "pre.txt"])
    facts, scan = _facts(path)
    assert facts
    assert scan.rejected_count == 0


@pytest.mark.parametrize("dropped", ["cik", "form", "accepted", "period", "adsh", "filed"])
def test_a_missing_required_sub_column_is_a_package_error(tmp_path: Path, dropped: str) -> None:
    columns = [c for c in SUB_COLUMNS if c != dropped]
    path = build_package(
        tmp_path / "2024q2.zip", extra_members={"sub.txt": ("\t".join(columns) + "\n").encode()}
    )
    with FsdsPackage(path) as package, pytest.raises(FsdsPackageError, match=dropped):
        package.scan_submissions(FsdsSelection(ciks=frozenset({CIK_MAIN})))


@pytest.mark.parametrize("dropped", ["tag", "version", "ddate", "qtrs", "uom", "value", "coreg"])
def test_a_missing_required_num_column_is_a_package_error(tmp_path: Path, dropped: str) -> None:
    columns = [c for c in NUM_COLUMNS_NEW if c != dropped]
    path = build_package(
        tmp_path / "2024q2.zip", extra_members={"num.txt": ("\t".join(columns) + "\n").encode()}
    )
    with FsdsPackage(path) as package, pytest.raises(FsdsPackageError, match=dropped):
        list(package.iter_facts(ALL_THREE, FsdsNumScan()))


def test_duplicate_header_columns_are_refused(tmp_path: Path) -> None:
    path = build_package(
        tmp_path / "2024q2.zip", extra_members={"num.txt": b"adsh\ttag\ttag\tversion\n"}
    )
    with FsdsPackage(path) as package, pytest.raises(FsdsPackageError, match="duplicate"):
        list(package.iter_facts(ALL_THREE, FsdsNumScan()))


def test_invalid_utf8_is_a_package_error_not_silent_replacement(tmp_path: Path) -> None:
    header = "\t".join(NUM_COLUMNS_NEW).encode()
    path = build_package(
        tmp_path / "2024q2.zip", extra_members={"num.txt": header + b"\n" + b"\xff\xfe\tbad\n"}
    )
    with FsdsPackage(path) as package, pytest.raises(FsdsPackageError, match="UTF-8"):
        list(package.iter_facts(ALL_THREE, FsdsNumScan()))


def test_a_selection_must_name_something() -> None:
    with pytest.raises(ValueError, match="at least one accession or CIK"):
        FsdsSelection()


def test_the_package_is_streamed_not_loaded_whole(tmp_path: Path) -> None:
    """A large NUM file is read line by line: only selected facts are ever materialised."""
    rows = [
        num_row(
            adsh=ACC_OTHER,
            tag="Assets",
            version="us-gaap/2024",
            ddate="20240630",
            qtrs="0",
            value=str(i),
        )
        for i in range(20_000)
    ]
    rows.append(
        num_row(
            adsh=ACC_10Q,
            tag="Assets",
            version="us-gaap/2024",
            ddate="20240331",
            qtrs="0",
            value="1.0000",
        )
    )
    path = build_package(tmp_path / "2024q2.zip", nums=rows)
    facts, scan = _facts(path, frozenset({ACC_10Q}))
    assert len(facts) == 1
    assert scan.rows_read == 1  # the other 20,000 rows were dismissed unparsed


def test_non_ascii_dimension_and_coregistrant_text_is_preserved_verbatim(tmp_path: Path) -> None:
    row = num_row(
        adsh=ACC_10Q,
        tag="Assets",
        version="us-gaap/2024",
        ddate="20240331",
        qtrs="0",
        coreg="Société Générale SA",
        segments="ProductOrServiceAxis=CafeMember;",
        value="1.0000",
    )
    facts, scan = _facts(build_package(tmp_path / "2024q2.zip", nums=[row]), frozenset({ACC_10Q}))
    assert scan.rejected_count == 0
    assert facts[0].coreg == "Société Générale SA"


def test_a_selected_sub_row_missing_required_columns_is_rejected_not_dropped(
    tmp_path: Path,
) -> None:
    full = default_subs()[0]
    truncated = "\t".join(full[c] for c in SUB_COLUMNS[: SUB_COLUMNS.index("period")])
    content = "\t".join(SUB_COLUMNS) + "\n" + truncated + "\n"
    path = build_package(tmp_path / "2024q2.zip", extra_members={"sub.txt": content.encode()})
    with FsdsPackage(path) as package:
        scan = package.scan_submissions(FsdsSelection(accessions=frozenset({ACC_10Q})))
    assert scan.rejected_count == 1
    assert "columns" in scan.rejected_samples[0]
    assert scan.submissions == {}


def test_an_omitted_trailing_empty_aciks_column_is_tolerated(tmp_path: Path) -> None:
    full = default_subs()[0]
    cells = [full[c] for c in SUB_COLUMNS[:-1]]  # the last column (aciks) omitted: it is empty
    content = "\t".join(SUB_COLUMNS) + "\n" + "\t".join(cells) + "\n"
    path = build_package(tmp_path / "2024q2.zip", extra_members={"sub.txt": content.encode()})
    with FsdsPackage(path) as package:
        scan = package.scan_submissions(FsdsSelection(accessions=frozenset({ACC_10Q})))
    assert scan.rejected_count == 0
    assert scan.submissions[ACC_10Q].additional_ciks is None


def test_a_cik_selected_row_with_a_malformed_accession_is_rejected_not_dropped(
    tmp_path: Path,
) -> None:
    subs = [
        sub_row(
            adsh="BAD-ACCESSION",
            cik="320193",
            form="10-Q",
            period="20240331",
            filed="20240503",
            accepted="2024-05-03 16:30:22",
        )
    ]
    path = build_package(tmp_path / "2024q2.zip", subs=subs)
    with FsdsPackage(path) as package:
        scan = package.scan_submissions(FsdsSelection(ciks=frozenset({CIK_MAIN})))
    assert scan.rejected_count == 1
    assert "not a dashed accession" in scan.rejected_samples[0]


def test_a_truncated_row_for_an_explicitly_selected_accession_is_rejected_not_dropped(
    tmp_path: Path,
) -> None:
    content = "\t".join(SUB_COLUMNS) + "\n" + ACC_10Q + "\n"  # just the accession, nothing else
    path = build_package(tmp_path / "2024q2.zip", extra_members={"sub.txt": content.encode()})
    with FsdsPackage(path) as package:
        scan = package.scan_submissions(FsdsSelection(accessions=frozenset({ACC_10Q})))
    assert scan.rejected_count == 1
    assert scan.submissions == {}


def test_an_out_of_scope_form_is_reported_without_validating_the_rest_of_its_row(
    tmp_path: Path,
) -> None:
    subs = [
        sub_row(adsh=ACC_20F, cik="999999", form="20-F", period="garbage", filed="also garbage")
    ]
    path = build_package(tmp_path / "2024q2.zip", subs=subs)
    with FsdsPackage(path) as package:
        scan = package.scan_submissions(FsdsSelection(ciks=frozenset({"0000999999"})))
    assert scan.rejected_count == 0
    assert scan.out_of_scope == {ACC_20F: "20-F"}


def test_free_text_may_contain_a_no_break_space_but_never_a_control_character(
    tmp_path: Path,
) -> None:
    row = num_row(
        adsh=ACC_10Q,
        tag="Assets",
        version="us-gaap/2024",
        ddate="20240331",
        qtrs="0",
        coreg="Parent\u00a0Holdings",
        value="1.0000",
    )
    facts, scan = _facts(build_package(tmp_path / "2024q2.zip", nums=[row]), frozenset({ACC_10Q}))
    assert scan.rejected_count == 0
    assert facts[0].coreg == "Parent\u00a0Holdings"
    with pytest.raises(ValueError, match="control characters"):
        CanonicalFinancialFact(
            accession_number=ACC_10Q,
            tag="Assets",
            taxonomy_version="us-gaap/2024",
            unit="USD",
            period_end=date(2024, 3, 31),
            duration_quarters=0,
            value=None,
            coreg="bad\x07bell",
        )


def test_a_member_corrupted_inside_the_zip_is_a_package_error_not_a_raw_exception(
    tmp_path: Path,
) -> None:
    rows = [
        num_row(
            adsh=ACC_10Q,
            tag=f"Concept{i}",
            version="us-gaap/2024",
            ddate="20240331",
            qtrs="0",
            value=f"{i}.0000",
        )
        for i in range(2000)
    ]
    path = build_package(tmp_path / "2024q2.zip", nums=rows)
    data = bytearray(path.read_bytes())
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo("num.txt")
        start = info.header_offset + 30 + len(info.filename) + 200
    for offset in range(start, start + 64):
        data[offset] ^= 0xFF  # damage the compressed stream of num.txt
    path.write_bytes(bytes(data))
    with FsdsPackage(path) as package, pytest.raises(FsdsPackageError, match="corrupt"):
        list(package.iter_facts(frozenset({ACC_10Q}), FsdsNumScan()))
