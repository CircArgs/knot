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

-- ──────────────────────────────────────────────────────────────────────────────
-- _user_corrections
-- Immortal audit log of user-submitted corrections. ``correction_type`` is
-- a real Pydantic-class discriminator (Pattern 1); ``payload`` is the
-- typed model dump. ``applied_revision`` pins the spec revision active
-- when the correction landed, so audit walk-back is mechanical.
--
-- The data-plane effect (mutation of knot_data.<class>) lives in the
-- per-class table tagged with ``_source = '_user_corrections'``; this
-- log is the source of truth for "what was submitted, when, by whom."
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS _user_corrections (
    id                SERIAL       PRIMARY KEY,
    correction_type   TEXT         NOT NULL,
    payload           JSONB        NOT NULL,
    applied_by        TEXT,
    applied_revision  INTEGER      NOT NULL REFERENCES spec_revisions(revision),
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS user_corrections_type
    ON _user_corrections (correction_type);

-- ──────────────────────────────────────────────────────────────────────────────
-- canonical_id_lineage
-- Append-only event log for canonical_id transitions: ingest, merge, split,
-- correction. Lineage is technically derivable from the SCD2 bindings tables
-- (knot_data.<class>_bindings) but this table is the human-readable audit
-- convenience: "who merged X into Y, when, by which correction, under which
-- spec revision."
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS canonical_id_lineage (
    event_id           SERIAL       PRIMARY KEY,
    class_name         TEXT         NOT NULL,
    change_type        TEXT         NOT NULL,
    from_canonical_ids TEXT[]       NOT NULL,
    to_canonical_ids   TEXT[]       NOT NULL,
    applied_revision   INTEGER      NOT NULL REFERENCES spec_revisions(revision),
    correction_id      INTEGER      REFERENCES _user_corrections(id),
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS canonical_id_lineage_class
    ON canonical_id_lineage (class_name, created_at DESC);

-- ──────────────────────────────────────────────────────────────────────────────
-- users
-- API-key-based auth. Each user has a username and an SHA-256 hash of
-- their issued bearer token (raw token never stored). is_admin gates
-- user-management endpoints. Hashes are 64 hex chars; uniqueness lets
-- the auth dep do a single indexed lookup per request.
-- ──────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    username      TEXT         PRIMARY KEY,
    api_key_hash  CHAR(64)     UNIQUE NOT NULL,
    is_admin      BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);
