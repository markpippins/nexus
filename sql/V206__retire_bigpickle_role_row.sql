-- =============================================================================
-- V206 (DBA) — retire the stale `big-pickle` role row (Ruling 19 follow-up).
--
-- STATUS: STAGED INERT — DRAFT. This migration REFUSES to run until the
-- destination for big-pickle's model-scoped artifacts is decided (architect
-- ruling; Ruling 19 §3 / Assembly thread 181845a4). Nothing here has been
-- applied; the DO block raises before any write while the sentinel is in place.
--
-- Ruling 19 (f054ba36) moved `big-pickle` OUT of config/roles/roles.json into
-- config/roles/address-kinds.json as kind=alias: it is a MODEL identity
-- (cf. the `mod-big-pickle` models seed), NOT a role. The live `tackle.roles`
-- row (created 2026-08-06) persists, and because
-- `tackle.prompts.role -> tackle.roles.name` a model-scoped bootstrap has no
-- other home:
--
--   tackle.prompts       2 rows  (slug `opencode-persona`, "Big Pickle
--                                 Bootstrap v0.1"; tags bootstrap / doctrine /
--                                 model:big-pickle / type:governance)
--   tackle.config_bundle 1 row   ("Primary: Muse Spark 1.3 Free for big-pickle")
--   tackle.role_memory / role_tool_access / agent_scheduler / sessions / tasks:
--                                 0 rows (verified before writing this file).
--
-- That row is why bin/verify-roles.py reported unknownRoles ['big-pickle']. The
-- REPORTING side is already fixed in PR #712 (verify-roles.py excludes
-- address-kinds.json names); THIS migration addresses the DATA side — re-key
-- the two model-scoped artifact families onto a decided destination, then
-- delete the role row.
--
-- ── DESTINATION ─────────────────────────────────────────────────────────────
-- Option A (this draft) is only correct once the architect rules the
-- destination. Live candidates (Ruling 19 §3):
--   A1. an existing role that should own the bootstrap persona, or
--   A2. (with option C) a model-scoped key, once tackle.prompts gains one.
-- `dest_role` below is the single knob. Until it is set to a real, existing
-- `tackle.roles.name`, the block raises and NOTHING changes. If the ruling
-- instead picks option B (keep the row; redefine tackle.roles as roles ∪
-- registered aliases), this migration is not needed and should be dropped.
--
-- APPLY ORDER: assumes V203 and V204 (PR #712, branch roles-vocab-normalize)
-- applied first — V206 is the next free number after them.
--
-- R9: data change — replicate to vanadium only on explicit operator go.
--
-- Idempotent: if the big-pickle row is already gone, this is a logged no-op.
-- =============================================================================

BEGIN;

DO $$
DECLARE
    -- ── SET THIS FROM THE RULING. Leave the sentinel to keep the file inert. ──
    dest_role text := '<<PENDING-RULING>>';
    sentinel  constant text := '<<PENDING-RULING>>';
    v_prompts int;
    v_bundles int;
    v_left    text[];
BEGIN
    IF dest_role = sentinel THEN
        RAISE EXCEPTION
            'V206 staged inert: destination for big-pickle prompts/config_bundle '
            'is undecided (Ruling 19 §3 / thread 181845a4). Set dest_role to the '
            'ruled destination role before applying.';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM tackle.roles WHERE name = 'big-pickle') THEN
        RAISE NOTICE 'V206: no big-pickle role row — already reconciled, nothing to do';
        RETURN;
    END IF;

    IF dest_role = 'big-pickle' THEN
        RAISE EXCEPTION 'V206: destination must differ from big-pickle';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM tackle.roles WHERE name = dest_role) THEN
        RAISE EXCEPTION 'V206: destination role % is not a tackle.roles row — refusing', dest_role;
    END IF;

    -- Collision preflight against the two unique keys we are about to re-key:
    --   tackle.prompts       UNIQUE(role, slug, version)
    --   tackle.config_bundle UNIQUE(role, model_id)
    IF EXISTS (
        SELECT 1
          FROM tackle.prompts p
          JOIN tackle.prompts q
            ON q.role = dest_role
           AND q.slug = p.slug
           AND q.version = p.version
         WHERE p.role = 'big-pickle'
    ) THEN
        RAISE EXCEPTION
            'V206: re-keying would collide on uq_prompts_role_slug_version for %', dest_role;
    END IF;

    IF EXISTS (
        SELECT 1
          FROM tackle.config_bundle c
          JOIN tackle.config_bundle d
            ON d.role = dest_role
           AND d.model_id = c.model_id
         WHERE c.role = 'big-pickle'
    ) THEN
        RAISE EXCEPTION
            'V206: re-keying would collide on config_bundle_role_model_id_key for %', dest_role;
    END IF;

    -- ── Relocate the model-scoped artifacts onto the decided destination ──────
    UPDATE tackle.prompts       SET role = dest_role WHERE role = 'big-pickle';
    GET DIAGNOSTICS v_prompts = ROW_COUNT;
    UPDATE tackle.config_bundle SET role = dest_role WHERE role = 'big-pickle';
    GET DIAGNOSTICS v_bundles = ROW_COUNT;

    -- ── Fail closed if ANY reference remains (mirrors V203's reference sweep) ──
    SELECT array_agg(t) INTO v_left FROM (
        SELECT 'role_memory'      AS t FROM tackle.role_memory      WHERE role = 'big-pickle'
        UNION ALL SELECT 'config_bundle'    FROM tackle.config_bundle    WHERE role = 'big-pickle'
        UNION ALL SELECT 'prompts'          FROM tackle.prompts          WHERE role = 'big-pickle'
        UNION ALL SELECT 'role_tool_access' FROM tackle.role_tool_access WHERE role = 'big-pickle'
        UNION ALL SELECT 'agent_scheduler'  FROM tackle.agent_scheduler  WHERE role = 'big-pickle'
        UNION ALL SELECT 'sessions'         FROM tackle.sessions         WHERE agent_role = 'big-pickle'
        UNION ALL SELECT 'tasks'            FROM tackle.tasks            WHERE role = 'big-pickle'
    ) s;

    IF v_left IS NOT NULL THEN
        RAISE EXCEPTION 'V206: unreconciled big-pickle references remain in %', v_left;
    END IF;

    DELETE FROM tackle.roles WHERE name = 'big-pickle';

    RAISE NOTICE
        'V206: relocated % prompt(s) and % config_bundle(s) to %; big-pickle role row deleted',
        v_prompts, v_bundles, dest_role;
END $$;

-- Postcondition: the role row is gone. (When the sentinel is in place the DO
-- block above raises first, so this is reached only on a real application or an
-- idempotent no-op.)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM tackle.roles WHERE name = 'big-pickle') THEN
        RAISE EXCEPTION 'V206: big-pickle survived the delete';
    END IF;
END $$;

COMMIT;
