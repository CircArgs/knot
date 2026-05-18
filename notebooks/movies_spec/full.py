"""Uber-complete spec extensions — composed in by import.

Importing this module *mutates the shared spec object* from
``movies_spec.base`` to add:

  - ``Movie.title_embedding``: a ``VECTOR(384)`` slot. Lowers to a
    ``vector(384)`` column on both the canonical and bindings
    tables plus an HNSW index per table; the ER notebook fills
    the column and uses the index for k-NN candidate generation.

  - ``tmdb`` source + ``tmdb_movie_b`` / ``tmdb_person_b`` bindings.
    Lower weight than imdb (0.70 vs 0.85) so the resolver argmax
    has a clear winner when both sources agree-but-disagree.

This *is* the migration story: the team adds a file, the file
contributes to the same spec, ``Spec.ddl()`` reflects the new shape,
the migration tool reconciles. There's no v1/v2 fork — just one
spec that grows.
"""

from knot import types
from movies_spec.base import movie, person, spec

# Extend the existing Movie class with an embedding slot. The slot
# lands on every materialization (canonical table + bindings table +
# resolved view + all-sources view); the HNSW index is auto-attached
# in DDL emission.
movie.slot("title_embedding", types.VECTOR(384))

# Second source. Identical binding shape to imdb — the spec doesn't
# distinguish "primary" from "secondary" sources, that's a host
# policy concern. Lower default_weight signals "trust imdb more"
# to the resolver.
tmdb = spec.add_source("tmdb")
tmdb_person_b = tmdb.bind(person).set_default_weight(0.70)
tmdb_movie_b = tmdb.bind(movie).set_default_weight(0.70)

spec.validate()
