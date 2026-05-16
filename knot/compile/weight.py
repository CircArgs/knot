"""Weight policy seed emission — INSERTs that populate the
``source_weight`` table from a ``Spec``.

Weights live in postgres (not baked into the resolved view's SQL) so
operators can tune them at runtime without recompiling views or
redeploying the spec:

    UPDATE knot_data.source_weight
       SET weight = 12.5
     WHERE source_name = 'imdb' AND class_name = 'Movie' AND slot_name = 'year';

``emit_weight_seed`` is **INSERT-only** (``ON CONFLICT DO NOTHING``).
The spec's weight values are *initial conditions*; once a row exists,
the operator's runtime tuning is authoritative. Redeploying the spec
adds rows for net-new (source, class, slot) triples and does not
clobber values for existing rows. To reset a row to the spec default,
delete it and redeploy.

Weight is per-slot: one row per (source_name, class_name, slot_name).
The identifier slot has no weight row (identity is not argmax-resolved).

Weight values are opaque floats. The resolver picks the highest one;
calibration / probability semantics belong to whatever produced the
numbers, not to knot.
"""

from __future__ import annotations

from typing import Any

from knot.spec import Spec


def emit_weight_seed(
    spec: Spec,
    *,
    schema: str = "knot_data",
    weight_table_name: str = "source_weight",
) -> list[tuple[str, list[Any]]]:
    """Return parameterized INSERTs that seed ``source_weight`` from
    the spec's bindings.

    Each tuple is ``(sql, [source_name, class_name, slot_name, weight])``.
    ``ON CONFLICT DO NOTHING`` preserves any operator tuning that's
    already happened in the live table.
    """
    sql = (
        f"INSERT INTO {schema}.{weight_table_name} "
        f"(source_name, class_name, slot_name, weight) "
        f"VALUES (%s, %s, %s, %s)\n"
        f"ON CONFLICT (source_name, class_name, slot_name) DO NOTHING;"
    )
    out: list[tuple[str, list[Any]]] = []
    for b in spec.source_bindings:
        cls = b.class_
        ident_name = b.identifier_slot.name
        for slot in cls.effective_slots():
            if slot.name == ident_name:
                continue  # identity isn't argmax-resolved; no weight row
            weight = b.weight_for(slot.name)
            out.append((sql, [b.source.name, cls.name, slot.name, weight]))
    return out
