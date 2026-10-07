"""Unit tests for bounded historical filing identity: history files, mapping and the client.

No network and no database: a mock transport serves SEC-shaped responses. What is under test
is the source contract (observed, not documented), the explicit bounds, and fail-closed
behaviour -- never availability.
"""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime
from pathlib import Path

import httpx2 as httpx
import pytest

from quantforge.ingestion.canonical import CanonicalSecFiling
from quantforge.models.enums import SecFormType
from quantforge.providers.errors import (
    ProviderNotFoundError,
    ProviderPayloadError,
    ProviderResponseError,
)
from quantforge.providers.filings import FilingHistoryProvider
from quantforge.providers.sec.client import SecClient, SecConfig
from quantforge.providers.sec.history import (
    HistoryFileRef,
    HistoryScopeError,
    accession_year,
    merge_filings,
    parse_history_file_refs,
    select_history_files,
)
from quantforge.providers.sec.mapper import (
    map_history_filings,
    map_recent_filings,
    recent_row_count,
)
from tests.sec_history_fixture import (
    CIK,
    H1_8K,
    H1_10K,
    H1_10Q_A,
    H1_FORM4,
    H2_10K,
    H2_10Q,
    H2_PLACEHOLDER,
    H2_PLACEHOLDER_EDT,
    HISTORY_1,
    HISTORY_2,
    R_10K_A,
    R_10Q,
    R_FORM4,
    R_OVERLAP,
    Recorder,
    Row,
    encode,
    file_entry,
    history,
    parent,
    synthetic_rows,
)

_UA = "QuantForgeTest tester@example.com"
SRC = Path(__file__).resolve().parents[1] / "src" / "quantforge"


async def _no_sleep(_delay: float) -> None:
    return None


def _client(recorder: Recorder) -> SecClient:
    return SecClient(
        SecConfig(user_agent=_UA, base_url="https://data.sec.gov"),
        transport=recorder.transport,
        sleep=_no_sleep,
        now=lambda: datetime(2026, 10, 4, 12, 0, tzinfo=UTC),
    )


def _two_file_routes() -> tuple[Recorder, list[object]]:
    h1 = [H1_10K, H1_10Q_A, H1_8K, H1_FORM4]
    h2 = [H2_10K, H2_10Q, H2_PLACEHOLDER, H2_PLACEHOLDER_EDT]
    doc = parent(
        [R_10Q, R_10K_A, R_FORM4, R_OVERLAP],
        [file_entry(HISTORY_1, h1), file_entry(HISTORY_2, h2)],
    )
    recorder = Recorder(
        {
            f"/submissions/CIK{CIK}.json": encode(doc),
            f"/submissions/{HISTORY_1}": encode(history(h1)),
            f"/submissions/{HISTORY_2}": encode(history(h2)),
        }
    )
    return recorder, [doc]


# ── history-file discovery: names, scope and bounds ──────────────────────────────────────


def test_a_parent_names_its_history_files_with_their_stated_ranges() -> None:
    doc = parent([R_10Q], [file_entry(HISTORY_1, [H1_10K, H1_8K]), file_entry(HISTORY_2, [H2_10K])])

    refs = parse_history_file_refs(doc, cik=CIK)

    assert [r.name for r in refs] == [HISTORY_1, HISTORY_2]
    assert refs[0].filing_count == 2
    assert (refs[0].filing_from, refs[0].filing_to) == (date(2021, 1, 27), date(2022, 10, 28))
    assert refs[1].path == f"/submissions/{HISTORY_2}"


@pytest.mark.parametrize("filings", [{"recent": {}}, {"recent": {}, "files": None}, {"files": []}])
def test_no_history_files_is_an_empty_scope(filings: object) -> None:
    assert parse_history_file_refs({"cik": "320193", "filings": filings}, cik=CIK) == ()


@pytest.mark.parametrize(
    "name",
    [
        "CIK0000999999-submissions-001.json",  # another registrant
        "../CIK0000320193-submissions-001.json",  # traversal
        "/submissions/CIK0000320193-submissions-001.json",
        "https://evil.example/CIK0000320193-submissions-001.json",
        "CIK0000320193-submissions-001.json?x=1",
        "CIK320193-submissions-001.json",  # unpadded
        "CIK0000320193-submissions-1.json",
        "CIK0000320193-submissions-001.JSON",
        "submissions.zip",
        "",
    ],
)
def test_a_file_name_that_is_not_exactly_this_registrants_pattern_is_refused(name: str) -> None:
    doc = {"cik": "320193", "filings": {"files": [{"name": name, "filingCount": 1}]}}

    with pytest.raises(ProviderPayloadError):
        parse_history_file_refs(doc, cik=CIK)


@pytest.mark.parametrize(
    "files",
    [
        "x",
        {"name": HISTORY_1},
        [{"name": 1}],
        [{"filingCount": 3}],
        [{"name": HISTORY_1, "filingCount": -1}],
        [{"name": HISTORY_1, "filingCount": True}],
        [{"name": HISTORY_1, "filingFrom": "last year"}],
        [{"name": HISTORY_1}, {"name": HISTORY_1}],
    ],
)
def test_a_malformed_files_array_is_a_payload_error(files: object) -> None:
    with pytest.raises(ProviderPayloadError):
        parse_history_file_refs({"cik": "320193", "filings": {"files": files}}, cik=CIK)


def test_accession_years_cover_both_centuries() -> None:
    assert accession_year("0000320193-24-000010") == 2024
    assert accession_year("0000320193-96-000023") == 1996
    assert accession_year("0000320193-93-000001") == 1993
    assert accession_year("0000320193-92-000001") == 2092  # EDGAR began in 1993


def _refs() -> tuple[HistoryFileRef, HistoryFileRef, HistoryFileRef]:
    return (
        HistoryFileRef("a", 10, date(2018, 1, 2), date(2023, 12, 29)),
        HistoryFileRef("b", 10, date(2010, 1, 4), date(2017, 12, 29)),
        HistoryFileRef("c", None, None, None),  # no stated range: can never be excluded
    )


def test_untargeted_scope_selects_every_named_file() -> None:
    selected, skipped = select_history_files(_refs(), unresolved=None)

    assert [r.name for r in selected] == ["a", "b", "c"] and skipped == ()


def test_targeted_scope_selects_only_files_that_could_hold_an_unresolved_accession() -> None:
    selected, skipped = select_history_files(_refs(), unresolved=["0000320193-14-000096"])

    assert [r.name for r in selected] == ["b", "c"]
    assert [r.name for r in skipped] == ["a"]


def test_the_range_has_a_one_year_tolerance_around_a_year_boundary() -> None:
    selected, _ = select_history_files(_refs(), unresolved=["0000320193-17-000001"])
    assert "a" in [r.name for r in selected], "an end-of-year submission can be dated next year"
    selected, _ = select_history_files(_refs(), unresolved=["0000320193-09-000001"])
    assert "b" in [r.name for r in selected]


def test_no_unresolved_target_means_nothing_to_fetch() -> None:
    selected, skipped = select_history_files(_refs(), unresolved=[])

    assert selected == () and len(skipped) == 3


# ── mapping: the history file shape ─────────────────────────────────────────────────────


def test_a_history_file_is_a_flat_set_of_arrays_mapped_with_the_same_contract() -> None:
    filings, rejected, rows = map_history_filings(
        history([H1_10K, H1_10Q_A, H1_8K, H1_FORM4]), cik=CIK
    )

    assert rejected == [] and rows == 4  # Form 4 is a row, but not a supported form
    assert {f.form for f in filings} == {
        SecFormType.FORM_10K,
        SecFormType.FORM_10Q_A,
        SecFormType.FORM_8K,
    }
    eight_k = next(f for f in filings if f.form is SecFormType.FORM_8K)
    assert all(f.accepted_at is None for f in filings), "bulk history never supplies an instant"
    assert eight_k.items == "2.02,9.01" and eight_k.cik == CIK


def test_a_parent_shaped_document_is_not_a_history_file() -> None:
    with pytest.raises(ProviderPayloadError, match="flat"):
        map_history_filings(parent([R_10Q]), cik=CIK)


def test_a_history_file_that_states_another_registrant_is_refused() -> None:
    with pytest.raises(ProviderPayloadError, match="wrong registrant"):
        map_history_filings({**history([H1_10K]), "cik": "789019"}, cik=CIK)


def test_a_history_file_may_omit_its_own_cik_and_a_matching_one_is_accepted() -> None:
    map_history_filings(history([H1_10K]), cik=CIK)
    map_history_filings({**history([H1_10K]), "cik": "320193"}, cik=CIK)


@pytest.mark.parametrize("document", [[], "x", None, {"form": []}])
def test_a_malformed_history_file_is_a_payload_error(document: object) -> None:
    with pytest.raises(ProviderPayloadError):
        map_history_filings(document, cik=CIK)


@pytest.mark.parametrize("row", [H2_PLACEHOLDER, H2_PLACEHOLDER_EDT], ids=["EST", "EDT"])
def test_a_midnight_eastern_acceptance_does_not_block_storing_the_identity(
    row: Row,
) -> None:
    filings, rejected, _rows = map_history_filings(history([H2_10K, row]), cik=CIK)

    assert rejected == []
    assert {f.accession_number for f in filings} == {H2_10K[0], row[0]}
    assert all(f.accepted_at is None for f in filings)


def test_a_placeholder_in_recent_stores_the_identity_like_any_other_row() -> None:
    placeholder = (
        "0000320193-96-000023",
        "10-K",
        "1996-12-04",
        "",
        "1996-12-04T05:00:00.000Z",
        "",
        "",
    )
    real = ("0000320193-96-000024", "10-K", "1996-12-04", "", "1996-12-04T11:00:01.000Z", "", "")
    document = parent([placeholder, real])

    filings, rejected = map_recent_filings(document, cik=CIK)

    assert [f.accession_number for f in filings] == [
        "0000320193-96-000023",
        "0000320193-96-000024",
    ]
    assert rejected == []


def test_recent_rows_are_counted_across_all_forms() -> None:
    assert recent_row_count(parent([R_10Q, R_FORM4, R_10K_A])) == 3
    assert recent_row_count({"filings": {}}) == 0 and recent_row_count([]) == 0


# ── merging representations ─────────────────────────────────────────────────────────────


def _filing(**overrides: object) -> CanonicalSecFiling:
    fields: dict[str, object] = {
        "cik": CIK,
        "accession_number": "0000320193-22-000108",
        "form": SecFormType.FORM_10K,
        "filing_date": date(2022, 10, 28),
        "accepted_at": datetime(2022, 10, 28, 20, 42, 27, tzinfo=UTC),
    }
    fields.update(overrides)
    return CanonicalSecFiling(**fields)  # type: ignore[arg-type]


def test_the_same_accession_with_identical_metadata_is_one_filing() -> None:
    merged, overlap = merge_filings([("recent", [_filing()]), (HISTORY_1, [_filing()])])

    assert len(merged) == 1 and overlap == 1


def test_overlapping_metadata_that_differs_fails_loudly_and_names_both_sources() -> None:
    other = _filing(primary_document="other.htm")

    with pytest.raises(ProviderPayloadError) as caught:
        merge_filings([("recent", [_filing()]), (HISTORY_1, [other])])

    assert "recent" in str(caught.value) and HISTORY_1 in str(caught.value)
    assert "primary_document" in str(caught.value) and "silently preferred" in str(caught.value)


def test_an_amendment_never_merges_with_its_original() -> None:
    amendment = _filing(accession_number="0000320193-22-000109", form=SecFormType.FORM_10K_A)

    merged, overlap = merge_filings([("recent", [_filing()]), (HISTORY_1, [amendment])])

    assert [f.form for f in merged] == [SecFormType.FORM_10K, SecFormType.FORM_10K_A]
    assert overlap == 0


# ── the client: requests, bounds, fail-closed ────────────────────────────────────────────


@pytest.mark.anyio
async def test_the_history_fetch_requests_the_parent_then_each_named_file_and_nothing_else() -> (
    None
):
    recorder, _ = _two_file_routes()
    async with _client(recorder) as client:
        result = await client.get_filing_history(CIK, max_history_files=5)

    assert recorder.paths == [
        f"/submissions/CIK{CIK}.json",
        f"/submissions/{HISTORY_1}",
        f"/submissions/{HISTORY_2}",
    ]
    assert set(recorder.hosts) == {"data.sec.gov"}
    assert set(recorder.user_agents) == {_UA}
    assert isinstance(client, FilingHistoryProvider)
    assert result.history_files_discovered == 2 and result.history_files_fetched == 2
    assert result.history_files_skipped == 0 and result.unresolved_accessions == ()


@pytest.mark.anyio
async def test_the_counts_distinguish_what_was_seen_parsed_overlapped_and_rejected() -> None:
    recorder, _ = _two_file_routes()
    async with _client(recorder) as client:
        result = await client.get_filing_history(CIK, max_history_files=5)

    assert result.recent_rows_seen == 4 and result.recent_filings_parsed == 3  # Form 4 skipped
    assert result.history_rows_seen == 8
    # 10-K(overlap), 10-Q/A, 8-K | 10-K, 10-Q and the two date-placeholder rows (identity is stored)
    assert result.history_filings_parsed == 7
    assert result.overlap_identical == 1
    assert result.rejected == ()
    accessions = [f.accession_number for f in result.filings]
    assert len(accessions) == len(set(accessions)) == 9
    assert all(f.accepted_at is None for f in result.filings)
    keys = [(f.filing_date, f.accession_number) for f in result.filings]
    assert keys == sorted(keys), "deterministic filing-date order, not acceptance order"


@pytest.mark.anyio
async def test_originals_and_amendments_stay_separate_accessions() -> None:
    recorder, _ = _two_file_routes()
    async with _client(recorder) as client:
        result = await client.get_filing_history(CIK, max_history_files=5)

    forms = {f.accession_number: f.form for f in result.filings}
    assert forms["0000320193-19-000067"] is SecFormType.FORM_10Q_A
    assert forms["0001104659-26-053166"] is SecFormType.FORM_10K_A
    assert forms["0000320193-22-000108"] is SecFormType.FORM_10K


@pytest.mark.anyio
async def test_every_response_is_captured_with_its_request_path_cik_and_retrieval_time() -> None:
    recorder, _ = _two_file_routes()
    async with _client(recorder) as client:
        result = await client.get_filing_history(CIK, max_history_files=5)

    parent_capture, first, second = result.captures
    assert (parent_capture.dataset, parent_capture.endpoint) == (
        "submissions",
        "/submissions/CIK{cik}.json",
    )
    assert first.dataset == second.dataset == "submissions-history"
    assert first.endpoint == f"/submissions/{HISTORY_1}"
    assert dict(first.request_parameters) == {"cik": CIK, "file": HISTORY_1}
    assert {c.retrieved_at for c in result.captures} == {datetime(2026, 10, 4, 12, 0, tzinfo=UTC)}
    assert all(_UA not in repr(c) and "tester@example.com" not in repr(c) for c in result.captures)


@pytest.mark.anyio
async def test_a_registrant_with_no_history_files_costs_exactly_one_request() -> None:
    recorder = Recorder({f"/submissions/CIK{CIK}.json": encode(parent([R_10Q]))})
    async with _client(recorder) as client:
        result = await client.get_filing_history(CIK, max_history_files=1)

    assert recorder.paths == [f"/submissions/CIK{CIK}.json"]
    assert result.history_files_discovered == 0 and len(result.filings) == 1


@pytest.mark.anyio
async def test_more_files_than_the_explicit_bound_is_refused_before_any_history_request() -> None:
    recorder, _ = _two_file_routes()
    async with _client(recorder) as client:
        with pytest.raises(HistoryScopeError, match="explicit bound"):
            await client.get_filing_history(CIK, max_history_files=1)

    assert recorder.paths == [f"/submissions/CIK{CIK}.json"], "nothing is silently truncated"


@pytest.mark.anyio
async def test_a_foreign_file_name_in_the_parent_stops_everything_after_one_request() -> None:
    doc = parent([R_10Q])
    doc["filings"]["files"] = [{"name": "CIK0000999999-submissions-001.json", "filingCount": 1}]
    recorder = Recorder({f"/submissions/CIK{CIK}.json": encode(doc)})
    async with _client(recorder) as client:
        with pytest.raises(ProviderPayloadError, match="another registrant"):
            await client.get_filing_history(CIK, max_history_files=5)

    assert recorder.paths == [f"/submissions/CIK{CIK}.json"]


# ── accession-targeted scope ────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_target_already_in_recent_fetches_no_history_file() -> None:
    recorder, _ = _two_file_routes()
    async with _client(recorder) as client:
        result = await client.get_filing_history(CIK, accessions=[R_10Q[0]], max_history_files=5)

    assert recorder.paths == [f"/submissions/CIK{CIK}.json"]
    assert result.history_files_fetched == 0 and result.history_files_skipped == 2
    assert result.unresolved_accessions == ()


@pytest.mark.anyio
async def test_a_target_outside_recent_fetches_only_the_files_that_could_hold_it_and_stops() -> (
    None
):
    recorder, _ = _two_file_routes()
    async with _client(recorder) as client:
        result = await client.get_filing_history(CIK, accessions=[H2_10K[0]], max_history_files=5)

    # file 1 spans 2019-2022 (+-1 year): it cannot hold a 2014 accession, so it is skipped.
    assert recorder.paths == [f"/submissions/CIK{CIK}.json", f"/submissions/{HISTORY_2}"]
    assert result.history_files_fetched == 1 and result.history_files_skipped == 1
    assert H2_10K[0] in {f.accession_number for f in result.filings}
    assert result.unresolved_accessions == ()


@pytest.mark.anyio
async def test_a_target_found_nowhere_is_unresolved_not_absent() -> None:
    recorder, _ = _two_file_routes()
    async with _client(recorder) as client:
        result = await client.get_filing_history(
            CIK, accessions=["0000320193-14-999999"], max_history_files=5
        )

    assert result.unresolved_accessions == ("0000320193-14-999999",)
    assert result.history_files_fetched == 1  # the only file whose range could hold it


@pytest.mark.anyio
async def test_a_target_in_recent_is_not_inferred_absent_when_recent_lacks_it() -> None:
    """Absent from ``recent`` selects history files to look in; it is not a verdict."""
    recorder, _ = _two_file_routes()
    async with _client(recorder) as client:
        result = await client.get_filing_history(
            CIK, accessions=[H1_8K[0], H2_10K[0]], max_history_files=5
        )

    assert result.history_files_fetched == 2 and result.unresolved_accessions == ()


# ── fail-closed on any history-file problem ──────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_history_file_that_cannot_be_downloaded_fails_the_whole_fetch() -> None:
    recorder, _ = _two_file_routes()
    recorder.routes[f"/submissions/{HISTORY_2}"] = 404
    async with _client(recorder) as client:
        with pytest.raises(ProviderNotFoundError) as caught:
            await client.get_filing_history(CIK, max_history_files=5)

    assert [c.dataset for c in caught.value.captures] == ["submissions", "submissions-history"]


@pytest.mark.anyio
async def test_a_malformed_history_file_fails_and_its_bytes_are_kept_as_evidence() -> None:
    recorder, _ = _two_file_routes()
    recorder.routes[f"/submissions/{HISTORY_1}"] = b'{"accessionNumber": "not-an-array"}'
    async with _client(recorder) as client:
        with pytest.raises(ProviderPayloadError) as caught:
            await client.get_filing_history(CIK, max_history_files=5)

    assert caught.value.captures[-1].body == b'{"accessionNumber": "not-an-array"}'


@pytest.mark.anyio
async def test_an_undecodable_history_file_fails() -> None:
    recorder, _ = _two_file_routes()
    recorder.routes[f"/submissions/{HISTORY_1}"] = b"<html>not json</html>"
    async with _client(recorder) as client:
        with pytest.raises(ProviderResponseError):
            await client.get_filing_history(CIK, max_history_files=5)


@pytest.mark.anyio
async def test_a_row_count_that_disagrees_with_the_stated_filing_count_is_not_complete() -> None:
    h1 = [H1_10K, H1_10Q_A]
    doc = parent([R_10Q], [file_entry(HISTORY_1, h1, count=3)])
    recorder = Recorder(
        {
            f"/submissions/CIK{CIK}.json": encode(doc),
            f"/submissions/{HISTORY_1}": encode(history(h1)),
        }
    )
    async with _client(recorder) as client:
        with pytest.raises(ProviderPayloadError, match="filingCount=3"):
            await client.get_filing_history(CIK, max_history_files=5)


@pytest.mark.anyio
async def test_conflicting_overlap_between_recent_and_history_fails_the_fetch() -> None:
    altered = (*R_OVERLAP[:5], "other.htm", *R_OVERLAP[6:])
    doc = parent([altered], [file_entry(HISTORY_1, [H1_10K])])
    recorder = Recorder(
        {
            f"/submissions/CIK{CIK}.json": encode(doc),
            f"/submissions/{HISTORY_1}": encode(history([H1_10K])),
        }
    )
    async with _client(recorder) as client:
        with pytest.raises(ProviderPayloadError, match="silently preferred"):
            await client.get_filing_history(CIK, max_history_files=5)


@pytest.mark.anyio
async def test_a_wrong_registrant_parent_is_refused_without_following_any_file() -> None:
    doc = parent([R_10Q], [file_entry(HISTORY_1, [H1_10K])])
    doc["cik"] = "789019"
    recorder = Recorder({f"/submissions/CIK{CIK}.json": encode(doc)})
    async with _client(recorder) as client:
        with pytest.raises(ProviderPayloadError, match="wrong registrant"):
            await client.get_filing_history(CIK, max_history_files=5)

    assert recorder.paths == [f"/submissions/CIK{CIK}.json"]


@pytest.mark.anyio
async def test_a_missing_user_agent_means_no_request_at_all() -> None:
    recorder, _ = _two_file_routes()
    client = SecClient(
        SecConfig(user_agent=None, base_url="https://data.sec.gov"),
        transport=recorder.transport,
        sleep=_no_sleep,
    )
    async with client:
        with pytest.raises(Exception, match="QF_SEC_USER_AGENT"):
            await client.get_filing_history(CIK, max_history_files=5)

    assert recorder.paths == []


@pytest.mark.anyio
async def test_a_redirect_is_not_followed_to_another_host() -> None:
    recorder, _ = _two_file_routes()
    recorder.routes[f"/submissions/{HISTORY_1}"] = lambda: httpx.Response(
        302, headers={"Location": "https://evil.example/x.json"}
    )
    async with _client(recorder) as client:
        with pytest.raises(ProviderResponseError):
            await client.get_filing_history(CIK, max_history_files=5)

    assert set(recorder.hosts) == {"data.sec.gov"}


def test_a_scale_history_file_is_mapped_without_anomaly_rejection() -> None:
    rows = synthetic_rows(2010, 400)

    filings, rejected, count = map_history_filings(history(rows), cik=CIK)

    assert (len(filings), rejected, count) == (400, [], 400)


# ── isolation: identity ingestion stays below every analysis layer ───────────────────────

_FORBIDDEN_LAYERS = (
    "quantforge.signals",
    "quantforge.research",
    "quantforge.backtest",
    "quantforge.execution",
    "quantforge.financials",
    "quantforge.api",
)
_HISTORY_MODULES = (
    "providers/sec/history.py",
    "providers/sec/mapper.py",
    "providers/sec/client.py",
    "ingestion/filing_history.py",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize("relative", _HISTORY_MODULES)
def test_history_identity_modules_import_no_analysis_layer(relative: str) -> None:
    offenders = {
        module
        for module in _imported_modules(SRC / relative)
        if module.startswith(_FORBIDDEN_LAYERS)
    }
    assert offenders == set(), relative


@pytest.mark.parametrize("relative", ["providers/sec/history.py", "ingestion/filing_history.py"])
def test_history_identity_modules_name_no_availability_or_fsds_concept(relative: str) -> None:
    tree = ast.parse((SRC / relative).read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
    }
    forbidden = {
        "AvailabilityBasis",
        "observed_at",
        "observed_at_basis",
        "known_as_of",
        "filing_public_at",
        "source_accepted_text",
        "FinancialFact",
        "FinancialFactSubmission",
    }
    assert not names & forbidden, relative


@pytest.mark.anyio
async def test_a_target_present_but_unsupported_is_located_not_unresolved() -> None:
    recorder, _ = _two_file_routes()
    async with _client(recorder) as client:
        result = await client.get_filing_history(
            CIK, accessions=[R_FORM4[0], H2_PLACEHOLDER[0]], max_history_files=5
        )

    assert result.unresolved_accessions == ()
    assert set(result.located_not_stored) == {R_FORM4[0]}, "Form 4 is unsupported"
    assert H2_PLACEHOLDER[0] in {f.accession_number for f in result.filings}, "identity is stored"
    assert recorder.paths[-1] == f"/submissions/{HISTORY_2}", "found, so no further file is read"


@pytest.mark.anyio
@pytest.mark.parametrize("bound", [0, -1, 101])
async def test_the_client_itself_enforces_the_file_bound_range(bound: int) -> None:
    recorder, _ = _two_file_routes()
    async with _client(recorder) as client:
        with pytest.raises(HistoryScopeError, match="between 1 and 100"):
            await client.get_filing_history(CIK, max_history_files=bound)

    assert recorder.paths == []


@pytest.mark.anyio
async def test_a_missing_history_file_is_named_as_a_history_file_not_a_missing_registrant() -> None:
    recorder, _ = _two_file_routes()
    recorder.routes[f"/submissions/{HISTORY_1}"] = 404
    async with _client(recorder) as client:
        with pytest.raises(ProviderNotFoundError, match="history file") as caught:
            await client.get_filing_history(CIK, max_history_files=5)

    assert HISTORY_1 in str(caught.value)


def test_a_recent_block_with_placeholders_adds_only_the_placeholder_identities() -> None:
    """Midnight-Eastern placeholder rows are ordinary identities now (no instant is read)."""
    modern = [R_10Q, R_10K_A, R_OVERLAP]
    document = parent([*modern, H2_PLACEHOLDER, H2_PLACEHOLDER_EDT])

    with_placeholders, rejected = map_recent_filings(document, cik=CIK)
    without, no_rejected = map_recent_filings(parent(modern), cik=CIK)

    assert no_rejected == [] and rejected == []
    assert {f.accession_number for f in with_placeholders} == {
        f.accession_number for f in without
    } | {H2_PLACEHOLDER[0], H2_PLACEHOLDER_EDT[0]}
