"""One canonical serialisation and SHA-256 fingerprint for reproducibility (Step 10.9A).

A deliberately small, deterministic JSON form -- NOT RFC 8785 / JCS (no claim of interoperability
with other canonicalisers); it only has to be stable within QuantForge:

* UTF-8, ``ensure_ascii``, no insignificant whitespace, object keys sorted, ``allow_nan=False``.
* ``Decimal`` -> fixed-point text (``format(value, "f")``): fractional scale is kept, so ``1.50`` and
  ``1.5`` are different canonical values (stored scale is domain data; existing API/fact semantics).
  A positive exponent renders as plain integer text (``1E+2`` -> ``100``) and ``-0`` stays ``-0``;
  PostgreSQL NUMERIC never yields either, so no stored value is affected. Non-finite decimals are
  rejected.
* ``datetime`` -> timezone-aware only, converted to UTC, ``YYYY-MM-DDTHH:MM:SS.ffffffZ``; a naive
  datetime is rejected (never reinterpreted); ``date`` -> ISO; enums -> their value; ``UUID`` ->
  canonical lowercase hyphenated text; dataclasses -> their fields (declaration names, sorted by
  key); tuples/lists keep their order (callers sort where order is semantically irrelevant).
* ``float`` is rejected everywhere. Unknown object types are rejected (no ``repr``, no ``id``).

No clock, locale, host timezone, process state or ordering of a dict/set influences the bytes.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Final

__all__ = [
    "FINGERPRINT_ALGORITHM",
    "Fingerprint",
    "canonical_bytes",
    "canonical_text",
    "canonical_value",
    "fingerprint_of",
    "fingerprint_of_text",
]

FINGERPRINT_ALGORITHM: Final = "sha256"
_SHA256_HEX_LENGTH: Final = 64


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """A digest together with the algorithm that produced it."""

    algorithm: str
    digest: str

    def __post_init__(self) -> None:
        if self.algorithm != FINGERPRINT_ALGORITHM or len(self.digest) != _SHA256_HEX_LENGTH:
            message = "only a 64-hex-digit sha256 fingerprint is supported"
            raise ValueError(message)
        int(self.digest, 16)


def canonical_value(value: object) -> Any:  # noqa: PLR0911, PLR0912 - one flat type switch
    """The JSON-compatible canonical form of ``value`` (see the module docstring)."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, Enum):
        return canonical_value(value.value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        message = "float is not allowed in a canonical representation"
        raise TypeError(message)
    if isinstance(value, Decimal):
        if not value.is_finite():
            message = "a non-finite Decimal has no canonical representation"
            raise ValueError(message)
        return format(value, "f")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            message = "a naive datetime has no canonical representation"
            raise ValueError(message)
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: canonical_value(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                message = "canonical objects have string keys only"
                raise TypeError(message)
            out[key] = canonical_value(item)
        return out
    if isinstance(value, (list, tuple)):
        return [canonical_value(item) for item in value]
    message = f"no canonical representation for {type(value).__name__}"
    raise TypeError(message)


def canonical_bytes(value: object) -> bytes:
    """The canonical UTF-8 bytes of ``value``."""
    return json.dumps(
        canonical_value(value),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def canonical_text(value: object) -> str:
    """The canonical form as text (ASCII-only JSON, so UTF-8 bytes == the text)."""
    return canonical_bytes(value).decode("ascii")


def fingerprint_of_text(text: str) -> Fingerprint:
    """SHA-256 over already-canonical text, exactly as stored."""
    return Fingerprint(FINGERPRINT_ALGORITHM, hashlib.sha256(text.encode("utf-8")).hexdigest())


def fingerprint_of(value: object) -> Fingerprint:
    """SHA-256 over the canonical bytes of ``value``."""
    return Fingerprint(FINGERPRINT_ALGORITHM, hashlib.sha256(canonical_bytes(value)).hexdigest())
