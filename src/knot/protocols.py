"""Knot DI seam — protocols, DataContext, and typed return shapes.

Every team-bound pipeline stage implements one Protocol subclass.  The
disagreement_stance class variable drives which SDK slot type the codegen
emits (MultiValued[T] vs Resolved[T]) and whether knot attaches a
trust-resolution CTE when fulfilling DataContext views.

See design/staging/multi-valued-semantics.md and di-input-contract.md.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import ConfigDict

from knot.metaschema import (
    BoolExpr,
    Compare,
    DerivedSlot,
    OntologyClass,
    Slot,
    SlotPath,
    SpecBase,
)


# ---------------------------------------------------------------------------
# ProtocolKind — the four disagreement stances
# ---------------------------------------------------------------------------

class ProtocolKind(str, Enum):
    DISAGREEMENT_AWARE = "disagreement_aware"  # ER, DqNormalizeRunner
    RESOLVED           = "resolved"            # Materializer, Translator, Constraint/Derivation evals, DqMergeRunner
    ER_DECISION_LENS   = "er_decision_lens"    # DqResolveRunner: entity_bindings + canonical_id_lineage
    TARGET_DIRECT      = "target_direct"       # DqPublishRunner: reads the published artifact


# ---------------------------------------------------------------------------
# DataContext — declarative data need for static tracing
# ---------------------------------------------------------------------------

class DataContext(SpecBase):
    """Declares what data a bound impl reads.

    Detected by typing at registration (any ClassVar-annotated attribute
    whose type is DataContext).  Knot walks the expression tree to gather
    spec references without executing the impl.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    primary: (
        OntologyClass
        | list[OntologyClass]
        | DerivedSlot
        | list[DerivedSlot]
        | Any  # list-comp over spec.classes; resolved at registration
    )
    where: Compare | BoolExpr | None = None
    project: list[SlotPath | Slot] | None = None


# ---------------------------------------------------------------------------
# Protocol base
# ---------------------------------------------------------------------------

class Protocol(SpecBase):
    """Abstract base for all knot DI protocols.

    Subclasses declare disagreement_stance as a ClassVar.  Knot reads it
    at registration to determine the SDK slot type emitted for DataContext
    expressions and whether a trust-resolution CTE is attached.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    disagreement_stance: ClassVar[ProtocolKind]


# ---------------------------------------------------------------------------
# Typed return shapes
# ---------------------------------------------------------------------------

class ScoreColumnMap(SpecBase):
    """Column-name mapping for ERResult output tables.

    Impl writers name columns however they like; this map declares which
    column carries which semantic.  Knot reads this at pipeline integration
    time — it never guesses column names.
    """

    a_canonical: str
    b_canonical: str
    score: str


class ERResult(SpecBase):
    """Return shape for ERProtocol.score()."""

    output_uri: str
    column_map: ScoreColumnMap


class MaterializeResult(SpecBase):
    """Return shape for MaterializerProtocol.materialize()."""

    target: str
    status: Literal["succeeded", "failed", "partial"]
    watermark: str | None = None


class DqColumnMap(SpecBase):
    """Column-name mapping for DqResult offender tables."""

    rule_id: str
    class_name: str
    slot_name: str | None
    offending_pk: str
    severity: str
    detail: str | None


class DqResult(SpecBase):
    """Return shape for all DqRunner.check() methods."""

    offenders_uri: str | None = None
    column_map: DqColumnMap
    passed: bool
    summary: dict[str, int]


class TranslateResult(SpecBase):
    """Return shape for TranslatorProtocol.translate()."""

    result_uri: str
    column_map: dict[str, str]


class ConstraintResult(SpecBase):
    """Return shape for ConstraintEvaluator.evaluate()."""

    passed: bool
    offenders_uri: str | None = None
    summary: str


class DerivationResult(SpecBase):
    """Return shape for DerivationEvaluator.evaluate()."""

    output_uri: str
    column_map: dict[str, str]


# ---------------------------------------------------------------------------
# Protocol family
# ---------------------------------------------------------------------------

class ERProtocol(Protocol):
    """Entity-resolution impl.  Reads per_source_facts; outputs scored pairs."""

    disagreement_stance: ClassVar[ProtocolKind] = ProtocolKind.DISAGREEMENT_AWARE

    def score(self, ctx: Any, **datacontexts: Any) -> ERResult:
        raise NotImplementedError


class MaterializerProtocol(Protocol):
    """Materialization impl.  Reads resolved_facts; writes to an external target."""

    disagreement_stance: ClassVar[ProtocolKind] = ProtocolKind.RESOLVED

    def materialize(self, ctx: Any, **datacontexts: Any) -> MaterializeResult:
        raise NotImplementedError


class TranslatorProtocol(Protocol):
    """Translator impl — non-lake targets only (Neo4j, vector stores, etc.).

    Lake queries are served by knot's built-in SQL endpoint without a bound
    impl.  See multi-valued-semantics.md table and core-design.md § 13.
    """

    disagreement_stance: ClassVar[ProtocolKind] = ProtocolKind.RESOLVED

    def translate(self, ctx: Any, expression: Any, **datacontexts: Any) -> TranslateResult:
        raise NotImplementedError


class ConstraintEvaluator(Protocol):
    """Constraint evaluation — knot-internal; reads resolved_facts."""

    disagreement_stance: ClassVar[ProtocolKind] = ProtocolKind.RESOLVED

    def evaluate(self, ctx: Any, **datacontexts: Any) -> ConstraintResult:
        raise NotImplementedError


class DerivationEvaluator(Protocol):
    """Derivation evaluation — knot-internal; reads resolved_facts."""

    disagreement_stance: ClassVar[ProtocolKind] = ProtocolKind.RESOLVED

    def evaluate(self, ctx: Any, **datacontexts: Any) -> DerivationResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# DqRunner family
# ---------------------------------------------------------------------------

class DqRunnerBase(Protocol):
    """Common marker for all DQ impl subprotocols.

    Each subclass pins its own disagreement_stance because each runs at a
    different pipeline stage against different upstream tables.
    """

    disagreement_stance: ClassVar[ProtocolKind]

    def check(self, ctx: Any, **datacontexts: Any) -> DqResult:
        raise NotImplementedError


class DqNormalizeRunner(DqRunnerBase):
    """Runs after normalize.  Reads per_source_facts.  Source bag is the input."""

    disagreement_stance: ClassVar[ProtocolKind] = ProtocolKind.DISAGREEMENT_AWARE


class DqResolveRunner(DqRunnerBase):
    """Runs after resolve.  Reads entity_bindings + canonical_id_lineage."""

    disagreement_stance: ClassVar[ProtocolKind] = ProtocolKind.ER_DECISION_LENS


class DqMergeRunner(DqRunnerBase):
    """Runs after merge.  Reads resolved_facts; trust-CTE applies."""

    disagreement_stance: ClassVar[ProtocolKind] = ProtocolKind.RESOLVED


class DqPublishRunner(DqRunnerBase):
    """Runs after publish.  Reads the published artifact directly."""

    disagreement_stance: ClassVar[ProtocolKind] = ProtocolKind.TARGET_DIRECT


# ---------------------------------------------------------------------------
# QueryReader — lake-side execution seam (commitment 12)
# ---------------------------------------------------------------------------

class QueryReader(Protocol):
    """Sync SELECT against the lake.

    The sole execution seam for knot-generated SQL.  SQL is always produced
    by sql_gen.emit_sql(); this protocol only runs it.  Errors raise — never
    swallow (commitment 16 / query-executor.md).

    The return type is intentionally opaque (Any): the bound implementation
    decides the handoff format.  Arrow Table for DuckDB-on-fs, Iceberg
    snapshot ref for S3+Trino, view name for Spark.  Concrete impls may
    narrow the return type in their own class signatures — that is fine.

    disagreement_stance is RESOLVED: QueryReader runs after trust resolution
    CTEs are attached, so callers always receive winner-selected values.
    """

    disagreement_stance: ClassVar[ProtocolKind] = ProtocolKind.RESOLVED

    def read(self, ctx: Any, sql: str) -> Any:
        """Execute sql and return the result in a backend-appropriate format.

        Args:
            ctx: Per-run context object knot threads for logging/tracing.
            sql: SQL string from sql_gen.emit_sql().

        Returns:
            Backend-specific handle: pyarrow.Table (DuckDB), Iceberg snapshot
            ref (Trino/S3), view name (Spark), etc.

        Raises:
            Implementation-defined errors on connection failure, syntax
            error, timeout, or type mismatch.  Must never swallow.
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# RunContext — typed per-run context object passed to bound impls
# ---------------------------------------------------------------------------

class BindingInfo(SpecBase):
    """Identity of the bound impl being invoked."""

    stage: str
    class_name: str | None
    impl_name: str
    revision: int
    config_revision: int | None = None


class RunContext(SpecBase):
    """Typed context object the orchestrator passes to bound impl protocol methods.

    Exposed fields per impl-contract.md:
    - config: the impl's hydrated Config snapshot (typed at the impl class level)
    - run_id: current pipeline_runs row id
    - compile_hash: content-addressed identity of the WorkflowSpec
    - binding_info: identity of the bound impl (stage, class_name, impl_name, revision)
    - upstream_results: stage_key -> Result from prior stages in this run
    - query_reader: the bound QueryReader instance for ad-hoc reads
    - materializer: the bound Materializer for writes

    Not exposed: lake_dir, raw filesystem paths, postgres connection.
    """

    config: Any                        # impl's hydrated Config; typed at the impl class level
    run_id: int
    compile_hash: str
    binding_info: BindingInfo
    upstream_results: dict[str, Any]   # stage_key -> Result; populated from prior stages
    query_reader: Any                  # bound QueryReader instance
    materializer: Any                  # bound Materializer instance
