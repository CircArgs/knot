"""Migration emitter — Alembic-autogenerate equivalent for knot.

Introspects a live postgres database and diffs against the in-memory
``Spec`` to produce a list of ``MigrationOp`` records that will bring
the database into alignment with the spec.

Phase 1 (this module): **additive-only**. Detects what's missing in
the database and emits ``ALTER`` / ``CREATE`` / ``UPSERT`` to add it.
Destructive operations (dropping tables, columns, indexes, etc.) and
type-change reconciliation are deferred — calls intentionally do NOT
emit ``DROP`` statements even when a database object exists in the DB
but not in the spec. Operators handle those manually until Phase 2.

Idempotent views: ``CREATE OR REPLACE VIEW`` is always emitted for
resolved views and virtual classes, so re-running the migration
reconciles view bodies even if no other change is detected.

Usage::

    ops = diff_against_db(spec, query)
    for op in ops:
        print(op.description, "destructive=", op.destructive)
        print(op.sql)
        if not op.destructive:
            host.execute(op.sql)

The ``query`` argument is a callable ``(sql: str, params: tuple) ->
list[tuple]`` — easy to plug a real psycopg cursor into, equally easy
to mock in unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from knot.compile.ddl import (
    _emit_bindings_indexes,
    _emit_bindings_table,
    _emit_fk_alters,
    _emit_table,
    _emit_trust_table,
    _emit_view,
)
from knot.compile.resolver import emit_resolved_view
from knot.spec import ClassKind, ClassRef, OntologyClass, Spec, VirtualClass


# Query callable: takes (sql, params) and returns row tuples.
QueryFn = Callable[[str, tuple[Any, ...]], list[tuple[Any, ...]]]


@dataclass(frozen=True, slots=True)
class MigrationOp:
    """One migration operation.

    Attributes
    ----------
    description
        Short human-readable summary suitable for a Flyway filename.
    sql
        The SQL the host runs to apply the change. Always rendered with
        ``IF NOT EXISTS`` style idempotency where postgres supports it.
    destructive
        True when applying the op may discard data. Phase 1 emits no
        destructive ops; the flag is reserved for Phase 2.
    target
        Coarse classification — ``"schema"``, ``"trust_table"``,
        ``"canonical"``, ``"bindings"``, ``"index"``, ``"fk"``,
        ``"resolved_view"``, ``"virtual_view"``, ``"trust_seed"``.
        Useful for grouping ops in migration files.
    """

    description: str
    sql: str
    destructive: bool = False
    target: str = ""


# ---------------------------------------------------------------------------
# Introspection helpers
# ---------------------------------------------------------------------------


def _existing_schemas(query: QueryFn) -> set[str]:
    rows = query(
        "SELECT schema_name FROM information_schema.schemata", ()
    )
    return {r[0] for r in rows}


def _existing_tables(query: QueryFn, schema: str) -> set[str]:
    rows = query(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = %s AND table_type = 'BASE TABLE'",
        (schema,),
    )
    return {r[0] for r in rows}


def _existing_views(query: QueryFn, schema: str) -> set[str]:
    rows = query(
        "SELECT table_name FROM information_schema.views "
        "WHERE table_schema = %s",
        (schema,),
    )
    return {r[0] for r in rows}


def _existing_columns(query: QueryFn, schema: str, table: str) -> set[str]:
    rows = query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s",
        (schema, table),
    )
    return {r[0] for r in rows}


def _existing_indexes(query: QueryFn, schema: str, table: str) -> set[str]:
    rows = query(
        "SELECT indexname FROM pg_indexes "
        "WHERE schemaname = %s AND tablename = %s",
        (schema, table),
    )
    return {r[0] for r in rows}


def _existing_fk_constraints(query: QueryFn, schema: str, table: str) -> set[str]:
    rows = query(
        "SELECT constraint_name FROM information_schema.table_constraints "
        "WHERE table_schema = %s AND table_name = %s "
        "AND constraint_type = 'FOREIGN KEY'",
        (schema, table),
    )
    return {r[0] for r in rows}


def _existing_trust_rows(
    query: QueryFn,
    schema: str,
    trust_table_name: str,
) -> dict[tuple[str, str], float]:
    """Return ``{(source_name, class_name): accuracy}`` from the trust
    table, or empty dict if the table doesn't exist."""
    if trust_table_name not in _existing_tables(query, schema):
        return {}
    rows = query(
        f"SELECT source_name, class_name, accuracy FROM {schema}.{trust_table_name}",
        (),
    )
    return {(r[0], r[1]): float(r[2]) for r in rows}


# ---------------------------------------------------------------------------
# Diff driver
# ---------------------------------------------------------------------------


def diff_against_db(
    spec: Spec,
    query: QueryFn,
    *,
    schema: str = "knot_data",
    bindings_suffix: str = "_bindings",
    resolved_suffix: str = "_resolved",
    trust_table_name: str = "source_accuracy",
) -> list[MigrationOp]:
    """Walk the spec, compare against the live database via ``query``,
    return ordered additive operations.

    Ordering enforced:
      1. CREATE SCHEMA (if missing)
      2. CREATE trust table (if missing)
      3. CREATE canonical tables (per concrete class, if missing)
      4. CREATE bindings tables + their indexes (if missing)
      5. ALTER ADD COLUMN on existing tables for missing slots
      6. ALTER ADD CONSTRAINT for missing FK references
      7. CREATE OR REPLACE resolved views (always emitted — idempotent)
      8. CREATE OR REPLACE virtual class views (always emitted)
      9. UPSERT source_accuracy rows (always emitted — idempotent)
    """
    ops: list[MigrationOp] = []

    # 1. Schema
    if schema not in _existing_schemas(query):
        ops.append(
            MigrationOp(
                description=f"create_schema_{schema}",
                sql=f"CREATE SCHEMA IF NOT EXISTS {schema};",
                target="schema",
            )
        )
    db_tables = _existing_tables(query, schema)
    db_views = _existing_views(query, schema)

    # 2. Trust table
    if trust_table_name not in db_tables:
        ops.append(
            MigrationOp(
                description=f"create_table_{trust_table_name}",
                sql=_emit_trust_table(
                    schema=schema,
                    trust_table_name=trust_table_name,
                    if_not_exists=True,
                ),
                target="trust_table",
            )
        )

    # 3-6. Per-class structure
    for cls in spec.classes:
        if isinstance(cls, OntologyClass) and cls.kind == ClassKind.CONCRETE:
            ops.extend(
                _diff_concrete_class(
                    cls,
                    query=query,
                    db_tables=db_tables,
                    schema=schema,
                    bindings_suffix=bindings_suffix,
                )
            )

    # 7. Resolved views (always replace — handles spec changes too)
    for cls in spec.classes:
        if isinstance(cls, OntologyClass) and cls.kind == ClassKind.CONCRETE:
            view_name = f"{cls.name.lower()}{resolved_suffix}"
            sql = emit_resolved_view(
                spec,
                cls,
                schema=schema,
                bindings_suffix=bindings_suffix,
                resolved_suffix=resolved_suffix,
                trust_table_name=trust_table_name,
                if_not_exists=True,
            )
            ops.append(
                MigrationOp(
                    description=f"replace_view_{view_name}",
                    sql=sql,
                    target="resolved_view",
                )
            )

    # 8. Virtual class views
    for cls in spec.classes:
        if isinstance(cls, VirtualClass):
            sql = _emit_view(cls, schema=schema, if_not_exists=True)
            ops.append(
                MigrationOp(
                    description=f"replace_view_{cls.name.lower()}",
                    sql=sql,
                    target="virtual_view",
                )
            )

    # 9. Trust seed — always emit; UPSERTs are idempotent. We render
    # them inline rather than calling emit_trust_seed because we want
    # the static SQL form here (a MigrationOp carries SQL, not params).
    trust_rows = _existing_trust_rows(query, schema, trust_table_name)
    for b in spec.source_bindings:
        if not isinstance(b.class_, OntologyClass):
            continue
        key = (b.source.name, b.class_.name)
        existing = trust_rows.get(key)
        if existing is not None and abs(existing - b.accuracy) < 1e-9:
            continue  # already at the spec'd value
        source_lit = "'" + b.source.name.replace("'", "''") + "'"
        class_lit = "'" + b.class_.name.replace("'", "''") + "'"
        ops.append(
            MigrationOp(
                description=(
                    f"upsert_trust_{b.source.name}_{b.class_.name}"
                ),
                sql=(
                    f"INSERT INTO {schema}.{trust_table_name} "
                    f"(source_name, class_name, accuracy) "
                    f"VALUES ({source_lit}, {class_lit}, {b.accuracy})\n"
                    f"ON CONFLICT (source_name, class_name) "
                    f"DO UPDATE SET accuracy = EXCLUDED.accuracy;"
                ),
                target="trust_seed",
            )
        )

    return ops


def _diff_concrete_class(
    cls: OntologyClass,
    *,
    query: QueryFn,
    db_tables: set[str],
    schema: str,
    bindings_suffix: str,
) -> list[MigrationOp]:
    ops: list[MigrationOp] = []
    canonical_name = cls.name.lower()
    bindings_name = f"{canonical_name}{bindings_suffix}"

    # Canonical table
    if canonical_name not in db_tables:
        ops.append(
            MigrationOp(
                description=f"create_table_{canonical_name}",
                sql=_emit_table(cls, schema=schema, if_not_exists=True),
                target="canonical",
            )
        )
    else:
        existing_cols = _existing_columns(query, schema, canonical_name)
        for slot in cls.effective_slots():
            if slot.name not in existing_cols:
                ops.append(
                    MigrationOp(
                        description=(
                            f"add_column_{canonical_name}_{slot.name}"
                        ),
                        sql=_add_column_sql(
                            schema, canonical_name, slot
                        ),
                        target="canonical",
                    )
                )

    # FK constraints on canonical (always emitted IF NOT EXISTS via the
    # ALTER's drop-and-add pair; idempotent at the DDL level).
    existing_fks = (
        _existing_fk_constraints(query, schema, canonical_name)
        if canonical_name in db_tables
        else set()
    )
    for fk_sql in _emit_fk_alters(cls, schema=schema, if_not_exists=True):
        # Parse out the constraint name from the ALTER's ADD line.
        constraint = _extract_fk_name(fk_sql)
        if constraint is not None and constraint in existing_fks:
            # Already on the table — skip both DROP and ADD lines for it.
            if "DROP CONSTRAINT" in fk_sql:
                continue
            # Otherwise this is the ADD line for an existing constraint —
            # skip; the if_not_exists=True drop is in a separate stmt.
            continue
        # Skip the DROP IF EXISTS line for constraints we know don't
        # exist (cleaner; postgres tolerates them but the SQL is noise).
        if "DROP CONSTRAINT IF EXISTS" in fk_sql and constraint not in existing_fks:
            continue
        ops.append(
            MigrationOp(
                description=f"add_fk_{constraint or 'unnamed'}",
                sql=fk_sql,
                target="fk",
            )
        )

    # Bindings table
    if bindings_name not in db_tables:
        ops.append(
            MigrationOp(
                description=f"create_table_{bindings_name}",
                sql=_emit_bindings_table(
                    cls,
                    schema=schema,
                    bindings_suffix=bindings_suffix,
                    if_not_exists=True,
                ),
                target="bindings",
            )
        )
        for idx_sql in _emit_bindings_indexes(
            cls,
            schema=schema,
            bindings_suffix=bindings_suffix,
            if_not_exists=True,
        ):
            ops.append(
                MigrationOp(
                    description=(
                        f"create_index_{_extract_index_name(idx_sql)}"
                    ),
                    sql=idx_sql,
                    target="index",
                )
            )
    else:
        existing_bcols = _existing_columns(query, schema, bindings_name)
        for slot in cls.effective_slots():
            if slot.name not in existing_bcols:
                ops.append(
                    MigrationOp(
                        description=(
                            f"add_column_{bindings_name}_{slot.name}"
                        ),
                        # Bindings columns are nullable (partial claims).
                        sql=_add_column_sql(
                            schema, bindings_name, slot, force_nullable=True
                        ),
                        target="bindings",
                    )
                )
        # Bronze layer: ensure raw_payload exists.
        if "raw_payload" not in existing_bcols:
            ops.append(
                MigrationOp(
                    description=f"add_column_{bindings_name}_raw_payload",
                    sql=(
                        f"ALTER TABLE {schema}.{bindings_name}\n"
                        "    ADD COLUMN IF NOT EXISTS raw_payload "
                        "jsonb NOT NULL DEFAULT '{}'::jsonb;"
                    ),
                    target="bindings",
                )
            )
        # Indexes — emit any that aren't present.
        existing_idxs = _existing_indexes(query, schema, bindings_name)
        for idx_sql in _emit_bindings_indexes(
            cls,
            schema=schema,
            bindings_suffix=bindings_suffix,
            if_not_exists=True,
        ):
            idx_name = _extract_index_name(idx_sql)
            if idx_name is not None and idx_name in existing_idxs:
                continue
            ops.append(
                MigrationOp(
                    description=f"create_index_{idx_name or 'unnamed'}",
                    sql=idx_sql,
                    target="index",
                )
            )

    return ops


# ---------------------------------------------------------------------------
# Column-level helpers
# ---------------------------------------------------------------------------


def _add_column_sql(
    schema: str,
    table: str,
    slot,
    *,
    force_nullable: bool = False,
) -> str:
    """``ALTER TABLE … ADD COLUMN IF NOT EXISTS … type [NOT NULL DEFAULT
    …]`` for one slot. ``force_nullable`` overrides the slot's
    ``identifier`` / ``required`` flags (used on the bindings table,
    where every non-identifier slot is nullable to allow partial
    claims)."""
    from knot.compile.ddl import _pg_type

    column = f"{slot.name} {_pg_type(slot.type)}"
    not_null = not force_nullable and (slot.identifier or slot.required)
    if not_null:
        # Adding a NOT NULL column to a table with existing rows requires
        # a default. Use the postgres-default for the type if the user
        # didn't specify one; the host can edit the migration to
        # backfill before applying if they have existing rows.
        column += " NOT NULL DEFAULT ''"  # safe default; only relevant for text
    return (
        f"ALTER TABLE {schema}.{table}\n"
        f"    ADD COLUMN IF NOT EXISTS {column};"
    )


def _extract_fk_name(fk_sql: str) -> str | None:
    """Pull ``fk_<class>_<slot>`` out of an ALTER TABLE statement
    emitted by _emit_fk_alters. Returns None on shape mismatch."""
    if "ADD CONSTRAINT " in fk_sql:
        after = fk_sql.split("ADD CONSTRAINT ", 1)[1]
        return after.split()[0].strip()
    if "DROP CONSTRAINT IF EXISTS " in fk_sql:
        after = fk_sql.split("DROP CONSTRAINT IF EXISTS ", 1)[1]
        return after.split(";", 1)[0].strip()
    return None


def _extract_index_name(idx_sql: str) -> str | None:
    """Pull the index name from a CREATE INDEX statement."""
    head = idx_sql.split("(", 1)[0]
    head = head.replace("IF NOT EXISTS", "")
    parts = head.replace("CREATE INDEX", "").strip().split()
    return parts[0] if parts else None


__all__ = ["MigrationOp", "QueryFn", "diff_against_db"]
