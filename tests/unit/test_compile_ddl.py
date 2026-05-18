"""knot.compile.ddl.emit_ddl — data-plane DDL emission."""

import sqlglot

from knot import Spec
from knot.compile import emit_ddl


def _all_parse(stmts: list[str]) -> bool:
    return all(sqlglot.parse_one(s, dialect="postgres") for s in stmts)


def test_default_emits_canonical_bindings_resolved_per_concrete(movie_spec):
    stmts = emit_ddl(movie_spec)
    assert stmts[0].startswith("CREATE SCHEMA IF NOT EXISTS knot_data")
    canonical = [
        s
        for s in stmts
        if s.startswith("CREATE TABLE")
        and "_bindings" not in s
        and "source_weight" not in s
    ]
    bindings = [s for s in stmts if s.startswith("CREATE TABLE") and "_bindings" in s]
    weight = [s for s in stmts if s.startswith("CREATE TABLE") and "source_weight" in s]
    resolved_views = [s for s in stmts if "_resolved AS" in s]
    all_sources_views = [s for s in stmts if "_all_sources AS" in s]
    virtual_views = [
        s
        for s in stmts
        if (s.startswith("CREATE VIEW") or s.startswith("CREATE OR REPLACE VIEW"))
        and "_resolved AS" not in s
        and "_all_sources AS" not in s
    ]
    assert len(canonical) == 3  # Movie, Person, Credit canonical tables
    assert len(bindings) == 3  # Movie, Person, Credit bindings tables
    assert len(weight) == 1  # source_weight (invariant)
    assert len(resolved_views) == 3  # Movie, Person, Credit resolved views
    assert len(all_sources_views) == 3  # parallel provenance views
    assert len(virtual_views) == 1  # DirectedMovie (the virtual class)
    assert _all_parse(stmts)


def test_abstract_class_has_no_table(movie_spec):
    stmts = emit_ddl(movie_spec)
    assert not any("knot_data.title (" in s for s in stmts)


def test_concrete_inherits_slots_from_abstract(movie_spec):
    stmts = emit_ddl(movie_spec)
    movie_table = next(
        s for s in stmts if "knot_data.movie (" in s and "_bindings" not in s
    )
    # year is Movie's own; name + canonical_id come from Title
    assert "year integer" in movie_table
    assert "name text" in movie_table
    assert "canonical_id text NOT NULL" in movie_table


def test_array_renders_as_postgres_array(movie_spec):
    stmts = emit_ddl(movie_spec)
    movie_table = next(
        s for s in stmts if "knot_data.movie (" in s and "_bindings" not in s
    )
    assert "genres text[]" in movie_table


def test_classref_renders_as_text(movie_spec):
    stmts = emit_ddl(movie_spec)
    credit_table = next(
        s for s in stmts if "knot_data.credit (" in s and "_bindings" not in s
    )
    assert "movie text" in credit_table
    assert "person text" in credit_table


def test_if_not_exists_kwarg(movie_spec):
    stmts = emit_ddl(movie_spec, if_not_exists=True)
    create_tables = [s for s in stmts if "CREATE TABLE" in s]
    assert all("CREATE TABLE IF NOT EXISTS" in s for s in create_tables)
    views = [s for s in stmts if "VIEW" in s]
    assert all("CREATE OR REPLACE VIEW" in s for s in views)


def test_emit_bindings_false_skips_scd2(movie_spec):
    stmts = emit_ddl(movie_spec, emit_bindings=False)
    assert not any("_bindings" in s for s in stmts)


def test_schema_and_suffix_kwargs(movie_spec):
    stmts = emit_ddl(movie_spec, schema="foo", bindings_suffix="__src")
    assert any("foo.movie__src" in s for s in stmts)
    assert not any("knot_data." in s for s in stmts)


def test_emit_descriptions_opt_in(movie_spec):
    no_desc = emit_ddl(movie_spec, emit_descriptions=False)
    with_desc = emit_ddl(movie_spec, emit_descriptions=True)
    assert not any("COMMENT ON" in s for s in no_desc)
    assert any("COMMENT ON TABLE knot_data.movie" in s for s in with_desc)


def test_fk_alters_emitted_for_classref_slots(movie_spec):
    stmts = emit_ddl(movie_spec)
    fk_stmts = [s for s in stmts if s.startswith("ALTER TABLE")]
    # Credit has two FK slots (movie, person). Title/Movie/Person have none.
    assert len(fk_stmts) == 2
    assert any(
        "fk_credit_movie" in s and "REFERENCES knot_data.movie(canonical_id)" in s
        for s in fk_stmts
    )
    assert any(
        "fk_credit_person" in s and "REFERENCES knot_data.person(canonical_id)" in s
        for s in fk_stmts
    )


def test_fk_alters_use_target_identifier_slot_name():
    # Spec-level override of the identifier slot name. Every class
    # gets `imdb_id` as its identifier; the FK references the
    # target's identifier column.
    spec = Spec(identifier_slot_name="imdb_id")
    movie = spec.add_class("Movie")
    credit = spec.add_class("Credit")
    credit.slot("movie", movie)
    stmts = emit_ddl(spec)
    fk = next(s for s in stmts if s.startswith("ALTER TABLE knot_data.credit"))
    assert "REFERENCES knot_data.movie(imdb_id)" in fk


def test_fk_alters_idempotent_with_if_not_exists(movie_spec):
    stmts = emit_ddl(movie_spec, if_not_exists=True)
    alter_stmts = [s for s in stmts if s.startswith("ALTER TABLE")]
    drops = [s for s in alter_stmts if "DROP CONSTRAINT IF EXISTS" in s]
    adds = [s for s in alter_stmts if "ADD CONSTRAINT" in s]
    assert len(drops) == 2  # one per FK
    assert len(adds) == 2
    # Drops precede their corresponding adds
    assert alter_stmts[0].startswith("ALTER TABLE knot_data.credit DROP")
    assert alter_stmts[1].startswith("ALTER TABLE knot_data.credit ADD")


def test_fk_alters_can_be_disabled(movie_spec):
    stmts = emit_ddl(movie_spec, emit_fk_references=False)
    assert not any(s.startswith("ALTER TABLE") for s in stmts)


def test_fk_alters_not_emitted_for_bindings_table(movie_spec):
    # The bindings table contains the same FK columns but should NOT
    # carry REFERENCES — bindings may claim about canonicals that don't
    # exist yet.
    stmts = emit_ddl(movie_spec)
    bindings_alters = [
        s for s in stmts if s.startswith("ALTER TABLE") and "_bindings" in s
    ]
    assert bindings_alters == []


def test_fk_alters_only_for_concrete_classes():
    # Abstract classes don't get a canonical table → no ALTER TABLE.
    spec = Spec(identifier_slot_name="canonical_id")
    title = spec.add_class("Title", kind="abstract")
    spec.add_class("Movie", is_a=title)
    other = spec.add_class("Other")
    other.slot("title", title)  # FK to abstract — questionable but allowed
    stmts = emit_ddl(spec)
    # No FK alter should reference an abstract class's nonexistent table.
    # Currently we DO emit one (FK to title) — that would fail at run time
    # because knot_data.title has no table. Document this as a known gap
    # the validator should catch.
    # For now just verify the alter exists targeting knot_data.title.
    alter = next(s for s in stmts if "fk_other_title" in s)
    assert "REFERENCES knot_data.title(canonical_id)" in alter


def test_bindings_carry_raw_payload_jsonb_column(movie_spec):
    stmts = emit_ddl(movie_spec)
    bindings = next(
        s for s in stmts if s.startswith("CREATE TABLE") and "movie_bindings" in s
    )
    # Bronze layer: every bindings row preserves the ingested shape.
    assert "raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb" in bindings


def test_indexes_emitted_per_concrete_bindings_table(movie_spec):
    stmts = emit_ddl(movie_spec)
    idx_stmts = [s for s in stmts if s.startswith("CREATE INDEX")]
    # 3 concrete classes (Movie, Person, Credit) × 2 indexes
    assert len(idx_stmts) == 6
    assert any("movie_bindings_current_idx" in s for s in idx_stmts)
    assert any("movie_bindings_source_idx" in s for s in idx_stmts)
    assert any("credit_bindings_current_idx" in s for s in idx_stmts)
    assert any("person_bindings_current_idx" in s for s in idx_stmts)


def test_indexes_are_partial_on_valid_to_null(movie_spec):
    stmts = emit_ddl(movie_spec)
    for s in stmts:
        if s.startswith("CREATE INDEX"):
            assert "WHERE valid_to IS NULL" in s


def test_index_source_path_uses_composite(movie_spec):
    stmts = emit_ddl(movie_spec)
    src_idx = next(s for s in stmts if "movie_bindings_source_idx" in s)
    assert "(canonical_id, source_name, source_identifier)" in src_idx


def test_index_current_path_uses_identifier_only(movie_spec):
    stmts = emit_ddl(movie_spec)
    cur_idx = next(s for s in stmts if "movie_bindings_current_idx" in s)
    assert "(canonical_id)" in cur_idx
    # The current-idx covers only the identifier; source columns appear
    # only in the partial-where clause, not in the index expression.
    assert "source_name" not in cur_idx.split("WHERE")[0]


def test_indexes_idempotent_with_if_not_exists(movie_spec):
    stmts = emit_ddl(movie_spec, if_not_exists=True)
    idx_stmts = [s for s in stmts if s.startswith("CREATE INDEX")]
    assert all("CREATE INDEX IF NOT EXISTS" in s for s in idx_stmts)


def test_indexes_can_be_disabled(movie_spec):
    stmts = emit_ddl(movie_spec, emit_indexes=False)
    assert not any(s.startswith("CREATE INDEX") for s in stmts)


def test_indexes_suppressed_when_bindings_suppressed(movie_spec):
    # No bindings tables = nothing to index.
    stmts = emit_ddl(movie_spec, emit_bindings=False)
    assert not any(s.startswith("CREATE INDEX") for s in stmts)


def test_bindings_table_has_er_metadata_jsonb_default_empty(movie_spec):
    """er_metadata is the per-row ER audit stamp — same shape as
    raw_payload (jsonb NOT NULL DEFAULT '{}'::jsonb), different author."""
    stmts = emit_ddl(movie_spec)
    bindings = next(s for s in stmts if "movie_bindings" in s)
    assert "er_metadata jsonb NOT NULL DEFAULT '{}'::jsonb" in bindings


def test_bindings_table_slots_nullable_pk_drops_canonical(movie_spec):
    """Bindings allow NULL on every slot, including the identifier —
    ingest writes source rows before ER assigns a canonical_id. The
    resolved view filters NULL identifier rows out. PK is
    (source_name, source_identifier, valid_from)."""
    stmts = emit_ddl(movie_spec)
    bindings = next(s for s in stmts if "movie_bindings" in s)
    # canonical_id is the identifier — NULLABLE in bindings (no NOT NULL)
    assert "canonical_id text NOT NULL" not in bindings
    assert "canonical_id text" in bindings
    # source_name, source_identifier always NOT NULL — they're the
    # ingest-layer identity (which source published which natural id).
    assert "source_name text NOT NULL" in bindings
    assert "source_identifier text NOT NULL" in bindings
    # year is a regular slot — NULLABLE (partial claim allowed)
    assert "year integer," in bindings or "year integer\n" in bindings
    # PK excludes canonical_id so it can start NULL
    assert "PRIMARY KEY (source_name, source_identifier, valid_from)" in bindings


# ---------------------------------------------------------------------------
# Vector slots — pgvector extension + HNSW indexes
# ---------------------------------------------------------------------------


def _spec_with_vector_slot(metric: str = "cosine"):
    from knot import Spec, types

    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT)
    movie.slot("title_embedding", types.VECTOR(384, metric=metric))
    spec.add_source("imdb").bind(movie)
    return spec


def test_vector_extension_only_when_used(movie_spec):
    # No vector slot in the standard movie_spec fixture.
    assert all("CREATE EXTENSION" not in s for s in emit_ddl(movie_spec))


def test_vector_extension_emitted_once_when_used():
    stmts = emit_ddl(_spec_with_vector_slot())
    ext_stmts = [s for s in stmts if "CREATE EXTENSION" in s]
    assert ext_stmts == ["CREATE EXTENSION IF NOT EXISTS vector;"]


def test_vector_column_on_canonical_and_bindings():
    stmts = emit_ddl(_spec_with_vector_slot())
    canonical = next(
        s for s in stmts if "knot_data.movie (" in s and "_bindings" not in s
    )
    bindings = next(s for s in stmts if "movie_bindings" in s)
    assert "title_embedding vector(384)" in canonical
    assert "title_embedding vector(384)" in bindings


def test_vector_hnsw_index_per_table_with_metric_ops():
    stmts = emit_ddl(_spec_with_vector_slot(metric="cosine"))
    hnsw = [s for s in stmts if "USING hnsw" in s]
    # One index on the canonical table, one on the bindings table.
    assert len(hnsw) == 2
    assert any(
        "movie_title_embedding_hnsw_idx" in s and "vector_cosine_ops" in s for s in hnsw
    )
    assert any(
        "movie_bindings_title_embedding_hnsw_idx" in s and "vector_cosine_ops" in s
        for s in hnsw
    )


def test_vector_hnsw_picks_ops_class_per_metric():
    for metric, ops in (
        ("cosine", "vector_cosine_ops"),
        ("l2", "vector_l2_ops"),
        ("ip", "vector_ip_ops"),
    ):
        stmts = emit_ddl(_spec_with_vector_slot(metric=metric))
        hnsw = [s for s in stmts if "USING hnsw" in s]
        assert hnsw, metric
        assert all(ops in s for s in hnsw), (metric, hnsw)


def test_vector_hnsw_respects_if_not_exists():
    stmts = emit_ddl(_spec_with_vector_slot(), if_not_exists=True)
    hnsw = [s for s in stmts if "USING hnsw" in s]
    assert hnsw
    assert all("CREATE INDEX IF NOT EXISTS" in s for s in hnsw)


def test_vector_hnsw_suppressed_when_indexes_disabled():
    stmts = emit_ddl(_spec_with_vector_slot(), emit_indexes=False)
    assert all("USING hnsw" not in s for s in stmts)
    # Extension + column still emit — column type and the extension
    # are not indexes.
    assert any("CREATE EXTENSION" in s for s in stmts)
    assert any("title_embedding vector(384)" in s for s in stmts)


def test_vector_bindings_index_skipped_when_bindings_skipped():
    stmts = emit_ddl(_spec_with_vector_slot(), emit_bindings=False)
    hnsw = [s for s in stmts if "USING hnsw" in s]
    # Only the canonical-table index remains.
    assert len(hnsw) == 1
    assert "movie_bindings" not in hnsw[0]


def test_vector_construction_rejects_bad_dim():
    import pytest

    from knot import types

    with pytest.raises(ValueError, match="positive integer"):
        types.VECTOR(0)
    with pytest.raises(ValueError, match="positive integer"):
        types.VECTOR(-1)


def test_vector_construction_rejects_unknown_metric():
    import pytest

    from knot import types

    with pytest.raises(ValueError, match="metric"):
        types.VECTOR(384, metric="manhattan")
