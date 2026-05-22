"""hitch's knot Spec — Movie / Person / Credit across 3 sources.

Mirrors the existing /mnt/main/code/knot/data/movies shape so the seed
JSON in hitch/seeds drops in without massaging.
"""

from __future__ import annotations

from knot import Spec, this, types


def build_spec(*, schema: str = "hitch", embedding_dim: int = 384) -> Spec:
    spec = Spec(identifier_slot_name="canonical_id", schema=schema)

    # ----- classes -----------------------------------------------------
    person = spec.add_class("Person")
    person.slot("name", types.TEXT, required=True)
    person.slot("birth_country", types.TEXT)
    person.slot("birth_year", types.INTEGER)

    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT, required=True)
    movie.slot("year", types.INTEGER)
    movie.slot("runtime_minutes", types.INTEGER)
    movie.slot("director", person)
    movie.slot("title_embedding", types.VECTOR(embedding_dim, metric="cosine"))

    credit = spec.add_class("Credit")
    credit.slot(
        "role",
        types.ENUM("director", "actor", "writer", "producer"),
        required=True,
    )
    credit.slot("movie", movie, required=True)
    credit.slot("person", person, required=True)

    # ----- virtuals + constraints --------------------------------------
    movie.add_virtual(
        "DirectedMovie",
        where=(
            (credit.col.movie == this.Movie) & (credit.col.role == "director")
        ).any(),
    )
    movie.add_constraint("year_sane", body=movie.col.year >= 1888)

    # ----- sources + bindings ------------------------------------------
    for src_name in ("imdb", "tmdb", "rottentomatoes"):
        src = spec.add_source(src_name)
        src.bind(movie)
        src.bind(person)
        src.bind(credit)

    return spec
