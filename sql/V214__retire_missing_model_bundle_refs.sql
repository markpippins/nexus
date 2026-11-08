-- V214 — Explicit disposition of historical bundles whose model row does not
-- exist.
--
-- Two INACTIVE bundles (planner + reviewer) point at model id
-- `mod-gemini-3-5-flash-genai`, which was never imported into
-- tackle.models. tackle's validator raised an ERROR for any bundle with a
-- missing model, active or not, so the whole configuration reported
-- valid=false and harness preflight refused to launch Engineer jobs
-- (2026-11-07).
--
-- A similarly named model exists (`mod-gemini-3-5-flash`), but nothing
-- establishes it is the model these bundles were configured against, and it
-- is unverified. Repointing would fabricate a mapping, so these references
-- are retired IN PLACE: rows are kept for history, forced inactive, and
-- stamped with the reason. No row is deleted and no model is invented.
--
-- The validator half of the fix — inactive bundles report `info` instead of
-- `error` — lives in typescript/tackle-srv/src/ai-config-validation.ts.
--
-- Idempotent: a re-run only refreshes the retirement stamp.

UPDATE tackle.config_bundle cb
   SET is_active = 0,
       metadata = (
         COALESCE(NULLIF(cb.metadata, '')::jsonb, '{}'::jsonb)
         || jsonb_build_object(
              'retired', true,
              'retired_by', 'V214',
              'retired_at', to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
              'retired_reason',
              'references model id ' || cb.model_id ||
              ' which is absent from tackle.models',
              'disposition',
              'retired-in-place: no verified replacement mapped'
            )
       )::text,
       updated_at = now()
 WHERE cb.id IN ('cb-planner-mod-gemini-3-5-flash',
                 'cb-reviewer-mod-gemini-3-5-flash')
   AND NOT EXISTS (
     SELECT 1 FROM tackle.models m WHERE m.id = cb.model_id
   );
