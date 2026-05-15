"""knot.ast — spec-layer primitives.

Three modules that together describe *what* a spec is, with no
postgres / SQL knowledge:

  - ``knot.ast.types`` — canonical user surface for slot types
    (``types.TEXT``, ``types.ARRAY(...)``)
  - ``knot.ast.expr`` — semantic expression substrate for constraint
    bodies, virtual-class predicates, and query WHERE clauses
  - ``knot.ast.select`` — read substrate: ``Query``, ``OrderBy``

These are pure data dataclasses; rendering to SQL lives in
``knot.compile``. Users typically import from ``knot`` directly
(``from knot import types, this``) — those names are re-exported
from this package.
"""

from knot.ast import expr, select, types
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
from knot.ast.select import OrderBy, Query
