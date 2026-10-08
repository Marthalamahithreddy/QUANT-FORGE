"""Read-only, bounded fingerprint of the stored state, for the release smoke's "nothing changed" proof.

    uv run --project apps/api python scripts/validation/state_fingerprint.py [--validation]

Prints one JSON object on stdout and no credential. By default it connects as the application does
(QF_DATABASE_URL); ``--validation`` points it at the isolated validation database instead.

WHAT IT PINS (per application table, in one REPEATABLE READ, READ ONLY transaction):

* ``rows``        the exact row count (for a table estimated under ``EXACT_COUNT_BELOW`` rows; a larger
                  table is not scanned to count it -- its write counters and digests pin it instead);
* ``writes``      PostgreSQL's cumulative ``n_tup_ins / n_tup_upd / n_tup_del`` counters, which move on ANY
                  insert, update or delete -- the whole-table "was this table written to" detector, at O(1)
                  cost and with no table scan;
* ``head``/``tail``  a digest of the first/last ``WINDOW`` rows by primary key (full row text), so a changed
                  value inside the window is seen even if the counters were reset;
* ``keys``        a digest of the primary keys of up to ``KEY_CAP`` rows (identity set);
* named IMMUTABLE evidence, where the schema already carries a digest, is digested in full (bounded by
  ``KEY_CAP``): saved research records (manifest/result fingerprints), raw artifact references
  (``sha256`` + path of fact artifacts and of ingestion runs).

WHAT IT DOES NOT PROVE: a value changed OUTSIDE the windows of a large table is caught by the counters
only; counters are cumulative since the statistics were last reset and are flushed with a short delay (the
release smoke waits before its second snapshot), so a write that lands in the final instant may be seen
only by the next snapshot. It reads; it never writes, migrates or locks beyond the read-only snapshot.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

import sqlalchemy as sa

WINDOW = 50
KEY_CAP = 5000
EXACT_COUNT_BELOW = 1_000_000

# Evidence that carries its own digest: (table, order column, columns digested in full)
IMMUTABLE = {
    "research_observation_record": (
        "id",
        ("id", "manifest_fingerprint", "result_fingerprint", "created_at"),
    ),
    "financial_fact_artifact": (
        "id",
        ("id", "sha256", "byte_length", "raw_artifact_path"),
    ),
    "ingestion_run": (
        "id",
        (
            "id",
            "raw_content_sha256",
            "raw_artifact_path",
            "raw_artifact_bytes",
            "status",
        ),
    ),
}


def _digest(rows: list[tuple[object, ...]]) -> str:
    return hashlib.sha256(
        json.dumps([[str(v) for v in row] for row in rows]).encode()
    ).hexdigest()[:16]


def fingerprint(connection: sa.Connection) -> dict[str, object]:
    connection.execute(
        sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    )
    tables = [
        row[0]
        for row in connection.execute(
            sa.text(
                "SELECT relname FROM pg_stat_user_tables WHERE schemaname = 'public' ORDER BY relname"
            )
        )
    ]
    counters = {
        row[0]: [int(row[1]), int(row[2]), int(row[3])]
        for row in connection.execute(
            sa.text(
                "SELECT relname, n_tup_ins, n_tup_upd, n_tup_del FROM pg_stat_user_tables WHERE schemaname = 'public'"
            )
        )
    }
    result: dict[str, object] = {}
    for table in tables:
        quoted = sa.sql.quoted_name(table, quote=True)
        key = connection.execute(
            sa.text(
                "SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid = i.indrelid "
                "AND a.attnum = ANY (i.indkey) WHERE i.indrelid = CAST(:t AS regclass) AND i.indisprimary "
                "ORDER BY a.attnum LIMIT 1"
            ),
            {"t": f'public."{table}"'},
        ).scalar()
        entry: dict[str, object] = {"writes": counters.get(table)}
        # An exact count scans the table: do it where that is cheap. A very large table is pinned by its
        # write counters (and the key/head/tail digests); its planner estimate is reported for information.
        estimate = int(
            connection.execute(
                sa.text(
                    "SELECT n_live_tup FROM pg_stat_user_tables WHERE schemaname = 'public' AND relname = :t"
                ),
                {"t": table},
            ).scalar()
            or 0
        )
        if estimate < EXACT_COUNT_BELOW:
            entry["rows"] = connection.execute(
                sa.text(f'SELECT count(*) FROM "{quoted}"')
            ).scalar()
        else:
            entry["rows"] = None
            entry["rows_estimate_order"] = len(str(estimate))  # the order of magnitude, stable under small drift
        if key:
            order = f'"{key}"'
            head = connection.execute(
                sa.text(
                    f'SELECT t::text FROM "{quoted}" t ORDER BY {order} LIMIT {WINDOW}'
                )
            )
            tail = connection.execute(
                sa.text(
                    f'SELECT t::text FROM "{quoted}" t ORDER BY {order} DESC LIMIT {WINDOW}'
                )
            )
            keys = connection.execute(
                sa.text(
                    f'SELECT {order}::text FROM "{quoted}" ORDER BY {order} LIMIT {KEY_CAP}'
                )
            )
            entry["head"] = _digest(list(head))
            entry["tail"] = _digest(list(tail))
            entry["keys"] = _digest(list(keys))
        if table in IMMUTABLE:
            order_column, columns = IMMUTABLE[table]
            selected = ", ".join(f'"{c}"' for c in columns)
            rows = connection.execute(
                sa.text(
                    f'SELECT {selected} FROM "{quoted}" ORDER BY "{order_column}" LIMIT {KEY_CAP}'
                )
            )
            entry["immutable"] = _digest(list(rows))
        result[table] = entry
    return result


def main() -> int:
    from quantforge.core.config import get_settings
    from quantforge.db.session import dispose_engines, get_engine

    try:
        settings = get_settings()
        if "--validation" in sys.argv:
            sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
            import common

            settings = common.validation_settings()
        with get_engine(settings).connect() as connection:
            out = fingerprint(connection)
            connection.rollback()
    except Exception as exc:  # class name only: a driver message can carry a DSN
        sys.stderr.write(f"state fingerprint failed: {type(exc).__name__}\n")
        return 1
    finally:
        dispose_engines()
    sys.stdout.write(json.dumps(out, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
