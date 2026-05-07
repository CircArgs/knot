"""A1 Iceberg materialization impl — structural scaffolding.

knot.protocols does not exist yet. This file establishes the correct
shape so it compiles once the core is built.

Category 14.1: single-class materialization (Movie → Iceberg analytics table).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

# These imports will resolve once knot core is built.
# They are intentionally left as forward references here.
from knot.protocols import DataContext, MaterializerProtocol  # noqa: F401

if TYPE_CHECKING:
    from knot_demo_a1.spec import Movie


class IcebergPublisherConfig:
    """Config for the Iceberg publisher.

    catalog: str — Iceberg catalog name (e.g. "prod_lake").
    namespace: str — target namespace / database.
    table_prefix: str — prepended to class name for the output table.
    """

    catalog: str = "prod_lake"
    namespace: str = "resolved_facts"
    table_prefix: str = ""


class IcebergPublisher(MaterializerProtocol):
    """Writes the resolved Movie view to an Iceberg analytics table.

    DataContext declares the primary class; knot passes the resolved
    single-valued Movie bag at materialization time.
    """

    Config: ClassVar[type] = IcebergPublisherConfig

    movies: DataContext = DataContext(primary=Movie)

    def materialize(self, ctx: IcebergPublisherConfig, movies: DataContext) -> None:
        """Write movies resolved-facts view to Iceberg.

        knot calls this once per run after the resolve:Movie stage
        completes. `movies` is the resolved-facts bag for all canonical
        Movie entities in scope.

        Implementation detail: uses PyIceberg catalog client from
        ctx.catalog, writes to {ctx.namespace}.{ctx.table_prefix}movie.
        """
        ...
