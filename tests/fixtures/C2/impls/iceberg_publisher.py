"""C2 Iceberg materialization impl — structural scaffolding.

knot.protocols does not exist yet. This file establishes the correct shape
so it compiles once the core is built.

Covers: cat 14.2 (multi-class), 14.5 (subclass-query materialization),
14.6 (exclude_classes), 11.7 (spec.classes as primary).
"""

from __future__ import annotations

from typing import ClassVar

from knot.protocols import DataContext, MaterializerProtocol, MaterializeResult  # noqa: F401
from tests.fixtures.C2.spec import spec


class IcebergConfig:
    """Config for the C2 Iceberg publisher.

    catalog: str — Iceberg catalog name.
    namespace: str — target namespace / database.
    table_prefix: str — prepended to class name for the output table.
    exclude_classes: list[OntologyClass] — classes omitted from export (cat 14.6).
    """

    catalog: str = "prod_lake"
    namespace: str = "resolved_facts_c2"
    table_prefix: str = ""
    exclude_classes: list = []


class IcebergPublisher(MaterializerProtocol):
    """Writes all resolved C2 classes to Iceberg analytics tables.

    Uses spec.classes as primary — all concrete classes are written
    automatically. Abstract Title is excluded (not materialised directly);
    its subclasses each get their own table with the full inherited + subclass
    slots (cat 14.5 — subclass-query materialization).

    New classes added to the spec flow into the next compile without
    changing this file (commitment 2).
    """

    Config: ClassVar[type] = IcebergConfig

    all_classes: DataContext = DataContext(
        primary=[
            c for c in spec.classes
            if not getattr(c, "abstract", False)
            and c not in IcebergConfig.exclude_classes
        ],
    )

    def materialize(self, ctx: IcebergConfig, all_classes) -> MaterializeResult:
        """Write each resolved-facts view to its Iceberg table.

        For each class in all_classes: write to
        {ctx.catalog}.{ctx.namespace}.{ctx.table_prefix}{class.name.lower()}.

        Title subclasses (Movie, Series, Episode, Game) each get a flat
        table that merges inherited Title slots with subclass-specific slots
        (cat 14.5) — no union-table indirection.
        """
        ...
