"""Podcasts domain — Podcast, PodcastEpisode, PodcastCredit.

Shares Person. Three aggregator sources, each with its own
spelling for shared facts (language code, category vocabulary,
episode duration off-by-one) — the resolver's job is to fuse
them per the per-source weights.
"""

from knot import types
from movies_spec.base import person, spec

podcast = spec.add_class("Podcast")
podcast.slot("title", types.TEXT, required=True)
podcast.slot("publisher", types.TEXT)
podcast.slot("language", types.TEXT)
podcast.slot("category", types.TEXT)
podcast.slot("start_year", types.INTEGER)
podcast.slot("title_embedding", types.VECTOR(384))

podcast_episode = spec.add_class("PodcastEpisode")
podcast_episode.slot("podcast", podcast)
podcast_episode.slot("episode_number", types.INTEGER)
podcast_episode.slot("title", types.TEXT)
podcast_episode.slot("air_date", types.DATE)
podcast_episode.slot("duration_minutes", types.INTEGER)

podcast_credit = spec.add_class("PodcastCredit")
podcast_credit.slot("role", types.TEXT, required=True)
podcast_credit.slot("podcast", podcast)
podcast_credit.slot("episode", podcast_episode)
podcast_credit.slot("person", person)

_PODCAST_CLASSES = [podcast, podcast_episode, person, podcast_credit]
apple_podcasts = spec.add_source("apple_podcasts")
spotify = spec.add_source("spotify")
listennotes = spec.add_source("listennotes")
for _src, _w in [(apple_podcasts, 0.80), (listennotes, 0.75), (spotify, 0.70)]:
    for _cls in _PODCAST_CLASSES:
        _src.bind(_cls).set_default_weight(_w)
