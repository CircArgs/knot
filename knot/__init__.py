"""knot — reflective ontology compiler (Python prototype).

Spec construction via dataclass builders + a semantic expression
language for constraint bodies and virtual-class predicates. No raw
SQL strings cross knot's user surface (see ``knot.expr``).

Entity-local validation runs in each dataclass's ``__post_init__``;
cross-entity well-formedness via ``Spec.validate()`` (returns errors)
or ``Spec.validate_strict()`` (raises ``SpecError``).
"""

from knot.expr import (
    Between,
    BoolOp,
    Compare,
    CountRel,
    Exists,
    Expr,
    InList,
    IsNull,
    Literal,
    Not,
    Raw,
    Ref,
    lit,
    raw,
)
from knot.spec import (
    BINDING_PRIOR_STRENGTH,
    Array,
    ClassKind,
    ClassRef,
    Constraint,
    OntologyClass,
    Primitive,
    Severity,
    Slot,
    Source,
    SourceBinding,
    SourceMap,
    Spec,
    SpecError,
    TypeExpression,
    VirtualClass,
)

__all__ = [
    # core spec entities
    "Primitive",
    "ClassKind",
    "Severity",
    "Array",
    "ClassRef",
    "TypeExpression",
    "Slot",
    "OntologyClass",
    "VirtualClass",
    "Constraint",
    "Source",
    "SourceBinding",
    "SourceMap",
    "BINDING_PRIOR_STRENGTH",
    "Spec",
    "SpecError",
    # expression builder
    "Expr",
    "Ref",
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
    "lit",
    "raw",
]
