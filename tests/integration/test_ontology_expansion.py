"""Ontology expansion / contraction mutation tests (categories 16, 17).

All tests are xfail-strict until knot core lands. Once `knot.spec_loader`
+ `knot.compiler` + `knot.run_store` exist, these convert to passing
integration tests automatically (TestEnv methods stop raising
NotImplementedError).
"""

from __future__ import annotations

import pytest

from tests.test_env import SpecEdit


# ---------------------------------------------------------------------------
# Category 16 — Ontology expansion
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="requires knot.compiler + knot.spec_loader")
def test_16_1_add_leaf_class_no_relations(env):
    """16.1 — Add `Book` standalone (no refs to existing classes).

    Existing workflows MUST NOT recompile (compile hashes unchanged).
    Whole-graph materializers see Book on next run via spec.classes.
    """
    env.set_scenario("B2")
    env.run_full_pipeline()
    pre_movie_hash = env.last_compile_hash("Movie")
    pre_person_hash = env.last_compile_hash("Person")

    env.edit_spec(SpecEdit.add_class(
        class_def={
            "name": "Book",
            "slots": [
                {"name": "isbn", "identifier": True, "required": True},
                {"name": "title", "required": True},
                {"name": "published_year", "required": False},
            ],
        },
    ))

    # Existing class compile hashes unchanged
    assert env.last_compile_hash("Movie") == pre_movie_hash
    assert env.last_compile_hash("Person") == pre_person_hash


@pytest.mark.xfail(strict=True, reason="requires knot.compiler + knot.spec_loader")
def test_16_2_add_subclass_under_existing_abstract(env):
    """16.2 — Add `Podcast → Title` subclass on C2.

    Subclass query on Title now returns Podcast too.
    """
    env.set_scenario("C2")
    env.run_full_pipeline()

    env.edit_spec(SpecEdit.add_class(
        class_def={
            "name": "Podcast",
            "slots": [
                {"name": "rss_feed_url", "required": True},
                {"name": "host", "required": False},
            ],
        },
        is_a="Title",
    ))

    # Subclass query returns Podcast
    impact = env.impact_analysis(
        SpecEdit.add_class(class_def={"name": "Podcast"}, is_a="Title")
    )
    assert "Podcast" in impact.affected_classes
    assert "Title" in impact.affected_classes


@pytest.mark.xfail(strict=True, reason="requires knot.compiler + knot.spec_loader")
def test_16_3_add_class_with_structural_ref(env):
    """16.3 — Book.author → Person.

    Cross-class pinning kicks in for Book runs (pins Person hash).
    Person edits subsequently surface in Book impact analysis.
    """
    env.set_scenario("B2")
    env.run_full_pipeline()

    env.edit_spec(SpecEdit.add_class(
        class_def={
            "name": "Book",
            "slots": [
                {"name": "isbn", "identifier": True, "required": True},
                {"name": "title", "required": True},
                {"name": "author", "range": "Person", "required": True},
            ],
        },
    ))

    impact = env.impact_analysis(SpecEdit.rename_slot("Person.name", "Person.full_name"))
    assert "Book" in impact.affected_classes


@pytest.mark.xfail(strict=True, reason="requires knot.compiler + knot.spec_loader")
def test_16_4_add_derivation_referencing_new_class(env):
    """16.4 — Person.books_authored derived from Book.author.

    Derivation walk includes the new Book class (per recent decision:
    derivation refs walk into compile network like slot.range).
    """
    env.set_scenario("B2")
    env.run_full_pipeline()

    env.edit_spec(SpecEdit.add_class(
        class_def={
            "name": "Book",
            "slots": [
                {"name": "isbn", "identifier": True, "required": True},
                {"name": "title", "required": True},
                {"name": "author", "range": "Person", "required": True},
            ],
        },
    ))
    env.edit_spec(SpecEdit.add_derivation(
        class_name="Person",
        slot_name="books_authored",
        derivation={
            "source_class": "Book",
            "filter": "Book.author == Person",
            "project": "self",
        },
    ))

    # Person's compile network now includes Book (via derivation ref)
    impact = env.impact_analysis(SpecEdit.rename_slot("Book.title", "Book.book_title"))
    assert "Person" in impact.affected_classes


@pytest.mark.xfail(strict=True, reason="requires knot.compiler + knot.spec_loader")
def test_16_5_add_new_source_for_new_class(env):
    """16.5 — Add `goodreads_books` source feeding Book."""
    env.set_scenario("B2")
    env.run_full_pipeline()

    env.edit_spec(SpecEdit.add_class(
        class_def={
            "name": "Book",
            "slots": [{"name": "isbn", "identifier": True, "required": True}],
        },
    ))
    env.edit_spec(SpecEdit.add_source(
        source_def={
            "name": "goodreads_books",
            "entity_class": "Book",
            "identifier_slot": "isbn",
        },
    ))

    # Compile produces normalize:goodreads_books stage
    run = env.run("Book")
    assert "normalize:goodreads_books" in run.cache_keys


@pytest.mark.xfail(strict=True, reason="requires knot.compiler + knot.spec_loader")
def test_16_6_discriminator_routed_source_widened(env):
    """16.6 — Add Podcast subclass; widen `imdb_titles` discriminator routing.

    Existing Movie/Series/Game routing preserved; Podcast newly routed.
    """
    env.set_scenario("C2")
    env.run_full_pipeline()

    env.edit_spec(SpecEdit.add_class(
        class_def={"name": "Podcast", "slots": [{"name": "rss_feed_url", "required": True}]},
        is_a="Title",
    ))

    impact = env.impact_analysis(SpecEdit.add_class(
        class_def={"name": "Podcast"},
        is_a="Title",
    ))
    assert any(s.startswith("imdb_") for s in impact.affected_workflows)


@pytest.mark.xfail(strict=True, reason="requires knot.compiler + knot.spec_loader")
def test_16_7_polymorphic_identifier_widened(env):
    """16.7 — Identifier.entity_class widened to point at Book."""
    env.set_scenario("C2")
    env.run_full_pipeline()

    env.edit_spec(SpecEdit.add_class(
        class_def={"name": "Book", "slots": [{"name": "isbn", "identifier": True, "required": True}]},
    ))

    # Identifier rows where entity_class='Book' now resolve cleanly
    book_identifier_run = env.run("Book")
    assert book_identifier_run.status == "succeeded"


@pytest.mark.xfail(strict=True, reason="requires knot.compiler + knot.run_store")
def test_16_8_existing_runs_walk_back_after_expansion(env):
    """16.8 — Backward compat. Pre-expansion pipeline_runs still walk back.

    `compiled_workflows` rows from before the spec edit dereference cleanly
    even after new classes are added (retention is forever per decision).
    """
    env.set_scenario("B2")
    env.run_full_pipeline()
    pre_run = env.last_completed_run("Movie")

    env.edit_spec(SpecEdit.add_class(
        class_def={"name": "Book", "slots": [{"name": "isbn", "identifier": True, "required": True}]},
    ))

    # Walk back through pre-expansion run still works
    walkback = env.audit_walkback(class_="Movie", canonical_id="mov_imdb_tt0133093")
    assert walkback.compile_hash == pre_run.compile_hash


@pytest.mark.xfail(strict=True, reason="requires knot.compiler + knot.spec_loader")
def test_16_9_add_parent_child_pair_simultaneously(env):
    """16.9 — Album + Track added together (mirrors Series + Episode).

    Two-class transactional add. Track.parent_album → Album.
    """
    env.set_scenario("C2")
    env.run_full_pipeline()

    # Both classes added; publish gate enforces atomicity (no half-states).
    env.edit_spec(SpecEdit.add_class(
        class_def={
            "name": "Album",
            "slots": [
                {"name": "mb_id", "identifier": True, "required": True},
                {"name": "title", "required": True},
            ],
        },
        is_a="Title",
    ))
    env.edit_spec(SpecEdit.add_class(
        class_def={
            "name": "Track",
            "slots": [
                {"name": "track_id", "identifier": True, "required": True},
                {"name": "title", "required": True},
                {"name": "parent_album", "range": "Album", "required": True},
            ],
        },
        is_a="Title",
    ))

    impact = env.impact_analysis(SpecEdit.rename_slot("Album.title", "Album.album_title"))
    assert "Track" in impact.affected_classes


@pytest.mark.xfail(strict=True, reason="requires knot.compiler + knot.spec_loader")
def test_16_10_new_class_used_immediately_as_er_signal(env):
    """16.10 — Add Book; immediately update Person ER's cross_references to use it."""
    env.set_scenario("B2")
    env.run_full_pipeline()

    env.edit_spec(SpecEdit.add_class(
        class_def={
            "name": "Book",
            "slots": [
                {"name": "isbn", "identifier": True, "required": True},
                {"name": "author", "range": "Person", "required": True},
            ],
        },
    ))
    env.edit_config(
        "er_person",
        cross_references=["Book"],
    )

    # Person ER's compile network now includes Book; Person reruns invalidate
    # if Book changes
    person_run = env.run("Person")
    assert "Book" in person_run.pinned_parent_runs


# ---------------------------------------------------------------------------
# Category 17 — Ontology contraction
# ---------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="requires knot.compiler + knot.spec_loader")
def test_17_1_remove_leaf_class_no_inbound_refs(env):
    """17.1 — Remove a leaf class with no inbound refs → publish-gate accepts."""
    env.set_scenario("B2")
    env.edit_spec(SpecEdit.add_class(
        class_def={"name": "Book", "slots": [{"name": "isbn", "identifier": True, "required": True}]},
    ))
    env.run_full_pipeline()

    env.edit_spec(SpecEdit.remove_class("Book"))
    # No exception → publish gate accepted


@pytest.mark.xfail(strict=True, reason="requires knot.compiler + knot.spec_loader")
def test_17_2_remove_class_with_inbound_refs_fails(env):
    """17.2 — Removing Person while Credit references Person → publish-gate rejects."""
    env.set_scenario("B2")
    env.run_full_pipeline()

    with pytest.raises(Exception, match=r"(?i)inbound|reference|publish.gate"):
        env.edit_spec(SpecEdit.remove_class("Person"))


@pytest.mark.xfail(strict=True, reason="requires knot.compiler + knot.spec_loader")
def test_17_3_rename_class(env):
    """17.3 — Rename Movie → Film; cross-class pinning hashes change."""
    env.set_scenario("B2")
    env.run_full_pipeline()

    impact = env.impact_analysis(SpecEdit.rename_class("Movie", "Film"))
    assert "Movie" in impact.affected_classes
    assert "Credit" in impact.affected_classes  # references Movie


@pytest.mark.xfail(strict=True, reason="requires knot.compiler + knot.spec_loader")
def test_17_4_change_superclass(env):
    """17.4 — Move Game from Title to a new InteractiveWork superclass."""
    env.set_scenario("C2")
    env.run_full_pipeline()

    env.edit_spec(SpecEdit.add_class(
        class_def={
            "name": "InteractiveWork",
            "abstract": True,
            "slots": [{"name": "platform", "required": False}],
        },
    ))
    env.edit_spec(SpecEdit.change_superclass(
        class_name="Game",
        new_parent="InteractiveWork",
    ))

    impact = env.impact_analysis(
        SpecEdit.change_superclass("Game", "InteractiveWork")
    )
    assert "Game" in impact.affected_classes
    assert "Title" in impact.affected_classes  # subclass query semantics shift


@pytest.mark.xfail(strict=True, reason="requires knot.compiler + knot.run_store")
def test_17_5_existing_runs_walk_back_after_contraction(env):
    """17.5 — Pre-removal pipeline_runs still walk back."""
    env.set_scenario("B2")
    env.edit_spec(SpecEdit.add_class(
        class_def={"name": "Book", "slots": [{"name": "isbn", "identifier": True, "required": True}]},
    ))
    env.run_full_pipeline()
    pre_book_run = env.last_completed_run("Book")

    env.edit_spec(SpecEdit.remove_class("Book"))

    # compiled_workflows row for pre-removal Book run still dereferences
    walkback = env.audit_walkback(class_="Book", canonical_id="bk_isbn_0451524934")
    assert walkback.compile_hash == pre_book_run.compile_hash


@pytest.mark.xfail(strict=True, reason="requires knot.compiler + knot.spec_loader")
def test_17_6_remove_then_readd_deprecation_cycle(env):
    """17.6 — Remove then re-add a class. Old runs still walk back; new runs see fresh class."""
    env.set_scenario("C2")
    env.edit_spec(SpecEdit.add_class(
        class_def={"name": "Book", "slots": [{"name": "isbn", "identifier": True, "required": True}]},
    ))
    env.run_full_pipeline()
    first_book_run = env.last_completed_run("Book")

    env.edit_spec(SpecEdit.remove_class("Book"))
    env.edit_spec(SpecEdit.add_class(
        class_def={"name": "Book", "slots": [{"name": "isbn", "identifier": True, "required": True}]},
    ))
    env.run_full_pipeline()
    second_book_run = env.last_completed_run("Book")

    # Different compile hash (re-added is a new class identity)
    assert first_book_run.compile_hash != second_book_run.compile_hash
    # Old run still walks back
    walkback = env.audit_walkback(class_="Book", canonical_id="bk_isbn_0451524934")
    assert walkback.pipeline_run_id == first_book_run.id
