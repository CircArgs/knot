"""C2 Movie ER impl — structural scaffolding.

knot.protocols does not exist yet. This file establishes the correct shape
so it compiles once the core is built.

Movie is a subclass of Title. C2 adds: discriminator-routed sources (cat 1.14),
three-source ER (IMDB+TMDB+Wikidata), encoding variants (cat 1.10).
"""

from __future__ import annotations

from typing import ClassVar

from knot.protocols import DataContext, ERProtocol, ERResult, ScoreColumnMap  # noqa: F401
from tests.fixtures.C2.spec import Movie, imdb_movies, tmdb_movies, wikidata_movies


class ERMovieConfig:
    """Config for the C2 Movie ER impl.

    blocking_threshold: float — Jaccard similarity floor for candidate pairs.
    title_weight: float — weight for title similarity in scoring.
    year_tolerance: int — max year diff for candidate pairs.
    trust_imdb: float — trust score for imdb_movies.
    trust_tmdb: float — trust score for tmdb_movies.
    trust_wikidata: float — trust score for wikidata_movies.
    """

    blocking_threshold: float = 0.6
    title_weight: float = 0.7
    year_tolerance: int = 1
    trust_imdb: float = 0.91
    trust_tmdb: float = 0.62
    trust_wikidata: float = 0.48


class ERMovie(ERProtocol):
    """ER impl for the C2 Movie class (Title subclass).

    Reads three per-source Movie views (DISAGREEMENT_AWARE). Seeded cases:
    - ~20 known dupes across IMDB+TMDB+Wikidata (cat 2.1, 2.3)
    - ~5 near-misses (same title, different entity) (cat 2.2)
    - Asymmetric coverage: some Movies absent from Wikidata (cat 2.4)
    - Surfaced conflicts (cat 2.5)
    - Encoding variants in wikidata_movies: en-dash vs em-dash vs hyphen (cat 1.10)
    - UNIQUE_OR_FAIL violation on imdb_id disagreement (cat 3.6)
    """

    Config: ClassVar[type] = ERMovieConfig

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

    def score(self, ctx: ERMovieConfig, imdb, tmdb, wikidata) -> ERResult:
        """Score Movie candidate pairs across three sources."""
        ...
