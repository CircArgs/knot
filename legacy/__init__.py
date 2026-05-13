"""knot — reflective ontology compiler.

Pure library. Takes a typed Pydantic ``Spec`` and emits the runtime
artifacts (postgres DDL + GraphQL schema + Pydantic row validators +
constraint SQL). Nothing here touches a connection or serves a request.

See ``LIBRARY_DESIGN.md`` for the implementer's handoff and ``RFC.md``
for the consumer-facing pitch.
"""

from importlib.metadata import version

# ── Metaschema + spec-graph walkers ──────────────────────────────────
from knot.spec import (
    AnyClass,
    Array,
    BoolExpr,
    BoolOpKind,
    ClassRef,
    Compare,
    CompareOp,
    Constraint,
    DefinedClass,
    DerivedSlot,
    DraftAlreadyPublishedError,
    DraftNotFoundError,
    Literal_,
    NullSemantics,
    OntologyClass,
    Primitive,
    PublishGateError,
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
    STANDARD_PRIMITIVE_NAMES,
    TypeExpression,
    canonical_dump,
    compute_content_hash,
    effective_constraints,
    effective_slots,
    is_stored,
    spec_from_dict,
    spec_to_dict,
    stored_slot_names,
)

__version__ = version("knot")

__all__ = (
    "__version__",
    # entity types
    "Spec",
    "OntologyClass",
    "DefinedClass",
    "AnyClass",
    "Slot",
    "Source",
    "SlotMapping",
    "SourceBinding",
    "Constraint",
    "DerivedSlot",
    "SpecBase",
    # type expressions
    "TypeExpression",
    "Primitive",
    "Array",
    "ClassRef",
    "STANDARD_PRIMITIVE_NAMES",
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
    # walkers
    "effective_slots",
    "effective_constraints",
    "is_stored",
    "stored_slot_names",
    # canonical + serialization
    "canonical_dump",
    "compute_content_hash",
    "spec_from_dict",
    "spec_to_dict",
    # errors
    "PublishGateError",
    "DraftNotFoundError",
    "DraftAlreadyPublishedError",
)
