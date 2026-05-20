"""Webscraped domain — Mention class + 4 low-trust scraper sources.

Self-contained sub-spec (``webscraped_subspec``). Mention has FKs to Movie (from
movies.py) and Person (from person.py) — cross-domain imports for
the class objects, but Mention itself is owned by this webscraped_subspec.

These sources prove the bronze-layer ``raw_payload jsonb``
preserves everything the source sent — most of what the scraper
ships isn't modeled in the spec.
"""

from knot import Spec, types
from media_spec.movies import movie
from media_spec.person import person

webscraped_subspec = Spec(identifier_slot_name="canonical_id")

mention = webscraped_subspec.add_class("Mention")
mention.slot("subject_movie", movie)
mention.slot("subject_person", person)
mention.slot("text_excerpt", types.TEXT)
mention.slot("sentiment", types.TEXT)

for _src_name in (
    "blog_review_aggregator",
    "fan_wiki",
    "letterboxd_user_reviews",
    "reddit_film_discussion",
):
    webscraped_subspec.add_source(_src_name).bind(mention)
