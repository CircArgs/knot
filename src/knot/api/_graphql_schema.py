"""GraphQL schema generation from a published Spec.

Each OntologyClass becomes a root query with several fields:

  movies(where, limit, offset, asOf, orderBy) -> MoviePage
      Paginated list with total count.  ``MoviePage`` shape:
          { rows: [String!]!, total: Int!, limit: Int!, offset: Int!, asOf: Int }

  movieByCanonicalId(canonicalId, asOf) -> String | null
      All contributions for a single canonical_id serialised as one JSON
      string.  Multiple sources → alphabetical-source tiebreak for scalar
      fields; multivalued slots unioned.  Returns null when not found.

  movieResolved(canonicalId, asOf) -> String | null
      Trust-resolved record via ``knot.graph.resolve.resolve_entity``.
      Returns null when not found.

orderBy is a list of { field: MovieField!, direction: ASC | DESC } objects.
MovieField is an enum of stored slot names.

Schema is cached by content_hash so it rebuilds only when the spec changes.

Centralisation rule: all SQL lives in db/. The resolver here delegates to
graph_store.query_rows / count_rows / get_canonical_contributions and
resolve.resolve_entity; ORDER BY is compiled via sql_compiler.compile_order_by.

Traversal (relation joins) and projection are out of scope for this slice.
"""

from __future__ import annotations

import enum
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


def _all_slots(oc: OntologyClass) -> list[Slot]:
    """Collect the full slot set for a class, walking the is_a chain.

    Defined classes inherit all slots from their parent (is_a) structurally.
    Own slots shadow parent slots of the same name.
    """
    seen_names: set[str] = set()
    result: list[Slot] = []
    current: OntologyClass | None = oc
    while current is not None:
        for slot in current.slots:
            if slot.name not in seen_names:
                seen_names.add(slot.name)
                result.append(slot)
        current = current.is_a
    return result


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
    """Build the top-level WhereInput for a class (one field per slot).

    For defined classes (is_a set + definition), walks the is_a chain to
    collect all inherited slots so the GraphQL surface matches actual columns.
    """
    type_name = f"WhereInput_{oc.name}"
    slot_types = {s.name: _make_slot_where_type(s, oc.name) for s in _all_slots(oc)}
    annotations: dict[str, Any] = {
        name: Optional[t] for name, t in slot_types.items()
    }
    ns: dict[str, Any] = {name: strawberry.UNSET for name in annotations}
    cls = type(type_name, (), {"__annotations__": annotations, **ns})
    return strawberry.input(cls)


# ---------------------------------------------------------------------------
# Page type: { rows: [String!]!, total: Int!, limit: Int!, offset: Int!, asOf: Int }
# ---------------------------------------------------------------------------

def _make_page_type(class_name: str) -> type:
    type_name = f"Page_{class_name}"
    annotations: dict[str, Any] = {
        "rows":   list[str],
        "total":  int,
        "limit":  int,
        "offset": int,
        "as_of":  Optional[int],
    }
    ns: dict[str, Any] = {
        "rows":   strawberry.UNSET,
        "total":  strawberry.UNSET,
        "limit":  strawberry.UNSET,
        "offset": strawberry.UNSET,
        "as_of":  None,
    }
    cls = type(type_name, (), {"__annotations__": annotations, **ns})
    return strawberry.type(cls)


# ---------------------------------------------------------------------------
# OrderBy input: { field: <ClassField>, direction: ASC | DESC }
# ---------------------------------------------------------------------------

@strawberry.enum
class OrderDirection(enum.Enum):
    ASC = "ASC"
    DESC = "DESC"


def _make_field_enum(oc: OntologyClass) -> type:
    """Build a strawberry enum of stored slot names for a class.

    For defined classes, walks the is_a chain so all inherited slots appear.
    """
    enum_name = f"Field_{oc.name}"
    # stored slots only (no derivation), collected from full inheritance chain
    stored = [s for s in _all_slots(oc) if getattr(s, "derivation", None) is None]
    members = {s.name: s.name for s in stored}
    py_enum = enum.Enum(enum_name, members)  # type: ignore[misc]
    return strawberry.enum(py_enum)


def _make_order_by_input(oc: OntologyClass, field_enum: type) -> type:
    """Build the OrderBy input type for a class."""
    type_name = f"OrderBy_{oc.name}"
    annotations: dict[str, Any] = {
        "field":     field_enum,
        "direction": OrderDirection,
    }
    ns: dict[str, Any] = {
        "field":     strawberry.UNSET,
        "direction": OrderDirection.ASC,
    }
    cls = type(type_name, (), {"__annotations__": annotations, **ns})
    return strawberry.input(cls)


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

    For defined classes (backed by VIEW), the SQL compiler validates slots
    against the primary class.  Since the VIEW exposes the parent class's
    columns, we use the effective storage class (is_a chain root) as primary.
    """
    if where_input is strawberry.UNSET or where_input is None:
        return None, []

    # For defined classes, compile predicates against the parent class so that
    # slot-identity validation in _compile_slot_path finds the right columns.
    # The VIEW exposes the parent's columns directly.
    storage_class = oc
    if getattr(oc, "definition", None) is not None and oc.is_a is not None:
        storage_class = oc.is_a

    all_predicates: list[Any] = []
    for slot in _all_slots(oc):
        slot_where = getattr(where_input, slot.name, strawberry.UNSET)
        if slot_where is strawberry.UNSET or slot_where is None:
            continue
        all_predicates.extend(_slot_where_to_predicates(slot, slot_where, storage_class))

    if not all_predicates:
        return None, []

    ctx = CompileContext(primary_class=storage_class, alias=alias)
    if len(all_predicates) == 1:
        fragment = compile_predicate(all_predicates[0], ctx)
    else:
        bool_expr = BoolExpr(op=BoolOpKind.AND, operands=all_predicates)
        fragment = compile_predicate(bool_expr, ctx)

    return fragment, ctx.params


def _build_order_by_sql(
    order_by_list: Any,
    alias: str = "s",
) -> sql.Composable | None:
    """Convert a list of OrderBy input objects to a sql.Composable fragment.

    Returns None when the list is empty/unset (caller uses default sort).
    """
    from knot.db.sql_compiler import compile_order_by

    if order_by_list is strawberry.UNSET or not order_by_list:
        return None
    terms = [(item.field.value, item.direction.value) for item in order_by_list]
    return compile_order_by(terms, alias=alias)


# ---------------------------------------------------------------------------
# Single-entity merge: multiple contributions → one dict (alpha-source tiebreak)
# ---------------------------------------------------------------------------

def _merge_contributions(
    contribs: list[dict[str, Any]],
    oc: OntologyClass,
) -> dict[str, Any]:
    """Merge multiple per-source contribution dicts into one.

    Scalar slots: value from alphabetically-first source that provides a
    non-null value (contribs are already ordered by _source from
    get_canonical_contributions).
    Multivalued slots: union across sources in source order, deduped.
    System columns (_canonical_id, _source, etc.) taken from first contrib.
    """
    if not contribs:
        return {}
    # contribs already sorted by _source (get_canonical_contributions ORDER BY s._source)
    merged: dict[str, Any] = {}
    # Copy system columns from first contrib
    for k, v in contribs[0].items():
        if k.startswith("_"):
            merged[k] = v

    for slot in _all_slots(oc):
        if slot.multivalued:
            flat: list[Any] = []
            seen_set: set = set()
            for c in contribs:
                vals = c.get(slot.name)
                if vals is None:
                    continue
                for v in vals:
                    try:
                        if v not in seen_set:
                            seen_set.add(v)
                            flat.append(v)
                    except TypeError:
                        if v not in flat:
                            flat.append(v)
            merged[slot.name] = flat if flat else None
        else:
            merged[slot.name] = None
            for c in contribs:
                val = c.get(slot.name)
                if val is not None:
                    merged[slot.name] = val
                    break  # first non-null alphabetical source wins
    return merged


# ---------------------------------------------------------------------------
# Schema builder
# ---------------------------------------------------------------------------

def _build_schema(spec: Spec) -> Schema:
    """Build a Strawberry Schema from the published Spec.

    Strategy for dynamic resolver types:
    - Build all input/result types per class.
    - Register a throw-away module in sys.modules so Strawberry can look up
      the type names from the resolver's __module__ attribute.
    - Create resolver functions using exec() so we control __globals__
      and __module__.

    Per-class root fields:
      <class_lower>(where, limit, offset, asOf, orderBy) -> Page_<Class>
      <class_lower>ByCanonicalId(canonicalId, asOf)      -> String | null
      <class_lower>Resolved(canonicalId, asOf)           -> String | null
    """
    from knot import db
    from knot.db import graph_store
    from knot.graph import resolve as _resolve_mod

    concrete_classes = [c for c in spec.classes if not c.abstract]

    # Build per-class types.
    where_types: dict[str, type] = {}
    page_types: dict[str, type] = {}
    field_enums: dict[str, type] = {}
    order_by_inputs: dict[str, type] = {}
    for oc in concrete_classes:
        where_types[oc.name] = _make_class_where_type(oc)
        page_types[oc.name] = _make_page_type(oc.name)
        field_enums[oc.name] = _make_field_enum(oc)
        order_by_inputs[oc.name] = _make_order_by_input(oc, field_enums[oc.name])

    # Build a synthetic module for Strawberry's type resolution.
    mod_name = f"knot.api._graphql_schema._dynamic_{id(spec)}"
    mod = types.ModuleType(mod_name)
    mod.__dict__["Optional"] = Optional
    mod.__dict__["int"] = int
    mod.__dict__["str"] = str
    mod.__dict__["list"] = list
    for t in where_types.values():
        mod.__dict__[t.__name__] = t
    for t in page_types.values():
        mod.__dict__[t.__name__] = t
    for t in field_enums.values():
        mod.__dict__[t.__name__] = t
    for t in order_by_inputs.values():
        mod.__dict__[t.__name__] = t
    mod.__dict__["OrderDirection"] = OrderDirection
    # Runtime helpers available inside resolver bodies.
    mod.__dict__["_json"] = json
    mod.__dict__["_db"] = db
    mod.__dict__["_gs"] = graph_store
    mod.__dict__["_resolve_mod"] = _resolve_mod
    mod.__dict__["_build_pred"] = build_predicate_sql
    mod.__dict__["_build_order_by"] = _build_order_by_sql
    mod.__dict__["_merge_contribs"] = _merge_contributions
    mod.__dict__["_UNSET"] = strawberry.UNSET

    sys.modules[mod_name] = mod

    query_fields: dict[str, Any] = {}

    for oc in concrete_classes:
        wtype = where_types[oc.name]
        ptype = page_types[oc.name]
        ob_input = order_by_inputs[oc.name]
        wtype_name = wtype.__name__
        ptype_name = ptype.__name__
        ob_name = ob_input.__name__
        bound_oc = oc

        # Unique per-class bindings in the module dict.
        oc_key = f"_oc_{oc.name}"
        ptype_key = f"_ptype_{oc.name}"
        mod.__dict__[oc_key] = bound_oc
        mod.__dict__[ptype_key] = ptype

        cls_lower = oc.name.lower()

        # ── List resolver (paginated) ──────────────────────────────────────
        list_fn_name = f"resolve_{cls_lower}"
        list_fn_src = (
            f"def {list_fn_name}(\n"
            f"    where: Optional[{wtype_name}] = _UNSET,\n"
            f"    limit: int = 100,\n"
            f"    offset: int = 0,\n"
            f"    as_of: Optional[int] = None,\n"
            f"    order_by: Optional[list[{ob_name}]] = _UNSET,\n"
            f") -> {ptype_name}:\n"
            f"    pred_sql, pred_params = _build_pred({oc_key}, where)\n"
            f"    ob_sql = _build_order_by(order_by)\n"
            f"    with _db.connect() as conn:\n"
            f"        rows = _gs.query_rows(\n"
            f"            conn, cls={oc_key},\n"
            f"            predicate_sql=pred_sql,\n"
            f"            predicate_params=pred_params,\n"
            f"            limit=limit, offset=offset, as_of=as_of,\n"
            f"            order_by_sql=ob_sql,\n"
            f"        )\n"
            f"        total = _gs.count_rows(\n"
            f"            conn, cls={oc_key}, as_of=as_of,\n"
            f"            predicate_sql=pred_sql, predicate_params=pred_params,\n"
            f"        )\n"
            f"    return {ptype_key}(\n"
            f"        rows=[_json.dumps(r, default=str) for r in rows],\n"
            f"        total=total, limit=limit, offset=offset, as_of=as_of,\n"
            f"    )\n"
        )

        # ── Single-entity (contributions) resolver ─────────────────────────
        by_id_fn_name = f"resolve_{cls_lower}_by_canonical_id"
        by_id_fn_src = (
            f"def {by_id_fn_name}(\n"
            f"    canonical_id: str,\n"
            f"    as_of: Optional[int] = None,\n"
            f") -> Optional[str]:\n"
            f"    with _db.connect() as conn:\n"
            f"        contribs = _gs.get_canonical_contributions(\n"
            f"            conn, cls={oc_key},\n"
            f"            canonical_id=canonical_id, as_of=as_of,\n"
            f"        )\n"
            f"    if not contribs:\n"
            f"        return None\n"
            f"    merged = _merge_contribs(contribs, {oc_key})\n"
            f"    return _json.dumps(merged, default=str)\n"
        )

        # ── Resolved view resolver ─────────────────────────────────────────
        resolved_fn_name = f"resolve_{cls_lower}_resolved"
        resolved_fn_src = (
            f"def {resolved_fn_name}(\n"
            f"    canonical_id: str,\n"
            f"    as_of: Optional[int] = None,\n"
            f") -> Optional[str]:\n"
            f"    with _db.connect() as conn:\n"
            f"        record = _resolve_mod.resolve_entity(\n"
            f"            conn, cls={oc_key},\n"
            f"            canonical_id=canonical_id, as_of=as_of,\n"
            f"        )\n"
            f"    if record is None:\n"
            f"        return None\n"
            f"    return _json.dumps(record, default=str)\n"
        )

        for fn_src, fn_name in [
            (list_fn_src, list_fn_name),
            (by_id_fn_src, by_id_fn_name),
            (resolved_fn_src, resolved_fn_name),
        ]:
            exec(fn_src, mod.__dict__)  # noqa: S102
            fn = mod.__dict__[fn_name]
            fn.__module__ = mod_name

        # Register the three root fields.
        query_fields[cls_lower] = strawberry.field(
            resolver=mod.__dict__[list_fn_name]
        )
        query_fields[f"{cls_lower}ByCanonicalId"] = strawberry.field(
            resolver=mod.__dict__[by_id_fn_name]
        )
        query_fields[f"{cls_lower}Resolved"] = strawberry.field(
            resolver=mod.__dict__[resolved_fn_name]
        )

    Query = strawberry.type(type("Query", (), query_fields))
    return strawberry.Schema(query=Query)
