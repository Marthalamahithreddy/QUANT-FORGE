"""Fetch ONE official SEC URL through the counting transport and save the bytes under artifacts/.

Usage: VALIDATION_LEDGER=ledger-X.json VALIDATION_CAP=12 uv run --project apps/api python \
    scripts/validation/sec_fetch.py URL OUTNAME
The contact identity is sent as the User-Agent and never printed or stored.
"""

from __future__ import annotations

import asyncio
import hashlib
import sys

import common
import httpx2 as httpx
from quantforge.core.config import get_settings


async def main(url: str, name: str) -> None:
    ua = get_settings().sec_user_agent.get_secret_value()
    async with httpx.AsyncClient(
        transport=common.CountingTransport("sec"), headers={"User-Agent": ua}, timeout=120.0
    ) as client:
        r = await client.get(url)
    print("HTTP", r.status_code, "bytes", len(r.content), "sha256", hashlib.sha256(r.content).hexdigest())
    (common.ART / name).write_bytes(r.content)
    led = common.ledger()
    print("ledger:", led["total"], "/", led["cap"])


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
