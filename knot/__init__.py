"""knot — reflective ontology compiler (Python prototype).

Spec construction via dataclass builders + a semantic expression
language for constraint bodies and virtual-class predicates. Slot
types come from ``knot.types`` (the canonical surface); no raw SQL
strings cross knot's user surface (see ``knot.expr``).

Entity-local validation runs in each dataclass's ``__post_init__``;
cross-entity well-formedness via ``Spec.validate()`` (raises
``SpecError``).
"""

from knot import types
from knot.expr import (
    Aggregate,
    Between,
    BoolOp,
    Compare,
    CountRel,
    Exists,
    Expr,
    FkChainRef,
    FkRef,
    InList,
    IsNull,
    Literal,
    Not,
    Raw,
    Ref,
    This,
    lit,
    raw,
    this,
)
from knot.select import OrderBy, Query
from knot.spec import (
    CORRECTIONS_SOURCE_NAME,
    ClassKind,
    Constraint,
    OntologyClass,
    Severity,
    Slot,
    SlotMapping,
    Source,
    SourceBinding,
    Spec,
    SpecError,
    TypeExpression,
    VirtualClass,
)

__all__ = [
    # type API
    "types",
    "TypeExpression",
    # core spec entities
    "ClassKind",
    "Severity",
    "Slot",
    "OntologyClass",
    "VirtualClass",
    "Constraint",
    "Source",
    "SourceBinding",
    "SlotMapping",
    "CORRECTIONS_SOURCE_NAME",
    "Spec",
    "SpecError",
    # expression builder
    "Expr",
    "Ref",
    "FkRef",
    "FkChainRef",
    "Literal",
    "Compare",
    "BoolOp",
    "Not",
    "IsNull",
    "InList",
    "Between",
    "Exists",
    "CountRel",
    "Raw",
    "This",
    "Aggregate",
    "this",
    "lit",
    "raw",
    # read substrate
    "Query",
    "OrderBy",
]
