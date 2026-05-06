"""C2 Series ER impl — structural scaffolding.

knot.protocols does not exist yet. This file establishes the correct shape
so it compiles once the core is built.

Series is a subclass of Title. Discriminator-routed source: imdb_series rows
carry a `kind` column that routes to Movie or Series (cat 1.14). Wildcard-drop
rows from wikidata_series are excluded (cat 1.15).
"""

from __future__ import annotations

from typing import ClassVar

from knot.protocols import DataContext, ERProtocol, ERResult, ScoreColumnMap  # noqa: F401
from tests.fixtures.C2.spec import Series, imdb_series, tmdb_series, wikidata_series


class ERSeriesConfig:
    """Config for the C2 Series ER impl.

    blocking_threshold: float — title similarity floor.
    trust_imdb: float — trust for imdb_series.
    trust_tmdb: float — trust for tmdb_series.
    trust_wikidata: float — trust for wikidata_series.
    """

    blocking_threshold: float = 0.65
    trust_imdb: float = 0.88
    trust_tmdb: float = 0.60
    trust_wikidata: float = 0.45


class ERSeries(ERProtocol):
    """ER impl for the C2 Series class (Title subclass).

    Designed-in cases:
    - Discriminator routing: imdb_series rows with kind='movie' are excluded
      from Series ER and handed to ERMovie instead (cat 1.14).
    - Wildcard-drop: wikidata_series rows with kind='exclude' are dropped (cat 1.15).
    - ~5 known dupes across IMDB+TMDB+Wikidata (cat 2.1, 2.3).
    - Asymmetric coverage (cat 2.4).
    """

    Config: ClassVar[type] = ERSeriesConfig

    imdb: DataContext = DataContext(
        primary=Series,
        # kind='series' filter happens in the source mapping layer (cat 1.14)
        where=Series.imdb_id.from_source(imdb_series).is_not_null(),
    )
    tmdb: DataContext = DataContext(
        primary=Series,
        where=Series.imdb_id.from_source(tmdb_series).is_not_null(),
    )
    wikidata: DataContext = DataContext(
        primary=Series,
        # wildcard-drop rows already excluded by source mapping (cat 1.15)
        where=Series.imdb_id.from_source(wikidata_series).is_not_null(),
    )

    def score(self, ctx: ERSeriesConfig, imdb, tmdb, wikidata) -> ERResult:
        """Score Series candidate pairs; title similarity primary signal."""
        ...
