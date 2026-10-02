"""Credential redaction for the run recorder -- the half that needs no database.

A run row outlives the process that wrote it and is not scrubbed by an API-key
rotation, so a credential that reaches it is effectively permanent. Redaction
therefore happens *before* the length limit is applied: truncating a URL that
opens with ``?apiKey=`` keeps the key and discards only the part that would have
identified it as sensitive.

The rest of the recorder -- run identity, manifests, terminal states, and the
assertion that a redacted message is what actually lands in the column -- needs
PostgreSQL and lives in ``tests/integration/test_ingestion_runs.py``.
"""

from __future__ import annotations

import pytest

from quantforge.ingestion.runs import (
    _MAX_ERROR_MESSAGE,
    _safe_error_text,
)
from quantforge.ingestion.runs import (
    _REDACTED as _REDACTED_MARKER,
)

_REDACTED_QUERY_SUFFIX = f"?{_REDACTED_MARKER}"

_CASES = [
    # (message, the secret that must not survive)
    ("401 from https://api.massive.com/v3/x?apiKey=SUPERSECRET123", "SUPERSECRET123"),
    ("refused https://api.massive.com/v2/y?token=abc123DEF", "abc123DEF"),
    ("api_key=deadbeefcafe rejected", "deadbeefcafe"),
    ("Authorization=Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig", "eyJhbGciOiJIUzI1NiJ9"),
    ("headers {'Authorization': 'Bearer sk-live-XYZQRS123'}", "sk-live-XYZQRS123"),
    ("Basic dXNlcjpwYXNzd29yZDEyMw== denied", "dXNlcjpwYXNzd29yZDEyMw"),
    ("Client error 401 for https://api.massive.com/v2/aggs?apikey=QQQQ&limit=5", "QQQQ"),
]


@pytest.mark.parametrize(("message", "secret"), _CASES)
def test_a_credential_in_an_exception_message_is_redacted(message: str, secret: str) -> None:
    assert secret not in _safe_error_text(RuntimeError(message))


_URL_CASES = [(message, secret) for message, secret in _CASES if "?" in message]


@pytest.mark.parametrize(("message", "secret"), _URL_CASES)
def test_every_query_parameter_goes_not_just_the_recognised_one(message: str, secret: str) -> None:
    """The whole query string is dropped, not just the parameter we recognised.

    An allowlist of "safe" parameters would be one edit away from leaking, and no
    parameter of a failed request is needed to diagnose it. Note the *path*
    survives, because that is what identifies which call failed.
    """
    out = _safe_error_text(RuntimeError(message))
    assert out.endswith(_REDACTED_QUERY_SUFFIX) or "?limit=5" not in out
    for fragment in ("?apikey=", "?apiKey=", "?token=", "?limit="):
        assert fragment not in out
    # The path is the part worth keeping.
    assert "/v2/aggs" in out or "/v3/" in out or "/v2/y" in out


# Note the intended shape for a credential that is *not* in a URL: the parameter
# name survives so an operator can see which credential was rejected, and only the
# value is replaced -- "api_key=[redacted] rejected". Asserted here so a future
# change that erases the whole assignment is visibly a change.
def test_a_credential_outside_a_url_keeps_its_name_but_not_its_value() -> None:
    out = _safe_error_text(RuntimeError("api_key=deadbeefcafe rejected"))
    assert out == "api_key=[redacted] rejected"


def test_a_bearer_token_is_redacted_before_the_assignment_rule_runs() -> None:
    """Order matters, and the wrong order passes a naive test.

    Applying ``Authorization=<value>`` first consumes the bare word ``Bearer`` as
    the value, orphaning the token that follows it into a message that now *looks*
    redacted and is not.
    """
    out = _safe_error_text(RuntimeError("Authorization=Bearer sk-live-SECRETVALUE"))
    assert "sk-live-SECRETVALUE" not in out
    assert "SECRET" not in out


def test_a_credential_in_a_long_message_is_still_redacted() -> None:
    """A message long enough to be truncated is exactly where this goes wrong.

    A naive implementation truncates first and keeps the prefix -- which is where
    the ``?apiKey=`` usually sits.
    """
    message = "GET https://api.massive.com/v3/reference?apiKey=LEAKME99 " + "x" * 4000
    assert "LEAKME99" not in _safe_error_text(RuntimeError(message))


def test_a_harmless_message_is_stored_unchanged() -> None:
    """Redaction must not destroy the diagnostic value of an ordinary message.

    Over-redaction is its own failure: an operator who cannot read the error cannot
    act on it.
    """
    text = "provider returned 503 for daily-bars; retried 3 times"
    assert _safe_error_text(RuntimeError(text)) == text


def test_a_non_string_exception_is_handled() -> None:
    """Some exceptions stringify to nothing useful; that must not crash the run."""
    assert _safe_error_text(ValueError()) == ""
    assert _safe_error_text(RuntimeError("   ")) == ""


def test_the_column_limit_is_the_documented_bound() -> None:
    """Guard the constant, so a future column change has to be deliberate."""
    assert _MAX_ERROR_MESSAGE == 1000
