"""C2 Person ER impl — structural scaffolding.

knot.protocols does not exist yet. This file establishes the correct shape
so it compiles once the core is built.

Three sources: IMDB, TMDB, Wikidata. Extends B2 Person ER with Wikidata.
Seeded with Unicode (cat 1.6, 1.7), date-format drift (cat 1.8),
encoding variants (cat 1.10), cross-system identifier merging (cat 8.6).
"""

from __future__ import annotations

from typing import ClassVar

from knot.protocols import DataContext, ERProtocol, ERResult, ScoreColumnMap  # noqa: F401
from knot_demo_c2.spec import Person, imdb_persons, tmdb_persons, wikidata_persons


class ERPersonConfig:
    """Config for the C2 Person ER impl.

    name_similarity_threshold: float — minimum fuzzy-name similarity.
    use_wikidata_id: bool — wikidata_id exact-match as forced merge signal.
    trust_imdb: float — trust for imdb_persons.
    trust_tmdb: float — trust for tmdb_persons.
    trust_wikidata: float — trust for wikidata_persons.
    """

    name_similarity_threshold: float = 0.85
    use_wikidata_id: bool = True
    trust_imdb: float = 0.88
    trust_tmdb: float = 0.65
    trust_wikidata: float = 0.72


class ERPerson(ERProtocol):
    """ER impl for the C2 Person class.

    Designed-in cases:
    - Unicode diacritics: Søren, Peña, Wachowski (cat 1.6)
    - Non-Latin scripts: CJK, Cyrillic, Arabic names (cat 1.7)
    - Date-format drift: '1943-07-14' vs 'Jul 14 1943' vs '14/7/1943' (cat 1.8)
    - Encoding variants: en-dash in names (cat 1.10)
    - ~20 known dupes across three sources (cat 2.1, 2.3)
    - ~5 near-misses (same name, different person) (cat 2.2)
    - Cross-system identifier merging via wikidata_id (cat 8.6)
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
    wikidata: DataContext = DataContext(
        primary=Person,
        where=Person.imdb_id.from_source(wikidata_persons).is_not_null(),
    )

    def score(self, ctx: ERPersonConfig, imdb, tmdb, wikidata) -> ERResult:
        """Score Person candidate pairs across three sources.

        Primary: wikidata_id exact match. Fallback: Unicode-aware
        name similarity above Config.name_similarity_threshold.
        """
        ...
