-- =============================================================================
-- V220 — request↔requirement and request↔artifact link substrate
-- Plan 8261650 AC11/AC12 · STAGED · NOT APPLIED TO LIVE
-- =============================================================================
-- AC11: replace the single `source_requirement_id` shortcut with a normalized
--       M2M `request_requirement_link(request_id, requirement_id, relation,
--       provenance)`; no single-source shortcut.
-- AC12: replace the polymorphic `(kind, ref)` artefact edge with
--       `artifact(id, kind, canonical_ref)` plus
--       `request_artifact_link(request_id, artifact_id, role, ...)`; no
--       polymorphic edge.
--
-- This file is STAGED PREP, not completion of AC11/AC12. The live cutover
-- still has to drop `source_requirement_id`, repoint every consumer, remove
-- the legacy polymorphic artefact carrier, and validate the links. That is a
-- separate operator-gated step; this migration performs no live write.
--
-- Binding role inputs:
--   * Architect 9eeda282-* follow-up decision: bitemporal link rows, surrogate
--     UUID PKs, partial-current uniqueness, RESTRICT FKs, required provenance,
--     closed CHECK vocabularies, old column kept/deprecated.
--   * DBA reconciliation: approved for staging only, with a pre-flight
--     relkind='r' guard on both FK target tables; live apply rejected; R9 no
--     replication. The old column is retained comment-only (no write-guard
--     trigger) because existing writers still write it.
--
-- Additive and idempotent: no DROP, TRUNCATE, DELETE, column rewrite, or data
-- repair in executable SQL. Re-running the file is a no-op apart from the
-- count-gated backfill, which inserts only missing links.
-- =============================================================================

BEGIN;

-- ── pre-flight: FK targets must be base tables ─────────────────────────────
-- V044 records that `work_requests.source_requirement_id` was once deliberately
-- left without an FK because `requirements` could be a VIEW. V220 cannot add
-- FKs to a view; abort by name instead of failing mid-migration if the target
-- relation kind has drifted again.
DO $$
DECLARE
    v_wr_kind "char";
    v_req_kind "char";
BEGIN
    IF to_regclass('resolution.work_request') IS NULL THEN
        RAISE EXCEPTION 'V220-PREFLIGHT-001: resolution.work_request is missing';
    END IF;
    IF to_regclass('resolution.requirement') IS NULL THEN
        RAISE EXCEPTION 'V220-PREFLIGHT-002: resolution.requirement is missing';
    END IF;

    SELECT relkind INTO v_wr_kind
      FROM pg_class WHERE oid = 'resolution.work_request'::regclass;
    SELECT relkind INTO v_req_kind
      FROM pg_class WHERE oid = 'resolution.requirement'::regclass;

    IF v_wr_kind <> 'r' OR v_req_kind <> 'r' THEN
        RAISE EXCEPTION
            'V220-PREFLIGHT-003: FK targets must be base tables (work_request=%, requirement=%); aborting for relation-kind drift',
            v_wr_kind, v_req_kind;
    END IF;
END $$;

-- ── request ↔ requirement ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS resolution.request_requirement_link (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id        uuid        NOT NULL
                      REFERENCES resolution.work_request (id) ON DELETE RESTRICT,
    requirement_id    uuid        NOT NULL
                      REFERENCES resolution.requirement (id) ON DELETE RESTRICT,
    relation          text        NOT NULL,
    provenance        text        NOT NULL,
    valid_from        timestamptz NOT NULL DEFAULT now(),
    valid_until       timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    recorded_on_dt    timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    CONSTRAINT request_requirement_link_relation_check
        CHECK (relation IN ('source', 'derives_from', 'basis_of', 'refines', 'validates')),
    CONSTRAINT request_requirement_link_provenance_check
        CHECK (btrim(provenance) <> ''),
    CONSTRAINT request_requirement_link_window_check
        CHECK (valid_until >= valid_from),
    CONSTRAINT request_requirement_link_recorded_window_check
        CHECK (recorded_until_dt >= recorded_on_dt)
);

COMMENT ON TABLE resolution.request_requirement_link IS
    'Plan 8261650 AC11: normalized many-to-many request↔requirement links. '
    'Bitemporal audit evidence; one CURRENT link per pair, historical links '
    'remain. Provenance is required and is not defaulted.';
COMMENT ON COLUMN resolution.request_requirement_link.relation IS
    'Link semantics; closed staging vocabulary source|derives_from|basis_of|refines|validates.';
COMMENT ON COLUMN resolution.request_requirement_link.provenance IS
    'Why/how this link exists. Required; backfill stamps v220:legacy-source_requirement_id.';
COMMENT ON COLUMN resolution.request_requirement_link.valid_until IS
    'Infinity means the link is currently valid. Expire before deleting either endpoint.';

CREATE UNIQUE INDEX IF NOT EXISTS uq_request_requirement_link_current
    ON resolution.request_requirement_link (request_id, requirement_id)
    WHERE valid_until = 'infinity'::timestamptz
      AND recorded_until_dt = 'infinity'::timestamptz;
CREATE INDEX IF NOT EXISTS idx_request_requirement_link_request
    ON resolution.request_requirement_link (request_id);
CREATE INDEX IF NOT EXISTS idx_request_requirement_link_requirement
    ON resolution.request_requirement_link (requirement_id);
CREATE INDEX IF NOT EXISTS idx_request_requirement_link_relation
    ON resolution.request_requirement_link (relation);

-- ── artifact ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS resolution.artifact (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    kind              text        NOT NULL,
    canonical_ref     text        NOT NULL,
    valid_from        timestamptz NOT NULL DEFAULT now(),
    valid_until       timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    recorded_on_dt    timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    CONSTRAINT artifact_kind_check
        CHECK (kind IN ('schema', 'document', 'binary', 'reference')),
    CONSTRAINT artifact_canonical_ref_check
        CHECK (btrim(canonical_ref) <> ''),
    CONSTRAINT artifact_window_check
        CHECK (valid_until >= valid_from),
    CONSTRAINT artifact_recorded_window_check
        CHECK (recorded_until_dt >= recorded_on_dt)
);

COMMENT ON TABLE resolution.artifact IS
    'Plan 8261650 AC12: canonical artefact identity. `kind` and '
    '`canonical_ref` are separate columns, not a polymorphic (kind, ref) pair '
    'carried on the link. Bitemporal audit evidence.';
COMMENT ON COLUMN resolution.artifact.canonical_ref IS
    'Stable reference in the artefact kind''s own namespace (path, URI, schema id, ...).';

CREATE UNIQUE INDEX IF NOT EXISTS uq_artifact_current
    ON resolution.artifact (kind, canonical_ref)
    WHERE valid_until = 'infinity'::timestamptz
      AND recorded_until_dt = 'infinity'::timestamptz;
CREATE INDEX IF NOT EXISTS idx_artifact_kind
    ON resolution.artifact (kind);

-- ── request ↔ artifact ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS resolution.request_artifact_link (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id        uuid        NOT NULL
                      REFERENCES resolution.work_request (id) ON DELETE RESTRICT,
    artifact_id       uuid        NOT NULL
                      REFERENCES resolution.artifact (id) ON DELETE RESTRICT,
    role              text        NOT NULL,
    provenance        text        NOT NULL,
    valid_from        timestamptz NOT NULL DEFAULT now(),
    valid_until       timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    recorded_on_dt    timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    CONSTRAINT request_artifact_link_role_check
        CHECK (role IN ('primary', 'supporting', 'evidence', 'output', 'input')),
    CONSTRAINT request_artifact_link_provenance_check
        CHECK (btrim(provenance) <> ''),
    CONSTRAINT request_artifact_link_window_check
        CHECK (valid_until >= valid_from),
    CONSTRAINT request_artifact_link_recorded_window_check
        CHECK (recorded_until_dt >= recorded_on_dt)
);

COMMENT ON TABLE resolution.request_artifact_link IS
    'Plan 8261650 AC12: normalized request↔artifact link with an explicit '
    'role and FK to resolution.artifact. No polymorphic (kind, ref) edge. '
    'Bitemporal audit evidence; expire before deleting either endpoint.';
COMMENT ON COLUMN resolution.request_artifact_link.role IS
    'Link role; closed staging vocabulary primary|supporting|evidence|output|input.';

CREATE UNIQUE INDEX IF NOT EXISTS uq_request_artifact_link_current
    ON resolution.request_artifact_link (request_id, artifact_id)
    WHERE valid_until = 'infinity'::timestamptz
      AND recorded_until_dt = 'infinity'::timestamptz;
CREATE INDEX IF NOT EXISTS idx_request_artifact_link_request
    ON resolution.request_artifact_link (request_id);
CREATE INDEX IF NOT EXISTS idx_request_artifact_link_artifact
    ON resolution.request_artifact_link (artifact_id);
CREATE INDEX IF NOT EXISTS idx_request_artifact_link_role
    ON resolution.request_artifact_link (role);

-- ── backfill the old single-source shortcut ────────────────────────────────
-- One link per non-null source_requirement_id. The link inherits the source
 -- row's bitemporal window so retired work requests produce retired links. The
-- existence check is deliberately window-independent: re-running the file
-- must not append a second row for a closed work request.
DO $$
DECLARE
    v_source bigint;
    v_backfilled bigint;
BEGIN
    SELECT count(*) INTO v_source
      FROM resolution.work_request
     WHERE source_requirement_id IS NOT NULL;

    INSERT INTO resolution.request_requirement_link
        (request_id, requirement_id, relation, provenance,
         valid_from, valid_until, recorded_on_dt, recorded_until_dt)
    SELECT w.id,
           w.source_requirement_id,
           'source',
           'v220:legacy-source_requirement_id',
           w.valid_from,
           w.valid_until,
           w.recorded_on_dt,
           w.recorded_until_dt
      FROM resolution.work_request w
     WHERE w.source_requirement_id IS NOT NULL
       AND NOT EXISTS (
           SELECT 1
             FROM resolution.request_requirement_link l
            WHERE l.request_id = w.id
              AND l.requirement_id = w.source_requirement_id
              AND l.relation = 'source'
              AND l.provenance = 'v220:legacy-source_requirement_id'
       );

    SELECT count(*) INTO v_backfilled
      FROM resolution.request_requirement_link l
      JOIN resolution.work_request w
        ON w.id = l.request_id
       AND w.source_requirement_id = l.requirement_id
     WHERE l.relation = 'source'
       AND l.provenance = 'v220:legacy-source_requirement_id';

    IF v_backfilled <> v_source THEN
        RAISE EXCEPTION
            'V220-BACKFILL-001: non-null source_requirement_id rows (%) != backfilled links (%)',
            v_source, v_backfilled;
    END IF;
END $$;

-- Compatibility rule for the staging window: the old column remains readable
-- and writable so existing writers keep working. It is not authoritative;
-- request_requirement_link is. A later cutover drops it after consumer repoint.
COMMENT ON COLUMN resolution.work_request.source_requirement_id IS
    'DEPRECATED (V220 staging): retained for existing readers/writers. '
    'Authoritative requirement links live in resolution.request_requirement_link. '
    'Dropped only at the live AC11 cutover after consumer repoint.';

-- ── postconditions (count-independent) ─────────────────────────────────────
DO $$
DECLARE
    v_missing bigint;
BEGIN
    IF to_regclass('resolution.request_requirement_link') IS NULL
       OR to_regclass('resolution.artifact') IS NULL
       OR to_regclass('resolution.request_artifact_link') IS NULL THEN
        RAISE EXCEPTION 'V220-POST-001: one or more V220 relations are missing';
    END IF;

    SELECT count(*) INTO v_missing
      FROM resolution.work_request w
     WHERE w.source_requirement_id IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM resolution.request_requirement_link l
            WHERE l.request_id = w.id
              AND l.requirement_id = w.source_requirement_id
              AND l.relation = 'source'
              AND l.provenance = 'v220:legacy-source_requirement_id'
       );
    IF v_missing > 0 THEN
        RAISE EXCEPTION 'V220-POST-002: % non-null source_requirement_id rows still lack their backfilled link', v_missing;
    END IF;

    IF EXISTS (
        SELECT 1 FROM resolution.request_requirement_link l
         WHERE NOT EXISTS (SELECT 1 FROM resolution.work_request w WHERE w.id = l.request_id)
            OR NOT EXISTS (SELECT 1 FROM resolution.requirement r WHERE r.id = l.requirement_id)
    ) THEN
        RAISE EXCEPTION 'V220-POST-003: orphan request_requirement_link row detected';
    END IF;
END $$;

COMMIT;

-- =============================================================================
-- ROLLBACK (rehearsal only; NOT executed by this file)
-- =============================================================================
-- BEGIN;
-- DROP TABLE IF EXISTS resolution.request_artifact_link;
-- DROP TABLE IF EXISTS resolution.artifact;
-- DROP TABLE IF EXISTS resolution.request_requirement_link;
-- COMMIT;
--
-- Post-rollback invariant: the three V220 tables and their indexes/comments are
-- gone; resolution.work_request.source_requirement_id and its data are intact.
-- =============================================================================