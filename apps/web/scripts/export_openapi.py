"""Print the QuantForge API's OpenAPI document as deterministic JSON.

Run through the backend's own environment (see scripts/openapi.mjs):

    uv run --frozen --project ../api python scripts/export_openapi.py

The document comes from ``create_app().openapi()``: nothing is hand-written. To make the bytes
independent of the machine, every QF_* environment variable and any .env file is ignored (the
application defaults are used), keys are sorted, and no timestamp, path or hostname is emitted.
"""

from __future__ import annotations

import json
import os
import sys

for key in [name for name in os.environ if name.startswith("QF_")]:
    del os.environ[key]

from quantforge.core.config import Settings  # noqa: E402  (after the environment is cleaned)
from quantforge.main import create_app  # noqa: E402

document = create_app(Settings(_env_file=None)).openapi()  # type: ignore[call-arg]
sys.stdout.write(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
