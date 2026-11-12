"""The schemas of TABRION's two reference applications, copied for STU-022's DDL comparison (no dependency).

What the docs call the reference applications (doc 03 section 10, MVP acceptance: "Referenz-Apps
``examples/parts`` und ``examples/measurements``") live in the tabrion repository, which Studio does not install.
Their schemas are copied here as of tabrion ``f4228ec`` (like the Levuvo fixtures of STU-024):

* **parts** (reference application A, RA-A-1): ``examples/parts/parts/schema.py`` and its Alembic revision
  ``0001_initial.py``. The custom types are written out per engine as their ``load_dialect_impl`` maps them:
  ``UtcDateTime`` is ``timestamptz`` on PostgreSQL and ``VARCHAR(32)`` on SQLite, ``ExactDecimal(p, s)`` is
  ``numeric(p, s)`` on PostgreSQL and ``BIGINT`` on SQLite, ``ID`` is ``bigint`` identity on PostgreSQL and the
  ``INTEGER PRIMARY KEY`` row id alias on SQLite.
* **measurements** (reference application B, RA-B-1/RA-B-2): the device file's migrations ``_v1`` to ``_v3`` of
  ``examples/measurements/measurements/db.py`` (SQLite, verbatim) and the upstream table ``UPSTREAM`` of
  ``examples/measurements/measurements/upstream.py`` (PostgreSQL).
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

__all__ = ["MEASUREMENTS_SQLITE", "measurements_upstream", "parts"]


def parts(engine: str) -> sa.MetaData:
    """The parts schema (``parts.schema.metadata``) with the types ``engine`` gets."""
    sqlite = engine == "sqlite"
    identifier: sa.types.TypeEngine[int] = sa.Integer() if sqlite else sa.BigInteger()

    def utc() -> sa.types.TypeEngine[Any]:
        return sa.String(32) if sqlite else sa.DateTime(timezone=True)

    def exact(precision: int, scale: int) -> sa.types.TypeEngine[Any]:
        return sa.BigInteger() if sqlite else sa.Numeric(precision, scale)

    def id_column() -> sa.Column[int]:
        return sa.Column("id", identifier, sa.Identity(always=True), nullable=False)

    metadata = sa.MetaData()
    sa.Table(
        "material",
        metadata,
        id_column(),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", utc(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_material"),
        sa.UniqueConstraint("code", name="uq_material_code"),
        sa.CheckConstraint("length(code) BETWEEN 1 AND 32", name="ck_material_code"),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 200", name="ck_material_name"),
    )
    sa.Table(
        "part",
        metadata,
        id_column(),
        sa.Column("part_number", sa.String(40), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("material_id", identifier, sa.ForeignKey("material.id", name="fk_part_material"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", utc(), nullable=False),
        sa.Column("updated_at", utc(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_part"),
        sa.UniqueConstraint("part_number", name="uq_part_part_number"),
        sa.CheckConstraint("length(part_number) BETWEEN 1 AND 40", name="ck_part_part_number"),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 200", name="ck_part_name"),
        sa.CheckConstraint("status IN ('draft', 'released', 'blocked', 'obsolete')", name="ck_part_status"),
        sa.CheckConstraint("version >= 1", name="ck_part_version"),
        sa.Index("ix_part_material_id", "material_id"),
    )
    sa.Table(
        "property",
        metadata,
        id_column(),
        sa.Column(
            "part_id", identifier, sa.ForeignKey("part.id", name="fk_property_part", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", exact(18, 6), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_property"),
        sa.UniqueConstraint("part_id", "key", name="uq_property_part_id_key"),
        sa.CheckConstraint("length(key) BETWEEN 1 AND 64", name="ck_property_key"),
        sa.CheckConstraint("length(unit) BETWEEN 1 AND 16", name="ck_property_unit"),
    )
    sa.Table(
        "part_relation",
        metadata,
        sa.Column(
            "assembly_id",
            identifier,
            sa.ForeignKey("part.id", name="fk_part_relation_assembly", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "component_id", identifier, sa.ForeignKey("part.id", name="fk_part_relation_component"), nullable=False
        ),
        sa.Column("quantity", exact(12, 3), nullable=False),
        sa.PrimaryKeyConstraint("assembly_id", "component_id", name="pk_part_relation"),
        sa.CheckConstraint("quantity > 0", name="ck_part_relation_quantity"),
        sa.CheckConstraint("assembly_id <> component_id", name="ck_part_relation_not_self"),
        sa.Index("ix_part_relation_component_id", "component_id"),
    )
    return metadata


def measurements_upstream() -> sa.MetaData:
    """The upstream table of the measurements transfer (``measurements.upstream.UPSTREAM``, PostgreSQL)."""
    metadata = sa.MetaData()
    sa.Table(
        "measurement",
        metadata,
        sa.Column("device_id", sa.String(64), primary_key=True),
        sa.Column("channel", sa.String(64), primary_key=True),
        sa.Column("ts_utc", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("value", sa.Double(), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("quality", sa.SmallInteger(), nullable=False),
        sa.Column("source_seq", sa.BigInteger(), nullable=False),
    )
    return metadata


#: The device file's migrations 1 to 3 (``measurements.db._v1`` .. ``_v3``), statement by statement, verbatim.
MEASUREMENTS_SQLITE = (
    """CREATE TABLE measurement (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id TEXT NOT NULL CHECK (length(device_id) BETWEEN 1 AND 64),
            channel TEXT NOT NULL CHECK (length(channel) BETWEEN 1 AND 64),
            ts_utc TEXT NOT NULL CHECK (length(ts_utc) = 27 AND substr(ts_utc, 27) = 'Z'),
            value REAL NOT NULL
                CHECK (typeof(value) = 'real' AND value BETWEEN -1.7976931348623157e308 AND 1.7976931348623157e308),
            unit TEXT NOT NULL CHECK (length(unit) BETWEEN 1 AND 16),
            quality INTEGER NOT NULL DEFAULT 0 CHECK (typeof(quality) = 'integer' AND quality BETWEEN 0 AND 255),
            UNIQUE (device_id, channel, ts_utc)
        )""",
    "CREATE INDEX measurement_ts ON measurement (ts_utc)",
    """CREATE TABLE transfer_mark (
            target TEXT PRIMARY KEY CHECK (length(target) BETWEEN 1 AND 64),
            seq INTEGER NOT NULL CHECK (typeof(seq) = 'integer' AND seq >= 0)
        )""",
)
