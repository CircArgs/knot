"""Unit tests for the ER helpers: assign_canonical_sql + recanonicalize_sql."""

import sqlglot

from knot import Spec, types
from knot.compile import emit_assign_canonical_sql, emit_recanonicalize_sql


def _basic_spec():
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT, required=True)
    movie.slot("year", types.INTEGER)
    imdb = spec.add_source("imdb")
    binding = imdb.bind(movie)
    return spec, binding


# ---------------------------------------------------------------------------
# assign_canonical_sql
# ---------------------------------------------------------------------------


def test_assign_canonical_emits_safe_update():
    """assign_canonical_sql UPDATEs only NULL-id bindings — re-running
    is a no-op once the id is set."""
    _, binding = _basic_spec()
    sql = emit_assign_canonical_sql(binding)
    assert "UPDATE knot_data.movie_bindings" in sql
    assert "SET canonical_id = %(canonical_id)s" in sql
    assert "AND canonical_id IS NULL" in sql  # safety: no clobber


def test_assign_canonical_bakes_in_source_name():
    """source is pinned by the binding — baked in as a SQL literal,
    not bound at runtime."""
    _, binding = _basic_spec()
    sql = emit_assign_canonical_sql(binding)
    assert "source_name = 'imdb'" in sql
    assert "%(source_name)s" not in sql


def test_assign_canonical_runtime_placeholders():
    """Three named placeholders: canonical_id, source_identifier,
    er_metadata. Host binds them via cur.execute params."""
    _, binding = _basic_spec()
    sql = emit_assign_canonical_sql(binding)
    assert "%(canonical_id)s" in sql
    assert "%(source_identifier)s" in sql
    assert "%(er_metadata)s" in sql


def test_assign_canonical_er_metadata_uses_coalesce():
    """er_metadata uses COALESCE so binding ``None`` keeps the
    existing column value; binding a JSON string overwrites."""
    _, binding = _basic_spec()
    sql = emit_assign_canonical_sql(binding)
    assert "er_metadata = COALESCE(%(er_metadata)s::jsonb, er_metadata)" in sql


def test_assign_canonical_schema_kwarg():
    _, binding = _basic_spec()
    sql = emit_assign_canonical_sql(binding, schema="alt")
    assert "UPDATE alt.movie_bindings" in sql


def test_assign_canonical_parses_postgres():
    _, binding = _basic_spec()
    sqlglot.parse_one(emit_assign_canonical_sql(binding), dialect="postgres")


# ---------------------------------------------------------------------------
# recanonicalize_sql
# ---------------------------------------------------------------------------


def test_recanonicalize_emits_simple_update():
    """recanonicalize_sql captures the old canonical_id, UPDATEs the
    binding row's canonical_id to the new value, and cascades. No
    SCD2 history preservation — recanonicalize is just an UPDATE."""
    _, binding = _basic_spec()
    sql = emit_recanonicalize_sql(binding)
    # CTE chain captures old then stamps new.
    assert "WITH old_state AS (" in sql
    assert "stamp AS (" in sql
    assert "UPDATE knot_data.movie_bindings" in sql
    assert "SET canonical_id = %(new_canonical_id)s" in sql
    # Only retag rows that have already been ER-stamped.
    assert "AND canonical_id IS NOT NULL" in sql


def test_recanonicalize_runtime_placeholders():
    _, binding = _basic_spec()
    sql = emit_recanonicalize_sql(binding)
    assert "%(new_canonical_id)s" in sql
    assert "%(source_identifier)s" in sql
    assert "%(er_metadata)s" in sql
    # source_name is baked in
    assert "source_name = 'imdb'" in sql
    assert "%(source_name)s" not in sql


def test_recanonicalize_er_metadata_uses_coalesce():
    """recanonicalize_sql uses COALESCE in the stamp UPDATE so binding
    ``None`` keeps the existing er_metadata; a JSON string overrides."""
    _, binding = _basic_spec()
    sql = emit_recanonicalize_sql(binding)
    assert "er_metadata = COALESCE(%(er_metadata)s::jsonb, er_metadata)" in sql


def test_recanonicalize_parses_postgres():
    _, binding = _basic_spec()
    sqlglot.parse_one(emit_recanonicalize_sql(binding), dialect="postgres")


# ---------------------------------------------------------------------------
# Option-3 ER: forward FK translation, backward fan-out, recanonicalize cascade
# ---------------------------------------------------------------------------


def _movie_credit_spec():
    """3-class spec: Person, Movie (FK director → Person), Credit
    (FK movie → Movie, FK person → Person). One source: imdb. Returns
    spec + bindings keyed by class name."""
    spec = Spec(identifier_slot_name="canonical_id")
    person = spec.add_class("Person")
    person.slot("name", types.TEXT, required=True)
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT, required=True)
    movie.slot("director", person)
    credit = spec.add_class("Credit")
    credit.slot("role", types.TEXT)
    credit.slot("movie", movie)
    credit.slot("person", person)
    imdb = spec.add_source("imdb")
    bindings = {
        "Person": imdb.bind(person),
        "Movie": imdb.bind(movie),
        "Credit": imdb.bind(credit),
    }
    return spec, bindings


def test_assign_canonical_forward_translates_fk_slots():
    """For each ClassRef slot on this class, the stamp UPDATE sets
    the FK column by looking up canonical_id in the target's bindings
    (same source) — COALESCE preserves the source-id if the target
    hasn't been ER'd yet."""
    _, bindings = _movie_credit_spec()
    sql = emit_assign_canonical_sql(bindings["Movie"])
    assert "director = COALESCE(" in sql
    assert "SELECT canonical_id FROM knot_data.person_bindings" in sql
    assert "AND source_identifier = knot_data.movie_bindings.director" in sql
    assert "AND canonical_id IS NOT NULL" in sql


def test_assign_canonical_backward_fanout_to_referrers():
    """Stamping a canonical on Movie fans out to every class that
    holds an FK to Movie (Credit.movie here), rewriting the FK column
    in-place from source-id to the new canonical-id. Fan-out CTEs
    are grouped per referencing CLASS to avoid multiple modifying
    CTEs touching the same row (postgres undefined behavior)."""
    _, bindings = _movie_credit_spec()
    sql = emit_assign_canonical_sql(bindings["Movie"])
    assert "fanout_credit AS (" in sql
    assert "UPDATE knot_data.credit_bindings" in sql
    # Per-column CASE rewrites only the matching column.
    assert (
        "movie = CASE WHEN movie = %(source_identifier)s THEN %(canonical_id)s" in sql
    )
    assert "movie = %(source_identifier)s" in sql


def test_assign_canonical_fanout_gated_on_stamp():
    """The fanout UPDATE is gated on ``EXISTS (SELECT 1 FROM stamp)``
    so re-running assign on an already-stamped row is a strict no-op
    (no force-fanout that could corrupt existing canonical-ids)."""
    _, bindings = _movie_credit_spec()
    sql = emit_assign_canonical_sql(bindings["Movie"])
    assert "WHERE EXISTS (SELECT 1 FROM stamp)" in sql


def test_assign_canonical_fanout_scopes_to_source():
    """Backward fan-out filters referencing bindings by the stamp's
    source_name — an imdb credit's .movie column holds an imdb id,
    so we only rewrite imdb-scoped referencing rows."""
    _, bindings = _movie_credit_spec()
    sql = emit_assign_canonical_sql(bindings["Movie"])
    fanout = sql[sql.index("fanout_credit AS (") :]
    assert "source_name = 'imdb'" in fanout


def test_assign_canonical_fanout_per_referrer_class():
    """One fanout CTE per referencing CLASS — same-class FK slots get
    combined into one UPDATE with multiple SET columns. Person's
    referrers are Movie.director + Credit.person → two CTEs (one
    per class)."""
    _, bindings = _movie_credit_spec()
    sql = emit_assign_canonical_sql(bindings["Person"])
    assert "fanout_movie AS (" in sql
    assert "fanout_credit AS (" in sql
    movie_fanout = sql[sql.index("fanout_movie AS (") :]
    assert "director = CASE WHEN director = %(source_identifier)s" in movie_fanout


def test_assign_canonical_does_not_register_in_canonical_table():
    """No canonical table exists anymore; assign_canonical no longer
    emits a register CTE. Bindings is the only table per class."""
    _, bindings = _movie_credit_spec()
    sql = emit_assign_canonical_sql(bindings["Movie"])
    assert "register AS" not in sql
    assert "INSERT INTO knot_data.movie " not in sql


def test_assign_canonical_no_referrers_no_fanout():
    """A leaf class with no incoming FKs (e.g. Credit, which is only
    a referrer, not a referent) produces no fanout CTEs."""
    _, bindings = _movie_credit_spec()
    sql = emit_assign_canonical_sql(bindings["Credit"])
    assert "fanout_" not in sql


def test_assign_canonical_no_fk_slots_no_forward_translation():
    """A class with no ClassRef slots (Person here — only scalar
    slots) emits no forward-translation SET clauses."""
    _, bindings = _movie_credit_spec()
    sql = emit_assign_canonical_sql(bindings["Person"])
    assert "SELECT canonical_id FROM knot_data." not in sql


def test_recanonicalize_cascades_to_referrers():
    """Recanonicalizing changes a row's canonical_id from m_old to
    m_new; every referencing class's bindings that hold m_old must
    be rewritten to m_new (canonical-id rewrite, source-agnostic)."""
    _, bindings = _movie_credit_spec()
    sql = emit_recanonicalize_sql(bindings["Movie"])
    assert "cascade_credit_movie AS (" in sql
    assert "UPDATE knot_data.credit_bindings" in sql
    assert "SET movie = %(new_canonical_id)s" in sql
    assert "WHERE movie = (SELECT old_id FROM old_state)" in sql


def test_recanonicalize_cascade_source_agnostic():
    """Cascade rewrites are canonical-id → canonical-id, so no
    source_name filter (unlike the assign fanout which is scoped to
    the stamp's source)."""
    _, bindings = _movie_credit_spec()
    sql = emit_recanonicalize_sql(bindings["Movie"])
    cascade = sql[sql.index("cascade_credit_movie") :]
    assert "source_name = 'imdb'" not in cascade


def test_recanonicalize_does_not_register_in_canonical_table():
    """No canonical table exists anymore; recanonicalize no longer
    emits a register CTE — just the stamp UPDATE + cascade fanouts."""
    _, bindings = _movie_credit_spec()
    sql = emit_recanonicalize_sql(bindings["Movie"])
    assert "register AS" not in sql
    assert "INSERT INTO knot_data.movie " not in sql


def test_assign_and_recanonicalize_parse_with_fanout():
    """Full option-3 SQL (forward translation, fanout, register,
    cascade) parses as valid postgres."""
    _, bindings = _movie_credit_spec()
    for b in bindings.values():
        a_sql = (
            emit_assign_canonical_sql(b)
            .replace("%(canonical_id)s", "'CID'")
            .replace("%(source_identifier)s", "'SID'")
            .replace("%(er_metadata)s", "'{}'")
        )
        r_sql = (
            emit_recanonicalize_sql(b)
            .replace("%(new_canonical_id)s", "'NCID'")
            .replace("%(source_identifier)s", "'SID'")
            .replace("%(er_metadata)s", "'{}'")
        )
        sqlglot.parse_one(a_sql, dialect="postgres")
        sqlglot.parse_one(r_sql, dialect="postgres")
