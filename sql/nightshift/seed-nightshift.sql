-- Night-shift scheduler seed — nexus_nightshift (Option-A isolation target).
--
-- Doubles as the canonical bootstrap for a fresh night-shift test DB. Mirrors
-- the live config_bundle rows (CLI/harn-opencode) so the scheduler runner's
-- fail-closed _is_interactive_hosted guard passes, binds the Planner's
-- Sonar-triage task (tackle.prompts + tackle.tasks), then inserts the four
-- nightshift cycle entries (Planner -> Builder -> Critic -> Reviewer) on a
-- staggered cron cadence.
--
-- Hermetic-safe: contains NO live credentials. The provider api_key here is a
-- placeholder; the real key lives in the live tackle.providers row and is not
-- required for scheduler launches (the launcher delegates to the harness's own
-- model/provider config).
--
-- Safe to re-run (idempotent): every statement guards on existence.
--
-- Usage:  psql -d nexus_nightshift -f seed-nightshift.sql

BEGIN;

-- ── 0. Cycle roles (FK targets for config_bundle / agent_scheduler) ──────
INSERT INTO tackle.roles (name, description) VALUES
  ('planner', 'Work decomposition authority — creates and manages implementation plans, promotes proposals'),
  ('builder', 'Implementation executor — picks up pending plans and implements them against acceptance criteria'),
  ('critic',  'Adversarial evaluator — surfaces risks, contradictions, and blind spots'),
  ('reviewer','Quality gate — reviews changes, issues approval/rejection receipts')
ON CONFLICT (name) DO NOTHING;

-- ── 1. Provider + verified model (config_bundle verified-gate target) ────
-- The verified_gate trigger forces is_active=0 unless the referenced model is
-- verified. A schema-only bootstrap has empty tackle.providers/models, so the
-- nightshift DB must carry its own provider+model rows.
INSERT INTO tackle.providers (id, name, type, endpoint_url, api_key, config_json, created_at, updated_at)
SELECT 'prov-nightshift', 'Nightshift placeholder provider', 'openai',
       'https://integrate.api.nvidia.com/v1',
       'nvapi-PLACEHOLDER-NOT-A-REAL-KEY',
       '{"opencodeProvider": "nvidia"}', now(), now()
WHERE NOT EXISTS (SELECT 1 FROM tackle.providers WHERE id='prov-nightshift');

INSERT INTO tackle.models (id, name, harness_id, provider_id, model_identifier, created_at, updated_at, verified)
SELECT 'mod-nightshift-deepseek-v4-flash', 'DeepSeek V4 Flash 0731 (nightshift)', 'harn-opencode',
       'prov-nightshift', 'deepseek-ai/deepseek-v4-flash-0731', now(), now(), true
WHERE NOT EXISTS (SELECT 1 FROM tackle.models WHERE id='mod-nightshift-deepseek-v4-flash');

-- ── 2. config_bundle: CLI/harn-opencode rows for the cycle roles ─────────
-- The runner refuses to launch a role unless an ACTIVE config_bundle with
-- invocation_mode != 'INTERACTIVE' exists (fail-closed, incident e6d854da
-- spirit). model_id here must be a verified model (see gate above).
INSERT INTO tackle.config_bundle
  (id, name, role, model_id, provider_id, harness_id, priority, invocation_mode, is_active, created_at, updated_at)
SELECT 'cb-nightshift-planner', 'Bundle: nightshift planner (CLI/harn-opencode)', 'planner',
       'mod-nightshift-deepseek-v4-flash', 'prov-nightshift', 'harn-opencode', 1, 'CLI', 1, now(), now()
WHERE NOT EXISTS (SELECT 1 FROM tackle.config_bundle WHERE role='planner' AND is_active=1 AND invocation_mode='CLI');

INSERT INTO tackle.config_bundle
  (id, name, role, model_id, provider_id, harness_id, priority, invocation_mode, is_active, created_at, updated_at)
SELECT 'cb-nightshift-builder', 'Bundle: nightshift builder (CLI/harn-opencode)', 'builder',
       'mod-nightshift-deepseek-v4-flash', 'prov-nightshift', 'harn-opencode', 1, 'CLI', 1, now(), now()
WHERE NOT EXISTS (SELECT 1 FROM tackle.config_bundle WHERE role='builder' AND is_active=1 AND invocation_mode='CLI');

INSERT INTO tackle.config_bundle
  (id, name, role, model_id, provider_id, harness_id, priority, invocation_mode, is_active, created_at, updated_at)
SELECT 'cb-nightshift-critic', 'Bundle: nightshift critic (CLI/harn-opencode)', 'critic',
       'mod-nightshift-deepseek-v4-flash', 'prov-nightshift', 'harn-opencode', 1, 'CLI', 1, now(), now()
WHERE NOT EXISTS (SELECT 1 FROM tackle.config_bundle WHERE role='critic' AND is_active=1 AND invocation_mode='CLI');

INSERT INTO tackle.config_bundle
  (id, name, role, model_id, provider_id, harness_id, priority, invocation_mode, is_active, created_at, updated_at)
SELECT 'cb-nightshift-reviewer', 'Bundle: nightshift reviewer (CLI/harn-opencode)', 'reviewer',
       'mod-nightshift-deepseek-v4-flash', 'prov-nightshift', 'harn-opencode', 1, 'CLI', 1, now(), now()
WHERE NOT EXISTS (SELECT 1 FROM tackle.config_bundle WHERE role='reviewer' AND is_active=1 AND invocation_mode='CLI');

-- ── 3. Harness row if missing (launcher reads tackle.harnesses) ──────────
INSERT INTO tackle.harnesses (id, name, invocation_semantics, created_at, updated_at)
SELECT 'harn-opencode', 'Opencode CLI',
       '{"binary":"opencode","capabilities":{"model":true,"agent":true,"working_directory":true,"system_prompt":false},"execution":{"mode":"interactive","subcommand":"run"},"semantics":{"model":{"type":"flag","flag":"--model"},"agent":{"type":"flag","flag":"--agent"},"working_directory":{"type":"flag","flag":"--dir"}},"role_mapping":{"strategy":"agent"}}',
       now(), now()
WHERE NOT EXISTS (SELECT 1 FROM tackle.harnesses WHERE id='harn-opencode');

-- ── 3b. Planner night-shift task (Sonar triage is the work source) ──────
-- The scheduler rows below carry task_slug='nightshift'. Without a matching
-- tackle.tasks row the runner has nothing to resolve, and the launch prompt
-- degrades to the agent_config title string (e.g. "nightshift-planner-triage") —
-- a launch with no instruction, no acceptance criteria and no Sonar scope.
-- The task template below is what the Planner actually receives.
INSERT INTO tackle.prompts (role, slug, version, title, body_md, tags)
VALUES (
  'planner', 'nightshift-planner-triage', 1,
  'Night-shift Sonar triage',
  $prompt$## Night-shift Sonar triage

You are the triage entry point for this night-shift cycle. Read the open
SonarQube findings for project `nexus`, group them into coherent batches, and
emit those batches as plans. You group; you do NOT implement, review or merge.

1. Inbox first: `nebula_get_inbox {"role":"planner"}` (or
   `nexus/bin/check-inbox.sh --role planner`). Surface anything that blocks
   triage before you start grouping.
2. Read the work source with `sonar_search_issues` (project `nexus`,
   `resolved:"false"`, `severities:"BLOCKER,CRITICAL,MAJOR"`). Completed
   findings drop out of that query structurally — do not maintain a skip-list.
   Page with `p`/`ps` and use `facets:"components,severities,rules"` for the
   grouping pass.
3. Group, in this order:
   - **Scope first** — one repo+area per batch (e.g. `nexus/typescript/*` vs
     `nexus/py/...`). Keeps the PR reviewable and the leak-period attribution
     sane.
   - **Severity/rule-class second** — new-code gate blockers ahead of
     leak-period debt; false-positive hotspots in their own batch.
   - **Risk third** — anything touching auth or DB boundaries gets its own
     small batch.
4. Constraints: same-file items batch together; **at most 5 work requests per
   cycle**. A sixth batch is a re-scope, not a bigger cycle.
5. For each batch, create one plan through the pipeline API
   (`nebula_create_plan`, or `POST http://localhost:3101/api/plans`) with a goal
   naming the scope, explicit acceptance criteria, and the claimed Sonar keys
   embedded as `inputs.sonar = { issueKeys, hotspotKeys, ruleKeys, component,
   severity, batch }` so the Builder can cite them and the Reviewer can close
   them after merge.
6. Record what you grouped (`nebula_create_agent_record`, `to:builder`,
   `type:status-update`) and stop. Do not write to SonarQube.

If SonarQube is unreachable, record the blocker and stop — do not invent
batches from memory.
$prompt$,
  ARRAY['nightshift','sonar','planner','triage']
)
ON CONFLICT (role, slug, version) DO UPDATE
  SET title = EXCLUDED.title, body_md = EXCLUDED.body_md,
      tags = EXCLUDED.tags, updated_at = now();

INSERT INTO tackle.tasks (role, task_slug, scope, acceptance_criteria, prompt_id, active)
SELECT 'planner', 'nightshift', 'sonar-triage',
       ARRAY[
         'Open Sonar findings for project nexus were read through sonar_search_issues (resolved=false); no skip-list was maintained',
         'Findings were grouped scope-first, then severity/rule-class, then risk; each batch covers one repo+area',
         'Each batch became one plan whose acceptance criteria cite the claimed sonar keys (inputs.sonar.issueKeys/hotspotKeys)',
         'At most 5 work requests were emitted in the cycle; same-file items share a batch',
         'The Planner performed no implementation, merge, or Sonar writeback'
       ],
       p.id, TRUE
FROM tackle.prompts p
WHERE p.role = 'planner' AND p.slug = 'nightshift-planner-triage' AND p.version = 1
ON CONFLICT (role, task_slug) DO UPDATE
  SET scope = EXCLUDED.scope,
      acceptance_criteria = EXCLUDED.acceptance_criteria,
      prompt_id = EXCLUDED.prompt_id,
      active = EXCLUDED.active,
      updated_at = now();

-- ── 4. Nightshift cycle entries ──────────────────────────────────────────
-- Staggered CRON cadence, not four equal intervals: with `interval 1800` and a
-- NULL last_run_at every row was due on the same tick and every 30 minutes
-- thereafter, so Planner -> Builder -> Critic -> Reviewer launched together
-- and the downstream roles raced the Planner's output.
--
-- One 30-minute cycle now runs Planner :00, Builder :05, Critic :10,
-- Reviewer :15 (and again at :30/:35/:40/:45). `_has_eligible_work` still gates
-- every role: planner on live Sonar findings, builder on READY
-- execution.requests, reviewer on open review tickets.
-- project_dir=/home/codex/dev matches the day harness bootstrap.
DO $$
DECLARE r RECORD;
BEGIN
  FOR r IN
    SELECT * FROM (VALUES
      ('planner',  '0,30 * * * *',  'nightshift-planner-triage'),
      ('builder',  '5,35 * * * *',  'nightshift-builder-implement'),
      ('critic',   '10,40 * * * *', 'nightshift-critic-gate'),
      ('reviewer', '15,45 * * * *', 'nightshift-reviewer-merge')
    ) AS v(role, cron_expr, title)
  LOOP
    UPDATE tackle.agent_scheduler
       SET schedule_type = 'cron',
           cron_expr = r.cron_expr,
           schedule_value = 1800,
           enabled = 1,
           agent_config = json_build_object('title', r.title)::text,
           updated_at = now()
     WHERE role = r.role AND coalesce(task_slug, '') = 'nightshift';

    IF NOT FOUND THEN
      INSERT INTO tackle.agent_scheduler
        (role, model_id, harness, agent_config, schedule_type, schedule_value,
         cron_expr, enabled, project_dir, task_slug)
      VALUES
        (r.role, 'mod-nightshift-deepseek-v4-flash', 'opencode',
         json_build_object('title', r.title)::text, 'cron', 1800,
         r.cron_expr, 1, '/home/codex/dev', 'nightshift');
    END IF;
  END LOOP;
END $$;

COMMIT;