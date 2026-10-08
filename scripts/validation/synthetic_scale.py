"""Build a clearly SYNTHETIC, disposable database for scale measurements -- never the validation database.

    uv run --project apps/api python scripts/validation/synthetic_scale.py build   [--listings 300] [--sessions 2520] [--quarters 40] [--saved 200]
    uv run --project apps/api python scripts/validation/synthetic_scale.py drop

The database is named ``qf_scale_synthetic`` (the script refuses any other name), is created from the real
Alembic migrations, and is filled with data that is synthetic in every visible place: company names
"SYNTHETIC-00001 Corp", tickers "SYN00001", sources named "synthetic-scale-*", a benchmark "SYNTHETIC-INDEX".
Nothing is fetched from a provider. Domain invariants hold: bars are on genuine XNYS sessions (taken from
the exchange calendar, never invented weekdays), every event falls on a session and is before-market, price
bars use the research price basis with one source per series, and saved research records are produced by the
real ``save_research_record`` service, so their fingerprints are genuine.

Then measure with ``perf_baseline.py --database qf_scale_synthetic``; remove with ``drop``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import exchange_calendars as xcals
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from quantforge.core.config import DEFAULT_DATABASE_URL

NAME = "qf_scale_synthetic"
API_ROOT = Path(__file__).resolve().parents[2] / "apps" / "api"


def url_for(database: str) -> str:
    return make_url(DEFAULT_DATABASE_URL).set(database=database).render_as_string(hide_password=False)


def admin(statement: str) -> None:
    engine = sa.create_engine(url_for("postgres"), isolation_level="AUTOCOMMIT", poolclass=sa.pool.NullPool)
    with engine.connect() as connection:
        connection.execute(sa.text(statement))
    engine.dispose()


def drop() -> None:
    engine = sa.create_engine(url_for("postgres"), isolation_level="AUTOCOMMIT", poolclass=sa.pool.NullPool)
    with engine.connect() as connection:
        connection.execute(
            sa.text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n AND pid <> pg_backend_pid()"),
            {"n": NAME},
        )
        connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{NAME}"'))
    engine.dispose()


def migrate() -> None:
    config = Config(API_ROOT / "alembic.ini")
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url_for(NAME))
    command.upgrade(config, "head")


def build(listings: int, sessions: int, quarters: int, saved: int) -> None:
    drop()
    admin(f'CREATE DATABASE "{NAME}"')
    migrate()
    calendar = xcals.get_calendar("XNYS")
    # the LAST `sessions` genuine exchange sessions up to the calendar's own final session
    days = [d.date() for d in calendar.sessions[-sessions:]]
    engine = sa.create_engine(url_for(NAME), poolclass=sa.pool.NullPool)
    with engine.begin() as c:
        c.execute(sa.text("CREATE TEMP TABLE synth_session (n int, d date) ON COMMIT DROP"))
        c.execute(sa.text("INSERT INTO synth_session VALUES (:n, :d)"), [{"n": i, "d": d} for i, d in enumerate(days)])
        c.execute(
            sa.text(
                """
                INSERT INTO exchange (id, created_at, updated_at, mic, name, timezone, is_active)
                VALUES (gen_random_uuid(), now(), now(), 'XNYS', 'SYNTHETIC exchange', 'America/New_York', true);
                INSERT INTO data_source (id, created_at, name, provider_type, is_active) VALUES
                    (gen_random_uuid(), now(), 'synthetic-scale-prices', 'MARKET_DATA_VENDOR', true),
                    (gen_random_uuid(), now(), 'synthetic-scale-filings', 'REGULATORY_FILING', true);
                """
            )
        )
        c.execute(
            sa.text(
                """
                INSERT INTO company (id, created_at, updated_at, legal_name, display_name, cik)
                SELECT gen_random_uuid(), now(), now(), 'SYNTHETIC-' || lpad(g::text, 5, '0') || ' Corp',
                       'SYNTHETIC-' || lpad(g::text, 5, '0') || ' Corp', lpad((9000000 + g)::text, 10, '0')
                FROM generate_series(1, :n) g
                """
            ),
            {"n": listings},
        )
        c.execute(
            sa.text(
                """
                INSERT INTO security (id, created_at, updated_at, company_id, security_type, name, currency, is_active)
                SELECT gen_random_uuid(), now(), now(), id, 'EQUITY', 'SYNTHETIC shares', 'USD', true FROM company
                """
            )
        )
        c.execute(
            sa.text(
                """
                INSERT INTO listing (id, created_at, updated_at, security_id, exchange_id, ticker, valid_from, is_primary)
                SELECT gen_random_uuid(), now(), now(), s.id, (SELECT id FROM exchange),
                       'SYN' || lpad(row_number() OVER (ORDER BY co.legal_name)::text, 5, '0'), date '2000-01-03', true
                FROM security s JOIN company co ON co.id = s.company_id
                """
            )
        )
        src = "(SELECT id FROM data_source WHERE name = 'synthetic-scale-prices')"
        c.execute(
            sa.text(
                f"""
                INSERT INTO daily_price_bar (id, created_at, listing_id, trading_date, open, high, low, close, volume,
                                             source_id, ingested_at, price_basis)
                SELECT gen_random_uuid(), now(), l.id, d.d, p.px, p.px * 1.01, p.px * 0.99, p.px, 1000000, {src}, now(),
                       'SPLIT_ADJUSTED'
                FROM listing l CROSS JOIN synth_session d
                CROSS JOIN LATERAL (SELECT round((100 + 10 * sin(d.n / 40.0) + (ascii(right(l.ticker, 1)) % 7))::numeric, 6) AS px) p
                """
            )
        )
        c.execute(
            sa.text(
                f"""
                INSERT INTO benchmark (id, created_at, updated_at, name, symbol, currency, source_id, is_active)
                VALUES (gen_random_uuid(), now(), now(), 'SYNTHETIC-INDEX', 'SYNI', 'USD', {src}, true);
                INSERT INTO benchmark_price_bar (id, created_at, benchmark_id, trading_date, close, source_id, ingested_at, price_basis)
                SELECT gen_random_uuid(), now(), (SELECT id FROM benchmark), d.d, round((400 + 20 * sin(d.n / 55.0))::numeric, 6),
                       {src}, now(), 'SPLIT_ADJUSTED'
                FROM synth_session d
                """
            )
        )
        step = max(1, (sessions - 30) // quarters)
        c.execute(
            sa.text(
                f"""
                INSERT INTO earnings_event (id, created_at, updated_at, company_id, fiscal_year, fiscal_quarter, timing,
                                            reported_eps, estimated_eps, source_id, ingested_at, fingerprint, event_date)
                SELECT gen_random_uuid(), now(), now(), co.id, 2017 + (q.k / 4), 1 + (q.k % 4), 'BEFORE_MARKET',
                       1.2000, 1.0000, {src}, now(), md5(co.id::text || q.k::text) || md5(q.k::text || co.id::text),
                       (SELECT d FROM synth_session WHERE n = 10 + q.k * {step} + (ascii(right(co.cik, 1)) % 5))
                FROM company co CROSS JOIN generate_series(0, {quarters - 1}) AS q(k)
                """
            )
        )
        fsrc = "(SELECT id FROM data_source WHERE name = 'synthetic-scale-filings')"
        c.execute(
            sa.text(
                f"""
                INSERT INTO sec_filing (id, created_at, company_id, accession_number, form, filing_date, source_id, ingested_at)
                SELECT gen_random_uuid(), now(), e.company_id,
                       lpad((9000000 + row_number() OVER ())::text, 10, '0') || '-26-' || lpad((row_number() OVER ())::text, 6, '0'),
                       '10-Q', e.event_date, {fsrc}, now()
                FROM earnings_event e
                """
            )
        )
        c.execute(
            sa.text(
                f"""
                INSERT INTO financial_fact_artifact (id, created_at, source_id, source_kind, artifact_name, sha256, byte_length,
                                                     raw_artifact_path, member_digests, num_has_segments, ingested_at)
                VALUES (gen_random_uuid(), now(), {fsrc}, 'SEC_FSDS', 'synthetic-package', repeat('0', 64), 1, 'synthetic/none', '{{}}'::jsonb,
                        false, now());
                INSERT INTO financial_fact_submission (id, created_at, artifact_id, sec_filing_id, period_end, fiscal_year,
                                                       fiscal_period, coregistrant_count)
                SELECT gen_random_uuid(), now(), (SELECT id FROM financial_fact_artifact), f.id, f.filing_date, 2026, 'Q1', 1
                FROM sec_filing f
                """
            )
        )
        c.execute(sa.text("ANALYZE"))
    # genuine saved research records, through the real service
    from sqlalchemy.orm import Session

    from quantforge.research_records import save_research_record

    with Session(engine) as session:
        bench = session.execute(sa.text("SELECT id FROM benchmark")).scalar_one()
        events = session.execute(
            sa.text("SELECT id FROM earnings_event ORDER BY fingerprint LIMIT :n"), {"n": saved}
        ).scalars().all()
        for index, event_id in enumerate(events):
            save_research_record(session, event_id, horizons=(1, 2, 5), benchmark_id=bench if index % 2 else None)
            if index % 50 == 49:
                session.commit()
        session.commit()
    with engine.connect() as c:
        counts = {
            table: c.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar()  # noqa: S608
            for table in ("company", "listing", "daily_price_bar", "benchmark_price_bar", "earnings_event", "sec_filing",
                          "financial_fact_submission", "research_observation_record")
        }
    engine.dispose()
    print("built", NAME, counts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["build", "drop"])
    parser.add_argument("--listings", type=int, default=300)
    parser.add_argument("--sessions", type=int, default=2520)
    parser.add_argument("--quarters", type=int, default=40)
    parser.add_argument("--saved", type=int, default=200)
    args = parser.parse_args()
    os.environ.setdefault("QF_ENVIRONMENT", "local")
    if args.action == "drop":
        drop()
        print("dropped", NAME)
    else:
        build(args.listings, args.sessions, args.quarters, args.saved)
    return 0


if __name__ == "__main__":
    sys.exit(main())
