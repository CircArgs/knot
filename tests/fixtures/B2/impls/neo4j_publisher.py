"""B2 Neo4j materialization impl — structural scaffolding.

knot.protocols does not exist yet. This file establishes the correct shape
so it compiles once the core is built.

Covers: cat 14.2 (multi-class materialization), 14.3 (multi-target),
14.4 (derived-edge materialization), 14.6 (Config.exclude_classes),
11.7 (multi-class primary), 11.8 (DerivedSlot as primary → edge view).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import ClassVar

import pyarrow as pa
from neo4j import GraphDatabase

from knot.protocols import DataContext, MaterializerProtocol, MaterializeResult  # noqa: F401
from knot.metaschema import DerivedSlot, OntologyClass, SpecBase  # noqa: F401
from tests.fixtures.B2.spec import (
    Movie, Person, Credit,
    movie_director, movie_actors, movie_writers, movie_producers,
    spec,
)


class Neo4jConfig(SpecBase):
    """Config for the B2 Neo4j publisher.

    uri: str — Bolt URI for the Neo4j instance.
    database: str — target Neo4j database name.
    batch_size: int — rows per UNWIND batch during LOAD CSV.
    classes: list[OntologyClass] — classes included in node export (cat 14.6).
    derived_edges: list[DerivedSlot] — derived slots to materialize as edges.
    """

    uri: str = "bolt://localhost:7687"
    database: str = "knot_b2"
    batch_size: int = 1000
    classes: list[OntologyClass] = [Movie, Person, Credit]       # cat 14.6 — edit to exclude
    derived_edges: list[DerivedSlot] = [                         # cat 11.8 — DerivedSlot as primary
        movie_director,
        movie_actors,
        movie_writers,
        movie_producers,
    ]


class Neo4jPublisher(MaterializerProtocol):
    """Writes the resolved B2 graph (Movie + Person + Credit) to Neo4j.

    Two DataContexts:
    - nodes: all classes in Config.classes (cat 14.2, 14.6, 11.7)
    - edges: derived slots materialised as Neo4j relationship types (cat 14.4, 11.8)

    Pattern per staging/multi-class-datacontexts.md — Config.classes list,
    no Graph symbol (commitment 2).
    """

    Config: ClassVar[type[Neo4jConfig]] = Neo4jConfig

    # Multi-class primary: Config.classes (cat 14.6 — ConfigRef substituted at compile)
    nodes: DataContext = DataContext(
        primary=Config.classes,
    )

    # Derived-slot primaries → edge views (cat 14.4, 11.8)
    edges: DataContext = DataContext(
        primary=Config.derived_edges,
    )

    def materialize(self, ctx: Neo4jConfig, nodes, edges) -> MaterializeResult:
        """Write nodes then edges to Neo4j via bulk UNWIND.

        For each class in nodes: write (:ClassName {canonical_id, ...}) nodes.
        For each derived slot in edges: write ()-[:RELATIONSHIP]->() edges.

        Audit trail: each run writes a :KnotRun node with compile_hash,
        enabling walk-back (cat 15.1, 15.3, 15.4).
        """
        started_at = datetime.now(timezone.utc).isoformat()
        driver = GraphDatabase.driver(ctx.uri, auth=("neo4j", "knottest"))

        try:
            # Write nodes: one label per OntologyClass
            # nodes is iterable of (OntologyClass, pa.Table) pairs
            node_pairs = nodes.items() if hasattr(nodes, "items") else nodes
            for cls, table in node_pairs:
                label = cls.name
                cypher = (
                    f"UNWIND $rows AS row "
                    f"MERGE (n:{label} {{canonical_id: row.canonical_id}}) "
                    f"SET n += row"
                )
                self._write_batches(driver, ctx, cypher, table)

            # Write edges: one rel type per DerivedSlot
            # edges is iterable of (DerivedSlot, pa.Table) pairs
            edge_pairs = edges.items() if hasattr(edges, "items") else edges
            for slot, table in edge_pairs:
                rel_type = slot.name.upper()
                # src label: the OntologyClass that owns this derived slot
                # Match by name — Pydantic copies objects at instantiation so
                # identity (is) is unreliable across config instances.
                src_label = next(
                    cls.name
                    for cls in ctx.classes
                    if any(s.name == slot.name for s in cls.slots)
                )
                # dst label: slot.range is the destination OntologyClass
                dst_label = slot.range.name
                cypher = (
                    f"UNWIND $rows AS row "
                    f"MATCH (src:{src_label} {{canonical_id: row.src_id}}) "
                    f"MATCH (dst:{dst_label} {{canonical_id: row.dst_id}}) "
                    f"MERGE (src)-[:{rel_type}]->(dst)"
                )
                self._write_batches(driver, ctx, cypher, table)

            # Audit node — CREATE per run; started_at is always unique
            completed_at = datetime.now(timezone.utc).isoformat()
            compile_hash = getattr(ctx, "compile_hash", None)
            with driver.session(database=ctx.database) as session:
                session.run(
                    "CREATE (r:KnotRun {compile_hash: $compile_hash, "
                    "started_at: $started_at, completed_at: $completed_at})",
                    compile_hash=compile_hash,
                    started_at=started_at,
                    completed_at=completed_at,
                )
        finally:
            driver.close()

        return MaterializeResult(target=ctx.database, status="succeeded", watermark=None)

    @staticmethod
    def _write_batches(driver, ctx: Neo4jConfig, cypher: str, table: pa.Table) -> None:
        """UNWIND rows in batches of ctx.batch_size."""
        rows = table.to_pylist()
        num_batches = max(1, math.ceil(len(rows) / ctx.batch_size))
        for i in range(num_batches):
            batch = rows[i * ctx.batch_size : (i + 1) * ctx.batch_size]
            with driver.session(database=ctx.database) as session:
                session.run(cypher, rows=batch)
