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


def test_canonical_is_identity_only(movie_spec):
    # Under option-3 ER, the canonical table is an identity registry —
    # just the identifier column(s). All slot values live in
    # <class>_bindings; the resolved view computes argmax-over-bindings
    # at read time. Slot columns on canonical would be dead schema.
    stmts = emit_ddl(movie_spec)
    movie_table = next(
        s for s in stmts if "knot_data.movie (" in s and "_bindings" not in s
    )
    assert "canonical_id text NOT NULL" in movie_table
    assert "PRIMARY KEY (canonical_id)" in movie_table
    # No slot columns on canonical.
    assert "year integer" not in movie_table
    assert "name text" not in movie_table


def test_concrete_inherits_slots_into_bindings(movie_spec):
    # Inherited-from-abstract slots land on the bindings table (where
    # all slot values live), not the canonical table.
    stmts = emit_ddl(movie_spec)
    bindings = next(s for s in stmts if "movie_bindings" in s and "CREATE TABLE" in s)
    assert "year integer" in bindings
    assert "name text" in bindings


def test_array_renders_as_postgres_array_on_bindings(movie_spec):
    stmts = emit_ddl(movie_spec)
    bindings = next(s for s in stmts if "movie_bindings" in s and "CREATE TABLE" in s)
    assert "genres text[]" in bindings


def test_classref_renders_as_text_on_bindings(movie_spec):
    # FK slots are text columns on the bindings table. Post-ER they
    # hold canonical-ids (after option-3 forward translation + backward
    # fan-out); pre-ER they hold the source's natural reference.
    stmts = emit_ddl(movie_spec)
    bindings = next(s for s in stmts if "credit_bindings" in s and "CREATE TABLE" in s)
    assert "movie text" in bindings
    assert "person text" in bindings


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


def test_no_fk_alters_emitted(movie_spec):
    # Under option-3 ER, FK columns live on the bindings table (which
    # holds source-ids pre-ER, canonical-ids post-ER) — so postgres FK
    # constraints would fail on pre-ER source-id values. Bindings stay
    # loose by design; ER orchestration owns referential integrity,
    # and the data-quality validators surface orphans. No FK ALTERs.
    stmts = emit_ddl(movie_spec)
    assert not any(s.startswith("ALTER TABLE") for s in stmts)


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
    from knot import types

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


def test_vector_column_on_bindings_only():
    # Vectors are slot values, not identity — they live on bindings
    # (where slot values live), not on the identity-only canonical
    # table.
    stmts = emit_ddl(_spec_with_vector_slot())
    canonical = next(
        s for s in stmts if "knot_data.movie (" in s and "_bindings" not in s
    )
    bindings = next(s for s in stmts if "movie_bindings" in s)
    assert "title_embedding vector(384)" not in canonical
    assert "title_embedding vector(384)" in bindings


def test_vector_hnsw_index_only_on_bindings():
    # One HNSW per vector slot — on bindings. Canonical has no vector
    # columns to index.
    stmts = emit_ddl(_spec_with_vector_slot(metric="cosine"))
    hnsw = [s for s in stmts if "USING hnsw" in s]
    assert len(hnsw) == 1
    assert (
        "movie_bindings_title_embedding_hnsw_idx" in hnsw[0]
        and "vector_cosine_ops" in hnsw[0]
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


def test_vector_indexes_skipped_when_bindings_skipped():
    # With bindings disabled there's nowhere left to host a vector
    # column (canonical is identity-only) → no HNSW indexes emitted.
    stmts = emit_ddl(_spec_with_vector_slot(), emit_bindings=False)
    hnsw = [s for s in stmts if "USING hnsw" in s]
    assert hnsw == []


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
