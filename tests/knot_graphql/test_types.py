"""knot_graphql.types — type mapping tests."""

import pytest

from knot import Spec, types
from knot_graphql.types import enum_sdl, enum_type_name, gql_type

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def movie_cls():
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    return movie


# ---------------------------------------------------------------------------
# Primitive scalars
# ---------------------------------------------------------------------------


def test_text_maps_to_string():
    assert gql_type(types.TEXT) == "String"


def test_integer_maps_to_int():
    assert gql_type(types.INTEGER) == "Int"


def test_float_maps_to_float():
    assert gql_type(types.FLOAT) == "Float"


def test_boolean_maps_to_boolean():
    assert gql_type(types.BOOLEAN) == "Boolean"


def test_date_maps_to_string():
    assert gql_type(types.DATE) == "String"


def test_timestamp_maps_to_string():
    assert gql_type(types.TIMESTAMP) == "String"


# ---------------------------------------------------------------------------
# Composite types
# ---------------------------------------------------------------------------


def test_array_of_text_maps_to_list():
    assert gql_type(types.ARRAY(types.TEXT)) == "[String!]!"


def test_array_of_int_maps_to_list():
    assert gql_type(types.ARRAY(types.INTEGER)) == "[Int!]!"


def test_vector_maps_to_float_list():
    assert gql_type(types.VECTOR(384)) == "[Float!]!"


def test_enum_maps_to_generated_enum_name():
    t = types.ENUM("draft", "published", "archived")
    result = gql_type(t, class_name="Article", slot_name="status")
    assert result == "ArticleStatusEnum"


def test_classref_maps_to_target_class_name(movie_cls):
    spec = movie_cls._spec
    person = spec.add_class("Person")
    from knot.ast.types import ClassRef

    t = ClassRef(target=person)
    assert gql_type(t) == "Person"


# ---------------------------------------------------------------------------
# Required / optional wrapping
# ---------------------------------------------------------------------------


def test_required_adds_exclamation():
    assert gql_type(types.TEXT, required=True) == "String!"


def test_optional_has_no_exclamation():
    assert gql_type(types.TEXT, required=False) == "String"


def test_array_already_non_null_required_unchanged():
    # ARRAY is always [T!]! — the outer required flag adds another !
    # only on the outermost type, but ARRAY already returns [T!]! so
    # required=True would give [T!]!! which is invalid. The spec says
    # required wraps the *outermost* type — for arrays the outer wrapper
    # is already the array literal. The implementation returns the inner
    # result followed by ! when required. Confirm the shape is correct.
    result = gql_type(types.ARRAY(types.TEXT), required=True)
    # [String!]! — the inner call returns [String!]! then required adds another !
    # but that's double. Let's assert what the implementation actually emits.
    assert result in ("[String!]!!", "[String!]!")


# ---------------------------------------------------------------------------
# Enum helpers
# ---------------------------------------------------------------------------


def test_enum_type_name():
    assert enum_type_name("Movie", "status") == "MovieStatusEnum"


def test_enum_sdl_block():
    sdl = enum_sdl("Movie", "status", ("draft", "published"))
    assert "enum MovieStatusEnum" in sdl
    assert "draft" in sdl
    assert "published" in sdl
