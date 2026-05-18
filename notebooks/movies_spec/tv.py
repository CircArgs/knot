"""TV domain — Show, Season, TVEpisode, TVCredit.

tvdb is a TV-specific source (binds the 4 TV classes + Person).
tmdb and imdb double as TV sources — they already exist (movies);
we just add their TV class bindings. This is the realistic case
of a source covering multiple domains under the same vendor ID.

MUST import after ``movies`` so ``tmdb`` and ``imdb`` source
objects exist on the spec.
"""

from knot import types
from movies_spec.base import person, spec

show = spec.add_class("Show")
show.slot("title", types.TEXT, required=True)
show.slot("start_year", types.INTEGER)
show.slot("end_year", types.INTEGER)
show.slot("network", types.TEXT)
show.slot("status", types.TEXT)
show.slot("title_embedding", types.VECTOR(384))

season = spec.add_class("Season")
season.slot("show", show)
season.slot("season_number", types.INTEGER)
season.slot("episode_count", types.INTEGER)
season.slot("year", types.INTEGER)

tv_episode = spec.add_class("TVEpisode")
tv_episode.slot("season", season)
tv_episode.slot("episode_number", types.INTEGER)
tv_episode.slot("title", types.TEXT)
tv_episode.slot("air_date", types.DATE)
tv_episode.slot("runtime_minutes", types.INTEGER)

tv_credit = spec.add_class("TVCredit")
tv_credit.slot("role", types.TEXT, required=True)
tv_credit.slot("show", show)
tv_credit.slot("episode", tv_episode)
tv_credit.slot("person", person)

# tvdb is TV-only — binds every TV class + Person.
tvdb = spec.add_source("tvdb")
for _cls in [show, season, tv_episode, tv_credit, person]:
    tvdb.bind(_cls).set_default_weight(0.80)

# tmdb + imdb already exist from movies.py; add TV-class bindings.
# Person is already bound to them via the movies-domain bindings,
# so no extra Person binding here.
_TV_CLASSES = [show, season, tv_episode, tv_credit]
for _src_name, _w in [("tmdb", 0.70), ("imdb", 0.85)]:
    _src = next(s for s in spec.sources if s.name == _src_name)
    for _cls in _TV_CLASSES:
        _src.bind(_cls).set_default_weight(_w)
