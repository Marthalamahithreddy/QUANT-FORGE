"""The shared decoder refuses pathologically nested bodies before parsing, on every host.

CPython 3.14 sizes its C recursion guard from the process stack limit, so the same body raises
``RecursionError`` on one host and decodes on another. These tests pin the host-independent rule.
"""

from __future__ import annotations

import json
import random
from decimal import Decimal

import pytest

from quantforge.providers.json_decimal import MAX_JSON_NESTING, decode_json_document


def _nested_lists(depth: int) -> bytes:
    return b"[" * depth + b"1" + b"]" * depth


def test_a_body_at_the_limit_decodes() -> None:
    assert decode_json_document(_nested_lists(MAX_JSON_NESTING)) is not None


def test_a_body_one_level_past_the_limit_is_refused() -> None:
    with pytest.raises(ValueError, match="nests deeper"):
        decode_json_document(_nested_lists(MAX_JSON_NESTING + 1))


@pytest.mark.parametrize(
    "body",
    [
        b"[" * 100_000 + b"]" * 100_000,
        b'{"a":' * 100_000 + b"1" + b"}" * 100_000,
        b'[{"a":' * 50_000 + b"1" + b"}]" * 50_000,
    ],
)
def test_a_pathological_body_is_refused_before_the_json_parser_runs(
    body: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    def must_not_parse(*_a: object, **_k: object) -> object:
        raise AssertionError("the parser must not see a pathologically nested body")

    monkeypatch.setattr(json, "loads", must_not_parse)

    with pytest.raises(ValueError, match="nests deeper"):
        decode_json_document(body)


def test_brackets_inside_strings_are_not_nesting() -> None:
    text = "[" * 500 + '\\"' + "{" * 500 + "\\\\"
    body = json.dumps({"note": text, "rows": [{"v": 1.5}]}).encode()

    assert decode_json_document(body) == {
        "note": json.loads(body)["note"],
        "rows": [{"v": Decimal("1.5")}],
    }


def test_a_wide_shallow_document_is_not_refused() -> None:
    body = b"[" + b",".join(b'{"x":[%d]}' % i for i in range(50_000)) + b"]"

    decoded = decode_json_document(body)

    assert isinstance(decoded, list)
    assert len(decoded) == 50_000


def test_closing_brackets_reduce_depth_so_sequential_siblings_are_fine() -> None:
    body = b"[" + b",".join([_nested_lists(MAX_JSON_NESTING - 1)] * 200) + b"]"

    assert isinstance(decode_json_document(body), list)


def _after_string(string_literal: str, depth: int) -> bytes:
    """A top-level array holding ``string_literal`` followed by a ``depth``-deep nested list."""
    return ("[" + string_literal + ", " + "[" * depth + "1" + "]" * depth + "]").encode()


@pytest.mark.parametrize(
    "string_literal",
    [
        r'"plain"',
        r'"escaped quote \" then [[[["',
        r'"ends with an escaped backslash \\"',
        r'"\\\\"',
        r'"\\\" mixed \\ \" "',
        '"' + "[" * 500 + '"',
    ],
)
def test_string_state_never_hides_or_invents_depth(string_literal: str) -> None:
    # Total depth = 1 (outer array) + n; the limit is MAX_JSON_NESTING.
    assert decode_json_document(_after_string(string_literal, MAX_JSON_NESTING - 1))
    with pytest.raises(ValueError, match="nests deeper"):
        decode_json_document(_after_string(string_literal, MAX_JSON_NESTING))


@pytest.mark.parametrize(
    "encoding",
    ["utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be", "utf-8-sig"],
)
def test_other_json_encodings_get_the_same_boundary(encoding: str) -> None:
    tricky = r'"escaped \" quote and backslash \\ with [[[["'
    ok = _after_string(tricky, MAX_JSON_NESTING - 1).decode().encode(encoding)
    deep = _after_string(tricky, MAX_JSON_NESTING).decode().encode(encoding)

    assert decode_json_document(ok)
    with pytest.raises(ValueError, match="nests deeper"):
        decode_json_document(deep)


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
def test_a_deep_body_in_another_encoding_is_refused_before_the_parser(
    encoding: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def must_not_parse(*_a: object, **_k: object) -> object:
        raise AssertionError("the parser must not see a pathologically nested body")

    monkeypatch.setattr(json, "loads", must_not_parse)
    body = (r'["\" ", ' + "[" * 100_000 + "]" * 100_000 + "]").encode(encoding)

    with pytest.raises(ValueError, match="nests deeper"):
        decode_json_document(body)


@pytest.mark.parametrize(
    "body", [b'["unterminated', b'["bad \\', b"\xff\xfe\xfa not text", b'{"a": "x' + b"[" * 200]
)
def test_malformed_or_unterminated_text_is_a_value_error_never_accepted(body: bytes) -> None:
    with pytest.raises(ValueError):
        decode_json_document(body)


def test_ordinary_provider_shapes_are_unchanged() -> None:
    body = b'{"results":[{"c":199.99,"t":1,"s":"a[b]{c}"}],"status":"OK","next_url":null}'

    assert decode_json_document(body) == {
        "results": [{"c": Decimal("199.99"), "t": 1, "s": "a[b]{c}"}],
        "status": "OK",
        "next_url": None,
    }


def _true_depth(value: object) -> int:
    """Nesting depth of a parsed document, computed iteratively (no recursion)."""
    deepest, stack = 0, [(value, 1)]
    while stack:
        item, level = stack.pop()
        if isinstance(item, dict | list):
            deepest = max(deepest, level)
            children = item.values() if isinstance(item, dict) else item
            stack.extend((child, level + 1) for child in children)
    return deepest


_TRICKY = ['"', "\\", "[", "]", "{", "}", "\n", "\u00e9", "plain", " ", ",", ":", "\\\\", '\\"']


def _random_document(rng: random.Random, depth: int) -> object:
    """A list/dict chain of exactly ``depth`` levels whose strings are full of structural characters."""
    text = "".join(rng.choice(_TRICKY) for _ in range(rng.randint(0, 12)))
    node: object = [text, 1.5]
    for _ in range(depth - 1):
        text = "".join(rng.choice(_TRICKY) for _ in range(rng.randint(0, 6)))
        node = {text: node, "k": [text][0]} if rng.random() < 0.5 else [text, node, text]
    return node


@pytest.mark.parametrize("seed", range(12))
def test_the_guard_agrees_with_true_depth_on_random_documents(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(25):
        depth = rng.choice(
            [
                1,
                2,
                10,
                MAX_JSON_NESTING - 1,
                MAX_JSON_NESTING,
                MAX_JSON_NESTING + 1,
                MAX_JSON_NESTING + 2,
                90,
            ]
        )
        document = _random_document(rng, depth)
        encoding = rng.choice(["utf-8", "utf-16", "utf-16-le", "utf-32-be", "utf-8-sig"])
        body = json.dumps(document, ensure_ascii=rng.random() < 0.5).encode(encoding)
        assert _true_depth(document) == depth
        if depth <= MAX_JSON_NESTING:
            assert decode_json_document(body) is not None
        else:
            with pytest.raises(ValueError, match="nests deeper"):
                decode_json_document(body)
