"""knot — reflective ontology compiler (Python prototype).

Spec construction via dataclass builders. SQL strings everywhere SQL
appears. No compile layer yet — this is just the spec graph.
"""

from knot.spec import (
    BINDING_PRIOR_STRENGTH,
    Array,
    ClassRef,
    Constraint,
    OntologyClass,
    Primitive,
    Slot,
    Source,
    SourceBinding,
    Spec,
    TypeExpression,
    VirtualClass,
)

__all__ = [
    "Primitive",
    "Array",
    "ClassRef",
    "TypeExpression",
    "Slot",
    "OntologyClass",
    "VirtualClass",
    "Constraint",
    "Source",
    "SourceBinding",
    "BINDING_PRIOR_STRENGTH",
    "Spec",
]
