"""Canonical type API for knot specs.

The one way to declare slot types — no enum direct access, no string
shorthand, no separate FK method. Everything funnels through this
module:

    types.TEXT, types.INTEGER, types.FLOAT, types.BOOLEAN,
    types.DATE, types.TIMESTAMP             — primitive scalars
    types.ARRAY(types.TEXT)                 — homogeneous array

FKs are not a separate type — passing an ``OntologyClass`` directly
to ``slot()`` (or to ``ARRAY()``) wraps it in a ``ClassRef``:

    movie.slot("director", person)
    movie.slot("authors", types.ARRAY(person))

The objects returned here are the same ``Primitive`` / ``Array``
instances the compile layer dispatches on — this module is the
user-facing surface, not a parallel representation.
"""

from __future__ import annotations

from knot.spec import Array, Primitive

TEXT = Primitive.TEXT
INTEGER = Primitive.INTEGER
FLOAT = Primitive.FLOAT
BOOLEAN = Primitive.BOOLEAN
DATE = Primitive.DATE
TIMESTAMP = Primitive.TIMESTAMP


def ARRAY(of):
    """Homogeneous array of another knot type or class."""
    return Array(of=of)


__all__ = [
    "TEXT",
    "INTEGER",
    "FLOAT",
    "BOOLEAN",
    "DATE",
    "TIMESTAMP",
    "ARRAY",
]
