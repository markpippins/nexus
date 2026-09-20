-- =============================================================================
-- V192 — coordination_checkpoints + v_coordination_blackboard (DBA)
-- Blackboard V1 slice 1: per-role, per-item-kind checkpoint state + the
-- derived blackboard view. Companion doctrine:
--   - Coordination Blackboard proposal ........ discussions 88385a46
--   - DBA position (hardening points 1-4) ...... comment 97131e6f on 88385a46
--   - To Do lifecycle policy v0.1 .............. discussions ac2d1382
--     (doctrine record 86d65d5d; SLA + stale criteria + bucket vocabulary)
--
-- STAGED INERT: nothing on live changes until applied on explicit operator go,
-- same activation posture as V167/V169/V170/V184/V189.
--
-- Design contract (hardening point 1: NO new write paths for blackboard items):
--   * The blackboard is DERIVED on read. The only new WRITE surface is
--     nebula.coordination_checkpoints — per-role, per-item-kind
--     last-reviewed state, advanced deliberately by an agent or the boot shim
--     at review time. Never written by the pipeline.
--   * Buckets are computed in the view, never stored — staleness cannot drift
--     from reality because it does not exist as data.
--   * Routing: to-do threads are routed by the de facto leading-bracket token
--     convention ([engineer], [inspector→engineer] takes the FIRST role token
--     as primary addressee; the arrow form is preserved in the title). Tokens
--     are validated against nebula.roles; unmapped tokens (barbie, bug, eng,
--     ops … observed in the live census) surface as bucket 'unrouted' —
--     visible, never silently dropped, never guessed. Structured addressee
--     storage is an upstream fix and deliberately NOT invented here.
--
-- SURVEYED LIVE STATE (titanium, 2026-09-20):
--   * migration head ......... V191 (this file is V192)
--   * assembly.forums.slug ... 'to-do' -> id 836a1dec (443 live posts, 89 at
--     rating 0, 309 at >=4; posts.created is the live timestamp surface)
--   * assembly.comments.role . populated on 82,591/82,712 rows -> addressee
--     comment attribution is real data, not heuristic
--   * nebula.roles ........... name text PK (FK target); the V190 lesson says
--     CHECK-drift is a failure mode — FK to the live roles table is born-clean
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Preflight gates — loud refusals, per house pattern.
-- -----------------------------------------------------------------------------
DO $preflight$
DECLARE
    v_count bigint;
BEGIN
    -- P1: out-of-order guard against the house migration ledger.
    -- (Live has NO nebula.schema_migrations; the ledger of record is
    -- resolution.migration_ledger — verified 2026-09-20.)
    IF to_regclass('resolution.migration_ledger') IS NOT NULL THEN
        SELECT count(*) INTO v_count FROM resolution.migration_ledger
         WHERE migration_label ~ '^V19[3-9]' OR migration_label ~ '^V[2-9][0-9][0-9]';
        IF v_count > 0 THEN
            RAISE EXCEPTION 'PREFLIGHT P1 FAIL: migrations newer than V192 present in the ledger (%) — this file is out of order', v_count
                USING HINT = 'Check sql/ head and resolution.migration_ledger; do not apply out-of-order migrations.';
        END IF;
    END IF;

    -- P2: the to-do forum must exist and be unique (the view folds it).
    SELECT count(*) INTO v_count FROM assembly.forums WHERE slug = 'to-do';
    IF v_count <> 1 THEN
        RAISE EXCEPTION 'PREFLIGHT P2 FAIL: expected exactly 1 assembly.forums row with slug=to-do, found %', v_count
            USING HINT = 'The blackboard view folds the to-do forum; without it the view is empty or ambiguous.';
    END IF;

    -- P3: the canonical agent-records surface must exist (inbox fold source).
    IF to_regclass('nebula.agent_records_history') IS NULL THEN
        RAISE EXCEPTION 'PREFLIGHT P3 FAIL: nebula.agent_records_history is absent'
            USING HINT = 'The inbox fold reads agent records; apply the base schema first.';
    END IF;

    -- P4: nebula.roles must exist and be populated (FK target + token mapping).
    IF to_regclass('nebula.roles') IS NULL THEN
        RAISE EXCEPTION 'PREFLIGHT P4 FAIL: nebula.roles is absent';
    END IF;
    SELECT count(*) INTO v_count FROM nebula.roles;
    IF v_count = 0 THEN
        RAISE EXCEPTION 'PREFLIGHT P4 FAIL: nebula.roles is empty — nothing to FK against or map tokens to';
    END IF;

    -- P5: this migration must not already exist (loud, not IF NOT EXISTS).
    IF to_regclass('nebula.coordination_checkpoints') IS NOT NULL THEN
        RAISE EXCEPTION 'PREFLIGHT P5 FAIL: nebula.coordination_checkpoints already exists'
            USING HINT = 'V192 is single-apply; re-apply is a drift signal, not an idempotent no-op.';
    END IF;
END
$preflight$;

-- -----------------------------------------------------------------------------
-- coordination_checkpoints — the ONE new write surface (deliberate, manual).
-- -----------------------------------------------------------------------------
CREATE TABLE nebula.coordination_checkpoints (
    role             text        NOT NULL REFERENCES nebula.roles(name)
                                 ON UPDATE CASCADE ON DELETE RESTRICT,
    item_kind        text        NOT NULL
                     CONSTRAINT coordination_checkpoints_item_kind_check
                     CHECK (item_kind IN ('inbox','todo','discussions','issues','change-log')),
    last_reviewed_at timestamptz NOT NULL DEFAULT to_timestamp(0),
    reviewed_by_model text,
    note             text,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT coordination_checkpoints_pk PRIMARY KEY (role, item_kind)
);

COMMENT ON TABLE  nebula.coordination_checkpoints IS
'Per-role, per-item-kind review checkpoint (blackboard V1, V192). The ONLY write surface of the coordination blackboard: advanced deliberately by an agent or boot shim at review time ("seen everything of this kind up to last_reviewed_at"). last_reviewed_at defaults to epoch = never reviewed. Never written by any pipeline.';
COMMENT ON COLUMN nebula.coordination_checkpoints.last_reviewed_at IS
'Review boundary: items of this kind created at/after this instant are NEW for the role. to_timestamp(0) = never reviewed.';

-- updated_at maintenance trigger (house pattern).
CREATE OR REPLACE FUNCTION nebula.tg_coordination_checkpoints_touch()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$fn$;

CREATE TRIGGER trg_coordination_checkpoints_touch
    BEFORE UPDATE ON nebula.coordination_checkpoints
    FOR EACH ROW
    EXECUTE FUNCTION nebula.tg_coordination_checkpoints_touch();

-- Seed one never-reviewed row per existing role x every kind: the checkpoint
-- surface is born visible (the shim can advance any cell immediately). Absence
-- is also handled by the view (LEFT JOIN + COALESCE), so roles created after
-- V192 degrade to 'never reviewed' honestly instead of erroring.
INSERT INTO nebula.coordination_checkpoints (role, item_kind, last_reviewed_at)
SELECT r.name, k.kind, to_timestamp(0)
FROM nebula.roles r
CROSS JOIN (VALUES ('inbox'),('todo'),('discussions'),('issues'),('change-log')) AS k(kind);

-- -----------------------------------------------------------------------------
-- v_coordination_blackboard — the derived fold. Read-only by construction.
--
-- One row per attention item, with the role it concerns and its bucket:
--   todo fold (assembly to-do forum):
--     bucket 'action-needed'  rating 0/1 inside the 72h ack SLA, or rating 6
--                             (reopened needs a pass)
--     bucket 'awaiting-pickup' rating 0/1 past the 72h SLA but < 14d
--     bucket 'stale'          rating 0/1, > 14d old, no addressee-role comment
--                             (STALE-UNACKED, policy ac2d1382 section 4)
--     bucket 'in-flight'      rating 2/3/8
--     bucket 'done'           rating 4/5/7
--     bucket 'unrouted'       token absent or not a nebula.roles name
--   inbox fold (nebula agent records tagged to:<role>):
--     bucket 'action-needed'  record created at/after the role's inbox
--                             checkpoint (epoch = never reviewed => everything)
--     bucket 'seen'           record predates the checkpoint
--   checkpoint branch: one row per (role, kind) with freshness, so a single
--   SELECT serves the boot shim's whole boot digest.
-- Policy constants (72h ack SLA, 14d stale window) are pinned here in SQL —
-- changing them is a reviewed migration edit, not a mutable runtime knob.
-- -----------------------------------------------------------------------------
CREATE VIEW nebula.v_coordination_blackboard AS
WITH todo_base AS (
    SELECT p.id                AS thread_id,
           p.title,
           p.created,
           p.rating,
           (regexp_match(p.title, '^\[([a-z0-9-]+)'))[1] AS raw_token,
           EXISTS (SELECT 1 FROM assembly.comments c
                    WHERE c.post_id = p.id AND c.role =
                        (SELECT r.name FROM nebula.roles r
                          WHERE r.name = (regexp_match(p.title, '^\[([a-z0-9-]+)'))[1]))
                      AS addressee_commented
    FROM assembly.posts p
    JOIN assembly.forums f ON f.id = p.forum_uuid
    WHERE f.slug = 'to-do'
),
todo_routed AS (
    SELECT t.*,
           r.name AS routed_role,
           (t.rating IN (2,3,8)) AS in_flight,
           (t.rating IN (4,5,7)) AS done
    FROM todo_base t
    LEFT JOIN nebula.roles r ON r.name = t.raw_token
)
-- ---- to-do fold -------------------------------------------------------------
SELECT 'todo'::text                          AS item_kind,
       tr.thread_id                          AS item_id,
       tr.title,
       tr.routed_role,       CASE
         WHEN tr.routed_role IS NULL                 THEN 'unrouted'
         WHEN tr.done                                THEN 'done'
         WHEN tr.in_flight                           THEN 'in-flight'
         WHEN tr.rating = 6                          THEN 'action-needed'   -- reopened
         WHEN tr.addressee_commented                 THEN 'in-flight'       -- acked but rating unadvanced (policy §6)
         WHEN (now() AT TIME ZONE 'UTC') - (tr.created AT TIME ZONE 'UTC') > interval '14 days'
                                                     THEN 'stale'
         WHEN (now() AT TIME ZONE 'UTC') - (tr.created AT TIME ZONE 'UTC') > interval '72 hours'
                                                     THEN 'awaiting-pickup'
         ELSE 'action-needed'
       END                                   AS bucket,
       tr.rating                             AS status_rating,
       tr.created,
       CASE
         WHEN tr.routed_role IS NULL  THEN 'no leading role token or token not in nebula.roles'
         WHEN tr.done                 THEN 'terminal state (accepted/rejected/closed)'
         WHEN tr.in_flight            THEN 'picked up / in progress / operator-approved'
         WHEN tr.rating = 6           THEN 'reopened — needs another pass'
         WHEN tr.addressee_commented  THEN 'acknowledged by addressee but status never advanced — policy §6 violation'
         WHEN (now() AT TIME ZONE 'UTC') - (tr.created AT TIME ZONE 'UTC') > interval '14 days'
                                      THEN 'STALE-UNACKED: >14d, no addressee-role comment'
         WHEN (now() AT TIME ZONE 'UTC') - (tr.created AT TIME ZONE 'UTC') > interval '72 hours'
                                      THEN 'past 72h ack SLA — awaiting visible pickup'
         ELSE 'inside 72h ack SLA'
       END                                   AS reason,
       (SELECT max(c.created) FROM assembly.comments c
         WHERE c.post_id = tr.thread_id)      AS last_activity_at
FROM todo_routed tr
UNION ALL
-- ---- inbox fold -------------------------------------------------------------
SELECT 'inbox'::text,
       ar.id,
       left(ar.title, 200),
       substring(t.role from 4) AS routed_role_target,
       CASE WHEN ar.created_at >= COALESCE(cp.last_reviewed_at, to_timestamp(0))
            THEN 'action-needed' ELSE 'seen' END,
       NULL::bigint,
       ar.created_at,
       CASE WHEN ar.created_at >= COALESCE(cp.last_reviewed_at, to_timestamp(0))
            THEN 'tagged ' || t.role || ' — newer than inbox checkpoint'
            ELSE 'predates inbox checkpoint' END,
       ar.created_at
FROM nebula.agent_records_history ar
CROSS JOIN LATERAL unnest(ar.tags) AS t(role)
LEFT JOIN nebula.coordination_checkpoints cp
       ON cp.role = substring(t.role from 4) AND cp.item_kind = 'inbox'
WHERE t.role LIKE 'to:%'
  AND length(t.role) > 3
  -- validate against roles: only emit rows whose target role exists
  AND EXISTS (SELECT 1 FROM nebula.roles r WHERE r.name = substring(t.role from 4))
UNION ALL
-- ---- checkpoint freshness branch --------------------------------------------
SELECT cp.item_kind,
       NULL::uuid,
       cp.role || ' / ' || cp.item_kind || ' checkpoint',
       cp.role,
       'checkpoint',
       NULL::bigint,
       cp.last_reviewed_at,
       CASE WHEN cp.last_reviewed_at = to_timestamp(0)
            THEN 'never reviewed'
            ELSE 'reviewed ' || to_char(cp.last_reviewed_at, 'YYYY-MM-DD HH24:MI') || 'Z' END,
       cp.updated_at
FROM nebula.coordination_checkpoints cp;

COMMENT ON VIEW nebula.v_coordination_blackboard IS
'The coordination blackboard (V192, blackboard V1): derived per-role attention fold over to-do threads + tagged agent records + checkpoint freshness. Buckets: action-needed / awaiting-pickup / stale / in-flight / done / unrouted / seen / checkpoint. Read-only by construction — the only write surface is nebula.coordination_checkpoints. Doctrine: discussions 88385a46, ac2d1382.';

-- -----------------------------------------------------------------------------
-- Inert-until-applied notice: this migration CHANGES LIVE when applied. It is
-- additive (one new table + one view + trigger/function); it touches no
-- existing surface. Rollback: DROP VIEW nebula.v_coordination_blackboard;
-- DROP TRIGGER trg_coordination_checkpoints_touch ON nebula.coordination_checkpoints;
-- DROP FUNCTION nebula.tg_coordination_checkpoints_touch();
-- DROP TABLE nebula.coordination_checkpoints;  (checkpoint state is the only
-- thing lost, and it is rebuildable by re-advancing reviews)
-- -----------------------------------------------------------------------------
