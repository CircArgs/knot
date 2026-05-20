"""Feature 1 — Spec.views_ddl() named helper."""

from knot import Spec, this, types


def _simple_spec() -> Spec:
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT)
    movie.slot("year", types.INTEGER)
    credit = spec.add_class("Credit")
    credit.slot("role", types.TEXT)
    credit.slot("movie", movie)
    movie.add_virtual(
        "DirectedMovie",
        where=(
            (credit.col.movie == this.Movie) & (credit.col.role == "director")
        ).any(),
    )
    spec.add_source("imdb").bind(movie)
    spec.add_source("imdb2").bind(credit)
    return spec


def test_views_ddl_returns_only_views():
    spec = _simple_spec()
    sql = spec.views_ddl()
    # Must contain view statements
    assert "VIEW" in sql
    # Must NOT contain table creation
    assert "CREATE TABLE" not in sql
    # Must NOT contain index creation
    assert "CREATE INDEX" not in sql
    # Must NOT contain source_weight table
    assert "source_weight" not in sql


def test_views_ddl_uses_create_or_replace():
    """views_ddl always emits idempotent CREATE OR REPLACE VIEW."""
    spec = _simple_spec()
    sql = spec.views_ddl()
    assert "CREATE OR REPLACE VIEW" in sql
    assert "CREATE VIEW" not in sql.replace("CREATE OR REPLACE VIEW", "")


def test_views_ddl_is_suffix_of_full_ddl():
    """Every statement in views_ddl() also appears in the full ddl()."""
    spec = _simple_spec()
    full = spec.ddl()
    views_only = spec.views_ddl()
    for stmt in views_only.split("\n\n"):
        stmt = stmt.strip()
        if not stmt:
            continue
        assert stmt in full, f"views_ddl stmt not found in full ddl:\n{stmt[:200]}"
