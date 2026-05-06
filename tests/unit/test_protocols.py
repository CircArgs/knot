"""Unit tests for knot.protocols."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from knot.metaschema import DerivedSlot, OntologyClass, TypeDefinition
from knot.protocols import (
    ConstraintEvaluator,
    ConstraintResult,
    DataContext,
    DerivationEvaluator,
    DerivationResult,
    DqColumnMap,
    DqMergeRunner,
    DqNormalizeRunner,
    DqPublishRunner,
    DqResolveRunner,
    DqResult,
    DqRunnerBase,
    ERProtocol,
    ERResult,
    MaterializerProtocol,
    MaterializeResult,
    Protocol,
    ProtocolKind,
    ScoreColumnMap,
    TranslateResult,
    TranslatorProtocol,
)


# ---------------------------------------------------------------------------
# ProtocolKind enum
# ---------------------------------------------------------------------------

def test_protocol_kind_has_four_members() -> None:
    members = list(ProtocolKind)
    assert len(members) == 4
    values = {m.value for m in members}
    assert values == {
        "disagreement_aware",
        "resolved",
        "er_decision_lens",
        "target_direct",
    }


# ---------------------------------------------------------------------------
# DataContext
# ---------------------------------------------------------------------------

def test_datacontext_accepts_single_ontology_class() -> None:
    cls = OntologyClass(name="Movie")
    dc = DataContext(primary=cls)
    assert dc.primary is cls
    assert dc.where is None
    assert dc.project is None


def test_datacontext_accepts_list_of_ontology_classes() -> None:
    movie = OntologyClass(name="Movie")
    person = OntologyClass(name="Person")
    dc = DataContext(primary=[movie, person])
    assert len(dc.primary) == 2


def test_datacontext_accepts_derived_slot() -> None:
    string_t = TypeDefinition(name="string", base="str")
    from knot.metaschema import RelationRef, RelationProject, SlotPath, Slot
    slot = Slot(name="director", range=None)
    dc = DataContext(primary=slot)
    assert dc.primary is slot


def test_datacontext_accepts_list_of_derived_slots() -> None:
    from knot.metaschema import Slot
    s1 = Slot(name="director", range=None)
    s2 = Slot(name="actors", range=None)
    dc = DataContext(primary=[s1, s2])
    assert len(dc.primary) == 2


def test_datacontext_extra_forbid() -> None:
    cls = OntologyClass(name="Movie")
    with pytest.raises(ValidationError):
        DataContext(primary=cls, unknown_field="oops")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Protocol subclass disagreement_stance
# ---------------------------------------------------------------------------

def test_er_protocol_stance() -> None:
    assert ERProtocol.disagreement_stance == ProtocolKind.DISAGREEMENT_AWARE


def test_materializer_protocol_stance() -> None:
    assert MaterializerProtocol.disagreement_stance == ProtocolKind.RESOLVED


def test_translator_protocol_stance() -> None:
    assert TranslatorProtocol.disagreement_stance == ProtocolKind.RESOLVED


def test_constraint_evaluator_stance() -> None:
    assert ConstraintEvaluator.disagreement_stance == ProtocolKind.RESOLVED


def test_derivation_evaluator_stance() -> None:
    assert DerivationEvaluator.disagreement_stance == ProtocolKind.RESOLVED


def test_dq_normalize_runner_stance() -> None:
    assert DqNormalizeRunner.disagreement_stance == ProtocolKind.DISAGREEMENT_AWARE


def test_dq_resolve_runner_stance() -> None:
    assert DqResolveRunner.disagreement_stance == ProtocolKind.ER_DECISION_LENS


def test_dq_merge_runner_stance() -> None:
    assert DqMergeRunner.disagreement_stance == ProtocolKind.RESOLVED


def test_dq_publish_runner_stance() -> None:
    assert DqPublishRunner.disagreement_stance == ProtocolKind.TARGET_DIRECT


# ---------------------------------------------------------------------------
# Result types are Pydantic models with extra=forbid
# ---------------------------------------------------------------------------

def test_er_result_is_pydantic_extra_forbid() -> None:
    result = ERResult(
        table=Path("/tmp/scores.parquet"),
        column_map=ScoreColumnMap(
            a_canonical="entity_a",
            b_canonical="entity_b",
            score="similarity_score",
        ),
    )
    assert result.table == Path("/tmp/scores.parquet")
    assert result.column_map.a_canonical == "entity_a"

    with pytest.raises(ValidationError):
        ERResult(
            table=Path("/tmp/x"),
            column_map=ScoreColumnMap(a_canonical="a", b_canonical="b", score="s"),
            unexpected="bad",  # type: ignore[call-arg]
        )


def test_materialize_result_is_pydantic_extra_forbid() -> None:
    result = MaterializeResult(target="neo4j", status="succeeded")
    assert result.status == "succeeded"
    assert result.watermark is None

    with pytest.raises(ValidationError):
        MaterializeResult(target="x", status="succeeded", bogus=1)  # type: ignore[call-arg]


def test_dq_result_is_pydantic_extra_forbid() -> None:
    col_map = DqColumnMap(
        rule_id="rule_id_col",
        class_name="class_name_col",
        slot_name=None,
        offending_pk="pk_col",
        severity="severity_col",
        detail=None,
    )
    result = DqResult(
        offenders_table=None,
        column_map=col_map,
        passed=True,
        summary={"total": 0},
    )
    assert result.passed is True

    with pytest.raises(ValidationError):
        DqResult(
            column_map=col_map,
            passed=True,
            summary={},
            extra_bad="x",  # type: ignore[call-arg]
        )


def test_translate_result_is_pydantic_extra_forbid() -> None:
    result = TranslateResult(
        result_table=Path("/tmp/out.parquet"),
        column_map={"year": "year_col"},
    )
    assert result.column_map["year"] == "year_col"

    with pytest.raises(ValidationError):
        TranslateResult(
            result_table=Path("/tmp/x"),
            column_map={},
            oops="y",  # type: ignore[call-arg]
        )


def test_constraint_result_is_pydantic_extra_forbid() -> None:
    result = ConstraintResult(passed=True, summary="all ok")
    assert result.offenders_table is None

    with pytest.raises(ValidationError):
        ConstraintResult(passed=True, summary="x", bad=1)  # type: ignore[call-arg]


def test_derivation_result_is_pydantic_extra_forbid() -> None:
    result = DerivationResult(
        output_table=Path("/tmp/derived.parquet"),
        column_map={"decade": "decade_col"},
    )
    assert result.column_map["decade"] == "decade_col"

    with pytest.raises(ValidationError):
        DerivationResult(
            output_table=Path("/tmp/x"),
            column_map={},
            extra="bad",  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# ScoreColumnMap and DqColumnMap are typed
# ---------------------------------------------------------------------------

def test_score_column_map_fields() -> None:
    m = ScoreColumnMap(a_canonical="a", b_canonical="b", score="s")
    assert m.a_canonical == "a"
    assert m.b_canonical == "b"
    assert m.score == "s"

    with pytest.raises(ValidationError):
        ScoreColumnMap(a_canonical="a", b_canonical="b", score="s", extra="x")  # type: ignore[call-arg]


def test_dq_column_map_fields() -> None:
    m = DqColumnMap(
        rule_id="r",
        class_name="c",
        slot_name="s",
        offending_pk="pk",
        severity="sev",
        detail="d",
    )
    assert m.rule_id == "r"
    assert m.slot_name == "s"

    m2 = DqColumnMap(
        rule_id="r",
        class_name="c",
        slot_name=None,
        offending_pk="pk",
        severity="sev",
        detail=None,
    )
    assert m2.slot_name is None


# ---------------------------------------------------------------------------
# Fixture impl classes instantiate correctly
# ---------------------------------------------------------------------------

def test_b2_er_movie_impl_class() -> None:
    from tests.fixtures.B2.impls.er_movie import ERMovie
    import inspect
    assert issubclass(ERMovie, ERProtocol)
    sig = inspect.signature(ERMovie.score)
    assert "imdb" in sig.parameters
    assert "tmdb" in sig.parameters
    assert "wikidata" in sig.parameters


def test_b2_er_person_impl_class() -> None:
    from tests.fixtures.B2.impls.er_person import ERPerson
    assert issubclass(ERPerson, ERProtocol)
    assert ERPerson.disagreement_stance == ProtocolKind.DISAGREEMENT_AWARE


def test_b2_er_credit_impl_class() -> None:
    from tests.fixtures.B2.impls.er_credit import ERCredit
    assert issubclass(ERCredit, ERProtocol)


def test_b2_neo4j_publisher_impl_class() -> None:
    from tests.fixtures.B2.impls.neo4j_publisher import Neo4jPublisher
    assert issubclass(Neo4jPublisher, MaterializerProtocol)
    assert Neo4jPublisher.disagreement_stance == ProtocolKind.RESOLVED


def test_b2_iceberg_publisher_impl_class() -> None:
    from tests.fixtures.B2.impls.iceberg_publisher import IcebergPublisher
    assert issubclass(IcebergPublisher, MaterializerProtocol)


def test_c2_er_movie_impl_class() -> None:
    from tests.fixtures.C2.impls.er_movie import ERMovie
    assert issubclass(ERMovie, ERProtocol)
    assert ERMovie.disagreement_stance == ProtocolKind.DISAGREEMENT_AWARE


def test_c2_er_person_impl_class() -> None:
    from tests.fixtures.C2.impls.er_person import ERPerson
    assert issubclass(ERPerson, ERProtocol)


def test_c2_er_credit_impl_class() -> None:
    from tests.fixtures.C2.impls.er_credit import ERCredit
    assert issubclass(ERCredit, ERProtocol)


def test_c2_er_series_impl_class() -> None:
    from tests.fixtures.C2.impls.er_series import ERSeries
    assert issubclass(ERSeries, ERProtocol)


def test_c2_neo4j_publisher_impl_class() -> None:
    from tests.fixtures.C2.impls.neo4j_publisher import Neo4jPublisher
    assert issubclass(Neo4jPublisher, MaterializerProtocol)


def test_c2_iceberg_publisher_impl_class() -> None:
    from tests.fixtures.C2.impls.iceberg_publisher import IcebergPublisher
    assert issubclass(IcebergPublisher, MaterializerProtocol)
