"""Spec persistence + rehydration against postgres `spec_revisions`.

Two complementary serializers:
  - `canonical_dump` (in `canonical.py`) for content hashing — strips
    defaults + RUNTIME fields.
  - `spec_to_dict` / `spec_from_dict` here for storage round-trip — full
    fidelity, cycle-safe via `{"$ref": "<name>"}` tokens at second visit
    of named SpecBase nodes.

Two-pass rehydration (per `spec-model.md` § "Persistence boundary"):
  - Pass 1: walk the JSON, build all named entities (TypeDefinition, Slot,
    OntologyClass, Source, Constraint) with their identifying fields and
    placeholder cross-refs.
  - Pass 2: walk again, resolving every `{"$ref": "<name>"}` token to the
    real Python object built in pass 1.

Draft lifecycle:
  - Drafts are spec_revisions rows with `published=FALSE`; mutable in place.
  - Publishing flips `published=TRUE` (atomically deactivates prior).
  - The publish gate (`publish_gate`) runs Pydantic re-parse + reference
    resolution across the spec graph before flipping the flag.  Steps 3 + 4
    (DataContext cross-checks, impact preview) are bindings-side and land
    with the modeling router.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import psycopg
from pydantic import BaseModel

from knot.ontology.canonical import compute_content_hash
from knot.ontology.metaschema import (
    BoolExpr,
    BoolOpKind,
    Compare,
    CompareOp,
    Constraint,
    DirectRef,
    DiscriminatedRef,
    FilteredRelation,
    FormatDerivation,
    GroupByMode,
    IdentifierPattern,
    Literal_,
    Matches,
    OntologyClass,
    PermissibleValue,
    RecursiveTraversal,
    RelationAggregate,
    RelationAll,
    RelationAny,
    RelationCount,
    RelationFirst,
    RelationProject,
    RelationRef,
    ResolutionPolicy,
    ScalarDerivation,
    Severity,
    Slot,
    SlotOverride,
    SlotPath,
    Source,
    Spec,
    TypeDefinition,
    UniqueKey,
    Within,
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class PublishGateError(Exception):
    """Raised when the publish gate rejects a candidate spec."""


class DraftNotFoundError(Exception):
    """Raised when a draft revision number doesn't exist."""


class DraftAlreadyPublishedError(Exception):
    """Raised when attempting to mutate a revision that's already published."""


# ---------------------------------------------------------------------------
# Serializer — full-fidelity Spec → plain dict (cycle-safe via $ref)
# ---------------------------------------------------------------------------

# Classes whose instances participate in cycle de-dup (have a `name` field).
# Inline-only nodes (expression tree) always emit fully even on revisit.
_NAMED_CLASSES = (TypeDefinition, Slot, OntologyClass, Source, Constraint)


def _is_named(obj: Any) -> bool:
    return isinstance(obj, _NAMED_CLASSES) and isinstance(getattr(obj, "name", None), str)


class _SerCtx:
    """Per-serialize state.  Tracks named-entity object identity by counter UID
    so two entities with the same name (e.g. `Movie.imdb_id` vs `Person.imdb_id`)
    serialize / deserialize as distinct objects.
    """

    def __init__(self) -> None:
        self.id_to_uid: dict[int, int] = {}
        self.next_uid: int = 0

    def assign(self, oid: int) -> int:
        uid = self.next_uid
        self.id_to_uid[oid] = uid
        self.next_uid += 1
        return uid


def _ser(obj: Any, ctx: _SerCtx) -> Any:
    """Serialize a Pydantic graph to plain Python.

    Named SpecBase nodes carry a per-object `$uid` (counter) on first visit;
    repeat visits emit `{"$ref": <uid>, "$kind": "<class>"}`.  Names live in the
    payload alongside but never key the cycle table — collisions across classes
    (Movie.imdb_id vs Person.imdb_id) are handled correctly.
    """
    if isinstance(obj, BaseModel):
        oid = id(obj)
        if _is_named(obj):
            if oid in ctx.id_to_uid:
                return {"$ref": ctx.id_to_uid[oid], "$kind": type(obj).__name__}
            uid = ctx.assign(oid)
        else:
            uid = None

        result: dict[str, Any] = {"$kind": type(obj).__name__}
        if uid is not None:
            result["$uid"] = uid
        for fname in type(obj).model_fields:
            value = getattr(obj, fname)
            result[fname] = _ser(value, ctx)
        return result

    if isinstance(obj, list):
        return [_ser(item, ctx) for item in obj]
    if isinstance(obj, tuple):
        return [_ser(item, ctx) for item in obj]
    if isinstance(obj, dict):
        return {k: _ser(v, ctx) for k, v in obj.items()}
    if isinstance(obj, (set, frozenset)):
        return sorted(_ser(item, ctx) for item in obj)

    # Enums → their .value
    val = getattr(obj, "value", None)
    if val is not None and hasattr(obj, "name") and not isinstance(obj, BaseModel):
        if isinstance(val, (str, int, float, bool)):
            return val

    return obj


def spec_to_dict(spec: Spec) -> dict[str, Any]:
    """Full-fidelity serialize a Spec.  Round-trips via `spec_from_dict`."""
    return _ser(spec, _SerCtx())


# ---------------------------------------------------------------------------
# Deserializer — plain dict → Spec with real Python refs (two-pass)
# ---------------------------------------------------------------------------

# Lookup: $kind class name → metaschema class
from knot.ontology.metaschema import Between as _Between  # noqa: E402

_KIND_REGISTRY: dict[str, type] = {
    "TypeDefinition": TypeDefinition,
    "PermissibleValue": PermissibleValue,
    "Slot": Slot,
    "SlotOverride": SlotOverride,
    "OntologyClass": OntologyClass,
    "DirectRef": DirectRef,
    "DiscriminatedRef": DiscriminatedRef,
    "IdentifierPattern": IdentifierPattern,
    "UniqueKey": UniqueKey,
    "Constraint": Constraint,
    "Source": Source,
    "Spec": Spec,
    "Literal_": Literal_,
    "SlotPath": SlotPath,
    "Compare": Compare,
    "BoolExpr": BoolExpr,
    "Within": Within,
    "Between": _Between,
    "Matches": Matches,
    "RelationRef": RelationRef,
    "FilteredRelation": FilteredRelation,
    "RelationProject": RelationProject,
    "RelationCount": RelationCount,
    "RelationAggregate": RelationAggregate,
    "RelationAny": RelationAny,
    "RelationAll": RelationAll,
    "RelationFirst": RelationFirst,
    "RecursiveTraversal": RecursiveTraversal,
    "ScalarDerivation": ScalarDerivation,
    "FormatDerivation": FormatDerivation,
}


class _Index:
    """Pass-1 build → UID-keyed dictionary of placeholder Pydantic objects."""

    def __init__(self) -> None:
        self.by_uid: dict[int, Any] = {}

    def get(self, uid: int) -> Any:
        if uid not in self.by_uid:
            raise KeyError(f"$ref → uid={uid} not found in pass-1 index")
        return self.by_uid[uid]


_PLACEHOLDER_KINDS = {
    "TypeDefinition": TypeDefinition,
    "Slot": Slot,
    "OntologyClass": OntologyClass,
    "Source": Source,
    "Constraint": Constraint,
}


def _pass1_build(d: Any, index: _Index) -> None:
    """Recursively walk the JSON tree; for each named entity that carries a
    `$uid`, create a name-only placeholder keyed by uid.  Source and
    Constraint placeholders are built later in pass 2 because their
    cross-refs (entity_class, identifier_slot, primary, body) need to
    resolve through the uid index.
    """
    if isinstance(d, list):
        for item in d:
            _pass1_build(item, index)
        return
    if not isinstance(d, dict):
        return
    if "$ref" in d:
        return

    kind = d.get("$kind")
    uid = d.get("$uid")
    name = d.get("name")
    placeholder_cls = _PLACEHOLDER_KINDS.get(kind)

    if (
        placeholder_cls in (TypeDefinition, Slot, OntologyClass)
        and isinstance(uid, int)
        and isinstance(name, str)
        and uid not in index.by_uid
    ):
        index.by_uid[uid] = placeholder_cls(name=name)

    for value in d.values():
        _pass1_build(value, index)


def _is_ref(d: Any) -> bool:
    return isinstance(d, dict) and "$ref" in d


def _resolve(d: Any, index: _Index) -> Any:
    """Pass-2: walk JSON → real Pydantic object graph.

    `$ref` tokens look up the placeholder in the uid index.
    Inline definitions with `$uid` patch the corresponding placeholder
    (preserving identity); inline definitions without `$uid` (Sources,
    Constraints, expression-tree nodes) construct fresh.
    """
    if isinstance(d, list):
        return [_resolve(item, index) for item in d]
    if not isinstance(d, dict):
        return d
    if "$ref" in d:
        return index.get(d["$ref"])

    kind = d.get("$kind")
    if kind is None:
        # Plain dict (e.g. prefixes); recurse into values.
        return {k: _resolve(v, index) for k, v in d.items() if k != "$kind"}

    cls = _KIND_REGISTRY.get(kind)
    if cls is None:
        raise PublishGateError(f"Unknown $kind during rehydration: {kind!r}")

    kwargs: dict[str, Any] = {}
    for fname, raw in d.items():
        if fname.startswith("$"):
            continue
        kwargs[fname] = _resolve(raw, index)

    # For named entities with a uid, patch the existing pass-1 placeholder
    # so cross-refs share Python object identity.
    uid = d.get("$uid")
    if isinstance(uid, int) and uid in index.by_uid:
        existing = index.by_uid[uid]
        if isinstance(existing, (TypeDefinition, Slot, OntologyClass)):
            for k, v in kwargs.items():
                if k != "name":
                    setattr(existing, k, v)
            return existing

    obj = cls(**kwargs)

    # Source and Constraint placeholders aren't pre-built; index them by
    # uid here so any subsequent $ref lands cleanly.
    if isinstance(uid, int) and uid not in index.by_uid:
        index.by_uid[uid] = obj

    return obj


def spec_from_dict(d: dict[str, Any]) -> Spec:
    """Two-pass rehydration: dict → Spec with real Python object refs throughout."""
    index = _Index()
    _pass1_build(d, index)

    # Pass 2: types first (no deps), then slots (range needs types/classes),
    # then classes (slots field needs slots), then sources (need class+slot),
    # then constraints (need class + expression refs into slots/classes).
    types_resolved = [_resolve(td, index) for td in d.get("types", [])]
    slots_resolved = [_resolve(sd, index) for sd in d.get("slots", [])]
    classes_resolved = [_resolve(cd, index) for cd in d.get("classes", [])]
    # Sources and constraints construct fresh; their cross-refs to types/
    # slots/classes resolve through the uid index built above.
    sources_resolved = [_resolve(s, index) for s in d.get("sources", [])]
    constraints_resolved = [_resolve(c, index) for c in d.get("constraints", [])]

    return Spec(
        id=d["id"],
        version=d["version"],
        types=types_resolved,
        slots=slots_resolved,
        classes=classes_resolved,
        sources=sources_resolved,
        constraints=constraints_resolved,
        prefixes=d.get("prefixes", {}),
        default_range=_resolve(d.get("default_range"), index) if d.get("default_range") else None,
    )


# ---------------------------------------------------------------------------
# Publish gate
# ---------------------------------------------------------------------------

def publish_gate(candidate: Spec) -> None:
    """Run all spec-graph-side validation.  Raises `PublishGateError` on failure.

    Step 1: Pydantic shape (already enforced by Spec instantiation; re-runs
            model_validate over the canonical dump as a defensive recheck).
    Step 2: Reference resolution — every Slot range, every Source.entity_class,
            every Source.identifier_slot, every Constraint.primary, every
            SlotOverride.slot must point at an entity present on the spec.

    Steps 3+4 (DataContext cross-checks + impact preview) are bindings-side
    and land with the modeling router.  This function is the natural place
    for them to be added.
    """
    # Step 1: shape was enforced at construction (extra='forbid' on every
    # SpecBase subclass; bad fields raise immediately).  Pydantic's
    # model_dump-and-revalidate trick doesn't work on the cyclic spec graph
    # (slot.range → OntologyClass → slots → Slot → range → ...), so we trust
    # that the in-memory Pydantic models are well-formed.
    if not isinstance(candidate, Spec):
        raise PublishGateError(
            f"publish_gate expected a Spec instance, got {type(candidate).__name__}"
        )

    # Step 2: reference resolution.
    classes_by_id = {id(c): c for c in candidate.classes}
    slots_by_id = {id(s): s for s in candidate.slots}
    types_by_id = {id(t): t for t in candidate.types}

    errors: list[str] = []

    for s in candidate.slots:
        if s.range is None:
            # Derived slots with deferred range are valid; structural slots
            # without range are an error.
            if s.derivation is None:
                errors.append(f"Slot {s.name!r} has no range and no derivation.")
            continue
        if isinstance(s.range, OntologyClass):
            if id(s.range) not in classes_by_id:
                errors.append(
                    f"Slot {s.name!r}.range references OntologyClass "
                    f"{s.range.name!r} not on spec.classes."
                )
        elif isinstance(s.range, TypeDefinition):
            if id(s.range) not in types_by_id:
                errors.append(
                    f"Slot {s.name!r}.range references TypeDefinition "
                    f"{s.range.name!r} not on spec.types."
                )

    for c in candidate.classes:
        for slot in c.slots:
            if id(slot) not in slots_by_id:
                errors.append(
                    f"Class {c.name!r}.slots includes Slot {slot.name!r} not on spec.slots."
                )

    for src in candidate.sources:
        if id(src.entity_class) not in classes_by_id:
            errors.append(
                f"Source {src.name!r}.entity_class references OntologyClass "
                f"{src.entity_class.name!r} not on spec.classes."
            )
        if id(src.identifier_slot) not in slots_by_id:
            errors.append(
                f"Source {src.name!r}.identifier_slot references Slot "
                f"{src.identifier_slot.name!r} not on spec.slots."
            )
        # identifier_slot must be one of entity_class.slots
        if not any(slot is src.identifier_slot for slot in src.entity_class.slots):
            errors.append(
                f"Source {src.name!r}.identifier_slot ({src.identifier_slot.name!r}) "
                f"is not on its entity_class {src.entity_class.name!r}."
            )

    for con in candidate.constraints:
        if id(con.primary) not in classes_by_id:
            errors.append(
                f"Constraint {con.name!r}.primary references OntologyClass "
                f"{con.primary.name!r} not on spec.classes."
            )

    if errors:
        raise PublishGateError("Publish gate failed:\n  - " + "\n  - ".join(errors))


# ---------------------------------------------------------------------------
# Postgres storage layer
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def get_published(conn: psycopg.Connection) -> Spec | None:
    """Return the currently-published spec, or None if none is published."""
    row = conn.execute(
        "SELECT spec FROM spec_revisions WHERE published = TRUE",
    ).fetchone()
    if row is None:
        return None
    payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    return spec_from_dict(payload)


def get_revision(conn: psycopg.Connection, revision: int) -> Spec:
    """Return the spec at a specific revision (draft or published)."""
    row = conn.execute(
        "SELECT spec FROM spec_revisions WHERE revision = %s",
        (revision,),
    ).fetchone()
    if row is None:
        raise DraftNotFoundError(f"spec_revisions {revision} not found")
    payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    return spec_from_dict(payload)


def list_drafts(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """Summary rows for all unpublished drafts."""
    rows = conn.execute(
        "SELECT revision, label, parent_revision, content_hash, created_at "
        "FROM spec_revisions WHERE published = FALSE "
        "ORDER BY revision DESC",
    ).fetchall()
    return [
        {
            "revision": r[0],
            "label": r[1],
            "parent_revision": r[2],
            "content_hash": r[3],
            "created_at": r[4].isoformat(),
        }
        for r in rows
    ]


def list_published(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """All published revisions, newest first.  Immortal."""
    rows = conn.execute(
        "SELECT revision, content_hash, parent_revision, label, "
        "created_at, published_at "
        "FROM spec_revisions WHERE published_at IS NOT NULL "
        "ORDER BY published_at DESC",
    ).fetchall()
    return [
        {
            "revision": r[0],
            "content_hash": r[1],
            "parent_revision": r[2],
            "label": r[3],
            "created_at": r[4].isoformat(),
            "published_at": r[5].isoformat() if r[5] else None,
        }
        for r in rows
    ]


def create_draft(
    conn: psycopg.Connection,
    *,
    parent_revision: int | None = None,
    label: str | None = None,
) -> int:
    """Create a draft branched from a published parent (or specified revision).

    If `parent_revision` is None, branches from the currently-published spec.
    If no spec is published yet, the draft starts as an empty Spec.
    Returns the new draft's revision number.
    """
    if parent_revision is not None:
        parent = get_revision(conn, parent_revision)
    else:
        parent = get_published(conn) or Spec(id="", version="0.0.0")

    payload = spec_to_dict(parent)
    content_hash = compute_content_hash(parent)

    row = conn.execute(
        """
        INSERT INTO spec_revisions
            (spec, content_hash, published, parent_revision, label, created_at)
        VALUES (%s, %s, FALSE, %s, %s, %s)
        RETURNING revision
        """,
        (json.dumps(payload), content_hash, parent_revision, label, _now()),
    ).fetchone()
    return row[0]


def update_draft(conn: psycopg.Connection, draft_id: int, spec: Spec) -> None:
    """Overwrite a draft's spec content.  Drafts are mutable; published rows are not."""
    row = conn.execute(
        "SELECT published FROM spec_revisions WHERE revision = %s",
        (draft_id,),
    ).fetchone()
    if row is None:
        raise DraftNotFoundError(f"spec_revisions {draft_id} not found")
    if row[0] is True:
        raise DraftAlreadyPublishedError(
            f"spec_revisions {draft_id} is already published; create a new draft "
            "branched from it instead."
        )

    payload = spec_to_dict(spec)
    content_hash = compute_content_hash(spec)
    conn.execute(
        "UPDATE spec_revisions SET spec = %s, content_hash = %s WHERE revision = %s",
        (json.dumps(payload), content_hash, draft_id),
    )


def get_published_revision(conn: psycopg.Connection) -> int | None:
    """Revision number of the currently-published spec, or None."""
    row = conn.execute(
        "SELECT revision FROM spec_revisions WHERE published = TRUE",
    ).fetchone()
    return row[0] if row else None


def publish_draft(
    conn: psycopg.Connection,
    draft_id: int,
    *,
    allow_destructive: bool = False,
) -> int:
    """Run the publish gate; on pass, atomically flip this draft to published
    and bring the data-plane schema in line with the new spec.

    The destructive-change check (DropClass / DropSlot / ChangeSlotType)
    runs against the diff before the flag flip; if any destructive change
    is present and ``allow_destructive`` is False, raises
    ``PublishGateError`` and the draft stays a draft.

    The whole flow — gate, diff, classify, flip, migrate — runs inside one
    transaction so concurrent publishes can't observe the partial unique
    index in a transient state, and the data plane and the spec are
    never out of sync.
    """
    from knot.db.migration import (
        apply_changes,
        diff_specs,
        is_destructive,
    )

    with conn.transaction():
        candidate = get_revision(conn, draft_id)
        publish_gate(candidate)
        prev = get_published(conn)

        changes = diff_specs(prev, candidate)
        destructive = [type(c).__name__ for c in changes if is_destructive(c)]
        if destructive and not allow_destructive:
            raise PublishGateError(
                "Publish would apply destructive changes "
                f"({', '.join(sorted(set(destructive)))}); pass "
                "allow_destructive=true to confirm."
            )

        # Demote → promote in two statements so the partial unique index
        # `(published) WHERE published = TRUE` doesn't see two TRUE rows
        # transiently (postgres validates per-row, not per-statement).
        conn.execute(
            "UPDATE spec_revisions SET published = FALSE "
            "WHERE published = TRUE AND revision <> %s",
            (draft_id,),
        )
        conn.execute(
            "UPDATE spec_revisions SET published = TRUE, published_at = %s "
            "WHERE revision = %s",
            (_now(), draft_id),
        )
        apply_changes(conn, changes)

    return draft_id


def discard_draft(conn: psycopg.Connection, draft_id: int) -> None:
    """Delete an unpublished draft.  Published revisions are immortal."""
    row = conn.execute(
        "SELECT published FROM spec_revisions WHERE revision = %s",
        (draft_id,),
    ).fetchone()
    if row is None:
        raise DraftNotFoundError(f"spec_revisions {draft_id} not found")
    if row[0] is True:
        raise DraftAlreadyPublishedError(
            f"spec_revisions {draft_id} is published; cannot be discarded."
        )
    conn.execute("DELETE FROM spec_revisions WHERE revision = %s", (draft_id,))


