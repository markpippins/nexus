-- =============================================================================
-- V161 (DBA): system_logs retention policy — prune routine categories, audit
-- categories fully excepted.
-- =============================================================================
-- Motivation (R1 eac2ece4, 2026-09-14): the canonical audit arc now feeds
-- tackle.system_logs (V155 REGISTRY_AUDIT, V156 NEBULA_AUDIT, V159 KG_AUDIT)
-- alongside routine noise (API_ROUTER ~300 rows/day). No retention policy
-- exists anywhere; routine noise grows unbounded. The audit categories are
-- already protected against *interactive* deletion by the V157 erase guard
-- (tackle.fn_guard_audit_erase); this migration adds scheduled *retention*
-- pruning for routine categories with the audit categories doubly excepted:
--   1. A CHECK constraint on the policy table REFUSES any policy row whose
--      category is in tackle.audit_log_categories() — the exception is
--      structural, not a data state (a future admin cannot accidentally
--      prune audit rows by adding a policy).
--   2. The prune function additionally hard-excludes the audit categories in
--      its DELETE ... WHERE, independent of policy contents.
--   3. The V157 erase guard remains the independent, already-live protection
--      for the audit categories: even an unprincipled DELETE without the
--      allow_audit_erase hatch is refused at the trigger.
-- ERASE-HATCH COMPATIBILITY: the guard can exempt a category from deletion
-- only when its category is in its V157 exclusion list. V157 currently pins
-- REGISTRY_AUDIT/NEBULA_AUDIT/KG_AUDIT. If a future audit category is added
-- to tackle.audit_log_categories() WITHOUT a matching V157 guard-list update,
-- the guard would NOT refuse its deletion — so this migration makes the
-- policy table REFUSE such a category outright (fail loud at policy-writing
-- time, not silently at prune time). See the CHECK below.
--
-- Why pruning, not partitioning: live volume is ~330 rows total (~38 MB on
-- disk incl. indexes/toast overhead), ~300 routine rows/day projected
-- (~110k/year). Pruning with the existing btree timestamp index handles this
-- indefinitely; declarative partitioning would be operational overhead with
-- no benefit at this scale (and would break the V157 guard's unpartitioned
-- trigger assumptions without careful rework). Revisit partitioning only if
-- daily routine volume exceeds ~1M rows.
--
-- Numbering: V159 = PR #245 (open), V160 reserved for the Lilac Stage D
-- revocation pre-stage (R1 47a214f1 lineage). This is V161.
--
-- R9 note: schema change. Normally MUST be replicated to vanadium; per
-- standing operator instruction (2026-09-14), replication is PARKED while
-- away from the home network — vanadium sync currently failing; this DDL
-- will reach it on the next healthy catch-up.
-- =============================================================================

BEGIN;

-- ── 1. The audit-category definition — single source of truth ───────────────
-- Extended from the V157/V159 category lists. Keep in sync with:
--   * tackle.audit_trail / tackle.recent_audit views (V157/V159)
--   * tackle.fn_guard_audit_erase exclusion list (V157)
--   * tackle-srv clearLogs() exclusion (typescript/tackle-srv/src/...)
CREATE OR REPLACE FUNCTION tackle.audit_log_categories()
RETURNS text[] LANGUAGE sql STABLE AS $$
  SELECT ARRAY['REGISTRY_AUDIT', 'NEBULA_AUDIT', 'KG_AUDIT']::text[];
$$;

COMMENT ON FUNCTION tackle.audit_log_categories() IS
  'Canonical audit-category definition (V161): fully excepted from retention pruning and, via the V157 erase guard, from all unprincipled deletion. Every new audit category MUST be added here AND to the V157 guard list AND to clearLogs() — the retention policy table refuses a category missing from the guard list so the gap fails loud.';

-- ── 2. Policy table — one retention rule per pruneable category ─────────────
CREATE TABLE IF NOT EXISTS tackle.system_logs_retention_policy (
  category            text PRIMARY KEY
                      -- Structural exception: audit categories may never get
                      -- a policy row. The prune function additionally
                      -- hard-excludes them (defense in depth).
                      CHECK (NOT (category = ANY (tackle.audit_log_categories()))),
  retain_days         integer NOT NULL CHECK (retain_days BETWEEN 1 AND 3650),
  enabled             boolean NOT NULL DEFAULT true,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE tackle.system_logs_retention_policy IS
  'Retention policy for tackle.system_logs (V161). One row per pruneable category; audit categories (tackle.audit_log_categories()) are structurally excepted — the CHECK refuses policy rows for them, and the prune function hard-excludes them independent of this table.';

-- ── 3. Seed policies — evidence-based (R1 eac2ece4 inventory) ───────────────
-- API_ROUTER: pure access noise (~296 rows/day), 14 days of troubleshooting
--   value, then worthless.
-- SYSTEM: service lifecycle notes, low volume, keep 90 days for incident
--   reconstruction.
-- Unlisted categories: pruned ONLY if a policy row exists (default-deny for
--   pruning) — a new category appearing is never silently deleted.
INSERT INTO tackle.system_logs_retention_policy (category, retain_days)
VALUES ('API_ROUTER', 14), ('SYSTEM', 90)
ON CONFLICT (category) DO NOTHING;

-- ── 4. Prune function — batched, reportable, dry-run capable ────────────────
-- Returns a JSONB run report; never deletes more than batch_limit per DELETE
-- (loop with small sleeps so a large backlog cannot pin the table or starve
-- the audit writers). Audit categories are hard-excluded in the WHERE clause
-- itself, independent of policy rows.
CREATE OR REPLACE FUNCTION tackle.prune_system_logs(
  p_dry_run boolean DEFAULT false,
  p_batch_limit integer DEFAULT 5000,
  p_max_batches integer DEFAULT 200
) RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE
  v_batch        integer := 0;
  v_deleted      bigint  := 0;
  v_cutoff       timestamptz;
  v_total_deleted bigint := 0;
  v_total_scanned bigint;
  v_report       jsonb;
BEGIN
  IF p_dry_run THEN
    SELECT count(*) INTO v_total_scanned FROM tackle.system_logs s
    WHERE EXISTS (
      SELECT 1 FROM tackle.system_logs_retention_policy p
      WHERE p.category = s.category AND p.enabled
        AND s.timestamp < now() - make_interval(days => p.retain_days)
    )
      AND NOT (s.category = ANY (tackle.audit_log_categories()));
    RETURN jsonb_build_object(
      'dry_run', true, 'would_delete', v_total_scanned,
      'audit_categories', tackle.audit_log_categories(),
      'computed_at', now());
  END IF;

  FOR v_batch IN 1..p_max_batches LOOP
    -- Recompute the cutoff per batch (cheap) and delete at most one batch.
    DELETE FROM tackle.system_logs s
    WHERE s.ctid IN (
      SELECT s2.ctid FROM tackle.system_logs s2
      JOIN tackle.system_logs_retention_policy p ON p.category = s2.category
      WHERE p.enabled
        AND s2.timestamp < now() - make_interval(days => p.retain_days)
        AND NOT (s2.category = ANY (tackle.audit_log_categories()))
      ORDER BY s2.timestamp
      LIMIT p_batch_limit
    );
    GET DIAGNOSTICS v_deleted = ROW_COUNT;
    v_total_deleted := v_total_deleted + v_deleted;
    EXIT WHEN v_deleted < p_batch_limit;
    PERFORM pg_sleep(0.1);
  END LOOP;

  v_report := jsonb_build_object(
    'dry_run', false,
    'deleted', v_total_deleted,
    'batches', v_batch,
    'audit_categories', tackle.audit_log_categories(),
    'policy_rows', (SELECT count(*) FROM tackle.system_logs_retention_policy WHERE enabled),
    'ran_at', now());

  -- Self-observation: the run itself is attributable evidence.
  INSERT INTO tackle.system_logs (id, "timestamp", level, category, message, source, details)
  VALUES ('retention-run-' || to_char(now(), 'YYYYMMDD"T"HH24MISS"Z"'),
          now(), 'INFO', 'SYSTEM',
          'retention prune: ' || v_total_deleted || ' rows deleted (audit categories excepted)',
          'tackle.prune_system_logs', v_report)
  ON CONFLICT (id) DO NOTHING;
  RETURN v_report;
END;
$$;

COMMENT ON FUNCTION tackle.prune_system_logs(boolean, integer, integer) IS
  'V161 retention pruning for tackle.system_logs: batched DELETE of rows whose category has an enabled policy and whose age exceeds retain_days. Audit categories (tackle.audit_log_categories()) are hard-excluded in the WHERE — double protection alongside the V157 erase guard. Dry-run mode returns the would-delete count without touching data.';

-- ── 5. Postconditions — fail loud, transaction aborts otherwise ─────────────
DO $$
DECLARE
  v_bad_policy integer;
  v_guard_missing integer;
BEGIN
  -- 5a. No policy row may name an audit category (structural exception).
  SELECT count(*) INTO v_bad_policy FROM tackle.system_logs_retention_policy
  WHERE category = ANY (tackle.audit_log_categories());
  IF v_bad_policy > 0 THEN
    RAISE EXCEPTION 'V161 postcondition FAILED: % audit-category policy row(s) exist', v_bad_policy
      USING ERRCODE = 'P1000';
  END IF;

  -- 5b. Guard bridge: every audit category must be covered by the V157 erase
  -- guard's exclusion list — otherwise the fail-loud design has a hole.
  SELECT count(*) INTO v_guard_missing
  FROM unnest(tackle.audit_log_categories()) AS c
  WHERE NOT EXISTS (
    SELECT 1 FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'tackle' AND p.proname = 'fn_guard_audit_erase'
      AND pg_get_functiondef(p.oid) LIKE '%''' || c || '''%'
  );
  IF v_guard_missing > 0 THEN
    RAISE EXCEPTION
      'V161 postcondition FAILED: % audit category(ies) missing from fn_guard_audit_erase exclusion list — add there before enabling retention', v_guard_missing
      USING ERRCODE = 'P1000';
  END IF;

  -- 5c. Dry-run must work and report the audit categories.
  IF (tackle.prune_system_logs(true) ->> 'audit_categories') IS NULL THEN
    RAISE EXCEPTION 'V161 postcondition FAILED: dry-run report lacks audit_categories' USING ERRCODE = 'P1000';
  END IF;
END $$;

COMMIT;
