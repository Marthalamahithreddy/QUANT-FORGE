"""Fetch ONE official EDGAR SGML header (no ingestion) and print its acceptance time.

Usage (repo root): uv run --project apps/api python scripts/validation/sec_header_probe.py CIK ACCESSION
Counts against the per-step ledger (VALIDATION_LEDGER / VALIDATION_CAP). The contact identity is
sent as the User-Agent and is never printed or stored.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import sys

import common
import httpx2 as httpx
from quantforge.core.config import get_settings


def header_url(cik: str, accession: str) -> str:
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"
        f"{accession}.hdr.sgml"
    )


async def main(cik: str, accession: str) -> None:
    ua = get_settings().sec_user_agent.get_secret_value()
    async with httpx.AsyncClient(
        transport=common.CountingTransport("sec"), headers={"User-Agent": ua}, timeout=60.0
    ) as client:
        r = await client.get(header_url(cik, accession))
    print("HTTP", r.status_code, "bytes", len(r.content), "sha256", hashlib.sha256(r.content).hexdigest())
    (common.ART / f"sec-header-{accession}.hdr.sgml").write_bytes(r.content)
    for tag in ("ACCESSION-NUMBER", "ACCEPTANCE-DATETIME"):
        m = re.search(rf"<{tag}>\s*([^\s<]+)", r.text)
        print(tag, m.group(1) if m else None)
    print("ledger:", {k: v for k, v in common.ledger().items() if k != "requests"})


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
