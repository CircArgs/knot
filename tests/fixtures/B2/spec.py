"""B2 tier spec — 3 classes (Movie, Person, Credit), 3 sources per class.

Real Pydantic metaschema objects per staging/spec-model.md.
All references are real Python objects, not name-strings.

Covers every edge-case category from EDGE-CASES.md (B2 is the kitchen-sink
default integration tier — see B2/README.md for the full coverage matrix).
"""

from __future__ import annotations

from knot.metaschema import (
    Compare,
    CompareOp,
    DirectRef,
    FilteredRelation,
    Literal_,
    OntologyClass,
    PermissibleValue,
    RelationProject,
    RelationRef,
    Slot,
    SlotPath,
    Source,
    Spec,
    TypeDefinition,
)
from knot.metaschema import ResolutionPolicy


# ---------------------------------------------------------------------------
# Built-in type references
# ---------------------------------------------------------------------------

string_t = TypeDefinition(name="string", base="str")
integer_t = TypeDefinition(name="integer", base="int")
float_t = TypeDefinition(name="float", base="float")
date_t = TypeDefinition(name="date", base="date")


# ---------------------------------------------------------------------------
# --- 1. Movie slots ---
# ---------------------------------------------------------------------------

movie_imdb_id = Slot(
    name="imdb_id",
    range=string_t,
    identifier=True,
    required=True,
    resolution_policy=ResolutionPolicy.UNIQUE_OR_FAIL,
    description="IMDB title identifier (tt-prefixed). All sources must agree; disagreement raises (cat 3.6).",
)

movie_title = Slot(
    name="title",
    range=string_t,
    required=True,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    description="Primary release title; highest-trust source wins (cat 3.2, 3.3).",
)

movie_year = Slot(
    name="year",
    range=integer_t,
    required=False,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    description="Release year (4-digit integer). ARGMAX_TRUST: per cat 3.2 design-in disagreements.",
)

movie_runtime_minutes = Slot(
    name="runtime_minutes",
    range=integer_t,
    required=False,
    resolution_policy=ResolutionPolicy.MEDIAN_NUMERIC,
    description="Total runtime in minutes. MEDIAN_NUMERIC across sources (cat 3.7).",
)

movie_rating = Slot(
    name="rating",
    range=float_t,
    required=False,
    resolution_policy=ResolutionPolicy.MEDIAN_NUMERIC,
    description="Aggregate rating 0.0–10.0. MEDIAN_NUMERIC (cat 3.7).",
)

movie_genres = Slot(
    name="genres",
    range=string_t,
    required=False,
    multivalued=True,
    resolution_policy=ResolutionPolicy.MODE,
    description="Genre tags; pipe-delimited in source CSV. Multivalued MODE (cat 3.1, 3.2).",
)

# Derived slots — resolved by RelationProject over Credit rows (cat 5.2, 5.3).
# derivation is patched below after Credit + credit_person + credit_role are declared.
movie_director = Slot(
    name="director",
    range=None,  # range resolved at compile: Person (via Credit.person)
    required=False,
    derivation=None,  # patched below
    description="Derived: Person(s) credited as director via Credit (cat 5.2).",
)

movie_actors = Slot(
    name="actors",
    range=None,
    required=False,
    multivalued=True,
    derivation=None,  # patched below
    description="Derived: Person(s) credited as actor via Credit (cat 5.3 — multi-result).",
)

movie_writers = Slot(
    name="writers",
    range=None,
    required=False,
    multivalued=True,
    derivation=None,  # patched below
    description="Derived: Person(s) credited as writer via Credit.",
)

movie_producers = Slot(
    name="producers",
    range=None,
    required=False,
    multivalued=True,
    derivation=None,  # patched below
    description="Derived: Person(s) credited as producer via Credit.",
)


# ---------------------------------------------------------------------------
# --- 2. Person slots ---
# ---------------------------------------------------------------------------

person_imdb_id = Slot(
    name="imdb_id",
    range=string_t,
    identifier=True,
    required=True,
    resolution_policy=ResolutionPolicy.UNIQUE_OR_FAIL,
    description="IMDB person identifier (nm-prefixed). UNIQUE_OR_FAIL (cat 3.6).",
)

person_name = Slot(
    name="name",
    range=string_t,
    required=True,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    description="Full display name. Seeded with Unicode/diacritics (cat 1.6, 1.7).",
)

person_birthdate = Slot(
    name="birthdate",
    range=date_t,
    required=False,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    description="Date of birth. Seeded with date-format drift across sources (cat 1.8).",
)

person_country = Slot(
    name="country",
    range=string_t,
    required=False,
    resolution_policy=ResolutionPolicy.MODE,
    description="Country of birth or primary nationality (ISO 3166-1 alpha-2 code).",
)

person_wikidata_id = Slot(
    name="wikidata_id",
    range=string_t,
    required=False,
    resolution_policy=ResolutionPolicy.UNIQUE_OR_FAIL,
    description="Wikidata entity ID (Q-prefixed). Cross-source merged identifier (cat 4.2).",
)


# ---------------------------------------------------------------------------
# --- 3. Credit slots ---
# ---------------------------------------------------------------------------

credit_id = Slot(
    name="credit_id",
    range=string_t,
    identifier=True,
    required=True,
    resolution_policy=ResolutionPolicy.UNIQUE_OR_FAIL,
    description="Synthetic unique credit identifier.",
)

# credit_person and credit_work reference OntologyClass objects declared below.
# We use forward-declaration via string then fix up after class construction —
# but since Python requires forward refs for circular references we declare the
# slots after the classes and attach them via slot_overrides. The canonical
# pattern (per spec-model.md): real object refs threaded explicitly.

_role_permissible = [
    PermissibleValue(text="director", description="Directed the work."),
    PermissibleValue(text="actor", description="Acted in the work."),
    PermissibleValue(text="writer", description="Wrote the screenplay or script."),
    PermissibleValue(text="producer", description="Produced the work."),
    PermissibleValue(text="composer", description="Composed the score."),
]

credit_role = Slot(
    name="role",
    range=string_t,
    required=True,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    permissible_values=_role_permissible,
    description=(
        "Role enum: director / actor / writer / producer / composer. "
        "Seeded with mixed-case variants (cat 1.5): DIRECTOR, Director, dIRECTOR."
    ),
)


# ---------------------------------------------------------------------------
# --- 4. OntologyClasses (Movie and Person declared before Credit so Credit
#        can hold real references to them) ---
# ---------------------------------------------------------------------------

Movie = OntologyClass(
    name="Movie",
    slots=[
        movie_imdb_id, movie_title, movie_year, movie_runtime_minutes,
        movie_rating, movie_genres,
        movie_director, movie_actors, movie_writers, movie_producers,
    ],
    description="A feature film with a theatrical release.",
)

Person = OntologyClass(
    name="Person",
    slots=[person_imdb_id, person_name, person_birthdate, person_country, person_wikidata_id],
    description="A person (director, actor, writer, etc.) credited in a work.",
)

# credit_person and credit_work declared here so they can hold real OntologyClass refs.
credit_person = Slot(
    name="person",
    range=Person,
    required=True,
    resolution_policy=ResolutionPolicy.UNIQUE_OR_FAIL,
    reference=DirectRef(target_class=Person, fk_slot=person_imdb_id),
    description="FK to Person canonical entity. Cross-class ref (cat 4.1, 4.2).",
)

credit_work = Slot(
    name="work",
    range=Movie,
    required=True,
    resolution_policy=ResolutionPolicy.UNIQUE_OR_FAIL,
    reference=DirectRef(target_class=Movie, fk_slot=movie_imdb_id),
    description="FK to Movie canonical entity. Cross-class ref (cat 4.1, 4.3 orphan test).",
)

Credit = OntologyClass(
    name="Credit",
    slots=[credit_id, credit_person, credit_work, credit_role],
    description=(
        "A credit joining a Person to a Movie with a role. "
        "Derivation source for Movie.director / actors / writers / producers (cat 5.1–5.3)."
    ),
)

# Backfill derived-slot range + derivation now that Movie/Person/Credit are declared.
movie_director.range = Person
movie_actors.range = Person
movie_writers.range = Person
movie_producers.range = Person

movie_director.derivation = RelationProject(
    relation=FilteredRelation(
        relation=RelationRef(from_class=Credit, slot=credit_work),
        filter=Compare(
            op=CompareOp.EQ,
            left=SlotPath(from_class=Credit, slots=[credit_role]),
            right=Literal_(value="director"),
        ),
    ),
    project=SlotPath(from_class=Credit, slots=[credit_person]),
)
movie_actors.derivation = RelationProject(
    relation=FilteredRelation(
        relation=RelationRef(from_class=Credit, slot=credit_work),
        filter=Compare(
            op=CompareOp.EQ,
            left=SlotPath(from_class=Credit, slots=[credit_role]),
            right=Literal_(value="actor"),
        ),
    ),
    project=SlotPath(from_class=Credit, slots=[credit_person]),
)
movie_writers.derivation = RelationProject(
    relation=FilteredRelation(
        relation=RelationRef(from_class=Credit, slot=credit_work),
        filter=Compare(
            op=CompareOp.EQ,
            left=SlotPath(from_class=Credit, slots=[credit_role]),
            right=Literal_(value="writer"),
        ),
    ),
    project=SlotPath(from_class=Credit, slots=[credit_person]),
)
movie_producers.derivation = RelationProject(
    relation=FilteredRelation(
        relation=RelationRef(from_class=Credit, slot=credit_work),
        filter=Compare(
            op=CompareOp.EQ,
            left=SlotPath(from_class=Credit, slots=[credit_role]),
            right=Literal_(value="producer"),
        ),
    ),
    project=SlotPath(from_class=Credit, slots=[credit_person]),
)


# ---------------------------------------------------------------------------
# --- 5. Sources ---
# ---------------------------------------------------------------------------

imdb_movies = Source(
    name="imdb_movies",
    entity_class=Movie,
    identifier_slot=movie_imdb_id,
    description="IMDB title export — highest trust for Movie. Primary disagreement target (cat 3.2, 3.3).",
)

tmdb_movies = Source(
    name="tmdb_movies",
    entity_class=Movie,
    identifier_slot=movie_imdb_id,
    description="TMDB title export — medium trust. Disagreement source (cat 3.2, 3.3, 2.4).",
)

wikidata_movies = Source(
    name="wikidata_movies",
    entity_class=Movie,
    identifier_slot=movie_imdb_id,
    description="Wikidata film export — lowest trust for Movie. Missing coverage (cat 2.4).",
)

imdb_persons = Source(
    name="imdb_persons",
    entity_class=Person,
    identifier_slot=person_imdb_id,
    description="IMDB person export — primary Person source.",
)

tmdb_persons = Source(
    name="tmdb_persons",
    entity_class=Person,
    identifier_slot=person_imdb_id,
    description="TMDB person export — secondary Person source for ER merging (cat 2.1, 2.3).",
)

imdb_credits = Source(
    name="imdb_credits",
    entity_class=Credit,
    identifier_slot=credit_id,
    description="IMDB credit export — primary Credit source.",
)

tmdb_credits = Source(
    name="tmdb_credits",
    entity_class=Credit,
    identifier_slot=credit_id,
    description="TMDB credit export — secondary Credit source; mixed-case roles (cat 1.5).",
)

wikidata_credits = Source(
    name="wikidata_credits",
    entity_class=Credit,
    identifier_slot=credit_id,
    description="Wikidata credits — tertiary source; sparse; orphan-ref test rows (cat 4.3).",
)


# ---------------------------------------------------------------------------
# --- 6. Spec root ---
# ---------------------------------------------------------------------------

spec = Spec(
    id="b2_integration",
    version="0.1.0",
    classes=[Movie, Person, Credit],
    slots=[
        movie_imdb_id, movie_title, movie_year, movie_runtime_minutes,
        movie_rating, movie_genres,
        movie_director, movie_actors, movie_writers, movie_producers,
        person_imdb_id, person_name, person_birthdate, person_country, person_wikidata_id,
        credit_id, credit_person, credit_work, credit_role,
    ],
    types=[string_t, integer_t, float_t, date_t],
)
