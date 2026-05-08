"""Pin the canonical-form serializer's behavior via a fixture hash.

If knot's canonical-form pipeline changes — cycle handling, runtime-field
strip set, default-value handling, JCS dialect, anything — this test
catches it and forces a *deliberate* bump:

  1. Run the test, see the new hash.
  2. Decide: was the change intentional? If yes, bump
     ``ontology.canonical.CANONICAL_DUMP_VERSION`` and update the
     pinned value below in the same commit.
  3. If no, revert the change.

The fixture is built in-memory from the typed metaschema entities (no
DB, no spec_store round-trip), so the hash is purely a function of the
canonical-form pipeline + the entity tree shape.
"""

from __future__ import annotations

from knot.ontology import (
    OntologyClass,
    Slot,
    Source,
    Spec,
    TypeDefinition,
    compute_content_hash,
)
from knot.ontology.canonical import CANONICAL_DUMP_VERSION


PINNED_HASH = "7f3318722fb1bd00fd1e057be13f8a61ed61226382cfc83a1333c9da09d79e36"
PINNED_VERSION = 3


def _fixture_spec() -> Spec:
    string_type = TypeDefinition(name="string", base="str")
    imdb_id = Slot(name="imdb_id", range=string_type, identifier=True, required=True)
    title = Slot(name="title", range=string_type, required=True)
    movie = OntologyClass(name="Movie", slots=[imdb_id, title])
    imdb_movies = Source(name="imdb_movies", entity_class=movie, identifier_slot=imdb_id)
    return Spec(
        id="canonical-fixture",
        version="1.0.0",
        types=[string_type],
        slots=[imdb_id, title],
        classes=[movie],
        sources=[imdb_movies],
    )


def test_canonical_dump_version_pinned():
    """If the version constant moved, the pinned hash should be bumped too."""
    assert CANONICAL_DUMP_VERSION == PINNED_VERSION, (
        f"CANONICAL_DUMP_VERSION changed to {CANONICAL_DUMP_VERSION} but "
        f"PINNED_HASH was not updated alongside it. Update both."
    )


def test_fixture_hash_is_pinned():
    """If this fails, the canonical-form pipeline changed.

    Either it was intentional (bump CANONICAL_DUMP_VERSION + this hash) or
    a regression (fix the canonical code).
    """
    actual = compute_content_hash(_fixture_spec())
    assert actual == PINNED_HASH, (
        f"\nCanonical hash drifted.\n"
        f"  expected: {PINNED_HASH}\n"
        f"  actual:   {actual}\n"
        f"If this change was deliberate, bump "
        f"ontology.canonical.CANONICAL_DUMP_VERSION and update "
        f"PINNED_HASH in this test."
    )
