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

  movieAggregate(where, asOf) -> AggregateResult_Movie
      Count + numeric aggregates over the filtered row set.

orderBy is a list of { field: MovieField!, direction: ASC | DESC } objects.
MovieField is an enum of all slot names (stored + derived).

WHERE filters on derived slots compile the derivation subquery into the
WHERE clause.  ORDER BY on derived slots inlines the derivation expression
as the sort key.

Schema is cached by content_hash so it rebuilds only when the spec changes.

Centralisation rule: all SQL lives in db/. The resolver here delegates to
graph_store.query_rows / count_rows / aggregate_rows /
get_canonical_contributions and resolve.resolve_entity; ORDER BY is compiled
via sql_compiler.compile_order_by.

Traversal (relation joins) and projection are out of scope for this slice.
"""

from __future__ import annotations

import enum
import json
import logging
import sys
import types
from typing import (  # noqa: UP035 — Optional referenced by generated resolver code (line ~697)
    Any,
    Optional,
)

import strawberry
from psycopg import sql
from strawberry import Schema

from knot.spec.compile.postgres import CompileContext, compile_predicate, compile_value
from knot.spec.metaschema import (
    Array,
    ClassRef,
    Compare,
    CompareOp,
    Literal_,
    Matches,
    OntologyClass,
    Primitive,
    Slot,
    SlotPath,
    Spec,
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

_PRIMITIVE_TO_PYTHON: dict[str, type] = {
    "string": str,
    "integer": int,
    "float": float,
    "boolean": bool,
    "datetime": str,  # ISO-8601 string
    "date": str,  # ISO-8601 string
}


def _all_slots(oc: OntologyClass) -> list[Slot]:
    """Collect the full slot set for a class, walking is_a + mixins.

    Defined classes inherit all slots from their parent (is_a) structurally;
    every class also inherits its mixins' slots. Own slots shadow parent /
    mixin slots of the same name.
    """
    seen_names: set[str] = set()
    result: list[Slot] = []
    visited: list[OntologyClass] = []
    queue: list[OntologyClass] = [oc]
    while queue:
        current = queue.pop(0)
        if any(current is v for v in visited):
            continue
        visited.append(current)
        # DefinedClass has no own slots (the VIEW inherits parent columns);
        # the walk continues into is_a / mixins below.
        for slot in getattr(current, "slots", []):
            if slot.name not in seen_names:
                seen_names.add(slot.name)
                result.append(slot)
        if current.is_a is not None:
            queue.append(current.is_a)
        queue.extend(current.mixins)
    return result


def _type_expr_python(type_expr: Any) -> type:
    """Map a TypeExpression to a Python type for GraphQL schema generation.

    Returns a parameterised ``list[X]`` for ``Array`` so Strawberry's annotation
    resolver can extract the element type — bare ``list`` triggers a crash
    inside Strawberry's create_list (get_args(list) is empty).
    """
    if isinstance(type_expr, Primitive):
        return _PRIMITIVE_TO_PYTHON.get(type_expr.name, str)
    if isinstance(type_expr, Array):
        inner = _type_expr_python(type_expr.of)
        return list[inner]
    if isinstance(type_expr, ClassRef):
        return str  # canonical_id FK
    return str


def _slot_python_type(slot: Slot) -> type:
    if slot.type is None:
        return str
    return _type_expr_python(slot.type)


# ---------------------------------------------------------------------------
# WhereInput type construction
# ---------------------------------------------------------------------------


def _make_slot_where_type(slot: Slot, class_name: str) -> type:
    """Build a strawberry.input type for one slot's comparison ops."""
    py = _slot_python_type(slot)
    type_name = f"WhereInput_{class_name}_{slot.name}"

    annotations: dict[str, Any] = {
        "eq": py | None,
        "neq": py | None,
        "gt": py | None,
        "gte": py | None,
        "lt": py | None,
        "lte": py | None,
        "in_": list[py] | None,
        "not_in": list[py] | None,
        "is_null": bool | None,
        "is_not_null": bool | None,
        "like": str | None,
    }
    ns: dict[str, Any] = {k: strawberry.UNSET for k in annotations}
    cls = type(type_name, (), {"__annotations__": annotations, **ns})
    return strawberry.input(cls)


def _class_where_fields(
    oc: OntologyClass,
    *,
    where_types_by_name: dict[str, type],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compute ``(annotations, ns)`` for a class's top-level WhereInput.

    For scalar Primitive / Array / derived slots, emit the existing per-slot
    where-input (eq/neq/gt/in/like/...).

    For scalar ClassRef slots, replace the per-slot operator-bag with the
    *target class's* WhereInput — so filtering like
    ``credit(where: { movie: { title: { eq: "Inception" } } })`` works.
    The compiler turns this into a subquery filter (see
    ``_compile_nested_where_subquery``).
    """
    annotations: dict[str, Any] = {}
    ns: dict[str, Any] = {}
    for slot in _all_slots(oc):
        t = slot.type
        if isinstance(t, ClassRef) and t.target_class.name in where_types_by_name:
            target_where = where_types_by_name[t.target_class.name]
            annotations[slot.name] = Optional[target_where]
            ns[slot.name] = strawberry.UNSET
        else:
            slot_where = _make_slot_where_type(slot, oc.name)
            annotations[slot.name] = Optional[slot_where]
            ns[slot.name] = strawberry.UNSET
    return annotations, ns


def _make_class_where_type(
    oc: OntologyClass,
    *,
    where_types_by_name: dict[str, type] | None = None,
) -> type:
    """Build the top-level WhereInput for a class (one field per slot).

    Stored slots + derived slots both appear. For ClassRef slots, the field
    is typed as the target's WhereInput (nested filtering). Defined classes
    walk the is_a chain to collect inherited slots so the GraphQL surface
    matches actual columns.

    When ``where_types_by_name`` is omitted, ClassRef slots fall back to
    canonical-id scalar filtering (legacy callers).
    """
    where_types_by_name = where_types_by_name or {}
    type_name = f"WhereInput_{oc.name}"
    annotations, ns = _class_where_fields(oc, where_types_by_name=where_types_by_name)
    cls = type(type_name, (), {"__annotations__": annotations, **ns})
    return strawberry.input(cls)


# ---------------------------------------------------------------------------
# Per-class object type: one field per slot (+ canonical_id), all Optional
# ---------------------------------------------------------------------------


def _make_classref_resolver(
    target_type: type, target_oc: OntologyClass, fk_attr: str
):
    """Async field resolver for a scalar ClassRef property — forward edge.

    Reads ``self.<fk_attr>`` (the canonical_id of the related row, stored as
    a strawberry.Private field on the parent type), fetches all contributions
    for that canonical_id from the target class's table, merges them via
    alphabetical-source resolution, and returns a typed instance of the
    target's GraphQL object type.
    """
    async def resolver(root):
        cid = getattr(root, fk_attr, None)
        if cid is None:
            return None
        from knot import db
        from knot.db import graph_store

        async with db.connect() as conn:
            contribs = await graph_store.get_canonical_contributions(
                conn, cls=target_oc, canonical_id=cid
            )
        if not contribs:
            return None
        merged = _merge_contributions(contribs, target_oc)
        merged.setdefault("_canonical_id", cid)
        return _row_to_typed(target_type, target_oc, merged)

    resolver.__annotations__ = {"root": Any, "return": Optional[target_type]}
    resolver.__name__ = f"resolve_{target_oc.name.lower()}_for_{fk_attr}"
    return resolver


def _make_classref_array_resolver(
    target_type: type, target_oc: OntologyClass, fk_attr: str
):
    """Async resolver for a scalar Array-of-ClassRef property — forward edge.

    ``self.<fk_attr>`` is a list[str] of canonical_ids; fetch each and return
    a list of typed instances. Misses (no contributions for an id) are dropped
    rather than surfacing as None entries — the consumer cares about the
    relation, not about gaps.
    """
    async def resolver(root):
        cids = getattr(root, fk_attr, None) or []
        if not cids:
            return []
        from knot import db
        from knot.db import graph_store

        out: list[Any] = []
        async with db.connect() as conn:
            for cid in cids:
                contribs = await graph_store.get_canonical_contributions(
                    conn, cls=target_oc, canonical_id=cid
                )
                if not contribs:
                    continue
                merged = _merge_contributions(contribs, target_oc)
                merged.setdefault("_canonical_id", cid)
                out.append(_row_to_typed(target_type, target_oc, merged))
        return out

    resolver.__annotations__ = {"root": Any, "return": list[target_type]}
    resolver.__name__ = f"resolve_{target_oc.name.lower()}_for_{fk_attr}_array"
    return resolver


def _make_reverse_classref_resolver(
    source_type: type, source_oc: OntologyClass, fk_slot: str
):
    """Async resolver for a back-edge field on the target side of a
    scalar ClassRef.

    Given ``Credit.movie : ClassRef → Movie``, the resolver attached to
    ``Movie.credits`` (back-edge) fetches every Credit row whose ``movie``
    column equals ``self.canonical_id``. Uses ``graph_store.query_rows``
    with a single-column predicate.
    """
    async def resolver(root):
        cid = getattr(root, "canonical_id", None)
        if cid is None:
            cid = getattr(root, "_canonical_id", None)
        if cid is None:
            return []
        from knot import db
        from knot.db import graph_store
        from psycopg import sql as _sql

        pred = _sql.SQL("s.{col} = %s").format(col=_sql.Identifier(fk_slot))
        async with db.connect() as conn:
            rows = await graph_store.query_rows(
                conn,
                cls=source_oc,
                predicate_sql=pred,
                predicate_params=[cid],
            )
        return [_row_to_typed(source_type, source_oc, r) for r in rows]

    resolver.__annotations__ = {"root": Any, "return": list[source_type]}
    resolver.__name__ = f"resolve_{source_oc.name.lower()}_back_via_{fk_slot}"
    return resolver


def _compute_back_edges(
    concrete: list[OntologyClass],
) -> dict[str, list[tuple[OntologyClass, str]]]:
    """Map ``target_class_name → [(source_oc, source_slot_name), ...]``.

    A back-edge exists whenever some concrete class's scalar ClassRef slot
    points to a target class also in the concrete set. Array-of-ClassRef
    back-edges are deferred (SQL predicate would need ANY(); not in demo).
    """
    name_to_oc = {oc.name: oc for oc in concrete}
    out: dict[str, list[tuple[OntologyClass, str]]] = {n: [] for n in name_to_oc}
    for oc in concrete:
        for slot in _all_slots(oc):
            if isinstance(slot.type, ClassRef):
                tname = slot.type.target_class.name
                if tname in name_to_oc:
                    out[tname].append((oc, slot.name))
    return out


def _back_edge_field_name(source_oc: OntologyClass, slot_name: str, n_paths: int) -> str:
    """Naming convention for back-edge fields.

    When there's a single ClassRef path from a source class to the target,
    use ``<srcLower>s`` (lowercase plural). When there are multiple paths
    from the same source (rare — e.g., a Movie with both ``director`` and
    ``producer`` ClassRefs to Person), disambiguate with the slot:
    ``<srcLower>s_via_<slot>``.
    """
    base = f"{source_oc.name.lower()}s"
    if n_paths > 1:
        return f"{base}_via_{slot_name}"
    return base


def _make_class_object_type(
    oc: OntologyClass,
    *,
    raw_classes: dict[str, type],
    back_edges: dict[str, list[tuple[OntologyClass, str]]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compute ``(annotations, ns)`` for a class's strawberry type.

    The actual class object is constructed (or patched) by the caller —
    this function only computes the field layout, so back-edges can
    reference sibling raw classes that may not yet be strawberry-decorated.
    """
    annotations: dict[str, Any] = {}
    ns: dict[str, Any] = {}

    # Forward edges from own + inherited slots.
    for slot in _all_slots(oc):
        t = slot.type
        # Scalar ClassRef → resolver-backed nested type + private fk_id.
        if isinstance(t, ClassRef) and t.target_class.name in raw_classes:
            target_type = raw_classes[t.target_class.name]
            target_oc = t.target_class
            fk_attr = f"_{slot.name}_id"
            annotations[fk_attr] = strawberry.Private[Optional[str]]
            ns[fk_attr] = None
            annotations[slot.name] = Optional[target_type]
            ns[slot.name] = strawberry.field(
                resolver=_make_classref_resolver(target_type, target_oc, fk_attr)
            )
        # Array of ClassRef → resolver-backed list of nested + private fk_ids.
        elif (
            isinstance(t, Array)
            and isinstance(t.of, ClassRef)
            and t.of.target_class.name in raw_classes
        ):
            target_type = raw_classes[t.of.target_class.name]
            target_oc = t.of.target_class
            fk_attr = f"_{slot.name}_ids"
            annotations[fk_attr] = strawberry.Private[Optional[list[str]]]
            ns[fk_attr] = None
            annotations[slot.name] = list[target_type]
            ns[slot.name] = strawberry.field(
                resolver=_make_classref_array_resolver(target_type, target_oc, fk_attr)
            )
        else:
            py = _slot_python_type(slot)
            annotations[slot.name] = py | None
            ns[slot.name] = None

    # Back-edge fields: for every (source, slot) where source.slot → this class.
    # Group by source class so we can disambiguate when one source has
    # multiple ClassRef paths to the same target.
    own_back = back_edges.get(oc.name, [])
    by_source: dict[str, list[str]] = {}
    for src_oc, slot_name in own_back:
        by_source.setdefault(src_oc.name, []).append(slot_name)
    for src_oc, slot_name in own_back:
        src_type = raw_classes[src_oc.name]
        n_paths = len(by_source[src_oc.name])
        field_name = _back_edge_field_name(src_oc, slot_name, n_paths)
        # Don't shadow a forward-edge field of the same name.
        if field_name in annotations:
            field_name = f"{field_name}_via_{slot_name}"
        annotations[field_name] = list[src_type]
        ns[field_name] = strawberry.field(
            resolver=_make_reverse_classref_resolver(src_type, src_oc, slot_name)
        )

    # System canonical_id (unless shadowed by a slot of the same name).
    if "canonical_id" not in annotations:
        annotations["canonical_id"] = str | None
        ns["canonical_id"] = None

    return annotations, ns


def _row_to_typed(class_type: type, oc: OntologyClass, row: Any) -> Any:
    """Construct an instance of ``class_type`` from a graph_store row dict.

    Returns None if the input is None. The graph_store dict uses
    ``_canonical_id`` for the system-attribution canonical id; we surface
    that as ``canonical_id`` on the GraphQL type unless the class shadows
    it with its own slot.

    For scalar ClassRef slots, the row carries the related entity's
    canonical_id; route into the private ``_<slot>_id`` field so the
    public slot's resolver can compute the nested object on access.
    Array-of-ClassRef is similar via ``_<slot>_ids``.
    """
    if row is None:
        return None
    own_slot_names = {s.name for s in _all_slots(oc)}
    kwargs: dict[str, Any] = {}
    for s in _all_slots(oc):
        t = s.type
        if isinstance(t, ClassRef):
            kwargs[f"_{s.name}_id"] = row.get(s.name)
        elif isinstance(t, Array) and isinstance(t.of, ClassRef):
            kwargs[f"_{s.name}_ids"] = row.get(s.name)
        else:
            kwargs[s.name] = row.get(s.name)
    if "canonical_id" not in own_slot_names:
        kwargs["canonical_id"] = row.get("_canonical_id") or row.get("canonical_id")
    return class_type(**kwargs)


# ---------------------------------------------------------------------------
# OrderBy input: { field: <ClassField>, direction: ASC | DESC }
# ---------------------------------------------------------------------------


@strawberry.enum
class OrderDirection(enum.Enum):
    ASC = "ASC"
    DESC = "DESC"


def _slot_camel(name: str) -> str:
    """snake_case slot name → camelCase enum member, matching Strawberry's
    auto_camel_case for fields. ``credit_count`` → ``creditCount``."""
    head, *tail = name.split("_")
    return head + "".join(part.title() for part in tail)


def _make_field_enum(oc: OntologyClass) -> type:
    """Build a strawberry enum of slot names for a class (stored + derived).

    For defined classes, walks the is_a chain so all inherited slots appear.
    Derived slots are included so ORDER BY can sort on computed expressions.

    Member names are camelCase (matching the GraphQL field convention) while
    enum *values* are the underlying snake_case slot names — so callers reading
    ``item.field.value`` get the real slot name back.

    Also emits relation-path entries for each scalar ClassRef slot so callers
    can sort by a related class's primitive property. E.g. on Credit (with
    ``movie: ClassRef → Movie``) the enum carries ``MOVIE_YEAR`` →
    ``movie.year``, ``MOVIE_TITLE`` → ``movie.title``, etc. The compiler
    turns dotted values into correlated subqueries (see ``_build_order_by_sql``).
    """
    enum_name = f"Field_{oc.name}"
    members: dict[str, str] = {}
    for s in _all_slots(oc):
        members[_slot_camel(s.name)] = s.name
        # Relation paths: only one hop, only primitive target slots (no
        # nested ClassRef / Array sorting through relations).
        if isinstance(s.type, ClassRef):
            target_oc = s.type.target_class
            for ts in _all_slots(target_oc):
                if isinstance(ts.type, (ClassRef, Array)):
                    continue
                key = f"{_slot_camel(s.name)}_{_slot_camel(ts.name)}"
                members[key] = f"{s.name}.{ts.name}"
    py_enum = enum.Enum(enum_name, members)  # type: ignore[misc]
    return strawberry.enum(py_enum)


def _make_order_by_input(oc: OntologyClass, field_enum: type) -> type:
    """Build the OrderBy input type for a class."""
    type_name = f"OrderBy_{oc.name}"
    annotations: dict[str, Any] = {
        "field": field_enum,
        "direction": OrderDirection,
    }
    ns: dict[str, Any] = {
        "field": strawberry.UNSET,
        "direction": OrderDirection.ASC,
    }
    cls = type(type_name, (), {"__annotations__": annotations, **ns})
    return strawberry.input(cls)


# ---------------------------------------------------------------------------
# WhereInput → expression tree → (sql_fragment, params)
# ---------------------------------------------------------------------------

_OP_MAP: dict[str, CompareOp] = {
    "eq": CompareOp.EQ,
    "neq": CompareOp.NEQ,
    "gt": CompareOp.GT,
    "gte": CompareOp.GTE,
    "lt": CompareOp.LT,
    "lte": CompareOp.LTE,
    "in_": CompareOp.IN,
    "not_in": CompareOp.NOT_IN,
    "is_null": CompareOp.IS_NULL,
    "is_not_null": CompareOp.IS_NOT_NULL,
}

_UNARY_OPS = {CompareOp.IS_NULL, CompareOp.IS_NOT_NULL}


def _slot_where_to_predicates(
    slot: Slot,
    slot_where: Any,
    oc: OntologyClass,
) -> list[Any]:
    """Convert a per-slot WhereInput to a list of expression-tree nodes.

    Only used for stored slots (where the slot is a real column).  For derived
    slots see ``_derived_slot_where_to_sql``.
    """
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
            predicates.append(Compare(op=op, left=path, right=Literal_(value=list(val))))
        else:
            predicates.append(Compare(op=op, left=path, right=Literal_(value=val)))

    like_val = getattr(slot_where, "like", strawberry.UNSET)
    if like_val is not strawberry.UNSET and like_val is not None:
        predicates.append(Matches(left=path, pattern=like_val))

    return predicates


def _derived_slot_where_to_sql(
    slot: Slot,
    slot_where: Any,
    storage_class: OntologyClass,
    alias: str,
    ctx: CompileContext,
) -> list[sql.Composable]:
    """Compile WHERE fragments for a derived slot by inlining its derivation.

    The derivation expression is compiled to a correlated subquery (or scalar
    expression), then wrapped with the requested comparison operator.

    Returns a list of ``sql.Composable`` fragments (one per active operator in
    ``slot_where``).  The ctx.params list is mutated in place as each operator
    is processed.
    """
    derivation = slot.derivation
    # Compile the derivation expression — params for it go into ctx first.
    deriv_ctx = CompileContext(primary_class=storage_class, alias=alias, params=ctx.params)
    deriv_sql = compile_value(derivation, deriv_ctx)
    # deriv_ctx.params is the same list as ctx.params (shared reference).

    fragments: list[sql.Composable] = []

    _BINARY_OP_SQL_LOCAL: dict[CompareOp, str] = {
        CompareOp.EQ: "=",
        CompareOp.NEQ: "<>",
        CompareOp.GT: ">",
        CompareOp.GTE: ">=",
        CompareOp.LT: "<",
        CompareOp.LTE: "<=",
    }

    for field_name, op in _OP_MAP.items():
        val = getattr(slot_where, field_name, strawberry.UNSET)
        if val is strawberry.UNSET or val is None:
            continue

        if op in _UNARY_OPS:
            if val:
                op_str = "IS NULL" if op == CompareOp.IS_NULL else "IS NOT NULL"
                fragments.append(
                    sql.SQL("({deriv}) {op}").format(
                        deriv=deriv_sql,
                        op=sql.SQL(op_str),
                    )
                )
        elif op == CompareOp.IN:
            ctx.params.append(list(val))
            fragments.append(
                sql.SQL("({deriv}) = ANY(").format(deriv=deriv_sql)
                + sql.Placeholder()
                + sql.SQL(")")
            )
        elif op == CompareOp.NOT_IN:
            ctx.params.append(list(val))
            fragments.append(
                sql.SQL("({deriv}) != ALL(").format(deriv=deriv_sql)
                + sql.Placeholder()
                + sql.SQL(")")
            )
        elif op in _BINARY_OP_SQL_LOCAL:
            ctx.params.append(val)
            fragments.append(
                sql.SQL("({deriv}) {op} ").format(
                    deriv=deriv_sql,
                    op=sql.SQL(_BINARY_OP_SQL_LOCAL[op]),
                )
                + sql.Placeholder()
            )

    like_val = getattr(slot_where, "like", strawberry.UNSET)
    if like_val is not strawberry.UNSET and like_val is not None:
        ctx.params.append(like_val)
        fragments.append(sql.SQL("({deriv}) LIKE ").format(deriv=deriv_sql) + sql.Placeholder())

    return fragments


def _compile_nested_where_subquery(
    target_oc: OntologyClass,
    nested_where: Any,
    alias_suffix: str,
) -> tuple[sql.Composable, list[Any]]:
    """Build ``SELECT canonical_id FROM <target> JOIN <bindings> WHERE <inner>``
    for the inside of a ClassRef nested-where filter.

    ``alias_suffix`` disambiguates aliases when multiple ClassRef filters
    nest at the same level (e.g., ``where: { movie: {...}, person: {...} }``).
    Uses ``s_<suffix>`` / ``b_<suffix>`` so generated SQL doesn't collide
    with the outer query's aliases (which are typically ``s`` / ``b``).
    """
    from knot.spec.compile.postgres._naming import (
        bindings_table_id as _bind_id,
    )
    from knot.spec.compile.postgres._naming import (
        table_id as _tbl_id,
    )

    # Defined classes are backed by a VIEW with _canonical_id in-row; the
    # JOIN to a _bindings table doesn't apply. Filter through the parent.
    storage_class = target_oc
    if getattr(target_oc, "definition", None) is not None and target_oc.is_a is not None:
        storage_class = target_oc.is_a

    inner_s = f"s_{alias_suffix}"
    inner_b = f"b_{alias_suffix}"
    inner_pred, inner_params = build_predicate_sql(target_oc, nested_where, alias=inner_s)
    src = _tbl_id(storage_class)
    bind = _bind_id(storage_class)

    if inner_pred is not None:
        subq = sql.SQL(
            "SELECT {bind_id}.canonical_id FROM {src} {s_a} "
            "JOIN {bind} {b_a} "
            "ON {bind_id}.knot_row_id = {src_id}._knot_row_id "
            "AND {bind_id}.valid_to IS NULL "
            "WHERE {pred}"
        ).format(
            src=src,
            bind=bind,
            s_a=sql.Identifier(inner_s),
            b_a=sql.Identifier(inner_b),
            src_id=sql.Identifier(inner_s),
            bind_id=sql.Identifier(inner_b),
            pred=inner_pred,
        )
    else:
        # No inner predicate → match any current binding (rare; just
        # filters parents that have a live target row at all).
        subq = sql.SQL(
            "SELECT {bind_id}.canonical_id FROM {bind} {b_a} "
            "WHERE {bind_id}.valid_to IS NULL"
        ).format(
            bind=bind,
            b_a=sql.Identifier(inner_b),
            bind_id=sql.Identifier(inner_b),
        )
    return subq, inner_params


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

    Stored slots are compiled via the expression-tree path.
    Derived slots inline their derivation expression as a subquery in WHERE.
    """
    if where_input is strawberry.UNSET or where_input is None:
        return None, []

    # For defined classes, compile predicates against the parent class so that
    # slot-identity validation in _compile_slot_path finds the right columns.
    # The VIEW exposes the parent's columns directly.
    storage_class = oc
    if getattr(oc, "definition", None) is not None and oc.is_a is not None:
        storage_class = oc.is_a

    ctx = CompileContext(primary_class=storage_class, alias=alias)
    all_fragments: list[sql.Composable] = []

    for slot in _all_slots(oc):
        slot_where = getattr(where_input, slot.name, strawberry.UNSET)
        if slot_where is strawberry.UNSET or slot_where is None:
            continue

        # ClassRef slot: nested-where is the target's WhereInput; compile a
        # subquery filter on the foreign-key column.
        if isinstance(slot.type, ClassRef):
            subq, sub_params = _compile_nested_where_subquery(
                slot.type.target_class, slot_where, slot.name
            )
            fragment = (
                sql.SQL("{a}.{col} IN (").format(
                    a=sql.Identifier(alias),
                    col=sql.Identifier(slot.name),
                )
                + subq
                + sql.SQL(")")
            )
            ctx.params.extend(sub_params)
            all_fragments.append(fragment)
            continue

        if getattr(slot, "derivation", None) is None:
            # Stored slot: use the expression-tree path.
            # Resolve the slot against the storage class for slot-identity check.
            storage_slot = slot
            if storage_class is not oc:
                storage_slot = next(
                    (s for s in _all_slots(storage_class) if s.name == slot.name),
                    slot,
                )
            tree_predicates = _slot_where_to_predicates(storage_slot, slot_where, storage_class)
            if tree_predicates:
                for pred in tree_predicates:
                    all_fragments.append(compile_predicate(pred, ctx))
        else:
            # Derived slot: inline the derivation expression as a subquery.
            frags = _derived_slot_where_to_sql(slot, slot_where, storage_class, alias, ctx)
            all_fragments.extend(frags)

    if not all_fragments:
        return None, []

    if len(all_fragments) == 1:
        fragment = all_fragments[0]
    else:
        fragment = sql.SQL(" AND ").join(sql.SQL("(") + f + sql.SQL(")") for f in all_fragments)

    return fragment, ctx.params


def _build_order_by_sql(
    order_by_list: Any,
    oc: OntologyClass,
    alias: str = "s",
) -> tuple[sql.Composable | None, list[Any]]:
    """Convert a list of OrderBy input objects to a (sql.Composable, params) pair.

    Returns (None, []) when the list is empty/unset (caller uses default sort).

    For stored slots, emits ``s.<col> ASC|DESC``.
    For derived slots, inlines the derivation expression as the sort key —
    the derivation subquery is compiled and used directly in ORDER BY.
    """
    if order_by_list is strawberry.UNSET or not order_by_list:
        return None, []

    # Build a slot-name → Slot map for quick lookup.
    slot_by_name: dict[str, Slot] = {s.name: s for s in _all_slots(oc)}

    # For defined classes, the storage class is the parent.
    storage_class = oc
    if getattr(oc, "definition", None) is not None and oc.is_a is not None:
        storage_class = oc.is_a

    stored_terms: list[tuple[str, str]] = []
    derived_parts: list[sql.Composable] = []
    derived_params: list[Any] = []

    for item in order_by_list:
        field_name = item.field.value
        direction = item.direction.value
        slot = slot_by_name.get(field_name)
        if slot is None or getattr(slot, "derivation", None) is None:
            # Stored slot (or unknown) — handled by compile_order_by.
            stored_terms.append((field_name, direction))
        else:
            # Derived slot — inline derivation expression.
            dir_upper = direction.upper()
            ctx = CompileContext(primary_class=storage_class, alias=alias, params=derived_params)
            deriv_sql = compile_value(slot.derivation, ctx)
            derived_parts.append(
                sql.SQL("({expr}) {dir}").format(
                    expr=deriv_sql,
                    dir=sql.SQL(dir_upper),
                )
            )

    # Compose stored + derived terms in the order they appear in the input list.
    # We rebuild in input order to preserve user-specified sort priority.
    all_parts: list[sql.Composable] = []
    all_params: list[Any] = list(derived_params)

    # Re-iterate to emit in input order.
    slot_by_name2: dict[str, Slot] = {s.name: s for s in _all_slots(oc)}
    CompileContext(primary_class=storage_class, alias=alias, params=[])
    for idx, item in enumerate(order_by_list):
        field_name = item.field.value
        direction = item.direction.value.upper()

        # Relation-path sort: enum value like "movie.year" → compile a
        # correlated subquery that returns the target column.
        if "." in field_name:
            fk_slot_name, target_slot_name = field_name.split(".", 1)
            fk_slot = slot_by_name2.get(fk_slot_name)
            if fk_slot is None or not isinstance(fk_slot.type, ClassRef):
                # Unknown / malformed path — skip silently to avoid an
                # internal-server-error from a stale enum value.
                continue
            target_oc = fk_slot.type.target_class
            storage_target = target_oc
            if (
                getattr(target_oc, "definition", None) is not None
                and target_oc.is_a is not None
            ):
                storage_target = target_oc.is_a
            from knot.spec.compile.postgres._naming import (
                bindings_table_id as _bind_id,
            )
            from knot.spec.compile.postgres._naming import (
                table_id as _tbl_id,
            )

            inner_s = f"os{idx}"
            inner_b = f"ob{idx}"
            subq = sql.SQL(
                "(SELECT {s}.{col} FROM {src} {s_a} JOIN {bind} {b_a} "
                "ON {b}.knot_row_id = {s}._knot_row_id "
                "AND {b}.valid_to IS NULL "
                "AND {b}.canonical_id = {outer}.{fk} "
                "LIMIT 1) {dir}"
            ).format(
                src=_tbl_id(storage_target),
                bind=_bind_id(storage_target),
                s_a=sql.Identifier(inner_s),
                b_a=sql.Identifier(inner_b),
                s=sql.Identifier(inner_s),
                b=sql.Identifier(inner_b),
                col=sql.Identifier(target_slot_name),
                outer=sql.Identifier(alias),
                fk=sql.Identifier(fk_slot_name),
                dir=sql.SQL(direction),
            )
            all_parts.append(subq)
            continue

        slot = slot_by_name2.get(field_name)
        if slot is not None and getattr(slot, "derivation", None) is not None:
            # Derived: compile fresh to get params in order.
            item_params: list[Any] = []
            ctx_item = CompileContext(primary_class=storage_class, alias=alias, params=item_params)
            deriv_sql = compile_value(slot.derivation, ctx_item)
            all_params.extend(item_params)
            all_parts.append(
                sql.SQL("({expr}) {dir}").format(
                    expr=deriv_sql,
                    dir=sql.SQL(direction),
                )
            )
        else:
            all_parts.append(
                sql.SQL("{alias}.{col} {dir}").format(
                    alias=sql.Identifier(alias),
                    col=sql.Identifier(field_name),
                    dir=sql.SQL(direction),
                )
            )

    if not all_parts:
        return None, []

    return sql.SQL(", ").join(all_parts), all_params


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
        if isinstance(slot.type, Array):
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
# Aggregate result type per class
# ---------------------------------------------------------------------------


def _make_aggregate_result_type(oc: OntologyClass) -> tuple[type, list[tuple[str, str, str]]]:
    """Build the AggregateResult strawberry type for a class.

    Always includes ``count: Int!``.  For each stored slot whose Postgres type
    is numeric (BIGINT / DOUBLE PRECISION), adds sum/avg/min/max fields.

    Returns ``(strawberry_type, agg_fields)`` where ``agg_fields`` is the list
    of ``(agg_func, slot_name, result_key)`` triples passed to
    ``graph_store.aggregate_rows``.
    """
    from knot.spec.compile.postgres._types import slot_pg_type

    type_name = f"AggregateResult_{oc.name}"

    _NUMERIC_PG_TYPES = {"BIGINT", "INTEGER", "DOUBLE PRECISION"}

    agg_fields: list[tuple[str, str, str]] = []
    annotations: dict[str, Any] = {"count": int}
    ns: dict[str, Any] = {"count": 0}

    for slot in _all_slots(oc):
        if getattr(slot, "derivation", None) is not None:
            continue  # derived slots have no column to aggregate
        pg_type = slot_pg_type(slot).upper()
        if pg_type not in _NUMERIC_PG_TYPES:
            continue
        for func in ("sum", "avg", "min", "max"):
            result_key = f"{func}_{slot.name}".replace("-", "_")
            # Title-case the func for the GraphQL field name: sumYear, avgYear…
            gql_key = f"{func}{slot.name.capitalize()}"
            agg_fields.append((func, slot.name, result_key))
            annotations[gql_key] = float | None
            ns[gql_key] = None

    cls = type(type_name, (), {"__annotations__": annotations, **ns})
    return strawberry.type(cls), agg_fields


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
      <class_lower>Aggregate(where, asOf)                -> AggregateResult_<Class>
    """
    from knot import db
    from knot.db import graph_store
    from knot.graph import resolve as _resolve_mod

    # Queryable classes: concrete OntologyClass + every DefinedClass (VIEW-backed).
    # Abstract OntologyClass instances have no table → not queryable, excluded.
    concrete_classes = [c for c in spec.classes if not getattr(c, "abstract", False)]

    # Build per-class types.
    where_types: dict[str, type] = {}
    class_object_types: dict[str, type] = {}
    field_enums: dict[str, type] = {}
    order_by_inputs: dict[str, type] = {}
    agg_result_types: dict[str, type] = {}
    agg_fields_map: dict[str, list[tuple[str, str, str]]] = {}
    # Stub-then-patch construction so back-edges can reference sibling
    # classes that haven't been decorated yet. Every class is created as
    # an empty Python class first, registered in the lookup, then patched
    # with annotations + ns (which include resolvers that capture the
    # sibling stubs). Strawberry decoration happens last, in any order —
    # the captured references are the same Python objects that get
    # decorated in-place.
    raw_classes: dict[str, type] = {}
    for oc in concrete_classes:
        raw_classes[oc.name] = type(f"Type_{oc.name}", (), {})

    back_edges = _compute_back_edges(concrete_classes)

    for oc in concrete_classes:
        cls = raw_classes[oc.name]
        annotations, ns = _make_class_object_type(
            oc, raw_classes=raw_classes, back_edges=back_edges
        )
        cls.__annotations__ = annotations
        for k, v in ns.items():
            setattr(cls, k, v)

    for oc in concrete_classes:
        class_object_types[oc.name] = strawberry.type(raw_classes[oc.name])

    # WhereInputs use the same stub-then-patch pattern as object types so
    # ClassRef slots can reference the target's WhereInput (enabling
    # nested filtering like ``credit(where: { movie: { title: ... } })``).
    raw_where_types: dict[str, type] = {}
    for oc in concrete_classes:
        raw_where_types[oc.name] = type(f"WhereInput_{oc.name}", (), {})

    for oc in concrete_classes:
        cls = raw_where_types[oc.name]
        annotations, ns = _class_where_fields(oc, where_types_by_name=raw_where_types)
        cls.__annotations__ = annotations
        for k, v in ns.items():
            setattr(cls, k, v)

    for oc in concrete_classes:
        where_types[oc.name] = strawberry.input(raw_where_types[oc.name])

    # All remaining per-class types are independent of each other.
    for oc in concrete_classes:
        field_enums[oc.name] = _make_field_enum(oc)
        order_by_inputs[oc.name] = _make_order_by_input(oc, field_enums[oc.name])
        agg_result_types[oc.name], agg_fields_map[oc.name] = _make_aggregate_result_type(oc)

    # Build a synthetic module for Strawberry's type resolution.
    mod_name = f"knot.spec.compile.graphql._dynamic_{id(spec)}"
    mod = types.ModuleType(mod_name)
    mod.__dict__["Optional"] = Optional
    mod.__dict__["int"] = int
    mod.__dict__["str"] = str
    mod.__dict__["list"] = list
    for t in where_types.values():
        mod.__dict__[t.__name__] = t
    for t in class_object_types.values():
        mod.__dict__[t.__name__] = t
    for t in field_enums.values():
        mod.__dict__[t.__name__] = t
    for t in order_by_inputs.values():
        mod.__dict__[t.__name__] = t
    for t in agg_result_types.values():
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
    mod.__dict__["_row_to_typed"] = _row_to_typed
    mod.__dict__["_UNSET"] = strawberry.UNSET

    sys.modules[mod_name] = mod

    query_fields: dict[str, Any] = {}

    for oc in concrete_classes:
        wtype = where_types[oc.name]
        ob_input = order_by_inputs[oc.name]
        agg_rtype = agg_result_types[oc.name]
        wtype_name = wtype.__name__
        ob_name = ob_input.__name__
        agg_rtype_name = agg_rtype.__name__
        bound_oc = oc

        # Unique per-class bindings in the module dict.
        oc_key = f"_oc_{oc.name}"
        ctype_key = f"_ctype_{oc.name}"
        agg_rtype_key = f"_agg_rtype_{oc.name}"
        agg_fields_key = f"_agg_fields_{oc.name}"
        mod.__dict__[oc_key] = bound_oc
        mod.__dict__[ctype_key] = class_object_types[oc.name]
        mod.__dict__[agg_rtype_key] = agg_rtype
        mod.__dict__[agg_fields_key] = agg_fields_map[oc.name]

        cls_lower = oc.name.lower()

        # ── List resolver (returns the list directly — no Page wrapper) ──
        ctype_name = class_object_types[oc.name].__name__
        list_fn_name = f"resolve_{cls_lower}"
        list_fn_src = (
            f"async def {list_fn_name}(\n"
            f"    where: {wtype_name} | None = _UNSET,\n"
            f"    limit: int = 100,\n"
            f"    offset: int = 0,\n"
            f"    as_of: int | None = None,\n"
            f"    order_by: Optional[list[{ob_name}]] = _UNSET,\n"
            f") -> list[{ctype_name}]:\n"
            f"    pred_sql, pred_params = _build_pred({oc_key}, where)\n"
            f"    ob_sql, ob_params = _build_order_by(order_by, {oc_key})\n"
            f"    async with _db.connect() as conn:\n"
            f"        rows = await _gs.query_rows(\n"
            f"            conn, cls={oc_key},\n"
            f"            predicate_sql=pred_sql,\n"
            f"            predicate_params=pred_params,\n"
            f"            limit=limit, offset=offset, as_of=as_of,\n"
            f"            order_by_sql=ob_sql,\n"
            f"            order_by_params=ob_params,\n"
            f"        )\n"
            f"    return [_row_to_typed({ctype_key}, {oc_key}, r) for r in rows]\n"
        )

        # ── Count resolver: <class>Count(where, asOf) → int ──────────────
        count_fn_name = f"resolve_{cls_lower}_count"
        count_fn_src = (
            f"async def {count_fn_name}(\n"
            f"    where: {wtype_name} | None = _UNSET,\n"
            f"    as_of: int | None = None,\n"
            f") -> int:\n"
            f"    pred_sql, pred_params = _build_pred({oc_key}, where)\n"
            f"    async with _db.connect() as conn:\n"
            f"        return await _gs.count_rows(\n"
            f"            conn, cls={oc_key}, as_of=as_of,\n"
            f"            predicate_sql=pred_sql, predicate_params=pred_params,\n"
            f"        )\n"
        )

        is_polymorphic = getattr(bound_oc, "identifier_pattern", None) is not None

        if is_polymorphic:
            # Polymorphic class: expose byDiscriminator(targetClass, key) instead of
            # byCanonicalId.  Lookup filters by class_slot == targetClass AND key_slot == key.
            # byCanonicalId, Resolved, and Aggregate are omitted — canonical_id is not
            # well-defined for polymorphic classes (each row references a different target).
            ip = bound_oc.identifier_pattern
            # Capture actual slot name strings now (at schema-build time) so
            # the generated resolver source embeds literal names, not key names.
            _disc_class_slot_name = ip.class_slot.name
            _disc_key_slot_name = ip.key_slot.name

            by_disc_fn_name = f"resolve_{cls_lower}_by_discriminator"
            ctype_name = class_object_types[oc.name].__name__
            by_disc_fn_src = (
                f"async def {by_disc_fn_name}(\n"
                f"    target_class: str,\n"
                f"    key: str,\n"
                f"    as_of: int | None = None,\n"
                f") -> {ctype_name} | None:\n"
                f"    from knot.spec.compile.postgres import CompileContext, compile_predicate\n"
                f"    from knot.spec.metaschema import BoolExpr, BoolOpKind, Compare, CompareOp, Literal_, SlotPath\n"
                f"    oc = {oc_key}\n"
                f"    class_slot = next(s for s in oc.slots if s.name == {_disc_class_slot_name!r})\n"
                f"    key_slot = next(s for s in oc.slots if s.name == {_disc_key_slot_name!r})\n"
                f"    pred_class = Compare(\n"
                f"        op=CompareOp.EQ,\n"
                f"        left=SlotPath(from_class=oc, slots=[class_slot]),\n"
                f"        right=Literal_(value=target_class),\n"
                f"    )\n"
                f"    pred_key = Compare(\n"
                f"        op=CompareOp.EQ,\n"
                f"        left=SlotPath(from_class=oc, slots=[key_slot]),\n"
                f"        right=Literal_(value=key),\n"
                f"    )\n"
                f"    combined = BoolExpr(op=BoolOpKind.AND, operands=[pred_class, pred_key])\n"
                f"    cctx = CompileContext(primary_class=oc, alias='s')\n"
                f"    pred_sql = compile_predicate(combined, cctx)\n"
                f"    async with _db.connect() as conn:\n"
                f"        rows = await _gs.query_rows(\n"
                f"            conn, cls=oc,\n"
                f"            predicate_sql=pred_sql,\n"
                f"            predicate_params=cctx.params,\n"
                f"            limit=1, offset=0, as_of=as_of,\n"
                f"        )\n"
                f"    if not rows:\n"
                f"        return None\n"
                f"    return _row_to_typed({ctype_key}, {oc_key}, rows[0])\n"
            )

            for fn_src, fn_name in [
                (list_fn_src, list_fn_name),
                (count_fn_src, count_fn_name),
                (by_disc_fn_src, by_disc_fn_name),
            ]:
                exec(fn_src, mod.__dict__)  # noqa: S102
                fn = mod.__dict__[fn_name]
                fn.__module__ = mod_name

            query_fields[cls_lower] = strawberry.field(resolver=mod.__dict__[list_fn_name])
            query_fields[f"{cls_lower}Count"] = strawberry.field(
                resolver=mod.__dict__[count_fn_name]
            )
            query_fields[f"{cls_lower}ByDiscriminator"] = strawberry.field(
                resolver=mod.__dict__[by_disc_fn_name]
            )

        else:
            ctype_name = class_object_types[oc.name].__name__

            # ── Single-entity (contributions) resolver ─────────────────────
            by_id_fn_name = f"resolve_{cls_lower}_by_canonical_id"
            by_id_fn_src = (
                f"async def {by_id_fn_name}(\n"
                f"    canonical_id: str,\n"
                f"    as_of: int | None = None,\n"
                f") -> {ctype_name} | None:\n"
                f"    async with _db.connect() as conn:\n"
                f"        contribs = await _gs.get_canonical_contributions(\n"
                f"            conn, cls={oc_key},\n"
                f"            canonical_id=canonical_id, as_of=as_of,\n"
                f"        )\n"
                f"    if not contribs:\n"
                f"        return None\n"
                f"    merged = _merge_contribs(contribs, {oc_key})\n"
                f"    merged.setdefault('_canonical_id', canonical_id)\n"
                f"    return _row_to_typed({ctype_key}, {oc_key}, merged)\n"
            )

            # ── Resolved view resolver ─────────────────────────────────────
            resolved_fn_name = f"resolve_{cls_lower}_resolved"
            resolved_fn_src = (
                f"async def {resolved_fn_name}(\n"
                f"    canonical_id: str,\n"
                f"    as_of: int | None = None,\n"
                f") -> {ctype_name} | None:\n"
                f"    async with _db.connect() as conn:\n"
                f"        record = await _resolve_mod.resolve_entity(\n"
                f"            conn, cls={oc_key},\n"
                f"            canonical_id=canonical_id, as_of=as_of,\n"
                f"        )\n"
                f"    if record is None:\n"
                f"        return None\n"
                f"    record.setdefault('_canonical_id', canonical_id)\n"
                f"    return _row_to_typed({ctype_key}, {oc_key}, record)\n"
            )

            # ── Aggregate resolver ─────────────────────────────────────────
            agg_fn_name = f"resolve_{cls_lower}_aggregate"
            agg_fn_src = (
                f"async def {agg_fn_name}(\n"
                f"    where: {wtype_name} | None = _UNSET,\n"
                f"    as_of: int | None = None,\n"
                f") -> {agg_rtype_name}:\n"
                f"    pred_sql, pred_params = _build_pred({oc_key}, where)\n"
                f"    async with _db.connect() as conn:\n"
                f"        result = await _gs.aggregate_rows(\n"
                f"            conn, cls={oc_key},\n"
                f"            as_of=as_of,\n"
                f"            predicate_sql=pred_sql,\n"
                f"            predicate_params=pred_params,\n"
                f"            agg_fields={agg_fields_key},\n"
                f"        )\n"
                f"    kwargs = {{'count': result['count']}}\n"
                f"    for _func, _slot, _key in {agg_fields_key}:\n"
                f"        gql_key = _func + _slot.capitalize()\n"
                f"        val = result.get(_key)\n"
                f"        kwargs[gql_key] = float(val) if val is not None else None\n"
                f"    return {agg_rtype_key}(**kwargs)\n"
            )

            for fn_src, fn_name in [
                (list_fn_src, list_fn_name),
                (count_fn_src, count_fn_name),
                (by_id_fn_src, by_id_fn_name),
                (resolved_fn_src, resolved_fn_name),
                (agg_fn_src, agg_fn_name),
            ]:
                exec(fn_src, mod.__dict__)  # noqa: S102
                fn = mod.__dict__[fn_name]
                fn.__module__ = mod_name

            query_fields[cls_lower] = strawberry.field(resolver=mod.__dict__[list_fn_name])
            query_fields[f"{cls_lower}Count"] = strawberry.field(
                resolver=mod.__dict__[count_fn_name]
            )
            query_fields[f"{cls_lower}ByCanonicalId"] = strawberry.field(
                resolver=mod.__dict__[by_id_fn_name]
            )
            query_fields[f"{cls_lower}Resolved"] = strawberry.field(
                resolver=mod.__dict__[resolved_fn_name]
            )
            query_fields[f"{cls_lower}Aggregate"] = strawberry.field(
                resolver=mod.__dict__[agg_fn_name]
            )

    Query = strawberry.type(type("Query", (), query_fields))
    return strawberry.Schema(query=Query)
