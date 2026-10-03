"""SEC identifier normalisation shared by the submissions client and the FSDS adapter."""

from __future__ import annotations

import re

__all__ = ["normalise_cik"]

_CIK_DIGITS = re.compile(r"^[0-9]{1,10}$")


def normalise_cik(value: str) -> str:
    """Return ``value`` as a zero-padded 10-digit CIK, or raise ``ValueError``."""
    candidate = value.strip()
    if not _CIK_DIGITS.fullmatch(candidate):
        message = f"CIK must be 1-10 digits, got {value!r}"
        raise ValueError(message)
    padded = candidate.zfill(10)
    if int(padded) == 0:
        message = "CIK must not be zero"
        raise ValueError(message)
    return padded
