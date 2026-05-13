"""knot — reflective ontology compiler (Python prototype).

Spec construction via dataclass builders. SQL strings everywhere SQL
appears. Entity-local validation runs in each dataclass's
``__post_init__``; cross-entity well-formedness via ``Spec.validate()``
(returns errors) or ``Spec.validate_strict()`` (raises).
"""

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
    Spec,
    SpecError,
    TypeExpression,
    VirtualClass,
)

__all__ = [
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
    "BINDING_PRIOR_STRENGTH",
    "Spec",
    "SpecError",
]
