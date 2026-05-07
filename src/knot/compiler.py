"""Knot compiler — Spec + bound impls + configs + watermarks → WorkflowSpec.

Implements:
- commitment 1 (knot is a compiler): reads spec, emits WorkflowSpec, never executes.
- commitment 3 (compile hash is run identity): canonical_dump + sha256 of the WorkflowSpec.
- commitment 8 (cross-class pinning): relation-class stages pin parent run hashes.
- per-stage cache key emission per staging/incremental-execution.md.

Per staging/datacontext-config-binding.md: ConfigRef nodes in DataContext expression
trees are substituted with literal values from the impl's Config snapshot at compile.
The substitution uses a single-dispatch visitor mirroring walk.py.
"""

from __future__ import annotations

import hashlib
import json
from functools import singledispatch
from typing import Any

from knot.canonical import canonical_dump, compute_content_hash
from knot.impact import BoundImpl
from knot.metaschema import (
    Between,
    BoolExpr,
    Compare,
    FilteredRelation,
    FormatDerivation,
    Literal_,
    Matches,
    OntologyClass,
    RecursiveTraversal,
    RelationAggregate,
    RelationAll,
    RelationAny,
    RelationCount,
    RelationFirst,
    RelationProject,
    RelationRef,
    ScalarDerivation,
    Slot,
    SlotPath,
    Spec,
    Within,
)
from knot.workflow_spec import StageSpec, WorkflowSpec

# ---------------------------------------------------------------------------
# ConfigRef — expression-tree node for unsubstituted Config field references.
# Per staging/datacontext-config-binding.md: a typed path, no value until compile.
# ---------------------------------------------------------------------------

class ConfigRef:
    """Symbolic reference to a Config field in a DataContext expression tree.

    Not a SpecBase — intentionally a plain class. Carries field_path and
    field_type; substituted at compile time from the config snapshot.
    """

    def __init__(self, field_path: tuple[str, ...], field_type: type) -> None:
        self.field_path = field_path
        self.field_type = field_type

    def __repr__(self) -> str:
        return f"ConfigRef({'.'.join(self.field_path)}: {self.field_type.__name__})"


# ---------------------------------------------------------------------------
# Single-dispatch ConfigRef substitution visitor
# (mirrors walk.py; per core-design.md commitment 10)
# ---------------------------------------------------------------------------

@singledispatch
def substitute_config_refs(node: object, config_snapshot: dict) -> object:
    """Walk expression tree; substitute ConfigRef nodes with literal values.

    Unrecognised node types pass through unchanged (they contain no ConfigRefs).
    This intentionally differs from walk_refs (which raises on missing handlers)
    because most expression nodes simply delegate to children.
    """
    return node


@substitute_config_refs.register
def _(node: ConfigRef, config_snapshot: dict) -> Literal_:
    """Substitute a ConfigRef with its literal value from config_snapshot."""
    value = config_snapshot
    for key in node.field_path:
        if isinstance(value, dict):
            value = value.get(key)
        else:
            value = None
        if value is None:
            break
    return Literal_(value=value)


@substitute_config_refs.register
def _(node: Literal_, config_snapshot: dict) -> Literal_:
    return node


@substitute_config_refs.register
def _(node: SlotPath, config_snapshot: dict) -> SlotPath:
    return node


@substitute_config_refs.register
def _(node: Compare, config_snapshot: dict) -> Compare:
    new_left = substitute_config_refs(node.left, config_snapshot)
    new_right = substitute_config_refs(node.right, config_snapshot) if node.right is not None else None
    return Compare(op=node.op, left=new_left, right=new_right)


@substitute_config_refs.register
def _(node: BoolExpr, config_snapshot: dict) -> BoolExpr:
    new_operands = [substitute_config_refs(op, config_snapshot) for op in node.operands]
    return BoolExpr(op=node.op, operands=new_operands)


@substitute_config_refs.register
def _(node: Within, config_snapshot: dict) -> Within:
    new_left = substitute_config_refs(node.left, config_snapshot)
    new_values = [substitute_config_refs(v, config_snapshot) for v in node.values]
    return Within(left=new_left, values=new_values)


@substitute_config_refs.register
def _(node: Between, config_snapshot: dict) -> Between:
    return Between(
        left=substitute_config_refs(node.left, config_snapshot),
        lower=substitute_config_refs(node.lower, config_snapshot),
        upper=substitute_config_refs(node.upper, config_snapshot),
        inclusive=node.inclusive,
    )


@substitute_config_refs.register
def _(node: Matches, config_snapshot: dict) -> Matches:
    return node


@substitute_config_refs.register
def _(node: RelationRef, config_snapshot: dict) -> RelationRef:
    return node


@substitute_config_refs.register
def _(node: FilteredRelation, config_snapshot: dict) -> FilteredRelation:
    return FilteredRelation(
        relation=substitute_config_refs(node.relation, config_snapshot),
        filter=substitute_config_refs(node.filter, config_snapshot),
    )


@substitute_config_refs.register
def _(node: RelationProject, config_snapshot: dict) -> RelationProject:
    return RelationProject(
        relation=substitute_config_refs(node.relation, config_snapshot),
        project=node.project,
    )


@substitute_config_refs.register
def _(node: RelationCount, config_snapshot: dict) -> RelationCount:
    return RelationCount(
        relation=substitute_config_refs(node.relation, config_snapshot),
        distinct=node.distinct,
    )


@substitute_config_refs.register
def _(node: RelationAggregate, config_snapshot: dict) -> RelationAggregate:
    return RelationAggregate(
        relation=substitute_config_refs(node.relation, config_snapshot),
        func=node.func,
        operand=node.operand,
        distinct=node.distinct,
        group_by=node.group_by,
        order_by=node.order_by,
        pivot=node.pivot,
    )


@substitute_config_refs.register
def _(node: RelationAny, config_snapshot: dict) -> RelationAny:
    return RelationAny(relation=substitute_config_refs(node.relation, config_snapshot))


@substitute_config_refs.register
def _(node: RelationAll, config_snapshot: dict) -> RelationAll:
    return RelationAll(
        relation=substitute_config_refs(node.relation, config_snapshot),
        body=substitute_config_refs(node.body, config_snapshot) if node.body is not None else None,
    )


@substitute_config_refs.register
def _(node: RelationFirst, config_snapshot: dict) -> RelationFirst:
    return RelationFirst(
        relation=substitute_config_refs(node.relation, config_snapshot),
        project=node.project,
        order_by=node.order_by,
        assert_unique=node.assert_unique,
    )


@substitute_config_refs.register
def _(node: RecursiveTraversal, config_snapshot: dict) -> RecursiveTraversal:
    return RecursiveTraversal(
        start=node.start,
        step=node.step,
        until=substitute_config_refs(node.until, config_snapshot) if node.until is not None else None,
        max_depth=node.max_depth,
    )


@substitute_config_refs.register
def _(node: ScalarDerivation, config_snapshot: dict) -> ScalarDerivation:
    return ScalarDerivation(
        expression=substitute_config_refs(node.expression, config_snapshot),
    )


@substitute_config_refs.register
def _(node: FormatDerivation, config_snapshot: dict) -> FormatDerivation:
    return node


# ---------------------------------------------------------------------------
# Cache key computation
# ---------------------------------------------------------------------------

def cache_key(
    stage_kind: str,
    class_name: str | None,
    inputs: dict,
) -> str:
    """Per-stage cache key: sha256 of (stage_kind, class_name, sorted-canonical-input-set).

    Same canonicalization as compiled-workflow-hashing.md: sorted keys, JCS bytes.
    """
    payload = {
        "stage_kind": stage_kind,
        "class_name": class_name,
        "inputs": inputs,
    }
    # Use sorted JSON (RFC 8785 JCS equivalent for plain dicts) for determinism.
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Topological sort
# ---------------------------------------------------------------------------

def _parent_classes_of(cls: OntologyClass) -> list[OntologyClass]:
    """Return OntologyClass objects this class structurally depends on via slot.range."""
    parents: list[OntologyClass] = []
    seen_ids: set[int] = set()
    for slot in cls.slots:
        if isinstance(slot.range, OntologyClass) and slot.range.name != cls.name:
            if id(slot.range) not in seen_ids:
                seen_ids.add(id(slot.range))
                parents.append(slot.range)
    return parents


def topo_sort(spec: Spec, scope: str) -> list[OntologyClass]:
    """Topological sort of classes in the spec for the given scope.

    scope="full"  → all classes in spec, toposorted by slot.range dependency edges.
    scope=ClassName → just that class (parents resolved but not included in output).
    scope="stage:<kind>:<class>" → single-class, same as ClassName.

    Returns classes in dependency order (parents before children).
    Raises ValueError on cycles.
    """
    # Determine which classes to include in the output.
    if scope == "full":
        target_classes = list(spec.classes)
    elif scope.startswith("stage:"):
        parts = scope.split(":", 2)
        class_name = parts[2] if len(parts) == 3 else None
        target_classes = [c for c in spec.classes if c.name == class_name]
    else:
        # class name scope
        target_classes = [c for c in spec.classes if c.name == scope]

    if not target_classes and scope not in ("full",):
        raise ValueError(f"No class found for scope {scope!r}")

    # Build class index from full spec (needed to resolve parents even for narrow scope).
    class_by_name: dict[str, OntologyClass] = {c.name: c for c in spec.classes}

    # Kahn's algorithm over target_classes + their transitive dependencies.
    # Build adjacency: cls -> its direct parent classes within the spec.
    def _parents(cls: OntologyClass) -> list[OntologyClass]:
        return [p for p in _parent_classes_of(cls) if p.name in class_by_name]

    # Collect all nodes we need to sort (target classes + transitive parents).
    all_nodes: dict[str, OntologyClass] = {}
    queue = list(target_classes)
    while queue:
        cls = queue.pop()
        if cls.name in all_nodes:
            continue
        all_nodes[cls.name] = cls
        for parent in _parents(cls):
            if parent.name not in all_nodes:
                queue.append(parent)

    # Build in-degree and adjacency for Kahn's.
    in_degree: dict[str, int] = {name: 0 for name in all_nodes}
    dependents: dict[str, list[str]] = {name: [] for name in all_nodes}

    for name, cls in all_nodes.items():
        for parent in _parents(cls):
            if parent.name in all_nodes:
                in_degree[name] += 1
                dependents[parent.name].append(name)

    ready = sorted([name for name, deg in in_degree.items() if deg == 0])
    sorted_names: list[str] = []
    while ready:
        node = ready.pop(0)
        sorted_names.append(node)
        for dep in sorted(dependents[node]):
            in_degree[dep] -= 1
            if in_degree[dep] == 0:
                ready.append(dep)
                ready.sort()

    if len(sorted_names) != len(all_nodes):
        cycle_nodes = [n for n in all_nodes if n not in sorted_names]
        raise ValueError(
            f"ER strategy dependency cycle detected among classes: {cycle_nodes}"
        )

    # Return only the requested target classes, in sorted order.
    target_names = {c.name for c in target_classes}
    return [all_nodes[name] for name in sorted_names if name in target_names]


# ---------------------------------------------------------------------------
# Stage list builder
# ---------------------------------------------------------------------------

# Pipeline stages per class, in order.
_CLASS_STAGES: list[str] = ["normalize", "resolve", "merge", "validate", "publish"]


def _impl_for(
    class_name: str,
    stage_kind: str,
    bound_impls: list[BoundImpl],
) -> BoundImpl | None:
    """Find the first bound impl whose workflow matches class_name."""
    for bi in bound_impls:
        if bi.workflow == class_name:
            return bi
    return None


def _sources_for_class(spec: Spec, cls: OntologyClass) -> list[str]:
    """Return source names whose entity_class is cls (via real-ref identity).

    Per spec-loading.md (closed): sources live on `spec.sources` since Round
    spec-authoring.  The compiler enumerates them here for normalize-stage
    emission.
    """
    return [s.name for s in spec.sources if s.entity_class is cls]


def _build_stages_for_class(
    cls: OntologyClass,
    sorted_classes: list[OntologyClass],
    class_source_names: list[str],
    bound_impls: list[BoundImpl],
    impl_configs: dict[str, dict],
    source_watermarks: dict[str, str],
    spec_revision_ids: dict[str, int],
    parents_override: dict[str, str] | None,
    # cache_key_store: callable that returns prior artifact path (None = miss)
    prior_cache_keys: dict[str, str] | None,
) -> list[StageSpec]:
    """Build StageSpec list for one class across all pipeline stages.

    Normalize fans out to one stage per Source (per spec.sources filtered by
    entity_class).  Other stages remain one-per-class.
    """
    stages: list[StageSpec] = []

    # Determine if this is a relation class (has slots whose range is another class).
    parent_class_names = [p.name for p in _parent_classes_of(cls)]
    is_relation_class = bool(parent_class_names)

    # Resolve pinned parent runs.
    pinned: dict[str, str] = {}
    if is_relation_class:
        for parent_name in parent_class_names:
            if parents_override and parent_name in parents_override:
                pinned[parent_name] = parents_override[parent_name]

    # Find bound impl for this class.
    impl = _impl_for(cls.name, cls.name, bound_impls)
    impl_name = impl.impl_name if impl else None
    impl_revision: int | None = None
    config_revision: int | None = None
    if impl_name:
        cfg = impl_configs.get(impl_name, {})
        config_revision = cfg.get("_revision")
        impl_revision = cfg.get("_impl_revision")

    requires_classes = [cls.name] + parent_class_names
    spec_rev_subset = {
        name: rev
        for name, rev in spec_revision_ids.items()
        if name in requires_classes
    }

    # ── normalize: one stage per source for this class ────────────────────
    for src_name in class_source_names:
        src_wm = source_watermarks.get(src_name, "")
        inputs: dict[str, Any] = {
            "spec_revisions": spec_rev_subset,
            "impl_revision": impl_revision,
            "config_revision": config_revision,
            "source_name": src_name,
            "source_watermark": src_wm,
        }
        ck = cache_key("normalize", cls.name, inputs)
        hit = prior_cache_keys.get(ck) if prior_cache_keys else None
        stages.append(StageSpec(
            kind="normalize",
            class_name=cls.name,
            source_name=src_name,
            impl_name=None,
            impl_revision=None,
            config_revision=None,
            cache_key=ck,
            cache_hit_artifact=hit,
            pinned_parent_runs={},
            source_watermarks={src_name: src_wm},
            requires_classes=requires_classes,
        ))

    # ── resolve / merge / validate / publish: one stage per class ─────────
    for stage_kind in ("resolve", "merge", "validate", "publish"):
        inputs = {
            "spec_revisions": spec_rev_subset,
            "impl_revision": impl_revision,
            "config_revision": config_revision,
        }
        if is_relation_class:
            inputs["pinned_parent_runs"] = pinned

        ck = cache_key(stage_kind, cls.name, inputs)
        hit = prior_cache_keys.get(ck) if prior_cache_keys else None

        stages.append(StageSpec(
            kind=stage_kind,  # type: ignore[arg-type]
            class_name=cls.name,
            source_name=None,
            impl_name=impl_name if stage_kind == "resolve" else None,
            impl_revision=impl_revision if stage_kind == "resolve" else None,
            config_revision=config_revision if stage_kind == "resolve" else None,
            cache_key=ck,
            cache_hit_artifact=hit,
            pinned_parent_runs=pinned if is_relation_class else {},
            source_watermarks={},
            requires_classes=requires_classes,
        ))

    return stages


# ---------------------------------------------------------------------------
# Main compile entry point
# ---------------------------------------------------------------------------

def compile(  # noqa: A001  (shadowing built-in is intentional: this IS the knot compiler)
    spec: Spec,
    bound_impls: list[BoundImpl],
    impl_configs: dict[str, dict],
    source_watermarks: dict[str, str],
    scope: str,
    parents_override: dict[str, str] | None = None,
    prior_cache_keys: dict[str, str] | None = None,
) -> WorkflowSpec:
    """Compile spec + impls + configs + watermarks → WorkflowSpec.

    1. Resolve scope: which classes / stages does this trigger touch?
    2. For relation classes: look up parent runs via cross-class-pinning logic
       (latest succeeded per parent class, or use parents_override).
    3. For each stage in toposort: substitute ConfigRefs in DataContext expressions
       (per datacontext-config-binding.md), compute cache key from inputs.
    4. Produce StageSpec list; serialize WorkflowSpec via canonical_dump.
    5. Hash the canonical bytes → that's the run identity.

    Args:
        spec: The ontology Spec.
        bound_impls: Registered BoundImpl bindings.
        impl_configs: impl_name -> Config snapshot dict (may include _revision key).
        source_watermarks: source_name -> watermark string.
        scope: "full", a class name like "Movie", or "stage:resolve:Movie".
        parents_override: cross-class pinning override — class_name -> run_hash.
            Overrides the default "latest succeeded" lookup for relation-class parents.
        prior_cache_keys: cache_key_hash -> artifact_path from prior successful runs.
            Used to populate cache_hit_artifact on StageSpec.

    Returns:
        WorkflowSpec with toposorted stages, cache keys, and pinned parent runs.
    """
    # 1. Resolve scope to ordered class list.
    sorted_classes = topo_sort(spec, scope)

    # 2. Build spec_revision_ids from all classes in scope.
    spec_revision_ids: dict[str, int] = {}
    for cls in sorted_classes:
        # Use a synthetic revision = 1 when no real revision tracking exists.
        # Real deployments supply these from postgres-control.
        spec_revision_ids[cls.name] = 1

    # 3. Build stages for each class in toposorted order.
    all_stages: list[StageSpec] = []
    for cls in sorted_classes:
        cls_stages = _build_stages_for_class(
            cls=cls,
            sorted_classes=sorted_classes,
            class_source_names=_sources_for_class(spec, cls),
            bound_impls=bound_impls,
            impl_configs=impl_configs,
            source_watermarks=source_watermarks,
            spec_revision_ids=spec_revision_ids,
            parents_override=parents_override,
            prior_cache_keys=prior_cache_keys,
        )
        all_stages.extend(cls_stages)

    # 4. Produce WorkflowSpec.
    workflow = WorkflowSpec(
        spec_revision_ids=spec_revision_ids,
        stages=all_stages,
    )

    return workflow


def compile_hash(workflow: WorkflowSpec) -> str:
    """Return the content-addressed hash of a compiled WorkflowSpec.

    This is the run identity per core-design.md commitment 3.
    Same machinery as canonical.py: canonical_dump → sha256.
    """
    return compute_content_hash(workflow)
