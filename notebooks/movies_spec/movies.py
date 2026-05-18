"""Movies domain — extend Movie, add MovieCredit + virtual + rule,
add tmdb + rottentomatoes sources with all bindings.

base.py only ships ``imdb→Movie``. This module fleshes out the rest
of the movies world: a second/third source so cross-source ER has
something to work with, a Credit relation linking Person to Movie
via a role, a virtual subclass for "movies that have any director
credit", and a sanity constraint.
"""

from knot import types
from movies_spec.base import imdb, movie, person, spec

# Movie slot extensions (runtime + the embedding ER blocks on)
movie.slot("runtime_minutes", types.INTEGER)
movie.slot("title_embedding", types.VECTOR(384))

# MovieCredit — Person/Movie via role
movie_credit = spec.add_class("MovieCredit")
movie_credit.slot("role", types.TEXT, required=True)
movie_credit.slot("movie", movie)
movie_credit.slot("person", person)

# Virtual subclass + constraint
movie.add_virtual(
    "DirectedMovie",
    where=movie.has_any(movie_credit, role="director"),
)
movie.add_constraint("year_sane", body=movie.col.year >= 1888)

# imdb already binds Movie + Person (in base); add MovieCredit
imdb_movie_credit_b = imdb.bind(movie_credit).set_default_weight(0.85)

# tmdb — second movie source
tmdb = spec.add_source("tmdb")
tmdb_movie_b = tmdb.bind(movie).set_default_weight(0.70)
tmdb_person_b = tmdb.bind(person).set_default_weight(0.70)
tmdb_movie_credit_b = tmdb.bind(movie_credit).set_default_weight(0.70)

# rottentomatoes — third movie source
rt = spec.add_source("rottentomatoes")
rt_movie_b = rt.bind(movie).set_default_weight(0.65)
rt_person_b = rt.bind(person).set_default_weight(0.65)
rt_movie_credit_b = rt.bind(movie_credit).set_default_weight(0.65)
