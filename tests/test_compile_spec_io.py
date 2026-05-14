"""knot.compile.spec_io — save_spec / load_queries / load_spec roundtrip."""

import json
from collections import defaultdict
from typing import Any

import sqlglot

from knot import Array, ClassRef, Primitive, Spec
from knot.compile import load_queries, load_spec, save_spec
from knot.compile.spec_io import type_from_json, type_to_json


def _simulate_inserts(stmts: list[tuple[str, list[Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Replay INSERTs into an in-memory rows-by-table-name dict.

    Each INSERT is parsed via sqlglot to extract its (table, columns); we
    zip with the params to produce a column-keyed dict row. jsonb-shaped
    type columns get decoded back to dicts.
    """
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sql, params in stmts:
        parsed = sqlglot.parse_one(sql, dialect="postgres")
        table_name = parsed.this.this.name
        cols = [e.name for e in parsed.this.expressions]
        assert len(cols) == len(params)
        row = dict(zip(cols, params))
        if "type" in row and isinstance(row["type"], str):
            try:
                row["type"] = json.loads(row["type"])
            except Exception:
                pass
        out[table_name].append(row)
    return dict(out)


# ---------------------------------------------------------------------------
# type_to_json / type_from_json codec
# ---------------------------------------------------------------------------


def test_type_codec_primitive():
    assert type_to_json(Primitive.TEXT) == {"kind": "primitive", "value": "text"}
    assert type_from_json({"kind": "primitive", "value": "text"}, {}) is Primitive.TEXT


def test_type_codec_array_of_primitive():
    t = Array(of=Primitive.INTEGER)
    j = type_to_json(t)
    assert j == {"kind": "array", "of": {"kind": "primitive", "value": "integer"}}
    back = type_from_json(j, {})
    assert isinstance(back, Array)
    assert back.of is Primitive.INTEGER


def test_type_codec_nested_array():
    t = Array(of=Array(of=Primitive.TEXT))
    j = type_to_json(t)
    back = type_from_json(j, {})
    assert isinstance(back, Array)
    assert isinstance(back.of, Array)
    assert back.of.of is Primitive.TEXT


def test_type_codec_classref(movie_spec):
    movie = next(c for c in movie_spec.classes if c.name == "Movie")
    cr = ClassRef(target=movie)
    j = type_to_json(cr)
    assert j == {"kind": "class_ref", "target": "Movie"}
    classes = {"Movie": movie}
    back = type_from_json(j, classes)
    assert isinstance(back, ClassRef)
    assert back.target is movie


def test_type_codec_classref_unknown_target_raises():
    import pytest

    with pytest.raises(KeyError):
        type_from_json({"kind": "class_ref", "target": "Ghost"}, {})


# ---------------------------------------------------------------------------
# save_spec
# ---------------------------------------------------------------------------


def test_save_spec_emits_per_entity_inserts(movie_spec):
    stmts = save_spec(movie_spec)
    rows = _simulate_inserts(stmts)
    assert len(rows["spec"]) == 1
    assert len(rows["classes"]) == 4   # Title, Movie, Person, Credit
    assert len(rows["virtual_classes"]) == 1
    assert len(rows["slots"]) == 11  # Title:2 + Movie:3 + Person:2 + Credit:4
    assert len(rows["sources"]) == 1
    assert len(rows["source_bindings"]) == 1
    assert len(rows["source_binding_mappings"]) == 2
    assert len(rows["constraints"]) == 1


def test_save_spec_writes_kind_as_value_string(movie_spec):
    stmts = save_spec(movie_spec)
    rows = _simulate_inserts(stmts)
    title_row = next(r for r in rows["classes"] if r["name"] == "Title")
    assert title_row["kind"] == "abstract"  # ClassKind.ABSTRACT.value, not the enum
    movie_row = next(r for r in rows["classes"] if r["name"] == "Movie")
    assert movie_row["kind"] == "concrete"


def test_save_spec_writes_severity_as_value_string(movie_spec):
    stmts = save_spec(movie_spec)
    rows = _simulate_inserts(stmts)
    c = rows["constraints"][0]
    assert c["severity"] == "error"


def test_save_spec_schema_kwarg(movie_spec):
    stmts = save_spec(movie_spec, schema="alt")
    for sql, _ in stmts:
        assert "INSERT INTO alt." in sql


# ---------------------------------------------------------------------------
# load_queries
# ---------------------------------------------------------------------------


def test_load_queries_returns_nine_selects():
    qs = load_queries("movies", "0.1")
    assert set(qs.keys()) == {
        "spec",
        "classes",
        "class_mixins",
        "virtual_classes",
        "slots",
        "sources",
        "source_bindings",
        "source_binding_mappings",
        "constraints",
    }


def test_load_queries_all_parse_postgres():
    for sql, _ in load_queries("movies", "0.1").values():
        sqlglot.parse_one(sql, dialect="postgres")


def test_load_queries_schema_kwarg():
    qs = load_queries("m", "v", schema="alt")
    for sql, _ in qs.values():
        assert "FROM alt." in sql


# ---------------------------------------------------------------------------
# Roundtrip: save → simulated rows → load → identity
# ---------------------------------------------------------------------------


def test_roundtrip_preserves_class_count_and_order(movie_spec):
    rows = _simulate_inserts(save_spec(movie_spec))
    restored = load_spec(rows)
    assert [c.name for c in restored.classes] == [c.name for c in movie_spec.classes]


def test_roundtrip_preserves_is_a(movie_spec):
    rows = _simulate_inserts(save_spec(movie_spec))
    restored = load_spec(rows)
    movie = next(c for c in restored.classes if c.name == "Movie")
    assert movie.is_a is not None and movie.is_a.name == "Title"


def test_roundtrip_preserves_slot_inheritance_required(movie_spec):
    rows = _simulate_inserts(save_spec(movie_spec))
    restored = load_spec(rows)
    movie = next(c for c in restored.classes if c.name == "Movie")
    assert movie["name"].required is True  # inherited from Title


def test_roundtrip_preserves_array_type(movie_spec):
    rows = _simulate_inserts(save_spec(movie_spec))
    restored = load_spec(rows)
    movie = next(c for c in restored.classes if c.name == "Movie")
    genres = movie["genres"]
    assert isinstance(genres.type, Array)
    assert genres.type.of is Primitive.TEXT


def test_roundtrip_classref_resolves_to_loaded_class(movie_spec):
    rows = _simulate_inserts(save_spec(movie_spec))
    restored = load_spec(rows)
    restored_movie = next(c for c in restored.classes if c.name == "Movie")
    restored_credit = next(c for c in restored.classes if c.name == "Credit")
    fk = restored_credit["movie"]
    assert isinstance(fk.type, ClassRef)
    # Object identity: the ClassRef.target IS the loaded Movie instance,
    # not a duplicate stand-in.
    assert fk.type.target is restored_movie


def test_roundtrip_preserves_virtual_class_with_is_a(movie_spec):
    rows = _simulate_inserts(save_spec(movie_spec))
    restored = load_spec(rows)
    dm = next(c for c in restored.classes if c.name == "DirectedMovie")
    movie = next(c for c in restored.classes if c.name == "Movie")
    assert dm.is_a is movie
    # Definition is an Expr; rendered SQL should still mention "director"
    assert "director" in dm.definition.to_sql(schema="knot_data", target_suffix="")


def test_roundtrip_preserves_binding_accuracy_and_mappings(movie_spec):
    from knot import SourceMap

    rows = _simulate_inserts(save_spec(movie_spec))
    restored = load_spec(rows)
    b = restored.source_bindings[0]
    assert b.accuracy == 0.85
    assert b.mappings == {
        "year": SourceMap(uses=("release_year",), sql="release_year"),
        "runtime_minutes": SourceMap(
            uses=("runtime",), sql="(regexp_match(runtime, '[0-9]+'))[1]::int"
        ),
    }


def test_roundtrip_preserves_constraint_severity_as_enum(movie_spec):
    from knot import Severity

    rows = _simulate_inserts(save_spec(movie_spec))
    restored = load_spec(rows)
    c = restored.constraints[0]
    assert c.severity is Severity.ERROR


def test_roundtrip_spec_validates_clean(movie_spec):
    rows = _simulate_inserts(save_spec(movie_spec))
    restored = load_spec(rows)
    assert restored.validate() == []


def test_load_spec_requires_exactly_one_spec_row():
    import pytest

    with pytest.raises(ValueError, match="expected 1 spec row"):
        load_spec({"spec": []})
    with pytest.raises(ValueError, match="expected 1 spec row"):
        load_spec({"spec": [{"id": "a", "version": "1"}, {"id": "b", "version": "2"}]})
