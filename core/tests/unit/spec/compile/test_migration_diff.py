"""Pure-Python diff_specs + is_destructive tests for the core change kinds.

Covers AddClass, AddSlot, DropClass, DropSlot, ChangeSlotTypeExpression — the
records emitted for class / slot existence and slot type changes — plus
their ``is_destructive`` classification.

The DDL apply_changes / publish-gate flow tests for these records live
in ``tests/integration/spec/compile/test_migration.py``.
"""

from __future__ import annotations

from knot.spec import OntologyClass, Primitive, Slot, Spec
from knot.spec.compile.postgres.migration import (
    AddClass,
    AddSlot,
    ChangeSlotTypeExpression,
    DropClass,
    DropSlot,
    diff_specs,
    is_destructive,
)


def _str_slot(name: str, **kw) -> Slot:
    return Slot(name=name, type=Primitive(name="string"), **kw)


def _int_slot(name: str, **kw) -> Slot:
    return Slot(name=name, type=Primitive(name="integer"), **kw)


# ---------------------------------------------------------------------------
# diff_specs — change-event generation
# ---------------------------------------------------------------------------


def test_diff_from_none_produces_add_class_for_each_concrete_class():
    id_slot = _str_slot("id", identifier=True)
    movie = OntologyClass(name="Movie", slots=[id_slot])
    series = OntologyClass(name="Series", slots=[id_slot])
    spec = Spec(id="t", version="1.0.0", slots=[id_slot], classes=[movie, series])
    changes = diff_specs(None, spec)
    types_ = {type(c).__name__ for c in changes}
    assert "AddClass" in types_
    add_names = {c.cls.name for c in changes if isinstance(c, AddClass)}
    assert add_names == {"Movie", "Series"}


def test_diff_from_none_skips_abstract_classes():
    id_slot = _str_slot("id", identifier=True)
    abstract = OntologyClass(name="Base", slots=[id_slot], abstract=True)
    concrete = OntologyClass(name="Movie", slots=[id_slot])
    spec = Spec(id="t", version="1.0.0", slots=[id_slot], classes=[abstract, concrete])
    changes = diff_specs(None, spec)
    add_names = {c.cls.name for c in changes if isinstance(c, AddClass)}
    assert "Base" not in add_names
    assert "Movie" in add_names


def test_diff_add_slot_detected():
    id_slot = _str_slot("id", identifier=True)
    title = _str_slot("title")
    prev_movie = OntologyClass(name="Movie", slots=[id_slot])
    cand_movie = OntologyClass(name="Movie", slots=[id_slot, title])
    prev = Spec(id="t", version="1.0.0", slots=[id_slot], classes=[prev_movie])
    cand = Spec(id="t", version="1.0.0", slots=[id_slot, title], classes=[cand_movie])
    changes = diff_specs(prev, cand)
    add_slots = [c for c in changes if isinstance(c, AddSlot)]
    assert len(add_slots) == 1
    assert add_slots[0].slot.name == "title"


def test_diff_drop_slot_detected():
    id_slot = _str_slot("id", identifier=True)
    title = _str_slot("title")
    prev_movie = OntologyClass(name="Movie", slots=[id_slot, title])
    cand_movie = OntologyClass(name="Movie", slots=[id_slot])
    prev = Spec(id="t", version="1.0.0", slots=[id_slot, title], classes=[prev_movie])
    cand = Spec(id="t", version="1.0.0", slots=[id_slot], classes=[cand_movie])
    changes = diff_specs(prev, cand)
    drop_slots = [c for c in changes if isinstance(c, DropSlot)]
    assert any(c.slot_name == "title" for c in drop_slots)


def test_diff_change_slot_type_detected():
    id_slot = _str_slot("id", identifier=True)
    year_str = _str_slot("year")
    year_int = _int_slot("year")
    prev_movie = OntologyClass(name="Movie", slots=[id_slot, year_str])
    cand_movie = OntologyClass(name="Movie", slots=[id_slot, year_int])
    prev = Spec(id="t", version="1.0.0", slots=[id_slot, year_str], classes=[prev_movie])
    cand = Spec(id="t", version="1.0.0", slots=[id_slot, year_int], classes=[cand_movie])
    changes = diff_specs(prev, cand)
    type_changes = [c for c in changes if isinstance(c, ChangeSlotTypeExpression)]
    assert any(c.slot.name == "year" for c in type_changes)


# ---------------------------------------------------------------------------
# is_destructive classification
# ---------------------------------------------------------------------------


def test_add_class_is_not_destructive():
    id_slot = _str_slot("id", identifier=True)
    cls = OntologyClass(name="Movie", slots=[id_slot])
    assert not is_destructive(AddClass(cls=cls))


def test_add_slot_is_not_destructive():
    cls = OntologyClass(name="Movie", slots=[])
    slot = _str_slot("title")
    assert not is_destructive(AddSlot(cls=cls, slot=slot))


def test_drop_class_is_destructive():
    assert is_destructive(DropClass(class_name="Movie"))


def test_drop_slot_is_destructive():
    cls = OntologyClass(name="Movie", slots=[])
    assert is_destructive(DropSlot(cls=cls, slot_name="title"))


def test_change_slot_type_is_destructive():
    cls = OntologyClass(name="Movie", slots=[])
    slot = _int_slot("year")
    assert is_destructive(
        ChangeSlotTypeExpression(cls=cls, slot=slot, prev_pg_type="TEXT", new_pg_type="INTEGER")
    )


def test_drop_source_is_destructive():
    # DropSource is Bucket A: rows become orphaned when the source is removed.
    # Requires allow_destructive=True at publish.
    from knot.spec.compile.postgres.migration import DropSource

    assert is_destructive(DropSource(source_name="imdb"))


def test_drop_source_requires_allow_destructive_at_publish():
    """diff_specs emits DropSource when a source is removed; publish must gate it."""
    id_slot = _str_slot("id", identifier=True)
    movie = OntologyClass(name="Movie", slots=[id_slot])
    from knot.spec import Source

    src = Source(name="imdb", entity_class=movie, identifier_slot=id_slot)
    prev = Spec(id="t", version="1.0.0", slots=[id_slot], classes=[movie], sources=[src])
    # candidate has no sources → DropSource
    cand = Spec(id="t", version="1.0.0", slots=[id_slot], classes=[movie], sources=[])
    changes = diff_specs(prev, cand)
    from knot.spec.compile.postgres.migration import DropSource

    drop_sources = [c for c in changes if isinstance(c, DropSource)]
    assert len(drop_sources) == 1
    assert drop_sources[0].source_name == "imdb"
    assert is_destructive(drop_sources[0])
