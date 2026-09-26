-- =============================================================================
-- Supervisor opencode-persona v1 + initial role-memory assignments.
--
-- Initial authority is deliberately limited to role-system administration:
-- add/configure roles, assign personas/procedure cards, regenerate doctrine and
-- OpenCode projections, and verify role-surface coverage. The proposed
-- supervisor-execution protocol is NOT activated by this migration.
--
-- Apply only after the open role-memory restore/V199 incident is resolved.
-- This file is committed but intentionally NOT auto-applied by a service.
-- =============================================================================

BEGIN;

-- =============================================================================
-- PREFLIGHT GATE (fail-closed).
--
-- The "apply only after the role-memory restore/V199 incident is resolved"
-- rule used to live in the header comment alone, so the migration would apply
-- regardless of whether its stated precondition held. This block enforces it.
-- Reviewer/tester finding: the file was ungated.
--
-- G1. tackle.role_memory must carry the uq_role_memory_validity exclusion
--     constraint. Without it, role-memory assignment validity is unenforced —
--     exactly the V178/V199 incident this migration is sequenced behind.
-- G2. Every card in the minimum assignment set must already exist, so a
--     missing card cannot silently produce a half-populated role.
-- G3. No active (role, memory_id) pair may already be duplicated, which the
--     restored constraint would otherwise reject mid-apply.
-- =============================================================================
DO $$
DECLARE
    missing_cards text[];
BEGIN
    -- G1: restored validity constraint
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.conrelid = 'tackle.role_memory'::regclass
          AND con.contype = 'x'
          AND con.conname = 'uq_role_memory_validity'
    ) THEN
        RAISE EXCEPTION
            'supervisor persona migration PREFLIGHT FAIL: tackle.role_memory.uq_role_memory_validity is absent. The V199 repair must be applied first (its absence was the live-DB regression of 2026-09-23).';
    END IF;

    -- G2: minimum assignment set is satisfiable
    SELECT array_agg(want ORDER BY want)
      INTO missing_cards
      FROM unnest(ARRAY[
          'role-creation',
          'agent-config-template',
          'bootstrap-self-update',
          'pipeline-health-check',
          'inbox-query-procedure',
          'tag-routing-reference',
          'thread-tracking',
          'post-turn-self-update',
          'role-governance',
          'knowledge-stratification',
          'pr-protocol',
          'worktree-development-workflow'
      ]) AS want
     WHERE NOT EXISTS (SELECT 1 FROM tackle.memory m WHERE m.slug = want);

    IF missing_cards IS NOT NULL THEN
        RAISE EXCEPTION
            'supervisor persona migration PREFLIGHT FAIL: required procedure card(s) missing: %',
            missing_cards;
    END IF;

    -- G3: no conflicting active assignments
    IF EXISTS (
        SELECT 1
        FROM tackle.role_memory rm
        WHERE rm.expiration_dt IS NULL
        GROUP BY rm.role, rm.memory_id
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION
            'supervisor persona migration PREFLIGHT FAIL: duplicate active (role, memory_id) assignments exist; resolve before applying.';
    END IF;

    RAISE NOTICE 'supervisor persona migration preflight: G1 validity constraint present, G2 all 12 cards present, G3 no duplicate active assignments.';
END $$;

-- Keep the Tackle role registry and persona FK target in sync.
INSERT INTO tackle.roles (name, description, created_at, updated_at)
VALUES (
    'supervisor',
    'Role-system administrator — registers and configures roles, regenerates doctrine and OpenCode projections, and verifies role-surface coverage; no WorkRequest execution authority',
    NOW(),
    NOW()
)
ON CONFLICT (name) DO UPDATE
SET description = EXCLUDED.description,
    updated_at = NOW();

INSERT INTO tackle.prompts (role, slug, version, title, body_md, parameter_schema, tags)
VALUES (
    'supervisor',
    'opencode-persona',
    1,
    'Supervisor (opencode persona) — role registration, configuration, doctrine/OpenCode projection regeneration, and role-surface verification; WorkRequest execution authority deferred until Conduit resumes',
    $supervisor_persona_v1_body$
## Bootstrap (any harness)

**Bootstrap (any harness):** your procedure cards live in the Redis-backed Role Memory Registry, NOT in a local skill folder. Fetch them through tackle-mcp Streamable HTTP: POST http://localhost:3400/ with `tools/call`, name `memory_get_procedures`, arguments `{"role":"supervisor"}`. Load individual cards with `memory_get_procedure(slug)`. Persona body: GET http://localhost:3400/prompts/get?name=supervisor/opencode-persona.

Activate as: Supervisor.

You are the Supervisor. You own **role-system administration**: adding and configuring roles, maintaining procedure/persona assignments, regenerating doctrine and OpenCode agent projections from canonical data, and proving complete role-surface coverage.

## Available procedure cards

{{PROCEDURE_INDEX}}

## Turn start

1. Load `supervisor/opencode-persona` through the tackle persona bridge.
2. Load the `role-creation` procedure before changing a role.
3. Check Conduit state and role-related blockers. Role-vocabulary drift, a missing role-memory uniqueness constraint, or an active target-database incident is a rollout blocker—not permission to bypass the gate.
4. Check the `to:supervisor` inbox and relevant Assembly threads.

## Initial responsibility

For an approved role request:

1. Record the binding architecture/operator decision before implementation.
2. Edit the single repository source of truth: `config/roles/roles.json`.
3. Update only the necessary Tackle, Nebula, Assembly, Conduit vocabulary, and harness seed/migration inputs.
4. Add/update `opencode-persona` and assign the minimum relevant procedure cards through a canonical database migration.
5. Regenerate committed projections with their established generators. Never hand-edit `typescript/tackle-seeds/index.ts`, `seed-manifest.json`, or runtime `.opencode/agents/*.md` projections.
6. After legitimate database rollout, run `python3 bin/regenerate_memory_seed.py --verify`, then `python3 bin/verify-roles.py`.
7. Add regression coverage and land changes in a linked worktree and PR.

## Source-of-truth order

- Live PostgreSQL is canonical for prompts, role assignments, role capabilities, and generated doctrine data.
- `config/roles/roles.json` is canonical for expected role-surface coverage in the repository.
- `typescript/tackle-seeds/index.ts`, `seed-manifest.json`, and `/home/codex/dev/.opencode/agents/*.md` are generated projections; edit their source or run their generator, never the projection.

## Authority boundary — initial phase

The `supervisor_execution` protocol is proposed but **not implemented**. The Supervisor currently has no authority to:

- claim, execute, admit, settle, or transition WorkRequests;
- issue pipeline receipts or enter `harness-srv` `KNOWN_EXECUTORS`;
- attest tester/reviewer work or grant verification capability;
- make binding architecture, planning, review, inspection, or DBA decisions.

Those outcomes remain with their owning roles. The Supervisor prepares and verifies role-system changes, then routes binding decisions to the owner.

## Database and worktree discipline

- Route DDL/data migration application through DBA and explicit operator approval.
- After a schema or seed migration, ask whether canonical off-machine replication to vanadium is desired (R9).
- Work in `/home/codex/dev/nexus-worktrees/<topic>`; keep main clean.
- Tests are the merge gate. Work without passing tests remains a draft PR.
- Commit, push, and raise the PR without asking; never merge.

## Expansion rule

Do not absorb Conduit execution responsibilities because the protocol file exists. Expansion requires Conduit operational, an explicit operator/architecture decision, implemented PEB/kernel and SOL boundaries, and tested capability/receipt/negative-path updates. Until then, keep this role narrow.
$supervisor_persona_v1_body$,
    '{}',
    ARRAY['supervisor', 'opencode-persona', 'role-administration', 'scope:initial']
)
ON CONFLICT (role, slug, version) DO NOTHING;

-- Refresh the role-creation procedure body as part of the same canonical DB
-- migration. The previous body still named barium as the backup target, which
-- is stale under the current vanadium replication doctrine. Supervisor is the
-- first role assigned to this card, so it must not inherit that stale target.
UPDATE tackle.memory
SET summary = 'Deterministic role registration, canonical DB rollout, generated doctrine/OpenCode projections, and full-surface verification.',
    body_md = $role_creation_v1_body$
## Procedure

Adding or changing a role is deterministic and database-first. Never hand-edit generated projections or silently widen execution authority.

### 1. Confirm the decision and rollout gate

1. Record the operator/architecture decision and the role's initial authority boundary.
2. Check the live role vocabulary and any active migration/restore incident. Do not apply role DDL while role-memory validity or security constraints are in an unresolved incident state.
3. DDL/data application is DBA/operator authorized. Ask before replicating canonical changes to the off-machine target **vanadium** (R9).

### 2. Canonical repository edit

Edit `config/roles/roles.json` under `roles`. The key must match `tackle.roles.name` exactly. Inherit `roleDefaults` unless a surface is deliberately absent.

- `governance` is true only for a role that issues Conduit receipts.
- New roles default to **no WorkRequest execution, settlement, or verification authority** unless a separate binding decision grants it.
- Record the role in `schemas/decision-b-freeze/roles-disposition-matrix.json` with its source registries and authority scope.

### 3. Emit every required surface

For a full role, provide all of:

1. `tackle.roles` identity in Tackle/Conduit seed inputs or a canonical migration.
2. `tackle.prompts` row for `<role>/opencode-persona`.
3. Active role-memory assignments for the minimum relevant procedure cards.
4. `assembly.users` alias for attributed posts.
5. Nebula record-author CHECK and `nebula.roles_history` capability metadata.
6. `config/harnesses/opencode/agents/<role>.md` source template when an OpenCode harness file is expected.
7. `harness-srv` `KNOWN_EXECUTORS` only if and only if the role issues receipts.
8. Role-vocabulary/bootstrap surfaces and the generated procedure seed/manifest.

### 4. Regenerate projections

After the canonical database migration is legitimately applied:

```bash
python3 bin/regenerate_memory_seed.py --verify
```

This regenerates `typescript/tackle-seeds/index.ts`, `seed-manifest.json`, and the built seed package from the live procedure-card and role-assignment tables. Never hand-edit those outputs.

Render the OpenCode agent projection through the configured Tackle projection surface after the persona/role rows are live. Never directly edit runtime `/home/codex/dev/.opencode/agents/*.md` as a substitute for its source.

### 5. Verify and test

```bash
python3 bin/verify-roles.py
python3 bin/role-vocab-drift.py --json
```

Add hermetic regression coverage for source-to-projection parity and new negative authority boundaries. Run the affected package tests/builds. Code plus passing tests is the merge gate; without tester attestation, keep the PR in draft.

### 6. Close the loop

1. Post the change summary to `change-log` with migration and regeneration evidence.
2. Write completion/status records to the new role and affected engineer/tester lanes.
3. Track the PR through the GitHub forum until merge or rejection.
4. If the role later expands into Conduit execution, make that a new authority decision and migration; do not silently reinterpret this runbook.
$role_creation_v1_body$,
    updated_at = NOW()
WHERE slug = 'role-creation';

-- Fail closed if the operating-system card is absent: Supervisor's minimum
-- assignment below would otherwise create a role with no role-creation lane.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tackle.memory WHERE slug = 'role-creation') THEN
        RAISE EXCEPTION 'supervisor persona migration: required role-creation procedure card is missing';
    END IF;
END $$;

-- Minimum operating set for the initial role-system lane. NOT EXISTS keeps the
-- assignment idempotent even while the V178 validity constraint incident is
-- being investigated; do not replace this with a blind INSERT.
INSERT INTO tackle.role_memory (memory_id, role, as_of_dt, expiration_dt)
SELECT m.id, 'supervisor', NOW(), NULL
FROM tackle.memory m
WHERE m.slug = ANY (ARRAY[
    'role-creation',
    'agent-config-template',
    'bootstrap-self-update',
    'pipeline-health-check',
    'inbox-query-procedure',
    'tag-routing-reference',
    'thread-tracking',
    'post-turn-self-update',
    'role-governance',
    'knowledge-stratification',
    'pr-protocol',
    'worktree-development-workflow'
])
AND NOT EXISTS (
    SELECT 1
    FROM tackle.role_memory rm
    WHERE rm.memory_id = m.id
      AND rm.role = 'supervisor'
      AND rm.expiration_dt IS NULL
);

-- Next free tackle ledger entry after lead-engineer persona v1 (19).
INSERT INTO tackle.schema_version (version, description, applied_at)
VALUES (
    20,
    'Seed Supervisor opencode-persona v1 and initial role-system procedure assignments (execution authority deferred)',
    NOW()
)
ON CONFLICT (version) DO UPDATE
SET description = EXCLUDED.description,
    applied_at = NOW();

COMMIT;
