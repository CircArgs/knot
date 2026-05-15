"""Canonical type API for knot specs.

The one way to declare slot types — no enum direct access, no string
shorthand, no separate FK method. Everything funnels through this
module:

    types.TEXT, types.INTEGER, types.FLOAT, types.BOOLEAN,
    types.DATE, types.TIMESTAMP             — primitive scalars
    types.ARRAY(types.TEXT)                 — homogeneous array
    types.FK(other_class)                   — FK to another class

The objects returned here are the same ``Primitive`` / ``Array`` /
``ClassRef`` instances the compile layer dispatches on — this module
is the user-facing surface, not a parallel representation.
"""

from __future__ import annotations

from knot.spec import Array, ClassRef, Primitive

TEXT = Primitive.TEXT
INTEGER = Primitive.INTEGER
FLOAT = Primitive.FLOAT
BOOLEAN = Primitive.BOOLEAN
DATE = Primitive.DATE
TIMESTAMP = Primitive.TIMESTAMP


def ARRAY(of):
    """Homogeneous array of another knot type."""
    return Array(of=of)


def FK(target):
    """FK reference to another class. Stored as the target's canonical_id."""
    return ClassRef(target=target)


__all__ = [
    "TEXT",
    "INTEGER",
    "FLOAT",
    "BOOLEAN",
    "DATE",
    "TIMESTAMP",
    "ARRAY",
    "FK",
]
