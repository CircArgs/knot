"""knot.compile.write — emit_find_er_candidates_sql.

Per unresolved binding in one (source, class), find the nearest
already-stamped neighbor in OTHER sources within %(threshold)s
distance. The substrate-level primitive ER policies build on.
"""

import pytest
import sqlglot

from knot import Spec, types
from knot.compile.write import emit_find_er_candidates_sql


def _make_spec(metric: str = "cosine"):
    spec = Spec(identifier_slot_name="canonical_id", schema="knot_data")
    person = spec.add_class("Person")
    person.slot("name", types.TEXT, required=True)
    person.slot("name_embedding", types.VECTOR(384, metric=metric))
    imdb = spec.add_source("imdb")
    return spec, person, imdb.bind(person)


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_parses_postgres():
    _, _, b = _make_spec()
    sql = emit_find_er_candidates_sql(
        b, identity_slot="name", embedding_slot="name_embedding"
    )
    sqlglot.parse_one(sql, dialect="postgres")


def test_one_threshold_placeholder():
    _, _, b = _make_spec()
    sql = emit_find_er_candidates_sql(
        b, identity_slot="name", embedding_slot="name_embedding"
    )
    assert sql.count("%(threshold)s") == 1
    # threshold is the ONLY placeholder — everything else inlines
    assert "%(" not in sql.replace("%(threshold)s", "")


def test_returns_three_columns():
    _, _, b = _make_spec()
    sql = emit_find_er_candidates_sql(
        b, identity_slot="name", embedding_slot="name_embedding"
    )
    assert "u.source_identifier" in sql
    assert "u.name AS identity_val" in sql
    assert "AS match_canonical_id" in sql


def test_excludes_this_source():
    _, _, b = _make_spec()
    sql = emit_find_er_candidates_sql(
        b, identity_slot="name", embedding_slot="name_embedding"
    )
    # outer scope filters TO this source
    assert "u.source_name = 'imdb'" in sql
    # inner scope filters AWAY from this source
    assert "b.source_name <> 'imdb'" in sql


def test_filters_unresolved_outer_and_stamped_inner():
    _, _, b = _make_spec()
    sql = emit_find_er_candidates_sql(
        b, identity_slot="name", embedding_slot="name_embedding"
    )
    # unresolved on the outer
    assert "u.canonical_id IS NULL" in sql
    # stamped on the inner
    assert "b.canonical_id IS NOT NULL" in sql


def test_skips_blank_identity():
    _, _, b = _make_spec()
    sql = emit_find_er_candidates_sql(
        b, identity_slot="name", embedding_slot="name_embedding"
    )
    # blank/whitespace identity rows are skipped — would otherwise
    # produce sha1("") collisions in the host's mint fallback
    assert "length(trim(u.name::text)) > 0" in sql


# ---------------------------------------------------------------------------
# Operator selection from metric
# ---------------------------------------------------------------------------


def test_cosine_uses_distance_operator():
    _, _, b = _make_spec(metric="cosine")
    sql = emit_find_er_candidates_sql(
        b, identity_slot="name", embedding_slot="name_embedding"
    )
    assert "<=>" in sql


def test_l2_uses_l2_operator():
    _, _, b = _make_spec(metric="l2")
    sql = emit_find_er_candidates_sql(
        b, identity_slot="name", embedding_slot="name_embedding"
    )
    assert "<->" in sql


def test_ip_uses_inner_product_operator():
    _, _, b = _make_spec(metric="ip")
    sql = emit_find_er_candidates_sql(
        b, identity_slot="name", embedding_slot="name_embedding"
    )
    assert "<#>" in sql


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


def test_unknown_identity_slot_raises_keyerror():
    _, _, b = _make_spec()
    with pytest.raises(KeyError):
        emit_find_er_candidates_sql(
            b, identity_slot="bogus", embedding_slot="name_embedding"
        )


def test_unknown_embedding_slot_raises_keyerror():
    _, _, b = _make_spec()
    with pytest.raises(KeyError):
        emit_find_er_candidates_sql(b, identity_slot="name", embedding_slot="bogus")


def test_non_vector_embedding_slot_raises_typeerror():
    _, _, b = _make_spec()
    # name is TEXT, not Vector — should refuse
    with pytest.raises(TypeError, match="must be a Vector slot"):
        emit_find_er_candidates_sql(
            b, identity_slot="name_embedding", embedding_slot="name"
        )


# ---------------------------------------------------------------------------
# binding method ↔ free function parity + public re-export
# ---------------------------------------------------------------------------


def test_binding_method_matches_free_function():
    _, _, b = _make_spec()
    assert b.find_er_candidates_sql(
        identity_slot="name", embedding_slot="name_embedding"
    ) == emit_find_er_candidates_sql(
        b,
        identity_slot="name",
        embedding_slot="name_embedding",
        schema=b._require_spec().schema,
    )


def test_module_reexport():
    from knot.compile import emit_find_er_candidates_sql as ext

    assert ext is emit_find_er_candidates_sql
