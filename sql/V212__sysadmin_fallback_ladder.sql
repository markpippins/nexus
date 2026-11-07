-- =============================================================================
-- V212: give the sysadmin role a primary it can actually reach, and a ladder.
--
-- What was wrong (measured 2026-11-06, not inferred):
--
--   The role's only active bundle pointed at nvidia/nemotron-3-super-120b-a12b.
--   A direct call to the NVIDIA endpoint returns
--
--     HTTP 404  Function 'ac74040f-…': Not found for account 'pcOFf8lS…'
--
--   so every hourly cycle spent 3-5s on it and advanced. The only rung behind
--   it was the SAME model (the resolver's fallback query includes the primary
--   row), which the harness collapsed as a duplicate -- leaving exactly one
--   hardcoded tail rung, opencode/big-pickle, which then ran to the 15-minute
--   per-attempt cap (exit=124) on most cycles. Five consecutive cycles posted
--   no heartbeat.
--
--   Measured through the same opencode CLI the harness spawns, trivial
--   "reply OK" run, 2026-11-06:
--
--     nvidia/nvidia/nemotron-3-super-120b-a12b      error, upstream 404   dead
--     nvidia/nvidia/nemotron-3.5-super-vl-120b-a12b "OK"                 alive
--     nvidia/nvidia/nemotron-3-ultra-550b-a55b      "OK"                 alive
--     opencode/big-pickle                           "OK"                 alive
--     opencode/glm-5.3-flash-free                   "OK"                 alive
--     opencode-go/glm-5.3-flash                     "OK"                 alive
--
-- This migration therefore: retires the 404 primary (kept for history, per the
-- V208 convention), installs nemotron-3.5-super-vl-120b-a12b as the primary,
-- and adds four fallbacks ordered by the provider preference the operator asked
-- for: nvidia -> opencode -> opencode-go.
--
-- The retired bundle is DEACTIVATED, never deleted: tackle.config_bundle is the
-- record of what a role was configured to use.
--
-- The verified-model gate trigger (db.ts v16) forces is_active=0 on any bundle
-- whose model is unverified. All five models below are verified=true at the time
-- of writing; if one regresses, its rung drops out of the resolver queue rather
-- than being silently retried.
--
-- Idempotent: targeted updates, deterministic ids, upsert on (role, model_id).
-- =============================================================================

BEGIN;

-- 1. Retire the primary that the account cannot serve.
UPDATE tackle.config_bundle
   SET is_active  = 0,
       updated_at = NOW(),
       metadata   = jsonb_set(
                      COALESCE(NULLIF(metadata, '')::jsonb, '{}'::jsonb),
                      '{retired_reason}',
                      to_jsonb('V212: NVIDIA endpoint returns 404 "Function not found for account" for this model (measured 2026-11-06)'::text)
                     )::text
 WHERE role = 'sysadmin'
   AND model_id = 'mod-1783906424536'
   AND is_active = 1;

-- 2. The model itself is unusable from this account: record that, so the UI
--    cannot offer it as a choice and no future bundle re-enters the queue.
UPDATE tackle.models
   SET verified   = FALSE,
       updated_at = NOW()
 WHERE id = 'mod-1783906424536';

-- 3. Record why the already-inactive muse-spark row is inactive. It was
--    retired by the verified-model gate (verified=false) and probed dead on
--    2026-11-05; this is the note the ladder's thinness needed.
UPDATE tackle.config_bundle
   SET updated_at = NOW(),
       metadata   = jsonb_set(
                      COALESCE(NULLIF(metadata, '')::jsonb, '{}'::jsonb),
                      '{retired_reason}',
                      to_jsonb('V212: model unverified and probed dead 2026-11-05 (error envelope, no tokens)'::text)
                     )::text
 WHERE role = 'sysadmin'
   AND model_id = 'mod-opencode-muse-spark-1-3-contributor-free';

-- 4. Install the ladder. priority 1 = primary, 2..5 = fallbacks in order;
--    the resolver sorts ascending, so this ordering IS the ladder.
INSERT INTO tackle.config_bundle
  (id, name, role, model_id, provider_id, harness_id, priority,
   invocation_mode, is_active, metadata, created_at, updated_at)
VALUES
  ('bundle-sysadmin-nv35vl-primary',
   'Primary: Nemotron 3.5 Super VL 120B for sysadmin',
   'sysadmin', 'mod-nvidia-nvidia-nemotron-3-5-super-vl-120b-a12b',
   'prov-1783906359513', 'harn-opencode', 1, 'CLI', 1,
   '{"environment":"production","source":"V212","verified":"2026-11-06 probe: OK through opencode CLI"}',
   NOW(), NOW()),

  ('bundle-sysadmin-nvultra-fallback',
   'Fallback 1: Nemotron 3 Ultra 550B for sysadmin',
   'sysadmin', 'mod-nvidia-nvidia-nemotron-3-ultra-550b-a55b',
   'prov-1783906359513', 'harn-opencode', 2, 'CLI', 1,
   '{"environment":"production","source":"V212","verified":"2026-11-06 probe: OK through opencode CLI"}',
   NOW(), NOW()),

  ('bundle-sysadmin-bigpickle-fallback',
   'Fallback 2: Big Pickle for sysadmin',
   'sysadmin', 'mod-big-pickle',
   'prov-opencode', 'harn-opencode', 3, 'CLI', 1,
   '{"environment":"production","source":"V212","verified":"2026-11-06 probe: OK through opencode CLI"}',
   NOW(), NOW()),

  ('bundle-sysadmin-glm53free-fallback',
   'Fallback 3: GLM-5.3 Flash Free for sysadmin',
   'sysadmin', 'mod-opencode-glm-5-3-flash-free',
   'prov-opencode', 'harn-opencode', 4, 'CLI', 1,
   '{"environment":"production","source":"V212","verified":"2026-11-06 probe: OK through opencode CLI"}',
   NOW(), NOW()),

  ('bundle-sysadmin-glm53go-fallback',
   'Fallback 4: GLM-5.3 Flash (OpenCode Go) for sysadmin',
   'sysadmin', 'mod-opencode-go-glm-5-3-flash',
   'prov-opencode-go', 'harn-opencode', 5, 'CLI', 1,
   '{"environment":"production","source":"V212","verified":"2026-11-06 probe: OK through opencode CLI"}',
   NOW(), NOW())
ON CONFLICT (role, model_id) DO UPDATE
   SET is_active   = EXCLUDED.is_active,
       priority    = EXCLUDED.priority,
       name        = EXCLUDED.name,
       provider_id = EXCLUDED.provider_id,
       harness_id  = EXCLUDED.harness_id,
       metadata    = EXCLUDED.metadata,
       updated_at  = NOW();

COMMIT;
