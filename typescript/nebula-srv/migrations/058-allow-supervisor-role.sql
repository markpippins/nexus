-- Migration 058: Allow Supervisor role in agent_records_history.
--
-- RENUMBERED from 055-allow-supervisor-role.sql (thread 6bba5dd3):
-- duplicate version prefixes make the startup runner (../src/migrate.ts)
-- skip this file on a fresh database (its twin 055-agent-records-tags-gin
-- sorts lex-first), leaving agent_records_role_check WITHOUT 'supervisor'
-- so the first supervisor-role record 42503s. Content unchanged.
--
-- Initial Supervisor authority is role-system administration only. This
-- migration widens the record-author vocabulary; it grants no WorkRequest
-- execution, receipt, review, or verification capability.
--
-- Idempotent: drop + recreate the CHECK with the same canonical vocabulary
-- carried by sql/V200__add_supervisor_role.sql.

BEGIN;

ALTER TABLE nebula.agent_records_history
    DROP CONSTRAINT IF EXISTS agent_records_role_check;

ALTER TABLE nebula.agent_records_history
    ADD CONSTRAINT agent_records_role_check
    CHECK (
        role = ''
        OR role = ANY (ARRAY[
            'architect', 'planner', 'builder', 'reviewer', 'critic',
            'analyst', 'inspector', 'engineer', 'engineer-ii', 'engineer-iii',
            'devops', 'topologist', 'auditor', 'dba', 'epistemologist',
            'operator', 'sysadmin', 'DBA', 'tester', 'analyst-ii',
            'design-synthesist', 'layout-mechanic', 'ontologist',
            'lead-engineer', 'sound-technician', 'supervisor'
        ]::text[])
    );

COMMIT;
