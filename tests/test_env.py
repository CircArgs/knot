"""Integration test environment for knot.

Wraps docker-compose stack (postgres + neo4j) + lake (local Iceberg) +
in-process toy orchestrator. Tests drive end-to-end runs and assert
against published outputs without each test reimplementing setup.

Working methods today: __init__, reset, teardown, cypher, cypher_one,
expected_facts, edge_cases.

Everything touching knot core (compiler, dispatcher, spec loader, run
records) raises NotImplementedError with a named dependency.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from neo4j import GraphDatabase, Driver
from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# Typed shapes
# ---------------------------------------------------------------------------


class Run(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    scope: str  # "Movie", "Credit", "full", "stage:resolve:Credit", etc.
    compile_hash: str
    pinned_parent_runs: dict[str, str]  # class_name → run_hash
    cache_keys: dict[str, str]          # stage_name → cache_key_hash
    cache_hit_stages: list[str]
    started_at: datetime
    completed_at: datetime | None
    status: Literal["pending", "running", "succeeded", "failed"]
    error: str | None


class RunSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runs: list[Run]
    full_pipeline_compile_hash: str


class RunArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    artifacts_per_stage: dict[str, bytes | str]  # stage_name → output payload


class Watermark(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_name: str
    value: str  # opaque per source


class SpecEdit(BaseModel):
    """Tagged-union of spec edits for impact analysis tests.

    Constructed via classmethods; the edit_type field is the discriminator.
    """

    model_config = ConfigDict(extra="forbid")

    edit_type: Literal[
        "rename_slot", "add_slot", "delete_slot", "change_resolution_policy"
    ]
    payload: dict[str, Any]

    @classmethod
    def rename_slot(cls, from_path: str, to_path: str) -> "SpecEdit":
        return cls(
            edit_type="rename_slot",
            payload={"from_path": from_path, "to_path": to_path},
        )

    @classmethod
    def add_slot(cls, class_: str, slot: dict) -> "SpecEdit":
        return cls(
            edit_type="add_slot",
            payload={"class_": class_, "slot": slot},
        )

    @classmethod
    def delete_slot(cls, path: str) -> "SpecEdit":
        return cls(
            edit_type="delete_slot",
            payload={"path": path},
        )

    @classmethod
    def change_resolution_policy(cls, path: str, new_policy: str) -> "SpecEdit":
        return cls(
            edit_type="change_resolution_policy",
            payload={"path": path, "new_policy": new_policy},
        )


class ERDecision(BaseModel):
    """Forced merge or non-merge decision for ER override tests."""

    model_config = ConfigDict(extra="forbid")

    decision_type: Literal["force_merge", "force_split"]
    canonical_ids: list[str]
    reason: str | None = None


class Walkback(BaseModel):
    """Audit walk-back result for a single canonical fact."""

    model_config = ConfigDict(extra="forbid")

    fact: dict
    canonical_id: str
    contributing_sources: set[str]
    er_decision: dict | None
    spec_revisions: dict[str, str]     # class_name → revision
    impl_revisions: dict[str, str]     # impl_name → content_hash
    config_revisions: dict[str, str]
    pinned_parent_runs: dict[str, str]
    compile_hash: str
    pipeline_run_id: str


class Impact(BaseModel):
    """Impact analysis result for a proposed spec edit."""

    model_config = ConfigDict(extra="forbid")

    affected_classes: set[str]
    affected_impls: set[str]
    affected_workflows: set[str]  # what would re-compile


class RegisteredImpl(BaseModel):
    """Returned by register_impl; holds binding metadata."""

    model_config = ConfigDict(extra="forbid")

    impl_name: str
    impl_module: str | None
    content_hash: str
    registered_at: datetime


# ---------------------------------------------------------------------------
# TestEnv
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestEnv:
    """Integration test environment for knot.

    Wraps docker-compose stack (postgres + neo4j) + lake (local Iceberg) +
    in-process toy orchestrator. Tests use this to drive end-to-end runs and
    assert against published outputs without each test reimplementing setup.

    Methods marked "works today" are fully implemented.
    Methods marked "requires knot.<module>" raise NotImplementedError.
    """

    __test__ = False  # not a pytest test class

    def __init__(
        self,
        postgres_dsn: str,
        neo4j_uri: str,
        neo4j_auth: tuple[str, str],
        lake_dir: Path,
    ) -> None:
        self._postgres_dsn = postgres_dsn
        self._neo4j_uri = neo4j_uri
        self._neo4j_auth = neo4j_auth
        self._lake_dir = lake_dir
        self._scenario: str | None = None
        self._neo4j_driver: Driver = GraphDatabase.driver(
            neo4j_uri, auth=neo4j_auth
        )

    # ------------------------------------------------------------------
    # Lifecycle  (works today)
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Truncate postgres control tables, clear neo4j, rm -rf lake_dir.

        Works today for neo4j + lake_dir; postgres truncation requires
        knot.schema to define table names.
        """
        # Clear neo4j graph
        with self._neo4j_driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")

        # Wipe lake dir
        if self._lake_dir.exists():
            shutil.rmtree(self._lake_dir)
        self._lake_dir.mkdir(parents=True, exist_ok=True)

        # Postgres: requires knot.schema for table list
        # When knot.schema lands, replace this with table truncation.
        # For now we clear what we can without that dependency.

    def teardown(self) -> None:
        """Close connections. Works today."""
        self._neo4j_driver.close()

    # ------------------------------------------------------------------
    # Scenario / fixture loading
    # ------------------------------------------------------------------

    def set_scenario(self, tier_name: str) -> None:
        """Load tier from tests/fixtures/<tier>/.

        Works today for recording the scenario name and validating the
        fixture directory exists. Seeding sources into the lake requires
        knot.normalize.
        """
        tier_dir = _FIXTURES_DIR / tier_name
        if not tier_dir.is_dir():
            raise FileNotFoundError(f"Fixture tier not found: {tier_dir}")
        self._scenario = tier_name

    def seed_sources(self, *source_names: str) -> None:
        """Load named CSVs into per_source_facts/.

        Requires knot.normalize to parse and write parquet/Iceberg.
        """
        raise NotImplementedError(
            "seed_sources requires knot.normalize (CSV → per_source_facts Iceberg)"
        )

    def register_impl(
        self, impl_name: str, impl_module: str | None = None
    ) -> RegisteredImpl:
        """Register a bound DI impl against the current spec.

        Requires knot.registry (impl load, DataContext validation, content hash).
        """
        raise NotImplementedError(
            "register_impl requires knot.registry (impl load + DataContext validation)"
        )

    def edit_config(self, impl_name: str, **fields: Any) -> None:
        """Update runtime-editable impl Config fields in postgres-control.

        Requires knot.control_plane (postgres config store).
        """
        raise NotImplementedError(
            "edit_config requires knot.control_plane (postgres config store)"
        )

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def update_source(self, source_name: str, row_changes: dict) -> Watermark:
        """Advance source watermark and write changed rows to the lake.

        Requires knot.source_layer (watermark management + lake write).
        """
        raise NotImplementedError(
            "update_source requires knot.source_layer (watermark + lake write)"
        )

    def edit_spec(self, edit: SpecEdit) -> None:
        """Apply a spec edit through the draft → publish gate.

        Requires knot.spec_loader + knot.compiler (publish gate validation).
        """
        raise NotImplementedError(
            "edit_spec requires knot.spec_loader + knot.compiler (publish gate)"
        )

    def edit_impl(self, impl_name: str, source_bytes: bytes) -> None:
        """Replace a bound impl's source bytes (active; takes effect on next compile).

        Requires knot.registry.
        """
        raise NotImplementedError(
            "edit_impl requires knot.registry (impl source replacement)"
        )

    def draft_impl(self, impl_name: str, source_bytes: bytes) -> None:
        """Write impl source as a draft (not yet active).

        Requires knot.registry.
        """
        raise NotImplementedError(
            "draft_impl requires knot.registry (draft impl staging)"
        )

    def publish_impl_draft(self, impl_name: str) -> None:
        """Publish a staged impl draft through the publish gate.

        Requires knot.registry + knot.compiler (publish gate).
        """
        raise NotImplementedError(
            "publish_impl_draft requires knot.registry + knot.compiler (publish gate)"
        )

    def submit_correction(
        self, class_: str, canonical_id: str, slot: str, value: Any
    ) -> None:
        """Write a user correction into postgres-control for migration at next run.

        Requires knot.control_plane (_user_corrections table).
        """
        raise NotImplementedError(
            "submit_correction requires knot.control_plane (_user_corrections table)"
        )

    def submit_er_decision(self, class_: str, decision: ERDecision) -> None:
        """Write a forced ER merge/split decision into postgres-control.

        Requires knot.control_plane (_user_er_decisions table).
        """
        raise NotImplementedError(
            "submit_er_decision requires knot.control_plane (_user_er_decisions table)"
        )

    # ------------------------------------------------------------------
    # Triggers
    # ------------------------------------------------------------------

    def run(
        self, scope: str, parents: dict[str, str] | None = None
    ) -> Run:
        """Compile and dispatch a run for the given scope.

        scope: class name ("Movie"), "full", or "stage:<kind>:<class>".
        parents: optional pinned parent run hashes override (cross-class pinning).

        Requires knot.compiler + knot.dispatcher.
        """
        raise NotImplementedError(
            "run requires knot.compiler (compile WorkflowSpec) + "
            "knot.dispatcher (submit to orchestrator)"
        )

    def run_full_pipeline(self) -> RunSet:
        """Compile and dispatch the full toposorted pipeline.

        Requires knot.compiler + knot.dispatcher.
        """
        raise NotImplementedError(
            "run_full_pipeline requires knot.compiler + knot.dispatcher"
        )

    def run_stage(self, stage: str, class_: str | None = None) -> Run:
        """Compile and dispatch a single pipeline stage.

        Requires knot.compiler + knot.dispatcher.
        """
        raise NotImplementedError(
            "run_stage requires knot.compiler + knot.dispatcher"
        )

    # ------------------------------------------------------------------
    # Run inspection
    # ------------------------------------------------------------------

    def last_run(self, scope: str) -> Run:
        """Return the most recent run record for scope (any status).

        Requires knot.run_store (pipeline_runs postgres table).
        """
        raise NotImplementedError(
            "last_run requires knot.run_store (pipeline_runs table)"
        )

    def last_completed_run(self, scope: str) -> Run:
        """Return the most recent succeeded run for scope.

        Requires knot.run_store.
        """
        raise NotImplementedError(
            "last_completed_run requires knot.run_store (pipeline_runs table)"
        )

    def last_compile_hash(self, scope: str) -> str:
        """Return the compile hash of the most recent succeeded run for scope.

        Requires knot.run_store.
        """
        raise NotImplementedError(
            "last_compile_hash requires knot.run_store (pipeline_runs table)"
        )

    def get_run(self, run_id: str) -> Run:
        """Fetch a specific run record by id.

        Requires knot.run_store.
        """
        raise NotImplementedError(
            "get_run requires knot.run_store (pipeline_runs table)"
        )

    def replay(self, compile_hash: str) -> RunArtifact:
        """Re-dispatch an existing compile hash; return per-stage artifacts.

        Requires knot.dispatcher (re-submit compiled WorkflowSpec by hash).
        """
        raise NotImplementedError(
            "replay requires knot.dispatcher (re-submit WorkflowSpec by compile_hash)"
        )

    def assert_byte_identical(self, a: RunArtifact, b: RunArtifact) -> None:
        """Assert two RunArtifacts are byte-identical across all stages.

        Implemented as a pure assertion once RunArtifacts exist.
        Raises AssertionError on mismatch.
        """
        if a.artifacts_per_stage.keys() != b.artifacts_per_stage.keys():
            raise AssertionError(
                f"Stage sets differ: {sorted(a.artifacts_per_stage)} "
                f"vs {sorted(b.artifacts_per_stage)}"
            )
        mismatches = [
            stage
            for stage in a.artifacts_per_stage
            if a.artifacts_per_stage[stage] != b.artifacts_per_stage[stage]
        ]
        if mismatches:
            raise AssertionError(
                f"Artifacts differ in stages: {mismatches}"
            )

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    def audit_walkback(self, class_: str, canonical_id: str) -> Walkback:
        """Walk the audit chain for a canonical fact.

        Requires knot.audit (pipeline_runs + compiled_workflows + entity_bindings).
        """
        raise NotImplementedError(
            "audit_walkback requires knot.audit "
            "(pipeline_runs + compiled_workflows + entity_bindings)"
        )

    def impact_analysis(self, spec_edit: SpecEdit) -> Impact:
        """Compute which classes/impls/workflows a spec edit would affect.

        Requires knot.compiler (single-dispatch visitor over typed entity tree).
        """
        raise NotImplementedError(
            "impact_analysis requires knot.compiler "
            "(single-dispatch visitor over spec reference graph)"
        )

    # ------------------------------------------------------------------
    # Target inspection  (works today)
    # ------------------------------------------------------------------

    def cypher(
        self, query: str, params: dict | None = None
    ) -> list[dict]:
        """Run a Cypher query against neo4j; return list of record dicts.

        Works today.
        """
        with self._neo4j_driver.session() as session:
            result = session.run(query, parameters=params or {})
            return [dict(record) for record in result]

    def cypher_one(
        self, query: str, params: dict | None = None
    ) -> dict:
        """Run a Cypher query and return exactly one record dict.

        Raises if zero or more than one record returned. Works today.
        """
        rows = self.cypher(query, params)
        if len(rows) != 1:
            raise AssertionError(
                f"cypher_one expected 1 row, got {len(rows)}"
            )
        return rows[0]

    def iceberg_table(self, name: str) -> Any:
        """Return a PyArrow Table for the named Iceberg table in lake_dir.

        Requires pyiceberg + a table written by a prior run.
        Raises NotImplementedError until knot.materialize has produced output.
        """
        # Defer pyiceberg import so the module loads without it installed.
        try:
            import pyiceberg.catalog  # type: ignore[import]
        except ImportError:
            raise NotImplementedError(
                "iceberg_table requires pyiceberg (pip install pyiceberg)"
            )
        raise NotImplementedError(
            "iceberg_table requires a prior knot.materialize run to have "
            "written the table into lake_dir"
        )

    def duckdb(self, sql: str) -> Any:
        """Run SQL against lake_dir via DuckDB; return a DuckDBPyRelation.

        Works today if DuckDB is installed and lake_dir contains parquet files.
        """
        try:
            import duckdb  # type: ignore[import]
        except ImportError:
            raise NotImplementedError(
                "duckdb requires duckdb (pip install duckdb)"
            )
        conn = duckdb.connect()
        conn.execute(f"SET home_directory='{self._lake_dir}'")
        return conn.execute(sql)

    # ------------------------------------------------------------------
    # Cache / incremental introspection
    # ------------------------------------------------------------------

    def cache_status(self, run_id: str) -> dict[str, Literal["hit", "miss"]]:
        """Return per-stage cache status for a completed run.

        Requires knot.run_store (pipeline_runs stage detail).
        """
        raise NotImplementedError(
            "cache_status requires knot.run_store (per-stage cache_hit records)"
        )

    # ------------------------------------------------------------------
    # Helpers  (works today)
    # ------------------------------------------------------------------

    def _scenario_dir(self) -> Path:
        if self._scenario is None:
            raise RuntimeError(
                "No scenario loaded. Call set_scenario() first."
            )
        return _FIXTURES_DIR / self._scenario

    def expected_facts(self) -> dict:
        """Parse expected_facts.yaml from the loaded scenario. Works today."""
        path = self._scenario_dir() / "expected_facts.yaml"
        with path.open() as fh:
            return yaml.safe_load(fh)

    def edge_cases(self) -> dict:
        """Parse edge_cases.yaml from the loaded scenario. Works today."""
        path = self._scenario_dir() / "edge_cases.yaml"
        with path.open() as fh:
            return yaml.safe_load(fh)
