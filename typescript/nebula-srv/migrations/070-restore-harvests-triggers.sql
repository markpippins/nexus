-- 070-restore-harvests-triggers.sql — restore the lost INSTEAD OF triggers on
-- nebula.harvests (bitemporal view) and ratchet their presence at the DB layer.
--
-- Defect: thread `e0a40d6d` / DBA verification 2026-09-30 — the live `nexus`
-- database has ZERO triggers on nebula.harvests, while the bitemporal upgrade
-- (scd-type4-bitemporal-upgrade.sql, hand-applied 2026-09-27/28; the ledger
-- gap 59-66 in nebula.schema_version is exactly that hand-applied window)
-- requires three INSTEAD OF triggers for the view to be writable at all:
-- trg_harvests_insert / trg_harvests_update / trg_harvests_delete, each
-- FOR EACH ROW EXECUTE FUNCTION nebula.harvests_{insert,update,delete}_trigger().
-- The 10 harvest trigger functions remain present live (verified); only the
-- trigger bindings were lost. No DROP TRIGGER appears in the DDL logs since
-- 2026-09-24 (log_statement='ddl' active) — consistent with the same unlogged
-- apply path that produced the d93fecf4 charter incident.
--
-- Consequence if left broken: INSERT/UPDATE/DELETE against nebula.harvests
-- fail with "cannot insert into view" (42809) — every harvest write from
-- nebula-srv / harvest pipeline would fail. Functions present, bindings lost.
--
-- Relkind-conditional on purpose: a FRESH numbered-chain apply produces
-- nebula.harvests as a TABLE (the bitemporal upgrade is unnumbered and never
-- runs there), where INSTEAD OF triggers are illegal. On such environments
-- 070 is a no-op that leaves a marker row documenting the check. The numbered
-- chain's own table triggers (023 lineage) remain the numbered chain's truth;
-- this migration only restores the bitemporal VIEW wiring that exists on
-- hand-upgraded environments like titanium.
--
-- Self-asserting: the final DO block re-verifies the restored set and ABORTS
-- the migration (raising) if any is missing — a half-restored state can never
-- be recorded as applied. (Set-based guard per Decision 23's spirit: the DB
-- itself refuses to certify an incomplete restore.)
--
-- DBA lane per Decision 26 `00d339f9` routing; defect thread `e0a40d6d`.

-- Relkind: 'v' = view (bitemporal-upgraded), 'r' = table (fresh chain).
CREATE TEMP TABLE _070_relkind AS
SELECT c.relkind FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'nebula' AND c.relname = 'harvests';

DO $$
DECLARE
  v_relkind "char";
BEGIN
  SELECT relkind INTO v_relkind FROM _070_relkind;
  IF v_relkind IS NULL THEN
    RAISE EXCEPTION '070: nebula.harvests does not exist';
  END IF;

  IF v_relkind = 'v' THEN
    -- Byte-faithful to scd-type4-bitemporal-upgrade.sql section 11.
    EXECUTE 'DROP TRIGGER IF EXISTS trg_harvests_insert ON nebula.harvests';
    EXECUTE 'CREATE TRIGGER trg_harvests_insert
                INSTEAD OF INSERT ON nebula.harvests
                FOR EACH ROW EXECUTE FUNCTION nebula.harvests_insert_trigger()';
    EXECUTE 'DROP TRIGGER IF EXISTS trg_harvests_update ON nebula.harvests';
    EXECUTE 'CREATE TRIGGER trg_harvests_update
                INSTEAD OF UPDATE ON nebula.harvests
                FOR EACH ROW EXECUTE FUNCTION nebula.harvests_update_trigger()';
    EXECUTE 'DROP TRIGGER IF EXISTS trg_harvests_delete ON nebula.harvests';
    EXECUTE 'CREATE TRIGGER trg_harvests_delete
                INSTEAD OF DELETE ON nebula.harvests
                FOR EACH ROW EXECUTE FUNCTION nebula.harvests_delete_trigger()';
  ELSE
    RAISE NOTICE '070: nebula.harvests is relkind % (numbered-chain TABLE). No view triggers to restore; the 023-lineage table triggers are the numbered chain''s truth. Recording a marker row.', v_relkind::text;
  END IF;
END $$;

-- Record what this environment got, for the trigger-loss audit trail.
CREATE TABLE IF NOT EXISTS nebula.trigger_restore_log (
  version      INTEGER PRIMARY KEY,
  restored_on  TIMESTAMPTZ NOT NULL DEFAULT now(),
  relkind      "char" NOT NULL,
  restored     TEXT[] NOT NULL
);
INSERT INTO nebula.trigger_restore_log (version, relkind, restored)
VALUES (70,
        (SELECT relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
          WHERE n.nspname='nebula' AND c.relname='harvests'),
        CASE WHEN (SELECT relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname='nebula' AND c.relname='harvests') = 'v'
             THEN ARRAY['trg_harvests_insert','trg_harvests_update','trg_harvests_delete']
             ELSE ARRAY['<table: numbered-chain triggers own this surface>'] END)
ON CONFLICT (version) DO UPDATE
  SET restored_on = now(), relkind = EXCLUDED.relkind, restored = EXCLUDED.restored;

-- ── Self-assertion: the migration must fail if the restore is incomplete ──
DO $$
DECLARE
  v_relkind "char";
  v_found int;
BEGIN
  SELECT relkind INTO v_relkind FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname='nebula' AND c.relname='harvests';

  IF v_relkind = 'v' THEN
    SELECT count(*) INTO v_found
      FROM pg_trigger t
      JOIN pg_class c ON c.oid = t.tgrelid
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname='nebula' AND c.relname='harvests' AND NOT t.tgisinternal
       AND t.tgname IN ('trg_harvests_insert','trg_harvests_update','trg_harvests_delete');
    IF v_found <> 3 THEN
      RAISE EXCEPTION '070 self-assertion FAILED: %/3 harvests view triggers present after restore', v_found;
    END IF;
  END IF;
  -- relkind r: nothing to assert; the marker row above is the record.
END $$;
