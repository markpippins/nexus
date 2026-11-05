-- =============================================================================
-- V208: retarget the tester role's config bundle off two dead model IDs.
--
-- On 2026-11-05 both models in the tester plan were measured dead from
-- harness-srv, on the same probe that the role's own runs were using:
--
--   x-preview-f-free   (priority 0, primary)  — emits an
--                       {"type":"error","error":{"data":{...}}} envelope and
--                       no tokens at all; the 120s first-token guard abandons
--                       it and the attempt is recorded exit=124;
--   stealth/ox-alpha   (priority 1, fallback) — "Unexpected server error",
--                       exit 1 in ~2s.
--
-- Consequence: a /run-direct on this plan produced NO output from either
-- attempt. Jobs 7e2f58e4 and 87ae9ae3 spent their whole cost (903s, then the
-- 540s cap once the ladder was longer) and posted nothing.
--
-- Replacement, both measured live on 2026-11-05 with a trivial "reply pong"
-- run through harness-srv: glm-5.3-flash-free (completed; it also produced
-- attestations e0c27b15 and 9890cc89) and big-pickle (completed).
--
-- The dead rows are DEACTIVATED, not deleted: tackle.config_bundle is the
-- record of what a role was configured to use, and "we tried ox-alpha" is
-- worth keeping. Provider diversity is still a gap — both replacements live
-- under prov-opencode, so an opencode-provider outage takes out the whole
-- ladder. Naming that here rather than implying it is covered.
--
-- Idempotent: re-running changes nothing (guarded inserts, deterministic ids,
-- targeted updates).
-- =============================================================================

BEGIN;

-- 1. The GLM-5.3 Flash Free row did not exist in tackle.models — the model was
--    only reachable through a per-run --model override. Record it so the plan
--    can name it.
INSERT INTO tackle.models (id, name, harness_id, provider_id, model_identifier,
                           created_at, updated_at, verified)
SELECT 'mod-opencode-glm-5-3-flash-free', 'GLM-5.3 Flash Free',
       'harn-opencode', 'prov-opencode', 'glm-5.3-flash-free',
       NOW(), NOW(), TRUE
WHERE NOT EXISTS (
  SELECT 1 FROM tackle.models WHERE provider_id = 'prov-opencode'
    AND model_identifier = 'glm-5.3-flash-free'
);

-- 2. Retire the two dead bundles for the tester role.
UPDATE tackle.config_bundle
   SET is_active   = 0,
       updated_at  = NOW(),
       metadata    = jsonb_set(
                       COALESCE(NULLIF(metadata, '')::jsonb, '{}'::jsonb),
                       '{retired_reason}',
                       to_jsonb('V208: model measured dead 2026-11-05 (no tokens / upstream error)'::text)
                     )::text
 WHERE role = 'tester'
   AND is_active = 1
   AND model_id IN ('mod-opencode-x-preview-f-free',
                    'mod-openrouter-stealth-ox-alpha');

-- 3. Install the replacements. priority 0 = primary, 1 = fallback;
--    resolveRoleModel sorts by priority, so this ordering is the ladder.
INSERT INTO tackle.config_bundle
  (id, name, role, model_id, provider_id, harness_id, priority,
   invocation_mode, is_active, metadata, created_at, updated_at)
VALUES
  ('bundle-tester-glm53-primary', 'Primary: GLM-5.3 Flash Free for tester',
   'tester', 'mod-opencode-glm-5-3-flash-free', 'prov-opencode', 'harn-opencode',
   0, 'CLI', 1,
   '{"environment":"production","source":"V208","verified":"2026-11-05 probe: completed"}',
   NOW(), NOW()),
  ('bundle-tester-bigpickle-fallback', 'Fallback: Big Pickle for tester',
   'tester', 'mod-big-pickle', 'prov-opencode', 'harn-opencode',
   1, 'CLI', 1,
   '{"environment":"production","source":"V208","verified":"2026-11-05 probe: completed"}',
   NOW(), NOW())
ON CONFLICT (role, model_id) DO UPDATE
   SET is_active   = 1,
       priority    = EXCLUDED.priority,
       name        = EXCLUDED.name,
       provider_id = EXCLUDED.provider_id,
       harness_id  = EXCLUDED.harness_id,
       metadata    = EXCLUDED.metadata,
       updated_at  = NOW();

COMMIT;
