"""Pure-Python diff_specs + is_destructive tests for the core change kinds.

Covers AddClass, AddSlot, DropClass, DropSlot, ChangeSlotType — the
records emitted for class / slot existence and slot type changes — plus
their ``is_destructive`` classification.

The DDL apply_changes / publish-gate flow tests for these records live
in ``tests/integration/spec/compile/test_migration.py``.
"""

from __future__ import annotations

from knot.spec import OntologyClass, Slot, Spec, TypeDefinition
from knot.spec.compile.postgres.migration import (
    AddClass,
    AddSlot,
    ChangeSlotType,
    DropClass,
    DropSlot,
    diff_specs,
    is_destructive,
)


def _str_type() -> TypeDefinition:
    return TypeDefinition(name="string", base="str")


def _int_type() -> TypeDefinition:
    return TypeDefinition(name="integer", base="int")


# ---------------------------------------------------------------------------
# diff_specs — change-event generation
# ---------------------------------------------------------------------------


def test_diff_from_none_produces_add_class_for_each_concrete_class():
    st = _str_type()
    id_slot = Slot(name="id", range=st, identifier=True)
    movie = OntologyClass(name="Movie", slots=[id_slot])
    series = OntologyClass(name="Series", slots=[id_slot])
    spec = Spec(id="t", version="1.0.0", types=[st], slots=[id_slot], classes=[movie, series])
    changes = diff_specs(None, spec)
    types_ = {type(c).__name__ for c in changes}
    assert "AddClass" in types_
    add_names = {c.cls.name for c in changes if isinstance(c, AddClass)}
    assert add_names == {"Movie", "Series"}


def test_diff_from_none_skips_abstract_classes():
    st = _str_type()
    id_slot = Slot(name="id", range=st, identifier=True)
    abstract = OntologyClass(name="Base", slots=[id_slot], abstract=True)
    concrete = OntologyClass(name="Movie", slots=[id_slot])
    spec = Spec(id="t", version="1.0.0", types=[st], slots=[id_slot], classes=[abstract, concrete])
    changes = diff_specs(None, spec)
    add_names = {c.cls.name for c in changes if isinstance(c, AddClass)}
    assert "Base" not in add_names
    assert "Movie" in add_names


def test_diff_add_slot_detected():
    st = _str_type()
    id_slot = Slot(name="id", range=st, identifier=True)
    title = Slot(name="title", range=st)
    prev_movie = OntologyClass(name="Movie", slots=[id_slot])
    cand_movie = OntologyClass(name="Movie", slots=[id_slot, title])
    prev = Spec(id="t", version="1.0.0", types=[st], slots=[id_slot], classes=[prev_movie])
    cand = Spec(id="t", version="1.0.0", types=[st], slots=[id_slot, title], classes=[cand_movie])
    changes = diff_specs(prev, cand)
    add_slots = [c for c in changes if isinstance(c, AddSlot)]
    assert len(add_slots) == 1
    assert add_slots[0].slot.name == "title"


def test_diff_drop_slot_detected():
    st = _str_type()
    id_slot = Slot(name="id", range=st, identifier=True)
    title = Slot(name="title", range=st)
    prev_movie = OntologyClass(name="Movie", slots=[id_slot, title])
    cand_movie = OntologyClass(name="Movie", slots=[id_slot])
    prev = Spec(id="t", version="1.0.0", types=[st], slots=[id_slot, title], classes=[prev_movie])
    cand = Spec(id="t", version="1.0.0", types=[st], slots=[id_slot], classes=[cand_movie])
    changes = diff_specs(prev, cand)
    drop_slots = [c for c in changes if isinstance(c, DropSlot)]
    assert any(c.slot_name == "title" for c in drop_slots)


def test_diff_change_slot_type_detected():
    str_t = _str_type()
    int_t = _int_type()
    id_slot = Slot(name="id", range=str_t, identifier=True)
    year_str = Slot(name="year", range=str_t)
    year_int = Slot(name="year", range=int_t)
    prev_movie = OntologyClass(name="Movie", slots=[id_slot, year_str])
    cand_movie = OntologyClass(name="Movie", slots=[id_slot, year_int])
    prev = Spec(
        id="t", version="1.0.0", types=[str_t], slots=[id_slot, year_str], classes=[prev_movie]
    )
    cand = Spec(
        id="t",
        version="1.0.0",
        types=[str_t, int_t],
        slots=[id_slot, year_int],
        classes=[cand_movie],
    )
    changes = diff_specs(prev, cand)
    type_changes = [c for c in changes if isinstance(c, ChangeSlotType)]
    assert any(c.slot.name == "year" for c in type_changes)


# ---------------------------------------------------------------------------
# is_destructive classification
# ---------------------------------------------------------------------------


def test_add_class_is_not_destructive():
    st = _str_type()
    id_slot = Slot(name="id", range=st, identifier=True)
    cls = OntologyClass(name="Movie", slots=[id_slot])
    assert not is_destructive(AddClass(cls=cls))


def test_add_slot_is_not_destructive():
    st = _str_type()
    cls = OntologyClass(name="Movie", slots=[])
    slot = Slot(name="title", range=st)
    assert not is_destructive(AddSlot(cls=cls, slot=slot))


def test_drop_class_is_destructive():
    assert is_destructive(DropClass(class_name="Movie"))


def test_drop_slot_is_destructive():
    cls = OntologyClass(name="Movie", slots=[])
    assert is_destructive(DropSlot(cls=cls, slot_name="title"))


def test_change_slot_type_is_destructive():
    int_t = _int_type()
    cls = OntologyClass(name="Movie", slots=[])
    slot = Slot(name="year", range=int_t)
    assert is_destructive(
        ChangeSlotType(cls=cls, slot=slot, prev_pg_type="TEXT", new_pg_type="BIGINT")
    )
