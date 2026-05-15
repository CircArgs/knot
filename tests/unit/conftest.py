"""Shared fixtures for knot tests."""

import pytest

from knot import SourceMap, Spec, types


@pytest.fixture
def movie_spec() -> Spec:
    """The canonical Movie / Title / Person / Credit / DirectedMovie spec
    used across tests. Exercises: is_a inheritance, abstract classes,
    Array typed types, ClassRef FKs, virtual classes, constraints,
    source bindings + per-slot mappings + accuracy.

    Bodies authored through the semantic builder (no raw SQL)."""
    spec = Spec(id="movies", version="0.1")

    title = spec.add_class("Title", kind="abstract", description="title hierarchy root")
    title.slot("canonical_id", types.TEXT, identifier=True)
    title.slot("name", types.TEXT, required=True)

    movie = spec.add_class("Movie", is_a=title, description="a film")
    movie.slot("year", types.INTEGER)
    movie.slot("runtime_minutes", types.INTEGER)
    movie.slot("genres", types.ARRAY(types.TEXT))

    person = spec.add_class("Person")
    person.slot("canonical_id", types.TEXT, identifier=True)
    person.slot("name", types.TEXT, required=True)

    credit = spec.add_class("Credit")
    credit.slot("canonical_id", types.TEXT, identifier=True)
    credit.slot("role", types.TEXT, required=True)
    credit.slot("movie", types.FK(movie))
    credit.slot("person", types.FK(person))

    spec.add_virtual_class(
        "DirectedMovie",
        base=movie,
        where=movie.has_any(credit, role="director"),
    )

    spec.add_constraint("year_sane", primary=movie, body=movie.col.year >= 1888)

    imdb = spec.add_source("imdb")
    binding = spec.bind(imdb, movie, identifier=movie["canonical_id"], accuracy=0.85)
    binding.map(
        year=SourceMap(uses=("release_year",), sql="release_year"),
        runtime_minutes=SourceMap(
            uses=("runtime",),
            sql="(regexp_match(runtime, '[0-9]+'))[1]::int",
        ),
    )

    return spec
