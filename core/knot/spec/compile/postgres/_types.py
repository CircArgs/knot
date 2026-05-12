"""Postgres column-type mapping — ``TypeExpression`` → postgres SQL type.

Single-dispatch on the TypeExpression hierarchy (Primitive / Array / ClassRef).
Whitelist; type names from the spec are never spliced raw into DDL.
This is compilation: the spec describes a slot in abstract terms, this
module decides what postgres column type it becomes.
"""

from __future__ import annotations

from knot.spec.metaschema import Array, ClassRef, Primitive, Property

_PG_TYPE_FOR_PRIMITIVE: dict[str, str] = {
    "string": "TEXT",
    "integer": "INTEGER",
    "float": "DOUBLE PRECISION",
    "boolean": "BOOLEAN",
    "datetime": "TIMESTAMPTZ",
    "date": "DATE",
}


def _type_expr_pg(type_expr: Primitive | Array | ClassRef) -> str:  # type: ignore[return]
    """Recursively map a TypeExpression to its postgres column type string."""
    if isinstance(type_expr, Primitive):
        return _PG_TYPE_FOR_PRIMITIVE.get(type_expr.name, "TEXT")
    if isinstance(type_expr, Array):
        inner = _type_expr_pg(type_expr.of)
        return f"{inner}[]"
    if isinstance(type_expr, ClassRef):
        return "TEXT"
    # Unreachable for well-formed specs — fallback to TEXT.
    return "TEXT"


def property_pg_type(prop: Slot) -> str:
    """Postgres column type for a stored property.

    Dispatches on property.type (TypeExpression):
      Primitive("string")      → TEXT
      Primitive("integer")     → INTEGER
      Primitive("float")       → DOUBLE PRECISION
      Primitive("boolean")     → BOOLEAN
      Primitive("datetime")    → TIMESTAMPTZ
      Primitive("date")        → DATE
      Array(Primitive(...))    → T[]
      ClassRef(target_class)   → TEXT (canonical_id FK)
      Array(ClassRef(...))     → TEXT[]
      None                     → TEXT (derived / untyped slots)
    """
    if property.type is None:
        return "TEXT"
    return _type_expr_pg(property.type)
