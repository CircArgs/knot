"""knot type → GraphQL scalar mapping.

Maps every ``knot.ast.types.TypeExpression`` to its GraphQL SDL
representation. Primitive scalars follow the standard GraphQL built-ins;
composite types recursively expand their inner type.

Design choices:
- DATE / TIMESTAMP → ``String`` (ISO 8601); no custom scalar ceremony.
- VECTOR(dim) → ``[Float!]!`` (float array); knot ships no GraphQL
  vector scalar — the host registers one if needed.
- ENUM(values) → a generated ``enum`` type named
  ``<ClassName><SlotName>Enum`` (caller must emit the enum definition
  separately via ``enum_sdl``).
- ClassRef(target) → the target class's GraphQL type name.

All returned strings are SDL fragments — they go directly into a field
definition, e.g. ``"String"`` or ``"[Int!]!"`` or ``"Movie"``.
"""

from __future__ import annotations

from knot.ast.types import Array, ClassRef, Enum, Primitive, TypeExpression, Vector


def gql_type(
    t: TypeExpression,
    *,
    required: bool = False,
    class_name: str = "",
    slot_name: str = "",
) -> str:
    """Return the SDL type string for a slot type.

    ``required=True`` wraps the outermost type in ``!`` (NOT NULL).
    ``class_name`` and ``slot_name`` are only needed when ``t`` is an
    ``Enum`` — they form the generated enum type name.
    """
    inner = _inner(t, class_name=class_name, slot_name=slot_name)
    return f"{inner}!" if required else inner


def _inner(t: TypeExpression, *, class_name: str, slot_name: str) -> str:
    match t:
        case Primitive.TEXT:
            return "String"
        case Primitive.INTEGER:
            return "Int"
        case Primitive.FLOAT:
            return "Float"
        case Primitive.BOOLEAN:
            return "Boolean"
        case Primitive.DATE | Primitive.TIMESTAMP:
            # ISO 8601 strings; the host registers a Date / DateTime
            # scalar if finer typing is needed.
            return "String"
        case Array(of=inner_t):
            elem = _inner(inner_t, class_name=class_name, slot_name=slot_name)
            # Array elements are always non-null at the element level;
            # the array itself is non-null (``[T!]!``).
            return f"[{elem}!]!"
        case Vector():
            # Dense float array — no knot-specific GraphQL scalar.
            return "[Float!]!"
        case Enum():
            return enum_type_name(class_name, slot_name)
        case ClassRef(target=target_cls):
            return target_cls.name
        case _:
            raise TypeError(f"Unknown knot type: {t!r}")


def enum_type_name(class_name: str, slot_name: str) -> str:
    """Canonical GraphQL enum type name for a given (class, slot) pair.

    Example: class ``Movie``, slot ``status`` → ``MovieStatusEnum``.
    """
    return f"{class_name}{slot_name.capitalize()}Enum"


def enum_sdl(class_name: str, slot_name: str, values: tuple[str, ...]) -> str:
    """Return the SDL block for one generated enum type.

    Values are emitted verbatim — the same strings the spec declared,
    which are also what the database stores. GraphQL conventionally
    uppercases enum values, but the spec is strict-validated and
    upper-casing here forces every host into a name-mapping shim
    (``{DIRECTOR: "director"}``) just to round-trip. Verbatim values
    keep host = DB = SDL consistent.
    """
    name = enum_type_name(class_name, slot_name)
    body = "\n  ".join(values)
    return f"enum {name} {{\n  {body}\n}}"
