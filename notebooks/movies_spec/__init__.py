# isort: skip_file
"""Shared spec package for the walkthrough notebooks.

This file is the FastAPI ``main.py`` analogue — it creates the
top-level ``spec`` (via ``base.py``) and includes the v1 domain
parts (``person`` + ``movies``).

Additional domains (games, podcasts, tv, webscraped) are opt-in:
``import movies_spec.full`` composes them onto the same spec.
That import IS the migration story (see notebook 03_migrate).
"""

from movies_spec.base import spec
from movies_spec import person as _person
from movies_spec import movies as _movies

spec.include(_person.part)
spec.include(_movies.part)

# Re-export the v1 handles every notebook actually grabs.
from movies_spec.person import person  # noqa: E402,F401
from movies_spec.movies import (  # noqa: E402,F401
    imdb,
    imdb_movie_b,
    imdb_movie_credit_b,
    imdb_person_b,
    movie,
    movie_credit,
    rt,
    rt_movie_b,
    rt_movie_credit_b,
    rt_person_b,
    tmdb,
    tmdb_movie_b,
    tmdb_movie_credit_b,
    tmdb_person_b,
)
