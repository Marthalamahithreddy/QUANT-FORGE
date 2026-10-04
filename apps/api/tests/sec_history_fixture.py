"""Offline SEC-shaped submissions responses with ``filings.files`` history files.

Pinned, small and synthetic in everything except the *shape*, which follows what the live
service was observed to return in step 10.7A/10.7B: a parent object with ``filings.recent``
(parallel arrays) and ``filings.files`` (name / filingCount / filingFrom / filingTo), and
flat history files holding the same parallel arrays. No payload is copied from the SEC and
no contact identity appears anywhere.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from typing import Any

import httpx2 as httpx

CIK = "0000320193"
HISTORY_1 = f"CIK{CIK}-submissions-001.json"
HISTORY_2 = f"CIK{CIK}-submissions-002.json"

#: (accession, form, filingDate, reportDate, acceptanceDateTime, primaryDocument, items)
Row = tuple[str, str, str, str, str, str, str]

# ── recent: the latest filings ──────────────────────────────────────────────────────────
R_10Q = (
    "0000320193-26-000006",
    "10-Q",
    "2026-01-30",
    "2025-12-27",
    "2026-01-30T11:01:32.000Z",
    "q.htm",
    "",
)
R_10K_A = (
    "0001104659-26-053166",
    "10-K/A",
    "2026-04-30",
    "2025-09-27",
    "2026-04-30T21:13:17.000Z",
    "ka.htm",
    "",
)
R_FORM4 = (
    "0001140361-26-038307",
    "4",
    "2026-10-01",
    "2026-09-29",
    "2026-10-01T22:30:11.000Z",
    "x.xml",
    "",
)
# An older filing that is ALSO still in `recent` (a representation overlap with history file 1).
R_OVERLAP = (
    "0000320193-22-000108",
    "10-K",
    "2022-10-28",
    "2022-09-24",
    "2022-10-28T20:42:27.000Z",
    "k.htm",
    "",
)

# ── history file 1: 2015-2023 ─────────────────────────────────────────────────────────────
H1_10K = R_OVERLAP
H1_10Q_A = (
    "0000320193-19-000067",
    "10-Q/A",
    "2019-08-01",
    "2019-06-29",
    "2019-08-01T20:03:10.000Z",
    "qa.htm",
    "",
)
H1_8K = (
    "0000320193-21-000010",
    "8-K",
    "2021-01-27",
    "2021-01-27",
    "2021-01-27T21:31:18.000Z",
    "k8.htm",
    "2.02,9.01",
)
H1_FORM4 = (
    "0001140361-17-000001",
    "4",
    "2017-02-10",
    "2017-02-08",
    "2017-02-10T15:00:00.000Z",
    "x.xml",
    "",
)

# ── history file 2: 1994-2014 ─────────────────────────────────────────────────────────────
H2_10K = (
    "0001193125-14-383437",
    "10-K",
    "2014-10-27",
    "2014-09-27",
    "2014-10-27T20:15:51.000Z",
    "k.htm",
    "",
)
H2_10Q = (
    "0001193125-13-300670",
    "10-Q",
    "2013-07-24",
    "2013-06-29",
    "2013-07-24T20:02:00.000Z",
    "q.htm",
    "",
)
#: Exactly midnight Eastern (EST): a date-only placeholder, not an acceptance instant.
H2_PLACEHOLDER = (
    "0000320193-96-000023",
    "10-K",
    "1996-12-04",
    "1996-09-27",
    "1996-12-04T05:00:00.000Z",
    "k.txt",
    "",
)
H2_PLACEHOLDER_EDT = (
    "0000320193-97-000018",
    "10-Q",
    "1997-08-12",
    "1997-06-27",
    "1997-08-12T04:00:00.000Z",
    "q.txt",
    "",
)


def columns(rows: Sequence[Row]) -> dict[str, list[str]]:
    """Parallel arrays in the submissions shape (plus columns the mapper ignores)."""
    return {
        "accessionNumber": [r[0] for r in rows],
        "filingDate": [r[2] for r in rows],
        "reportDate": [r[3] for r in rows],
        "acceptanceDateTime": [r[4] for r in rows],
        "act": ["34"] * len(rows),
        "form": [r[1] for r in rows],
        "fileNumber": ["001-36743"] * len(rows),
        "filmNumber": [""] * len(rows),
        "items": [r[6] for r in rows],
        "core_type": [""] * len(rows),
        "size": ["1"] * len(rows),
        "isXBRL": ["0"] * len(rows),
        "isInlineXBRL": ["0"] * len(rows),
        "isXBRLNumeric": ["0"] * len(rows),
        "primaryDocument": [r[5] for r in rows],
        "primaryDocDescription": [""] * len(rows),
    }


def file_entry(name: str, rows: Sequence[Row], *, count: int | None = None) -> dict[str, Any]:
    dates = sorted(r[2] for r in rows)
    return {
        "name": name,
        "filingCount": len(rows) if count is None else count,
        "filingFrom": dates[0],
        "filingTo": dates[-1],
    }


def parent(recent: Sequence[Row], files: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    return {
        "cik": "320193",
        "name": "Example Filer Inc.",
        "filings": {"recent": columns(recent), "files": list(files)},
    }


def history(rows: Sequence[Row]) -> dict[str, Any]:
    return columns(rows)


def encode(document: object) -> bytes:
    return json.dumps(document, separators=(",", ":")).encode()


def synthetic_rows(year: int, count: int, *, start_sequence: int = 1) -> list[Row]:
    """``count`` distinct, normal-looking weekday filings in ``year`` (no anomalies)."""
    rows: list[Row] = []
    day = date(year, 1, 4)
    for index in range(count):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        form = ("10-Q", "10-K", "8-K")[index % 3]
        rows.append(
            (
                f"0000320193-{year % 100:02d}-{start_sequence + index:06d}",
                form,
                day.isoformat(),
                day.isoformat(),
                f"{day.isoformat()}T15:00:00.000Z",
                "d.htm",
                "",
            )
        )
        day += timedelta(days=1 if index % 2 else 2)
    return rows


class Recorder:
    """A MockTransport that serves ``routes`` and records every request path + header."""

    def __init__(self, routes: Mapping[str, bytes | int | Callable[[], httpx.Response]]) -> None:
        self.routes = dict(routes)
        self.paths: list[str] = []
        self.user_agents: list[str | None] = []
        self.hosts: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        self.hosts.append(request.url.host)
        self.user_agents.append(request.headers.get("User-Agent"))
        route = self.routes.get(request.url.path)
        if route is None:
            return httpx.Response(404)
        if isinstance(route, int):
            return httpx.Response(route)
        if callable(route):
            return route()
        return httpx.Response(200, content=route)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
