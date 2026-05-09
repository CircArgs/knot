"""POST /graph/corrections — typed correction discriminated union + audit list."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, ValidationError

from knot import db
from knot.api.auth.security import Principal, require_user
from knot.api.graph._common import StrictBase, published_or_409, resolve_class
from knot.api.row_models import build_row_model_for_class, build_value_model_for_slot
from knot.db import graph_store, spec_store
from knot.graph import corrections as graph_corrections
from knot.spec import Slot

router = APIRouter()


# ─── Correction types — discriminated union by ``type`` literal ─────────────


class PropertyCorrection(StrictBase):
    type: Literal["property"] = "property"
    class_name: str
    canonical_id: str
    slot: str
    value: Any


class Merge(StrictBase):
    type: Literal["merge"] = "merge"
    class_name: str
    keep_canonical_id: str
    merge_canonical_ids: list[str] = Field(..., max_length=1_000)


class Split(StrictBase):
    type: Literal["split"] = "split"
    class_name: str
    source_canonical_id: str
    partitions: dict[str, list[tuple[str, str]]]


class Add(StrictBase):
    type: Literal["add"] = "add"
    class_name: str
    new_canonical_id: str
    values: dict[str, Any] = Field(default_factory=dict)


class Tombstone(StrictBase):
    type: Literal["tombstone"] = "tombstone"
    class_name: str
    canonical_id: str
    reason: str | None = None


class RejectContribution(StrictBase):
    type: Literal["reject_contribution"] = "reject_contribution"
    class_name: str
    canonical_id: str
    source: str


Correction = Annotated[
    PropertyCorrection | Merge | Split | Add | Tombstone | RejectContribution,
    Field(discriminator="type"),
]


class CorrectionResponse(StrictBase):
    id: int
    correction_type: str
    applied_revision: int


# ─── Helpers ────────────────────────────────────────────────────────────────


def _validate_property_value(slot: Slot, value: Any) -> Any:
    """Validate the corrected value against the slot's full constraint set
    (type, pattern, min/max, Literal-from-permissible-values, multivalued
    list-shape). Raises HTTPException(422) on mismatch.

    Same constraint-application logic as ingest's per-source row model so
    corrections enforce the same rules as ingestion.
    """
    one_field = build_value_model_for_slot(slot)
    try:
        return one_field.model_validate({slot.name: value}).model_dump()[slot.name]
    except ValidationError as exc:
        errors = [{**e, "loc": ("body", "value", *e["loc"])} for e in exc.errors()]
        raise HTTPException(422, detail=errors)


# ─── Endpoint ───────────────────────────────────────────────────────────────


@router.post("/corrections", response_model=CorrectionResponse)
def submit_correction(
    body: Correction,
    principal: Principal = Depends(require_user),
) -> CorrectionResponse:
    """Submit a typed correction. Auto-applies in one transaction:
    audit row + per-class data mutation + bandit feedback against
    disagreeing sources. 422 on payload type mismatch; 404 on unknown
    class/slot/canonical_id."""
    with db.connect() as conn:
        spec = published_or_409(conn)

        if isinstance(body, PropertyCorrection):
            cls = resolve_class(spec, body.class_name)
            slot = next((s for s in cls.slots if s.name == body.slot), None)
            if slot is None:
                raise HTTPException(404, f"Slot {body.slot!r} not on class {cls.name!r}")
            value = _validate_property_value(slot, body.value)
            spec_revision = spec_store.get_published_revision(conn)
            correction_id = graph_corrections.apply_property_correction(
                conn,
                cls=cls,
                canonical_id=body.canonical_id,
                slot_name=body.slot,
                value=value,
                spec_revision=spec_revision,
                applied_by=principal.username,
                payload_for_log=body.model_dump(),
            )
            return CorrectionResponse(
                id=correction_id,
                correction_type="property",
                applied_revision=spec_revision,
            )

        if isinstance(body, Merge):
            cls = resolve_class(spec, body.class_name)
            if not body.merge_canonical_ids:
                raise HTTPException(400, "merge_canonical_ids must be non-empty")
            if body.keep_canonical_id in body.merge_canonical_ids:
                raise HTTPException(
                    400,
                    "keep_canonical_id must not appear in merge_canonical_ids",
                )
            seen: set[str] = set()
            deduped: list[str] = []
            for cid in body.merge_canonical_ids:
                if cid not in seen:
                    seen.add(cid)
                    deduped.append(cid)
            if not graph_store.canonical_id_exists(
                conn,
                cls=cls,
                canonical_id=body.keep_canonical_id,
            ):
                raise HTTPException(
                    404,
                    f"keep_canonical_id {body.keep_canonical_id!r} has no "
                    f"contributions for class {cls.name!r}",
                )
            for cid in deduped:
                if not graph_store.canonical_id_exists(
                    conn,
                    cls=cls,
                    canonical_id=cid,
                ):
                    raise HTTPException(
                        404,
                        f"merge_canonical_id {cid!r} has no contributions for class {cls.name!r}",
                    )
            spec_revision = spec_store.get_published_revision(conn)
            correction_id = graph_corrections.apply_merge(
                conn,
                cls=cls,
                keep_canonical_id=body.keep_canonical_id,
                merge_canonical_ids=deduped,
                spec_revision=spec_revision,
                applied_by=principal.username,
                payload_for_log=body.model_dump(),
            )
            return CorrectionResponse(
                id=correction_id,
                correction_type="merge",
                applied_revision=spec_revision,
            )

        if isinstance(body, Split):
            cls = resolve_class(spec, body.class_name)
            if len(body.partitions) < 2:
                raise HTTPException(400, "split requires at least 2 partitions")
            if not body.partitions:
                raise HTTPException(400, "partitions must be non-empty")
            if not graph_store.canonical_id_exists(
                conn,
                cls=cls,
                canonical_id=body.source_canonical_id,
            ):
                raise HTTPException(
                    404,
                    f"source_canonical_id {body.source_canonical_id!r} has no "
                    f"current contributions for class {cls.name!r}",
                )
            for new_cid in body.partitions:
                if graph_store.canonical_id_exists(conn, cls=cls, canonical_id=new_cid):
                    raise HTTPException(
                        409,
                        f"new_canonical_id {new_cid!r} already exists for class {cls.name!r}",
                    )
            all_members: list[tuple[str, str]] = []
            for members in body.partitions.values():
                all_members.extend(members)
            if len(all_members) != len(set(all_members)):
                raise HTTPException(
                    400, "each (source, source_row_id) must appear in exactly one partition"
                )
            spec_revision = spec_store.get_published_revision(conn)
            correction_id = graph_corrections.apply_split(
                conn,
                cls=cls,
                source_canonical_id=body.source_canonical_id,
                partitions=body.partitions,
                spec_revision=spec_revision,
                applied_by=principal.username,
                payload_for_log=body.model_dump(),
            )
            return CorrectionResponse(
                id=correction_id,
                correction_type="split",
                applied_revision=spec_revision,
            )

        if isinstance(body, Add):
            cls = resolve_class(spec, body.class_name)
            if graph_store.canonical_id_exists(
                conn,
                cls=cls,
                canonical_id=body.new_canonical_id,
            ):
                raise HTTPException(
                    409,
                    f"new_canonical_id {body.new_canonical_id!r} already exists "
                    f"for class {cls.name!r}",
                )
            SyntheticRowModel = build_row_model_for_class(cls)
            try:
                validated_values = SyntheticRowModel.model_validate(body.values).model_dump(
                    exclude_none=True
                )
            except Exception as exc:
                raise HTTPException(422, detail=str(exc))
            spec_revision = spec_store.get_published_revision(conn)
            correction_id = graph_corrections.apply_add(
                conn,
                cls=cls,
                new_canonical_id=body.new_canonical_id,
                values=validated_values,
                spec_revision=spec_revision,
                applied_by=principal.username,
                payload_for_log=body.model_dump(),
            )
            return CorrectionResponse(
                id=correction_id,
                correction_type="add",
                applied_revision=spec_revision,
            )

        if isinstance(body, Tombstone):
            cls = resolve_class(spec, body.class_name)
            if not graph_store.canonical_id_exists(
                conn,
                cls=cls,
                canonical_id=body.canonical_id,
            ):
                raise HTTPException(
                    404,
                    f"canonical_id {body.canonical_id!r} has no current "
                    f"contributions for class {cls.name!r}",
                )
            spec_revision = spec_store.get_published_revision(conn)
            correction_id = graph_corrections.apply_tombstone(
                conn,
                cls=cls,
                canonical_id=body.canonical_id,
                reason=body.reason,
                spec_revision=spec_revision,
                applied_by=principal.username,
                payload_for_log=body.model_dump(),
            )
            return CorrectionResponse(
                id=correction_id,
                correction_type="tombstone",
                applied_revision=spec_revision,
            )

        if isinstance(body, RejectContribution):
            cls = resolve_class(spec, body.class_name)
            if not graph_store.canonical_id_exists(
                conn,
                cls=cls,
                canonical_id=body.canonical_id,
            ):
                raise HTTPException(
                    404,
                    f"canonical_id {body.canonical_id!r} has no current "
                    f"contributions for class {cls.name!r}",
                )
            spec_revision = spec_store.get_published_revision(conn)
            correction_id = graph_corrections.apply_reject_contribution(
                conn,
                cls=cls,
                canonical_id=body.canonical_id,
                source=body.source,
                spec_revision=spec_revision,
                applied_by=principal.username,
                payload_for_log=body.model_dump(),
            )
            return CorrectionResponse(
                id=correction_id,
                correction_type="reject_contribution",
                applied_revision=spec_revision,
            )

        # Unreachable — all union members are handled above.
        raise HTTPException(
            501,
            f"Correction type {body.type!r} not implemented yet (open).",
        )


@router.get("/corrections")
def list_corrections(limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
    with db.connect() as conn:
        return db.corrections.list_audit_log(conn, limit=limit)
