"""Canonical listing-history contracts and the Massive snapshot / ticker-events adapter (no network)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from quantforge.ingestion.canonical import (
    CanonicalTickerChange,
    CanonicalTickerEvents,
    CanonicalTickerSnapshot,
)
from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.providers.errors import ProviderResponseError
from quantforge.providers.massive.client import MassiveClient, MassiveConfig
from quantforge.providers.massive.dto import MassiveTickerChangeEventDTO, MassiveTickerSnapshotDTO
from quantforge.providers.massive.mapper import to_canonical_ticker_snapshot

SHARE_CLASS = "BBG001SCTQY4"
COMPOSITE = "BBG000BWQYZ5"
DAY = date(2020, 1, 2)


def _record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "ticker": "A",
        "name": "Agilent",
        "market": "stocks",
        "locale": "us",
        "type": "CS",
        "active": True,
        "primary_exchange": "XNYS",
        "cik": "1090872",
        "composite_figi": COMPOSITE,
        "share_class_figi": SHARE_CLASS,
        "last_updated_utc": "2021-04-25T00:00:00Z",
    }
    record.update(overrides)
    return record


def _client(handler: Any, **config: Any) -> MassiveClient:
    async def no_sleep(_seconds: float) -> None:
        return None

    return MassiveClient(
        MassiveConfig(api_key="test-key-0123456789", base_url="https://api.massive.com", **config),
        transport=httpx.MockTransport(handler),
        sleep=no_sleep,
    )


def _page(*records: dict[str, Any], next_url: Any = None) -> httpx.Response:
    body: dict[str, Any] = {"status": "OK", "request_id": "r", "results": list(records)}
    if next_url is not None:
        body["next_url"] = next_url
    return httpx.Response(200, content=json.dumps(body).encode())


# ── canonical ────────────────────────────────────────────────────────────────


def test_a_canonical_snapshot_keeps_share_class_and_composite_figi_apart() -> None:
    snapshot = CanonicalTickerSnapshot(
        as_of_date=DAY,
        ticker="A",
        active=True,
        share_class_figi=SHARE_CLASS,
        composite_figi=COMPOSITE,
        cik="0001090872",
    )
    assert snapshot.share_class_figi != snapshot.composite_figi


@pytest.mark.parametrize(
    "overrides",
    [
        {"ticker": "a"},
        {"ticker": ""},
        {"share_class_figi": "NOTAFIGI"},
        {"composite_figi": "bbg000bwqyz5"},
        {"cik": "1090872"},
        {"primary_mic": "XNY"},
        {"active": "yes"},
        {"as_of_date": "2020-01-02"},
    ],
)
def test_malformed_canonical_snapshots_are_rejected(overrides: dict[str, Any]) -> None:
    fields: dict[str, Any] = {"as_of_date": DAY, "ticker": "A", "active": True}
    fields.update(overrides)
    with pytest.raises(ValueError):
        CanonicalTickerSnapshot(**fields)


def test_ticker_events_must_be_in_ascending_date_order() -> None:
    later = CanonicalTickerChange("META", date(2022, 6, 9))
    earlier = CanonicalTickerChange("FB", date(2012, 5, 18))
    CanonicalTickerEvents("META", (earlier, later))
    with pytest.raises(ValueError, match="ascending"):
        CanonicalTickerEvents("META", (later, earlier))


# ── snapshot DTO / mapping ───────────────────────────────────────────────────


def test_a_snapshot_maps_dates_exactly_as_stated_and_never_derives_an_interval() -> None:
    dto = MassiveTickerSnapshotDTO.from_provider(
        _record(active=False, delisted_utc="2021-04-23T00:00:00Z")
    )
    snapshot = to_canonical_ticker_snapshot(dto, as_of_date=DAY)

    assert snapshot.as_of_date == DAY
    assert snapshot.delisted_date == date(2021, 4, 23)
    assert snapshot.primary_mic == "XNYS"
    assert snapshot.cik == "0001090872", "CIK is zero-padded and stays company-level"
    assert not hasattr(snapshot, "valid_from") and not hasattr(snapshot, "valid_to")


def test_an_unknown_venue_code_leaves_the_mic_empty_rather_than_guessing() -> None:
    dto = MassiveTickerSnapshotDTO.from_provider(_record(primary_exchange="ZZZZ"))
    assert to_canonical_ticker_snapshot(dto, as_of_date=DAY).primary_mic is None


def test_a_blank_figi_is_absent_not_an_empty_string() -> None:
    dto = MassiveTickerSnapshotDTO.from_provider(_record(share_class_figi="  ", cik=""))
    snapshot = to_canonical_ticker_snapshot(dto, as_of_date=DAY)
    assert snapshot.share_class_figi is None and snapshot.cik is None


@pytest.mark.parametrize(
    "overrides",
    [{"ticker": None}, {"active": "true"}, {"cik": "abc"}, {"composite_figi": 5}, {"active": None}],
)
def test_malformed_snapshot_records_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(Exception, match="invalid ticker snapshot record"):
        MassiveTickerSnapshotDTO.from_provider(_record(**overrides))


def test_a_bad_delisted_utc_is_rejected() -> None:
    dto = MassiveTickerSnapshotDTO.from_provider(_record(delisted_utc="not-a-date"))
    with pytest.raises(Exception, match="delisted_utc"):
        to_canonical_ticker_snapshot(dto, as_of_date=DAY)


# ── snapshot client ──────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_the_snapshot_request_uses_the_documented_parameters_and_a_header_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _page(_record())

    async with _client(handler) as client:
        result = await client.get_ticker_snapshots("a", DAY)

    request = seen[0]
    assert request.url.path == "/v3/reference/tickers"
    assert dict(request.url.params) == {
        "ticker": "A",
        "date": "2020-01-02",
        "market": "stocks",
        "active": "true",
        "limit": "1000",
    }
    assert "test-key" not in str(request.url)
    assert [s.ticker for s in result.snapshots] == ["A"] and result.rejected == ()


@pytest.mark.anyio
async def test_unsupported_instrument_types_are_counted_not_failed() -> None:
    page = _page(_record(), _record(type="WARRANT"), _record(type="ETF"))
    async with _client(lambda _r: page) as client:
        result = await client.get_ticker_snapshots("A", DAY)

    assert len(result.snapshots) == 1 and result.unsupported == 2 and result.rejected == ()


@pytest.mark.anyio
async def test_a_record_for_another_ticker_or_a_malformed_record_is_rejected() -> None:
    page = _page(_record(), _record(ticker="B"), _record(active="maybe"))
    async with _client(lambda _r: page) as client:
        result = await client.get_ticker_snapshots("A", DAY)

    assert len(result.snapshots) == 1 and len(result.rejected) == 2


@pytest.mark.anyio
async def test_snapshot_pagination_follows_next_url_and_captures_every_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("cursor") == "2":
            return _page(_record(share_class_figi="BBG001SCTQY5"))
        return _page(
            _record(), next_url="https://api.massive.com/v3/reference/tickers?cursor=2&ticker=A"
        )

    async with _client(handler) as client:
        result = await client.get_ticker_snapshots("A", DAY)

    assert len(result.snapshots) == 2 and len(result.captures) == 2
    assert "page" in result.captures[1].request_parameters


@pytest.mark.anyio
async def test_a_repeating_snapshot_cursor_is_a_pagination_loop() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return _page(_record(), next_url="https://api.massive.com/v3/reference/tickers?cursor=same")

    async with _client(handler) as client:
        with pytest.raises(ProviderResponseError, match="pagination loop"):
            await client.get_ticker_snapshots("A", DAY)


@pytest.mark.anyio
async def test_snapshot_raw_bytes_are_captured_verbatim(tmp_path: Path) -> None:
    body = json.dumps({"status": "OK", "results": [_record()]}).encode()
    async with _client(lambda _r: httpx.Response(200, content=body)) as client:
        result = await client.get_ticker_snapshots("A", DAY)

    assert result.captures[0].body == body
    assert (result.captures[0].provider, result.captures[0].dataset) == (
        "massive",
        "ticker-snapshots",
    )
    assert RawResponseStore(root=tmp_path / "raw").store_all(result.captures) is not None


# ── ticker events ────────────────────────────────────────────────────────────

META_EVENTS = {
    "request_id": "31d59dda",
    "results": {
        "events": [
            {"date": "2022-06-09", "ticker_change": {"ticker": "META"}, "type": "ticker_change"},
            {"date": "2012-05-18", "ticker_change": {"ticker": "FB"}, "type": "ticker_change"},
        ],
        "name": "Meta Platforms, Inc. Class A Common Stock",
    },
    "status": "OK",
}


def _events_response(document: Any) -> httpx.Response:
    return httpx.Response(200, content=json.dumps(document).encode())


@pytest.mark.anyio
async def test_the_documented_meta_payload_maps_to_ascending_ticker_changes() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _events_response(META_EVENTS)

    async with _client(handler) as client:
        result = await client.get_ticker_events("meta")

    assert seen[0].url.path == "/vX/reference/tickers/META/events"
    assert result.rejected == () and result.events is not None
    assert [(c.ticker, c.effective_date) for c in result.events.changes] == [
        ("FB", date(2012, 5, 18)),
        ("META", date(2022, 6, 9)),
    ]
    assert result.events.name is not None
    assert result.captures[0].dataset == "ticker-events"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "event",
    [
        {"type": "ticker_change", "date": "2022-06-09"},
        {"type": "ticker_change", "date": "2022-06-09", "ticker_change": {}},
        {"type": "ticker_change", "date": "2022-06-09", "ticker_change": {"ticker": ""}},
        {"type": "ticker_change", "date": "06/09/2022", "ticker_change": {"ticker": "META"}},
        {"type": "ticker_change", "date": 20220609, "ticker_change": {"ticker": "META"}},
        {"type": "name_change", "date": "2022-06-09", "ticker_change": {"ticker": "META"}},
        {"date": "2022-06-09", "ticker_change": {"ticker": "META"}},
        "ticker_change",
    ],
)
async def test_experimental_payload_changes_fail_explicitly_per_record(event: Any) -> None:
    document = {"results": {"events": [event]}, "status": "OK"}
    async with _client(lambda _r: _events_response(document)) as client:
        result = await client.get_ticker_events("META")

    assert len(result.rejected) == 1 and result.events is not None and result.events.changes == ()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "document",
    [{"status": "OK"}, {"results": []}, {"results": {"events": "none"}}, {"results": {}}, []],
)
async def test_a_missing_or_reshaped_envelope_is_rejected_with_the_raw_evidence(
    document: Any,
) -> None:
    async with _client(lambda _r: _events_response(document)) as client:
        result = await client.get_ticker_events("META")

    assert result.events is None and len(result.rejected) == 1 and len(result.captures) == 1


def test_the_event_dto_accepts_only_ticker_change() -> None:
    dto = MassiveTickerChangeEventDTO.from_provider(
        {"type": "ticker_change", "date": "2022-06-09", "ticker_change": {"ticker": " meta "}}
    )
    assert dto.new_ticker == "META"
