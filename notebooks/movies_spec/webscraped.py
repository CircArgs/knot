"""Webscraped domain — Mention class + low-trust scraper sources.

These sources prove a specific knot claim: **the bronze-layer
``raw_payload jsonb`` preserves everything the source sent, even
when the spec only models a handful of fields.**

Each Mention is "this URL/comment/wiki page mentions this Movie
and/or this Person". The spec models just the FKs + text +
sentiment. Each source ships 5-10 extra fields (subreddit, upvote
ratio, infobox dict, letterboxd diary flags, blog author, etc.)
which all land in raw_payload verbatim — recoverable without
re-fetching.

MUST import after ``movies`` so ``movie`` is on the spec (Mention
has FK(movie)).
"""

from knot import types
from movies_spec.base import movie, person, spec

mention = spec.add_class("Mention")
mention.slot("subject_movie", movie)
mention.slot("subject_person", person)
mention.slot("text_excerpt", types.TEXT)
mention.slot("sentiment", types.TEXT)

for _src_name, _w in [
    ("blog_review_aggregator", 0.45),
    ("fan_wiki", 0.40),
    ("letterboxd_user_reviews", 0.35),
    ("reddit_film_discussion", 0.30),
]:
    spec.add_source(_src_name).bind(mention).set_default_weight(_w)
