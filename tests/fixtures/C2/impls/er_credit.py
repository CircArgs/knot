"""C2 Credit ER impl — structural scaffolding.

knot.protocols does not exist yet. This file establishes the correct shape
so it compiles once the core is built.

Credit.work is polymorphic (DiscriminatedRef targeting any Title subclass).
Consumer must declare cross_references explicitly (per spec-model.md —
cat 4.4, 8.2). Orphan references seeded in tmdb_credits (cat 4.3).
"""

from __future__ import annotations

from typing import ClassVar

from knot.protocols import DataContext, ERProtocol  # noqa: F401
from tests.fixtures.C2.spec import (
    Credit, Movie, Series, Episode, Game, Person,
    imdb_credits, tmdb_credits,
)


class ERCreditConfig:
    """Config for the C2 Credit ER impl.

    cross_references: list — explicit class dependencies for polymorphic FK resolution.
    trust_imdb: float — trust for imdb_credits.
    trust_tmdb: float — trust for tmdb_credits.
    """

    # Explicit polymorphic dependency declarations (cat 4.4, 8.2).
    # Without these, knot cannot statically trace Credit → Title subclass edges.
    cross_references: list = [Movie, Series, Episode, Game, Person]
    trust_imdb: float = 0.90
    trust_tmdb: float = 0.58


class ERCredit(ERProtocol):
    """ER impl for the C2 Credit class.

    Credit.work is a DiscriminatedRef (polymorphic): it targets Movie, Series,
    Episode, or Game depending on the entity_class discriminator column.
    cross_references explicitly declares all candidate target classes so knot
    can add those edges to the static dependency graph (cat 4.4, 8.2).

    Designed-in cases:
    - cat 4.1: credits correctly reference resolved Movie/Series/Episode/Game + Person
    - cat 4.2: references through merged canonical_id (lineage redirect)
    - cat 4.3: tmdb_credits rows referencing non-existent Title entities (orphan)
    - cat 1.5: mixed-case role values (DIRECTOR, Director, dIRECTOR) in tmdb_credits
    - cat 8.3: subclass query — Credit rows span all Title subclasses
    """

    Config: ClassVar[type] = ERCreditConfig

    imdb: DataContext = DataContext(
        primary=Credit,
        where=Credit.credit_id.from_source(imdb_credits).is_not_null(),
        project=[Credit.credit_id, Credit.person, Credit.work, Credit.role],
    )
    tmdb: DataContext = DataContext(
        primary=Credit,
        where=Credit.credit_id.from_source(tmdb_credits).is_not_null(),
        project=[Credit.credit_id, Credit.person, Credit.work, Credit.role],
    )

    def score(self, ctx: ERCreditConfig, imdb, tmdb) -> None:
        """Score Credit candidate pairs.

        Merge on (person_canonical_id, work_canonical_id, role) tuple.
        work_canonical_id resolved via DiscriminatedRef — entity_class
        column routes to correct canonical_id namespace per Title subclass.
        Role comparison is case-insensitive (cat 1.5).
        """
        ...
