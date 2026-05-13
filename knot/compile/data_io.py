"""Write-data path — SQL machinery for writing one source claim (one
row) to a class's bindings table, using SCD2 semantics.

A binding write is two statements the host runs in a single transaction:

  1. **close-out**  UPDATE the currently-open prior binding's
     ``valid_to = now()`` for the matching
     ``(<identifier>, source_name, source_identifier)`` triple where
     ``valid_to IS NULL``. No-op when no prior exists.
  2. **insert**     INSERT a new binding row with all slot values;
     postgres fills ``valid_from`` from its column DEFAULT.

Both statements use named parameters (psycopg ``%(name)s`` style); the
host passes a dict keyed by the column names in ``close_out_columns`` /
``insert_columns``.

Spec-model validation (NOT NULL, types, identifier present) is handled
by postgres against the DDL the bindings table already carries.
Business-rule validation (``Constraint.body`` predicates) is the
separate ``knot.compile.constraints`` emitter; the host composes both
into the same transaction.
"""

from __future__ import annotations

from dataclasses import dataclass

from knot.spec import OntologyClass, Slot, Spec


@dataclass(frozen=True)
class BindingWrite:
    """Statements + parameter shapes for one SCD2 binding write."""

    close_out_sql: str
    close_out_columns: tuple[str, ...]
    insert_sql: str
    insert_columns: tuple[str, ...]


def _find_concrete(spec: Spec, name: str) -> OntologyClass:
    for c in spec.classes:
        if isinstance(c, OntologyClass) and c.name == name:
            if c.kind != "concrete":
                raise ValueError(
                    f"class {name!r} is {c.kind!r}; only concrete classes "
                    f"have bindings tables"
                )
            return c
    raise ValueError(f"no concrete class named {name!r} in spec")


def _effective_slots(cls: OntologyClass) -> list[Slot]:
    seen: set[str] = set()
    out: list[Slot] = []
    for parent in cls._chain():
        for sl in parent.slots:
            if sl.name in seen:
                continue
            seen.add(sl.name)
            out.append(sl)
    return out


def _identifier_slot(cls: OntologyClass) -> Slot:
    for sl in _effective_slots(cls):
        if sl.identifier:
            return sl
    raise ValueError(f"{cls.name!r} has no identifier slot")


def emit_binding_write(
    spec: Spec,
    class_name: str,
    *,
    schema: str = "knot_data",
    bindings_suffix: str = "_bindings",
) -> BindingWrite:
    """Return the two-statement SCD2 write machinery for ``class_name``."""
    cls = _find_concrete(spec, class_name)
    ident = _identifier_slot(cls)
    bindings_table = f"{schema}.{cls.name.lower()}{bindings_suffix}"

    close_out_columns = (ident.name, "source_name", "source_identifier")
    close_out_sql = (
        f"UPDATE {bindings_table}\n"
        f"SET valid_to = now()\n"
        f"WHERE {ident.name} = %({ident.name})s\n"
        f"  AND source_name = %(source_name)s\n"
        f"  AND source_identifier = %(source_identifier)s\n"
        f"  AND valid_to IS NULL;"
    )

    slot_columns = tuple(sl.name for sl in _effective_slots(cls))
    insert_columns = ("source_name", "source_identifier") + slot_columns
    columns_csv = ", ".join(insert_columns)
    placeholders_csv = ", ".join(f"%({c})s" for c in insert_columns)
    insert_sql = (
        f"INSERT INTO {bindings_table} ({columns_csv})\n"
        f"VALUES ({placeholders_csv});"
    )

    return BindingWrite(
        close_out_sql=close_out_sql,
        close_out_columns=close_out_columns,
        insert_sql=insert_sql,
        insert_columns=insert_columns,
    )


__all__ = ["BindingWrite", "emit_binding_write"]
