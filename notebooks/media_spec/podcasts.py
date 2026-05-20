"""Podcasts domain — Podcast + PodcastEpisode + PodcastCredit.

Self-contained sub-spec (``podcasts_subspec``). Shares ``person`` from
person.py via cross-domain import.
"""

from knot import Spec, types
from media_spec.person import person

podcasts_subspec = Spec(identifier_slot_name="canonical_id")

podcast = podcasts_subspec.add_class("Podcast")
podcast.slot("title", types.TEXT, required=True)
podcast.slot("publisher", types.TEXT)
podcast.slot("language", types.TEXT)
podcast.slot("category", types.TEXT)
podcast.slot("start_year", types.INTEGER)
podcast.slot("title_embedding", types.VECTOR(384))

podcast_episode = podcasts_subspec.add_class("PodcastEpisode")
podcast_episode.slot("podcast", podcast)
podcast_episode.slot("episode_number", types.INTEGER)
podcast_episode.slot("title", types.TEXT)
podcast_episode.slot("air_date", types.DATE)
podcast_episode.slot("duration_minutes", types.INTEGER)

podcast_credit = podcasts_subspec.add_class("PodcastCredit")
podcast_credit.slot("role", types.TEXT, required=True)
podcast_credit.slot("podcast", podcast)
podcast_credit.slot("episode", podcast_episode)
podcast_credit.slot("person", person)

_PODCAST_CLASSES = [podcast, podcast_episode, person, podcast_credit]
apple_podcasts = podcasts_subspec.add_source("apple_podcasts")
spotify = podcasts_subspec.add_source("spotify")
listennotes = podcasts_subspec.add_source("listennotes")
for _src in [apple_podcasts, listennotes, spotify]:
    for _cls in _PODCAST_CLASSES:
        _src.bind(_cls)
