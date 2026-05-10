"""Pure-Python diff tests for mixin slot composition.

Adding / removing a mixin should cause the contributed slots to surface
as AddSlot / DropSlot records in ``diff_specs`` — no DDL execution
needed. The DDL-applying mixin tests (column materialisation, GraphQL
queryability, collision / cycle rejection) live in
``tests/integration/spec/compile/test_mixins.py``.
"""

from __future__ import annotations

from knot.spec import OntologyClass, Slot, Source, Spec, TypeDefinition
from knot.spec.compile.postgres import migration


def _string_type() -> TypeDefinition:
    return TypeDefinition(name="string", base="str")


def _ts_type() -> TypeDefinition:
    return TypeDefinition(name="datetime", base="datetime")


def test_add_mixin_emits_addslot_diff():
    st, dt = _string_type(), _ts_type()

    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    movie_v1 = OntologyClass(name="Movie", slots=[imdb_id])
    src_v1 = Source(name="imdb", entity_class=movie_v1, identifier_slot=imdb_id)
    spec_v1 = Spec(
        id="add_mixin",
        version="1.0.0",
        types=[st],
        slots=[imdb_id],
        classes=[movie_v1],
        sources=[src_v1],
    )

    created_at = Slot(name="created_at", range=dt)
    timestamped = OntologyClass(
        name="Timestamped",
        slots=[created_at],
        abstract=True,
    )
    imdb_id2 = Slot(name="imdb_id", range=st, identifier=True, required=True)
    movie_v2 = OntologyClass(
        name="Movie",
        slots=[imdb_id2],
        mixins=[timestamped],
    )
    src_v2 = Source(name="imdb", entity_class=movie_v2, identifier_slot=imdb_id2)
    spec_v2 = Spec(
        id="add_mixin",
        version="1.0.0",
        types=[st, dt],
        slots=[imdb_id2, created_at],
        classes=[movie_v2, timestamped],
        sources=[src_v2],
    )

    changes = migration.diff_specs(spec_v1, spec_v2)
    add_slot_changes = [c for c in changes if isinstance(c, migration.AddSlot)]
    added_names = {c.slot.name for c in add_slot_changes}
    assert "created_at" in added_names


def test_remove_mixin_emits_dropslot_diff():
    st, dt = _string_type(), _ts_type()

    created_at = Slot(name="created_at", range=dt)
    timestamped = OntologyClass(
        name="Timestamped",
        slots=[created_at],
        abstract=True,
    )
    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    movie_v1 = OntologyClass(
        name="Movie",
        slots=[imdb_id],
        mixins=[timestamped],
    )
    src_v1 = Source(name="imdb", entity_class=movie_v1, identifier_slot=imdb_id)
    spec_v1 = Spec(
        id="remove_mixin",
        version="1.0.0",
        types=[st, dt],
        slots=[imdb_id, created_at],
        classes=[movie_v1, timestamped],
        sources=[src_v1],
    )

    imdb_id2 = Slot(name="imdb_id", range=st, identifier=True, required=True)
    movie_v2 = OntologyClass(name="Movie", slots=[imdb_id2])
    src_v2 = Source(name="imdb", entity_class=movie_v2, identifier_slot=imdb_id2)
    spec_v2 = Spec(
        id="remove_mixin",
        version="1.0.0",
        types=[st],
        slots=[imdb_id2],
        classes=[movie_v2],
        sources=[src_v2],
    )

    changes = migration.diff_specs(spec_v1, spec_v2)
    drop_slot_changes = [c for c in changes if isinstance(c, migration.DropSlot)]
    dropped_names = {c.slot_name for c in drop_slot_changes}
    assert "created_at" in dropped_names
