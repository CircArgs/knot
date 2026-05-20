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
via ``is_a`` walks. Bindings tables carry every slot the class declares
plus a ``(source_name, source_identifier)`` PK pair, ``raw_payload`` for
unmapped extras, and ``er_metadata`` stamped at ER time. Writes are
upserts on the PK — one row per (source, source_id), not SCD2 history.
"""

from __future__ import annotations

from knot.ast.select import Layer
from knot.ast.types import Array, ClassRef, Primitive, TypeExpression, Vector
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
    all_sources_suffix: str = "_all_sources",
    weight_table_name: str = "source_weight",
    if_not_exists: bool = False,
    emit_bindings: bool = True,
    emit_resolved_views: bool = True,
    emit_all_sources_views: bool = True,
    emit_virtual_views: bool = True,
    emit_indexes: bool = True,
    emit_weight_table: bool = True,
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
    all_sources_suffix
        Suffix appended to the class name for the all-sources /
        provenance view that emits one ``jsonb`` per slot keyed by
        source name.
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
    emit_all_sources_views
        When False, skip the ``<class>_all_sources`` provenance views.
        Set to False for deployments that only need the resolved
        layer.
    emit_indexes
        When True, emit a btree index on ``canonical_id`` for each
        ``<class>_bindings`` table — covers the resolver's per-slot
        argmax lookup and recanonicalize's cascade. The PK on
        ``(source_name, source_identifier)`` already covers
        ingest/ER lookups by source key.
    emit_weight_table
        When True, emit the invariant ``<schema>.<weight_table_name>``
        (default ``source_weight``) that carries the runtime
        per-(source, class, slot) weight. The resolver views
        ``LEFT JOIN`` against this table; the host upserts rows at
        runtime via ``binding.upsert_weight_sql()``.
    emit_descriptions
        When True, follow each entity with ``COMMENT ON TABLE / COLUMN /
        VIEW`` for any non-empty ``description`` fields.
    """
    # Local import — resolver imports from knot.spec, ddl imports from
    # knot.spec; resolver doesn't import from ddl, so no cycle.
    from knot.compile.resolver import emit_all_sources_view, emit_resolved_view

    stmts: list[str] = [f"CREATE SCHEMA IF NOT EXISTS {schema};"]

    # pgvector extension — only emit when the spec actually uses it.
    # Idempotent via IF NOT EXISTS; database superuser may be required
    # the first time it runs depending on the postgres install.
    if _spec_has_vector_slot(spec):
        stmts.append("CREATE EXTENSION IF NOT EXISTS vector;")

    # Invariant weight-policy table — must exist before any resolved
    # view that LEFT JOINs against it.
    if emit_weight_table:
        stmts.append(
            _emit_weight_table(
                schema=schema,
                weight_table_name=weight_table_name,
                if_not_exists=if_not_exists,
            )
        )

    for cls in spec.classes.values():
        match cls:
            case OntologyClass(kind=ClassKind.CONCRETE):
                # No canonical table — bindings is the only relation
                # per class. The resolved view sources canonical_ids
                # via SELECT DISTINCT over bindings.
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
                        stmts.extend(
                            _emit_vector_indexes(
                                cls,
                                table_name=f"{cls.name.lower()}{bindings_suffix}",
                                schema=schema,
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
                            weight_table_name=weight_table_name,
                            if_not_exists=if_not_exists,
                        )
                    )
                if emit_all_sources_views and emit_bindings:
                    # Provenance view — same bindings + weight dependency.
                    stmts.append(
                        emit_all_sources_view(
                            spec,
                            cls,
                            schema=schema,
                            bindings_suffix=bindings_suffix,
                            all_sources_suffix=all_sources_suffix,
                            weight_table_name=weight_table_name,
                            if_not_exists=if_not_exists,
                        )
                    )
            # VirtualClass handled below in dependency order.
            # Abstract OntologyClass falls through (no table).

    # Emit virtual views in topological order (shallowest depth first) so
    # a nested virtual's CREATE VIEW can reference its parent virtual's
    # view, which must already exist.
    if emit_virtual_views:
        virtual_classes = sorted(
            (c for c in spec.classes.values() if isinstance(c, VirtualClass)),
            key=_virtual_depth,
        )
        for vc in virtual_classes:
            stmts.append(_emit_view(vc, schema=schema, if_not_exists=if_not_exists))
            if emit_descriptions and vc.description:
                stmts.append(
                    _comment_on(
                        "VIEW",
                        f"{schema}.{vc.name.lower()}",
                        vc.description,
                    )
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
        case Vector(dim=dim):
            # pgvector type. The HNSW index is emitted separately so a
            # spec with vector slots needs the ``vector`` extension
            # present (knot emits ``CREATE EXTENSION IF NOT EXISTS
            # vector`` once at the top of Spec.ddl() when any vector slot
            # exists).
            return f"vector({dim})"
    raise TypeError(f"unhandled type expression: {type(t).__name__}")


def _spec_has_vector_slot(spec: Spec) -> bool:
    """True if any concrete class in ``spec`` declares a vector slot.
    Used to gate ``CREATE EXTENSION IF NOT EXISTS vector;`` emission."""
    for cls in spec.classes.values():
        if not isinstance(cls, OntologyClass) or cls.kind != ClassKind.CONCRETE:
            continue
        for slot in cls.effective_slots():
            if isinstance(slot.type, Vector):
                return True
    return False


def _emit_vector_indexes(
    cls: OntologyClass,
    *,
    table_name: str,
    schema: str,
    if_not_exists: bool,
) -> list[str]:
    """One HNSW index per vector slot on ``cls``, against ``table_name``.
    The operator class is picked by the slot's metric (``cosine`` ⇒
    ``vector_cosine_ops``, etc.)."""
    maybe_if_not_exists = "IF NOT EXISTS " if if_not_exists else ""
    out: list[str] = []
    for slot in cls.effective_slots():
        if not isinstance(slot.type, Vector):
            continue
        idx = f"{table_name}_{slot.name}_hnsw_idx"
        out.append(
            f"CREATE INDEX {maybe_if_not_exists}{idx}\n"
            f"    ON {schema}.{table_name}\n"
            f"    USING hnsw ({slot.name} {slot.type.hnsw_ops});"
        )
    return out


# ---------------------------------------------------------------------------
# Per-entity emitters
# ---------------------------------------------------------------------------


def _create_table(*, if_not_exists: bool) -> str:
    return "CREATE TABLE IF NOT EXISTS" if if_not_exists else "CREATE TABLE"


def _create_view(*, if_not_exists: bool) -> str:
    return "CREATE OR REPLACE VIEW" if if_not_exists else "CREATE VIEW"


def _virtual_depth(vc: VirtualClass) -> int:
    """Depth of ``vc`` in the virtual is_a chain (0 = parent is OntologyClass)."""
    depth = 0
    node = vc.is_a
    while isinstance(node, VirtualClass):
        depth += 1
        node = node.is_a
    return depth


def _emit_view(vc: VirtualClass, *, schema: str, if_not_exists: bool) -> str:
    # The concrete root class drives the ``this.<Name>`` binding for
    # correlated aggregates — it flows unchanged through the nested
    # SELECT * chain, so every level binds against the same outer row.
    concrete_root = vc.concrete_root()

    # FROM targets the IMMEDIATE parent:
    #   - OntologyClass parent → <parent>_resolved
    #   - VirtualClass parent  → <parent_virtual_name> (the parent's own view)
    if isinstance(vc.is_a, OntologyClass):
        from_clause = f"{schema}.{vc.is_a.name.lower()}_resolved"
    else:
        from_clause = f"{schema}.{vc.is_a.name.lower()}"

    # WHERE contains ONLY this virtual's own definition.  The parent
    # virtual's view already filters by its own definition, so we don't
    # re-AND it here.
    body_sql = compile_sql(
        vc.definition,
        schema=schema,
        layer=Layer.RESOLVED,
        outer_class=concrete_root.name,
    )
    return (
        f"{_create_view(if_not_exists=if_not_exists)} "
        f"{schema}.{vc.name.lower()} AS\n"
        f"SELECT * FROM {from_clause}\n"
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
        # Ingest writes source rows BEFORE ER assigns a canonical_id;
        # the resolved view filters those rows out via ``WHERE <ident>
        # IS NOT NULL`` until ER claims them. A source may also only
        # project some non-identifier slots; those are nullable for the
        # same reason.
        col = f"    {slot.name} {_pg_type(slot.type)}"
        columns.append(col)
    # raw_payload — the full row as ingested, preserved for backfilling
    # new slots later without re-ingesting from the source. Always
    # populated by emit_batch_write; default '{}' lets legacy bindings
    # rows satisfy NOT NULL after an ALTER.
    columns.append("    raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb")
    # er_metadata — caller-defined audit payload stamped at ER time
    # (run id, method, confidence, …). Empty by default; set by
    # emit_assign_canonical / emit_recanonicalize when callers pass
    # ``er_metadata={...}``. Same shape as raw_payload, different author.
    columns.append("    er_metadata jsonb NOT NULL DEFAULT '{}'::jsonb")
    # PK is (source_name, source_identifier) — one row per
    # (source, source_id) ever; re-ingest is an UPSERT, not a new
    # validity period. canonical_id is NOT part of the PK so it can
    # start NULL and be assigned later by ER. Audit/history is a
    # separate concern — sink via triggers / CDC if needed.
    columns.append("    PRIMARY KEY (source_name, source_identifier)")
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


# ---------------------------------------------------------------------------
# Bindings table indexes — resolver's per-canonical lookup
# ---------------------------------------------------------------------------


def _emit_bindings_indexes(
    cls: OntologyClass,
    *,
    schema: str,
    bindings_suffix: str,
    if_not_exists: bool,
) -> list[str]:
    """One btree index on canonical_id — covers the resolver's per-slot
    argmax lookup (``WHERE canonical_id = X``) and the recanonicalize
    cascade (``WHERE <fk_slot> = <old_canonical_id>``). Lookups by
    ``(source_name, source_identifier)`` are already covered by the
    PK, so no separate composite index is needed."""
    table_name = f"{cls.name.lower()}{bindings_suffix}"
    table = f"{schema}.{table_name}"
    ident = cls.identifier_slot()
    maybe_if_not_exists = "IF NOT EXISTS " if if_not_exists else ""

    return [
        f"CREATE INDEX {maybe_if_not_exists}{table_name}_canonical_idx\n"
        f"    ON {table} ({ident.name});",
    ]


# ---------------------------------------------------------------------------
# Weight-policy table — runtime tuning surface for source/slot weights
# ---------------------------------------------------------------------------


def _emit_weight_table(
    *,
    schema: str,
    weight_table_name: str,
    if_not_exists: bool,
) -> str:
    """Invariant table carrying the runtime per-(source, class, slot)
    weight. The resolver views ``LEFT JOIN`` against this table;
    operators tune weights with plain ``UPDATE`` statements without
    redeploying. Weight values are opaque floats — knot does not
    constrain or interpret them; the higher value wins the argmax."""
    ct = "CREATE TABLE IF NOT EXISTS" if if_not_exists else "CREATE TABLE"
    return (
        f"{ct} {schema}.{weight_table_name} (\n"
        "    source_name text NOT NULL,\n"
        "    class_name  text NOT NULL,\n"
        "    slot_name   text NOT NULL,\n"
        "    weight      double precision NOT NULL,\n"
        "    PRIMARY KEY (source_name, class_name, slot_name)\n"
        ");"
    )
