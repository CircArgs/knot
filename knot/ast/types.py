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

The dataclasses ``Primitive``, ``Array``, ``ClassRef`` are the
internal AST representation — defined here, imported by every layer
that needs them (``spec.py``, the compile emitters). This module is
the canonical home; nothing else *defines* them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from knot.spec import OntologyClass


class Primitive(StrEnum):
    """The closed set of primitive scalar types."""

    TEXT = "text"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATE = "date"
    TIMESTAMP = "timestamp"


@dataclass(slots=True)
class Array:
    """Homogeneous array of another ``TypeExpression``."""

    of: TypeExpression

    def __post_init__(self):
        # Coerce ``OntologyClass`` to ``ClassRef`` so ``types.ARRAY(person)``
        # works the same way ``slot("director", person)`` does.
        self.of = _coerce_type(self.of)

    def __str__(self) -> str:
        return f"array<{self.of}>"


@dataclass(slots=True)
class ClassRef:
    """FK reference to another class — stored as the target's canonical_id."""

    target: OntologyClass

    def __str__(self) -> str:
        return self.target.name


TypeExpression = Primitive | Array | ClassRef


def _coerce_type(t):
    """Validate or coerce a slot type. Canonical inputs:

      - values from ``knot.ast.types`` (``types.TEXT``, ``types.ARRAY(...)``)
      - an ``OntologyClass`` instance (auto-wraps in ``ClassRef``)

    Local import of ``OntologyClass`` keeps this module self-contained
    at type-check time (ast → spec would otherwise be a cycle)."""
    from knot.spec import OntologyClass

    if isinstance(t, (Primitive, Array, ClassRef)):
        return t
    if isinstance(t, OntologyClass):
        return ClassRef(target=t)
    raise TypeError(
        f"Slot type must be a value from knot.ast.types (TEXT/INTEGER/FLOAT/"
        f"BOOLEAN/DATE/TIMESTAMP/ARRAY(...)) or an OntologyClass instance; "
        f"got {type(t).__name__}"
    )


# Public-facing constants — the user surface.
TEXT = Primitive.TEXT
INTEGER = Primitive.INTEGER
FLOAT = Primitive.FLOAT
BOOLEAN = Primitive.BOOLEAN
DATE = Primitive.DATE
TIMESTAMP = Primitive.TIMESTAMP


def ARRAY(of):
    """Homogeneous array of another knot type or class."""
    return Array(of=of)
