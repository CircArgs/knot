"""DDL emitter — pure function from a ``Spec`` to a list of SQL statements.

Emits one statement per artifact:

  - ``CREATE SCHEMA``                  for the data plane schema
  - ``CREATE TABLE <class>``           per concrete ``OntologyClass``
  - ``CREATE TABLE <class>_bindings``  per concrete ``OntologyClass``
                                       (optional via ``emit_bindings``)
  - ``CREATE VIEW <virtual>``          per ``VirtualClass``
  - ``COMMENT ON ...``                 per entity with a description
                                       (opt-in via ``emit_descriptions``)

Abstract classes get no table; their slots flow into concrete subclasses
via ``is_a`` walks. Bindings tables mirror the canonical class columns,
with an extra ``(source_name, source_identifier)`` layer and SCD2
``valid_from`` / ``valid_to``.
"""

from __future__ import annotations

from knot.ast.types import Array, ClassRef, Primitive, TypeExpression
from knot.compile.expr import compile_sql
from knot.spec import (
    ClassKind,
    OntologyClass,
    Spec,
    VirtualClass,
)

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def emit_ddl(
    spec: Spec,
    *,
    schema: str = "knot_data",
    bindings_suffix: str = "_bindings",
    resolved_suffix: str = "_resolved",
    trust_table_name: str = "source_trust",
    if_not_exists: bool = False,
    emit_bindings: bool = True,
    emit_resolved_views: bool = True,
    emit_fk_references: bool = True,
    emit_indexes: bool = True,
    emit_trust_table: bool = True,
    emit_descriptions: bool = False,
) -> list[str]:
    """Return the DDL statements that materialize ``spec``.

    Parameters
    ----------
    schema
        Postgres schema for the data plane. Created with ``IF NOT EXISTS``
        regardless of ``if_not_exists``.
    bindings_suffix
        Suffix appended to the class table name to form the SCD2
        bindings table name.
    resolved_suffix
        Suffix appended to the class name for the resolved-view that
        argmaxes across currently-open bindings.
    if_not_exists
        When True, emit ``CREATE TABLE IF NOT EXISTS`` and ``CREATE OR
        REPLACE VIEW``. Use for re-runnable migrations. FK constraints
        are emitted as ``DROP CONSTRAINT IF EXISTS`` + ``ADD
        CONSTRAINT``, and indexes use ``CREATE INDEX IF NOT EXISTS``.
    emit_bindings
        When False, skip the ``<class>_bindings`` tables entirely.
    emit_resolved_views
        When False, skip the ``<class>_resolved`` views. Set to False
        for write-direct workflows that hit the canonical table.
    emit_fk_references
        When True, emit ``ALTER TABLE … ADD CONSTRAINT … FOREIGN KEY``
        for every ``ClassRef`` slot on every concrete canonical table,
        targeting the referenced class's identifier column. Bindings
        tables intentionally stay loose (a binding may claim about a
        canonical that doesn't exist yet).
    emit_indexes
        When True, emit partial indexes on each ``<class>_bindings``
        table that match the resolver's per-slot lookup and the SCD2
        close-out hot paths (both filter on ``valid_to IS NULL``).
    emit_trust_table
        When True, emit the invariant ``<schema>.<trust_table_name>``
        (default ``source_trust``) that carries the runtime per-(source,
        class, slot) trust. The resolver views ``LEFT JOIN`` against
        this table; seed its rows from the spec via
        ``knot.compile.trust.emit_trust_seed``.
    emit_descriptions
        When True, follow each entity with ``COMMENT ON TABLE / COLUMN /
        VIEW`` for any non-empty ``description`` fields.
    """
    # Local import — resolver imports from knot.spec, ddl imports from
    # knot.spec; resolver doesn't import from ddl, so no cycle.
    from knot.compile.resolver import emit_resolved_view

    stmts: list[str] = [f"CREATE SCHEMA IF NOT EXISTS {schema};"]

    # Invariant trust-policy table — must exist before any resolved
    # view that LEFT JOINs against it.
    if emit_trust_table:
        stmts.append(
            _emit_trust_table(
                schema=schema,
                trust_table_name=trust_table_name,
                if_not_exists=if_not_exists,
            )
        )

    for cls in spec.classes:
        match cls:
            case OntologyClass(kind=ClassKind.CONCRETE):
                stmts.append(
                    _emit_table(cls, schema=schema, if_not_exists=if_not_exists)
                )
                if emit_descriptions:
                    stmts.extend(_emit_class_comments(cls, schema=schema))
                if emit_bindings:
                    stmts.append(
                        _emit_bindings_table(
                            cls,
                            schema=schema,
                            bindings_suffix=bindings_suffix,
                            if_not_exists=if_not_exists,
                        )
                    )
                    if emit_indexes:
                        stmts.extend(
                            _emit_bindings_indexes(
                                cls,
                                schema=schema,
                                bindings_suffix=bindings_suffix,
                                if_not_exists=if_not_exists,
                            )
                        )
                if emit_resolved_views and emit_bindings:
                    # Resolved view depends on the bindings table existing.
                    stmts.append(
                        emit_resolved_view(
                            spec,
                            cls,
                            schema=schema,
                            bindings_suffix=bindings_suffix,
                            resolved_suffix=resolved_suffix,
                            trust_table_name=trust_table_name,
                            if_not_exists=if_not_exists,
                        )
                    )
            case VirtualClass():
                stmts.append(
                    _emit_view(cls, schema=schema, if_not_exists=if_not_exists)
                )
                if emit_descriptions and cls.description:
                    stmts.append(
                        _comment_on(
                            "VIEW",
                            f"{schema}.{cls.name.lower()}",
                            cls.description,
                        )
                    )
            # Abstract OntologyClass falls through (no table).

    # Second pass: FK constraints on canonical class tables. Emitted
    # after every CREATE TABLE so target tables exist regardless of
    # spec.classes order.
    if emit_fk_references:
        for cls in spec.concrete_classes():
            stmts.extend(
                _emit_fk_alters(cls, schema=schema, if_not_exists=if_not_exists)
            )

    return stmts


# ---------------------------------------------------------------------------
# Type expression → postgres type
# ---------------------------------------------------------------------------


_PRIMITIVE_TO_PG: dict[Primitive, str] = {
    Primitive.TEXT: "text",
    Primitive.INTEGER: "integer",
    Primitive.FLOAT: "double precision",
    Primitive.BOOLEAN: "boolean",
    Primitive.DATE: "date",
    Primitive.TIMESTAMP: "timestamptz",
}


def _pg_type(t: TypeExpression) -> str:
    match t:
        case Primitive():
            return _PRIMITIVE_TO_PG[t]
        case Array(of=inner):
            return f"{_pg_type(inner)}[]"
        case ClassRef():
            # canonical_id FK, stored as text. The cross-table FK
            # constraint is added by the second-pass ALTER TABLE.
            return "text"
    raise TypeError(f"unhandled type expression: {type(t).__name__}")


# ---------------------------------------------------------------------------
# Per-entity emitters
# ---------------------------------------------------------------------------


def _create_table(*, if_not_exists: bool) -> str:
    return "CREATE TABLE IF NOT EXISTS" if if_not_exists else "CREATE TABLE"


def _create_view(*, if_not_exists: bool) -> str:
    return "CREATE OR REPLACE VIEW" if if_not_exists else "CREATE VIEW"


def _emit_table(cls: OntologyClass, *, schema: str, if_not_exists: bool) -> str:
    columns: list[str] = []
    pk_cols: list[str] = []
    for slot in cls.effective_slots():
        col = f"    {slot.name} {_pg_type(slot.type)}"
        if slot.identifier or slot.required:
            col += " NOT NULL"
        columns.append(col)
        if slot.identifier:
            pk_cols.append(slot.name)
    if pk_cols:
        columns.append(f"    PRIMARY KEY ({', '.join(pk_cols)})")
    body = ",\n".join(columns)
    return f"{_create_table(if_not_exists=if_not_exists)} {schema}.{cls.name.lower()} (\n{body}\n);"


def _emit_view(vc: VirtualClass, *, schema: str, if_not_exists: bool) -> str:
    parent = vc.is_a.name.lower()
    # VirtualClass.definition is an Expr (from knot.expr). The view
    # selects from the *canonical* parent table, so refs in the
    # predicate render with no resolved-suffix.
    body_sql = compile_sql(vc.definition, schema=schema, target_suffix="")
    return (
        f"{_create_view(if_not_exists=if_not_exists)} "
        f"{schema}.{vc.name.lower()} AS\n"
        f"SELECT * FROM {schema}.{parent}\n"
        f"WHERE {body_sql};"
    )


def _emit_bindings_table(
    cls: OntologyClass,
    *,
    schema: str,
    bindings_suffix: str,
    if_not_exists: bool,
) -> str:
    columns: list[str] = [
        "    source_name text NOT NULL",
        "    source_identifier text NOT NULL",
    ]
    for slot in cls.effective_slots():
        # All slots are nullable in bindings — including the identifier.
        # Bronze-layer ingest writes source rows BEFORE ER assigns a
        # canonical_id; the resolved view filters those rows out via
        # ``WHERE <ident> IS NOT NULL`` until ER claims them. A source
        # may also only project some non-identifier slots; those are
        # nullable for the same reason.
        col = f"    {slot.name} {_pg_type(slot.type)}"
        columns.append(col)
    # Bronze-layer raw payload — the full row as ingested, preserved
    # for backfilling new slots later without re-ingesting from the
    # source. Always populated by emit_batch_write; default '{}' lets
    # legacy bindings rows satisfy NOT NULL after an ALTER.
    columns.append("    raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb")
    columns.append("    valid_from timestamptz NOT NULL DEFAULT now()")
    columns.append("    valid_to timestamptz")
    # PK is (source_name, source_identifier, valid_from) — one open row
    # per source/source_identifier at any moment, and history tracked
    # via SCD2 valid_from/valid_to. canonical_id is NOT part of the PK
    # so it can start NULL and be assigned later by an async ER worker.
    columns.append("    PRIMARY KEY (source_name, source_identifier, valid_from)")
    body = ",\n".join(columns)
    return (
        f"{_create_table(if_not_exists=if_not_exists)} "
        f"{schema}.{cls.name.lower()}{bindings_suffix} (\n{body}\n);"
    )


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


def _escape_comment(text: str) -> str:
    """Postgres SQL string literal escaping — double single-quotes."""
    return text.replace("'", "''")


def _comment_on(kind: str, ident: str, text: str) -> str:
    return f"COMMENT ON {kind} {ident} IS '{_escape_comment(text)}';"


def _emit_class_comments(cls: OntologyClass, *, schema: str) -> list[str]:
    out: list[str] = []
    table_id = f"{schema}.{cls.name.lower()}"
    if cls.description:
        out.append(_comment_on("TABLE", table_id, cls.description))
    for slot in cls.effective_slots():
        if slot.description:
            out.append(
                _comment_on("COLUMN", f"{table_id}.{slot.name}", slot.description)
            )
    return out


# ---------------------------------------------------------------------------
# Foreign-key constraint emission (second pass over the canonical tables)
# ---------------------------------------------------------------------------


def _emit_fk_alters(
    cls: OntologyClass,
    *,
    schema: str,
    if_not_exists: bool,
) -> list[str]:
    """For every ``ClassRef`` slot on ``cls``, emit an ``ALTER TABLE``
    that adds a foreign-key constraint to the target's canonical
    identifier. Only emitted for the canonical table — bindings tables
    intentionally stay loose because a binding can claim about a
    canonical that doesn't exist yet.

    Constraint names are deterministic (``fk_<class>_<slot>``); when
    ``if_not_exists`` is True the alter is preceded by ``DROP
    CONSTRAINT IF EXISTS`` so the pass is idempotent.
    """
    table = f"{schema}.{cls.name.lower()}"
    out: list[str] = []
    for slot in cls.effective_slots():
        if not isinstance(slot.type, ClassRef):
            continue
        target = slot.type.target
        target_table = f"{schema}.{target.name.lower()}"
        target_pk = target.identifier_slot().name
        constraint = f"fk_{cls.name.lower()}_{slot.name}"
        if if_not_exists:
            out.append(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint};")
        out.append(
            f"ALTER TABLE {table} ADD CONSTRAINT {constraint}\n"
            f"    FOREIGN KEY ({slot.name}) "
            f"REFERENCES {target_table}({target_pk});"
        )
    return out


# ---------------------------------------------------------------------------
# Bindings table indexes — match the two hot paths against the SCD2 layout
# ---------------------------------------------------------------------------


def _emit_bindings_indexes(
    cls: OntologyClass,
    *,
    schema: str,
    bindings_suffix: str,
    if_not_exists: bool,
) -> list[str]:
    """Partial indexes on the bindings table sized to the two hot paths:

    1. Resolver per-slot lookup: ``WHERE canonical_id = X AND valid_to
       IS NULL`` — covered by a partial index on the identifier column.
    2. SCD2 close-out: ``WHERE (canonical_id, source_name,
       source_identifier) = (X, Y, Z) AND valid_to IS NULL`` — covered
       by a composite partial index on those three columns.

    Both indexes are partial (``WHERE valid_to IS NULL``) because the
    bindings table accumulates closed-out rows forever; the index only
    needs to be O(open rows), not O(all rows).
    """
    table_name = f"{cls.name.lower()}{bindings_suffix}"
    table = f"{schema}.{table_name}"
    ident = cls.identifier_slot()
    maybe_if_not_exists = "IF NOT EXISTS " if if_not_exists else ""

    return [
        f"CREATE INDEX {maybe_if_not_exists}{table_name}_current_idx\n"
        f"    ON {table} ({ident.name})\n"
        f"    WHERE valid_to IS NULL;",
        f"CREATE INDEX {maybe_if_not_exists}{table_name}_source_idx\n"
        f"    ON {table} ({ident.name}, source_name, source_identifier)\n"
        f"    WHERE valid_to IS NULL;",
    ]


# ---------------------------------------------------------------------------
# Trust-policy table — runtime tuning surface for source accuracies
# ---------------------------------------------------------------------------


def _emit_trust_table(
    *,
    schema: str,
    trust_table_name: str,
    if_not_exists: bool,
) -> str:
    """Invariant table carrying the runtime per-(source, class, slot)
    trust. The resolver views ``LEFT JOIN`` against this table; operators
    tune trust with plain ``UPDATE`` statements without redeploying."""
    ct = "CREATE TABLE IF NOT EXISTS" if if_not_exists else "CREATE TABLE"
    return (
        f"{ct} {schema}.{trust_table_name} (\n"
        "    source_name text NOT NULL,\n"
        "    class_name  text NOT NULL,\n"
        "    slot_name   text NOT NULL,\n"
        "    trust       double precision NOT NULL\n"
        "                CHECK (trust >= 0 AND trust <= 1),\n"
        "    PRIMARY KEY (source_name, class_name, slot_name)\n"
        ");"
    )
