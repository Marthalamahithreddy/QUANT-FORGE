# ruff: noqa: F811 - fixtures imported from the observation integration tests
"""Step 11.0B: ``GET /api/v1/benchmarks`` over real stored benchmarks, via real HTTP.

Proves: identity-only items, deterministic (name, id) order with bounded pagination, both active and
inactive benchmarks listed faithfully, two SELECTs whatever the row count, no network, and that a
discovered id is what the research observation endpoint then uses (explicitly, never by default).
"""

from __future__ import annotations

import socket
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tests.integration.test_research_observation import (  # noqa: F401 - fixtures and helpers
    _event,
    _record_statements,
    market,
)

from quantforge.core.config import Settings
from quantforge.db.session import get_session
from quantforge.main import create_app
from quantforge.models.benchmark import Benchmark

pytestmark = pytest.mark.integration

URL = "/api/v1/benchmarks"
ITEM_KEYS = {"id", "name", "symbol", "currency", "is_active"}


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    application: FastAPI = create_app(Settings(_env_file=None))
    application.dependency_overrides[get_session] = lambda: db_session
    with TestClient(application, raise_server_exceptions=False) as test_client:
        yield test_client


def _uuid(n: int) -> uuid.UUID:
    return uuid.UUID(int=n)


def _seed(db_session: Session) -> list[Benchmark]:
    """Overlapping names (three share one) plus an inactive, symbol-less, currency-less series."""
    rows = [
        Benchmark(id=_uuid(30), name="Alpha Index", symbol="AI3", currency="USD"),
        Benchmark(id=_uuid(10), name="Alpha Index", symbol="AI1", currency="USD"),
        Benchmark(id=_uuid(20), name="Alpha Index", symbol=None, currency="EUR"),
        Benchmark(id=_uuid(5), name="Alpha Index Plus", symbol="AIP", currency="USD"),
        Benchmark(id=_uuid(99), name="Zeta Index", symbol=None, currency=None, is_active=False),
        Benchmark(id=_uuid(1), name="Beta Index", symbol="BI", currency="USD"),
    ]
    db_session.add_all(rows)
    db_session.commit()
    return rows


EXPECTED_ORDER = [10, 20, 30, 5, 1, 99]  # name ASC, then id ASC


def _ids(body: dict[str, Any]) -> list[str]:
    return [item["id"] for item in body["items"]]


def test_no_benchmarks_is_a_200_with_an_empty_page(client: TestClient) -> None:
    response = client.get(URL)
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


def test_one_benchmark_lists_exactly_its_identity_fields(
    client: TestClient, db_session: Session
) -> None:
    db_session.add(
        Benchmark(
            id=_uuid(7),
            name="Solo Index",
            symbol="SOLO",
            currency="USD",
            description="free text that must not leak",
        )
    )
    db_session.commit()
    body = client.get(URL).json()
    assert (body["total"], body["limit"], body["offset"]) == (1, 50, 0)
    (item,) = body["items"]
    assert set(item) == ITEM_KEYS
    assert item == {
        "id": str(_uuid(7)),
        "name": "Solo Index",
        "symbol": "SOLO",
        "currency": "USD",
        "is_active": True,
    }
    assert "leak" not in str(body) and "source" not in str(body) and "run" not in str(body.keys())


def test_many_benchmarks_are_ordered_by_name_then_id_with_the_right_total(
    client: TestClient, db_session: Session
) -> None:
    _seed(db_session)
    body = client.get(URL).json()
    assert body["total"] == 6
    assert _ids(body) == [str(_uuid(n)) for n in EXPECTED_ORDER]
    assert [i["name"] for i in body["items"]][:3] == ["Alpha Index"] * 3, "ties broken by id"
    assert client.get(URL).json() == body, "repeatable on unchanged data"


def test_active_and_inactive_are_both_listed_and_faithfully_represented(
    client: TestClient, db_session: Session
) -> None:
    _seed(db_session)
    items = {i["id"]: i for i in client.get(URL).json()["items"]}
    inactive = items[str(_uuid(99))]
    assert inactive["is_active"] is False
    assert inactive["symbol"] is None and inactive["currency"] is None, "nulls stay null"
    assert items[str(_uuid(10))]["is_active"] is True
    assert items[str(_uuid(20))] == {
        "id": str(_uuid(20)),
        "name": "Alpha Index",
        "symbol": None,
        "currency": "EUR",
        "is_active": True,
    }
    assert sorted(i["is_active"] for i in items.values()) == [False] + [True] * 5


def test_pagination_pages_neither_repeat_nor_skip(client: TestClient, db_session: Session) -> None:
    _seed(db_session)
    seen: list[str] = []
    for offset in (0, 2, 4):
        page = client.get(URL, params={"limit": 2, "offset": offset}).json()
        assert (page["total"], page["limit"], page["offset"]) == (6, 2, offset)
        seen.extend(_ids(page))
    assert seen == [str(_uuid(n)) for n in EXPECTED_ORDER]
    last = client.get(URL, params={"limit": 4, "offset": 4}).json()
    assert _ids(last) == [str(_uuid(1)), str(_uuid(99))][: len(last["items"])]
    assert len(last["items"]) == 2
    past = client.get(URL, params={"limit": 2, "offset": 6})
    assert past.status_code == 200
    assert past.json() == {"items": [], "total": 6, "limit": 2, "offset": 6}
    default = client.get(URL).json()
    assert default["limit"] == 50 and len(default["items"]) == 6


def test_the_maximum_page_is_bounded_and_the_statement_count_is_constant(
    client: TestClient, db_session: Session
) -> None:
    _seed(db_session)
    with _record_statements(db_session) as few:
        assert client.get(URL).status_code == 200
    db_session.add_all(
        Benchmark(id=_uuid(1000 + n), name=f"Bulk Index {n:03d}", currency="USD")
        for n in range(210)
    )
    db_session.commit()
    db_session.expire_all()
    with _record_statements(db_session) as many:
        response = client.get(URL, params={"limit": 200})
    body = response.json()
    assert (len(body["items"]), body["total"]) == (200, 216)
    selects = [[q for q in run if q.lstrip().upper().startswith("SELECT")] for run in (few, many)]
    assert [len(run) for run in selects] == [2, 2], selects  # page + total, never per benchmark
    assert client.get(URL, params={"limit": 201}).status_code == 422
    assert client.get(URL, params={"limit": 200, "offset": 200}).json()["items"].__len__() == 16


@pytest.mark.parametrize(
    "query",
    ["limit=0", "limit=-3", "limit=201", "offset=-1", "offset=1000001", "limit=5&limit=6", "x=1"],
)
def test_invalid_parameters_are_422_over_real_data(
    client: TestClient, db_session: Session, query: str
) -> None:
    _seed(db_session)
    assert client.get(f"{URL}?{query}").status_code == 422


def test_listing_is_select_only_and_uses_no_network(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(db_session)
    before = db_session.execute(sa.text("SELECT count(*), count(DISTINCT id) FROM benchmark")).one()

    def tripwire(*_a: object, **_k: object) -> None:
        raise AssertionError("network access")

    monkeypatch.setattr(socket.socket, "connect", tripwire)
    monkeypatch.setattr(socket, "create_connection", tripwire)
    db_session.expire_all()
    with _record_statements(db_session) as statements:
        assert client.get(URL).status_code == 200
    allowed = ("SELECT", "WITH", "SAVEPOINT", "RELEASE SAVEPOINT", "ROLLBACK")
    assert statements and all(s.lstrip().upper().startswith(allowed) for s in statements)
    page = next(q for q in statements if q.lstrip().upper().startswith("SELECT BENCHMARK."))
    for column in ("description", "source_id", "created_at", "updated_at"):
        assert column not in page, f"{column} must never be loaded by the discovery query"
    assert not any("price" in q.lower() or "data_source" in q.lower() for q in statements), (
        "benchmark identity only: no level and no source is read"
    )
    after = db_session.execute(sa.text("SELECT count(*), count(DISTINCT id) FROM benchmark")).one()
    assert before == after


def test_a_discovered_benchmark_id_is_what_the_research_observation_then_uses(
    client: TestClient, db_session: Session, market: dict[str, object]
) -> None:
    event = _event(db_session, market)
    db_session.add(Benchmark(name="Another Index", symbol="ANO", currency="USD"))
    db_session.commit()

    listed = client.get(URL).json()["items"]
    discovered = next(i for i in listed if i["name"] == "Observation Index")
    assert discovered["symbol"] == "OBSI" and len(listed) == 2

    chosen = client.get(
        f"/api/v1/research/observations/{event.id}",
        params={"horizons": "1,2", "benchmark_id": discovered["id"]},
    )
    assert chosen.status_code == 200, chosen.text
    body = chosen.json()
    assert body["parameters"]["benchmark_id"] == discovered["id"]
    study = body["descriptive"]["study"]
    assert study["benchmark"]["benchmark_id"] == discovered["id"]
    assert [r["benchmark_return"] for r in study["benchmark"]["returns"]] == ["0.025", "0.05"]

    # no automatic selection: with benchmarks stored, an omitted benchmark_id stays omitted
    unchosen = client.get(
        f"/api/v1/research/observations/{event.id}", params={"horizons": "1,2"}
    ).json()
    assert unchosen["parameters"]["benchmark_id"] is None
    assert unchosen["descriptive"]["study"]["benchmark"]["benchmark_id"] is None
