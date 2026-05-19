"""Movies domain — Movie + MovieCredit + 3 sources.

Self-contained sub-spec (``part``) that base.py includes. Declares
the Movie class, the MovieCredit relation, the DirectedMovie virtual
subclass, the year-sanity constraint, and the imdb / tmdb /
rottentomatoes sources with all bindings.

The default __init__.py loads this (along with person) for the v1
demo surface; ``full.py`` adds games/podcasts/tv/webscraped on top.
"""

from knot import Spec, types
from movies_spec.person import person

part = Spec(identifier_slot_name="canonical_id")

# Movie class
movie = part.add_class("Movie")
movie.slot("title", types.TEXT, required=True)
movie.slot("year", types.INTEGER)
movie.slot("director", person)  # FK
movie.slot("runtime_minutes", types.INTEGER)
movie.slot("title_embedding", types.VECTOR(384))

# MovieCredit — Person/Movie via role
movie_credit = part.add_class("MovieCredit")
movie_credit.slot("role", types.TEXT, required=True)
movie_credit.slot("movie", movie)
movie_credit.slot("person", person)

# Virtual subclass + constraint
movie.add_virtual(
    "DirectedMovie",
    where=movie.has_any(movie_credit, role="director"),
)
movie.add_constraint("year_sane", body=movie.col.year >= 1888)

# Sources + their bindings
imdb = part.add_source("imdb")
imdb_movie_b = imdb.bind(movie)
imdb_person_b = imdb.bind(person)
imdb_movie_credit_b = imdb.bind(movie_credit)

tmdb = part.add_source("tmdb")
tmdb_movie_b = tmdb.bind(movie)
tmdb_person_b = tmdb.bind(person)
tmdb_movie_credit_b = tmdb.bind(movie_credit)

rt = part.add_source("rottentomatoes")
rt_movie_b = rt.bind(movie)
rt_person_b = rt.bind(person)
rt_movie_credit_b = rt.bind(movie_credit)
