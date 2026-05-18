"""TV domain — Show + Season + TVEpisode + TVCredit.

Self-contained sub-spec (``part``). tvdb is TV-only and lives in
``part``. tmdb + imdb already exist on the parent spec via
movies.py — we import the source handles and call ``.bind()`` on
them; those bindings land on the parent's ``source_bindings`` list
directly (the bind side-effect resolves through the source's
``_spec`` pointer, which is the parent by the time this module
loads).

This is the realistic case of one source vendor covering multiple
domains under one ID — same shape a real CKG would handle.

Requires: ``movies.part`` already included in the parent spec.
``full.py`` orders the includes correctly.
"""

from knot import Spec, types
from movies_spec.movies import imdb, tmdb
from movies_spec.person import person

part = Spec(identifier_slot_name="canonical_id")

show = part.add_class("Show")
show.slot("title", types.TEXT, required=True)
show.slot("start_year", types.INTEGER)
show.slot("end_year", types.INTEGER)
show.slot("network", types.TEXT)
show.slot("status", types.TEXT)
show.slot("title_embedding", types.VECTOR(384))

season = part.add_class("Season")
season.slot("show", show)
season.slot("season_number", types.INTEGER)
season.slot("episode_count", types.INTEGER)
season.slot("year", types.INTEGER)

tv_episode = part.add_class("TVEpisode")
tv_episode.slot("season", season)
tv_episode.slot("episode_number", types.INTEGER)
tv_episode.slot("title", types.TEXT)
tv_episode.slot("air_date", types.DATE)
tv_episode.slot("runtime_minutes", types.INTEGER)

tv_credit = part.add_class("TVCredit")
tv_credit.slot("role", types.TEXT, required=True)
tv_credit.slot("show", show)
tv_credit.slot("episode", tv_episode)
tv_credit.slot("person", person)

# tvdb — TV-only source, owned by this part
tvdb = part.add_source("tvdb")
for _cls in [show, season, tv_episode, tv_credit, person]:
    tvdb.bind(_cls).set_default_weight(0.80)

# Reuse tmdb + imdb for TV bindings. Their .bind() lands on the
# already-included parent spec's source_bindings list.
_TV_CLASSES = [show, season, tv_episode, tv_credit]
for _src, _w in [(tmdb, 0.70), (imdb, 0.85)]:
    for _cls in _TV_CLASSES:
        _src.bind(_cls).set_default_weight(_w)
