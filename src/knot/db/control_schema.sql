-- knot control-plane schema (spec router scope)
-- Idempotent: CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
--
-- Two postgres schemas in play:
--   public     — control plane (spec_revisions; what knot knows)
--   knot_data  — data plane (per-class fact tables; what knot stores).
--                Per-class tables are created/altered by the migration
--                emitter (knot.migration) at publish time.

CREATE SCHEMA IF NOT EXISTS knot_data;

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

-- ──────────────────────────────────────────────────────────────────────────────
-- trust_config
-- Per-source trust scores. Runtime-editable, NOT draft → publish; affects
-- query-time resolution (see knot.db.resolve). Source name is FK by name to
-- the currently-published spec's Source entities (validated at the API layer).
-- Default 0.5 means "unconfigured = neutral."
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trust_config (
    source_name  TEXT             PRIMARY KEY,
    trust_score  DOUBLE PRECISION NOT NULL DEFAULT 0.5
                 CHECK (trust_score >= 0.0 AND trust_score <= 1.0),
    updated_at   TIMESTAMPTZ      NOT NULL DEFAULT now()
);

-- ──────────────────────────────────────────────────────────────────────────────
-- trust_posteriors
-- Per-(source, slot) Beta posterior driving bandit-style resolution
-- (THOMPSON_SAMPLING / UCB1). alpha and beta initialise at the uniform
-- prior (1, 1); each Bernoulli observation increments alpha (success) or
-- beta (failure). Mean = alpha / (alpha + beta); observation count =
-- alpha + beta - 2.
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trust_posteriors (
    source_name  TEXT             NOT NULL,
    slot_name    TEXT             NOT NULL,
    alpha        DOUBLE PRECISION NOT NULL DEFAULT 1.0
                 CHECK (alpha > 0),
    beta         DOUBLE PRECISION NOT NULL DEFAULT 1.0
                 CHECK (beta > 0),
    updated_at   TIMESTAMPTZ      NOT NULL DEFAULT now(),
    PRIMARY KEY (source_name, slot_name)
);
