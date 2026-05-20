"""Integration tests for option-3 ER semantics against live postgres.

Option-3 design recap (see CLAUDE.md): ER stamps the binding row's
``canonical_id``, then in the same statement (a) translates this row's
own FK slots from source-id to canonical-id (forward), (b) fans out to
every referencing class's bindings to rewrite their FK columns
in-place (backward), and (c) registers the new canonical_id in the
class's identity-only canonical table. Recanonicalize cascades
canonical-id rewrites to every referencing binding.

These tests exercise the full loop end-to-end against postgres 16 —
the unit tests verify SQL shape, these verify semantics under the live
database.
"""

from __future__ import annotations

from knot import Spec, types
from knot.compile import emit_ddl
from tests.integration.conftest import exec_many

_SOURCE_WEIGHTS = {"imdb": 0.85, "tmdb": 0.7}


def _build_spec(schema: str) -> Spec:
    """3-class spec: Person, Movie (FK director → Person),
    Credit (FK movie → Movie, FK person → Person). Two sources:
    imdb + tmdb."""
    spec = Spec(identifier_slot_name="canonical_id", schema=schema)

    person = spec.add_class("Person")
    person.slot("name", types.TEXT, required=True)

    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT, required=True)
    movie.slot("year", types.INTEGER)
    movie.slot("director", person)

    credit = spec.add_class("Credit")
    credit.slot("role", types.TEXT, required=True)
    credit.slot("movie", movie)
    credit.slot("person", person)

    for src_name in ("imdb", "tmdb"):
        src = spec.add_source(src_name)
        src.bind(person)
        src.bind(movie)
        src.bind(credit)
    return spec


def _deploy(pg, spec: Spec, schema: str) -> None:
    exec_many(pg, emit_ddl(spec, schema=schema))
    with pg.cursor() as cur:
        for binding in spec.source_bindings:
            weight = _SOURCE_WEIGHTS.get(binding.source.name, 0.0)
            upsert = binding.upsert_weight_sql()
            ident = binding.identifier_slot.name
            for slot in binding.class_.effective_slots():
                if slot.name == ident:
                    continue
                cur.execute(upsert, {"slot_name": slot.name, "weight": weight})


def _json(v) -> str:
    import json

    return json.dumps(v)


def _write(pg, binding, rows: list[dict]) -> None:
    close_out, insert = binding.write_sql()
    payload = _json(rows)
    with pg.cursor() as cur:
        cur.execute(close_out, {"rows": payload})
        cur.execute(insert, {"rows": payload})


def _assign(pg, binding, *, canonical_id: str, source_identifier: str) -> None:
    with pg.cursor() as cur:
        cur.execute(
            binding.assign_canonical_sql(),
            {
                "canonical_id": canonical_id,
                "source_identifier": source_identifier,
                "er_metadata": None,
            },
        )


def _recan(pg, binding, *, new_canonical_id: str, source_identifier: str) -> None:
    with pg.cursor() as cur:
        cur.execute(
            binding.recanonicalize_sql(),
            {
                "new_canonical_id": new_canonical_id,
                "source_identifier": source_identifier,
                "er_metadata": None,
            },
        )


def _fetch_one(pg, sql: str, params: dict | None = None):
    with pg.cursor() as cur:
        cur.execute(sql, params or {})
        return cur.fetchone()


def _fetch_all(pg, sql: str, params: dict | None = None):
    with pg.cursor() as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


# ---------------------------------------------------------------------------
# Forward FK translation — target already ER'd when source is stamped
# ---------------------------------------------------------------------------


def test_forward_fk_translation_when_target_already_erd(pg, schema):
    """Person is ER'd first (so person_bindings has canonical_id). Then
    Movie is ingested with .director = person's source-id, and ER'd.
    The stamp's forward-translation SET clause should rewrite
    movie_bindings.director from the source-id to the person's
    canonical_id in one atomic statement."""
    spec = _build_spec(schema)
    imdb_person = spec.classes["Person"].binding_for(spec.sources["imdb"])
    imdb_movie = spec.classes["Movie"].binding_for(spec.sources["imdb"])
    _deploy(pg, spec, schema)

    # Person ingested + ER'd first.
    _write(pg, imdb_person, [{"source_identifier": "nm001", "name": "Tarantino"}])
    _assign(pg, imdb_person, canonical_id="p_qt", source_identifier="nm001")

    # Movie ingested with director = imdb's person id "nm001".
    _write(
        pg,
        imdb_movie,
        [{"source_identifier": "tt001", "title": "Pulp Fiction", "director": "nm001"}],
    )
    # Pre-stamp: director column still holds the source-id.
    pre = _fetch_one(
        pg,
        f"SELECT director FROM {schema}.movie_bindings WHERE source_identifier='tt001'",
    )
    assert pre == ("nm001",)

    # Stamp the movie's canonical_id — forward translation rewrites
    # .director to p_qt in the same statement.
    _assign(pg, imdb_movie, canonical_id="m_pulp", source_identifier="tt001")

    post = _fetch_one(
        pg,
        f"SELECT director FROM {schema}.movie_bindings WHERE source_identifier='tt001'",
    )
    assert post == ("p_qt",)


def test_forward_fk_translation_preserves_source_id_when_target_unresolved(pg, schema):
    """Movie is ER'd BEFORE Person is ER'd — the forward-translation
    lookup returns NULL (target binding has canonical_id IS NULL), and
    COALESCE keeps the source-id in place. The backward fan-out from
    Person's later ER will catch it."""
    spec = _build_spec(schema)
    imdb_person = spec.classes["Person"].binding_for(spec.sources["imdb"])
    imdb_movie = spec.classes["Movie"].binding_for(spec.sources["imdb"])
    _deploy(pg, spec, schema)

    _write(pg, imdb_person, [{"source_identifier": "nm001", "name": "Tarantino"}])
    _write(
        pg,
        imdb_movie,
        [{"source_identifier": "tt001", "title": "Pulp Fiction", "director": "nm001"}],
    )

    # ER the movie BEFORE the person — forward lookup misses.
    _assign(pg, imdb_movie, canonical_id="m_pulp", source_identifier="tt001")

    director = _fetch_one(
        pg,
        f"SELECT director FROM {schema}.movie_bindings WHERE source_identifier='tt001'",
    )
    assert director == ("nm001",), "should preserve source-id when target unresolved"


# ---------------------------------------------------------------------------
# Backward fan-out — incoming FKs from already-ingested referrers
# ---------------------------------------------------------------------------


def test_backward_fanout_catches_orphan_referrers(pg, schema):
    """Credit ingested first (referencing imdb movie tt001), THEN
    Movie ER'd. The fan-out should find the credit row with .movie =
    'tt001' and rewrite it to m_pulp."""
    spec = _build_spec(schema)
    imdb_movie = spec.classes["Movie"].binding_for(spec.sources["imdb"])
    imdb_credit = spec.classes["Credit"].binding_for(spec.sources["imdb"])
    imdb_person = spec.classes["Person"].binding_for(spec.sources["imdb"])
    _deploy(pg, spec, schema)

    # Ingest person + movie + credit; credit's .movie holds imdb's tt001.
    _write(pg, imdb_person, [{"source_identifier": "nm001", "name": "Tarantino"}])
    _write(
        pg,
        imdb_movie,
        [{"source_identifier": "tt001", "title": "Pulp Fiction"}],
    )
    _write(
        pg,
        imdb_credit,
        [
            {
                "source_identifier": "cr1",
                "role": "director",
                "movie": "tt001",
                "person": "nm001",
            }
        ],
    )

    # Pre-fanout: credit.movie still holds the source-id.
    pre = _fetch_one(
        pg,
        f"SELECT movie FROM {schema}.credit_bindings WHERE source_identifier='cr1'",
    )
    assert pre == ("tt001",)

    # ER the movie — fan-out rewrites every imdb credit.movie='tt001'.
    _assign(pg, imdb_movie, canonical_id="m_pulp", source_identifier="tt001")

    post = _fetch_one(
        pg,
        f"SELECT movie FROM {schema}.credit_bindings WHERE source_identifier='cr1'",
    )
    assert post == ("m_pulp",)


def test_backward_fanout_scopes_to_source_name(pg, schema):
    """imdb movie ER stamp must NOT rewrite tmdb credits' .movie field
    (which holds a tmdb id, not an imdb id) — even if the tmdb credit's
    .movie happens to share the imdb source-id literal."""
    spec = _build_spec(schema)
    imdb_movie = spec.classes["Movie"].binding_for(spec.sources["imdb"])
    tmdb_credit = spec.classes["Credit"].binding_for(spec.sources["tmdb"])
    _deploy(pg, spec, schema)

    _write(pg, imdb_movie, [{"source_identifier": "tt001", "title": "Pulp Fiction"}])
    # A tmdb credit that *coincidentally* uses "tt001" as its .movie
    # value (would be a tmdb movie id, not imdb's — namespace collision).
    _write(
        pg,
        tmdb_credit,
        [
            {
                "source_identifier": "tmdb_cr1",
                "role": "actor",
                "movie": "tt001",
                "person": "tmdb_p1",
            }
        ],
    )

    _assign(pg, imdb_movie, canonical_id="m_pulp", source_identifier="tt001")

    # The tmdb credit's .movie must NOT have been touched — it lives
    # in tmdb's id namespace, not imdb's.
    tmdb = _fetch_one(
        pg,
        f"SELECT movie FROM {schema}.credit_bindings WHERE source_identifier='tmdb_cr1'",
    )
    assert tmdb == ("tt001",), "must not cross source namespaces"


def test_assign_is_strictly_idempotent(pg, schema):
    """Re-running assign on an already-stamped row must be a strict
    no-op: the EXISTS gate on the fan-out prevents force-rewriting
    referencing bindings that already hold canonical-ids."""
    spec = _build_spec(schema)
    imdb_movie = spec.classes["Movie"].binding_for(spec.sources["imdb"])
    imdb_credit = spec.classes["Credit"].binding_for(spec.sources["imdb"])
    _deploy(pg, spec, schema)

    _write(pg, imdb_movie, [{"source_identifier": "tt001", "title": "Pulp Fiction"}])
    _write(
        pg,
        imdb_credit,
        [{"source_identifier": "cr1", "role": "director", "movie": "tt001"}],
    )
    _assign(pg, imdb_movie, canonical_id="m_pulp", source_identifier="tt001")
    # Now the credit holds m_pulp.
    assert _fetch_one(
        pg,
        f"SELECT movie FROM {schema}.credit_bindings WHERE source_identifier='cr1'",
    ) == ("m_pulp",)

    # Re-run with a *different* canonical_id — must NOT corrupt the
    # already-translated credit, because the stamp UPDATE doesn't match
    # (canonical_id is already set), so the EXISTS-gated fanout doesn't
    # fire.
    _assign(pg, imdb_movie, canonical_id="m_other", source_identifier="tt001")
    assert _fetch_one(
        pg,
        f"SELECT movie FROM {schema}.credit_bindings WHERE source_identifier='cr1'",
    ) == ("m_pulp",), "idempotent: re-run must not force-fanout a different id"


# ---------------------------------------------------------------------------
# Canonical registry population
# ---------------------------------------------------------------------------


def test_canonical_registry_populated_on_first_stamp(pg, schema):
    """The class's canonical table (identity registry) gets a row
    INSERTed on every fresh ER stamp — driven by ``FROM stamp`` so
    re-runs (no-op stamp) don't add phantom rows."""
    spec = _build_spec(schema)
    imdb_movie = spec.classes["Movie"].binding_for(spec.sources["imdb"])
    _deploy(pg, spec, schema)

    _write(pg, imdb_movie, [{"source_identifier": "tt001", "title": "Pulp Fiction"}])
    pre = _fetch_all(pg, f"SELECT canonical_id FROM {schema}.movie")
    assert pre == []

    _assign(pg, imdb_movie, canonical_id="m_pulp", source_identifier="tt001")
    post = _fetch_all(pg, f"SELECT canonical_id FROM {schema}.movie")
    assert post == [("m_pulp",)]


def test_canonical_registry_no_phantom_on_idempotent_assign(pg, schema):
    """Re-running assign on an already-stamped row must not register
    a phantom canonical_id."""
    spec = _build_spec(schema)
    imdb_movie = spec.classes["Movie"].binding_for(spec.sources["imdb"])
    _deploy(pg, spec, schema)

    _write(pg, imdb_movie, [{"source_identifier": "tt001", "title": "Pulp Fiction"}])
    _assign(pg, imdb_movie, canonical_id="m_pulp", source_identifier="tt001")
    _assign(pg, imdb_movie, canonical_id="m_other", source_identifier="tt001")  # no-op

    rows = _fetch_all(
        pg, f"SELECT canonical_id FROM {schema}.movie ORDER BY canonical_id"
    )
    assert rows == [("m_pulp",)], "no phantom m_other registration"


# ---------------------------------------------------------------------------
# Recanonicalize cascade
# ---------------------------------------------------------------------------


def test_recanonicalize_cascades_to_referrers(pg, schema):
    """Recanonicalizing a movie from m_old to m_new must rewrite every
    referencing binding (credit.movie) that held m_old — and source-
    agnostically, since post-ER FK columns hold canonical-ids (no
    source coupling)."""
    spec = _build_spec(schema)
    imdb_movie = spec.classes["Movie"].binding_for(spec.sources["imdb"])
    imdb_credit = spec.classes["Credit"].binding_for(spec.sources["imdb"])
    tmdb_credit = spec.classes["Credit"].binding_for(spec.sources["tmdb"])
    _deploy(pg, spec, schema)

    _write(pg, imdb_movie, [{"source_identifier": "tt001", "title": "Pulp Fiction"}])
    _write(
        pg,
        imdb_credit,
        [{"source_identifier": "cr1", "role": "director", "movie": "tt001"}],
    )
    # tmdb credit refers to the same canonical movie via tmdb's id.
    _write(
        pg,
        tmdb_credit,
        [{"source_identifier": "tmdb_cr1", "role": "actor", "movie": "552"}],
    )

    # First ER: assign m_pulp to imdb's movie. Imdb credit gets m_pulp
    # via fan-out; tmdb credit's .movie still "552" (tmdb movie binding
    # not ER'd; namespace-scoped fanout missed it).
    _assign(pg, imdb_movie, canonical_id="m_pulp", source_identifier="tt001")

    # Then a separate ER step writes the tmdb credit's .movie to m_pulp
    # (in the real world this is what happens when tmdb's movie binding
    # for "552" gets ER'd to the same canonical). Simulate by direct UPDATE.
    pg.execute(
        f"UPDATE {schema}.credit_bindings SET movie='m_pulp' "
        f"WHERE source_identifier='tmdb_cr1'"
    )

    # Now recanonicalize m_pulp → m_pulp_v2. Both credits must update.
    _recan(pg, imdb_movie, new_canonical_id="m_pulp_v2", source_identifier="tt001")
    rows = _fetch_all(
        pg,
        f"SELECT source_identifier, movie FROM {schema}.credit_bindings "
        f"WHERE valid_to IS NULL ORDER BY source_identifier",
    )
    assert rows == [("cr1", "m_pulp_v2"), ("tmdb_cr1", "m_pulp_v2")]


# ---------------------------------------------------------------------------
# End-to-end: resolved view FK join actually returns matching rows
# ---------------------------------------------------------------------------


def test_resolved_view_fk_join_returns_correct_rows(pg, schema):
    """The whole point of option-3: after ER, FK columns hold
    canonical-ids and the resolved view's FK joins (which join
    target.canonical_id = source.fk_col) actually find rows."""
    spec = _build_spec(schema)
    person = spec.classes["Person"]
    movie = spec.classes["Movie"]
    imdb_person = person.binding_for(spec.sources["imdb"])
    imdb_movie = movie.binding_for(spec.sources["imdb"])
    _deploy(pg, spec, schema)

    _write(pg, imdb_person, [{"source_identifier": "nm001", "name": "Tarantino"}])
    _assign(pg, imdb_person, canonical_id="p_qt", source_identifier="nm001")
    _write(
        pg,
        imdb_movie,
        [
            {
                "source_identifier": "tt001",
                "title": "Pulp Fiction",
                "year": 1994,
                "director": "nm001",
            }
        ],
    )
    _assign(pg, imdb_movie, canonical_id="m_pulp", source_identifier="tt001")

    # FK-traversal query: movie.director.name. The resolved view
    # exposes movie.director = "p_qt" (canonical-id post-translation);
    # joining to person_resolved.canonical_id works.
    q = movie.resolved.select(movie.col.title, movie.col.director.name).where(
        movie.col.director.name == "Tarantino"
    )
    rows = _fetch_all(pg, q.sql())
    assert rows == [("Pulp Fiction", "Tarantino")]
