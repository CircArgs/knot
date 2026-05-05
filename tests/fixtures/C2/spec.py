"""C2 tier spec — rich ontology, 10 classes, ~3 sources per leaf class.

Real Pydantic metaschema objects per staging/spec-model.md.
All references are real Python objects, not name-strings.

Covers ALL edge-case categories from EDGE-CASES.md at richer ontology than B2.
Additional C2 cases: 1.14 (discriminator routing), 1.15 (wildcard-drop),
4.4 (polymorphic Identifier refs), 8.x (full subclass + polymorphic suite),
8.5 (is_a depth ≥ 3: Game → Title → CreativeWork sketch via abstract=True Title).
"""

from __future__ import annotations

from knot.metaschema import (
    Compare,
    CompareOp,
    DirectRef,
    DiscriminatedRef,
    FilteredRelation,
    IdentifierPattern,
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
boolean_t = TypeDefinition(name="boolean", base="bool")


# ---------------------------------------------------------------------------
# --- 1. Country class (leaf; no dependencies) ---
# ---------------------------------------------------------------------------

country_code = Slot(
    name="code",
    range=string_t,
    identifier=True,
    required=True,
    resolution_policy=ResolutionPolicy.UNIQUE_OR_FAIL,
    description="ISO 3166-1 alpha-2 country code.",
)

country_name = Slot(
    name="name",
    range=string_t,
    required=True,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    description="Country display name. Derivation chain: Person.country.name (cat 5.4).",
)

country_region = Slot(
    name="region",
    range=string_t,
    required=False,
    resolution_policy=ResolutionPolicy.MODE,
    description="World region (e.g. Europe, Asia).",
)

Country = OntologyClass(
    name="Country",
    slots=[country_code, country_name, country_region],
    description="ISO 3166-1 country. Referenced by Person.country (DirectRef).",
)


# ---------------------------------------------------------------------------
# --- 2. Abstract Title superclass (cat 8.3, 8.5) ---
# ---------------------------------------------------------------------------

title_canonical_id = Slot(
    name="canonical_id",
    range=string_t,
    identifier=True,
    required=True,
    resolution_policy=ResolutionPolicy.UNIQUE_OR_FAIL,
    description="System-assigned canonical identifier for any Title subclass.",
)

title_primary_title = Slot(
    name="primary_title",
    range=string_t,
    required=True,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    description="Primary display title. Shared across all Title subclasses.",
)

title_original_title = Slot(
    name="original_title",
    range=string_t,
    required=False,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    description="Original language title. Seeded with non-Latin scripts (cat 1.7).",
)

title_runtime_minutes = Slot(
    name="runtime_minutes",
    range=integer_t,
    required=False,
    resolution_policy=ResolutionPolicy.MEDIAN_NUMERIC,
    description="Runtime in minutes — MEDIAN_NUMERIC across sources (cat 3.7).",
)

title_imdb_id = Slot(
    name="imdb_id",
    range=string_t,
    identifier=False,
    required=False,
    resolution_policy=ResolutionPolicy.UNIQUE_OR_FAIL,
    description="IMDB identifier (tt-prefixed). Optional; not all Titles have IMDB entries.",
)

# Abstract Title class — cannot be a range of non-derivation slots (cat 8.3, 8.4)
Title = OntologyClass(
    name="Title",
    abstract=True,
    slots=[title_canonical_id, title_primary_title, title_original_title, title_runtime_minutes, title_imdb_id],
    description=(
        "Abstract superclass for all creative works: Movie, Series, Episode, Game. "
        "Subclass query on Title returns all four subclasses (cat 8.3). "
        "is_a depth: Movie/Series/Episode/Game → Title (depth 1 from leaf; cat 8.5)."
    ),
)


# ---------------------------------------------------------------------------
# --- 3. Movie subclass ---
# ---------------------------------------------------------------------------

movie_year = Slot(
    name="year",
    range=integer_t,
    required=False,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    description="Theatrical release year. Disagreement seeded (cat 3.2, 3.3).",
)

movie_rating = Slot(
    name="rating",
    range=float_t,
    required=False,
    resolution_policy=ResolutionPolicy.MEDIAN_NUMERIC,
    description="Aggregate rating 0.0–10.0.",
)

movie_genres = Slot(
    name="genres",
    range=string_t,
    required=False,
    multivalued=True,
    resolution_policy=ResolutionPolicy.MODE,
    description="Genre tags; pipe-delimited in source CSV.",
)

# Derived slots — derivation patched below after Credit + credit_* slots are declared.
movie_director = Slot(
    name="director",
    range=None,  # → Person; patched below
    required=False,
    derivation=None,  # patched below
    description="Derived: director(s) via Credit (cat 5.2).",
)

movie_actors = Slot(
    name="actors",
    range=None,  # → Person; patched below
    required=False,
    multivalued=True,
    derivation=None,  # patched below
    description="Derived: actors via Credit (cat 5.3).",
)

movie_writers = Slot(
    name="writers",
    range=None,  # → Person; patched below
    required=False,
    multivalued=True,
    derivation=None,  # patched below
    description="Derived: writers via Credit.",
)

movie_producers = Slot(
    name="producers",
    range=None,  # → Person; patched below
    required=False,
    multivalued=True,
    derivation=None,  # patched below
    description="Derived: producers via Credit.",
)

Movie = OntologyClass(
    name="Movie",
    is_a=Title,
    slots=[movie_year, movie_rating, movie_genres, movie_director, movie_actors, movie_writers, movie_producers],
    description=(
        "Feature film. Subclass of Title (cat 8.3, 8.4). "
        "Seeded: dupes across IMDB/TMDB/Wikidata (cat 2.1, 2.3), derivation chain (cat 5.4)."
    ),
)


# ---------------------------------------------------------------------------
# --- 4. Series subclass ---
# ---------------------------------------------------------------------------

series_season_count = Slot(
    name="season_count",
    range=integer_t,
    required=False,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    description="Number of seasons.",
)

series_episode_count = Slot(
    name="episode_count",
    range=integer_t,
    required=False,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    description="Total episode count.",
)

series_creator = Slot(
    name="creator",
    range=None,  # → Person; patched below
    required=False,
    derivation=None,  # patched below
    description="Derived: series creator via Credit (cat 5.2 — Series variant).",
)

Series = OntologyClass(
    name="Series",
    is_a=Title,
    slots=[series_season_count, series_episode_count, series_creator],
    description=(
        "TV/streaming series. Subclass of Title. "
        "Discriminator-routed source: rows route to Movie OR Series via kind column (cat 1.14)."
    ),
)


# ---------------------------------------------------------------------------
# --- 5. Episode subclass (references Series) ---
# ---------------------------------------------------------------------------

episode_season_number = Slot(
    name="season_number",
    range=integer_t,
    required=False,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    description="Season number within the parent series.",
)

episode_number = Slot(
    name="episode_number",
    range=integer_t,
    required=False,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    description="Episode number within the season.",
)

episode_parent_series = Slot(
    name="parent_series",
    range=Series,
    required=True,
    resolution_policy=ResolutionPolicy.UNIQUE_OR_FAIL,
    reference=DirectRef(target_class=Series, fk_slot=title_canonical_id),
    description="FK to parent Series canonical entity (cat 8.4 — override slot in subclass).",
)

episode_lead_actors = Slot(
    name="lead_actors",
    range=None,  # → Person; patched below
    required=False,
    multivalued=True,
    derivation=None,  # patched below
    description="Derived: lead actors via Credit. Deep chain: Episode.parent_series.creator.country.name (cat 5.4).",
)

Episode = OntologyClass(
    name="Episode",
    is_a=Title,
    slots=[episode_season_number, episode_number, episode_parent_series, episode_lead_actors],
    description=(
        "A single episode of a Series. Subclass of Title (cat 8.4). "
        "parent_series is a slot override not present on Movie/Series."
    ),
)


# ---------------------------------------------------------------------------
# --- 6. Game subclass ---
# ---------------------------------------------------------------------------

game_platforms = Slot(
    name="platforms",
    range=string_t,
    required=False,
    multivalued=True,
    resolution_policy=ResolutionPolicy.MODE,
    description="Target platforms (PC, PS5, Xbox, Switch, …). Multivalued MODE.",
)

game_publisher = Slot(
    name="publisher",
    range=string_t,
    required=False,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    description="Publishing studio name (string; not FK — studios may not be in Studio class).",
)

game_release_year = Slot(
    name="release_year",
    range=integer_t,
    required=False,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    description="Game release year. Disagreement seeded across IGDB/Steam/RAWG (cat 3.2, 3.3).",
)

Game = OntologyClass(
    name="Game",
    is_a=Title,
    slots=[game_platforms, game_publisher, game_release_year],
    description=(
        "A video game. Subclass of Title. "
        "Discriminator-routed source also applies (cat 1.14). "
        "Wildcard-drop rows seeded (cat 1.15)."
    ),
)


# ---------------------------------------------------------------------------
# --- 7. Person (references Country via DirectRef) ---
# ---------------------------------------------------------------------------

person_imdb_id = Slot(
    name="imdb_id",
    range=string_t,
    identifier=True,
    required=True,
    resolution_policy=ResolutionPolicy.UNIQUE_OR_FAIL,
    description="IMDB person identifier (nm-prefixed). UNIQUE_OR_FAIL.",
)

person_name = Slot(
    name="name",
    range=string_t,
    required=True,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    description="Full display name. Unicode/diacritics (cat 1.6), non-Latin scripts (cat 1.7).",
)

person_birthdate = Slot(
    name="birthdate",
    range=date_t,
    required=False,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    description="Date of birth. Date-format drift across sources (cat 1.8).",
)

person_country = Slot(
    name="country",
    range=Country,
    required=False,
    resolution_policy=ResolutionPolicy.MODE,
    reference=DirectRef(target_class=Country, fk_slot=country_code),
    description="Country FK — enables derivation chain Person.country.name (cat 5.4).",
)

person_wikidata_id = Slot(
    name="wikidata_id",
    range=string_t,
    required=False,
    resolution_policy=ResolutionPolicy.UNIQUE_OR_FAIL,
    description="Wikidata Q-ID. Cross-source merged identifier (cat 4.2, 8.6).",
)

person_directing_credits = Slot(
    name="directing_credits",
    range=None,  # → Title; patched below (polymorphic across Movie+Series)
    required=False,
    multivalued=True,
    derivation=None,  # patched below
    description="Derived: works directed by this person (cat 5.6).",
)

Person = OntologyClass(
    name="Person",
    slots=[person_imdb_id, person_name, person_birthdate, person_country, person_wikidata_id, person_directing_credits],
    description=(
        "A person credited in a creative work. "
        "Unicode seeding (cat 1.6, 1.7), date-format drift (cat 1.8). "
        "country slot enables derivation chain (cat 5.4)."
    ),
)


# ---------------------------------------------------------------------------
# --- 8. Studio ---
# ---------------------------------------------------------------------------

studio_studio_id = Slot(
    name="studio_id",
    range=string_t,
    identifier=True,
    required=True,
    resolution_policy=ResolutionPolicy.UNIQUE_OR_FAIL,
    description="Studio identifier.",
)

studio_name = Slot(
    name="name",
    range=string_t,
    required=True,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    description="Studio display name.",
)

studio_country = Slot(
    name="country",
    range=Country,
    required=False,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    reference=DirectRef(target_class=Country, fk_slot=country_code),
    description="Country where studio is headquartered.",
)

studio_films = Slot(
    name="films",
    range=None,  # → Title (polymorphic: Movie OR Series produced by Studio); patched below
    required=False,
    multivalued=True,
    derivation=None,  # patched below
    description="Derived: Movie OR Series produced by this Studio via Credit (cat 14.5).",
)

Studio = OntologyClass(
    name="Studio",
    slots=[studio_studio_id, studio_name, studio_country, studio_films],
    description="A film/TV production studio.",
)


# ---------------------------------------------------------------------------
# --- 9. Award ---
# ---------------------------------------------------------------------------

award_award_id = Slot(
    name="award_id",
    range=string_t,
    identifier=True,
    required=True,
    resolution_policy=ResolutionPolicy.UNIQUE_OR_FAIL,
    description="Award identifier.",
)

award_name = Slot(
    name="name",
    range=string_t,
    required=True,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    description="Award name (e.g. 'Best Picture').",
)

award_year = Slot(
    name="year",
    range=integer_t,
    required=True,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    description="Year the award was given.",
)

award_recipient = Slot(
    name="recipient",
    range=None,  # → Person; patched below
    required=False,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    reference=DirectRef(target_class=None, fk_slot=person_imdb_id),  # patched below
    description="Person who received the award.",
)

award_work = Slot(
    name="work",
    range=None,  # → Title (polymorphic); patched below
    required=False,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    description="Title the award was given for (polymorphic — Movie or Series).",
)

Award = OntologyClass(
    name="Award",
    slots=[award_award_id, award_name, award_year, award_recipient, award_work],
    description="An industry award (e.g. Oscar, BAFTA, Emmy).",
)


# ---------------------------------------------------------------------------
# --- 10. Identifier (polymorphic class — cat 4.4, 8.1, 8.2) ---
# ---------------------------------------------------------------------------

identifier_id = Slot(
    name="identifier_id",
    range=string_t,
    identifier=True,
    required=True,
    resolution_policy=ResolutionPolicy.UNIQUE_OR_FAIL,
    description="Synthetic identifier row key.",
)

identifier_entity_class = Slot(
    name="entity_class",
    range=string_t,
    required=True,
    resolution_policy=ResolutionPolicy.UNIQUE_OR_FAIL,
    description=(
        "Discriminator: name of the target OntologyClass (Movie, Series, Episode, Game, Person, …). "
        "cat 8.1: multiple entity_class values seeded."
    ),
)

identifier_entity_src_key = Slot(
    name="entity_src_key",
    range=string_t,
    required=True,
    resolution_policy=ResolutionPolicy.UNIQUE_OR_FAIL,
    description="Source key value for the target entity (e.g. IMDB tt-id).",
)

identifier_system = Slot(
    name="system",
    range=string_t,
    required=True,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    description="Source system name (imdb, tmdb, wikidata, igdb, …).",
)

Identifier = OntologyClass(
    name="Identifier",
    slots=[identifier_id, identifier_entity_class, identifier_entity_src_key, identifier_system],
    identifier_pattern=IdentifierPattern(
        class_slot=identifier_entity_class,
        key_slot=identifier_entity_src_key,
    ),
    description=(
        "Polymorphic identifier reification. entity_class+entity_src_key discriminate the target. "
        "cat 4.4: used as ER signal with explicit cross_references declaration. "
        "cat 8.1: rows span Movie, Series, Episode, Game, Person entity classes. "
        "cat 8.2: used as polymorphic ER signal — must be explicitly declared by consumers."
    ),
)


# ---------------------------------------------------------------------------
# --- 11. Credit (work: Title — polymorphic across all Title subclasses) ---
# ---------------------------------------------------------------------------

_role_permissible = [
    PermissibleValue(text="director", description="Directed the work."),
    PermissibleValue(text="actor", description="Acted in the work."),
    PermissibleValue(text="writer", description="Wrote the screenplay or script."),
    PermissibleValue(text="producer", description="Produced the work."),
    PermissibleValue(text="composer", description="Composed the score."),
    PermissibleValue(text="creator", description="Created the series."),
]

credit_id = Slot(
    name="credit_id",
    range=string_t,
    identifier=True,
    required=True,
    resolution_policy=ResolutionPolicy.UNIQUE_OR_FAIL,
    description="Synthetic unique credit identifier.",
)

credit_person = Slot(
    name="person",
    range=Person,
    required=True,
    resolution_policy=ResolutionPolicy.UNIQUE_OR_FAIL,
    reference=DirectRef(target_class=Person, fk_slot=person_imdb_id),
    description="FK to Person canonical entity (cat 4.1, 4.2).",
)

credit_work = Slot(
    name="work",
    range=Title,
    required=True,
    resolution_policy=ResolutionPolicy.UNIQUE_OR_FAIL,
    reference=DiscriminatedRef(
        target_class=Title,
        class_slot=identifier_entity_class,
        key_slot=identifier_entity_src_key,
    ),
    description=(
        "FK to any Title subclass (Movie, Series, Episode, Game). "
        "Polymorphic via DiscriminatedRef (cat 4.4). Orphan rows seeded (cat 4.3)."
    ),
)

credit_role = Slot(
    name="role",
    range=string_t,
    required=True,
    resolution_policy=ResolutionPolicy.ARGMAX_TRUST,
    permissible_values=_role_permissible,
    description="Role enum with mixed-case seeding (cat 1.5): DIRECTOR, Director, dIRECTOR.",
)

Credit = OntologyClass(
    name="Credit",
    slots=[credit_id, credit_person, credit_work, credit_role],
    description=(
        "A credit joining a Person to any Title subclass via a role. "
        "work is polymorphic (cat 4.4) — routes to Movie, Series, Episode, or Game. "
        "Derivation source for all derived slots on Title subclasses."
    ),
)


# ---------------------------------------------------------------------------
# --- 12. Patch forward refs + derivations now that all classes are declared ---
# ---------------------------------------------------------------------------

movie_director.range = Person
movie_actors.range = Person
movie_writers.range = Person
movie_producers.range = Person
series_creator.range = Person
episode_lead_actors.range = Person
person_directing_credits.range = Title
studio_films.range = Title
award_recipient.range = Person
award_recipient.reference = DirectRef(target_class=Person, fk_slot=person_imdb_id)
award_work.range = Title

# Patch derivations — real expression-tree refs (Issue 1 fix)
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
series_creator.derivation = RelationProject(
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
episode_lead_actors.derivation = RelationProject(
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
person_directing_credits.derivation = RelationProject(
    relation=FilteredRelation(
        relation=RelationRef(from_class=Credit, slot=credit_person),
        filter=Compare(
            op=CompareOp.EQ,
            left=SlotPath(from_class=Credit, slots=[credit_role]),
            right=Literal_(value="director"),
        ),
    ),
    project=SlotPath(from_class=Credit, slots=[credit_work]),
)
studio_films.derivation = RelationProject(
    relation=FilteredRelation(
        relation=RelationRef(from_class=Credit, slot=credit_person),
        filter=Compare(
            op=CompareOp.EQ,
            left=SlotPath(from_class=Credit, slots=[credit_role]),
            right=Literal_(value="producer"),
        ),
    ),
    project=SlotPath(from_class=Credit, slots=[credit_work]),
)


# ---------------------------------------------------------------------------
# --- 13. Sources ---
# ---------------------------------------------------------------------------

# Movie sources
imdb_movies = Source(
    name="imdb_movies",
    entity_class=Movie,
    identifier_slot=title_imdb_id,
    description="IMDB Movie export. Primary trust. Disagreement target (cat 3.2, 3.3).",
)

tmdb_movies = Source(
    name="tmdb_movies",
    entity_class=Movie,
    identifier_slot=title_imdb_id,
    description="TMDB Movie export. Medium trust. Missing coverage (cat 2.4).",
)

wikidata_movies = Source(
    name="wikidata_movies",
    entity_class=Movie,
    identifier_slot=title_imdb_id,
    description="Wikidata Movie export. Lowest trust. Encoding variants (cat 1.10).",
)

# Series sources — also carries discriminator-routed rows (cat 1.14)
imdb_series = Source(
    name="imdb_series",
    entity_class=Series,
    identifier_slot=title_imdb_id,
    description="IMDB Series export. Also contains rows routed to Movie (cat 1.14 discriminator).",
)

tmdb_series = Source(
    name="tmdb_series",
    entity_class=Series,
    identifier_slot=title_imdb_id,
    description="TMDB Series export. Medium trust.",
)

wikidata_series = Source(
    name="wikidata_series",
    entity_class=Series,
    identifier_slot=title_imdb_id,
    description="Wikidata Series export. Wildcard-drop rows seeded (cat 1.15).",
)

# Episode sources
imdb_episodes = Source(
    name="imdb_episodes",
    entity_class=Episode,
    identifier_slot=title_imdb_id,
    description="IMDB Episode export.",
)

tmdb_episodes = Source(
    name="tmdb_episodes",
    entity_class=Episode,
    identifier_slot=title_imdb_id,
    description="TMDB Episode export.",
)

# Game sources (3 sources per class for C2)
igdb_games = Source(
    name="igdb_games",
    entity_class=Game,
    identifier_slot=title_canonical_id,
    description="IGDB Game export. Primary game source.",
)

steam_games = Source(
    name="steam_games",
    entity_class=Game,
    identifier_slot=title_canonical_id,
    description="Steam Game export. Platform-list disagreement seeded (cat 3.2).",
)

rawg_games = Source(
    name="rawg_games",
    entity_class=Game,
    identifier_slot=title_canonical_id,
    description="RAWG Game export. Wildcard-drop rows seeded (cat 1.15).",
)

# Person sources
imdb_persons = Source(
    name="imdb_persons",
    entity_class=Person,
    identifier_slot=person_imdb_id,
    description="IMDB Person export. Primary trust. Unicode names (cat 1.6, 1.7).",
)

tmdb_persons = Source(
    name="tmdb_persons",
    entity_class=Person,
    identifier_slot=person_imdb_id,
    description="TMDB Person export. ER merge candidates (cat 2.1, 2.3).",
)

wikidata_persons = Source(
    name="wikidata_persons",
    entity_class=Person,
    identifier_slot=person_imdb_id,
    description="Wikidata Person export. Date-format drift (cat 1.8). Encoding variants (cat 1.10).",
)

# Credit sources
imdb_credits = Source(
    name="imdb_credits",
    entity_class=Credit,
    identifier_slot=credit_id,
    description="IMDB Credit export. Mixed-case roles (cat 1.5).",
)

tmdb_credits = Source(
    name="tmdb_credits",
    entity_class=Credit,
    identifier_slot=credit_id,
    description="TMDB Credit export. Orphan-ref rows seeded (cat 4.3).",
)

# Identifier sources
imdb_identifiers = Source(
    name="imdb_identifiers",
    entity_class=Identifier,
    identifier_slot=identifier_id,
    description="IMDB Identifier rows spanning multiple entity classes (cat 8.1).",
)

tmdb_identifiers = Source(
    name="tmdb_identifiers",
    entity_class=Identifier,
    identifier_slot=identifier_id,
    description="TMDB Identifier rows. ER signal via polymorphic ref (cat 8.2).",
)

wikidata_identifiers = Source(
    name="wikidata_identifiers",
    entity_class=Identifier,
    identifier_slot=identifier_id,
    description="Wikidata Identifier rows. Cross-system merge (cat 8.6).",
)

# Studio sources
imdb_studios = Source(
    name="imdb_studios",
    entity_class=Studio,
    identifier_slot=studio_studio_id,
    description="IMDB Studio export.",
)

tmdb_studios = Source(
    name="tmdb_studios",
    entity_class=Studio,
    identifier_slot=studio_studio_id,
    description="TMDB Studio export.",
)

# Award sources
imdb_awards = Source(
    name="imdb_awards",
    entity_class=Award,
    identifier_slot=award_award_id,
    description="IMDB Award export.",
)

tmdb_awards = Source(
    name="tmdb_awards",
    entity_class=Award,
    identifier_slot=award_award_id,
    description="TMDB Award export.",
)

# Country sources
iso_countries = Source(
    name="iso_countries",
    entity_class=Country,
    identifier_slot=country_code,
    description="ISO 3166-1 country table.",
)

wikidata_countries = Source(
    name="wikidata_countries",
    entity_class=Country,
    identifier_slot=country_code,
    description="Wikidata country export. Encoding variants (cat 1.10).",
)


# ---------------------------------------------------------------------------
# --- 14. Spec root ---
# ---------------------------------------------------------------------------

spec = Spec(
    id="c2_stress",
    version="0.1.0",
    classes=[
        Title,    # abstract
        Movie, Series, Episode, Game,  # Title subclasses
        Person, Credit, Identifier,
        Studio, Award, Country,
    ],
    slots=[
        # Title shared
        title_canonical_id, title_primary_title, title_original_title,
        title_runtime_minutes, title_imdb_id,
        # Movie-specific
        movie_year, movie_rating, movie_genres,
        movie_director, movie_actors, movie_writers, movie_producers,
        # Series-specific
        series_season_count, series_episode_count, series_creator,
        # Episode-specific
        episode_season_number, episode_number, episode_parent_series, episode_lead_actors,
        # Game-specific
        game_platforms, game_publisher, game_release_year,
        # Person
        person_imdb_id, person_name, person_birthdate, person_country,
        person_wikidata_id, person_directing_credits,
        # Credit
        credit_id, credit_person, credit_work, credit_role,
        # Identifier
        identifier_id, identifier_entity_class, identifier_entity_src_key, identifier_system,
        # Studio
        studio_studio_id, studio_name, studio_country, studio_films,
        # Award
        award_award_id, award_name, award_year, award_recipient, award_work,
        # Country
        country_code, country_name, country_region,
    ],
    types=[string_t, integer_t, float_t, date_t, boolean_t],
)
