-- =============================================================================
--  V176 — retire the V171 plans write-through mirror trigger (consumer cutover)
--
--  After nebula-srv plan CRUD and conduit-mcp update_plan are flipped to write
--  nebula.blueprints_history directly (the consumer-cutover half of ruling
--  5e6d4b02 §2), the V171 write-through shim trigger is retired:
--
--    trg_plans_mirror_to_blueprints (AFTER INSERT ON
--    nebula.implementation_plans_history) — the transition window's clock.
--
--  Post-retirement, nebula.implementation_plans_history is frozen: no writer
--  targets it, and intentional direct writes no longer propagate (expected —
--  canonical is blueprints_history). The compat views (nebula.implementation_plans,
--  nebula.plans, nebula.plan_status, nebula.plans_by_status) continue to read
--  from blueprints_history unchanged.
--
--  Apply AFTER the code PR (writer flip) is merged and deployed, so no live
--  writer regresses to the legacy table. Idempotent on re-apply.
-- =============================================================================

BEGIN;

DROP TRIGGER IF EXISTS trg_plans_mirror_to_blueprints
    ON nebula.implementation_plans_history;

-- Verify: the trigger is gone and blueprints remains the canonical source.
DO $$
DECLARE
    v_trigger_remaining  boolean;
    v_blueprints_count   integer;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_plans_mirror_to_blueprints'
          AND tgrelid = 'nebula.implementation_plans_history'::regclass
    ) INTO v_trigger_remaining;

    IF v_trigger_remaining THEN
        RAISE EXCEPTION 'V176 verify: mirror trigger still present on implementation_plans_history'
            USING ERRCODE = 'P0001';
    END IF;

    SELECT count(*) INTO v_blueprints_count FROM nebula.blueprints_history;

    RAISE NOTICE '✅ V176 applied — mirror trigger retired; blueprints_history canonical (% rows).', v_blueprints_count;
END $$;

COMMIT;