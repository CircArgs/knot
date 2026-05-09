"""Lake materialization orchestration — emit SELECT bodies per class.

knot does not execute the materialization; it emits SELECT bodies that the
operator wraps in their target's DDL/DML (Trino CTAS, dbt model file,
Iceberg MERGE INTO, etc.). The compiler at
``knot.spec.compile.postgres.lake`` produces the SQL strings; this
orchestrator picks the materializable classes off the published spec and
collects the per-class output.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from knot.db import spec_store
from knot.spec.compile.postgres import lake as _lake_compile


@dataclass(frozen=True)
class ClassMaterialization:
    name: str
    current: str  # SELECT body — flat current snapshot
    history: str  # SELECT body — full SCD2 timeline


class NoSpecPublishedError(Exception):
    """Raised when no spec is currently published.

    Local to this module today — the other graph modules use
    ``published_or_409`` at the route boundary instead. Hoist if a third
    module needs it.
    """


class ClassNotMaterializableError(Exception):
    """Raised when a class filter doesn't match a materializable class."""

    def __init__(self, class_name: str) -> None:
        self.class_name = class_name
        super().__init__(
            f"Class {class_name!r} is not on the published spec or is not "
            "materializable (abstract / defined-class view)."
        )


async def materialize(
    conn: psycopg.AsyncConnection,
    *,
    class_filter: str | None = None,
) -> tuple[int, list[ClassMaterialization]]:
    """Generate SELECT bodies for every materializable class on the published spec.

    Returns ``(spec_revision, materializations)``. Raises
    ``NoSpecPublishedError`` when no spec is published, and
    ``ClassNotMaterializableError`` if ``class_filter`` doesn't match a
    materializable class.
    """
    spec = await spec_store.get_published(conn)
    if spec is None:
        raise NoSpecPublishedError("No spec is published yet.")
    revision = await spec_store.get_published_revision(conn) or 0

    classes = [c for c in spec.classes if _lake_compile.is_materializable(c)]
    if class_filter is not None:
        classes = [c for c in classes if c.name == class_filter]
        if not classes:
            raise ClassNotMaterializableError(class_filter)

    materializations = [
        ClassMaterialization(
            name=c.name,
            current=_lake_compile.materialize_current(c),
            history=_lake_compile.materialize_history(c),
        )
        for c in classes
    ]
    return revision, materializations
