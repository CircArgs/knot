-- knot control-plane schema
-- Bootstrap with CREATE TABLE IF NOT EXISTS; idempotent on re-apply.
-- compiled_workflows rows are NEVER deleted — audit walk-back depends on
-- every prior compile hash remaining dereferenceable indefinitely.

-- ──────────────────────────────────────────────────────────────────────────────
-- compiled_workflows
-- Content-addressed store of compiled WorkflowSpecs (core-design § 3).
-- The hash IS the run's identity; rows here are immutable once inserted.
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS compiled_workflows (
    hash                   CHAR(64)    PRIMARY KEY,   -- sha256 hex of canonical_dump
    canonicalizer_version  SMALLINT    NOT NULL DEFAULT 1,
    spec                   JSONB       NOT NULL,       -- full canonicalized WorkflowSpec
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Time-range scans over compiled_workflows (audit, retention reporting).
CREATE INDEX IF NOT EXISTS compiled_workflows_created_at
    ON compiled_workflows (created_at);

-- ──────────────────────────────────────────────────────────────────────────────
-- pipeline_runs
-- One row per dispatched run; always references a compiled_workflows hash.
-- scope identifies what was run: class name, "full", or "stage:resolve:Credit".
-- cache_keys / cache_hit_stages capture the incremental-execution plan
-- (incremental-execution.md).
-- pinned_parent_runs carries cross-class pin map for relation classes
-- (cross-class-pinning.md): {"Movie": "<hash>", "Person": "<hash>"}.
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id                 BIGSERIAL    PRIMARY KEY,
    compile_hash       CHAR(64)     NOT NULL REFERENCES compiled_workflows (hash),
    scope              VARCHAR(255) NOT NULL,  -- e.g. "Movie", "full", "stage:resolve:Credit"
    pinned_parent_runs JSONB        NOT NULL DEFAULT '{}'::jsonb,
    cache_keys         JSONB        NOT NULL DEFAULT '{}'::jsonb,   -- per-stage cache key
    cache_hit_stages   TEXT[]       NOT NULL DEFAULT ARRAY[]::TEXT[],
    started_at         TIMESTAMPTZ  NOT NULL,
    completed_at       TIMESTAMPTZ,
    status             VARCHAR(32)  NOT NULL
                           CHECK (status IN ('pending', 'running', 'succeeded', 'failed')),
    error              TEXT
);

CREATE INDEX IF NOT EXISTS pipeline_runs_compile_hash_status
    ON pipeline_runs (compile_hash, status);

CREATE INDEX IF NOT EXISTS pipeline_runs_scope_completed_at
    ON pipeline_runs (scope, completed_at DESC);

-- ──────────────────────────────────────────────────────────────────────────────
-- impl_source
-- Python source for bound impls stored in knot (core-design § 5).
-- Revision is a monotonic counter per name; is_published = false means draft.
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS impl_source (
    name          VARCHAR(255) NOT NULL,
    revision      INTEGER      NOT NULL,
    source_bytes  BYTEA        NOT NULL,
    content_hash  CHAR(64)     NOT NULL,
    is_published  BOOLEAN      NOT NULL DEFAULT false,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (name, revision)
);

-- ──────────────────────────────────────────────────────────────────────────────
-- impl_config
-- Pydantic-dumped Config snapshots for bound impls (datacontext-config-binding.md).
-- ConfigRef substitution at compile time reads the current revision here.
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS impl_config (
    impl_name       VARCHAR(255) NOT NULL,
    revision        INTEGER      NOT NULL,
    config_snapshot JSONB        NOT NULL,
    content_hash    CHAR(64)     NOT NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (impl_name, revision)
);

-- ──────────────────────────────────────────────────────────────────────────────
-- _user_corrections
-- Operator-submitted field corrections (core-design § 13).
-- Live in postgres briefly, then migrate to the lake at the next pipeline run
-- via the _user_corrections source.  applied_at_run_id tracks which run
-- carried the correction into the lake.
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS _user_corrections (
    id               BIGSERIAL    PRIMARY KEY,
    class_name       VARCHAR(255) NOT NULL,
    canonical_id     VARCHAR(255) NOT NULL,
    slot_name        VARCHAR(255) NOT NULL,
    value            JSONB        NOT NULL,
    submitted_by     VARCHAR(255),           -- operator identity; no auth enforcement
    submitted_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    applied_to_lake  BOOLEAN      NOT NULL DEFAULT false,
    applied_at_run_id BIGINT      REFERENCES pipeline_runs (id)
);

CREATE INDEX IF NOT EXISTS user_corrections_class_applied
    ON _user_corrections (class_name, applied_to_lake);

-- ──────────────────────────────────────────────────────────────────────────────
-- _user_er_decisions
-- Operator-submitted force-merge / force-split decisions.
-- canonical_ids is the set of entity ids the decision covers.
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS _user_er_decisions (
    id                BIGSERIAL   PRIMARY KEY,
    class_name        VARCHAR(255) NOT NULL,
    decision_type     VARCHAR(32)  NOT NULL
                          CHECK (decision_type IN ('force_merge', 'force_split')),
    canonical_ids     TEXT[]       NOT NULL,
    reason            TEXT,
    submitted_by      VARCHAR(255),
    submitted_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    applied_to_lake   BOOLEAN      NOT NULL DEFAULT false,
    applied_at_run_id BIGINT       REFERENCES pipeline_runs (id)
);
