"""Unit tests for effective_constraints — walks is_a + mixin chains.

A constraint with primary=MediaItem must be effective on every concrete
descendant (Movie, TVSeries, Episode). Mixin chains contribute too —
e.g. a constraint on the Auditable mixin must fire for every class that
mixes it in.
"""

from __future__ import annotations

from knot.spec import effective_constraints
from knot.spec.metaschema import (
    Constraint,
    OntologyClass,
    Severity,
    Slot,
    Spec,
)


def _cls(
    name: str,
    *,
    is_a: OntologyClass | None = None,
    mixins: list[OntologyClass] | None = None,
    abstract: bool = False,
    slots: list[Slot] | None = None,
) -> OntologyClass:
    return OntologyClass(
        name=name,
        is_a=is_a,
        mixins=mixins or [],
        abstract=abstract,
        slots=slots or [],
    )


def _con(name: str, primary: OntologyClass, body: str = "1=1") -> Constraint:
    return Constraint(name=name, primary=primary, body=body, severity=Severity.ERROR)


def _spec(*, classes: list, constraints: list) -> Spec:
    return Spec(id="t", version="1", classes=classes, constraints=constraints)


def test_constraint_on_parent_inherits_to_child():
    """is_a chain: a constraint on MediaItem is effective on Movie."""
    media = _cls("MediaItem", abstract=True)
    movie = _cls("Movie", is_a=media)

    rule = _con("year_required", primary=media, body="year IS NOT NULL")
    spec = _spec(classes=[media, movie], constraints=[rule])

    movie_rules = effective_constraints(movie, spec)
    assert [c.name for c in movie_rules] == ["year_required"]


def test_constraint_on_grandparent_inherits():
    """Walks the full is_a chain, not just direct parent."""
    a = _cls("A", abstract=True)
    b = _cls("B", is_a=a, abstract=True)
    c = _cls("C", is_a=b)

    rule = _con("r", primary=a)
    spec = _spec(classes=[a, b, c], constraints=[rule])

    assert [r.name for r in effective_constraints(c, spec)] == ["r"]


def test_own_constraint_present():
    """A class sees its own direct constraints."""
    movie = _cls("Movie")
    rule = _con("year_range", primary=movie)
    spec = _spec(classes=[movie], constraints=[rule])

    assert [c.name for c in effective_constraints(movie, spec)] == ["year_range"]


def test_sibling_constraint_not_inherited():
    """A constraint on a sibling class does NOT apply."""
    media = _cls("MediaItem", abstract=True)
    movie = _cls("Movie", is_a=media)
    series = _cls("TVSeries", is_a=media)

    rule = _con("movie_only", primary=movie)
    spec = _spec(classes=[media, movie, series], constraints=[rule])

    assert effective_constraints(series, spec) == []
    assert [c.name for c in effective_constraints(movie, spec)] == ["movie_only"]


def test_mixin_constraint_inherits():
    """A constraint on a mixin fires for classes that mix it in."""
    auditable = _cls("Auditable", abstract=True)
    movie = _cls("Movie", mixins=[auditable])

    rule = _con("audited", primary=auditable)
    spec = _spec(classes=[auditable, movie], constraints=[rule])

    assert [c.name for c in effective_constraints(movie, spec)] == ["audited"]


def test_dedup_when_reachable_via_two_paths():
    """If a class can reach the same constraint via is_a AND mixin, count once."""
    base = _cls("Base", abstract=True)
    rule = _con("only_once", primary=base)
    # Movie reaches Base via is_a AND via mixin (synthetic but legal).
    movie = _cls("Movie", is_a=base, mixins=[base])
    spec = _spec(classes=[base, movie], constraints=[rule])

    rules = effective_constraints(movie, spec)
    assert [r.name for r in rules] == ["only_once"]
