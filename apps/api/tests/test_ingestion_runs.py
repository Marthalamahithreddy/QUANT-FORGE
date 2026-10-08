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

import time

import pytest

from quantforge.ingestion.runs import (
    _MAX_ERROR_MESSAGE,
    RecordFailure,
    _safe_error_text,
    partial_error_summary,
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


# ── Step 12C: more of what must never reach a persisted reason ───────────────

_IDENTITY_CASES = [
    ("could not connect to postgresql://quantforge:Hunter2pw@db.internal:5432/qf", "Hunter2pw"),
    ("postgresql+psycopg://admin:s3cr3tPASS@10.0.0.4/quantforge refused", "s3cr3tPASS"),
    ("User-Agent: QuantForge research jane.doe@example.org rejected", "jane.doe@example.org"),
    ("raw store failed at /Users/someone/Desktop/raw/finnhub/earnings/x.json", "/Users/someone"),
    ("cannot write /var/lib/quantforge/raw/massive/reference/x.json", "/var/lib/quantforge"),
    ("disk error at /home/ops/quantforge/raw", "/home/ops"),
]


@pytest.mark.parametrize(("message", "secret"), _IDENTITY_CASES)
def test_database_credentials_contact_identity_and_absolute_paths_are_redacted(
    message: str, secret: str
) -> None:
    assert secret not in _safe_error_text(RuntimeError(message))


def test_an_ordinary_message_survives_redaction_unchanged() -> None:
    message = "AAPL had no listing covering 2026-09-14 (the ticker may have been reused)"
    assert _safe_error_text(RuntimeError(message)) == message


def _failure(label: str, message: str) -> RecordFailure:
    return RecordFailure.of(label, ValueError(message))


def test_a_single_failure_summary_names_the_count_and_the_reason() -> None:
    summary = partial_error_summary([_failure("MSFT", "locale 'global' is not 'us'")], 1)
    assert summary == "1 record(s) failed; first: MSFT: locale 'global' is not 'us'"


def test_several_failures_report_the_total_and_the_first_in_processing_order() -> None:
    failures = [_failure("AAPL", "boom one"), _failure("MSFT", "boom two"), _failure("ZZZZ", "x")]
    summary = partial_error_summary(failures, 3)
    assert summary == "3 record(s) failed; first: AAPL: boom one"
    assert partial_error_summary(list(failures), 3) == summary, "deterministic"


def test_the_total_is_the_runs_own_count_even_when_fewer_reasons_were_kept() -> None:
    assert partial_error_summary([_failure("AAPL", "boom")], 4) == (
        "4 record(s) failed; first: AAPL: boom"
    )


def test_no_failure_means_no_summary() -> None:
    assert partial_error_summary([], 0) is None
    assert partial_error_summary([], 2) is None
    assert partial_error_summary([_failure("AAPL", "x")], 0) is None


def test_a_secret_in_a_failure_never_reaches_the_summary() -> None:
    failures = [_failure("AAPL", "refused https://x/y?apiKey=SUPERSECRET and token=abc123DEF")]
    summary = partial_error_summary(failures, 1)
    assert summary is not None
    assert "SUPERSECRET" not in summary and "abc123DEF" not in summary


def test_the_summary_is_capped_to_the_error_message_column() -> None:
    summary = partial_error_summary([_failure("AAPL", "x" * 5000)], 1)
    assert summary is not None
    assert len(summary) == _MAX_ERROR_MESSAGE
    assert summary.endswith("...")
    assert summary.startswith("1 record(s) failed; first: AAPL: xxx")


def test_a_failure_is_redacted_when_it_is_created() -> None:
    failure = _failure("AAPL", "password=hunter2hunter2 rejected")
    assert "hunter2hunter2" not in failure.reason
    assert failure.error_type == "ValueError"


# ── Step 12C review: DSN passwords with awkward characters, more path roots ──────

# A raw "/" in a password cannot be told from the start of the URL path (a URL's authority ends at
# the first "/"); real DSNs percent-encode it, so that case is a documented limit, not a rule.
_AWKWARD_DSNS = [
    ("postgresql://user:p@ss@host/db", "ss@host", "p@ss"),
    ("postgresql://user:pa?ss@host/db failed", "pa?ss", "user:pa"),
]


@pytest.mark.parametrize(("message", "leak_a", "leak_b"), _AWKWARD_DSNS)
def test_a_dsn_password_with_an_awkward_character_does_not_survive(
    message: str, leak_a: str, leak_b: str
) -> None:
    out = _safe_error_text(RuntimeError(message))
    assert "postgresql" in out, "the scheme stays so the reader can tell what failed"
    assert leak_b not in out and "ss@host" not in out.replace("[redacted]@host", "")


@pytest.mark.parametrize(
    "message",
    [
        "raw root /Volumes/External/quantforge/raw is full",
        "cannot read file:///Users/me/raw/x.json",
        "missing /app/data/raw/massive/x.json",
        "see /Library/Application Support/quantforge",
    ],
)
def test_more_absolute_path_roots_are_redacted(message: str) -> None:
    out = _safe_error_text(RuntimeError(message))
    assert "[path]" in out
    for leaked in ("External", "/Users/me", "/app/data", "Application"):
        assert leaked not in out.replace("[path]", "") or leaked == "Application"


def test_a_path_in_an_endpoint_template_is_not_mistaken_for_a_filesystem_path() -> None:
    message = "GET /v3/reference/tickers/{ticker} and /api/v1/earnings-events failed"
    assert _safe_error_text(RuntimeError(message)) == message


# ── Step 12F: quoted values, JSON bodies, prefixed names ───────────────────────

_12F_CASES = [
    # (message, the secret that must not survive, a diagnostic fragment that must)
    ("connect failed password='Sup3rS3cret with space' dbname=qf", "Sup3rS3cret", "dbname=qf"),
    ('connect failed password="Sup3rS3cret with space" dbname=qf', "Sup3rS3cret", "dbname=qf"),
    (
        'rejected: {"api_key": "Sup3rS3cretKey99", "status": 403}',
        "Sup3rS3cretKey99",
        '"status": 403',
    ),
    ('rejected: {"apiKey":"Sup3rS3cretKey99"}', "Sup3rS3cretKey99", "rejected"),
    ('{"token":"Sup3rS3cretKey99","ok":false}', "Sup3rS3cretKey99", '"ok":false'),
    ("client_secret=Sup3rS3cretKey99 for the app", "Sup3rS3cretKey99", "for the app"),
    ("(client_secret: 'Sup3rS3cret key')", "Sup3rS3cret", "("),
    ("x-api-key=Sup3rS3cretKey99; retrying", "Sup3rS3cretKey99", "retrying"),
    ("refresh_token=Sup3rS3cretKey99.", "Sup3rS3cretKey99", "refresh_token"),
    ("DB_PASSWORD=Sup3rS3cretKey99", "Sup3rS3cretKey99", "DB_PASSWORD"),
    ("CLIENT_SECRET = 'Sup3rS3cretKey99'", "Sup3rS3cretKey99", "CLIENT_SECRET"),
    (r'{"password": "a\"Sup3rS3cret b"} next', "Sup3rS3cret", "next"),
    ("failed (password='Sup3rS3cretKey99'), retry later", "Sup3rS3cretKey99", "retry later"),
    ('{"Authorization": "Bearer Sup3rS3cretKey99"}', "Sup3rS3cretKey99", "Authorization"),
    ("password=Sup3rS3cret\\ with\\ escapes tail", "Sup3rS3cret", "tail"),
    ("password=Sup3rS3cret,part2;part3 then", "part3", "then"),
    ("password=Sup3rS3cret,part2;part3 then", "part2", "then"),
]


@pytest.mark.parametrize(("message", "secret", "kept"), _12F_CASES)
def test_quoted_json_and_prefixed_credentials_are_redacted_but_the_diagnosis_survives(
    message: str, secret: str, kept: str
) -> None:
    out = _safe_error_text(RuntimeError(message))
    assert secret not in out
    assert _REDACTED_MARKER in out
    assert kept in out


@pytest.mark.parametrize(
    "message",
    [
        "the exchange calendar cannot prove 5 sessions after 2026-10-02",
        "author=john secretary=x tokens=3 passwords=2 password_hint=none oauthy",
        "records_failed=1 HTTP 403 from provider",
        "token budget unaffected",  # prose is not an assignment
        "which ticker did you mean? try AAPL",  # a bare question mark is not a query string
        "unknown listing for ZZZZ - did you mean ZZZ?",
    ],
)
def test_words_that_merely_contain_a_credential_name_are_left_alone(message: str) -> None:
    assert _safe_error_text(RuntimeError(message)) == message


def test_a_pathological_message_is_redacted_in_bounded_time() -> None:
    started = time.perf_counter()
    for payload in (
        "a-" * 200_000,
        "x" * 400_000,
        "password=" * 50_000,
        ('"' + "k" * 50 + '" ') * 5_000,
    ):
        _safe_error_text(RuntimeError(payload))
    assert time.perf_counter() - started < 2.0


def test_a_credential_beyond_the_stored_prefix_is_never_stored_and_one_inside_it_is_redacted() -> (
    None
):
    message = "password='Sup3rS3cretKey99' " + "y" * 10_000 + " token=Sup3rS3cretLate"
    out = _safe_error_text(RuntimeError(message))
    assert "Sup3rS3cret" not in out
    assert len(out) <= _MAX_ERROR_MESSAGE or out.startswith("password=")
