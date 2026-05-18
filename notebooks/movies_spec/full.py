# isort: skip_file
"""The 'uber-complete' spec — composes all domain extensions
into the shared ``spec`` object from ``base.py``.

Each domain lives in its own file:

  person.py     cross-domain Person extensions (birth_year, embedding, …)
  movies.py     Movie extensions, MovieCredit, virtual, year_sane,
                tmdb + rottentomatoes sources
  games.py      Studio + Platform + Game + Release + GameCredit,
                igdb + giant_bomb + steam sources
  podcasts.py   Podcast + PodcastEpisode + PodcastCredit,
                apple_podcasts + spotify + listennotes sources
  tv.py         Show + Season + TVEpisode + TVCredit,
                tvdb + reused tmdb/imdb for TV class bindings
  webscraped.py Mention class, 4 low-trust scraper sources

Import order matters where one file's source/class is referenced
by another:

  * person     — earliest; movies/games/podcasts/tv all use the
                 extended Person slots
  * movies     — declares tmdb + imdb_movie_credit_b; tv reuses
                 tmdb + imdb; webscraped's Mention FK→Movie
  * games / podcasts — independent, order doesn't matter
  * tv         — must follow movies (looks up tmdb + imdb sources)
  * webscraped — must follow movies (Mention FK→Movie)

The ``isort: skip_file`` directive at the top preserves this
hand-ordered narrative against import-sorter auto-rewrites.

This module finishes with one ``spec.validate()`` so any cross-
file inconsistency surfaces at import time.
"""

from movies_spec.base import spec

import movies_spec.person  # noqa: E402,F401  ← cross-domain Person slots
import movies_spec.movies  # noqa: E402,F401
import movies_spec.games  # noqa: E402,F401
import movies_spec.podcasts  # noqa: E402,F401
import movies_spec.tv  # noqa: E402,F401  ← needs movies
import movies_spec.webscraped  # noqa: E402,F401  ← needs movies

spec.validate()
