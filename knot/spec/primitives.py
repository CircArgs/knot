"""The standard primitive types every knot spec inherits from the base spec.

These six TypeDefinitions are auto-published as the base spec on first
startup. New drafts branch from the latest published revision by default,
so authors get the primitives via lineage and don't need to register them.
"""

from __future__ import annotations

from knot.spec.metaschema import TypeDefinition

STANDARD_PRIMITIVES: list[TypeDefinition] = [
    TypeDefinition(name="string",   base="str"),
    TypeDefinition(name="integer",  base="int"),
    TypeDefinition(name="float",    base="float"),
    TypeDefinition(name="boolean",  base="bool"),
    TypeDefinition(name="datetime", base="datetime"),
    TypeDefinition(name="date",     base="date"),
]


__all__ = ["STANDARD_PRIMITIVES"]
