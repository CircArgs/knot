"""C2 Neo4j materialization impl — structural scaffolding.

knot.protocols does not exist yet. This file establishes the correct shape
so it compiles once the core is built.

Covers: cat 14.2 (multi-class materialization), 14.4 (derived-edge),
14.5 (subclass-query materialization — Title → all subclasses),
14.6 (Config.exclude_classes), 8.3 (subclass query), 11.7, 11.8.
"""

from __future__ import annotations

from typing import ClassVar

from knot.protocols import DataContext, MaterializerProtocol  # noqa: F401
from knot.metaschema import DerivedSlot, OntologyClass, SpecBase  # noqa: F401
from tests.fixtures.C2.spec import (
    Title, Movie, Series, Episode, Game,
    Person, Credit, Identifier, Studio, Award, Country,
    movie_director, movie_actors, movie_writers, movie_producers,
    series_creator, episode_lead_actors,
    studio_films, person_directing_credits,
    spec,
)


class Neo4jConfig(SpecBase):
    """Config for the C2 Neo4j publisher.

    uri: str — Bolt URI for the Neo4j instance.
    database: str — target Neo4j database name.
    batch_size: int — rows per UNWIND batch.
    classes: list[OntologyClass] — classes included in node export (cat 14.6).
    derived_edges: list[DerivedSlot] — derived slots materialised as edge types (cat 14.4).
    """

    uri: str = "bolt://localhost:7687"
    database: str = "knot_c2"
    batch_size: int = 1000
    # Concrete classes only — Title is abstract (not materialised directly; cat 14.5, 8.3)
    classes: list[OntologyClass] = [
        Movie, Series, Episode, Game,
        Person, Credit, Identifier, Studio, Award, Country,
    ]
    derived_edges: list[DerivedSlot] = [                         # cat 14.4, 11.8
        movie_director, movie_actors, movie_writers, movie_producers,
        series_creator, episode_lead_actors,
        studio_films, person_directing_credits,
    ]


class Neo4jPublisher(MaterializerProtocol):
    """Writes the resolved C2 graph to Neo4j.

    Includes all concrete classes (Title subclasses + Person + Credit +
    Identifier + Studio + Award + Country) as nodes, plus derived slots
    as typed relationship edges.

    Title is abstract — not emitted as a node label. Subclass query
    returns Movie + Series + Episode + Game nodes under the :Title label
    (cat 14.5, 8.3).

    No Graph symbol — Config.classes holds the concrete class list;
    ConfigRef substitutes at compile (commitment 2 / datacontext-config-binding.md).
    """

    Config: ClassVar[type[Neo4jConfig]] = Neo4jConfig

    # Config.classes defaults to all concrete classes; edit Config to exclude (cat 14.6)
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
          - Movie/Series/Episode/Game nodes also carry the :Title label (cat 14.5, 8.3).
        For each derived slot in edges: write ()-[:RELATIONSHIP_TYPE]->() edges.

        Polymorphic Identifier nodes are written with both :Identifier and
        :{entity_class} labels to support subclass-style queries (cat 4.4, 8.1).

        Audit trail: each run writes a :KnotRun node with compile_hash,
        spec_rev, and watermarks (cat 15.1).
        """
        ...
