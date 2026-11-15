-- V219 — registry-backed request_kind + kind-agnostic backlog eligibility.
--
-- Plan 8261650 acceptance criterion 1 (WR canonicalization landing, Option B).
--
-- STAGED — NOT APPLIED. This file is the reviewed artifact, not a change.
-- Live application is admin-approval-gated: the DBA posts this file to the
-- Drafts forum and applies it only after the admin approves that exact scope,
-- per plan 8261650 AC1 ("DBA posts the alteration to Drafts and applies only
-- after admin approval; Builder must not start until applied").
--
-- ── Why this file exists at V219 (corrections to the V216 forum draft) ──────
--
-- The proposal for this criterion was posted as a forum-only DRAFT labelled
-- V216. Binding DBA decision 1a3d32d8-1b10-4097-b2b8-051f28c32683 reviewed the
-- DDL as sound but recorded three defects that blocked apply-as-is, all fixed
-- here:
--
--   D1 VERSION COLLISION. V216 is taken by V216__nebula_sol_concept_registry
--      (applied 2026-11-13); V217 and V218 are also applied. The filename, the
--      header, the ledger label and the backfill basis key all say V219 now.
--      (The draft's basis literal was 'v216:legacy-execution-store'; it is
--      'v219:legacy-execution-store' here. The admin approves the literal in
--      this file, not the one in the superseded draft.)
--
--   D2 HARD-CODED ROW COUNT. The draft expected "attested = 46"; live held 47
--      at review time and the count keeps moving. The backfill was already
--      count-independent (it keys on `request_kind IS NULL`); the defect was
--      in the *postcheck*, which would have reported a false failure. This
--      file states no row count anywhere in executable SQL — the postcheck
--      below compares the attested count to the table's own total, so it stays
--      true at any apply-time cardinality.
--
--   D3 NO FILE ON DISK. The DDL existed only inside a forum post and could not
--      be reviewed or applied as a file. This is that file; guard tests live in
--      bin/tests/test_v219_request_kind_migration.py.
--
-- Carried forward unchanged from the reviewed draft: the registry table, the
-- nullable no-default column, the attested backfill, the guarded FK, the
-- relational role-eligibility table, and the kind-agnostic backlog projection.
-- Two design points the DBA flagged as acceptable-but-worth-stating are
-- restated at their statements: the blanket `build` attestation, and the
-- deliberately unconstrained `role` text column.
--
-- Additive only. No DROP, no TRUNCATE, no DELETE, no data movement beyond one
-- attributable provenance key. Idempotent: the whole file may be re-run.
--
-- ── Postchecks (run after apply; no expected row count is hard-coded) ───────
--
--   -- every current row is registry-backed and attested, at whatever
--   -- cardinality the table holds when this runs:
--   SELECT
--     (SELECT count(*) FROM resolution.work_request)                        AS total,
--     (SELECT count(*) FROM resolution.work_request WHERE request_kind IS NULL)
--                                                                           AS null_kind,
--     (SELECT count(*) FROM resolution.work_request
--       WHERE context ? 'legacy_request_kind_basis')                        AS attested,
--     (SELECT count(*) FROM resolution.work_request wr
--       WHERE NOT EXISTS (SELECT 1 FROM resolution.request_kind_registry r
--                          WHERE r.request_kind = wr.request_kind))         AS unbacked;
--   -- expect null_kind = 0, unbacked = 0, attested = total
--
--   SELECT count(*) = 3 AS registry_ok
--     FROM resolution.request_kind_registry
--    WHERE request_kind IN ('build','attestation','decision');
--
--   SELECT count(*) AS coupled            -- expect 0
--     FROM pg_constraint
--    WHERE conrelid = 'resolution.work_request'::regclass
--      AND pg_get_constraintdef(oid) ILIKE '%request_kind%'
--      AND pg_get_constraintdef(oid) ILIKE '%business_status%';
--
--   SELECT count(*) FROM resolution.work_request_backlog;   -- 0 until
--   -- eligibility rows exist; that is correct, not a defect.
--
-- ── Rollback (not executed by this file) ───────────────────────────────────
--
--   BEGIN;
--   UPDATE resolution.work_request
--      SET context = context - 'legacy_request_kind_basis'
--    WHERE context ? 'legacy_request_kind_basis';
--   DROP VIEW  IF EXISTS resolution.work_request_backlog;
--   DROP TABLE IF EXISTS resolution.request_role_eligibility;
--   ALTER TABLE resolution.work_request DROP COLUMN IF EXISTS request_kind;
--   ALTER TABLE resolution.work_request
--       DROP CONSTRAINT IF EXISTS fk_work_request_request_kind;
--   DROP INDEX IF EXISTS resolution.idx_resolution_work_request_kind;
--   DROP TABLE IF EXISTS resolution.request_kind_registry;
--   COMMIT;
--
-- Post-rollback invariants: work_request is back to its pre-V219 column count,
-- none of the three new relations exists, and no row carries the basis key.

BEGIN;

-- A1.1  Registry. Extending the vocabulary is a ROW, never a migration and
--       never an enum ALTER.
CREATE TABLE IF NOT EXISTS resolution.request_kind_registry (
    request_kind text        PRIMARY KEY,
    description  text        NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE resolution.request_kind_registry IS
    'Registry-backed request kind axis (plan 8261650). Initial vocabulary: '
    'build, attestation, decision. Extension is a registry row. Independent of '
    'business_status: the two axes are never coupled by CHECK, trigger or '
    'generated column, and no cross-kind superstate is stored here.';

INSERT INTO resolution.request_kind_registry (request_kind, description) VALUES
    ('build',       'Work that produces or changes an artifact in the substrate'),
    ('attestation', 'Work whose deliverable is a verification/attestation record'),
    ('decision',    'Work whose deliverable is a recorded decision or ruling')
ON CONFLICT (request_kind) DO NOTHING;

-- A1.2  The column. Nullable on add and deliberately WITHOUT a default:
--       a writer that forgets the axis must leave an observable NULL, not be
--       silently labelled 'build'. ABSENT IS NOT A VALUE.
ALTER TABLE resolution.work_request
    ADD COLUMN IF NOT EXISTS request_kind text;

-- A1.3  Attested backfill of pre-existing rows. Count-independent by
--       construction: it keys on the NULL the column just acquired, so it is
--       correct at any cardinality and a no-op on re-run. The basis travels
--       with the value so a later reader can see this was an attestation with
--       a stated basis, not an inference from status or name.
--
--       Disclosed and reversible: every row that existed at apply time is
--       labelled 'build' until corrected. A pre-existing WorkRequest whose
--       true kind is 'attestation' or 'decision' is mislabelled by this
--       blanket attestation — auditable via the basis key, and the admin is
--       asked to approve that explicitly.
UPDATE resolution.work_request
   SET request_kind = 'build',
       context = COALESCE(context, '{}'::jsonb)
              || jsonb_build_object('legacy_request_kind_basis',
                                    'v219:legacy-execution-store')
 WHERE request_kind IS NULL;

-- A1.4  FK to the registry. Constraint DDL has no IF NOT EXISTS form.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'resolution.work_request'::regclass
           AND conname  = 'fk_work_request_request_kind'
    ) THEN
        ALTER TABLE resolution.work_request
            ADD CONSTRAINT fk_work_request_request_kind
            FOREIGN KEY (request_kind)
            REFERENCES resolution.request_kind_registry (request_kind);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_resolution_work_request_kind
    ON resolution.work_request (request_kind);

-- A2  Relational role eligibility: one row per (request, role), so the
--     projection stays indexable and auditable instead of encoding role
--     eligibility in context JSON.
CREATE TABLE IF NOT EXISTS resolution.request_role_eligibility (
    request_id uuid        NOT NULL
               REFERENCES resolution.work_request (id) ON DELETE CASCADE,
    role       text        NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (request_id, role)
);

COMMENT ON TABLE resolution.request_role_eligibility IS
    'Roles that may claim a WorkRequest (plan 8261650). Relational by design. '
    'Eligibility is not a context/JSON convention and not a lifecycle proxy.';

CREATE INDEX IF NOT EXISTS idx_request_role_eligibility_role
    ON resolution.request_role_eligibility (role);

-- A3  Kind-agnostic backlog: open on the lifecycle axis, unclaimed, and
--     role-eligible. Deliberately NO request_kind predicate — an attestation
--     or decision request is exactly as eligible as a build request. The view
--     uses `wr.*` and is therefore frozen at creation: a future column on
--     work_request needs CREATE OR REPLACE VIEW to become visible here.
CREATE OR REPLACE VIEW resolution.work_request_backlog AS
SELECT
    wr.*,
    ARRAY(
        SELECT e.role
          FROM resolution.request_role_eligibility e
         WHERE e.request_id = wr.id
         ORDER BY e.role
    ) AS eligible_roles
FROM resolution.work_request wr
WHERE wr.business_status IN ('DRAFT', 'APPROVED')
  AND wr.consumed_at IS NULL
  AND EXISTS (
        SELECT 1 FROM resolution.request_role_eligibility e
         WHERE e.request_id = wr.id
      );

COMMENT ON VIEW resolution.work_request_backlog IS
    'Kind-agnostic backlog eligibility projection (plan 8261650): open, '
    'unclaimed, role-eligible WorkRequests across ALL kinds. Not `category IS '
    'NULL`, not DRAFT-only, not a nullness proxy.';

COMMIT;
