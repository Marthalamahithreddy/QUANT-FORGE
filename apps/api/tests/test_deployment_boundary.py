"""Step 12E: the properties of the serving process that the deployment boundary relies on.

The API is deployed WITHOUT authentication, behind a private network or reverse proxy (see the runbook's
"deployment constraints"). That is only safe while the process (1) cannot write to anything, (2) cannot
reach a provider, and (3) says nothing about its environment. These tests pin those three properties, so
a later change that adds a write route, a provider call on a request path, or a verbose error cannot
pass unnoticed.
"""

from __future__ import annotations

import json
import subprocess
import sys

from fastapi.testclient import TestClient

from quantforge.api.v1.ready import check_database
from quantforge.core.config import Settings
from quantforge.main import create_app

_SERVE = """
import json, sys
from fastapi.testclient import TestClient
from quantforge.core.config import Settings
from quantforge.main import create_app

application = create_app(Settings(_env_file=None))
with TestClient(application) as client:
    for path in ("/health", "/api/v1/health/ready", "/api/v1/earnings-events?limit=1", "/openapi.json"):
        client.get(path)
print(json.dumps(sorted(m for m in sys.modules if m.startswith(("quantforge.providers", "quantforge.ingestion")))))
"""


def test_every_route_the_api_serves_is_a_read() -> None:
    """No write method is advertised, so no write endpoint can exist without this test changing."""
    schema = create_app(Settings(_env_file=None)).openapi()
    methods = {method.upper() for item in schema["paths"].values() for method in item}
    assert methods == {"GET"}
    assert len(schema["paths"]) >= 10  # the surface really was inspected


def test_write_methods_are_refused_on_every_path() -> None:
    application = create_app(Settings(_env_file=None))
    paths = [
        path.replace("{event_id}", "x").replace("{record_id}", "x").replace("{accession}", "x")
        for path in application.openapi()["paths"]
    ]
    with TestClient(application, raise_server_exceptions=False) as client:
        for path in paths:
            for method in ("post", "put", "patch", "delete"):
                response = getattr(client, method)(path)
                assert response.status_code == 405, (method, path, response.status_code)
                assert "GET" in response.headers.get("allow", ""), (method, path)


def test_serving_requests_never_loads_a_provider_or_ingestion_module() -> None:
    """The serving process cannot call Massive, Finnhub or the SEC: their code is never even imported."""
    run = subprocess.run([sys.executable, "-c", _SERVE], capture_output=True, text=True, check=True)
    assert json.loads(run.stdout.strip().splitlines()[-1]) == []


def test_an_unhandled_error_reveals_no_internals() -> None:
    """A deliberately broken dependency yields a bare 500: no exception text, path, SQL or stack."""
    application = create_app(Settings(_env_file=None))

    def explode() -> None:
        message = "secret-internal-detail /srv/app/quantforge/db.py SELECT * FROM salaries"
        raise RuntimeError(message)

    application.dependency_overrides[check_database] = explode
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/health/ready")
    assert response.status_code == 500
    for fragment in ("secret-internal-detail", "/srv/app", "SELECT", "Traceback", "RuntimeError"):
        assert fragment not in response.text
