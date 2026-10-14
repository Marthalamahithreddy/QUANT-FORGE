"""Decimal-exact JSON decoding.

The problem
-----------
``json.loads(b'{"close": 199.99}')`` yields a Python ``float``, because JSON
numbers with a fractional part become IEEE-754 doubles. ``Decimal(199.99)``
constructed *from that float* is not ``Decimal("199.99")``; it is
``Decimal('199.99000000000000284217094304040074348449707031250')``. Storing
that as a ``NUMERIC(20, 6)`` column would round back to a clean
``199.990000``, hiding the damage from a comparison but permanently poisoning
any arithmetic done in Python first, and any value that did not round cleanly
would store wrong digits.

The fix is to never create the float. ``json.loads`` accepts a ``parse_float``
hook that receives the *literal text* of each fractional number, so
``parse_float=Decimal`` produces ``Decimal("199.99")`` straight from the source
characters. There is no float anywhere in the chain, so there is nothing to
round.

Integer JSON numbers stay ``int``: a share count is genuinely integral, and
keeping it integral avoids a gratuitous conversion. The scale of a price is a
storage decision made by the ``NUMERIC`` column, not something to be invented
here, so ``Decimal("199.99")`` is stored as ``199.990000`` by the database
exactly once, deterministically.

Non-finite values
-----------------
``json`` accepts the JavaScript-only literals ``NaN``, ``Infinity`` and
``-Infinity`` by default, and they have no meaning in a price feed. They are
rejected here rather than being allowed to reach a ``Decimal``, which cannot
represent them.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any, cast

__all__ = [
    "MAX_JSON_NESTING",
    "JsonValue",
    "decode_json_document",
    "reject_non_finite",
]

#: A decoded JSON document. ``Decimal`` appears in the union because of
#: ``parse_float=Decimal``; this alias exists so downstream type checkers do not
#: have to repeat it.
JsonValue = None | bool | int | Decimal | str | list["JsonValue"] | dict[str, "JsonValue"]


#: Deepest array/object nesting a provider body may have. Real provider documents are a handful of
#: levels deep; the bound exists so a hostile body is refused the same way on every host.
MAX_JSON_NESTING = 64

# A string literal (so brackets inside text are not counted), or one structural bracket.
_TOKEN = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"|[\[\]{}]')


def _exceeds_nesting(text: str, limit: int) -> bool:
    """True when ``text`` nests arrays/objects deeper than ``limit`` (a scan, not a parse).

    CPython's JSON scanner recurses once per level. Whether a very deep body raises
    ``RecursionError`` or decodes depends on the interpreter's C-stack allowance, which differs
    between hosts (on 3.14 it follows the process stack limit). Counting depth first makes the
    refusal deterministic and keeps the decoder from consuming unbounded stack.

    The scan runs on decoded TEXT, never raw bytes: ``json.loads`` also accepts UTF-16/32 input,
    where an escaped quote is not the two bytes ``\\"`` and a byte-level scan would lose track of
    string state. Strings are consumed whole, so a bracket inside one is never counted; for any
    document the parser accepts the tokenisation is exact.
    """
    if text.count("[") + text.count("{") <= limit:
        return False  # too few opening brackets to reach the limit, strings included
    depth = 0
    for token in _TOKEN.finditer(text):
        first = token.group()[0]
        if first in "[{":
            depth += 1
            if depth > limit:
                return True
        elif first in "]}":
            depth -= 1
    return False


def reject_non_finite(literal: str) -> Any:
    """``parse_constant`` hook that refuses ``NaN``/``Infinity`` outright.

    ``json`` calls this for the bare JavaScript constants, passing their source
    text. Any of them appearing in a provider payload is a contract violation:
    PostgreSQL ``NUMERIC`` cannot hold them, and silently substituting zero
    would fabricate a price.
    """
    message = f"non-finite JSON number {literal!r} is not a valid provider value"
    raise ValueError(message)


def decode_json_document(raw: bytes) -> JsonValue:
    """Decode provider bytes with exact decimals and no non-finite values.

    Args:
        raw: The response body exactly as received.

    Returns:
        The decoded document, where every fractional JSON number is a
        ``Decimal`` built from its own source text.

    Raises:
        ValueError: If the body is not valid JSON, nests deeper than
            ``MAX_JSON_NESTING``, or contains ``NaN``,
            ``Infinity`` or ``-Infinity``. Callers translate this into a
            :class:`~quantforge.providers.errors.ProviderResponseError`.
    """
    if not raw.strip():
        message = "empty body"
        raise ValueError(message)
    # Decode exactly as json.loads(bytes) would (encoding detection included), so the depth check
    # and the parser always see the same characters.
    try:
        text = raw.decode(json.detect_encoding(raw), "surrogatepass")
    except UnicodeDecodeError as exc:
        message = "body is not decodable JSON text"
        raise ValueError(message) from exc
    if _exceeds_nesting(text, MAX_JSON_NESTING):
        message = f"JSON nests deeper than {MAX_JSON_NESTING} levels"
        raise ValueError(message)
    # parse_float receives the literal substring, so the decimal is constructed
    # from the characters the provider sent rather than from a float round-trip.
    # json.loads is annotated to return Any; the cast states the guarantee this
    # function actually makes, which is that every fractional number is a Decimal.
    return cast(
        "JsonValue",
        json.loads(
            text,
            parse_float=Decimal,
            parse_int=int,
            parse_constant=reject_non_finite,
        ),
    )
