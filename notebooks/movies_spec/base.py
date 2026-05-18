"""Base spec — the v1 the walkthrough starts from.

Foundational entities (Person, Movie + FK) and the foundational
source binding (imdb). No embeddings, no second source — those land
in ``movies_spec.full`` when the migration notebook composes them in.
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
