"""hitch's knot Spec — Movie / Person / Studio / Credit across 3 sources.

A real, extensive spec — not a sketch:

  - **4 concrete classes** (Person, Studio, Movie, Credit) wired into
    a richer FK graph (Movie → Person director, Movie → Studio,
    Credit → Movie + Person).
  - **5 virtual classes** filtering across year, runtime, box office,
    living/deceased — every shape: numeric, NULL-aware, nested
    (`DirectedMovie` predicate composes with `RecentMovie` etc.).
  - **~18 user constraints** spanning ranges, NULL-safe FK chains
    (`director.birth_year < year`), bidirectional consistency
    (`movie.director == director-credit.person`), correlated aggregates
    (`movie_has_director_credit`), non-empty strings.
  - **Vector slot** (`title_embedding`) with HNSW + cosine metric.
  - **ENUM slot** (`mpaa_rating`, `role`).
  - **Array slots** (`genres`, `primary_profession`, `countries`).

Constraints are NULL-safe by design (per knot's three-valued logic
contract): every cross-field rule guards with `.is_null() |` so a
genuinely-missing value doesn't trigger the rule. Range constraints
on optional slots wrap the same way.
"""

from __future__ import annotations

from knot import Spec, this, types


def build_spec(*, schema: str = "hitch", embedding_dim: int = 384) -> Spec:
    spec = Spec(identifier_slot_name="canonical_id", schema=schema)

    # =====================================================================
    # Classes
    # =====================================================================

    # ----- Person --------------------------------------------------------
    person = spec.add_class("Person")
    person.slot("name", types.TEXT, required=True)
    person.slot("birth_country", types.TEXT)
    person.slot("birth_year", types.INTEGER)
    person.slot("death_year", types.INTEGER)
    person.slot("height_cm", types.INTEGER)
    person.slot("primary_profession", types.ARRAY(types.TEXT))

    # ----- Studio --------------------------------------------------------
    studio = spec.add_class("Studio")
    studio.slot("name", types.TEXT, required=True)
    studio.slot("country", types.TEXT)
    studio.slot("founded_year", types.INTEGER)

    # ----- Movie ---------------------------------------------------------
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT, required=True)
    movie.slot("year", types.INTEGER)
    movie.slot("runtime_minutes", types.INTEGER)
    movie.slot("director", person)
    movie.slot("studio", studio)
    movie.slot("genres", types.ARRAY(types.TEXT))
    movie.slot("countries", types.ARRAY(types.TEXT))
    # GraphQL enum names must be ``[_A-Za-z][_0-9A-Za-z]*`` — so the
    # MPAA values use the underscored form everywhere (DB, SDL, host).
    movie.slot(
        "mpaa_rating",
        types.ENUM("G", "PG", "PG_13", "R", "NC_17", "NOT_RATED"),
    )
    movie.slot("box_office_usd", types.INTEGER)
    movie.slot("title_embedding", types.VECTOR(embedding_dim, metric="cosine"))

    # ----- Credit --------------------------------------------------------
    credit = spec.add_class("Credit")
    credit.slot(
        "role",
        types.ENUM("director", "actor", "writer", "producer"),
        required=True,
    )
    credit.slot("movie", movie, required=True)
    credit.slot("person", person, required=True)
    credit.slot("ordering", types.INTEGER)  # billing position

    # =====================================================================
    # Virtual classes
    # =====================================================================
    movie.add_virtual(
        "DirectedMovie",
        where=(
            (credit.col.movie == this.Movie) & (credit.col.role == "director")
        ).any(),
    )
    movie.add_virtual(
        "RecentMovie",
        where=movie.col.year.is_not_null() & (movie.col.year >= 1995),
    )
    movie.add_virtual(
        "ClassicMovie",
        where=movie.col.year.is_not_null() & (movie.col.year <= 1985),
    )
    movie.add_virtual(
        "LongMovie",
        where=movie.col.runtime_minutes.is_not_null()
        & (movie.col.runtime_minutes >= 150),
    )
    movie.add_virtual(
        "Blockbuster",
        where=movie.col.box_office_usd.is_not_null()
        & (movie.col.box_office_usd >= 100_000_000),
    )
    person.add_virtual("LivingPerson", where=person.col.death_year.is_null())

    # =====================================================================
    # Constraints — user-declared, NULL-safe across the board.
    # =====================================================================

    # ----- Movie ---------------------------------------------------------
    movie.add_constraint(
        "movie_year_sane",
        body=movie.col.year.is_null() | (movie.col.year >= 1888),
    )
    movie.add_constraint(
        "movie_year_not_future",
        body=movie.col.year.is_null() | (movie.col.year <= 2030),
    )
    movie.add_constraint(
        "movie_runtime_sane",
        body=movie.col.runtime_minutes.is_null()
        | ((movie.col.runtime_minutes >= 1) & (movie.col.runtime_minutes <= 600)),
    )
    movie.add_constraint(
        "movie_title_not_blank",
        body=movie.col.title != "",
    )
    movie.add_constraint(
        "movie_box_office_non_negative",
        body=movie.col.box_office_usd.is_null() | (movie.col.box_office_usd >= 0),
    )
    movie.add_constraint(
        "movie_director_alive_at_release",
        body=movie.col.director.birth_year.is_null()
        | movie.col.year.is_null()
        | (movie.col.director.birth_year < movie.col.year),
    )
    movie.add_constraint(
        "movie_director_death_after_release",
        body=movie.col.director.death_year.is_null()
        | movie.col.year.is_null()
        | (movie.col.director.death_year >= movie.col.year),
    )
    movie.add_constraint(
        "movie_has_director_credit",
        body=movie.col.director.is_null()
        | ((credit.col.movie == this.Movie) & (credit.col.role == "director")).any(),
    )

    # ----- Person --------------------------------------------------------
    person.add_constraint(
        "person_birth_year_sane",
        body=person.col.birth_year.is_null()
        | ((person.col.birth_year >= 1850) & (person.col.birth_year <= 2030)),
    )
    person.add_constraint(
        "person_name_not_blank",
        body=person.col.name != "",
    )
    person.add_constraint(
        "person_death_after_birth",
        body=person.col.death_year.is_null()
        | person.col.birth_year.is_null()
        | (person.col.death_year > person.col.birth_year),
    )
    person.add_constraint(
        "person_height_sane",
        body=person.col.height_cm.is_null()
        | ((person.col.height_cm >= 50) & (person.col.height_cm <= 260)),
    )

    # ----- Studio --------------------------------------------------------
    studio.add_constraint(
        "studio_name_not_blank",
        body=studio.col.name != "",
    )
    studio.add_constraint(
        "studio_founded_year_sane",
        body=studio.col.founded_year.is_null()
        | ((studio.col.founded_year >= 1880) & (studio.col.founded_year <= 2030)),
    )

    # ----- Credit --------------------------------------------------------
    credit.add_constraint(
        "credit_ordering_non_negative",
        body=credit.col.ordering.is_null() | (credit.col.ordering >= 0),
    )
    # Bidirectional: if a credit is a director role, the movie's
    # director FK should point at this credit's person.
    credit.add_constraint(
        "credit_director_matches_movie_director",
        body=(credit.col.role != "director")
        | credit.col.movie.director.is_null()
        | (credit.col.movie.director == credit.col.person),
    )

    # =====================================================================
    # Sources + bindings
    # =====================================================================
    for src_name in ("imdb", "tmdb", "rottentomatoes"):
        src = spec.add_source(src_name)
        for cls in (person, studio, movie, credit):
            src.bind(cls)

    return spec
