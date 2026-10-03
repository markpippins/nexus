-- =============================================================================
-- V203 (DBA): role-vocabulary reconciliation — retire the uppercase DBA/Rover
-- rows left behind by Ruling 8 normalization (#712 step 2).
--
-- Ruling 8 deletes the `DBA` stub and retires `Rover` in config/roles/roles.json.
-- Their `tackle.roles` rows remained (created ad-hoc, not by seedDefaultRoles),
-- so bin/verify-roles.py reports them as `unknownRoles ['DBA','Rover']`. This
-- migration removes them AFTER reconciling every FK that points at them, so no
-- reference is orphaned:
--
--   tackle.role_memory      DBA 17  -> reassigned to the canonical `dba`
--                                     (these ARE the dba procedure cards:
--                                      dba-schema-map, dba-replication-protocol,
--                                      dba-change-review-workflow, ...)
--   tackle.config_bundle    DBA  2  -> reassigned to `dba`
--   tackle.role_tool_access DBA  5  -> deleted (dba already carries the same 5
--                                     grants; reassigning would violate
--                                     uq_role_tool_access_role_mcp_tool)
--   tackle.prompts          DBA  1  -> deleted (dba already carries a
--                                     byte-identical `database-admin` copy plus
--                                     the canonical `opencode-persona`)
--   tackle.config_bundle    Rover 4 -> deleted (Rover has no successor; retired)
--   tackle.agent_scheduler / tackle.sessions / tackle.tasks: 0 rows for both
--                                     (verified before writing this file).
--
-- Ruling 16 (f8bd88f6) dispositions: assembly `DBA` -> `dba` (case flip) and
-- `Rover` retired — both handled by V204. Historical nebula/assembly records
-- that carry the TEXT 'DBA'/'Rover' are immutable history and are NOT touched.
--
-- Idempotent: every statement is a no-op on an already-reconciled database,
-- and the DO block refuses (loudly) rather than guessing if the successor role
-- is absent or any reference is left unreconciled.
-- =============================================================================

BEGIN;

DO $$
DECLARE
    v_left text[];
BEGIN
    -- Preflight: the canonical lowercase successor must exist.
    IF NOT EXISTS (SELECT 1 FROM tackle.roles WHERE name = 'dba') THEN
        RAISE EXCEPTION 'V203: canonical role "dba" is absent — refusing to retire DBA';
    END IF;

    -- Reassign the live bindings DBA -> dba (the successor role).
    UPDATE tackle.role_memory   SET role = 'dba' WHERE role = 'DBA';
    UPDATE tackle.config_bundle SET role = 'dba' WHERE role = 'DBA';

    -- dba already carries these; drop the uppercase duplicates rather than
    -- reassign (would violate the unique keys).
    DELETE FROM tackle.role_tool_access WHERE role = 'DBA';
    DELETE FROM tackle.prompts          WHERE role = 'DBA';

    -- Rover is retired with no successor: drop its dead bindings.
    DELETE FROM tackle.config_bundle WHERE role = 'Rover';

    -- Fail closed if ANY reference remains (the DELETE below would otherwise
    -- surface as a raw FK error with no attribution).
    SELECT array_agg(t) INTO v_left FROM (
        SELECT 'role_memory'      AS t FROM tackle.role_memory      WHERE role IN ('DBA','Rover')
        UNION ALL SELECT 'config_bundle'    FROM tackle.config_bundle    WHERE role IN ('DBA','Rover')
        UNION ALL SELECT 'prompts'          FROM tackle.prompts          WHERE role IN ('DBA','Rover')
        UNION ALL SELECT 'role_tool_access' FROM tackle.role_tool_access WHERE role IN ('DBA','Rover')
        UNION ALL SELECT 'agent_scheduler'  FROM tackle.agent_scheduler  WHERE role IN ('DBA','Rover')
        UNION ALL SELECT 'sessions'         FROM tackle.sessions         WHERE agent_role IN ('DBA','Rover')
        UNION ALL SELECT 'tasks'            FROM tackle.tasks            WHERE role IN ('DBA','Rover')
    ) s;

    IF v_left IS NOT NULL THEN
        RAISE EXCEPTION 'V203: unreconciled references remain in %', v_left;
    END IF;

    DELETE FROM tackle.roles WHERE name IN ('DBA','Rover');
END $$;

-- Postcondition: exactly the canonical lowercase vocabulary remains.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM tackle.roles WHERE name IN ('DBA','Rover')) THEN
        RAISE EXCEPTION 'V203: DBA/Rover survived the delete';
    END IF;
END $$;

COMMIT;
