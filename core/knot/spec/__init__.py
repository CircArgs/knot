"""Ontology submodule — typed spec graph + canonical hashing.

Pure: no SQL, no postgres, no I/O. The typed entity tree (``metaschema``)
and the canonical-form hasher (``canonical``) live here. Expression-tree
→ SQL emission and DDL emission both live under ``knot.db``.
"""

from knot.spec.canonical import canonical_dump, compute_content_hash
from knot.spec.effective_constraints import effective_constraints
from knot.spec.effective_properties import effective_properties, is_stored, stored_property_names
from knot.spec.errors import (
    DraftAlreadyPublishedError,
    DraftNotFoundError,
    PublishGateError,
)
from knot.spec.metaschema import (
    STANDARD_PRIMITIVE_NAMES,
    AnyClass,
    Array,
    BoolExpr,
    BoolOpKind,
    ClassRef,
    Compare,
    CompareOp,
    Constraint,
    DefinedClass,
    DerivedProperty,
    Literal_,
    NullSemantics,
    OntologyClass,
    Primitive,
    Property,
    PropertyConstraints,
    PropertyMapping,
    PropertyPath,
    RelationAggregate,
    RelationAll,
    RelationAny,
    RelationCount,
    RelationFirst,
    RelationProject,
    RelationRef,
    ResolutionPolicy,
    Severity,
    Source,
    SourceBinding,
    Spec,
    SpecBase,
    TypeExpression,
)
from knot.spec.serialization import spec_from_dict, spec_to_dict

__all__ = [
    # entity types
    "Spec",
    "OntologyClass",
    "DefinedClass",
    "AnyClass",
    "Property",
    "Source",
    "PropertyMapping",
    "SourceBinding",
    "Constraint",
    "DerivedProperty",
    "SpecBase",
    # type expression hierarchy
    "TypeExpression",
    "Primitive",
    "Array",
    "ClassRef",
    "STANDARD_PRIMITIVE_NAMES",
    # property constraints
    "PropertyConstraints",
    # enums
    "ResolutionPolicy",
    "Severity",
    "CompareOp",
    "BoolOpKind",
    "NullSemantics",
    # expression tree
    "Literal_",
    "PropertyPath",
    "Compare",
    "BoolExpr",
    "RelationRef",
    "RelationProject",
    "RelationCount",
    "RelationAggregate",
    "RelationAny",
    "RelationAll",
    "RelationFirst",
    # canonical
    "compute_content_hash",
    "canonical_dump",
    # serialization (full-fidelity round-trip)
    "spec_from_dict",
    "spec_to_dict",
    # effective-property helpers
    "effective_properties",
    "is_stored",
    "stored_property_names",
    # effective-constraint helpers (walks is_a + mixin chain)
    "effective_constraints",
    # errors
    "DraftAlreadyPublishedError",
    "DraftNotFoundError",
    "PublishGateError",
]
