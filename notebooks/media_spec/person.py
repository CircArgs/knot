"""Person — shared across every domain.

A Tarantino is the same Person whether he's directing a movie
(MovieCredit), credited on a video-game adaptation (GameCredit),
or guesting on a podcast (PodcastCredit). One class, many
domain-specific edges to it.

Owns the full Person slot set up-front. Domain files import
``person`` from here when they need an FK target.

Module surface
--------------
``part``    a self-contained ``Spec`` holding just Person — the
            APIRouter analogue. Base composes it in via
            ``spec.include(part)``.
``person``  the OntologyClass handle for cross-domain FK references.
"""

from knot import Spec, types

# `part` is an APIRouter-style sub-spec — base.py composes it in.
part = Spec(identifier_slot_name="canonical_id")

# Person is shared across every domain — one class, many edges to it.
person = part.add_class("Person")
person.slot("name", types.TEXT, required=True)
person.slot("birth_country", types.TEXT)
person.slot("birth_year", types.INTEGER)
person.slot("role_description", types.TEXT)      # podcasts feed shape
person.slot("role_summary", types.TEXT)          # games feed shape
person.slot("name_embedding", types.VECTOR(384)) # pgvector + HNSW
