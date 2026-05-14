"""Trust policy emission — INSERTs that seed the ``source_accuracy``
table from a ``Spec``.

The accuracy values live in postgres (not in the resolved view's SQL)
so operators can tune them at runtime with plain UPDATE statements
without recompiling views or redeploying the spec:

    UPDATE knot_data.source_accuracy
       SET accuracy = 0.8
     WHERE source_name = 'imdb' AND class_name = 'Movie';

``emit_trust_seed`` produces idempotent UPSERTs (``ON CONFLICT (...)
DO UPDATE SET accuracy = EXCLUDED.accuracy``) that reconcile the table
to the spec at deploy time. Running it on every deploy keeps the
policy in sync as bindings are added or removed.
"""

from __future__ import annotations

from typing import Any

from knot.spec import OntologyClass, Spec


def emit_trust_seed(
    spec: Spec,
    *,
    schema: str = "knot_data",
    trust_table_name: str = "source_accuracy",
) -> list[tuple[str, list[Any]]]:
    """Return parameterized UPSERTs that reconcile ``source_accuracy``
    to the spec's bindings.

    Each tuple is ``(sql, [source_name, class_name, accuracy])``. The
    host runs them in a transaction. Re-running is idempotent — same
    spec produces the same final state.
    """
    sql = (
        f"INSERT INTO {schema}.{trust_table_name} "
        f"(source_name, class_name, accuracy) "
        f"VALUES (%s, %s, %s)\n"
        f"ON CONFLICT (source_name, class_name) "
        f"DO UPDATE SET accuracy = EXCLUDED.accuracy;"
    )
    out: list[tuple[str, list[Any]]] = []
    for b in spec.source_bindings:
        cls = b.class_
        if not isinstance(cls, OntologyClass):
            continue
        out.append((sql, [b.source.name, cls.name, b.accuracy]))
    return out


__all__ = ["emit_trust_seed"]
