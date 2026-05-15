"""Trust policy seed emission — INSERTs that populate the
``source_trust`` table from a ``Spec``.

Trust values live in postgres (not baked into the resolved view's SQL)
so operators can tune them at runtime without recompiling views or
redeploying the spec:

    UPDATE knot_data.source_trust
       SET trust = 0.8
     WHERE source_name = 'imdb' AND class_name = 'Movie' AND slot_name = 'year';

``emit_trust_seed`` is **INSERT-only** (``ON CONFLICT DO NOTHING``).
The spec's trust values are *initial conditions*; once a row exists,
the operator's runtime tuning is authoritative. Redeploying the spec
adds rows for net-new (source, class, slot) triples and does not
clobber values for existing rows. To reset a row to the spec default,
delete it and redeploy.

Trust is per-slot: one row per (source_name, class_name, slot_name).
The identifier slot has no trust row (identity is not argmax-resolved).
"""

from __future__ import annotations

from typing import Any

from knot.spec import Spec


def emit_trust_seed(
    spec: Spec,
    *,
    schema: str = "knot_data",
    trust_table_name: str = "source_trust",
) -> list[tuple[str, list[Any]]]:
    """Return parameterized INSERTs that seed ``source_trust`` from
    the spec's bindings.

    Each tuple is ``(sql, [source_name, class_name, slot_name, trust])``.
    ``ON CONFLICT DO NOTHING`` preserves any operator tuning that's
    already happened in the live table.
    """
    sql = (
        f"INSERT INTO {schema}.{trust_table_name} "
        f"(source_name, class_name, slot_name, trust) "
        f"VALUES (%s, %s, %s, %s)\n"
        f"ON CONFLICT (source_name, class_name, slot_name) DO NOTHING;"
    )
    out: list[tuple[str, list[Any]]] = []
    for b in spec.source_bindings:
        cls = b.class_
        ident_name = b.identifier_slot.name
        for slot in cls.effective_slots():
            if slot.name == ident_name:
                continue  # identity isn't argmax-resolved; no trust row
            trust = b.trust_for(slot.name)
            out.append((sql, [b.source.name, cls.name, slot.name, trust]))
    return out
