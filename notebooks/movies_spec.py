"""Shared spec for the walkthrough notebooks.

This is the canonical "movies" spec — Movie + Person + FK +
imdb source bindings. Every notebook in this directory imports
from here rather than rebuilding the spec inline, mirroring how
real deployments work (workers, service API, ER pipeline all
import the same spec module).

Add new classes / sources / constraints here; the notebooks pick
them up automatically.
"""

from knot import Spec, types

spec = Spec(identifier_slot_name="canonical_id")

person = spec.add_class("Person")
person.slot("name", types.TEXT, required=True)
person.slot("birth_country", types.TEXT)

movie = spec.add_class("Movie")
movie.slot("title", types.TEXT, required=True)
movie.slot("year", types.INTEGER)
movie.slot("director", person)  # FK — pass the class

imdb = spec.add_source("imdb")
imdb_person_b = imdb.bind(person).set_default_weight(0.85)
imdb_movie_b = imdb.bind(movie).set_default_weight(0.85)

spec.validate()
