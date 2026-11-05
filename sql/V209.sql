-- =============================================================================
-- V209: give the tester ladder a rung that does not live under prov-opencode.
--
-- V208 replaced two dead tester models (x-preview-f-free, stealth/ox-alpha) with
-- glm-5.3-flash-free -> big-pickle, and said plainly that provider diversity was
-- still a gap: both live under prov-opencode, so an opencode-provider outage
-- takes out the whole ladder. This closes that gap.
--
-- A third tester row, muse-spark-1-3-contributor-free (priority 1), was already
-- inactive when this migration was written but carried no reason (metadata
-- '{}'). Probed 2026-11-05 anyway to confirm the retirement was right:
--   {"type":"error", ... "ref":"err_397b3c07"}, exit 1, no tokens. The update
-- below records that reason; it does not change the row's active state.
--
-- Candidates probed 2026-11-05 with a trivial "reply pong" run through the same
-- opencode CLI the harness spawns (no fallback ladder involved, so the result is
-- the model's own):
--
--   openrouter/openai/gpt-oss-120b            -> "pong"                ALIVE
--   openrouter/openrouter/free                -> "pong"                ALIVE
--   mistral/mistral-medium-latest             -> no token in 70s        dead
--   nvidia/nvidia/nemotron-3-super-120b-a12b  -> error envelope         dead
--   nvidia/z-ai/glm-5.2                       -> "Unexpected server error" dead
--   opencode/muse-spark-1-3-contributor-free  -> error envelope         dead
--
-- gpt-oss-120b is installed as priority 2 (last resort): it is the cross-provider
-- rung that keeps the role alive if prov-opencode fails wholesale, not a
-- replacement for the fast primary. big-pickle stays at priority 1.
--
-- Idempotent: targeted update + guarded upsert, no deletes.
-- =============================================================================

BEGIN;

-- 1. Record why the (already inactive) muse-spark row is inactive. Kept for
--    history, like the V208 rows.
UPDATE tackle.config_bundle
   SET is_active  = 0,
       updated_at = NOW(),
       metadata   = jsonb_set(
                      COALESCE(NULLIF(metadata, '')::jsonb, '{}'::jsonb),
                      '{retired_reason}',
                      to_jsonb('V209: model measured dead 2026-11-05 (error envelope, no tokens)'::text)
                    )::text
 WHERE role = 'tester'
   AND model_id = 'mod-opencode-muse-spark-1-3-contributor-free'
   AND COALESCE(metadata, '') NOT LIKE '%retired_reason%';

-- 2. Cross-provider rung. The model row already exists (mod-1782144526299,
--    harness harn-opencode, model_identifier openai/gpt-oss-120b, provider
--    OpenRouter) and is marked verified; resolveRoleModel composes the wire id
--    from the provider's opencodeProvider, giving
--    openrouter/openai/gpt-oss-120b — the exact id probed above.
INSERT INTO tackle.config_bundle
  (id, name, role, model_id, provider_id, harness_id, priority,
   invocation_mode, is_active, metadata, created_at, updated_at)
VALUES
  ('bundle-tester-gptoss-openrouter-fallback', 'Fallback: GPT-OSS 120B (OpenRouter) for tester',
   'tester', 'mod-1782144526299', 'prov-1782144397043', 'harn-opencode',
   2, 'CLI', 1,
   '{"environment":"production","source":"V209","verified":"2026-11-05 probe: replied pong on openrouter/openai/gpt-oss-120b","role":"cross-provider last resort"}',
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
