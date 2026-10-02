"""Import-purity tests.

The foundation milestone requires that the API can be imported, constructed and
asked for liveness with PostgreSQL and Redis both stopped. That is a property of
a *cold* process, so it is verified in a fresh interpreter rather than in the
already-warm test process, where earlier tests could mask a regression.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

#: Asserts that no engine or client exists after import, after create_app, and
#: after serving the liveness endpoints.
PROBE = textwrap.dedent(
    """
    import quantforge.cache.redis as redis_module
    import quantforge.db.session as session_module
    from quantforge.main import create_app

    assert session_module._registry._engine is None, "engine created during import"
    assert redis_module._registry._client is None, "redis client created during import"

    application = create_app()

    assert session_module._registry._engine is None, "engine created by create_app"
    assert redis_module._registry._client is None, "redis client created by create_app"

    from fastapi.testclient import TestClient

    with TestClient(application) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/v1/health").status_code == 200

    print("CLEAN")
    """
)

#: Installed before any application import, so a real dial attempt during import
#: or construction becomes a hard failure instead of a silent success.
SOCKET_GUARD = textwrap.dedent(
    """
    import socket

    def _forbidden(*args, **kwargs):
        raise AssertionError("a socket was opened during import or create_app")

    socket.socket.connect = _forbidden
    """
)


def _run_probe(source: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def test_cold_process_opens_no_connection_and_serves_liveness() -> None:
    """Import plus create_app must not touch PostgreSQL or Redis."""
    result = _run_probe(PROBE)

    assert result.returncode == 0, f"probe failed:\n{result.stdout}\n{result.stderr}"
    assert "CLEAN" in result.stdout


def test_cold_process_never_dials_out() -> None:
    """A socket connect during import or construction must fail the probe."""
    result = _run_probe(SOCKET_GUARD + PROBE)

    assert result.returncode == 0, f"probe failed:\n{result.stdout}\n{result.stderr}"
    assert "CLEAN" in result.stdout
