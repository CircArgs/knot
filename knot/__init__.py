"""knot — reflective ontology compiler (Python prototype).

Spec construction via dataclass builders + a semantic expression
language for constraint bodies and virtual-class predicates. Slot
types come from ``knot.types`` (the canonical surface); no raw SQL
strings cross knot's user surface (see ``knot.expr``).

Entity-local validation runs in each dataclass's ``__post_init__``;
cross-entity well-formedness via ``Spec.validate()`` (raises
``SpecError``).
"""

from knot.ast import types
from knot.ast.expr import (
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
from knot.ast.select import Layer, OrderBy, Query
from knot.ast.types import Vector
from knot.spec import (
    CORRECTIONS_SOURCE_NAME,
    DEFAULT_WEIGHT,
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
