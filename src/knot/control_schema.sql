-- knot control-plane schema (spec router scope)
-- Idempotent: CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
--
-- Only the spec_revisions table lives here.  Modeling-router tables
-- (compiled_workflows, pipeline_runs, bound_impls, impl_revision,
-- impl_config, _user_corrections, _user_er_decisions) belong to the
-- modeling router and land separately.

-- ──────────────────────────────────────────────────────────────────────────────
-- spec_revisions
-- One row per spec authoring revision.  Drafts have published=FALSE and
-- are mutable (UPDATEs in place as the user adds classes / slots / sources).
-- Published rows have published=TRUE; exactly one at a time may be published.
-- Drafts may be branched from a published parent (or from another draft).
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS spec_revisions (
    revision         SERIAL       PRIMARY KEY,
    spec             JSONB        NOT NULL,        -- full-fidelity spec dump
    content_hash     CHAR(64)     NOT NULL,        -- canonical_dump → sha256
    published        BOOLEAN      NOT NULL DEFAULT FALSE,
    parent_revision  INT          REFERENCES spec_revisions(revision),
    label            TEXT,                          -- optional human-friendly draft label
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    published_at     TIMESTAMPTZ
);

-- Exactly one row at a time may be published.
CREATE UNIQUE INDEX IF NOT EXISTS spec_revisions_one_published
    ON spec_revisions (published) WHERE published = TRUE;

CREATE INDEX IF NOT EXISTS spec_revisions_published_at
    ON spec_revisions (published_at) WHERE published_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS spec_revisions_parent
    ON spec_revisions (parent_revision);
