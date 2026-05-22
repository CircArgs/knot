"""SDL codegen — walk a knot Spec and emit a GraphQL SDL string.

One ``type <ClassName>`` per concrete ``OntologyClass`` and
``VirtualClass``. The Query root gets one by-id lookup and one
paginated list field per concrete + virtual class.

Reverse-FK fields: for each ``(ref_cls, ref_slot)`` in
``cls.referrers``, a ``<refSlot>Count: Int!`` field is emitted.

Public API::

    sdl = emit_sdl(spec)
"""

from __future__ import annotations

from knot.ast.types import Enum
from knot.spec import OntologyClass, Spec, VirtualClass
from knot_graphql.types import enum_sdl, gql_type


def emit_sdl(spec: Spec) -> str:
    """Return one GraphQL SDL string for the whole spec.

    The string is self-contained: it includes enum type definitions,
    one ``type`` block per concrete and virtual class, and a ``Query``
    root with by-id and list fields for every class.
    """
    parts: list[str] = []

    # 1. Enum type definitions (global, referenced by field types)
    for cls in spec.concrete_classes():
        for slot in cls.effective_slots():
            if isinstance(slot.type, Enum):
                parts.append(enum_sdl(cls.name, slot.name, slot.type.values))

    # 2. Concrete class types
    for cls in spec.concrete_classes():
        parts.append(_class_type(cls, spec))

    # 3. Virtual class types (same field shape as their concrete root)
    for vc in spec.virtual_classes():
        parts.append(_virtual_type(vc))

    # 4. Query root
    parts.append(_query_root(spec))

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Type block builders
# ---------------------------------------------------------------------------


def _class_type(cls: OntologyClass, spec: Spec) -> str:
    """Emit one ``type <ClassName> { … }`` block."""
    fields: list[str] = []

    for slot in cls.effective_slots():
        gql = gql_type(
            slot.type,
            required=slot.required,
            class_name=cls.name,
            slot_name=slot.name,
        )
        fields.append(f"  {slot.name}: {gql}")

    # Reverse-FK fields: one count field per (ref_class, fk_slot)
    # pair. Namespace by the referring class so two different
    # classes with same-named FK slots (e.g. MovieCredit.person +
    # GameCredit.person both → Person) get distinct field names.
    for ref_cls, ref_slot in cls.referrers:
        count_name = f"{_camel(ref_cls.name)}{_pascal(ref_slot.name)}Count"
        fields.append(f"  {count_name}: Int!")

    body = "\n".join(fields)
    return f"type {cls.name} {{\n{body}\n}}"


def _virtual_type(vc: VirtualClass) -> str:
    """Emit a ``type <VirtualName>`` block mirroring its concrete root's fields."""
    root = vc.concrete_root()
    fields: list[str] = []

    for slot in root.effective_slots():
        gql = gql_type(
            slot.type,
            required=slot.required,
            class_name=root.name,
            slot_name=slot.name,
        )
        fields.append(f"  {slot.name}: {gql}")

    body = "\n".join(fields)
    return f"type {vc.name} {{\n{body}\n}}"


# ---------------------------------------------------------------------------
# Query root
# ---------------------------------------------------------------------------


def _query_root(spec: Spec) -> str:
    """Emit the ``type Query { … }`` block."""
    lines: list[str] = []

    for cls in spec.concrete_classes():
        lines.append(f"  {_camel(cls.name)}(canonical_id: ID!): {cls.name}")
        lines.append(
            f"  {_camel(cls.name)}List(first: Int = 20, after: String): [{cls.name}!]!"
        )

    for vc in spec.virtual_classes():
        lines.append(f"  {_camel(vc.name)}(canonical_id: ID!): {vc.name}")
        lines.append(
            f"  {_camel(vc.name)}List(first: Int = 20, after: String): [{vc.name}!]!"
        )

    body = "\n".join(lines)
    return f"type Query {{\n{body}\n}}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _camel(name: str) -> str:
    """``Movie`` → ``movie``, ``DirectedMovie`` → ``directedMovie``."""
    return name[0].lower() + name[1:]


def _pascal(name: str) -> str:
    """``person`` → ``Person``, ``birth_year`` → ``Birth_year``."""
    return name[0].upper() + name[1:] if name else name
