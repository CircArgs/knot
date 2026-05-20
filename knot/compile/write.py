"""Write-data path — upsert + ER stamp SQL templates.

``emit_binding_write_sql`` takes a ``SourceBinding`` and returns ONE
SQL template referencing a single ``%(rows)s::jsonb`` parameter:
``INSERT ... ON CONFLICT (source_name, source_identifier) DO
UPDATE SET ...``. Re-ingesting the same source's same source_id
upserts in place; ``canonical_id`` and ``er_metadata`` (both
ER-owned) are preserved across re-ingests, every other slot +
``raw_payload`` gets overwritten.

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


def _emit_class_upsert(
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
    ident_name = cls.identifier_slot().name

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

    # ON CONFLICT DO UPDATE — re-ingest is an in-place upsert keyed on
    # (source_name, source_identifier). Update every slot the source
    # provides + raw_payload, but PRESERVE canonical_id and er_metadata
    # (both owned by ER — the source doesn't know either). FK slot
    # columns get reset to source-ids; a re-translation pass is the
    # host's concern if the source's FK references changed.
    update_lines = [
        f"  {s.name} = EXCLUDED.{s.name}" for s in eff_slots if s.name != ident_name
    ]
    update_lines.append("  raw_payload = EXCLUDED.raw_payload")
    update_block = ",\n".join(update_lines)

    return (
        f"INSERT INTO {table} ({columns_csv})\n"
        "SELECT\n" + ",\n".join(select_lines) + "\n" + raw_subquery + "\n"
        f"ON CONFLICT (source_name, source_identifier) DO UPDATE SET\n"
        f"{update_block};"
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def emit_binding_write_sql(
    binding: SourceBinding,
    *,
    schema: str = "knot_data",
    bindings_suffix: str = "_bindings",
) -> str:
    """Return one upsert SQL template for the binding. References a
    single ``%(rows)s::jsonb`` parameter — the host's connector binds
    the rows::

        sql = binding.write_sql()
        with pg.cursor() as cur:
            cur.execute(sql, {"rows": rows})

    Semantics: ``INSERT ... ON CONFLICT (source_name, source_identifier)
    DO UPDATE`` — re-ingesting the same source/source_id upserts in
    place. ``canonical_id`` and ``er_metadata`` are preserved across
    re-ingests (both ER-owned); every other slot + ``raw_payload``
    gets overwritten from the new payload. FK slot columns reset to
    source-ids; if a source's FK references changed, the host runs a
    separate re-translation pass (no canonical-id-preserving auto-
    retranslate today).

    Constraint enforcement is the host's concern — run
    ``spec.emit_validation()`` SELECTs after the write inside the
    same transaction and roll back if any return rows.
    """
    _check_concrete(binding.class_)
    return _emit_class_upsert(
        binding,
        schema=schema,
        bindings_suffix=bindings_suffix,
        rows_param="rows",
    )


def emit_retract_sql(
    binding: SourceBinding,
    *,
    schema: str = "knot_data",
    bindings_suffix: str = "_bindings",
) -> str:
    """Return the SQL template that retracts (deletes) one binding row.

    Use to withdraw a source's claim entirely — most commonly to
    pull a user correction so the resolver falls back to the
    next-best source. Without SCD2 there's no row to "close out";
    retraction is a straight DELETE.

    SQL has two named placeholders ``%(canonical_id)s`` and
    ``%(source_identifier)s`` — both required so the host explicitly
    asserts which canonical the binding pointed at (defense against
    deleting the wrong claim after an ER reassignment)::

        cur.execute(binding.retract_sql(),
                    {"canonical_id": "...", "source_identifier": "..."})
    """
    _check_concrete(binding.class_)
    ident = binding.class_.identifier_slot()
    table = _bindings_id(binding.class_, schema=schema, suffix=bindings_suffix)
    source_literal = _sql_literal(binding.source.name)
    return (
        f"DELETE FROM {table}\n"
        f"WHERE {ident.name} = %(canonical_id)s\n"
        f"  AND source_name = {source_literal}\n"
        f"  AND source_identifier = %(source_identifier)s;"
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
            f"AND {target_ident} IS NOT NULL LIMIT 1)"
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
            f")"
        )

    all_ctes = [
        # ``RETURNING`` exposes the stamp's row count to downstream
        # fanout CTEs via ``EXISTS (SELECT 1 FROM stamp)`` — gates
        # strict idempotency.
        f"stamp AS (\n"
        f"  UPDATE {table}\n"
        f"  SET {set_block}\n"
        f"  WHERE source_name = {source_literal}\n"
        f"    AND source_identifier = %(source_identifier)s\n"
        f"    AND {ident_name} IS NULL\n"
        f"  RETURNING {ident_name}\n"
        f")",
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
    ``canonical_id``. Three things happen atomically:

    1. **Capture** — read the OLD canonical_id so the cascade knows
       which referencing FK values to rewrite.
    2. **Stamp** — UPDATE the binding's canonical_id (and optionally
       er_metadata) to the new value.
    3. **Cascade** — for every (referencer_class, fk_slot) pointing
       at this class, UPDATE referencing bindings whose FK column
       held the OLD canonical_id, setting it to the NEW one. Source-
       agnostic — canonical-ids are global identifiers.

    SQL has three named placeholders — ``%(new_canonical_id)s``,
    ``%(source_identifier)s``, ``%(er_metadata)s``. ``er_metadata``
    uses ``COALESCE`` so binding ``None`` keeps the existing value;
    binding a JSON string overrides::

        cur.execute(binding.recanonicalize_sql(), {
            "new_canonical_id":  "m_correct",
            "source_identifier": "tt001",
            "er_metadata":       json.dumps({...}),  # or None to keep
        })
    """
    _check_concrete(binding.class_)
    ident_name = binding.class_.identifier_slot().name
    table = _bindings_id(binding.class_, schema=schema, suffix=bindings_suffix)
    source_literal = _sql_literal(binding.source.name)

    # Cascade: every referencing class's bindings hold this row's OLD
    # canonical_id in their FK column. Rewrite them to the NEW value.
    # The cascade is source-agnostic — post-ER FK columns hold
    # canonical-ids, no source coupling. ``(SELECT old_id FROM
    # old_state)`` is a scalar subquery — at most one row; NULL if
    # the row didn't exist or wasn't yet ER-stamped, in which case
    # the cascade WHERE doesn't match anything.
    cascade_ctes: list[str] = []
    for ref_cls, ref_slot in binding.class_.referrers:
        ref_table = _bindings_id(ref_cls, schema=schema, suffix=bindings_suffix)
        cte_name = f"cascade_{ref_cls.name.lower()}_{ref_slot.name}"
        cascade_ctes.append(
            f"{cte_name} AS (\n"
            f"  UPDATE {ref_table}\n"
            f"  SET {ref_slot.name} = %(new_canonical_id)s\n"
            f"  WHERE {ref_slot.name} = (SELECT old_id FROM old_state)\n"
            f")"
        )

    all_ctes = [
        # Capture the OLD canonical_id BEFORE the stamp UPDATE rewrites it.
        f"old_state AS (\n"
        f"  SELECT {ident_name} AS old_id\n"
        f"  FROM {table}\n"
        f"  WHERE source_name = {source_literal}\n"
        f"    AND source_identifier = %(source_identifier)s\n"
        f"    AND {ident_name} IS NOT NULL\n"
        f")",
        # Stamp the new canonical_id + optional er_metadata override.
        f"stamp AS (\n"
        f"  UPDATE {table}\n"
        f"  SET {ident_name} = %(new_canonical_id)s,\n"
        f"      er_metadata = COALESCE(%(er_metadata)s::jsonb, er_metadata)\n"
        f"  WHERE source_name = {source_literal}\n"
        f"    AND source_identifier = %(source_identifier)s\n"
        f"    AND {ident_name} IS NOT NULL\n"
        f")",
        *cascade_ctes,
    ]
    return "WITH " + ",\n".join(all_ctes) + "\nSELECT 1;"
