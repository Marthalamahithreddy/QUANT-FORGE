"""Alembic configuration tests.

These assert on the committed configuration rather than on a live database, so
they run in any environment. They exist to protect the two properties that are
easy to regress and expensive to discover late: no credential in a committed
file, and exactly one autogenerate target.
"""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest

from quantforge.db.base import Base
from quantforge.models import ALL_TABLES

API_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = API_ROOT / "alembic.ini"
ALEMBIC_DIR = API_ROOT / "alembic"
ENV_PY = ALEMBIC_DIR / "env.py"
VERSIONS_DIR = ALEMBIC_DIR / "versions"


@pytest.fixture(scope="module")
def ini() -> configparser.ConfigParser:
    # interpolation=None keeps Alembic's own %(here)s token literal instead of
    # letting configparser try to resolve it.
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(ALEMBIC_INI)
    return parser


@pytest.fixture(scope="module")
def env_source() -> str:
    return ENV_PY.read_text(encoding="utf-8")


def test_expected_layout_exists() -> None:
    assert ALEMBIC_INI.is_file()
    assert ENV_PY.is_file()
    assert (ALEMBIC_DIR / "script.py.mako").is_file()
    assert VERSIONS_DIR.is_dir()


def test_script_location_points_at_the_package(ini: configparser.ConfigParser) -> None:
    assert ini.get("alembic", "script_location").endswith("/alembic")


def test_source_tree_is_prepended_for_imports(ini: configparser.ConfigParser) -> None:
    """Lets Alembic import the package from a bare checkout."""
    assert ini.get("alembic", "prepend_sys_path").endswith("/src")


def test_alembic_ini_contains_no_database_credentials() -> None:
    """The URL must come from Settings, never from a committed file."""
    raw = ALEMBIC_INI.read_text(encoding="utf-8")

    assert "sqlalchemy.url" not in raw
    for marker in ("quantforge:", "postgresql://", "postgresql+psycopg://", "password"):
        assert marker not in raw


def test_env_reads_the_url_from_application_settings(env_source: str) -> None:
    assert "from quantforge.core.config import get_settings" in env_source
    assert "get_settings().database_url" in env_source


def test_env_uses_base_metadata_as_the_autogenerate_target(env_source: str) -> None:
    assert "from quantforge.db.base import Base" in env_source
    assert "target_metadata = Base.metadata" in env_source


def test_env_imports_the_models_package(env_source: str) -> None:
    """Metadata is populated by module import, so env.py must import the package.

    Without this, ``alembic revision --autogenerate`` would compare an empty
    target against a populated database and emit a no-op migration that looks
    like a successful run.
    """
    assert "import quantforge.models" in env_source


def test_env_enables_type_comparison(env_source: str) -> None:
    """compare_type catches column type changes; it is off by default."""
    assert "compare_type=True" in env_source


def test_env_reuses_the_application_dsn_normalisation(env_source: str) -> None:
    """Migrations must select the same driver as the application."""
    assert "from quantforge.db.session import normalise_database_url" in env_source


def test_base_metadata_contains_exactly_the_known_tables() -> None:
    """The autogenerate target holds the known schema, and only that schema."""
    assert set(Base.metadata.tables) == set(ALL_TABLES)


def test_baseline_revision_exists_and_is_empty() -> None:
    """The 0001 baseline must survive as the empty root of the history."""
    revisions = sorted(VERSIONS_DIR.glob("*.py"))
    assert [p.name for p in revisions] == [
        "0001_baseline.py",
        "0002_financial_domain.py",
        "0003_ingestion_runs_and_issuer_cik.py",
        "0004_earnings_ingestion.py",
        "0005_earnings_event_date.py",
        "0006_price_basis.py",
        "0007_availability_basis.py",
        "0008_sec_filing.py",
        "0009_stock_split.py",
        "0010_listing_history_fact.py",
        "0011_financial_fact.py",
        "0012_fsds_accepted_text.py",
        "0013_research_records.py",
    ]

    source = revisions[0].read_text(encoding="utf-8")
    assert 'revision: str = "0001"' in source
    assert "down_revision: str | Sequence[str] | None = None" in source
    # No table operations, by design.
    assert "op.create_table" not in source
    assert "op.drop_table" not in source
