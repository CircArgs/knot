"""Pure-Python diff tests for mixin slot composition.

Adding / removing a mixin should cause the contributed slots to surface
as AddSlot / DropSlot records in ``diff_specs`` — no DDL execution
needed. The DDL-applying mixin tests (column materialisation, GraphQL
queryability, collision / cycle rejection) live in
``tests/integration/spec/compile/test_mixins.py``.
"""

from __future__ import annotations

from knot.spec import OntologyClass, Primitive, Slot, Spec
from knot.spec.compile.postgres import migration


def test_add_mixin_emits_addslot_diff():
    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    movie_v1 = OntologyClass(name="Movie", slots=[imdb_id])
    spec_v1 = Spec(
        id="add_mixin",
        version="1.0.0",
        slots=[imdb_id],
        classes=[movie_v1],
    )

    created_at = Slot(name="created_at", type=Primitive(name="datetime"))
    timestamped = OntologyClass(
        name="Timestamped",
        slots=[created_at],
        abstract=True,
    )
    imdb_id2 = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    movie_v2 = OntologyClass(
        name="Movie",
        slots=[imdb_id2],
        mixins=[timestamped],
    )
    spec_v2 = Spec(
        id="add_mixin",
        version="1.0.0",
        slots=[imdb_id2, created_at],
        classes=[movie_v2, timestamped],
    )

    changes = migration.diff_specs(spec_v1, spec_v2)
    add_slot_changes = [c for c in changes if isinstance(c, migration.AddSlot)]
    added_names = {c.slot.name for c in add_slot_changes}
    assert "created_at" in added_names


def test_remove_mixin_emits_dropslot_diff():
    created_at = Slot(name="created_at", type=Primitive(name="datetime"))
    timestamped = OntologyClass(
        name="Timestamped",
        slots=[created_at],
        abstract=True,
    )
    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    movie_v1 = OntologyClass(
        name="Movie",
        slots=[imdb_id],
        mixins=[timestamped],
    )
    spec_v1 = Spec(
        id="remove_mixin",
        version="1.0.0",
        slots=[imdb_id, created_at],
        classes=[movie_v1, timestamped],
    )

    imdb_id2 = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    movie_v2 = OntologyClass(name="Movie", slots=[imdb_id2])
    spec_v2 = Spec(
        id="remove_mixin",
        version="1.0.0",
        slots=[imdb_id2],
        classes=[movie_v2],
    )

    changes = migration.diff_specs(spec_v1, spec_v2)
    drop_slot_changes = [c for c in changes if isinstance(c, migration.DropSlot)]
    dropped_names = {c.slot_name for c in drop_slot_changes}
    assert "created_at" in dropped_names
