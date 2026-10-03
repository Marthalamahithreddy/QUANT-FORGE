"""Tiny FSDS-shaped packages for tests: real file layout, fictional filers, no network.

The layout mirrors the SEC's "Financial Statement Data Sets" documentation: a ZIP of
tab-delimited UTF-8 ``sub.txt`` / ``num.txt`` / ``tag.txt`` / ``pre.txt`` whose first line names
lowercase columns. Companies, accessions and amounts are invented; only the *format* and the
documented semantics (``qtrs``, month-end-rounded ``ddate``, ``version`` = accession for an
extension, ``segments`` after the December 2024 reprocessing) are the SEC's.
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path

SUB_COLUMNS = [
    "adsh",
    "cik",
    "name",
    "sic",
    "countryba",
    "stprba",
    "cityba",
    "zipba",
    "bas1",
    "bas2",
    "baph",
    "countryma",
    "stprma",
    "cityma",
    "zipma",
    "mas1",
    "mas2",
    "countryinc",
    "stprinc",
    "ein",
    "former",
    "changed",
    "afs",
    "wksi",
    "fye",
    "form",
    "period",
    "fy",
    "fp",
    "filed",
    "accepted",
    "prevrpt",
    "detail",
    "instance",
    "nciks",
    "aciks",
]
NUM_COLUMNS_NEW = [
    "adsh",
    "tag",
    "version",
    "ddate",
    "qtrs",
    "uom",
    "segments",
    "coreg",
    "value",
    "footnote",
]
NUM_COLUMNS_LEGACY = [
    "adsh",
    "tag",
    "version",
    "coreg",
    "ddate",
    "qtrs",
    "uom",
    "value",
    "footnote",
]

# A 10-Q (fiscal Q2 of a September-year-end filer; its 52/53-week quarter ended Mar 30, which
# FSDS rounds to the month end 20240331), a 10-K, and a 10-Q/A amending the 10-Q.
ACC_10Q = "0000320193-24-000010"
ACC_10K = "0000320193-23-000106"
ACC_10QA = "0000320193-24-000020"
ACC_20F = "0000999999-24-000001"  # a form whose facts are out of scope
ACC_OTHER = "0000555555-24-000007"  # another registrant, not stored in tests
CIK_MAIN = "0000320193"
CIK_OTHER = "0000555555"
EXT_VERSION_10Q = ACC_10Q  # an extension tag's version is the accession that defined it


def sub_row(**fields: str) -> dict[str, str]:
    base = dict.fromkeys(SUB_COLUMNS, "")
    base.update(
        {
            "name": "EXAMPLE FILER INC",
            "sic": "3571",
            "countryba": "US",
            "stprba": "CA",
            "cityba": "SPRINGFIELD",
            "countryinc": "US",
            "afs": "1-LAF",
            "wksi": "1",
            "fye": "0930",
            "detail": "1",
            "instance": "exfi-20240330.htm",
            "nciks": "1",
            "prevrpt": "0",
            "fy": "2024",
            "fp": "Q2",
        }
    )
    base.update(fields)
    return base


def num_row(**fields: str) -> dict[str, str]:
    base = dict.fromkeys(NUM_COLUMNS_NEW, "")
    base["uom"] = "USD"
    base.update(fields)
    return base


def default_subs() -> list[dict[str, str]]:
    return [
        sub_row(
            adsh=ACC_10Q,
            cik="320193",  # unpadded, as the SEC states it
            form="10-Q",
            period="20240331",
            filed="20240503",
            accepted="2024-05-03 16:30:22.0",
        ),
        sub_row(
            adsh=ACC_10K,
            cik="320193",
            form="10-K",
            period="20230930",
            fy="2023",
            fp="FY",
            filed="20231103",
            accepted="2023-11-03 18:08:27.0",
            instance="exfi-20230930.htm",
        ),
        sub_row(
            adsh=ACC_10QA,
            cik="320193",
            form="10-Q/A",
            period="20240331",
            filed="20240517",
            accepted="2024-05-17 09:12:40.0",
        ),
        sub_row(
            adsh=ACC_20F,
            cik="999999",
            form="20-F",
            period="20231231",
            fy="2023",
            fp="FY",
            filed="20240301",
            accepted="2024-03-01 07:00:00.0",
        ),
        sub_row(
            adsh=ACC_OTHER,
            cik="555555",
            form="10-Q",
            period="20240630",
            fp="Q2",
            filed="20240802",
            accepted="2024-08-02 16:01:02.0",
        ),
    ]


def default_nums() -> list[dict[str, str]]:
    eps = "EarningsPerShareDiluted"
    gaap = "us-gaap/2024"
    return [
        # -- the 10-Q: fiscal Q2 quarter (qtrs=1) AND year-to-date (qtrs=2), same concept --
        num_row(
            adsh=ACC_10Q,
            tag=eps,
            version=gaap,
            ddate="20240331",
            qtrs="1",
            uom="USD",
            value="1.5300",
        ),
        num_row(
            adsh=ACC_10Q,
            tag=eps,
            version=gaap,
            ddate="20240331",
            qtrs="2",
            uom="USD",
            value="3.9100",
        ),
        # prior-year comparative columns appear as their own facts
        num_row(
            adsh=ACC_10Q,
            tag=eps,
            version=gaap,
            ddate="20230331",
            qtrs="1",
            uom="USD",
            value="1.5200",
        ),
        # negative, and a high-precision magnitude
        num_row(
            adsh=ACC_10Q,
            tag="NetIncomeLoss",
            version=gaap,
            ddate="20240331",
            qtrs="1",
            value="-1234567.0000",
        ),
        num_row(
            adsh=ACC_10Q,
            tag="Assets",
            version=gaap,
            ddate="20240331",
            qtrs="0",
            value="12345678901234567890.1234",
        ),
        # shares and a pure ratio: units are not all USD
        num_row(
            adsh=ACC_10Q,
            tag="WeightedAverageNumberOfDilutedSharesOutstanding",
            version=gaap,
            ddate="20240331",
            qtrs="1",
            uom="shares",
            value="15500000000.0000",
        ),
        num_row(
            adsh=ACC_10Q,
            tag="EffectiveIncomeTaxRateContinuingOperations",
            version=gaap,
            ddate="20240331",
            qtrs="1",
            uom="pure",
            value="0.1600",
        ),
        # a dimensional fact and a co-registrant fact (same concept/period as a plain one)
        num_row(
            adsh=ACC_10Q,
            tag="Revenues",
            version=gaap,
            ddate="20240331",
            qtrs="1",
            value="90000000000.0000",
        ),
        num_row(
            adsh=ACC_10Q,
            tag="Revenues",
            version=gaap,
            ddate="20240331",
            qtrs="1",
            segments="ProductOrServiceAxis=IPhoneMember;",
            value="45000000000.0000",
        ),
        num_row(
            adsh=ACC_10Q,
            tag="Revenues",
            version=gaap,
            ddate="20240331",
            qtrs="1",
            coreg="GuarantorSubsidiaryMember",
            value="1000.0000",
        ),
        # an issuer extension: its version IS the accession that defined it
        num_row(
            adsh=ACC_10Q,
            tag="ServicesRevenueAdjusted",
            version=EXT_VERSION_10Q,
            ddate="20240331",
            qtrs="1",
            value="23000000000.0000",
        ),
        # same local name as the standard concept but a different taxonomy release
        num_row(
            adsh=ACC_10Q,
            tag=eps,
            version="us-gaap/2023",
            ddate="20240331",
            qtrs="1",
            uom="USD",
            value="1.5300",
        ),
        # -- the 10-K: an annual fact (qtrs=4) --
        num_row(
            adsh=ACC_10K,
            tag=eps,
            version="us-gaap/2023",
            ddate="20230930",
            qtrs="4",
            uom="USD",
            value="6.1300",
        ),
        # -- the amendment: same concept and period as the original, a different value --
        num_row(
            adsh=ACC_10QA,
            tag=eps,
            version=gaap,
            ddate="20240331",
            qtrs="1",
            uom="USD",
            value="1.5400",
        ),
        # -- out of scope form, and an unselected registrant --
        num_row(
            adsh=ACC_20F,
            tag="Assets",
            version="ifrs-full/2023",
            ddate="20231231",
            qtrs="0",
            value="5.0000",
        ),
        num_row(
            adsh=ACC_OTHER, tag="Assets", version=gaap, ddate="20240630", qtrs="0", value="7.0000"
        ),
    ]


def _render(columns: Iterable[str], rows: Iterable[Mapping[str, str]]) -> bytes:
    cols = list(columns)
    lines = ["\t".join(cols)]
    lines.extend("\t".join(row.get(col, "") for col in cols) for row in rows)
    return ("\n".join(lines) + "\n").encode("utf-8")


def build_package(
    path: Path,
    *,
    subs: list[dict[str, str]] | None = None,
    nums: list[dict[str, str]] | None = None,
    with_segments: bool = True,
    extra_members: Mapping[str, bytes] | None = None,
    omit: Iterable[str] = (),
) -> Path:
    """Write an FSDS-shaped ZIP at ``path`` and return it."""
    sub_rows = default_subs() if subs is None else subs
    num_rows = default_nums() if nums is None else nums
    num_columns = NUM_COLUMNS_NEW if with_segments else NUM_COLUMNS_LEGACY
    members = {
        "sub.txt": _render(SUB_COLUMNS, sub_rows),
        "num.txt": _render(num_columns, num_rows),
        "tag.txt": _render(
            ["tag", "version", "custom", "abstract", "datatype", "iord", "crdr", "tlabel", "doc"],
            [],
        ),
        "pre.txt": _render(
            [
                "adsh",
                "report",
                "line",
                "stmt",
                "inpth",
                "rfile",
                "tag",
                "version",
                "plabel",
                "negating",
            ],
            [],
        ),
        "readme.htm": b"<html></html>",
    }
    members.update(extra_members or {})
    skip = set(omit)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            if name not in skip:
                archive.writestr(name, payload)
    return path
