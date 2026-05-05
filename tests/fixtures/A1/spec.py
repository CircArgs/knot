"""A1 tier spec — 1 class (Movie), 1 source (imdb_movies).

Real Pydantic metaschema objects per staging/spec-model.md.
All references are real Python objects, not name-strings.
"""

from __future__ import annotations

from knot.metaschema import (
    OntologyClass,
    Slot,
    Source,
    Spec,
    TypeDefinition,
)
from knot.metaschema import ResolutionPolicy


# ---------------------------------------------------------------------------
# Built-in type references (mirrors Spec built-ins; listed here for clarity)
# ---------------------------------------------------------------------------

string_t = TypeDefinition(name="string", base="str")
integer_t = TypeDefinition(name="integer", base="int")
float_t = TypeDefinition(name="float", base="float")


# ---------------------------------------------------------------------------
# Slots
# ---------------------------------------------------------------------------

imdb_id = Slot(
    name="imdb_id",
    range=string_t,
    identifier=True,
    required=True,
    resolution_policy=ResolutionPolicy.UNIQUE_OR_FAIL,
    description="IMDB title identifier (tt-prefixed).",
)

title = Slot(
    name="title",
    range=string_t,
    required=True,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    description="Primary release title.",
)

year = Slot(
    name="year",
    range=integer_t,
    required=False,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    description="Release year (4-digit integer).",
)

runtime_minutes = Slot(
    name="runtime_minutes",
    range=integer_t,
    required=False,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    description="Total runtime in minutes.",
)

rating = Slot(
    name="rating",
    range=float_t,
    required=False,
    resolution_policy=ResolutionPolicy.MEDIAN_NUMERIC,
    description="Aggregate audience/critic rating (0.0–10.0).",
)

genres = Slot(
    name="genres",
    range=string_t,
    required=False,
    multivalued=True,
    resolution_policy=ResolutionPolicy.MODE,
    description="Genre tags; pipe-delimited in source CSV.",
)


# ---------------------------------------------------------------------------
# OntologyClass
# ---------------------------------------------------------------------------

Movie = OntologyClass(
    name="Movie",
    slots=[imdb_id, title, year, runtime_minutes, rating, genres],
    description="A feature film with a single theatrical release.",
)


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------

imdb_movies_source = Source(
    name="imdb_movies",
    entity_class=Movie,
    identifier_slot=imdb_id,
    description="IMDB title export — primary source for A1 tier.",
)


# ---------------------------------------------------------------------------
# Spec root
# ---------------------------------------------------------------------------

spec = Spec(
    id="a1_smoke",
    version="0.1.0",
    classes=[Movie],
    slots=[imdb_id, title, year, runtime_minutes, rating, genres],
    types=[string_t, integer_t, float_t],
)
