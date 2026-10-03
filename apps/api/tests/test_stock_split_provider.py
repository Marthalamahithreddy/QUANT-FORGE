"""Canonical stock split, the Massive mapping, and the Massive splits client (no network).

Orientation under test (Massive docs): ``split_from`` = old shares, ``split_to`` =
new shares -- "in a 2-for-1 split, split_from would be 1".
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from quantforge.ingestion.canonical import CanonicalStockSplit
from quantforge.ingestion.raw_store import RawResponseStore
from quantforge.models.enums import StockSplitAdjustmentType
from quantforge.providers.errors import ProviderResponseError
from quantforge.providers.massive.client import MassiveClient, MassiveConfig
from quantforge.providers.massive.dto import MassiveSplitDTO
from quantforge.providers.massive.mapper import to_canonical_stock_split


def _record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": "Eabc123",
        "ticker": "NVDA",
        "execution_date": "2024-06-10",
        "split_from": 1,
        "split_to": 10,
        "adjustment_type": "forward_split",
        "historical_adjustment_factor": 0.1,
    }
    record.update(overrides)
    return record


def _canonical(**overrides: Any) -> CanonicalStockSplit:
    fields: dict[str, Any] = {
        "provider_split_id": "Eabc123",
        "ticker": "NVDA",
        "execution_date": date(2024, 6, 10),
        "old_shares": Decimal(1),
        "new_shares": Decimal(10),
        "adjustment_type": StockSplitAdjustmentType.FORWARD_SPLIT,
    }
    fields.update(overrides)
    return CanonicalStockSplit(**fields)


# ── canonical ────────────────────────────────────────────────────────────────


def test_forward_split_keeps_old_and_new_orientation() -> None:
    split = to_canonical_stock_split(MassiveSplitDTO.from_provider(_record()))
    assert (split.old_shares, split.new_shares) == (Decimal(1), Decimal(10))
    assert split.adjustment_type is StockSplitAdjustmentType.FORWARD_SPLIT


def test_reverse_split_is_not_assumed_to_have_new_greater_than_old() -> None:
    record = _record(split_from=10, split_to=1, adjustment_type="reverse_split")
    split = to_canonical_stock_split(MassiveSplitDTO.from_provider(record))
    assert (split.old_shares, split.new_shares) == (Decimal(10), Decimal(1))
    assert split.adjustment_type is StockSplitAdjustmentType.REVERSE_SPLIT


def test_a_fractional_ratio_is_kept_exactly() -> None:
    record = _record(split_from=1, split_to=Decimal("1.027"), adjustment_type="stock_dividend")
    split = to_canonical_stock_split(MassiveSplitDTO.from_provider(record))
    assert split.new_shares == Decimal("1.027") and split.new_shares.as_tuple().exponent == -3


@pytest.mark.parametrize("bad", [0, -1, Decimal("-0.5")])
@pytest.mark.parametrize("field", ["old_shares", "new_shares"])
def test_non_positive_ratios_are_rejected(field: str, bad: Decimal | int) -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        _canonical(**{field: Decimal(bad)})


@pytest.mark.parametrize(
    ("old", "new", "kind"),
    [
        (1, 10, StockSplitAdjustmentType.REVERSE_SPLIT),
        (10, 1, StockSplitAdjustmentType.FORWARD_SPLIT),
        (10, 1, StockSplitAdjustmentType.STOCK_DIVIDEND),
    ],
)
def test_a_ratio_that_contradicts_its_classification_is_rejected(
    old: int, new: int, kind: StockSplitAdjustmentType
) -> None:
    with pytest.raises(ValueError, match="contradicts the ratio"):
        _canonical(old_shares=Decimal(old), new_shares=Decimal(new), adjustment_type=kind)


@pytest.mark.parametrize("kind", list(StockSplitAdjustmentType))
def test_a_ratio_of_one_is_not_a_split(kind: StockSplitAdjustmentType) -> None:
    with pytest.raises(ValueError, match="no share-count change"):
        _canonical(old_shares=Decimal(2), new_shares=Decimal(2), adjustment_type=kind)


def test_float_and_nonfinite_ratios_are_rejected() -> None:
    with pytest.raises(ValueError, match="must be a Decimal"):
        _canonical(new_shares=10.0)
    with pytest.raises(ValueError, match="finite"):
        _canonical(new_shares=Decimal("NaN"))


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider_split_id": ""},
        {"provider_split_id": " x "},
        {"ticker": "nvda"},
        {"ticker": ""},
        {"execution_date": "2024-06-10"},
        {"adjustment_type": "FORWARD_SPLIT"},
    ],
)
def test_malformed_canonical_values_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        _canonical(**overrides)


# ── mapping / DTO ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": None},
        {"id": ""},
        {"ticker": None},
        {"execution_date": None},
        {"execution_date": "2024-6-10"},
        {"execution_date": "10/06/2024"},
        {"execution_date": 1718000000},
        {"split_from": None},
        {"split_to": "10"},
        {"split_to": True},
        {"split_to": 10.5},
        {"adjustment_type": None},
    ],
)
def test_malformed_provider_records_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(Exception, match="invalid split record"):
        MassiveSplitDTO.from_provider(_record(**overrides), identifier="NVDA")


def test_a_missing_required_field_is_rejected() -> None:
    record = _record()
    del record["id"]
    with pytest.raises(Exception, match="invalid split record"):
        MassiveSplitDTO.from_provider(record)


def test_an_unknown_adjustment_type_is_rejected_not_guessed() -> None:
    dto = MassiveSplitDTO.from_provider(_record(adjustment_type="spin_off"))
    with pytest.raises(Exception, match="unrecognised split adjustment_type"):
        to_canonical_stock_split(dto)


def test_unknown_extra_fields_are_ignored_and_the_adjustment_factor_is_not_read() -> None:
    dto = MassiveSplitDTO.from_provider(_record(brand_new_field="x"))
    assert not hasattr(dto, "historical_adjustment_factor")


# ── client ───────────────────────────────────────────────────────────────────


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


@pytest.mark.anyio
async def test_one_page_is_fetched_with_the_documented_parameters() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _page(_record())

    async with _client(handler) as client:
        result = await client.get_stock_splits("nvda")

    request = seen[0]
    assert request.url.path == "/stocks/v1/splits"
    assert dict(request.url.params) == {
        "ticker": "NVDA",
        "limit": "5000",
        "sort": "execution_date.asc",
    }
    assert "test-key" not in str(request.url), "the key is a header, never a URL"
    assert request.headers["authorization"].startswith("Bearer ")
    assert [s.provider_split_id for s in result.splits] == ["Eabc123"]
    assert result.rejected == ()


@pytest.mark.anyio
async def test_raw_bytes_are_captured_verbatim_without_a_key() -> None:
    body = json.dumps({"status": "OK", "results": [_record()]}).encode()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    async with _client(handler) as client:
        result = await client.get_stock_splits("NVDA")

    capture = result.captures[0]
    assert capture.body == body
    assert (capture.provider, capture.dataset) == ("massive", "stock-splits")
    assert capture.endpoint == "/stocks/v1/splits"
    assert "test-key" not in json.dumps(capture.request_parameters)


@pytest.mark.anyio
async def test_multiple_pages_are_followed_and_results_sorted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("cursor") == "2":
            return _page(_record(id="E1", execution_date="2021-07-20", split_to=4))
        return _page(
            _record(id="E2", execution_date="2024-06-10"),
            next_url="https://api.massive.com/stocks/v1/splits?cursor=2&ticker=NVDA",
        )

    async with _client(handler) as client:
        result = await client.get_stock_splits("NVDA")

    assert [s.provider_split_id for s in result.splits] == ["E1", "E2"]
    assert len(result.captures) == 2


@pytest.mark.anyio
async def test_each_page_capture_records_its_position_and_a_digest_never_the_cursor_url() -> None:
    cursor = "YWN0aXZlPXRydWUmZGF0ZT0yMDIxLTA0LTI1" * 3  # long, opaque, base64-ish with '='
    leak = "LEAK-SECRET"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("cursor") == cursor:
            return _page(_record(id="E1", execution_date="2021-07-20", split_to=4))
        return _page(
            _record(id="E2"),
            next_url=f"https://api.massive.com/stocks/v1/splits?cursor={cursor}&apiKey={leak}&token={leak}",
        )

    async with _client(handler) as client:
        result = await client.get_stock_splits("NVDA")

    first, second = (c.request_parameters for c in result.captures)
    assert "page" not in first and first["limit"] == "5000"
    assert second["page"] == "2" and len(second["next_url_sha256"]) == 32
    assert cursor not in json.dumps(second) and leak not in json.dumps(second)
    assert first != second, "a repeated or dropped page is distinguishable in the manifest"


@pytest.mark.anyio
async def test_a_long_cursor_page_capture_is_storable_in_the_real_raw_store(tmp_path: Path) -> None:
    cursor = "YWN0aXZlPXRydWUmZGF0ZT0yMDIxLTA0LTI1" * 3

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("cursor") == cursor:
            return _page()
        return _page(
            _record(), next_url=f"https://api.massive.com/stocks/v1/splits?cursor={cursor}"
        )

    async with _client(handler) as client:
        result = await client.get_stock_splits("NVDA")

    stored = RawResponseStore(root=tmp_path / "raw").store_all(result.captures)
    assert stored is not None


@pytest.mark.anyio
async def test_captures_gathered_before_a_mid_pagination_failure_are_attached() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("cursor") == "2":
            return httpx.Response(200, content=b'{"status":"OK"}')  # no results
        return _page(_record(), next_url="https://api.massive.com/stocks/v1/splits?cursor=2")

    async with _client(handler) as client:
        with pytest.raises(ProviderResponseError) as raised:
            await client.get_stock_splits("NVDA")

    assert len(raised.value.captures) == 2
    assert raised.value.captures[1].body == b'{"status":"OK"}'


@pytest.mark.anyio
async def test_a_next_url_host_is_discarded() -> None:
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.params.get("cursor"):
            return _page()
        return _page(_record(), next_url="https://evil.example.com/stocks/v1/splits?cursor=2")

    async with _client(handler) as client:
        await client.get_stock_splits("NVDA")

    assert set(hosts) == {"api.massive.com"}


@pytest.mark.anyio
async def test_an_empty_result_is_a_valid_empty_answer() -> None:
    async with _client(lambda _r: _page()) as client:
        result = await client.get_stock_splits("NVDA")
    assert result.splits == () or list(result.splits) == []
    assert result.rejected == () and len(result.captures) == 1


@pytest.mark.anyio
async def test_a_missing_results_member_is_an_error_not_an_empty_answer() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"status":"OK"}')

    async with _client(handler) as client:
        with pytest.raises(ProviderResponseError, match="results"):
            await client.get_stock_splits("NVDA")


@pytest.mark.anyio
async def test_a_malformed_next_url_is_rejected() -> None:
    async with _client(lambda _r: _page(_record(), next_url=12345)) as client:
        with pytest.raises(ProviderResponseError, match="next_url"):
            await client.get_stock_splits("NVDA")


@pytest.mark.anyio
async def test_a_repeating_next_url_is_a_pagination_loop() -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return _page(_record(), next_url="https://api.massive.com/stocks/v1/splits?cursor=same")

    async with _client(handler) as client:
        with pytest.raises(ProviderResponseError, match="pagination loop"):
            await client.get_stock_splits("NVDA")


@pytest.mark.anyio
async def test_the_page_ceiling_is_enforced() -> None:
    counter = {"n": 0}

    def handler(_r: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        return _page(
            _record(id=f"E{counter['n']}"),
            next_url=f"https://api.massive.com/stocks/v1/splits?cursor={counter['n']}",
        )

    async with _client(handler, max_pages=3) as client:
        with pytest.raises(ProviderResponseError, match="3 pages"):
            await client.get_stock_splits("NVDA")


@pytest.mark.anyio
async def test_bad_records_are_reported_not_hidden_and_good_ones_survive() -> None:
    page = _page(
        _record(),
        _record(id="Ebad", split_to=0),
        _record(id="Eodd", adjustment_type="spin_off"),
        _record(id="Eother", ticker="AAPL"),
    )
    async with _client(lambda _r: page) as client:
        result = await client.get_stock_splits("NVDA")

    assert [s.provider_split_id for s in result.splits] == ["Eabc123"]
    assert len(result.rejected) == 3
    assert any("AAPL" in reason and "NVDA" in reason for reason in result.rejected)


@pytest.mark.anyio
async def test_provider_ratios_are_decoded_without_a_binary_float() -> None:
    body = (
        b'{"status":"OK","results":[{"id":"E1","ticker":"X","execution_date":"2024-01-02",'
        b'"split_from":1.0,"split_to":1.027,"adjustment_type":"stock_dividend"}]}'
    )
    async with _client(lambda _r: httpx.Response(200, content=body)) as client:
        result = await client.get_stock_splits("X")

    (split,) = result.splits
    assert split.new_shares == Decimal("1.027") and str(split.new_shares) == "1.027"


@pytest.mark.anyio
async def test_an_unsafe_ticker_never_reaches_the_url() -> None:
    async with _client(lambda _r: _page()) as client:
        with pytest.raises(ValueError, match="not allowed"):
            await client.get_stock_splits("../etc")
