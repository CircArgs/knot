"""B2 Person ER impl — structural scaffolding.

knot.protocols does not exist yet. This file establishes the correct shape
so it compiles once the core is built.

Covers: cat 2.x (ER merge), 1.6 (Unicode diacritics), 1.7 (non-Latin scripts),
1.8 (date-format drift), 4.2 (cross-source merged identifiers).
"""

from __future__ import annotations

from typing import ClassVar

from knot.protocols import DataContext, ERProtocol, ERResult, ScoreColumnMap  # noqa: F401
from tests.fixtures.B2.spec import Person, imdb_persons, tmdb_persons


class ERPersonConfig:
    """Config for the Person ER impl.

    name_similarity_threshold: float — minimum fuzzy-name similarity to block.
    use_wikidata_id: bool — if True, wikidata_id exact-match is a forced merge signal.
    trust_imdb: float — trust score for imdb_persons contributions.
    trust_tmdb: float — trust score for tmdb_persons contributions.
    """

    name_similarity_threshold: float = 0.85
    use_wikidata_id: bool = True
    trust_imdb: float = 0.88
    trust_tmdb: float = 0.65


class ERPerson(ERProtocol):
    """ER impl for the Person class.

    Reads per-source Person views (DISAGREEMENT_AWARE) and produces canonical_id
    assignments. wikidata_id exact-match is the primary deduplication signal when
    available; name+birthdate similarity is the fallback.

    Designed-in cases:
    - Unicode name matching: Søren Kierkegaard, Alejandro González Iñárritu (cat 1.6)
    - Non-Latin names: Kurosawa Akira (CJK), Andrei Tarkovsky (Cyrillic) (cat 1.7)
    - Birthdate format drift: IMDB='1943-07-14', TMDB='Jul 14 1943' (cat 1.8)
    - ~20 known dupes across IMDB+TMDB (cat 2.1, 2.3)
    - ~5 near-misses (same name, different entity) (cat 2.2)
    - Cross-source merged identifiers via wikidata_id (cat 4.2)
    """

    Config: ClassVar[type] = ERPersonConfig

    imdb: DataContext = DataContext(
        primary=Person,
        where=Person.imdb_id.from_source(imdb_persons).is_not_null(),
    )
    tmdb: DataContext = DataContext(
        primary=Person,
        where=Person.imdb_id.from_source(tmdb_persons).is_not_null(),
    )

    def score(self, ctx: ERPersonConfig, imdb, tmdb) -> ERResult:
        """Score Person candidate pairs.

        Primary signal: wikidata_id exact match (forced merge when
        Config.use_wikidata_id=True). Fallback: fuzzy name similarity
        above Config.name_similarity_threshold + birthdate within 1 year.
        """
        ...
