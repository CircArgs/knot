"""Ontology submodule — typed spec graph + canonical hashing.

Pure: no SQL, no postgres, no I/O. The typed entity tree (``metaschema``)
and the canonical-form hasher (``canonical``) live here. Expression-tree
→ SQL emission and DDL emission both live under ``knot.db``.
"""

from knot.spec.canonical import canonical_dump, compute_content_hash
from knot.spec.effective_slots import effective_slots, is_stored, stored_slot_names
from knot.spec.errors import (
    DraftAlreadyPublishedError,
    DraftNotFoundError,
    PublishGateError,
)
from knot.spec.metaschema import (
    STANDARD_PRIMITIVE_NAMES,
    Array,
    BoolExpr,
    BoolOpKind,
    ClassRef,
    Compare,
    CompareOp,
    Constraint,
    DerivedSlot,
    Literal_,
    NullSemantics,
    OntologyClass,
    Primitive,
    RelationAggregate,
    RelationAll,
    RelationAny,
    RelationCount,
    RelationFirst,
    RelationProject,
    RelationRef,
    ResolutionPolicy,
    Severity,
    Slot,
    SlotConstraints,
    SlotMapping,
    SlotPath,
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
    "Slot",
    "Source",
    "SlotMapping",
    "SourceBinding",
    "Constraint",
    "DerivedSlot",
    "SpecBase",
    # type expression hierarchy
    "TypeExpression",
    "Primitive",
    "Array",
    "ClassRef",
    "STANDARD_PRIMITIVE_NAMES",
    # slot constraints
    "SlotConstraints",
    # enums
    "ResolutionPolicy",
    "Severity",
    "CompareOp",
    "BoolOpKind",
    "NullSemantics",
    # expression tree
    "Literal_",
    "SlotPath",
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
    # effective-slot helpers
    "effective_slots",
    "is_stored",
    "stored_slot_names",
    # errors
    "DraftAlreadyPublishedError",
    "DraftNotFoundError",
    "PublishGateError",
]
