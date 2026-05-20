"""Unit tests for the read substrate (query AST + compile_query)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from knot import types
from knot.ast.select import Layer, OrderBy, Query
from knot.compile.query import compile_query
from knot.spec import Spec


def _make_movie_spec() -> tuple[Spec, OntologyClass]:  # noqa: F821
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT)
    movie.slot("year", types.INTEGER)
    return spec, movie


def test_bare_class_is_select_star():
    spec, movie = _make_movie_spec()
    q = Query(class_name="Movie")
    sql = compile_query(q, spec=spec, schema="knot_data")
    assert sql == "SELECT *\nFROM knot_data.movie_resolved;"


def test_simple_where():
    spec, movie = _make_movie_spec()
    q = movie.resolved.where(movie.col.year >= 1900)
    sql = compile_query(q, spec=spec, schema="knot_data")
    assert "SELECT *" in sql
    assert "FROM knot_data.movie_resolved" in sql
    assert "WHERE knot_data.movie_resolved.year >= 1900" in sql


def test_chain_where_ands():
    spec, movie = _make_movie_spec()
    q = movie.resolved.where(movie.col.year >= 1900).where(movie.col.year <= 2000)
    sql = compile_query(q, spec=spec, schema="knot_data")
    assert "1900" in sql
    assert "2000" in sql
    assert "AND" in sql


def test_order_by_limit_offset():
    spec, movie = _make_movie_spec()
    q = movie.resolved.order_by(movie.col.year, "desc").limit(10).offset(5)
    sql = compile_query(q, spec=spec, schema="knot_data")
    assert "ORDER BY knot_data.movie_resolved.year DESC" in sql
    assert "LIMIT 10" in sql
    assert "OFFSET 5" in sql


def test_multi_order():
    spec, movie = _make_movie_spec()
    q = movie.resolved.order_by(movie.col.year, "desc").order_by(movie.col.title)
    sql = compile_query(q, spec=spec, schema="knot_data")
    assert (
        "ORDER BY knot_data.movie_resolved.year DESC, knot_data.movie_resolved.title ASC"
    ) in sql


def test_projection():
    spec, movie = _make_movie_spec()
    q = movie.resolved.select(movie.col.title, movie.col.year)
    sql = compile_query(q, spec=spec, schema="knot_data")
    assert "SELECT knot_data.movie_resolved.title, knot_data.movie_resolved.year" in sql
    assert "FROM knot_data.movie_resolved" in sql


def test_full_chain():
    spec, movie = _make_movie_spec()
    q = (
        movie.resolved.where(movie.col.year >= 1990)
        .where(movie.col.year <= 2000)
        .order_by(movie.col.year, "desc")
        .limit(50)
        .select(movie.col.title, movie.col.year)
    )
    sql = compile_query(q, spec=spec, schema="knot_data")
    assert "SELECT knot_data.movie_resolved.title, knot_data.movie_resolved.year" in sql
    assert "FROM knot_data.movie_resolved" in sql
    assert "WHERE" in sql
    assert "AND" in sql
    assert "ORDER BY knot_data.movie_resolved.year DESC" in sql
    assert "LIMIT 50" in sql


def test_layer_canonical():
    """Query can target the canonical table instead of _resolved."""
    spec, movie = _make_movie_spec()
    q = replace(movie.resolved.where(movie.col.year == 2020), layer=Layer.CANONICAL)
    sql = compile_query(q, spec=spec, schema="knot_data")
    assert "FROM knot_data.movie\n" in sql
    assert "knot_data.movie.year = 2020" in sql


def test_invalid_order_direction():
    with pytest.raises(ValueError, match="direction"):
        OrderBy(ref=None, direction="sideways")  # type: ignore[arg-type]


def test_fluent_immutability():
    """Each builder call returns a new Query — the original is untouched."""
    spec, movie = _make_movie_spec()
    base = movie.resolved.where(movie.col.year == 2020)
    with_limit = base.limit(10)
    assert base.limit_value is None
    assert with_limit.limit_value == 10


# ---------------------------------------------------------------------------
# FK transparent walks
# ---------------------------------------------------------------------------


def _make_movie_director_spec():
    """Movie with a `director` FK pointing at Person."""
    spec = Spec(identifier_slot_name="canonical_id")
    person = spec.add_class("Person")
    person.slot("name", types.TEXT)
    person.slot("birth_country", types.TEXT)
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT)
    movie.slot("year", types.INTEGER)
    movie.slot("director", person)
    return spec, movie, person


def test_fk_ref_as_value():
    """Movie.col.director used standalone renders as the FK column."""
    from knot.ast.expr import FkRef
    from knot.compile.expr import compile_sql

    spec, movie, person = _make_movie_director_spec()
    ref = movie.col.director
    assert isinstance(ref, FkRef)
    assert ref.target_class_name == "Person"
    sql = compile_sql(ref, schema="knot_data", layer=Layer.RESOLVED)
    assert sql == "knot_data.movie_resolved.director"


def test_fk_walk_in_where():
    spec, movie, person = _make_movie_director_spec()
    q = movie.resolved.where(movie.col.director.birth_country == "USA")
    sql = compile_query(q, spec=spec, schema="knot_data")
    # JOIN to Person, aliased by FK path: movie_director
    assert (
        "JOIN knot_data.person_resolved AS movie_director "
        "ON movie_director.canonical_id = knot_data.movie_resolved.director"
    ) in sql
    # WHERE references the alias, not the full schema-qualified table
    assert "movie_director.birth_country = 'USA'" in sql


def test_fk_walk_in_projection():
    spec, movie, person = _make_movie_director_spec()
    q = movie.resolved.select(movie.col.title, movie.col.director.name)
    sql = compile_query(q, spec=spec, schema="knot_data")
    # FkChainRef renders via alias, not schema-qualified table name
    assert "SELECT knot_data.movie_resolved.title, movie_director.name" in sql
    assert "JOIN knot_data.person_resolved AS movie_director" in sql


def test_fk_walk_in_order_by():
    spec, movie, person = _make_movie_director_spec()
    q = movie.resolved.order_by(movie.col.director.name, "desc")
    sql = compile_query(q, spec=spec, schema="knot_data")
    assert "ORDER BY movie_director.name DESC" in sql
    assert "JOIN knot_data.person_resolved AS movie_director" in sql


def test_fk_walk_dedupe_one_join():
    """Two refs walking the same FK should produce a single JOIN."""
    spec, movie, person = _make_movie_director_spec()
    q = movie.resolved.where(movie.col.director.birth_country == "USA").select(
        movie.col.title, movie.col.director.name
    )
    sql = compile_query(q, spec=spec, schema="knot_data")
    assert sql.count("JOIN knot_data.person_resolved AS movie_director") == 1


def test_full_query_with_fk_walk():
    """The example query: movies with directors, ordered, limited, projected."""
    spec, movie, person = _make_movie_director_spec()
    q = (
        movie.resolved.order_by(movie.col.year, "desc")
        .limit(10)
        .select(movie.col.title, movie.col.director.name)
    )
    sql = compile_query(q, spec=spec, schema="knot_data")
    assert "SELECT knot_data.movie_resolved.title, movie_director.name" in sql
    assert "FROM knot_data.movie_resolved" in sql
    assert "JOIN knot_data.person_resolved AS movie_director" in sql
    assert "ORDER BY knot_data.movie_resolved.year DESC" in sql
    assert "LIMIT 10" in sql


# ---------------------------------------------------------------------------
# Correlation (this) + Aggregates (count/any/all/none)
# ---------------------------------------------------------------------------


def test_this_outside_aggregate_raises():
    """A bare this.X reference outside an Aggregate context is an error."""
    from knot.ast.expr import this
    from knot.compile.expr import compile_sql

    with pytest.raises(ValueError, match="this.Person used outside"):
        compile_sql(this.Person, schema="knot_data", layer=Layer.RESOLVED)


def test_any_existence():
    """Persons who have directed at least one movie since 2020."""
    from knot.ast.expr import this

    spec, movie, person = _make_movie_director_spec()
    q = person.resolved.where((movie.col.director == this.Person).any())
    sql = compile_query(q, spec=spec, schema="knot_data")
    assert "FROM knot_data.person_resolved" in sql
    assert "EXISTS (SELECT 1 FROM knot_data.movie_resolved WHERE" in sql
    assert (
        "knot_data.movie_resolved.director = knot_data.person_resolved.canonical_id"
        in sql
    )


def test_none_non_existence():
    """Persons who have never directed a movie."""
    from knot.ast.expr import this

    spec, movie, person = _make_movie_director_spec()
    q = person.resolved.where((movie.col.director == this.Person).none())
    sql = compile_query(q, spec=spec, schema="knot_data")
    assert "NOT EXISTS (SELECT 1 FROM knot_data.movie_resolved WHERE" in sql


def test_count_threshold():
    """Directors who have directed more than 5 movies."""
    from knot.ast.expr import this

    spec, movie, person = _make_movie_director_spec()
    q = person.resolved.where((movie.col.director == this.Person).count() > 5)
    sql = compile_query(q, spec=spec, schema="knot_data")
    assert "(SELECT COUNT(*) FROM knot_data.movie_resolved WHERE" in sql
    assert "> 5" in sql


def test_count_equals_zero():
    """Equivalent to .none() — count == 0."""
    from knot.ast.expr import this

    spec, movie, person = _make_movie_director_spec()
    q = person.resolved.where((movie.col.director == this.Person).count() == 0)
    sql = compile_query(q, spec=spec, schema="knot_data")
    assert "(SELECT COUNT(*) FROM knot_data.movie_resolved WHERE" in sql
    assert "= 0" in sql


def test_all_universal():
    """Universal quantification via .all() — emitted as NOT EXISTS of counter-example."""
    from knot.ast.expr import this

    spec, movie, person = _make_movie_director_spec()
    # Hypothetical: movies whose director's birth_country == "Japan" — but
    # in *all* form: movies where the director's country is Japan for
    # every Movie row matching the predicate. Contrived since
    # there's only one director per movie, but tests the compile shape.
    q = person.resolved.where(
        (movie.col.director == this.Person).all(movie.col.year >= 1900)
    )
    sql = compile_query(q, spec=spec, schema="knot_data")
    assert "NOT EXISTS (SELECT 1 FROM knot_data.movie_resolved WHERE" in sql
    assert "AND NOT (" in sql
    assert "knot_data.movie_resolved.year >= 1900" in sql


def test_this_wrong_class_raises():
    """this.Movie used inside a Person.where(...) should raise."""
    from knot.ast.expr import this

    spec, movie, person = _make_movie_director_spec()
    q = person.resolved.where((movie.col.director == this.Movie).any())
    with pytest.raises(ValueError, match="doesn't match the enclosing class"):
        compile_query(q, spec=spec, schema="knot_data")


def test_aggregate_invalid_kind():
    from knot.ast.expr import Aggregate, Ref

    with pytest.raises(ValueError, match="kind must be"):
        Aggregate(kind="sum", predicate=Ref(class_name="X", slot_name="y"))


def test_aggregate_all_requires_condition():
    from knot.ast.expr import Aggregate, Ref

    with pytest.raises(ValueError, match="requires a condition"):
        Aggregate(kind="all", predicate=Ref(class_name="X", slot_name="y"))


# ---------------------------------------------------------------------------
# Layer-targeted entry points: cls.resolved / cls.all_sources / cls.from_source
# ---------------------------------------------------------------------------


def test_resolved_targets_resolved_view():
    spec, movie = _make_movie_spec()
    q = movie.resolved
    assert q.layer is Layer.RESOLVED
    sql = q.sql()
    assert "FROM knot_data.movie_resolved" in sql


def test_all_sources_targets_provenance_view():
    spec, movie = _make_movie_spec()
    q = movie.all_sources
    assert q.layer is Layer.ALL_SOURCES
    sql = q.sql()
    assert "FROM knot_data.movie_all_sources" in sql


def test_from_source_targets_bindings_with_filter():
    spec, movie = _make_movie_spec()
    imdb = spec.add_source("imdb")
    imdb.bind(movie)
    q = movie.from_source(imdb)
    assert q.layer is Layer.BINDINGS
    sql = q.sql()
    assert "FROM knot_data.movie_bindings" in sql
    assert "source_name = 'imdb'" in sql


def test_from_source_chains_with_where():
    spec, movie = _make_movie_spec()
    imdb = spec.add_source("imdb")
    imdb.bind(movie)
    q = movie.from_source(imdb).where(movie.col.year >= 2000)
    sql = q.sql()
    assert "source_name = 'imdb'" in sql
    assert "knot_data.movie_bindings.year >= 2000" in sql
    assert "AND" in sql


def test_unresolved_targets_bindings_with_null_canonical():
    spec, movie = _make_movie_spec()
    q = movie.unresolved
    assert q.layer is Layer.BINDINGS
    sql = q.sql()
    assert "FROM knot_data.movie_bindings" in sql
    assert "canonical_id IS NULL" in sql


def test_unresolved_chains_with_where():
    spec, movie = _make_movie_spec()
    imdb = spec.add_source("imdb")
    imdb.bind(movie)
    q = movie.unresolved.where(movie.col.year >= 2000)
    sql = q.sql()
    assert "canonical_id IS NULL" in sql
    assert "knot_data.movie_bindings.year >= 2000" in sql
    assert "AND" in sql


def test_lock_for_update_skip_locked_renders_at_tail():
    """The ER worker shape — claim a batch of unresolved bindings
    atomically without re-processing rows another worker has."""
    spec, movie = _make_movie_spec()
    imdb = spec.add_source("imdb")
    imdb.bind(movie)
    q = movie.unresolved.limit(50).lock("for_update_skip_locked")
    sql = q.sql()
    # Lock clause must come after LIMIT (postgres clause order).
    assert sql.rstrip(";\n ").endswith("FOR UPDATE SKIP LOCKED")
    assert "LIMIT 50" in sql


def test_lock_for_update_basic():
    spec, movie = _make_movie_spec()
    q = movie.resolved.lock("for_update")
    assert "FOR UPDATE" in q.sql()
    assert "SKIP LOCKED" not in q.sql()


def test_lock_for_share():
    spec, movie = _make_movie_spec()
    q = movie.resolved.lock("for_share")
    assert "FOR SHARE" in q.sql()


def test_lock_rejects_invalid_mode():
    spec, movie = _make_movie_spec()
    with pytest.raises(ValueError, match="lock mode must be one of"):
        movie.resolved.lock("for_obliterate")


def test_lock_default_is_none_no_clause():
    spec, movie = _make_movie_spec()
    sql = movie.resolved.sql()
    assert "FOR UPDATE" not in sql
    assert "FOR SHARE" not in sql


def test_col_canonical_id_works_across_all_layers():
    """cls.col.<identifier> (the auto-added identifier slot) must
    render correctly in every layer-targeted query — resolved,
    all_sources, from_source, unresolved. Layer prefix changes;
    the ref shape doesn't."""
    spec, movie = _make_movie_spec()
    imdb = spec.add_source("imdb")
    imdb.bind(movie)
    cases = [
        (movie.resolved, "knot_data.movie_resolved.canonical_id"),
        (movie.all_sources, "knot_data.movie_all_sources.canonical_id"),
        (movie.from_source(imdb), "knot_data.movie_bindings.canonical_id"),
        (movie.unresolved, "knot_data.movie_bindings.canonical_id"),
    ]
    for q, expected_ref in cases:
        sql = q.where(movie.col.canonical_id.in_(["m_a"])).limit(1).sql()
        assert expected_ref in sql, f"missing {expected_ref} in:\n{sql}"


def test_bindings_col_accessor_emits_typed_refs():
    """cls.bindings_col exposes source_name/source_identifier/
    er_metadata/raw_payload as Refs so readers can compose without
    Raw(...)."""
    spec, movie = _make_movie_spec()
    imdb = spec.add_source("imdb")
    imdb.bind(movie)
    q = movie.unresolved.where(movie.bindings_col.source_name == "imdb").select(
        movie.bindings_col.source_identifier, movie.col.title
    )
    sql = q.sql()
    assert "source_name = 'imdb'" in sql
    assert "knot_data.movie_bindings.source_identifier" in sql
    assert "FROM knot_data.movie_bindings" in sql


def test_bindings_col_rejects_typo():
    spec, movie = _make_movie_spec()
    with pytest.raises(KeyError, match="not a bindings-table column"):
        _ = movie.bindings_col.not_a_real_column


def test_bindings_col_rejects_spec_slot_name():
    """Spec-declared slots use cls.col, not cls.bindings_col — keep
    the two surfaces non-overlapping."""
    spec, movie = _make_movie_spec()
    # 'year' is a spec slot on Movie, not a bindings-table column.
    with pytest.raises(KeyError, match="not a bindings-table column"):
        _ = movie.bindings_col.year


def test_abstract_class_blocks_query_entry_points():
    """Virtual/abstract classes can't be queried — they have no relation."""
    from knot.spec import ClassKind

    spec, movie = _make_movie_spec()
    movie.kind = ClassKind.ABSTRACT
    with pytest.raises(ValueError, match="only concrete classes"):
        _ = movie.resolved
    with pytest.raises(ValueError, match="only concrete classes"):
        _ = movie.all_sources
    with pytest.raises(ValueError, match="only concrete classes"):
        _ = movie.from_source(spec.add_source("imdb"))
    with pytest.raises(ValueError, match="only concrete classes"):
        _ = movie.unresolved


# ---------------------------------------------------------------------------
# JOIN aliasing — same-target collision + multi-hop
# ---------------------------------------------------------------------------


def _make_two_fk_spec():
    """Movie with TWO FK slots pointing at the same target (Person):
    ``director`` and ``writer``. Reproduces Alex's reported collision."""
    spec = Spec(identifier_slot_name="canonical_id")
    person = spec.add_class("Person")
    person.slot("name", types.TEXT)
    person.slot("birth_country", types.TEXT)
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT)
    movie.slot("year", types.INTEGER)
    movie.slot("director", person)
    movie.slot("writer", person)
    return spec, movie, person


def _make_multihop_spec():
    """Movie → director (Person) → employer (Company). Two-hop chain."""
    spec = Spec(identifier_slot_name="canonical_id")
    company = spec.add_class("Company")
    company.slot("name", types.TEXT)
    person = spec.add_class("Person")
    person.slot("name", types.TEXT)
    person.slot("employer", company)
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT)
    movie.slot("director", person)
    return spec, movie, person, company


def test_two_fks_same_target_produce_two_joins():
    """Alex's collision case: director AND writer both point at Person.
    A query selecting both must produce TWO JOINs with distinct aliases,
    not a duplicate reference that postgres would reject."""
    spec, movie, person = _make_two_fk_spec()
    q = movie.resolved.select(
        movie.col.title,
        movie.col.director.name,
        movie.col.writer.name,
    )
    sql = compile_query(q, spec=spec, schema="knot_data")

    # Two distinct aliased JOINs against person_resolved
    assert "JOIN knot_data.person_resolved AS movie_director" in sql
    assert "JOIN knot_data.person_resolved AS movie_writer" in sql
    assert sql.count("JOIN knot_data.person_resolved") == 2

    # Each FkChainRef renders via its own alias
    assert "movie_director.name" in sql
    assert "movie_writer.name" in sql

    # Sanity: no un-aliased bare person_resolved references in SELECT/WHERE
    assert "knot_data.person_resolved.name" not in sql


def test_two_fks_same_target_where_and_select():
    """director and writer both used in WHERE and SELECT — still two JOINs."""
    spec, movie, person = _make_two_fk_spec()
    q = movie.resolved.where(
        (movie.col.director.birth_country == "USA")
        & (movie.col.writer.birth_country == "UK")
    ).select(movie.col.title, movie.col.director.name, movie.col.writer.name)
    sql = compile_query(q, spec=spec, schema="knot_data")

    assert sql.count("JOIN knot_data.person_resolved") == 2
    assert "movie_director.birth_country = 'USA'" in sql
    assert "movie_writer.birth_country = 'UK'" in sql
    assert "movie_director.name" in sql
    assert "movie_writer.name" in sql


def test_same_fk_chain_referenced_multiple_times_one_join():
    """The same FK chain (movie.col.director.X) referenced in WHERE,
    ORDER BY, and SELECT produces exactly one JOIN."""
    spec, movie, person = _make_movie_director_spec()
    q = (
        movie.resolved.where(movie.col.director.birth_country == "USA")
        .order_by(movie.col.director.name, "asc")
        .select(movie.col.title, movie.col.director.name)
    )
    sql = compile_query(q, spec=spec, schema="knot_data")

    assert sql.count("JOIN knot_data.person_resolved AS movie_director") == 1
    assert "movie_director.birth_country = 'USA'" in sql
    assert "ORDER BY movie_director.name ASC" in sql
    assert "movie_director.name" in sql


def test_multihop_chain_produces_chained_joins():
    """Movie → director (Person) → employer (Company): two-hop chain
    yields two JOINs, each referencing the previous alias.

    FkChainRef supports multi-hop by constructing the chain tuple directly;
    the compiler walks each prefix to emit one JOIN per hop."""
    from knot.ast.expr import FkChainRef

    spec, movie, person, company = _make_multihop_spec()

    # Build a two-hop chain ref manually:
    # Movie.director → Person, then Person.employer → Company, terminal: name
    chain_ref = FkChainRef(
        source_class="Movie",
        chain=(("director", "Person"), ("employer", "Company")),
        terminal_slot="name",
    )
    q = movie.resolved.select(movie.col.title, chain_ref)
    sql = compile_query(q, spec=spec, schema="knot_data")

    # First hop: movie_director aliases person_resolved, ON references primary table
    assert "JOIN knot_data.person_resolved AS movie_director" in sql
    assert "ON movie_director.canonical_id = knot_data.movie_resolved.director" in sql

    # Second hop: movie_director_employer aliases company_resolved,
    # ON references the previous alias (movie_director)
    assert "JOIN knot_data.company_resolved AS movie_director_employer" in sql
    assert "ON movie_director_employer.canonical_id = movie_director.employer" in sql

    # Terminal column uses the final alias
    assert "movie_director_employer.name" in sql
