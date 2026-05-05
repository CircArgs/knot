"""B2 Movie ER impl — structural scaffolding.

knot.protocols does not exist yet. This file establishes the correct shape
so it compiles once the core is built.

Covers: cat 2.x (ER merge/non-merge), 3.x (trust resolution), 4.2
(cross-source merged identifiers), 4.6 (cross-class pinning).
"""

from __future__ import annotations

from typing import ClassVar

from knot.protocols import DataContext, ERProtocol  # noqa: F401
from knot.metaschema import ResolutionPolicy  # noqa: F401
from tests.fixtures.B2.spec import Movie, imdb_movies, tmdb_movies, wikidata_movies


class ERMovieConfig:
    """Config for the Movie ER impl.

    blocking_threshold: float — Jaccard similarity floor for blocking candidate pairs.
    title_weight: float — weight applied to title similarity in the scoring function.
    year_tolerance: int — max year difference to still score as a candidate pair.
    trust_imdb: float — trust score for imdb_movies contributions.
    trust_tmdb: float — trust score for tmdb_movies contributions.
    trust_wikidata: float — trust score for wikidata_movies contributions.
    """

    blocking_threshold: float = 0.6
    title_weight: float = 0.7
    year_tolerance: int = 1
    trust_imdb: float = 0.91
    trust_tmdb: float = 0.62
    trust_wikidata: float = 0.48


class ERMovie(ERProtocol):
    """ER impl for the Movie class.

    Reads per-source Movie views (DISAGREEMENT_AWARE stance) and produces
    canonical_id assignments. Trust scores drive trust-resolution CTEs
    (ARGMAX_TRUST on title/year; MEDIAN_NUMERIC on runtime_minutes/rating).

    Designed-in cases:
    - ~20 known dupes across IMDB+TMDB+Wikidata (cat 2.1, 2.3)
    - ~10 near-misses that must NOT merge (cat 2.2)
    - Asymmetric coverage: some movies in IMDB+TMDB but absent from Wikidata (cat 2.4)
    - Surfaced conflicts on year/title disagreement (cat 2.5)
    - _user_er_decisions forced merges (cat 2.6) and forced non-merges (cat 2.7)
    - UNIQUE_OR_FAIL violation on imdb_id disagreement (cat 3.6)
    - Cross-source merged identifier lineage (cat 4.2)
    """

    Config: ClassVar[type] = ERMovieConfig

    # DISAGREEMENT_AWARE: source bag is the input — bare Movie.title is MultiValued[str]
    imdb: DataContext = DataContext(
        primary=Movie,
        where=Movie.imdb_id.from_source(imdb_movies).is_not_null(),
    )
    tmdb: DataContext = DataContext(
        primary=Movie,
        where=Movie.imdb_id.from_source(tmdb_movies).is_not_null(),
    )
    wikidata: DataContext = DataContext(
        primary=Movie,
        where=Movie.imdb_id.from_source(wikidata_movies).is_not_null(),
    )

    def score(self, ctx: ERMovieConfig, imdb, tmdb, wikidata) -> None:
        """Score candidate pairs for ER merging.

        Uses title similarity (weighted by Config.title_weight) and year
        proximity (within Config.year_tolerance) for blocking + scoring.
        Trust weights: imdb=0.91, tmdb=0.62, wikidata=0.48.
        """
        ...
