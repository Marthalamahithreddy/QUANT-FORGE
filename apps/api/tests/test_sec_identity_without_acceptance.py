"""Bulk SEC submissions give filing IDENTITY only; ``accepted_at`` is never read from the JSON.

Step 11.2H. The submissions-JSON ``acceptanceDateTime`` has no documented timezone and was observed
to differ from the official EDGAR header by zero or one Eastern offset, and to change over time for
the same accession (``docs/sec-acceptance-time-design.md``). So the mapper must not turn it into an
instant, whether or not the value happens to look right, and an untrusted acceptance value must not
block storing a valid identity.
"""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from quantforge.models.enums import SecFormType
from quantforge.providers.errors import ProviderPayloadError
from quantforge.providers.sec.history import merge_filings
from quantforge.providers.sec.mapper import map_history_filings, map_recent_filings

_EVIDENCE = json.loads((Path(__file__).parent / "sec_acceptance_evidence.json").read_text())
_TARGET = "0000320187-26-000037"
_PAIRS = {pair["accession"]: pair for pair in _EVIDENCE["header_pairs"]}


def _document(*rows: tuple[str, str, str, Any], cik: str = "0000320193") -> dict[str, Any]:
    """A submissions parent document; each row is (accession, form, filing date, acceptance)."""
    return {
        "cik": cik.lstrip("0"),
        "filings": {
            "recent": {
                "accessionNumber": [r[0] for r in rows],
                "form": [r[1] for r in rows],
                "filingDate": [r[2] for r in rows],
                "acceptanceDateTime": [r[3] for r in rows],
                "reportDate": ["" for _ in rows],
                "primaryDocument": ["doc.htm" for _ in rows],
                "items": ["" for _ in rows],
            }
        },
    }


def test_the_real_nke_response_is_accepted_as_identity_with_no_acceptance_instant() -> None:
    filings, rejected = map_recent_filings(_EVIDENCE, cik=_EVIDENCE["cik"])

    assert rejected == []
    assert len(filings) == 164
    assert all(filing.accepted_at is None for filing in filings)
    target = next(f for f in filings if f.accession_number == _TARGET)
    assert (target.form, target.filing_date) == (SecFormType.FORM_10Q, date(2026, 4, 1))


@pytest.mark.parametrize("accession", ["0000320193-26-000006", "0000320193-26-000013"])
def test_the_aapl_accessions_whose_json_changed_map_to_the_same_identity(accession: str) -> None:
    """The SEC returned a different JSON instant for these accessions three days apart."""
    pair = _PAIRS[accession]
    earlier = {
        "0000320193-26-000006": "2026-01-30T11:01:32.000Z",
        "0000320193-26-000013": "2026-05-01T10:01:00.000Z",
    }[accession]
    first, _ = map_recent_filings(
        _document((accession, "10-Q", pair["filing_date"], earlier)), cik="0000320193"
    )
    second, _ = map_recent_filings(
        _document((accession, "10-Q", pair["filing_date"], pair["json_raw"])), cik="0000320193"
    )

    assert first == second
    assert first[0].accepted_at is None
    assert earlier != pair["json_raw"]


@pytest.mark.parametrize(
    "accession", ["0001193125-26-027207", "0001045810-26-000021", "0001193125-26-191507"]
)
def test_a_json_value_that_happens_to_equal_the_header_is_still_not_promoted(
    accession: str,
) -> None:
    pair = _PAIRS[accession]
    assert pair["diff_seconds"] == 0
    filings, _ = map_recent_filings(
        _document((accession, pair["form"], pair["filing_date"], pair["json_raw"])),
        cik="0000320193",
    )
    assert filings[0].accepted_at is None


@pytest.mark.parametrize(
    "acceptance",
    [
        "1999-05-03T04:00:00.000Z",  # a date-only placeholder (midnight Eastern)
        "not a timestamp",
        "",
        None,
        "2026-04-02T00:16:39",  # no Z
        "2026-04-02T00:16:39+08:00",
    ],
)
def test_an_unusable_acceptance_value_does_not_block_storing_the_identity(
    acceptance: str | None,
) -> None:
    filings, rejected = map_recent_filings(
        _document(("0000320193-26-000006", "10-Q", "2026-01-30", acceptance)), cik="0000320193"
    )
    assert rejected == []
    assert [f.accession_number for f in filings] == ["0000320193-26-000006"]
    assert filings[0].accepted_at is None


def test_an_acceptance_value_of_the_wrong_type_is_a_malformed_row() -> None:
    filings, rejected = map_recent_filings(
        _document(
            ("0000320193-26-000006", "10-Q", "2026-01-30", 12345),
            ("0000320193-26-000007", "10-Q", "2026-01-31", None),
        ),
        cik="0000320193",
    )
    assert [f.accession_number for f in filings] == ["0000320193-26-000007"]
    assert len(rejected) == 1
    assert "0000320193-26-000006" in rejected[0]


def test_an_arbitrarily_long_acceptance_text_does_not_block_the_identity() -> None:
    filings, rejected = map_recent_filings(
        _document(("0000320193-26-000006", "10-Q", "2026-01-30", "x" * 500)), cik="0000320193"
    )
    assert rejected == [] and len(filings) == 1


def test_the_acceptance_array_is_not_required() -> None:
    document = _document(("0000320193-26-000006", "10-Q", "2026-01-30", None))
    del document["filings"]["recent"]["acceptanceDateTime"]
    filings, rejected = map_recent_filings(document, cik="0000320193")
    assert rejected == []
    assert len(filings) == 1


def test_a_response_whose_every_timestamp_looks_implausible_is_not_refused() -> None:
    """Many weekend / out-of-hours instants used to trip the whole-response tripwire."""
    rows = tuple(
        (f"0000320193-26-{n:06d}", "8-K", "2026-01-30", "2026-01-31T03:00:00.000Z")
        for n in range(1, 21)
    )
    filings, rejected = map_recent_filings(_document(*rows), cik="0000320193")
    assert len(filings) == 20
    assert rejected == []


@pytest.mark.parametrize(
    ("accession", "form", "filing_date"),
    [
        ("not-an-accession", "10-Q", "2026-01-30"),
        ("0000320193-26-000006", "10-Q", "not-a-date"),
        ("0000320193-26-000006", "10-Q", "1980-01-01"),
    ],
)
def test_an_invalid_identity_field_still_refuses_the_row(
    accession: str, form: str, filing_date: str
) -> None:
    filings, rejected = map_recent_filings(
        _document(
            (accession, form, filing_date, "2026-01-30T16:01:32.000Z"),
            ("0000320193-26-000099", "10-Q", "2026-02-01", None),
        ),
        cik="0000320193",
    )
    assert [f.accession_number for f in filings] == ["0000320193-26-000099"]
    assert len(rejected) == 1


@pytest.mark.parametrize("missing", ["accessionNumber", "form", "filingDate"])
def test_a_missing_identity_array_refuses_the_whole_response(missing: str) -> None:
    document = _document(("0000320193-26-000006", "10-Q", "2026-01-30", None))
    del document["filings"]["recent"][missing]
    with pytest.raises(ProviderPayloadError):
        map_recent_filings(document, cik="0000320193")


def test_filings_are_in_deterministic_filing_date_then_accession_order() -> None:
    filings, _ = map_recent_filings(
        _document(
            ("0000320193-26-000020", "8-K", "2026-02-02", None),
            ("0000320193-26-000011", "8-K", "2026-02-02", None),
            ("0000320193-26-000005", "10-Q", "2026-01-30", None),
        ),
        cik="0000320193",
    )
    assert [f.accession_number for f in filings] == [
        "0000320193-26-000005",
        "0000320193-26-000011",
        "0000320193-26-000020",
    ]


def test_the_history_shape_has_the_same_identity_semantics() -> None:
    recent = _document(("0000320193-26-000006", "10-Q", "2026-01-30", "2026-01-30T16:01:32.000Z"))
    history = copy.deepcopy(recent["filings"]["recent"])
    history["acceptanceDateTime"] = ["2026-01-30T11:01:32.000Z"]  # a different JSON representation
    from_recent, _ = map_recent_filings(recent, cik="0000320193")
    from_history, rejected, rows = map_history_filings(history, cik="0000320193")

    assert (rejected, rows) == ([], 1)
    assert from_history == from_recent
    assert from_history[0].accepted_at is None
    merged, overlap = merge_filings([("recent", from_recent), ("history", from_history)])
    assert overlap == 1
    assert [f.accession_number for f in merged] == ["0000320193-26-000006"]
