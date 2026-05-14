"""Shared fixtures for knot tests."""

import pytest

from knot import Array, Primitive, SourceMap, Spec


@pytest.fixture
def movie_spec() -> Spec:
    """The canonical Movie / Title / Person / Credit / DirectedMovie spec
    used across tests. Exercises: is_a inheritance, abstract classes,
    Array typed types, ClassRef FKs, virtual classes, constraints,
    source bindings + per-slot mappings + accuracy.

    Bodies authored through the semantic builder (no raw SQL)."""
    spec = Spec(id="movies", version="0.1")

    title = spec.add_class("Title", kind="abstract", description="title hierarchy root")
    title.slot("canonical_id", Primitive.TEXT, identifier=True)
    title.slot("name", Primitive.TEXT, required=True)

    movie = spec.add_class("Movie", is_a=title, description="a film")
    movie.slot("year", Primitive.INTEGER)
    movie.slot("runtime_minutes", Primitive.INTEGER)
    movie.slot("genres", Array(of=Primitive.TEXT))

    person = spec.add_class("Person")
    person.slot("canonical_id", Primitive.TEXT, identifier=True)
    person.slot("name", Primitive.TEXT, required=True)

    credit = spec.add_class("Credit")
    credit.slot("canonical_id", Primitive.TEXT, identifier=True)
    credit.slot("role", Primitive.TEXT, required=True)
    credit.fk("movie", to=movie)
    credit.fk("person", to=person)

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
