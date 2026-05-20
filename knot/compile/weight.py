"""Weight runtime — read/write SQL emitters for the ``source_weight``
table.

Weights are **runtime-only** in knot. The spec describes structure
(what slots exist, what binds to what); the operator owns calibration
(what each (source, class, slot) weighs at argmax time). Removing
spec-level weight setters eliminates a real source of confusion —
"did this weight come from the spec or the runtime tuning" was a
question nobody could answer without grepping for set_default_weight
calls and comparing them to the table state.

This module emits three SQL shapes:

  - ``emit_read_weights_sql``    SELECT a binding's current weights
  - ``emit_upsert_weight_sql``   set one (source, class, slot) weight
  - ``emit_upsert_weights_sql``  bulk: set N slots in one statement

All three return parameterized SQL — the host's connector binds the
values. The first is for "what's already there?" inspection
(useful for gating: don't reset a weight an operator just tuned).
The next two are for setting; ``ON CONFLICT … UPDATE`` upsert
semantics so the same call works for first-time insert and
subsequent re-tuning.
"""

from __future__ import annotations

from knot.spec import Source, SourceBinding


def emit_read_weights_sql(
    binding: SourceBinding,
    *,
    weight_table_name: str = "source_weight",
) -> str:
    """SELECT this binding's currently-stored weights.

    Returns rows of ``(slot_name, weight)``. Empty result means no
    runtime tuning has happened yet for this binding's (source, class)
    pair; the resolver falls back to ``COALESCE(weight, 0)`` for
    those slots, so unset → 0.

    The host typically uses this to gate write decisions: skip the
    upsert if the operator has already tuned the value, or merge
    spec-suggested defaults with operator overrides.
    """
    spec = binding._require_spec()
    return (
        f"SELECT slot_name, weight\n"
        f"FROM {spec.schema}.{weight_table_name}\n"
        f"WHERE source_name = '{binding.source.name}'\n"
        f"  AND class_name = '{binding.class_.name}';"
    )


def emit_upsert_weight_sql(
    binding: SourceBinding,
    *,
    weight_table_name: str = "source_weight",
) -> str:
    """Set the weight for one (source, class, slot) — INSERT … ON
    CONFLICT UPDATE.

    Two named placeholders the host binds:
      - ``%(slot_name)s`` — the class slot to weight
      - ``%(weight)s``    — the new weight value (opaque float)

    Same statement works for first-time insert and re-tuning.
    """
    spec = binding._require_spec()
    return (
        f"INSERT INTO {spec.schema}.{weight_table_name}\n"
        f"    (source_name, class_name, slot_name, weight)\n"
        f"VALUES ('{binding.source.name}', '{binding.class_.name}',\n"
        f"        %(slot_name)s, %(weight)s)\n"
        f"ON CONFLICT (source_name, class_name, slot_name)\n"
        f"DO UPDATE SET weight = EXCLUDED.weight;"
    )


def emit_upsert_weights_sql(
    binding: SourceBinding,
    *,
    weight_table_name: str = "source_weight",
) -> str:
    """Bulk-set N weights for this binding in one statement.

    One named placeholder the host binds:
      - ``%(weights)s`` — a JSON object ``{slot_name: weight, …}``

    Same upsert semantics as the single-slot form: existing rows get
    overwritten, missing rows get inserted. The host typically renders
    the dict to JSON via ``json.dumps`` before binding.
    """
    spec = binding._require_spec()
    return (
        f"INSERT INTO {spec.schema}.{weight_table_name}\n"
        f"    (source_name, class_name, slot_name, weight)\n"
        f"SELECT '{binding.source.name}', '{binding.class_.name}',\n"
        f"       key, (value)::text::double precision\n"
        f"FROM jsonb_each(%(weights)s::jsonb)\n"
        f"ON CONFLICT (source_name, class_name, slot_name)\n"
        f"DO UPDATE SET weight = EXCLUDED.weight;"
    )


def emit_delete_weight_sql(
    binding: SourceBinding,
    slot_name: str,
    *,
    weight_table_name: str = "source_weight",
) -> str:
    """DELETE the ``(source, class, slot)`` weight row, reverting to the
    resolver's ``COALESCE(weight, 0)`` fallback.

    Validates ``slot_name`` against the binding's class — ``KeyError`` on
    typo. Returns a single DELETE statement with all three key columns
    inlined as literals (no placeholders).
    """
    binding.class_.get_slot(slot_name)  # KeyError on typo
    spec = binding._require_spec()
    src = "'" + binding.source.name.replace("'", "''") + "'"
    cls = "'" + binding.class_.name.replace("'", "''") + "'"
    slot = "'" + slot_name.replace("'", "''") + "'"
    return (
        f"DELETE FROM {spec.schema}.{weight_table_name}\n"
        f"WHERE source_name = {src}\n"
        f"  AND class_name = {cls}\n"
        f"  AND slot_name = {slot};"
    )


def emit_source_read_weights_sql(
    source: Source,
    *,
    weight_table_name: str = "source_weight",
) -> str:
    """SELECT every weight for ``source`` across all of its bindings.
    Convenience for "show me everything this source currently weighs."
    Returns rows of ``(class_name, slot_name, weight)``."""
    if source._spec is None:
        raise RuntimeError(
            f"Source {source.name!r} is not attached to a Spec "
            f"(create via spec.add_source(...))"
        )
    return (
        f"SELECT class_name, slot_name, weight\n"
        f"FROM {source._spec.schema}.{weight_table_name}\n"
        f"WHERE source_name = '{source.name}'\n"
        f"ORDER BY class_name, slot_name;"
    )
