"""B2 Iceberg materialization impl — structural scaffolding.

knot.protocols does not exist yet. This file establishes the correct shape
so it compiles once the core is built.

Covers: cat 14.1 (single-class materialization), 14.3 (multi-target — this
impl writes alongside Neo4j in the same pipeline run), 11.7 (spec.classes
as primary).
"""

from __future__ import annotations

from typing import ClassVar

from knot.protocols import DataContext, MaterializerProtocol, MaterializeResult  # noqa: F401
from tests.fixtures.B2.spec import Movie, Person, Credit, spec


class IcebergConfig:
    """Config for the B2 Iceberg publisher.

    catalog: str — Iceberg catalog name.
    namespace: str — target namespace / database.
    table_prefix: str — prepended to class name for the output table.
    """

    catalog: str = "prod_lake"
    namespace: str = "resolved_facts_b2"
    table_prefix: str = ""


class IcebergPublisher(MaterializerProtocol):
    """Writes all resolved B2 classes to Iceberg analytics tables.

    Uses spec.classes as primary — all 3 classes (Movie, Person, Credit)
    are written in one impl. New spec classes flow in automatically at
    the next compile without touching this file (cat 11.7, commitment 2).
    """

    Config: ClassVar[type] = IcebergConfig

    # spec.classes covers Movie, Person, Credit — no Graph symbol (commitment 2)
    all_classes: DataContext = DataContext(primary=spec.classes)

    def materialize(self, ctx: IcebergConfig, all_classes) -> MaterializeResult:
        """Write each resolved-facts view to its Iceberg table.

        For each class in all_classes: write to
        {ctx.catalog}.{ctx.namespace}.{ctx.table_prefix}{class.name.lower()}.

        Iceberg table schema is derived from the resolved-facts view at
        compile time; schema evolution follows Iceberg type promotion rules.
        """
        ...
