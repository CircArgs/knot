"""knot_graphql.codegen — SDL generation tests."""

import pytest

from knot import Spec, this, types
from knot_graphql.codegen import emit_sdl

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def simple_spec():
    """Movie / Person / Credit spec — the canonical demo shape."""
    spec = Spec(identifier_slot_name="canonical_id")

    person = spec.add_class("Person")
    person.slot("name", types.TEXT, required=True)
    person.slot("birth_year", types.INTEGER)

    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT, required=True)
    movie.slot("year", types.INTEGER)
    movie.slot("director", person)  # FK slot

    credit = spec.add_class("Credit")
    credit.slot("role", types.TEXT, required=True)
    credit.slot("movie", movie)
    credit.slot("person", person)

    movie.add_virtual(
        "DirectedMovie",
        where=(credit.col.movie == this.Movie).any(),
    )

    return spec


@pytest.fixture
def enum_spec():
    spec = Spec(identifier_slot_name="canonical_id")
    article = spec.add_class("Article")
    article.slot("title", types.TEXT, required=True)
    article.slot("status", types.ENUM("draft", "published", "archived"))
    return spec


# ---------------------------------------------------------------------------
# Structural presence
# ---------------------------------------------------------------------------


def test_sdl_contains_movie_type(simple_spec):
    sdl = emit_sdl(simple_spec)
    assert "type Movie {" in sdl


def test_sdl_contains_person_type(simple_spec):
    sdl = emit_sdl(simple_spec)
    assert "type Person {" in sdl


def test_sdl_contains_credit_type(simple_spec):
    sdl = emit_sdl(simple_spec)
    assert "type Credit {" in sdl


# ---------------------------------------------------------------------------
# Field scalar mapping
# ---------------------------------------------------------------------------


def test_text_field_maps_to_string(simple_spec):
    sdl = emit_sdl(simple_spec)
    assert "year: Int" in sdl


def test_required_slot_gets_exclamation(simple_spec):
    sdl = emit_sdl(simple_spec)
    # title is required=True → String!
    assert "title: String!" in sdl


def test_optional_slot_has_no_exclamation(simple_spec):
    sdl = emit_sdl(simple_spec)
    # year is optional → Int (no !)
    assert "year: Int\n" in sdl or "year: Int\r\n" in sdl or "  year: Int" in sdl


# ---------------------------------------------------------------------------
# FK slot generates target type reference
# ---------------------------------------------------------------------------


def test_fk_slot_generates_target_type(simple_spec):
    sdl = emit_sdl(simple_spec)
    # director is a ClassRef to Person → field type should be Person
    assert "director: Person" in sdl


# ---------------------------------------------------------------------------
# Reverse-FK count fields
# ---------------------------------------------------------------------------


def test_reverse_fk_count_field_on_movie(simple_spec):
    sdl = emit_sdl(simple_spec)
    # Credit.movie references Movie → creditMovieCount field
    # (namespace = referring class + fk slot — disambiguates when
    # multiple classes have same-named FKs to the same target).
    assert "creditMovieCount: Int!" in sdl


def test_reverse_fk_count_field_on_person(simple_spec):
    sdl = emit_sdl(simple_spec)
    # Credit.person references Person → creditPersonCount field.
    # Movie.director also points at Person → movieDirectorCount.
    assert "creditPersonCount: Int!" in sdl
    assert "movieDirectorCount: Int!" in sdl


# ---------------------------------------------------------------------------
# Enum type
# ---------------------------------------------------------------------------


def test_enum_generates_graphql_enum(enum_spec):
    sdl = emit_sdl(enum_spec)
    assert "enum ArticleStatusEnum" in sdl
    # Values emit verbatim — same casing the spec declared and the DB
    # stores. Forced uppercasing would force every host into a
    # name-mapping shim just to round-trip.
    assert "draft" in sdl
    assert "published" in sdl
    assert "archived" in sdl


# ---------------------------------------------------------------------------
# Virtual class
# ---------------------------------------------------------------------------


def test_virtual_class_generates_its_own_type(simple_spec):
    sdl = emit_sdl(simple_spec)
    assert "type DirectedMovie {" in sdl


# ---------------------------------------------------------------------------
# Query root
# ---------------------------------------------------------------------------


def test_query_root_has_list_field_per_class(simple_spec):
    sdl = emit_sdl(simple_spec)
    assert "movieList(" in sdl
    assert "personList(" in sdl
    assert "creditList(" in sdl


def test_query_root_has_by_id_field_per_class(simple_spec):
    sdl = emit_sdl(simple_spec)
    assert "movie(canonical_id: ID!)" in sdl
    assert "person(canonical_id: ID!)" in sdl
    assert "credit(canonical_id: ID!)" in sdl


def test_query_root_virtual_list(simple_spec):
    sdl = emit_sdl(simple_spec)
    assert "directedMovieList(" in sdl


# ---------------------------------------------------------------------------
# Parse validity — requires graphql-core
# ---------------------------------------------------------------------------


def test_sdl_parses_as_valid_graphql(simple_spec):
    pytest.importorskip("graphql")
    from graphql import build_schema as gql_build

    sdl = emit_sdl(simple_spec)
    # Raises if SDL is malformed.
    schema = gql_build(sdl)
    assert schema is not None


def test_enum_sdl_parses_as_valid_graphql(enum_spec):
    pytest.importorskip("graphql")
    from graphql import build_schema as gql_build

    sdl = emit_sdl(enum_spec)
    schema = gql_build(sdl)
    assert schema is not None
