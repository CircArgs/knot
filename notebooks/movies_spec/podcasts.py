"""Podcasts domain — Podcast + PodcastEpisode + PodcastCredit.

Self-contained sub-spec (``part``). Shares ``person`` from
person.py via cross-domain import.
"""

from knot import Spec, types
from movies_spec.person import person

part = Spec(identifier_slot_name="canonical_id")

podcast = part.add_class("Podcast")
podcast.slot("title", types.TEXT, required=True)
podcast.slot("publisher", types.TEXT)
podcast.slot("language", types.TEXT)
podcast.slot("category", types.TEXT)
podcast.slot("start_year", types.INTEGER)
podcast.slot("title_embedding", types.VECTOR(384))

podcast_episode = part.add_class("PodcastEpisode")
podcast_episode.slot("podcast", podcast)
podcast_episode.slot("episode_number", types.INTEGER)
podcast_episode.slot("title", types.TEXT)
podcast_episode.slot("air_date", types.DATE)
podcast_episode.slot("duration_minutes", types.INTEGER)

podcast_credit = part.add_class("PodcastCredit")
podcast_credit.slot("role", types.TEXT, required=True)
podcast_credit.slot("podcast", podcast)
podcast_credit.slot("episode", podcast_episode)
podcast_credit.slot("person", person)

_PODCAST_CLASSES = [podcast, podcast_episode, person, podcast_credit]
apple_podcasts = part.add_source("apple_podcasts")
spotify = part.add_source("spotify")
listennotes = part.add_source("listennotes")
for _src, _w in [(apple_podcasts, 0.80), (listennotes, 0.75), (spotify, 0.70)]:
    for _cls in _PODCAST_CLASSES:
        _src.bind(_cls).set_default_weight(_w)
