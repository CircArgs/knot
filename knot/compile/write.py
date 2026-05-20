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

from knot.ast.types import Array, ClassRef, Primitive, TypeExpression, Vector
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
    jsonb-element row. Handles primitive, array, class-ref, and
    vector slots."""
    match t:
        case Primitive():
            return _PRIMITIVE_TO_JSONB_CAST[t]
        case Array(of=inner):
            # ARRAY-coerce a jsonb array of scalars into a postgres array.
            return f"::{_jsonb_cast(inner).removeprefix('::')}[]"
        case ClassRef():
            return "::text"
        case Vector(dim=dim):
            # pgvector accepts its text form (``"[0.1, 0.2, ...]"``)
            # via direct cast — that's what ``r->>'col'`` produces for
            # a json array of floats.
            return f"::vector({dim})"
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
    if isinstance(slot.type, Vector):
        # Same shape as Primitive — pgvector parses the text form.
        return f"raw.{raw_field}::vector({slot.type.dim})"
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

    Three things happen atomically in one statement:

    1. **Stamp** — set ``canonical_id`` on the binding row (no-op if
       already set; ``emit_recanonicalize_sql`` is the way to change
       an existing assignment).
    2. **Forward FK translation** — for every ``ClassRef`` slot on
       this class (e.g. ``Movie.director`` → Person), look up the
       current value in the *target's* bindings table within the same
       source's namespace; if the target binding has been ER-stamped,
       rewrite the column from source-id to canonical-id. If the
       target hasn't been ER'd yet, the column keeps its source-id
       and the target's own ER stamp will fan out to fix it later
       (see ``emit_assign_canonical_sql``'s backward fan-out, not yet
       wired here).
    3. **Register** — ``INSERT`` the new canonical_id into the
       class's identity-registry table (``ON CONFLICT DO NOTHING``).

    SQL has three named placeholders — ``%(canonical_id)s``,
    ``%(source_identifier)s``, ``%(er_metadata)s``. Host binds::

        cur.execute(binding.assign_canonical_sql(), {
            "canonical_id":      "m_pulpfiction",
            "source_identifier": "tt0110912",
            "er_metadata":       json.dumps({"run_id": "r42"}),  # or None
        })
    """
    _check_concrete(binding.class_)
    cls = binding.class_
    ident_name = cls.identifier_slot().name
    table = _bindings_id(cls, schema=schema, suffix=bindings_suffix)
    canonical_table = f"{schema}.{cls.name.lower()}"
    source_literal = _sql_literal(binding.source.name)

    # Forward FK translation — for each ClassRef slot on this class,
    # rewrite the column from source-id to the target's canonical_id
    # (if the target binding has been ER'd; otherwise COALESCE keeps
    # the source-id intact for a later backward fan-out to translate).
    set_clauses = [
        f"{ident_name} = %(canonical_id)s",
        "er_metadata = COALESCE(%(er_metadata)s::jsonb, er_metadata)",
    ]
    for slot in cls.effective_slots():
        if not isinstance(slot.type, ClassRef):
            continue
        target = slot.type.target
        target_table = _bindings_id(target, schema=schema, suffix=bindings_suffix)
        target_ident = target.identifier_slot().name
        # Same-source assumption: an imdb credit's .movie field holds
        # an imdb movie id, so we look it up in the target's bindings
        # for THIS source. Cross-source FK references (e.g. corrections)
        # are not handled here — they'd need a separate code path.
        lookup = (
            f"(SELECT {target_ident} FROM {target_table} "
            f"WHERE source_name = {source_literal} "
            f"AND source_identifier = {table}.{slot.name} "
            f"AND {target_ident} IS NOT NULL "
            f"AND valid_to IS NULL LIMIT 1)"
        )
        set_clauses.append(f"{slot.name} = COALESCE({lookup}, {table}.{slot.name})")

    set_block = ",\n    ".join(set_clauses)
    # Backward fan-out — for every (referencing_class, fk_slot) that
    # points at this class, rewrite any referencing binding whose FK
    # column still holds the just-stamped source-id. Same-source
    # assumption: a referencing binding's FK column carries source-ids
    # in the binding's own source's namespace, so we filter on the
    # referencer's source_name = this stamp's source_name. The
    # ``EXISTS (SELECT 1 FROM stamp)`` gates the whole fanout on the
    # stamp UPDATE actually firing — re-running assign_canonical when
    # the row was already stamped is a strict no-op, never a force-
    # fanout that could corrupt existing canonical-id values.
    fanout_ctes: list[str] = []
    for ref_cls, ref_slot in cls.referrers:
        ref_table = _bindings_id(ref_cls, schema=schema, suffix=bindings_suffix)
        cte_name = f"fanout_{ref_cls.name.lower()}_{ref_slot.name}"
        fanout_ctes.append(
            f"{cte_name} AS (\n"
            f"  UPDATE {ref_table}\n"
            f"  SET {ref_slot.name} = %(canonical_id)s\n"
            f"  WHERE EXISTS (SELECT 1 FROM stamp)\n"
            f"    AND source_name = {source_literal}\n"
            f"    AND {ref_slot.name} = %(source_identifier)s\n"
            f"    AND valid_to IS NULL\n"
            f")"
        )

    # Registry insert — driven by the UPDATE's RETURNING so it fires
    # only when the stamp actually matched a row (not a phantom
    # registration on an already-stamped re-run).
    register_cte = (
        f"register AS (\n"
        f"  INSERT INTO {canonical_table} ({ident_name})\n"
        f"  SELECT {ident_name} FROM stamp\n"
        f"  ON CONFLICT ({ident_name}) DO NOTHING\n"
        f")"
    )

    all_ctes = [
        f"stamp AS (\n"
        f"  UPDATE {table}\n"
        f"  SET {set_block}\n"
        f"  WHERE source_name = {source_literal}\n"
        f"    AND source_identifier = %(source_identifier)s\n"
        f"    AND {ident_name} IS NULL\n"
        f"    AND valid_to IS NULL\n"
        f"  RETURNING {ident_name}\n"
        f")",
        register_cte,
        *fanout_ctes,
    ]
    # Chain of write-only CTEs needs an outer SELECT to be a valid
    # postgres statement; the SELECT 1 is the tail.
    return "WITH " + ",\n".join(all_ctes) + "\nSELECT 1;"


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

    canonical_table = f"{schema}.{binding.class_.name.lower()}"

    # Cascade: every referencing class's bindings hold this row's OLD
    # canonical_id in their FK column (post-original-ER). They need
    # rewriting to the NEW canonical_id. Canonical-id rewrites are
    # source-agnostic (no source_name filter) because canonical-ids
    # are global. ``(SELECT canonical_id FROM closed)`` is a scalar
    # subquery — at most one row; NULL if the close-out didn't fire,
    # in which case the cascade's WHERE doesn't match anything.
    cascade_ctes: list[str] = []
    for ref_cls, ref_slot in binding.class_.referrers:
        ref_table = _bindings_id(ref_cls, schema=schema, suffix=bindings_suffix)
        cte_name = f"cascade_{ref_cls.name.lower()}_{ref_slot.name}"
        cascade_ctes.append(
            f"{cte_name} AS (\n"
            f"  UPDATE {ref_table}\n"
            f"  SET {ref_slot.name} = %(new_canonical_id)s\n"
            f"  WHERE {ref_slot.name} = (SELECT {ident_name} FROM closed)\n"
            f"    AND valid_to IS NULL\n"
            f")"
        )

    all_ctes = [
        f"closed AS (\n"
        f"  UPDATE {table} SET valid_to = now()\n"
        f"  WHERE source_name = {source_literal}\n"
        f"    AND source_identifier = %(source_identifier)s\n"
        f"    AND valid_to IS NULL\n"
        f"  RETURNING *\n"
        f")",
        f"inserted AS (\n"
        f"  INSERT INTO {table} ({', '.join(insert_cols)})\n"
        f"  SELECT {', '.join(select_cols)}\n"
        f"  FROM closed\n"
        f")",
        # Register the new canonical_id in the identity table — driven
        # by ``FROM closed`` so it only fires when the close-out matched.
        f"register AS (\n"
        f"  INSERT INTO {canonical_table} ({ident_name})\n"
        f"  SELECT %(new_canonical_id)s FROM closed\n"
        f"  ON CONFLICT ({ident_name}) DO NOTHING\n"
        f")",
        *cascade_ctes,
    ]
    return "WITH " + ",\n".join(all_ctes) + "\nSELECT 1;"
