"""SEC client tests against a mock transport: identity, pacing, retries, raw capture."""

from __future__ import annotations

import json
from typing import Any

import httpx2 as httpx
import pytest

from quantforge.providers.errors import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderNotFoundError,
    ProviderPayloadError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderServerError,
)
from quantforge.providers.sec.client import SecClient, SecConfig, normalise_cik

_UA = "QuantForgeTest tester@example.com"

_BODY = json.dumps(
    {
        "cik": "320193",
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-24-000010"],
                "form": ["10-Q"],
                "filingDate": ["2024-05-03"],
                "reportDate": ["2024-03-30"],
                "acceptanceDateTime": ["2024-05-03T16:30:22.000Z"],
                "primaryDocument": ["q.htm"],
                "items": [""],
            }
        },
    }
).encode()


async def _no_sleep(_delay: float) -> None:
    return None


def _client(handler: Any, *, user_agent: str | None = _UA, **config: Any) -> SecClient:
    return SecClient(
        SecConfig(user_agent=user_agent, base_url="https://data.sec.gov", **config),
        transport=httpx.MockTransport(handler),
        sleep=_no_sleep,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [("320193", "0000320193"), ("0000320193", "0000320193"), (" 5 ", "0000000005")],
)
def test_cik_is_zero_padded(value: str, expected: str) -> None:
    assert normalise_cik(value) == expected


@pytest.mark.parametrize("value", ["", "abc", "12345678901", "0", "0000000000", "-1"])
def test_a_bad_cik_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="CIK"):
        normalise_cik(value)


@pytest.mark.anyio
async def test_the_declared_user_agent_is_sent_and_the_path_is_the_padded_cik() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=_BODY)

    result = await _client(handler).get_filings("320193")

    assert seen[0].headers["User-Agent"] == _UA
    assert seen[0].url.path == "/submissions/CIK0000320193.json"
    assert [f.accession_number for f in result.filings] == ["0000320193-24-000010"]
    assert result.rejected == ()


@pytest.mark.anyio
@pytest.mark.parametrize("agent", [None, "", "   ", "QuantForge"])
async def test_no_request_is_made_without_a_declared_contact(agent: str | None) -> None:
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, content=_BODY)

    with pytest.raises(ProviderConfigurationError, match="QF_SEC_USER_AGENT"):
        await _client(handler, user_agent=agent).get_filings("320193")

    assert calls == []


@pytest.mark.anyio
async def test_the_capture_holds_the_exact_bytes_and_no_identity() -> None:
    client = _client(lambda _r: httpx.Response(200, content=_BODY))

    result = await client.get_filings("320193")

    capture = result.captures[0]
    assert capture.provider == "sec-edgar"
    assert capture.dataset == "submissions"
    assert capture.endpoint == "/submissions/CIK{cik}.json"
    assert capture.body == _BODY
    assert dict(capture.request_parameters) == {"cik": "0000320193"}
    assert "tester@example.com" not in json.dumps(dict(capture.request_parameters))


def test_the_config_repr_never_prints_the_identity() -> None:
    config = SecConfig(user_agent=_UA, base_url="https://data.sec.gov")

    assert "tester@example.com" not in repr(config)
    assert "<set>" in repr(config)


@pytest.mark.anyio
async def test_a_404_is_a_not_found_answer() -> None:
    with pytest.raises(ProviderNotFoundError):
        await _client(lambda _r: httpx.Response(404)).get_filings("320193")


@pytest.mark.anyio
async def test_a_403_is_a_configuration_problem_and_is_never_retried() -> None:
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(403)

    with pytest.raises(ProviderAuthenticationError, match="QF_SEC_USER_AGENT"):
        await _client(handler, max_retries=3).get_filings("320193")

    assert calls == [1]


@pytest.mark.anyio
async def test_a_429_is_retried_within_a_small_budget_then_surfaced_as_rate_limiting() -> None:
    calls: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "1"})

    with pytest.raises(ProviderRateLimitError):
        await _client(handler, max_retries=2).get_filings("320193")

    assert len(calls) <= 3, "no retry storm"


@pytest.mark.anyio
async def test_a_transient_server_error_is_retried_then_succeeds() -> None:
    responses = iter([httpx.Response(503), httpx.Response(200, content=_BODY)])

    result = await _client(lambda _r: next(responses)).get_filings("320193")

    assert len(result.filings) == 1


@pytest.mark.anyio
async def test_a_persistent_server_error_is_surfaced() -> None:
    with pytest.raises(ProviderServerError):
        await _client(lambda _r: httpx.Response(503), max_retries=1).get_filings("320193")


@pytest.mark.anyio
async def test_non_json_is_a_response_error() -> None:
    with pytest.raises(ProviderResponseError):
        await _client(lambda _r: httpx.Response(200, content=b"<html>blocked</html>")).get_filings(
            "320193"
        )


@pytest.mark.anyio
async def test_rejected_records_are_reported_not_dropped_silently() -> None:
    bad = json.loads(_BODY)
    bad["filings"]["recent"]["filingDate"] = ["not-a-date"]

    result = await _client(
        lambda _r: httpx.Response(200, content=json.dumps(bad).encode())
    ).get_filings("320193")

    assert result.filings == []
    assert len(result.rejected) == 1


@pytest.mark.anyio
async def test_a_response_for_a_different_registrant_is_refused() -> None:
    wrong = json.loads(_BODY)
    wrong["cik"] = "789019"

    with pytest.raises(ProviderPayloadError, match="wrong registrant"):
        await _client(
            lambda _r: httpx.Response(200, content=json.dumps(wrong).encode())
        ).get_filings("320193")


@pytest.mark.anyio
async def test_a_mapping_failure_still_carries_the_raw_response_as_evidence() -> None:
    body = b'{"cik": "320193", "filings": {}}'

    with pytest.raises(ProviderPayloadError) as caught:
        await _client(lambda _r: httpx.Response(200, content=body)).get_filings("320193")

    assert [c.body for c in caught.value.captures] == [body]
    assert "tester@example.com" not in json.dumps(
        [dict(c.request_parameters) for c in caught.value.captures]
    )


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("QF_SEC_MIN_REQUEST_INTERVAL_SECONDS", "0"),
        ("QF_SEC_MIN_REQUEST_INTERVAL_SECONDS", "-1"),
        ("QF_SEC_MIN_REQUEST_INTERVAL_SECONDS", "inf"),
        ("QF_SEC_MAX_RETRIES", "-1"),
        ("QF_SEC_MAX_RETRIES", "50"),
        ("QF_SEC_REQUEST_TIMEOUT_SECONDS", "0"),
    ],
)
def test_the_pacing_floor_cannot_be_configured_away(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    from pydantic import ValidationError  # noqa: PLC0415

    from quantforge.core.config import Settings  # noqa: PLC0415

    monkeypatch.setenv(variable, value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.anyio
async def test_an_undecodable_200_still_carries_its_bytes_as_evidence() -> None:
    body = b"<html>Request Rate Threshold Exceeded</html>"

    with pytest.raises(ProviderResponseError) as caught:
        await _client(lambda _r: httpx.Response(200, content=body)).get_filings("320193")

    assert [c.body for c in caught.value.captures] == [body]


def test_the_client_enforces_the_pacing_floor_itself() -> None:
    config = SecConfig(user_agent=_UA, base_url="https://data.sec.gov", min_request_interval=0)

    assert config.min_request_interval >= 0.2


@pytest.mark.parametrize("agent", ["Müller App ops@example.com", "App ops@example.com\nX-Evil: 1"])
def test_a_non_ascii_or_control_character_identity_is_a_configuration_error(agent: str) -> None:
    config = SecConfig(user_agent=agent, base_url="https://data.sec.gov")

    with pytest.raises(ProviderConfigurationError, match="printable ASCII"):
        config.require_user_agent()


@pytest.mark.anyio
async def test_a_pathologically_nested_body_is_a_response_error_with_evidence() -> None:
    body = b"[" * 100_000 + b"]" * 100_000

    with pytest.raises(ProviderResponseError) as caught:
        await _client(lambda _r: httpx.Response(200, content=body)).get_filings("320193")

    assert [c.body for c in caught.value.captures] == [body]


def test_a_stale_low_pacing_value_does_not_stop_settings_loading_but_the_client_clamps_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from quantforge.core.config import Settings  # noqa: PLC0415

    monkeypatch.setenv("QF_SEC_MIN_REQUEST_INTERVAL_SECONDS", "0.1")

    settings = Settings(_env_file=None)

    assert settings.sec_min_request_interval_seconds == 0.1
    config = SecConfig(
        user_agent=_UA,
        base_url="https://data.sec.gov",
        min_request_interval=settings.sec_min_request_interval_seconds,
    )
    assert config.min_request_interval == 0.2
