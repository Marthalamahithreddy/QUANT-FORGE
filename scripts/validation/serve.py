"""Serve the real API against the isolated validation database (no fixtures). Prints no credential.

Usage (repo root): uv run --project apps/api python scripts/validation/serve.py [port]
"""

from __future__ import annotations

import json
import os
import sys

import common
import uvicorn


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    os.environ["QF_DATABASE_URL"] = common.validation_settings().database_url
    web_port = os.environ.get("QF_WEB_PORT", "3101")
    os.environ["QF_CORS_ORIGINS"] = json.dumps(
        [f"http://127.0.0.1:{web_port}", f"http://localhost:{web_port}"]
    )
    from quantforge.core.config import get_settings

    get_settings.cache_clear()  # validation_settings() cached the .env settings before the override
    uvicorn.run("quantforge.main:app", host="127.0.0.1", port=port, log_level=os.environ.get("VALIDATION_LOG_LEVEL", "warning"))


if __name__ == "__main__":
    main()
