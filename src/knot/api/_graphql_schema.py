"""GraphQL schema generation from a published Spec.

Each OntologyClass becomes a Strawberry type; each is queryable with:
  - where: flat AND of Compare predicates per slot (WhereInput per class)
  - limit: Int (default 100)
  - offset: Int (default 0)
  - as_of: Int | None (pin to spec_revision <= N)

Schema is cached by content_hash so it rebuilds only when the spec changes.

Centralisation rule: all SQL lives in db/. The resolver here delegates to
graph_store.query_rows which holds the parameterised SQL.

Traversal (movie.credits joins) and projection (SELECT specific cols) are
out of scope for this slice. Full rows are returned for every match.
"""

from __future__ import annotations

import json
import logging
import sys
import types
from typing import Any, Optional

import strawberry
from psycopg import sql
from strawberry import Schema

from knot.db.sql_compiler import CompileContext, compile_predicate
from knot.ontology.metaschema import (
    BoolExpr,
    BoolOpKind,
    Compare,
    CompareOp,
    Literal_,
    Matches,
    OntologyClass,
    Slot,
    SlotPath,
    Spec,
    TypeDefinition,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema cache  {content_hash: strawberry.Schema}
# ---------------------------------------------------------------------------

_schema_cache: dict[str, Schema] = {}


def get_or_build_schema(spec: Spec, content_hash: str) -> Schema:
    """Return the cached schema for content_hash, building it on miss."""
    if content_hash not in _schema_cache:
        _schema_cache[content_hash] = _build_schema(spec)
    return _schema_cache[content_hash]


# ---------------------------------------------------------------------------
# Slot range → Python type mapping
# ---------------------------------------------------------------------------

_RANGE_TO_PYTHON: dict[str, type] = {
    "str":      str,
    "string":   str,
    "int":      int,
    "integer":  int,
    "float":    float,
    "number":   float,
    "bool":     bool,
    "boolean":  bool,
    "datetime": str,  # ISO-8601 string
}


def _slot_python_type(slot: Slot) -> type:
    rng = slot.range
    if rng is None:
        return str
    if isinstance(rng, TypeDefinition):
        base = rng.base or rng.name
        return _RANGE_TO_PYTHON.get(base, str)
    # OntologyClass reference → canonical_id is a string FK
    return str


# ---------------------------------------------------------------------------
# WhereInput type construction
# ---------------------------------------------------------------------------

def _make_slot_where_type(slot: Slot, class_name: str) -> type:
    """Build a strawberry.input type for one slot's comparison ops."""
    py = _slot_python_type(slot)
    type_name = f"WhereInput_{class_name}_{slot.name}"

    annotations: dict[str, Any] = {
        "eq":          Optional[py],
        "neq":         Optional[py],
        "gt":          Optional[py],
        "gte":         Optional[py],
        "lt":          Optional[py],
        "lte":         Optional[py],
        "in_":         Optional[list[py]],
        "not_in":      Optional[list[py]],
        "is_null":     Optional[bool],
        "is_not_null": Optional[bool],
        "like":        Optional[str],
    }
    ns: dict[str, Any] = {k: strawberry.UNSET for k in annotations}
    cls = type(type_name, (), {"__annotations__": annotations, **ns})
    return strawberry.input(cls)


def _make_class_where_type(oc: OntologyClass) -> type:
    """Build the top-level WhereInput for a class (one field per slot)."""
    type_name = f"WhereInput_{oc.name}"
    slot_types = {s.name: _make_slot_where_type(s, oc.name) for s in oc.slots}
    annotations: dict[str, Any] = {
        name: Optional[t] for name, t in slot_types.items()
    }
    ns: dict[str, Any] = {name: strawberry.UNSET for name in annotations}
    cls = type(type_name, (), {"__annotations__": annotations, **ns})
    return strawberry.input(cls)


# ---------------------------------------------------------------------------
# Result type: {rows: [String!]}
# ---------------------------------------------------------------------------

def _make_result_type(class_name: str) -> type:
    type_name = f"QueryResult_{class_name}"
    annotations: dict[str, Any] = {"rows": list[str]}
    cls = type(type_name, (), {"__annotations__": annotations})
    return strawberry.type(cls)


# ---------------------------------------------------------------------------
# WhereInput → expression tree → (sql_fragment, params)
# ---------------------------------------------------------------------------

_OP_MAP: dict[str, CompareOp] = {
    "eq":          CompareOp.EQ,
    "neq":         CompareOp.NEQ,
    "gt":          CompareOp.GT,
    "gte":         CompareOp.GTE,
    "lt":          CompareOp.LT,
    "lte":         CompareOp.LTE,
    "in_":         CompareOp.IN,
    "not_in":      CompareOp.NOT_IN,
    "is_null":     CompareOp.IS_NULL,
    "is_not_null": CompareOp.IS_NOT_NULL,
}

_UNARY_OPS = {CompareOp.IS_NULL, CompareOp.IS_NOT_NULL}


def _slot_where_to_predicates(
    slot: Slot,
    slot_where: Any,
    oc: OntologyClass,
) -> list[Any]:
    """Convert a per-slot WhereInput to a list of expression-tree nodes."""
    predicates: list[Any] = []
    path = SlotPath(from_class=oc, slots=[slot])

    for field_name, op in _OP_MAP.items():
        val = getattr(slot_where, field_name, strawberry.UNSET)
        if val is strawberry.UNSET or val is None:
            continue
        if op in _UNARY_OPS:
            if val:
                predicates.append(Compare(op=op, left=path))
        elif op in (CompareOp.IN, CompareOp.NOT_IN):
            predicates.append(
                Compare(op=op, left=path, right=Literal_(value=list(val)))
            )
        else:
            predicates.append(
                Compare(op=op, left=path, right=Literal_(value=val))
            )

    like_val = getattr(slot_where, "like", strawberry.UNSET)
    if like_val is not strawberry.UNSET and like_val is not None:
        predicates.append(Matches(left=path, pattern=like_val))

    return predicates


def build_predicate_sql(
    oc: OntologyClass,
    where_input: Any,
    alias: str = "s",
) -> tuple[sql.Composable | None, list[Any]]:
    """Convert the top-level WhereInput object to (sql_fragment, params).

    Returns (None, []) when no filters are specified (caller omits WHERE).
    """
    if where_input is strawberry.UNSET or where_input is None:
        return None, []

    all_predicates: list[Any] = []
    for slot in oc.slots:
        slot_where = getattr(where_input, slot.name, strawberry.UNSET)
        if slot_where is strawberry.UNSET or slot_where is None:
            continue
        all_predicates.extend(_slot_where_to_predicates(slot, slot_where, oc))

    if not all_predicates:
        return None, []

    ctx = CompileContext(primary_class=oc, alias=alias)
    if len(all_predicates) == 1:
        fragment = compile_predicate(all_predicates[0], ctx)
    else:
        bool_expr = BoolExpr(op=BoolOpKind.AND, operands=all_predicates)
        fragment = compile_predicate(bool_expr, ctx)

    return fragment, ctx.params


# ---------------------------------------------------------------------------
# Schema builder
# ---------------------------------------------------------------------------

def _build_schema(spec: Spec) -> Schema:
    """Build a Strawberry Schema from the published Spec.

    Strategy for dynamic resolver types:
    - Build all input/result types per class.
    - Register a throw-away module in sys.modules so Strawberry can look up
      the type names from the resolver's __module__ attribute.
    - Create resolver functions using types.FunctionType so we control
      __globals__ and __module__.
    """
    from knot import db
    from knot.db import graph_store

    concrete_classes = [c for c in spec.classes if not c.abstract]

    # Build input/result types for all classes.
    where_types: dict[str, type] = {}
    result_types: dict[str, type] = {}
    for oc in concrete_classes:
        where_types[oc.name] = _make_class_where_type(oc)
        result_types[oc.name] = _make_result_type(oc.name)

    # Build a synthetic module that Strawberry can look up by name.
    # The module's __dict__ must contain all the type names used in annotations.
    mod_name = f"knot.api._graphql_schema._dynamic_{id(spec)}"
    mod = types.ModuleType(mod_name)
    mod.__dict__["Optional"] = Optional
    mod.__dict__["int"] = int
    mod.__dict__["str"] = str
    for t in where_types.values():
        mod.__dict__[t.__name__] = t
    for t in result_types.values():
        mod.__dict__[t.__name__] = t
    # Runtime helpers available inside resolver bodies.
    mod.__dict__["_json"] = json
    mod.__dict__["_db"] = db
    mod.__dict__["_gs"] = graph_store
    mod.__dict__["_build_pred"] = build_predicate_sql
    mod.__dict__["_UNSET"] = strawberry.UNSET

    sys.modules[mod_name] = mod

    query_fields: dict[str, Any] = {}

    for oc in concrete_classes:
        wtype = where_types[oc.name]
        rtype = result_types[oc.name]
        wtype_name = wtype.__name__
        rtype_name = rtype.__name__
        bound_oc = oc

        # Compile resolver code with type names expressed as strings that
        # resolve via the synthetic module's __dict__.
        fn_name = f"resolve_{oc.name.lower()}"
        fn_src = (
            f"def {fn_name}(\n"
            f"    where: Optional[{wtype_name}] = _UNSET,\n"
            f"    limit: int = 100,\n"
            f"    offset: int = 0,\n"
            f"    as_of: Optional[int] = None,\n"
            f") -> {rtype_name}:\n"
            f"    pred_sql, pred_params = _build_pred(_oc, where)\n"
            f"    with _db.connect() as conn:\n"
            f"        rows = _gs.query_rows(\n"
            f"            conn, cls=_oc,\n"
            f"            predicate_sql=pred_sql,\n"
            f"            predicate_params=pred_params,\n"
            f"            limit=limit, offset=offset, as_of=as_of,\n"
            f"        )\n"
            f"    return _rtype(rows=[_json.dumps(r, default=str) for r in rows])\n"
        )

        # Inject oc-specific bindings into the module's __dict__ under
        # unique names so concurrent classes don't stomp each other.
        oc_key = f"_oc_{oc.name}"
        rtype_key = f"_rtype_{oc.name}"
        mod.__dict__[oc_key] = bound_oc
        mod.__dict__[rtype_key] = rtype

        fn_src = fn_src.replace("_oc", oc_key).replace("_rtype", rtype_key)

        exec(fn_src, mod.__dict__)  # noqa: S102
        resolver_fn = mod.__dict__[fn_name]
        resolver_fn.__module__ = mod_name

        query_fields[oc.name.lower()] = strawberry.field(resolver=resolver_fn)

    Query = strawberry.type(type("Query", (), query_fields))
    return strawberry.Schema(query=Query)
