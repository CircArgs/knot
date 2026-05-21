"""Canonical type API for knot specs.

The one way to declare slot types — no enum direct access, no string
shorthand, no separate FK method. Everything funnels through this
module:

    types.TEXT, types.INTEGER, types.FLOAT, types.BOOLEAN,
    types.DATE, types.TIMESTAMP             — primitive scalars
    types.ARRAY(types.TEXT)                 — homogeneous array
    types.VECTOR(384)                       — pgvector dense embedding

FKs are not a separate type — passing an ``OntologyClass`` directly
to ``slot()`` (or to ``ARRAY()``) wraps it in a ``ClassRef``:

    movie.slot("director", person)
    movie.slot("authors", types.ARRAY(person))

The dataclasses ``Primitive``, ``Array``, ``ClassRef``, ``Vector``
are the internal AST representation — defined here, imported by every
layer that needs them (``spec.py``, the compile emitters). This module
is the canonical home; nothing else *defines* them.
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


# pgvector distance operator classes. The metric name picks both the
# operator family for the HNSW index and the comparison operator at
# query time. Closed set — extending requires a deliberate edit.
_VECTOR_OPS = {
    "cosine": "vector_cosine_ops",
    "l2": "vector_l2_ops",
    "ip": "vector_ip_ops",
}


@dataclass(slots=True)
class Vector:
    """Dense embedding column backed by pgvector. ``dim`` is the
    embedding dimension (required, must be positive); ``metric`` picks
    the distance function the HNSW index is built for."""

    dim: int
    metric: str = "cosine"

    def __post_init__(self):
        if not isinstance(self.dim, int) or self.dim <= 0:
            raise ValueError(f"VECTOR dim must be a positive integer, got {self.dim!r}")
        if self.metric not in _VECTOR_OPS:
            raise ValueError(
                f"VECTOR metric must be one of {sorted(_VECTOR_OPS)}, "
                f"got {self.metric!r}"
            )

    @property
    def hnsw_ops(self) -> str:
        """pgvector operator class for the HNSW index."""
        return _VECTOR_OPS[self.metric]

    def __str__(self) -> str:
        return f"vector<{self.dim},{self.metric}>"


@dataclass(slots=True)
class Enum:
    """Closed set of allowed text values for a slot.

    Stored as ``TEXT`` in postgres with an inline ``CHECK`` constraint.
    No ``CREATE TYPE`` ceremony — the constraint is self-contained in
    the ``CREATE TABLE``, so adding/removing values is a simple
    ``ALTER TABLE … ALTER COLUMN … TYPE … USING …`` (or migrate-tool
    diff) with no separate type object to manage."""

    values: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.values:
            raise ValueError("ENUM requires at least one value")
        for v in self.values:
            if not isinstance(v, str):
                raise TypeError(
                    f"ENUM values must all be strings; got {type(v).__name__}: {v!r}"
                )
        if len(set(self.values)) != len(self.values):
            seen: set[str] = set()
            dupes = [v for v in self.values if v in seen or seen.add(v)]  # type: ignore[func-returns-value]
            raise ValueError(f"ENUM values must be unique; duplicates: {dupes!r}")

    def __str__(self) -> str:
        return "enum(" + ", ".join(repr(v) for v in self.values) + ")"


TypeExpression = Primitive | Array | ClassRef | Vector | Enum


def _coerce_type(t):
    """Validate or coerce a slot type. Canonical inputs:

      - values from ``knot.ast.types`` (``types.TEXT``, ``types.ARRAY(...)``,
        ``types.VECTOR(...)``)
      - an ``OntologyClass`` instance (auto-wraps in ``ClassRef``)

    Local import of ``OntologyClass`` keeps this module self-contained
    at type-check time (ast → spec would otherwise be a cycle)."""
    from knot.spec import OntologyClass

    if isinstance(t, (Primitive, Array, ClassRef, Vector, Enum)):
        return t
    if isinstance(t, OntologyClass):
        return ClassRef(target=t)
    raise TypeError(
        f"Slot type must be a value from knot.ast.types (TEXT/INTEGER/FLOAT/"
        f"BOOLEAN/DATE/TIMESTAMP/ARRAY(...)/VECTOR(...)/ENUM(...)) or an "
        f"OntologyClass instance; got {type(t).__name__}"
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


def VECTOR(dim: int, metric: str = "cosine") -> Vector:
    """Dense embedding column backed by pgvector. ``dim`` is the
    embedding dimension; ``metric`` is one of ``"cosine"``, ``"l2"``,
    ``"ip"`` and picks both the HNSW index operator class and the
    query-time distance operator."""
    return Vector(dim=dim, metric=metric)


def ENUM(*values: str) -> Enum:
    """Closed set of allowed text values. Stored as ``TEXT`` with an
    inline ``CHECK`` constraint — no ``CREATE TYPE`` ceremony."""
    return Enum(values=tuple(values))
