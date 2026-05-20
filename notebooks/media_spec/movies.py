"""Movies domain — Movie + MovieCredit + 3 sources.

Self-contained sub-spec (``movies_subspec``) that base.py includes. Declares
the Movie class, the MovieCredit relation, the DirectedMovie virtual
subclass, the year-sanity constraint, and the imdb / tmdb /
rottentomatoes sources with all bindings.

The default __init__.py loads this (along with person) for the v1
demo surface; ``full.py`` adds games/podcasts/tv/webscraped on top.
"""

from knot import Spec, this, types
from media_spec.person import person  # cross-domain FK target

# Self-contained sub-spec — base.py composes it in.
movies_subspec = Spec(identifier_slot_name="canonical_id")

# Movie — title + year + director (FK to Person).
movie = movies_subspec.add_class("Movie")
movie.slot("title", types.TEXT, required=True)
movie.slot("year", types.INTEGER)
movie.slot("director", person)                    # FK to Person
movie.slot("runtime_minutes", types.INTEGER)
movie.slot("title_embedding", types.VECTOR(384))  # pgvector + HNSW

# MovieCredit — many-to-many Person↔Movie tagged with a role.
movie_credit = movies_subspec.add_class("MovieCredit")
movie_credit.slot("role", types.TEXT, required=True)
movie_credit.slot("movie", movie)
movie_credit.slot("person", person)

# Virtual subclass — movies that have a "director" credit.
# Correlated-aggregate form: "movies where there exists a MovieCredit
# with .movie pointing back at this Movie AND role == 'director'."
movie.add_virtual(
    "DirectedMovie",
    where=(
        (movie_credit.col.movie == this.Movie)
        & (movie_credit.col.role == "director")
    ).any(),
)
# Cinema started in 1888 — anything earlier is a data bug.
movie.add_constraint("year_sane", body=movie.col.year >= 1888)

# Three sources, each binding the same three classes.
imdb = movies_subspec.add_source("imdb")
imdb_movie_b = imdb.bind(movie)
imdb_person_b = imdb.bind(person)
imdb_movie_credit_b = imdb.bind(movie_credit)

tmdb = movies_subspec.add_source("tmdb")
tmdb_movie_b = tmdb.bind(movie)
tmdb_person_b = tmdb.bind(person)
tmdb_movie_credit_b = tmdb.bind(movie_credit)

rt = movies_subspec.add_source("rottentomatoes")
rt_movie_b = rt.bind(movie)
rt_person_b = rt.bind(person)
rt_movie_credit_b = rt.bind(movie_credit)
