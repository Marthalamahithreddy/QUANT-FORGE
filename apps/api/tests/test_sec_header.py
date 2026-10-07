"""The EDGAR filing header: the only approved source of ``accepted_at`` (step 11.2H)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from quantforge.providers.errors import (
    ProviderNotFoundError,
    ProviderPayloadError,
)
from quantforge.providers.filings import FilingHeaderProvider
from quantforge.providers.sec.client import SecClient, SecConfig
from quantforge.providers.sec.header import header_path, parse_filing_header
from tests.sec_header_fixture import header

_EVIDENCE = json.loads((Path(__file__).parent / "sec_acceptance_evidence.json").read_text())
_PAIRS = _EVIDENCE["header_pairs"]
_UA = "QuantForgeTest tester@example.com"


@pytest.mark.parametrize("pair", _PAIRS, ids=[pair["accession"] for pair in _PAIRS])
def test_real_header_values_convert_to_the_expected_utc_instant_in_both_offsets(
    pair: dict[str, Any],
) -> None:
    parsed = parse_filing_header(
        header(
            accession=pair["accession"],
            acceptance=pair["header_raw"],
            form=pair["form"],
            filing_date=pair["filing_date"].replace("-", ""),
        )
    )

    assert parsed.accepted_at == datetime.fromisoformat(pair["header_eastern_as_utc"])
    assert parsed.accepted_at.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
    assert parsed.acceptance_text == pair["header_raw"]


def test_the_real_nke_header_is_20_16_39_utc_in_daylight_time() -> None:
    parsed = parse_filing_header(header())
    assert parsed.accepted_at == datetime(2026, 4, 1, 20, 16, 39, tzinfo=UTC)
    assert (parsed.accession_number, parsed.form) == ("0000320187-26-000037", "10-Q")
    assert parsed.filing_date == date(2026, 4, 1) and parsed.ciks == ("0000320187",)


def test_standard_time_uses_a_five_hour_offset_and_daylight_time_four() -> None:
    winter = parse_filing_header(header(acceptance="20251230162045", filing_date="20251230"))
    summer = parse_filing_header(header(acceptance="20260630162045", filing_date="20260630"))
    assert winter.accepted_at == datetime(2025, 12, 30, 21, 20, 45, tzinfo=UTC)
    assert summer.accepted_at == datetime(2026, 6, 30, 20, 20, 45, tzinfo=UTC)


@pytest.mark.parametrize(
    "acceptance",
    ["20251102013000", "20251102010000", "20251102013059"],  # the repeated hour (fall back)
)
def test_an_ambiguous_wall_time_is_refused_not_guessed(acceptance: str) -> None:
    with pytest.raises(ProviderPayloadError, match="daylight-saving transition"):
        parse_filing_header(header(acceptance=acceptance, filing_date="20251102"))


@pytest.mark.parametrize("acceptance", ["20250309020000", "20250309023000", "20250309025959"])
def test_a_non_existent_wall_time_is_refused_not_guessed(acceptance: str) -> None:
    with pytest.raises(ProviderPayloadError, match="daylight-saving transition"):
        parse_filing_header(header(acceptance=acceptance, filing_date="20250309"))


def test_times_next_to_the_transitions_are_unambiguous() -> None:
    after_fall_back = parse_filing_header(
        header(acceptance="20251102020000", filing_date="20251102")
    )
    after_spring = parse_filing_header(header(acceptance="20250309030000", filing_date="20250309"))
    assert after_fall_back.accepted_at == datetime(2025, 11, 2, 7, 0, tzinfo=UTC)
    assert after_spring.accepted_at == datetime(2025, 3, 9, 7, 0, tzinfo=UTC)


def test_windows_line_endings_are_read() -> None:
    assert parse_filing_header(header(eol="\r\n")).accepted_at.hour == 20


def test_a_co_registrant_header_states_every_cik() -> None:
    parsed = parse_filing_header(header(ciks=("0000320187", "0000789019")))
    assert parsed.ciks == ("0000320187", "0000789019")


@pytest.mark.parametrize(
    "acceptance",
    [
        "",
        "2026040116163",
        "202604011616390",
        "2026-04-01 16:16:39",
        "20261301161639",
        "20260401246039",
    ],
)
def test_a_malformed_acceptance_text_is_refused(acceptance: str) -> None:
    with pytest.raises(ProviderPayloadError):
        parse_filing_header(header(acceptance=acceptance))


def test_a_pre_edgar_date_is_refused() -> None:
    with pytest.raises(ProviderPayloadError, match="predates"):
        parse_filing_header(header(acceptance="19921231101010"))


def test_a_missing_or_repeated_field_is_refused() -> None:
    good = header().decode()
    with pytest.raises(ProviderPayloadError, match="ACCEPTANCE-DATETIME"):
        parse_filing_header(good.replace("<ACCEPTANCE-DATETIME>20260401161639\n", "").encode())
    with pytest.raises(ProviderPayloadError, match="exactly one"):
        parse_filing_header(good.replace("<TYPE>10-Q\n", "<TYPE>10-Q\n<TYPE>10-K\n").encode())
    with pytest.raises(ProviderPayloadError, match="CIK"):
        parse_filing_header(good.replace("<CIK>0000320187\n", "").encode())
    with pytest.raises(ProviderPayloadError, match="FILING-DATE"):
        parse_filing_header(good.replace("<FILING-DATE>20260401", "<FILING-DATE>2026-04").encode())


@pytest.mark.parametrize(
    "body", [b"<html><body>Not Found</body></html>", b"", b"\xff\xfe<SEC-HEADER>", b"{}"]
)
def test_a_document_that_is_not_an_edgar_header_is_refused(body: bytes) -> None:
    with pytest.raises(ProviderPayloadError):
        parse_filing_header(body)


def test_the_header_path_is_the_archives_path_of_the_accession() -> None:
    assert header_path("0000320187", "0000320187-26-000037") == (
        "/Archives/edgar/data/320187/000032018726000037/0000320187-26-000037.hdr.sgml"
    )
    with pytest.raises(ValueError, match="accession"):
        header_path("0000320187", "../../etc/passwd")


def test_acceptance_text_is_not_hardcoded_to_a_fixed_offset() -> None:
    """The conversion follows the zone database: the same wall time differs by season."""
    a = parse_filing_header(header(acceptance="20260115120000", filing_date="20260115"))
    b = parse_filing_header(header(acceptance="20260715120000", filing_date="20260715"))
    assert a.accepted_at.hour == 17 and b.accepted_at.hour == 16


def _client(handler: Any) -> SecClient:
    async def no_sleep(_delay: float) -> None:
        return None

    return SecClient(
        SecConfig(user_agent=_UA, base_url="https://data.sec.gov"),
        transport=httpx.MockTransport(handler),
        sleep=no_sleep,
    )


@pytest.mark.anyio
async def test_the_client_makes_exactly_one_request_to_the_archives_header_path() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=header())

    client = _client(handler)
    result = await client.get_filing_header("320187", "0000320187-26-000037")

    assert isinstance(client, FilingHeaderProvider)
    assert len(seen) == 1
    assert (seen[0].url.host, seen[0].url.path) == (
        "www.sec.gov",
        "/Archives/edgar/data/320187/000032018726000037/0000320187-26-000037.hdr.sgml",
    )
    assert seen[0].headers["User-Agent"] == _UA
    assert result.header.accepted_at == datetime(2026, 4, 1, 20, 16, 39, tzinfo=UTC)
    capture = result.capture
    assert (capture.provider, capture.dataset, capture.body) == (
        "sec-edgar",
        "filing-header",
        header(),
    )
    assert "tester@example.com" not in repr(capture.request_parameters) + capture.endpoint


@pytest.mark.anyio
async def test_a_header_that_cannot_be_parsed_carries_the_raw_response_as_evidence() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>maintenance</html>")

    with pytest.raises(ProviderPayloadError) as caught:
        await _client(handler).get_filing_header("320187", "0000320187-26-000037")

    assert caught.value.captures[0].body == b"<html>maintenance</html>"


@pytest.mark.anyio
async def test_a_missing_header_is_not_found_and_makes_one_request() -> None:
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404)

    with pytest.raises(ProviderNotFoundError):
        await _client(handler).get_filing_header("320187", "0000320187-26-000037")
    assert calls == [1]


@pytest.mark.parametrize(
    ("name", "accession", "form", "filing_date", "cik", "expected"),
    [
        (
            "sec_header_real_nke_26_000037.hdr.sgml",
            "0000320187-26-000037",
            "10-Q",
            date(2026, 4, 1),
            "0000320187",
            datetime(2026, 4, 1, 20, 16, 39, tzinfo=UTC),
        ),
        (
            "sec_header_real_msft_26_027207.hdr.sgml",
            "0001193125-26-027207",
            "10-Q",
            date(2026, 1, 28),
            "0000789019",
            datetime(2026, 1, 28, 21, 7, 34, tzinfo=UTC),
        ),
    ],
)
def test_verbatim_real_edgar_headers_parse(
    name: str,
    accession: str,
    form: str,
    filing_date: date,
    cik: str,
    expected: datetime,
) -> None:
    """Real ``.hdr.sgml`` bytes fetched from EDGAR on 2026-10-07 (public data, unmodified)."""
    body = (Path(__file__).parent / name).read_bytes()

    parsed = parse_filing_header(body)

    assert (parsed.accession_number, parsed.form, parsed.filing_date) == (
        accession,
        form,
        filing_date,
    )
    assert cik in parsed.ciks
    assert parsed.accepted_at == expected


def test_a_cik_that_is_not_digits_is_a_malformed_header() -> None:
    good = header().decode().replace("<CIK>0000320187", "<CIK>abc")
    with pytest.raises(ProviderPayloadError, match="CIK"):
        parse_filing_header(good.encode())


@pytest.mark.anyio
async def test_the_header_request_accepts_plain_text_and_a_404_names_the_accession() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(404)

    with pytest.raises(ProviderNotFoundError) as caught:
        await _client(handler).get_filing_header("320187", "0000320187-26-000037")

    assert seen[0].headers["Accept"].startswith("text/plain")
    assert "0000320187-26-000037" in str(caught.value) and "submissions" not in str(caught.value)
