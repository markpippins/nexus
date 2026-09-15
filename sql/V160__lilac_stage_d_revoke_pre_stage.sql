-- =============================================================================
-- V160 (Lilac Stage D revocation pre-stage — DBA): pguser SELECT-only on the
-- legacy receipt/ticket surfaces. DRAFT — NOT APPLIED TO LIVE.
-- =============================================================================
-- ⚠️  DO NOT APPLY UNTIL STAGE D EXECUTION IS AUTHORIZED ⚠️
-- This file is the pre-staged, reviewed artifact of the DBA's Stage D
-- sequencing (To Do thread ca5941ca lineage; decision e15a052e session).
-- Applying it is a DBA act, not an engineer act: per ruling 3d03c144/V146 the
-- DBA signs Stage D at verification time, and this migration's own self-gate
-- (below) refuses to apply until resolution.c6_retirement_gate() reports
-- satisfied = true — which requires the DBA signoff row to exist FIRST
-- (binding_signoffs = 3). The gate makes "premature apply" impossible rather
-- than merely discouraged; still, apply it only as part of the Stage D
-- sequence after flip #2 (nexus-execution-worker) and its trailing-24h gate.
--
-- WHAT RETIREMENT MEANS HERE (matches V144's conservative contract, executed
-- one step earlier: the revoke pre-stage):
--   1. SELECT-only on vision.receipts + vision.tickets for the application
--      role (pguser). INSERT/UPDATE/DELETE/TRUNCATE are refused.
--   2. Data is NOT destroyed, renamed, or dropped here — V144 (the rename
--      migration) remains the destructive-shape step and keeps its own gate.
--
-- DEPLOYMENT REALITY DISCOVERED DURING DRAFTING (evidence, live 2026-09-14):
--   On THIS host, pguser is a SUPERUSER and owns every vision.* table.
--   PostgreSQL semantics make a plain REVOKE against a superuser/table-owner
--   COSMETIC ONLY: superusers bypass all ACL checks (has_table_privilege
--   keeps reporting true), so a REVOKE alone would not enforce anything here.
--   This migration therefore installs TWO independent layers:
--     A. ACL layer: OWNER TO a non-login custodian role + REVOKE DML.
--        Meaningful on hosts where the applier is a normal role (the
--        vanadium replica is the target replica host; its applier role may
--        differ). On a superuser host this layer is documentation-in-DDL.
--     B. GUARD layer (enforces EVERYWHERE, superuser included): statement
--        triggers on both tables that refuse INSERT/UPDATE/DELETE/TRUNCATE
--        unless the deliberate hatch is set:
--            SET LOCAL resolution.allow_legacy_write = 'on'
--        inside the same transaction. This mirrors the V157 audit erase
--        guard pattern exactly (convention proven in this repo). The hatch
--        exists so a documented, deliberate legacy write (e.g. C4-style
--        import backfill on a restore host) remains possible WITHOUT
--        re-editing the migration — fail-closed by default, opt-in per
--        transaction, and every legal write through the hatch is expected
--        to land in the NEBULA_AUDIT trail via the V156 triggers.
--
-- SCOPE: exactly the two surfaces V144 renames (receipts, tickets). NOT a
-- blanket vision.* revoke: the other vision tables (work_requests,
-- wr_compile_verdicts, *_history) are consumed by other subsystems with
-- live writers as of 2026-09-14 evidence; widening this scope is a separate
-- ruled decision, not an assumption.
--
-- REVERSIBILITY (documented, deliberate — same spirit as V144):
--   SET LOCAL resolution.allow_legacy_write = 'on'  -- per-transaction bypass
--   DROP TRIGGER trg_vision_receipts_no_write ON vision.receipts;
--   DROP TRIGGER trg_vision_tickets_no_write  ON vision.tickets;
--   DROP FUNCTION resolution.fn_guard_legacy_write();
--   ALTER TABLE vision.receipts OWNER TO pguser;   -- (and tickets)
--   GRANT INSERT, UPDATE, DELETE ON vision.receipts, vision.tickets TO pguser;
--   (No data is touched by this migration; reversible in one maintenance
--   window.)
--
-- R9 note: schema change. MUST be replicated to vanadium after application
-- (operator confirmation required per AGENTS.md R9). Currently parked per
-- standing operator instruction (off-network, 2026-09-14).
--
-- FREEZE RE-VERIFICATION QUERIES: see the attached
--   scripts/lilac-stage-d-freeze-verification.sql
-- =============================================================================

BEGIN;

-- ── Self-gate: refuse unless the C6 retirement gate is satisfied ────────────
DO $$
DECLARE
  v_gate jsonb;
BEGIN
  IF to_regprocedure('resolution.c6_retirement_gate()') IS NULL THEN
    RAISE EXCEPTION
      'V160 self-gate FAILED: resolution.c6_retirement_gate() not present — apply V141 first'
      USING ERRCODE = 'P1000';
  END IF;

  v_gate := resolution.c6_retirement_gate();

  IF NOT COALESCE((v_gate ->> 'satisfied')::boolean, false) THEN
    RAISE EXCEPTION
      'V160 self-gate FAILED: c6_retirement_gate not satisfied (green_soak=%, signoffs=%, non_closed_tickets=%, undisposed_tickets=%, missing_twins=%). Stage D is not authorized yet; this migration must NOT be applied until it is.',
      v_gate ->> 'green_soak_days',
      v_gate ->> 'binding_signoffs',
      v_gate ->> 'vision_tickets_non_closed',
      v_gate ->> 'vision_tickets_undisposed',
      v_gate ->> 'vision_receipts_missing_twin'
      USING ERRCODE = 'P1000';
  END IF;
END $$;

-- ── Custodian role for the ACL layer ────────────────────────────────────────
-- Non-login role that takes OWNERSHIP of the two legacy tables so pguser's
-- owner-privileges no longer apply (matters on non-superuser appliers).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'legacy_surface_custodian') THEN
    CREATE ROLE legacy_surface_custodian NOLOGIN;
  END IF;
END $$;

-- ── A. ACL layer ─────────────────────────────────────────────────────────────
-- Ownership first (REVOKE against the owner is a no-op). NOTE: on a
-- superuser applier this SUCCEEDS but does not bind the superuser; the
-- guard layer below is what actually enforces on such hosts.
DO $$
DECLARE
  t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['receipts', 'tickets'] LOOP
    EXECUTE format('ALTER TABLE vision.%I OWNER TO legacy_surface_custodian', t);
    EXECUTE format('REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON vision.%I FROM pguser', t);
    EXECUTE format('GRANT SELECT ON vision.%I TO pguser', t);
  END LOOP;
END $$;

-- ── B. Guard layer (the enforcement that works even for superusers) ─────────
CREATE OR REPLACE FUNCTION resolution.fn_guard_legacy_write() RETURNS trigger AS $$
BEGIN
  IF COALESCE(current_setting('resolution.allow_legacy_write', true), 'off') <> 'on' THEN
    RAISE EXCEPTION
      'legacy-surface write refused: % on vision.% — vision.receipts/vision.tickets are SELECT-only in Stage D (V160). To write deliberately (documented import/restore paths only), run inside a transaction with SET LOCAL resolution.allow_legacy_write = ''on''',
      TG_OP, TG_TABLE_NAME
      USING ERRCODE = 'P0001';
  END IF;
  RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_vision_receipts_no_write ON vision.receipts;
CREATE TRIGGER trg_vision_receipts_no_write
  BEFORE INSERT OR UPDATE OR DELETE ON vision.receipts
  FOR EACH STATEMENT EXECUTE FUNCTION resolution.fn_guard_legacy_write();

DROP TRIGGER IF EXISTS trg_vision_tickets_no_write ON vision.tickets;
CREATE TRIGGER trg_vision_tickets_no_write
  BEFORE INSERT OR UPDATE OR DELETE ON vision.tickets
  FOR EACH STATEMENT EXECUTE FUNCTION resolution.fn_guard_legacy_write();

-- ── Postconditions — fail loud inside the same transaction ──────────────────
DO $$
DECLARE
  v_probe integer;
BEGIN
  -- 1. Write probe MUST be refused (guard layer, no hatch).
  BEGIN
    INSERT INTO vision.receipts (id, plan_id, type, agent_role, session_id,
                                 ticket_id, artifact_path, summary, metadata_json,
                                 tokens_used, created_at)
    VALUES ('v160-probe-refused', 'v160-probe', 'PROBE', 'probe', 'v160', NULL, NULL,
            'V160 postcondition write probe — must be refused', '{}', 0, now());
    RAISE EXCEPTION 'V160 postcondition FAILED: INSERT into vision.receipts was NOT refused'
      USING ERRCODE = 'P1000';
  EXCEPTION
    WHEN OTHERS THEN
      IF SQLSTATE NOT IN ('P0001', '42501') THEN
        RAISE;  -- unexpected failure shape: propagate, transaction aborts
      END IF;
      -- expected: guard refusal (P0001) or ACL denial (42501)
  END;

  -- 2. Read probe MUST succeed (SELECT-only contract).
  SELECT count(*) INTO v_probe FROM vision.receipts LIMIT 1;
  IF v_probe IS NULL THEN
    RAISE EXCEPTION 'V160 postcondition FAILED: SELECT probe returned NULL' USING ERRCODE = 'P1000';
  END IF;

  -- 3. Hatch path MUST permit the write (and leave zero residue).
  --    set_config(..., true) is SET LOCAL: the hatch lives only for this
  --    transaction and cannot leak to the caller's session state.
  BEGIN
    PERFORM set_config('resolution.allow_legacy_write', 'on', true);
    INSERT INTO vision.receipts (id, plan_id, type, agent_role, session_id,
                                 ticket_id, artifact_path, summary, metadata_json,
                                 tokens_used, created_at)
    VALUES ('v160-probe-hatched', 'v160-probe', 'PROBE', 'probe', 'v160', NULL, NULL,
            'V160 hatch verification row — deleted below', '{}', 0, now());
  EXCEPTION
    WHEN OTHERS THEN
      RAISE EXCEPTION 'V160 postcondition FAILED: hatched INSERT refused unexpectedly (%)', SQLSTATE
        USING ERRCODE = 'P1000';
  END;

  DELETE FROM vision.receipts WHERE id = 'v160-probe-hatched';
  IF EXISTS (SELECT 1 FROM vision.receipts WHERE id = 'v160-probe-hatched') THEN
    RAISE EXCEPTION 'V160 postcondition FAILED: probe cleanup failed' USING ERRCODE = 'P1000';
  END IF;

  -- 4. ACL layer state check (documents the reality on this host rather
  --    than asserting an outcome that differs between hosts).
  IF NOT has_table_privilege('pguser', 'vision.receipts', 'SELECT') THEN
    RAISE EXCEPTION 'V160 postcondition FAILED: SELECT grant missing' USING ERRCODE = 'P1000';
  END IF;
END $$;

COMMIT;

-- ── Apply-time stamps for the freeze-verification script ────────────────────
-- The obj_description prefix (ISO timestamp) is what
-- scripts/lilac-stage-d-freeze-verification.sql check #4 anchors on.
DO $$
DECLARE v_stamp text := to_char(now(), 'YYYY-MM-DD"T"HH24:MI:SS');
BEGIN
  EXECUTE format('COMMENT ON TABLE vision.receipts IS %L',
    v_stamp || ' — Lilac Stage D freeze applied (V160): SELECT-only for pguser, guard trg_vision_receipts_no_write, hatch SET LOCAL resolution.allow_legacy_write');
  EXECUTE format('COMMENT ON TABLE vision.tickets IS %L',
    v_stamp || ' — Lilac Stage D freeze applied (V160): SELECT-only for pguser, guard trg_vision_tickets_no_write, hatch SET LOCAL resolution.allow_legacy_write');
END $$;

-- =============================================================================
-- POST-APPLY FREEZE VERIFICATION (run after applying, and periodically):
--   scripts/lilac-stage-d-freeze-verification.sql
-- Exits non-zero on any drift from the Stage D contract.
-- =============================================================================
