-- =============================================================================
-- V213: take the retired 404 model out of the remaining role ladders.
--
-- What V212 left behind (measured 2026-11-07, after #907 merged):
--
--   V212 retired the sysadmin bundle for nvidia/nemotron-3-super-120b-a12b and
--   set that model's verified flag to FALSE. The verified-model gate
--   (trg_config_bundle_verified_gate, V16) only fires on a write to
--   tackle.config_bundle, so flipping tackle.models.verified did not cascade
--   to bundle rows that already existed. Two roles therefore kept an ACTIVE
--   rung on a model the account cannot serve:
--
--     engineer   priority 4   cb-engineer-mod-nemotron-120b
--     reviewer   priority 5   cb-reviewer-mod-1783906424536
--
--   Both appeared in `GET :3410/config/ai/resolve/<role>` fallback_models.
--   That is the failure mode #907 removed for sysadmin — a rung that costs a
--   ~3-5s upstream 404 before the chain advances — still live for two roles.
--
-- `python/tackle/db.py` passes fallback_models through unchanged and does not
-- de-duplicate, and wind-ui-v2 / nexus-control-edge read the same route, so
-- neither consumer would have filtered the dead rung out on its own.
--
-- Scope is deliberate: this touches ONLY the model V212 retired. Five other
-- active bundles point at a different unverified model (x-preview-f-free) and
-- are left for their own disposition — sweeping every unverified model here
-- would deactivate primaries whose roles have not been re-planned, which is a
-- separate decision, not a cleanup.
--
-- The rows are DEACTIVATED, never deleted: tackle.config_bundle is the record
-- of what a role was configured to use.
--
-- Idempotent: a second run finds no active bundle on this model and changes
-- nothing.
-- =============================================================================

BEGIN;

-- 1. Re-assert the model's retirement on a database that has not run V212.
--    (No-op here; it makes this migration self-sufficient on a fresh restore.)
UPDATE tackle.models
   SET verified   = FALSE,
       updated_at = NOW()
 WHERE id = 'mod-1783906424536'
   AND verified IS DISTINCT FROM FALSE;

-- 2. Deactivate every remaining ACTIVE bundle on that model, whatever role it
--    belongs to. Targeted by model, so a role that re-adds the model without
--    its own decision cannot quietly re-enter the resolver queue.
UPDATE tackle.config_bundle
   SET is_active   = 0,
       updated_at  = NOW(),
       metadata    = jsonb_set(
                       COALESCE(NULLIF(metadata, '')::jsonb, '{}'::jsonb),
                       '{retired_reason}',
                       to_jsonb('V213: model retired by V212 (NVIDIA endpoint returns 404 "Function not found for account"); bundle was still active because the verified-model gate fires only on config_bundle writes'::text)
                     )::text
 WHERE model_id = 'mod-1783906424536'
   AND is_active = 1;

COMMIT;
