-- Migration 054: Allow analyst-ii role in nebula.agent_records_history
--
-- Role-surface parity: analyst-ii is a canonical analysis-only agent role
-- (tackle.roles, harness agent file, assembly alias, persona, procedure cards,
-- governance). The CHECK previously only allowed 'analyst' and 'engineer-ii' —
-- records authored as role 'analyst-ii' were rejected. Same pattern as
-- migrations 049/050/051/052/053.
--
-- analyst-ii is deliberately analysis-only: it never issues decisions.
-- The 'governance' registration (KNOWN_EXECUTORS) only affects receipt
-- attribution; analyst-ii does not author binding authority.
--
-- Idempotent (drop + recreate the CHECK). Alters the nebula mirror only —
-- the startup-runner path maintains nebula alone; scratch-mirror parity is
-- the operator-tier contract (sql/ migrations carrying the ROLE-VOCAB PIN).
-- The role-surface verifier reads both surfaces.

BEGIN;

ALTER TABLE nebula.agent_records_history
    DROP CONSTRAINT IF EXISTS agent_records_role_check;

ALTER TABLE nebula.agent_records_history
    ADD CONSTRAINT agent_records_role_check
    CHECK (
        role = ''
        OR role = ANY (ARRAY[
            'architect', 'planner', 'builder', 'reviewer', 'critic',
            'analyst', 'inspector', 'engineer', 'engineer-ii', 'devops',
            'topologist',
            'auditor', 'dba', 'epistemologist', 'operator',
            'sysadmin', 'DBA', 'tester', 'analyst-ii'
        ]::text[])
    );

COMMIT;
