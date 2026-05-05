"""B2 Neo4j materialization impl — structural scaffolding.

knot.protocols does not exist yet. This file establishes the correct shape
so it compiles once the core is built.

Covers: cat 14.2 (multi-class materialization), 14.3 (multi-target),
14.4 (derived-edge materialization), 14.6 (Config.exclude_classes),
11.7 (multi-class primary), 11.8 (DerivedSlot as primary → edge view).
"""

from __future__ import annotations

from typing import ClassVar

from knot.protocols import DataContext, MaterializerProtocol  # noqa: F401
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

    def materialize(self, ctx: Neo4jConfig, nodes, edges) -> None:
        """Write nodes then edges to Neo4j via bulk UNWIND.

        For each class in nodes: write (:ClassName {canonical_id, ...}) nodes.
        For each derived slot in edges: write ()-[:RELATIONSHIP]->() edges.

        Audit trail: each run writes a :KnotRun node with compile_hash,
        enabling walk-back (cat 15.1, 15.3, 15.4).
        """
        ...
