"""Read-only performance baseline of the API against a database (default: the validation database).

    uv run --project apps/api python scripts/validation/perf_baseline.py [--runs 30] [--database NAME] [--json]

In-process (``TestClient``, no network, no provider): for each call it reports the FIRST call's latency, the
warm median / p95 / maximum over ``--runs`` repeats, the SQL statement count of one request, and the
response size. Statement counts are taken with a ``before_cursor_execute`` listener on the engine the
requests use, so they are exact, not estimated. Every request is a GET.

The call set is the one recorded in ``docs/release-candidate-hardening-design.md`` section 9 (the 12A
baseline), so the two are directly comparable. Latency depends on the machine: compare ratios and
statement counts across runs on the same machine, not absolute milliseconds across machines.

``--database NAME`` points the same measurements at another database (the synthetic-scale run uses a
disposable one); the golden validation database is only ever READ.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parent))

from quantforge.core.config import DEFAULT_DATABASE_URL, Settings, get_settings  # noqa: E402
from quantforge.db.session import dispose_engines, get_engine  # noqa: E402
from quantforge.main import create_app  # noqa: E402


def settings_for(database: str) -> Settings:
    """Settings whose database is ``database``, installed as the process-wide settings the app reads."""
    url = make_url(DEFAULT_DATABASE_URL).set(database=database)
    os.environ["QF_DATABASE_URL"] = url.render_as_string(hide_password=False)
    get_settings.cache_clear()
    return get_settings()


class Counter:
    """Counts the SQL statements the engine executes while armed."""

    def __init__(self, engine: sa.Engine) -> None:
        self.statements = 0
        event.listen(engine, "before_cursor_execute", self._count)

    def _count(self, *_args: Any) -> None:
        self.statements += 1


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(fraction * (len(ordered) - 1)))]


def measure(client: TestClient, counter: Counter, path: str, runs: int) -> dict[str, Any]:
    start = time.perf_counter()
    first = client.get(path)
    first_ms = (time.perf_counter() - start) * 1000
    counter.statements = 0
    client.get(path)
    statements = counter.statements
    samples: list[float] = []
    for _ in range(runs):
        start = time.perf_counter()
        client.get(path)
        samples.append((time.perf_counter() - start) * 1000)
    return {
        "status": first.status_code,
        "bytes": len(first.content),
        "statements": statements,
        "first_ms": round(first_ms, 2),
        "median_ms": round(statistics.median(samples), 2),
        "p95_ms": round(percentile(samples, 0.95), 2),
        "max_ms": round(max(samples), 2),
        "runs": runs,
    }


def discover(client: TestClient) -> dict[str, str]:
    """Identities from the database under test (first stored event, filing, benchmark and saved record)."""
    event_item = client.get("/api/v1/earnings-events?limit=1").json()["items"][0]
    filing = client.get("/api/v1/filings?limit=1").json()["items"][0]
    benchmark = client.get("/api/v1/benchmarks?limit=1").json()["items"][0]
    record = client.get("/api/v1/research/records?limit=1").json()["items"][0]
    return {
        "event": event_item["id"],
        "accession": filing["accession"],
        "cik": filing["company_cik"],
        "benchmark": benchmark["id"],
        "record": record["record_id"],
    }


def calls(ids: dict[str, str]) -> list[tuple[str, str]]:
    event_id, bench = ids["event"], ids["benchmark"]
    obs = f"/api/v1/research/observations/{event_id}"
    return [
        ("health", "/health"),
        ("ready", "/api/v1/health/ready"),
        ("earnings list (limit 20)", "/api/v1/earnings-events?limit=20"),
        ("earnings list (limit 200, max)", "/api/v1/earnings-events?limit=200"),
        ("earnings detail", f"/api/v1/earnings-events/{event_id}"),
        ("benchmarks", "/api/v1/benchmarks?limit=50"),
        ("filings list (limit 20)", "/api/v1/filings?limit=20"),
        ("filings list (limit 200, max)", "/api/v1/filings?limit=200"),
        (
            "filing EPS",
            f"/api/v1/filings/{ids['accession']}/eps?cik={ids['cik']}&period_class=QUARTER&eps_kind=DILUTED",
        ),
        ("research default (stock-only)", obs),
        ("research 1 horizon + benchmark", f"{obs}?horizons=1&benchmark_id={bench}"),
        ("research 3 horizons + benchmark", f"{obs}?horizons=1,2,5&benchmark_id={bench}"),
        (
            "research 10 horizons + benchmark",
            f"{obs}?horizons=1,2,3,4,5,6,7,8,9,10&benchmark_id={bench}",
        ),
        ("saved list (limit 20)", "/api/v1/research/records?limit=20"),
        ("saved list (limit 200, max)", "/api/v1/research/records?limit=200"),
        ("saved detail", f"/api/v1/research/records/{ids['record']}"),
        ("saved verify", f"/api/v1/research/records/{ids['record']}/verify"),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--database", default="quantforge_validation")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    settings = settings_for(args.database)
    application = create_app(settings)
    counter = Counter(get_engine(settings))
    results: dict[str, dict[str, Any]] = {}
    with TestClient(application) as client:
        ids = discover(client)
        for name, path in calls(ids):
            results[name] = measure(client, counter, path, args.runs)
    dispose_engines()

    if args.json:
        sys.stdout.write(json.dumps({"database": args.database, "results": results}, indent=1) + "\n")
        return 0
    print(f"database {args.database}; warm = median over {args.runs} runs; every request is a GET")
    print(f"{'call':36} {'status':>6} {'stmts':>5} {'bytes':>8} {'first':>8} {'median':>8} {'p95':>8} {'max':>8}")
    for name, r in results.items():
        print(
            f"{name:36} {r['status']:>6} {r['statements']:>5} {r['bytes']:>8} "
            f"{r['first_ms']:>8} {r['median_ms']:>8} {r['p95_ms']:>8} {r['max_ms']:>8}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
