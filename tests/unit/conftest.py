"""Shared fixtures for knot tests."""

import pytest

from knot import Spec, types


@pytest.fixture
def movie_spec() -> Spec:
    """Canonical Movie / Title / Person / Credit / DirectedMovie spec.
    Exercises: is_a inheritance, abstract classes, Array typed types,
    ClassRef FKs, virtual classes, constraints, per-slot source mappings,
    base + per-slot trust.

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
    credit.slot("movie", movie)
    credit.slot("person", person)

    movie.add_virtual(
        "DirectedMovie",
        where=movie.has_any(credit, role="director"),
    )

    movie.add_constraint("year_sane", body=movie.col.year >= 1888)

    imdb = spec.add_source("imdb")
    binding = imdb.bind(movie)
    binding.set_default_weight(0.85)
    binding.slot(class_slot="year", source_slot="release_year")
    binding.slot(
        class_slot="runtime_minutes",
        source_slot="runtime",
        sql="(regexp_match(runtime, '[0-9]+'))[1]::int",
    )

    return spec
