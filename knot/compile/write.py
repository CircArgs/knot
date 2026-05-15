"""Write-data path — batched SCD2 binding writes.

Single primitive: ``emit_batch_write`` takes a list of ``ClassWrites``
(one per class participating in the batch) and returns a single
transactional SQL script the host runs in one ``execute(sql, params)``
call. Single-row writes are just a one-element list with one row.

For each ``ClassWrites`` the emitter produces:

  1. **bulk close-out** — ``UPDATE … FROM jsonb_array_elements(…)`` that
     sets ``valid_to = now()`` on every prior currently-open binding for
     the ``(identifier, source_name, source_identifier)`` triples in the
     batch.
  2. **bulk insert** — ``INSERT … SELECT FROM jsonb_array_elements(…)``.
     When ``use_mappings=True`` the binding's per-slot SQL projections
     are applied server-side over the raw source fields; otherwise the
     row dicts are interpreted as direct slot values.

When ``enforce=True`` the script ends with a PL/pgSQL ``DO`` block that
runs every validation SELECT whose primary is one of the affected
classes (error severity only) and ``RAISE EXCEPTION`` on any violation;
the surrounding transaction rolls back automatically.

Row payloads are passed as one ``jsonb`` parameter per class, named
``<class_lowercase>_rows``. Postgres' ``jsonb_array_elements`` iterates
each element as ``r`` so mapping expressions reference its keys as bare
columns through the ``raw`` subquery alias (no rewriting of the
mappings is required).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from knot.compile.constraints import emit_validation
from knot.spec import (
    Array,
    ClassKind,
    ClassRef,
    OntologyClass,
    Primitive,
    Slot,
    SourceBinding,
    Spec,
    TypeExpression,
)

# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClassWrites:
    """One class's contribution to a batch write.

    ``binding`` carries the (source, class) identity and the per-slot
    mappings (``binding.slot_mappings``). ``rows`` is a list of dicts
    keyed by **raw source field names** — i.e. the ``source_slot``
    names declared in the binding. Slots without an explicit
    ``.slot(...)`` mapping default to a same-name passthrough: row key
    equals class slot name.

    Every row must also include ``source_identifier`` (the source's
    own opaque ID, used for SCD2 close-out).
    """

    binding: SourceBinding
    rows: list[dict[str, Any]]


@dataclass(frozen=True)
class BatchWrite:
    """A multi-class batch write as a list of single-statement
    ``(sql, params)`` tuples.

    Each tuple is one already-parameterized statement the host runs
    via ``cursor.execute(sql, params)``. Multi-statement scripts +
    params don't mix with postgres' prepared-statement protocol, so
    the emitter splits at compile time — the host doesn't need to
    know about any blank-line convention.

    Run them in order inside a transaction:

        with pg.transaction():
            with pg.cursor() as cur:
                for sql, params in bw.statements:
                    cur.execute(sql, params)
    """

    statements: list[tuple[str, dict[str, Any]]]
    affected_classes: tuple[str, ...]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_concrete(cls: OntologyClass) -> None:
    if cls.kind != ClassKind.CONCRETE:
        raise ValueError(
            f"class {cls.name!r} is {cls.kind.value!r}; only concrete classes have bindings tables"
        )


def _bindings_id(cls: OntologyClass, *, schema: str, suffix: str) -> str:
    return f"{schema}.{cls.name.lower()}{suffix}"


def _sql_literal(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


_PRIMITIVE_TO_JSONB_CAST: dict[Primitive, str] = {
    Primitive.TEXT: "::text",
    Primitive.INTEGER: "::integer",
    Primitive.FLOAT: "::double precision",
    Primitive.BOOLEAN: "::boolean",
    Primitive.DATE: "::date",
    Primitive.TIMESTAMP: "::timestamptz",
}


def _jsonb_cast(t: TypeExpression) -> str:
    """Return a postgres cast suffix that pulls a typed value out of a
    jsonb-element row. Handles primitive, array, and class-ref slots."""
    match t:
        case Primitive():
            return _PRIMITIVE_TO_JSONB_CAST[t]
        case Array(of=inner):
            # ARRAY-coerce a jsonb array of scalars into a postgres array.
            return f"::{_jsonb_cast(inner).removeprefix('::')}[]"
        case ClassRef():
            return "::text"
    raise TypeError(f"unhandled type: {type(t).__name__}")


def _jsonb_extract(slot: Slot, raw_field: str | None = None) -> str:
    """SQL fragment that pulls ``raw_field`` (or ``slot.name`` if not
    given) out of the ``r`` jsonb element with the right cast."""
    field = raw_field or slot.name
    cast = _jsonb_cast(slot.type)
    if isinstance(slot.type, Array):
        # jsonb arrays must round-trip through unnest+ARRAY because
        # direct ``::text[]`` on a jsonb array doesn't work.
        inner = cast.removeprefix("::").removesuffix("[]")
        return (
            f"(SELECT ARRAY(SELECT (value #>> '{{}}')::{inner} "
            f"FROM jsonb_array_elements(r->'{field}') AS value))"
        )
    return f"(r->>'{field}'){cast}"


# ---------------------------------------------------------------------------
# Per-class emitters
# ---------------------------------------------------------------------------


def _emit_raw_subquery(
    binding: SourceBinding,
    rows_param: str,
) -> str:
    """Build the ``FROM (...) AS raw`` subquery for a binding.

    Exposes ``source_identifier`` plus every source field referenced
    by any effective mapping (explicit or implicit-passthrough) as a
    text alias. ``r`` itself passes through as ``__raw_payload`` so
    the outer SELECT can populate the bindings table's ``raw_payload``
    column with the full ingested shape (bronze layer)."""
    raw_fields: list[str] = ["source_identifier"]
    seen = set(raw_fields)
    for slot in binding.class_.effective_slots():
        m = binding.effective_mapping(slot.name)
        for src in m.source_slot:
            if src not in seen:
                raw_fields.append(src)
                seen.add(src)

    alias_lines = ["        r AS __raw_payload"]
    alias_lines.extend(f"        (r->>'{f}') AS {f}" for f in raw_fields)
    aliases = ",\n".join(alias_lines)
    return (
        "FROM (\n"
        "    SELECT\n"
        f"{aliases}\n"
        f"    FROM jsonb_array_elements(%({rows_param})s::jsonb) AS r\n"
        ") AS raw"
    )


def _passthrough_value(slot: Slot, raw_field: str) -> str:
    """Render the value for a slot in a passthrough mapping (no SQL
    transform), using the raw subquery's text alias for ``raw_field``."""
    if isinstance(slot.type, Primitive):
        return f"raw.{raw_field}{_PRIMITIVE_TO_JSONB_CAST[slot.type]}"
    if isinstance(slot.type, ClassRef):
        return f"raw.{raw_field}::text"
    if isinstance(slot.type, Array):
        # Array passthrough needs the original jsonb element (text →
        # text[] doesn't cast directly). Use the preserved ``__raw_payload``.
        inner = _jsonb_cast(slot.type.of).removeprefix("::").removesuffix("[]")
        return (
            f"(SELECT ARRAY(SELECT (value #>> '{{}}')::{inner} "
            f"FROM jsonb_array_elements(raw.__raw_payload->'{raw_field}') AS value))"
        )
    raise TypeError(f"unhandled slot type: {type(slot.type).__name__}")


def _emit_class_insert(
    cw: ClassWrites,
    *,
    schema: str,
    bindings_suffix: str,
    rows_param: str,
) -> str:
    cls = cw.binding.class_
    _check_concrete(cls)
    table = _bindings_id(cls, schema=schema, suffix=bindings_suffix)
    source_literal = _sql_literal(cw.binding.source.name)
    eff_slots = cls.effective_slots()
    # raw_payload always trails the slot columns; preserves the full
    # ingested row so unmapped fields are recoverable later.
    insert_columns = (
        ["source_name", "source_identifier"]
        + [s.name for s in eff_slots]
        + ["raw_payload"]
    )
    columns_csv = ", ".join(insert_columns)

    raw_subquery = _emit_raw_subquery(cw.binding, rows_param)
    select_lines: list[str] = [
        f"    {source_literal}",
        "    raw.source_identifier",
    ]
    for slot in eff_slots:
        m = cw.binding.effective_mapping(slot.name)
        if m.sql is not None:
            # Explicit SQL — user owns casting; the SQL references the
            # raw subquery's text aliases by bare name (postgres resolves
            # them to ``raw.<name>`` via the FROM alias).
            select_lines.append(f"    {m.sql}")
        else:
            select_lines.append(f"    {_passthrough_value(slot, m.source_slot[0])}")
    select_lines.append("    raw.__raw_payload")
    return (
        f"INSERT INTO {table} ({columns_csv})\n"
        "SELECT\n" + ",\n".join(select_lines) + "\n" + raw_subquery + ";"
    )


def _emit_class_close_out(
    cw: ClassWrites,
    *,
    schema: str,
    bindings_suffix: str,
    rows_param: str,
) -> str:
    cls = cw.binding.class_
    _check_concrete(cls)
    ident = cls.identifier_slot()
    table = _bindings_id(cls, schema=schema, suffix=bindings_suffix)
    source_literal = _sql_literal(cw.binding.source.name)
    # In mapped mode the identifier lives under raw.<ident_name>; in
    # direct-slot mode it's under r->>'<ident_name>'. We use jsonb
    # extraction in both cases for the close-out — simpler.
    return (
        f"UPDATE {table} AS b\n"
        f"SET valid_to = now()\n"
        f"FROM (\n"
        f"    SELECT\n"
        f"        (r->>'{ident.name}') AS {ident.name},\n"
        f"        (r->>'source_identifier') AS source_identifier\n"
        f"    FROM jsonb_array_elements(%({rows_param})s::jsonb) AS r\n"
        f") AS keys\n"
        f"WHERE b.{ident.name} = keys.{ident.name}\n"
        f"  AND b.source_name = {source_literal}\n"
        f"  AND b.source_identifier = keys.source_identifier\n"
        f"  AND b.valid_to IS NULL;"
    )


# ---------------------------------------------------------------------------
# Enforcement block — PL/pgSQL DO that RAISEs on error-severity violations
# ---------------------------------------------------------------------------


def _emit_enforcement_block(
    spec: Spec,
    affected_classes: set[str],
    *,
    schema: str,
) -> str | None:
    """Return a PL/pgSQL DO block that runs validation for the affected
    classes' constraints and raises on any error-severity violation, or
    ``None`` if no error-severity constraints touch the batch.

    Note: validation SELECTs target the canonical table (not bindings)
    by emit_validation's current contract. Until the resolver maintains
    the canonical view from bindings, in-batch enforcement only catches
    constraints checkable against canonical state the host writes
    directly. Retargeting validation to the bindings-current view is a
    separate follow-up item.
    """
    relevant_names: list[str] = []
    for c in spec.constraints:
        if c.primary.name not in affected_classes:
            continue
        if c.severity.value != "error":
            continue
        relevant_names.append(c.name)
    if not relevant_names:
        return None

    # Reuse emit_validation to get fully-rewritten SELECTs, then filter.
    all_v = dict(emit_validation(spec, schema=schema))
    union_parts = []
    for name in relevant_names:
        if name not in all_v:
            continue
        # Strip the trailing ';' for UNION ALL composition.
        union_parts.append(all_v[name].rstrip().rstrip(";"))
    if not union_parts:
        return None
    union_sql = "\n  UNION ALL\n".join(union_parts)

    return (
        "DO $$\n"
        "DECLARE v RECORD;\n"
        "BEGIN\n"
        "  FOR v IN\n"
        f"{union_sql}\n"
        "  LOOP\n"
        "    RAISE EXCEPTION "
        "'knot constraint violated: rule=% class=% pk=% message=%',\n"
        "      v.rule_id, v.class_name, v.offending_pk, COALESCE(v.message, '');\n"
        "  END LOOP;\n"
        "END $$;"
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def emit_batch_write(
    spec: Spec,
    writes: list[ClassWrites],
    *,
    schema: str = "knot_data",
    bindings_suffix: str = "_bindings",
    enforce: bool = True,
) -> BatchWrite:
    """Return a multi-class batch write as a list of per-statement
    ``(sql, params)`` tuples. The host runs them in a transaction.

    Each class contributes two statements (close-out + insert) that
    share one jsonb param. The optional enforcement DO block has no
    params and is appended last when ``enforce=True``.
    """
    if not writes:
        raise ValueError("emit_batch_write: writes list is empty")

    statements: list[tuple[str, dict[str, Any]]] = []
    used_param_keys: set[str] = set()
    affected: set[str] = set()

    for cw in writes:
        cls = cw.binding.class_
        _check_concrete(cls)
        affected.add(cls.name)
        # Param key per (source, class). Same source + same class twice
        # in one batch is a duplicate (rejected); different sources writing
        # the same class is fine — they go to different param slots.
        rows_param = f"{cw.binding.source.name.lower()}_{cls.name.lower()}_rows"
        if rows_param in used_param_keys:
            raise ValueError(
                f"duplicate ClassWrites for ({cw.binding.source.name!r}, "
                f"{cls.name!r}) in batch"
            )
        used_param_keys.add(rows_param)
        class_params = {rows_param: json.dumps(cw.rows)}
        statements.append(
            (
                _emit_class_close_out(
                    cw,
                    schema=schema,
                    bindings_suffix=bindings_suffix,
                    rows_param=rows_param,
                ),
                class_params,
            )
        )
        statements.append(
            (
                _emit_class_insert(
                    cw,
                    schema=schema,
                    bindings_suffix=bindings_suffix,
                    rows_param=rows_param,
                ),
                class_params,
            )
        )

    if enforce:
        block = _emit_enforcement_block(spec, affected, schema=schema)
        if block is not None:
            statements.append((block, {}))

    return BatchWrite(
        statements=statements,
        affected_classes=tuple(sorted(affected)),
    )


def emit_close_out(
    spec: Spec,
    *,
    class_name: str,
    source_name: str,
    schema: str = "knot_data",
    bindings_suffix: str = "_bindings",
) -> str:
    """Close out the currently-open binding for one ``(class, source,
    source_identifier)`` pair without inserting a replacement.

    Use to retract a source's claim — most commonly to withdraw a user
    correction so the resolver falls back to the next-best source.
    The corresponding INSERT half of the SCD2 dance is intentionally
    omitted; this is just a one-shot ``UPDATE``.

    Returns parameterized SQL with named placeholders ``%(canonical_id)s``
    and ``%(source_identifier)s``. The host runs::

        conn.execute(sql, {"canonical_id": "...", "source_identifier": "..."})
    """
    cls = _find_concrete(spec, class_name)
    ident = cls.identifier_slot()
    table = _bindings_id(cls, schema=schema, suffix=bindings_suffix)
    source_literal = _sql_literal(source_name)
    return (
        f"UPDATE {table}\n"
        f"SET valid_to = now()\n"
        f"WHERE {ident.name} = %(canonical_id)s\n"
        f"  AND source_name = {source_literal}\n"
        f"  AND source_identifier = %(source_identifier)s\n"
        f"  AND valid_to IS NULL;"
    )


def _find_concrete(spec: Spec, name: str) -> OntologyClass:
    """Look up a concrete OntologyClass by name; raise if not found or
    not concrete."""
    for c in spec.classes:
        if isinstance(c, OntologyClass) and c.name == name:
            if c.kind != ClassKind.CONCRETE:
                raise ValueError(
                    f"class {name!r} is {c.kind.value!r}; only concrete "
                    f"classes have bindings tables"
                )
            return c
    raise ValueError(f"no concrete class named {name!r} in spec")


# ---------------------------------------------------------------------------
# Async ER helpers — bronze→silver canonical_id assignment
# ---------------------------------------------------------------------------


def emit_assign_canonical(
    cls: OntologyClass,
    canonical_id: str,
    *,
    source_name: str,
    source_identifier: str,
    schema: str = "knot_data",
    bindings_suffix: str = "_bindings",
) -> tuple[str, dict[str, Any]]:
    """SQL that assigns a ``canonical_id`` to one previously-unresolved
    binding row identified by ``(source_name, source_identifier)``.

    Refuses to clobber an existing assignment: the ``WHERE`` clause
    includes ``<ident> IS NULL``, so re-running is a no-op. To change
    an already-assigned canonical_id, call ``emit_recanonicalize``.

    Returns ``(sql, params)`` with named ``%(...)s`` placeholders.
    """
    _check_concrete(cls)
    ident_name = cls.identifier_slot().name
    bindings_table = _bindings_id(cls, schema=schema, suffix=bindings_suffix)
    sql = (
        f"UPDATE {bindings_table}\n"
        f"SET {ident_name} = %(canonical_id)s\n"
        f"WHERE source_name = %(source_name)s\n"
        f"  AND source_identifier = %(source_identifier)s\n"
        f"  AND {ident_name} IS NULL\n"
        f"  AND valid_to IS NULL;"
    )
    return sql, {
        "canonical_id": canonical_id,
        "source_name": source_name,
        "source_identifier": source_identifier,
    }


def emit_recanonicalize(
    cls: OntologyClass,
    new_canonical_id: str,
    *,
    source_name: str,
    source_identifier: str,
    schema: str = "knot_data",
    bindings_suffix: str = "_bindings",
) -> tuple[str, dict[str, Any]]:
    """SQL that reassigns a binding row's ``canonical_id``, preserving
    history via SCD2.

    Closes the currently-open binding (``valid_to = now()``) and
    inserts a new row with the corrected ``canonical_id`` and
    otherwise-identical state (same source, same slot values, same
    raw_payload). Old row stays addressable for history; the resolved
    view sees only the new one.

    One atomic statement via a writable CTE — ``now()`` is the same
    instant on both halves of the close-out / re-insert.

    Returns ``(sql, params)`` with named ``%(...)s`` placeholders.
    """
    _check_concrete(cls)
    ident_name = cls.identifier_slot().name
    bindings_table = _bindings_id(cls, schema=schema, suffix=bindings_suffix)
    eff_slots = cls.effective_slots()

    insert_cols = (
        ["source_name", "source_identifier"]
        + [s.name for s in eff_slots]
        + ["raw_payload", "valid_from"]
    )
    select_cols: list[str] = ["source_name", "source_identifier"]
    for slot in eff_slots:
        if slot.name == ident_name:
            select_cols.append(f"%(new_canonical_id)s AS {ident_name}")
        else:
            select_cols.append(slot.name)
    select_cols.extend(["raw_payload", "now() AS valid_from"])

    sql = (
        f"WITH closed AS (\n"
        f"    UPDATE {bindings_table} SET valid_to = now()\n"
        f"    WHERE source_name = %(source_name)s\n"
        f"      AND source_identifier = %(source_identifier)s\n"
        f"      AND valid_to IS NULL\n"
        f"    RETURNING *\n"
        f")\n"
        f"INSERT INTO {bindings_table} ({', '.join(insert_cols)})\n"
        f"SELECT {', '.join(select_cols)}\n"
        f"FROM closed;"
    )
    return sql, {
        "new_canonical_id": new_canonical_id,
        "source_name": source_name,
        "source_identifier": source_identifier,
    }


__all__ = [
    "ClassWrites",
    "BatchWrite",
    "emit_batch_write",
    "emit_close_out",
    "emit_assign_canonical",
    "emit_recanonicalize",
]
