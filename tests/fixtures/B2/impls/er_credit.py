"""B2 Credit ER impl — structural scaffolding.

knot.protocols does not exist yet. This file establishes the correct shape
so it compiles once the core is built.

Covers: cat 4.1 (credit references existing Movie+Person), 4.2 (reference
through merged canonical_id), 4.3 (orphan reference), 4.6 (cross-class pinning
composes with ER post-merge state).

Credit ER is pinned downstream of Movie and Person: Credit.work references
Movie canonical_ids and Credit.person references Person canonical_ids. After
Movie and Person ER resolve, Credit rows are re-pinned to the merged
canonical_ids (cat 4.2, 4.6).
"""

from __future__ import annotations

from typing import ClassVar

from knot.protocols import DataContext, ERProtocol, ERResult, ScoreColumnMap  # noqa: F401
from tests.fixtures.B2.spec import (
    Credit, Movie, Person,
    imdb_credits, tmdb_credits, wikidata_credits,
)


class ERCreditConfig:
    """Config for the Credit ER impl.

    cross_references: list — classes Credit's ER reads for FK resolution.
    trust_imdb: float — trust for imdb_credits contributions.
    trust_tmdb: float — trust for tmdb_credits contributions.
    trust_wikidata: float — trust for wikidata_credits contributions.
    """

    # Explicit cross-class dependency declarations (per spec-model.md §
    # "Polymorphic references and consumer-declared dependencies").
    cross_references: list = [Movie, Person]
    trust_imdb: float = 0.90
    trust_tmdb: float = 0.60
    trust_wikidata: float = 0.40


class ERCredit(ERProtocol):
    """ER impl for the Credit class.

    Credit deduplication is FK-driven: two Credit rows from different sources
    that reference the same (person_canonical_id, work_canonical_id, role) tuple
    are merged. ER runs after Movie and Person ER so canonical_id lineage is
    available for the FK lookup (cat 4.2, 4.6).

    Designed-in cases:
    - cat 4.1: credits correctly reference resolved Movie + Person canonical_ids
    - cat 4.2: credits referencing pre-merge source_id are re-routed through lineage
    - cat 4.3: wikidata_credits rows referencing Movie/Person that don't exist (orphan)
    - cat 4.6: cross-class pinning — Credit stage pins to Movie+Person run hashes
    - cat 1.5: mixed-case role values (DIRECTOR, Director, dIRECTOR) in tmdb_credits
    """

    Config: ClassVar[type] = ERCreditConfig

    imdb: DataContext = DataContext(
        primary=Credit,
        where=Credit.credit_id.from_source(imdb_credits).is_not_null(),
        # Resolves FK to Movie and Person post-merge canonical_ids:
        project=[Credit.credit_id, Credit.person, Credit.work, Credit.role],
    )
    tmdb: DataContext = DataContext(
        primary=Credit,
        where=Credit.credit_id.from_source(tmdb_credits).is_not_null(),
        project=[Credit.credit_id, Credit.person, Credit.work, Credit.role],
    )
    wikidata: DataContext = DataContext(
        primary=Credit,
        where=Credit.credit_id.from_source(wikidata_credits).is_not_null(),
        project=[Credit.credit_id, Credit.person, Credit.work, Credit.role],
    )

    def score(self, ctx: ERCreditConfig, imdb, tmdb, wikidata) -> ERResult:
        """Score Credit candidate pairs.

        Two credits merge when they share the same (person_canonical_id,
        work_canonical_id, role) tuple. role comparison is case-insensitive
        to handle mixed-case variants (cat 1.5).
        """
        ...
