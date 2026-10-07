"""Real SEC evidence for the submissions-JSON ``acceptanceDateTime`` question (step 11.2G).

``sec_acceptance_evidence.json`` holds the 164 supported-form rows of the real NKE submissions
response (fetched 2026-10-07) and nine paired comparisons against the official EDGAR header
``<ACCEPTANCE-DATETIME>`` (Eastern wall clock). It is observed evidence, not a semantic claim.

These tests pin two facts, neither of which depends on a hypothesis about SEC internals:

* the adapter maps the real NKE response (identity only; it used to refuse it), and
* in the sampled pairs the literal-``Z`` instant differs from the header instant by exactly zero
  or exactly one Eastern UTC offset for that date, never anything else.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from quantforge.providers.sec.mapper import map_recent_filings

_EVIDENCE = json.loads((Path(__file__).parent / "sec_acceptance_evidence.json").read_text())
_EASTERN = ZoneInfo("America/New_York")
_PAIRS = _EVIDENCE["header_pairs"]


def test_the_real_nke_response_is_no_longer_refused() -> None:
    """Superseded 11.2G regression: the adapter used to refuse this response as a misreading."""
    filings, rejected = map_recent_filings(_EVIDENCE, cik=_EVIDENCE["cik"])
    assert (len(filings), rejected) == (164, [])


def test_the_fixture_is_the_full_supported_form_response() -> None:
    assert len(_EVIDENCE["filings"]["recent"]["accessionNumber"]) == 164


@pytest.mark.parametrize("pair", _PAIRS, ids=[pair["accession"] for pair in _PAIRS])
def test_json_literal_z_differs_from_the_header_by_zero_or_one_eastern_offset(
    pair: dict[str, Any],
) -> None:
    header = datetime.strptime(pair["header_raw"], "%Y%m%d%H%M%S").replace(tzinfo=_EASTERN)
    literal = datetime.strptime(pair["json_raw"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    offset = int(-header.utcoffset().total_seconds())  # type: ignore[union-attr]
    diff = int((literal - header.astimezone(UTC)).total_seconds())
    assert diff in (0, offset)
    assert diff == pair["diff_seconds"]
    assert offset == pair["eastern_offset_seconds"]


def test_the_sample_contains_both_behaviours_and_both_offsets() -> None:
    diffs = {pair["diff_seconds"] for pair in _PAIRS}
    assert diffs == {0, 14400, 18000}
