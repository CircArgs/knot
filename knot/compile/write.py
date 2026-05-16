"""Write-data path — SCD2 binding write SQL templates.

``emit_binding_write_sql`` takes a ``SourceBinding`` and returns
``(close_out_sql, insert_sql)`` — two SQL templates both referencing
a single ``%(rows)s::jsonb`` parameter. The host binds the rows to
its connector and runs both statements sequentially in one
transaction. knot never touches the actual row data; rows are
runtime input, not compile-time input.

The emitter produces:

  1. **close-out** — ``UPDATE … FROM jsonb_array_elements(%(rows)s::jsonb)``
     sets ``valid_to = now()`` on every prior currently-open binding
     for the ``(identifier, source_identifier)`` keys in the batch.
  2. **insert** — ``INSERT … SELECT FROM jsonb_array_elements(%(rows)s::jsonb)``.
     The binding's per-slot mappings (``source_slot``, optional
     ``sql``) are applied server-side over the raw source fields.

Postgres' ``jsonb_array_elements`` iterates each element as ``r`` so
mapping expressions reference its keys as bare columns through the
``raw`` subquery alias (no rewriting of the mappings is required).

Constraint enforcement is a separate concern — the host runs
``spec.emit_validation()`` SELECTs after the write inside the same
transaction and rolls back if any return rows.

Multi-binding atomic writes (multiple sources / classes in one
transaction) are the host's responsibility: call
``binding.write_sql()`` per binding, run all of the resulting
statements in a single ``pg.transaction()``.
"""

from __future__ import annotations

from knot.ast.types import Array, ClassRef, Primitive, TypeExpression
from knot.spec import ClassKind, OntologyClass, Slot, SourceBinding

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
    column with the full ingested shape."""
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
    binding: SourceBinding,
    *,
    schema: str,
    bindings_suffix: str,
    rows_param: str,
) -> str:
    cls = binding.class_
    _check_concrete(cls)
    table = _bindings_id(cls, schema=schema, suffix=bindings_suffix)
    source_literal = _sql_literal(binding.source.name)
    eff_slots = cls.effective_slots()
    # raw_payload always trails the slot columns; preserves the full
    # ingested row so unmapped fields are recoverable later.
    insert_columns = (
        ["source_name", "source_identifier"]
        + [s.name for s in eff_slots]
        + ["raw_payload"]
    )
    columns_csv = ", ".join(insert_columns)

    raw_subquery = _emit_raw_subquery(binding, rows_param)
    select_lines: list[str] = [
        f"    {source_literal}",
        "    raw.source_identifier",
    ]
    for slot in eff_slots:
        m = binding.effective_mapping(slot.name)
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
    binding: SourceBinding,
    *,
    schema: str,
    bindings_suffix: str,
    rows_param: str,
) -> str:
    cls = binding.class_
    _check_concrete(cls)
    ident = cls.identifier_slot()
    table = _bindings_id(cls, schema=schema, suffix=bindings_suffix)
    source_literal = _sql_literal(binding.source.name)
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
# Public entry point
# ---------------------------------------------------------------------------


def emit_binding_write_sql(
    binding: SourceBinding,
    *,
    schema: str = "knot_data",
    bindings_suffix: str = "_bindings",
) -> tuple[str, str]:
    """Return ``(close_out_sql, insert_sql)`` for one binding's SCD2
    write. Both reference a single ``%(rows)s::jsonb`` parameter — the
    host's connector binds the rows.

    Run them in order in one transaction::

        close_out, insert = binding.write_sql()
        with pg.transaction(), pg.cursor() as cur:
            cur.execute(close_out, {"rows": rows})
            cur.execute(insert,    {"rows": rows})

    Constraint enforcement is the host's concern — run
    ``spec.emit_validation()`` SELECTs after the write inside the
    same transaction and roll back if any return rows.
    """
    _check_concrete(binding.class_)
    rows_param = "rows"
    return (
        _emit_class_close_out(
            binding,
            schema=schema,
            bindings_suffix=bindings_suffix,
            rows_param=rows_param,
        ),
        _emit_class_insert(
            binding,
            schema=schema,
            bindings_suffix=bindings_suffix,
            rows_param=rows_param,
        ),
    )


def emit_close_out_sql(
    binding: SourceBinding,
    *,
    schema: str = "knot_data",
    bindings_suffix: str = "_bindings",
) -> str:
    """Return the SQL template that closes out one currently-open
    binding row without inserting a replacement.

    Use to retract a source's claim — most commonly to withdraw a
    user correction so the resolver falls back to the next-best
    source. The corresponding INSERT half of the SCD2 dance is
    intentionally omitted; this is just a one-shot ``UPDATE``.

    SQL has named placeholders ``%(canonical_id)s`` and
    ``%(source_identifier)s``. Host binds them::

        cur.execute(binding.close_out_sql(),
                    {"canonical_id": "...", "source_identifier": "..."})
    """
    _check_concrete(binding.class_)
    ident = binding.class_.identifier_slot()
    table = _bindings_id(binding.class_, schema=schema, suffix=bindings_suffix)
    source_literal = _sql_literal(binding.source.name)
    return (
        f"UPDATE {table}\n"
        f"SET valid_to = now()\n"
        f"WHERE {ident.name} = %(canonical_id)s\n"
        f"  AND source_name = {source_literal}\n"
        f"  AND source_identifier = %(source_identifier)s\n"
        f"  AND valid_to IS NULL;"
    )


# ---------------------------------------------------------------------------
# ER helpers — canonical_id assignment + reassignment
# ---------------------------------------------------------------------------


def emit_assign_canonical_sql(
    binding: SourceBinding,
    *,
    schema: str = "knot_data",
    bindings_suffix: str = "_bindings",
) -> str:
    """Return the SQL template that assigns a ``canonical_id`` to one
    previously-unresolved binding row.

    Refuses to clobber an existing assignment: the ``WHERE`` clause
    includes ``<ident> IS NULL``, so re-running is a no-op. To change
    an already-assigned canonical_id, use ``emit_recanonicalize_sql``.

    SQL has three named placeholders — ``%(canonical_id)s``,
    ``%(source_identifier)s``, ``%(er_metadata)s``. The
    ``er_metadata`` column uses ``COALESCE`` so binding ``None`` keeps
    the existing value; binding a JSON-serialized string overwrites.
    Host binds::

        cur.execute(binding.assign_canonical_sql(), {
            "canonical_id":      "m_pulpfiction",
            "source_identifier": "tt0110912",
            "er_metadata":       json.dumps({"run_id": "r42"}),  # or None
        })
    """
    _check_concrete(binding.class_)
    ident_name = binding.class_.identifier_slot().name
    table = _bindings_id(binding.class_, schema=schema, suffix=bindings_suffix)
    source_literal = _sql_literal(binding.source.name)
    return (
        f"UPDATE {table}\n"
        f"SET {ident_name} = %(canonical_id)s,\n"
        f"    er_metadata = COALESCE(%(er_metadata)s::jsonb, er_metadata)\n"
        f"WHERE source_name = {source_literal}\n"
        f"  AND source_identifier = %(source_identifier)s\n"
        f"  AND {ident_name} IS NULL\n"
        f"  AND valid_to IS NULL;"
    )


def emit_recanonicalize_sql(
    binding: SourceBinding,
    *,
    schema: str = "knot_data",
    bindings_suffix: str = "_bindings",
) -> str:
    """Return the SQL template that reassigns a binding row's
    ``canonical_id``, preserving history via SCD2.

    Closes the currently-open binding (``valid_to = now()``) and
    inserts a new row with the corrected ``canonical_id`` and
    otherwise-identical state (same source, same slot values, same
    raw_payload). Old row stays addressable for history; the resolved
    view sees only the new one. One atomic statement via a writable
    CTE — ``now()`` is the same instant on both halves.

    SQL has three named placeholders — ``%(new_canonical_id)s``,
    ``%(source_identifier)s``, ``%(er_metadata)s``. ``er_metadata``
    uses ``COALESCE`` so binding ``None`` inherits the closed row's
    payload; binding a JSON string overrides::

        cur.execute(binding.recanonicalize_sql(), {
            "new_canonical_id":  "m_correct",
            "source_identifier": "tt001",
            "er_metadata":       json.dumps({...}),  # or None to inherit
        })
    """
    _check_concrete(binding.class_)
    ident_name = binding.class_.identifier_slot().name
    table = _bindings_id(binding.class_, schema=schema, suffix=bindings_suffix)
    source_literal = _sql_literal(binding.source.name)
    eff_slots = binding.class_.effective_slots()

    insert_cols = (
        ["source_name", "source_identifier"]
        + [s.name for s in eff_slots]
        + ["raw_payload", "er_metadata", "valid_from"]
    )
    select_cols: list[str] = ["source_name", "source_identifier"]
    for slot in eff_slots:
        if slot.name == ident_name:
            select_cols.append(f"%(new_canonical_id)s AS {ident_name}")
        else:
            select_cols.append(slot.name)
    select_cols.append("raw_payload")
    select_cols.append("COALESCE(%(er_metadata)s::jsonb, er_metadata) AS er_metadata")
    select_cols.append("now() AS valid_from")

    return (
        f"WITH closed AS (\n"
        f"    UPDATE {table} SET valid_to = now()\n"
        f"    WHERE source_name = {source_literal}\n"
        f"      AND source_identifier = %(source_identifier)s\n"
        f"      AND valid_to IS NULL\n"
        f"    RETURNING *\n"
        f")\n"
        f"INSERT INTO {table} ({', '.join(insert_cols)})\n"
        f"SELECT {', '.join(select_cols)}\n"
        f"FROM closed;"
    )
