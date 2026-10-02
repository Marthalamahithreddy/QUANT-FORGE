"""Declarative base and metadata shared by all QuantForge ORM models.

The metadata carries a constraint naming convention so that every constraint
Alembic creates has a deterministic, pattern-based name. Without it PostgreSQL
invents names such as ``dailypricebar_listing_id_fkey``, and a later
``ALTER``/``DROP`` of a constraint has to hard-code that auto-generated string.
With the convention in place, autogenerate and hand-written migrations refer to
constraints by stable symbolic name.

Application models live in :mod:`quantforge.models`. Importing that package is
what registers the tables on ``Base.metadata``; ``alembic/env.py`` imports it
explicitly so autogenerate sees the full schema.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

#: Deterministic constraint names. ``%(table_name)s``/``%(column_0_name)s`` keep
#: names unique per table; the explicit ``constraint_name`` escape hatch lets a
#: hand-written constraint opt out when a descriptive name reads better.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base shared by every QuantForge ORM model.

    The foundation milestone declared no tables. The financial-domain milestone
    adds the issuer/instrument/observation schema, so autogenerate now produces
    a real migration instead of an empty one.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
