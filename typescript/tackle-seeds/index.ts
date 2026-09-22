/**
 * tackle-seeds — canonical seed SQL for the tackle procedure-card registry.
 *
 * The database (tackle.memory / tackle.role_memory) is the source of truth.
 * This module renders the seedMemoryProcedures() DO-block SQL that a fresh-DB
 * bootstrap runs (ON CONFLICT (slug) DO NOTHING, so re-running is safe:
 * existing procedures are left untouched, new ones are added).
 *
 * Consumed by both tackle-srv and tackle-mcp. DO NOT hand-edit the template
 * literal body — regenerate it from the live DB instead:
 *
 *   python3 nexus/bin/regenerate_memory_seed.py [--verify]
 */
export function seedMemoryProcedures(): string {
  const SQL = `tackle`;
  return `
DO $mem$
DECLARE
    v_memory_id UUID;
    v_role TEXT;
    v_roles TEXT[];
BEGIN

    -- ──────────────────────────────────────────────────────────
    --  1. Pipeline Health Check
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'pipeline-health-check',
        'Pipeline Health Check',
        'DB-first pipeline health check: blocked plans, plan-status drift (stuck pending + expired/cancelled tickets + external completion evidence), flagged changes before each turn (resolved/maintenance noise excluded).',
        '## Procedure\n'
        '\n'
        'DB-first health check of the WorkRequest pipeline. Canonical state lives in PostgreSQL (\`vision.*\`, \`conduit.*\`, \`nebula.*\`); the filesystem is a derived projection and \`nexus/.conduit-data\` is retired (posterity mirror: \`nexus/audit/CONDUIT_DATA\`). Run at the start of every conversational turn, before responding to the user:\n'
        '**Automated backstop:** a scheduled sweep (\`nexus/bin/pipeline-health-sweep.py\`, systemd user timer \`nexus-pipeline-health.timer\`, every 30 min) runs these checks **plus the projection-vs-replay drift scan** (\`conduit-srv GET /wr/drift-scan\`, plan 1285 — active WRs whose \`conduit.work_request_state\` projection disagrees with event replay) and posts findings to the Assembly \`drift-reports\` forum (a new thread only when the finding set changes; resolution thread when it clears). At turn start, prefer the latest pipeline-health thread in \`drift-reports\`; the queries below are the manual fallback.\n'
        '\n'
        '1. **Blocked plans** — plans whose latest receipt is \`BLOCK\`/\`HOLD\`, or with failed/stale tickets, mean the pipeline is jammed — report the blocker prominently. Query:\n'
        '\n'
        '   WITH latest AS (SELECT DISTINCT ON (plan_id) plan_id, type, created_at\n'
        '     FROM vision.receipts WHERE plan_id ~ ''^[0-9]+$''\n'
        '     ORDER BY plan_id, created_at DESC)\n'
        '   SELECT plan_id, type FROM latest WHERE type IN (''BLOCK'',''HOLD'') ORDER BY created_at DESC;\n'
        '\n'
        '2. **Plan-status drift** (pending/PLAN_CREATE + expired/cancelled ticket + external completion evidence) — plans that LOOK pending but the work actually finished, was abandoned, or ran outside the pipeline (the 1274/1275 and 2026-08-09 ghost-batch failure modes). Four signals in one query:\n'
        '\n'
        '   WITH latest AS (\n'
        '     SELECT DISTINCT ON (plan_id) plan_id, type, created_at\n'
        '     FROM vision.receipts WHERE plan_id ~ ''^[0-9]+$''\n'
        '     ORDER BY plan_id, created_at DESC),\n'
        '   stuck AS (\n'
        '     SELECT plan_id, created_at FROM latest\n'
        '     WHERE type = ''PLAN_CREATE'' AND created_at < NOW() - INTERVAL ''24 hours'')\n'
        '   SELECT s.plan_id, to_char(s.created_at,''YYYY-MM-DD'') AS last_plan_create,\n'
        '     (SELECT count(*) FROM vision.tickets t\n'
        '       WHERE t.plan_id = s.plan_id AND (t.status = ''expired''\n'
        '         OR (t.status IN (''open'',''claimed'',''stale'')\n'
        '             AND t.expires_at IS NOT NULL AND t.expires_at < NOW()))) AS expired_tickets,\n'
        '     (SELECT count(*) FROM vision.tickets t\n'
        '       WHERE t.plan_id = s.plan_id AND t.status = ''cancelled'') AS cancelled_tickets,\n'
        '     (SELECT count(*) FROM nebula.agent_records ar\n'
        '       WHERE (ar.plan_ref = s.plan_id\n'
        '          OR ar.content ~* (''(^|[^0-9])'' || s.plan_id || ''([^0-9]|$)''))\n'
        '         AND ar.record_type IN (''report'',''inspection'',''engineering_log'',''assessment'',''analysis'',''decision'')\n'
        '         AND COALESCE(ar.title,'''') NOT ILIKE ''%pre-fk-snapshot%''\n'
        '         AND COALESCE(ar.title,'''') NOT ILIKE ''%drift%''\n'
        '         AND COALESCE(ar.title,'''') NOT ILIKE ''%ghost%''\n'
        '         AND COALESCE(ar.title,'''') NOT ILIKE ''%cross-reference%''\n'
        '         AND COALESCE(ar.title,'''') NOT ILIKE ''CROSS REFERENCES%'') AS evidence_rows\n'
        '   FROM stuck s ORDER BY s.created_at LIMIT 20;\n'
        '\n'
        '   Interpretation per row:\n'
        '   - \`expired_tickets > 0\`: the plan''s ticket(s) expired unclaimed (24h, no re-arm).\n'
        '   - \`cancelled_tickets > 0\`: the plan''s ticket(s) were cancelled while the plan is still pending — abandoned/ghost work (the July-2026 batch signature; 142 ghosts closed via CANCELLED receipts 2026-08-09). Cleanup: issue a \`CANCELLED\` receipt via conduit-srv \`POST /api/receipts/\` (append-only closure) — NOT delete_plan (upstream already archived) and NOT re-dispatch.\n'
        '   - \`evidence_rows > 0\` (noise excluded — pre-fk-snapshot bulk rows, self-authored drift/ghost cleanup records, prompts/responses, cross-reference indexes): external completion evidence exists (agent records, verification inspections, engineering logs referencing the plan). The plan is implemented-but-pending (drift): fix by closure — record IMPLEMENTATION + REVIEW_PASS via conduit — NOT by re-dispatch. Heuristic signal — confirm each candidate manually before closing (UUID/substring coincidences and plan-mirror assessments can still false-positive).\n'
        '   - Oldest-first ordering with \`LIMIT 20\` keeps the report bounded; revisit the tail next turn.\n'
        '   - \`evidence_rows = 0 AND expired_tickets = 0 AND cancelled_tickets = 0\`: genuinely stuck-pending — escalate to the owning role or re-arm the ticket.\n'
        '\n'
        '3. **Flagged changes / blocker reports** — change reports that failed review and inspection blocker reports live in \`nebula.agent_records\`:\n'
        '\n'
        '   SELECT record_type, role, left(title,70) AS title, created_at\n'
        '   FROM nebula.agent_records\n'
        '   WHERE ((tags && ARRAY[''type:rejection'',''type:violation'',''type:incident''])\n'
        '      OR record_type = ''inspection'')\n'
        '     AND NOT (tags && ARRAY[''status:resolved'',''status:done'',''status:closed'',''resolved'',''done'',''closed''])\n'
        '     AND NOT (tags && ARRAY[''cycle:hourly-maintenance'',''hourly-maintenance''])\n'
        '     AND NOT (record_type = ''inspection'' AND (title IN (''.gitkeep'',''REGISTRY'') OR tags = ''{}''))\n'
        '   ORDER BY created_at DESC LIMIT 20;\n'
        '\n'
        '   Noise excluded: records tagged resolved/done/closed (incl. bare variants), routine\n'
        '   hourly-maintenance cycle records, and empty-tag inspection artifacts (.gitkeep/REGISTRY).\n'
        '   Remaining rows are genuinely open incidents/rejections/violations and verification records.\n'
        '\n'
        '4. **Persistence** — these checks are persistent. Report on every turn until resolved. Do not suppress because you already reported before. When the automated sweep is healthy, its \`drift-reports\` thread is the live report; manual checks here are the fallback (sweep down or ad-hoc triage).\n'
        '\n'
        '5. **Full change-detection** — for completed plans and inspection reports, load the \`pipeline-watch\` skill and run its check procedure.',
        ARRAY['turn-protocol', 'pipeline', 'blocker', 'health-check', 'drift'],
        ARRAY['start of turn', 'before responding', 'health check', 'pipeline check', 'drift', 'stuck pending', 'expired ticket', 'cancelled ticket', 'ghost plan', 'implemented but pending'],
        ARRAY['pipeline-watch']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'analyst-ii', 'architect', 'builder', 'critic', 'devops', 'engineer', 'engineer-ii', 'inspector', 'lead-engineer', 'planner', 'reviewer', 'sysadmin', 'tester', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    --  2. Bootstrap Self-Update (Activation)
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'bootstrap-self-update',
        'Bootstrap Self-Update (Activation)',
        'On activation: base boot (inbox + issues forum + to-do forum); Freebuff adds change-log scan + completion dedupe.',
        '## Procedure\n'
        '\n'
        'On role activation (every session start):\n'
        '\n'
        '1. **Ensure projection target directories exist:**\n'
        '   \`\`\`\n'
        '   mkdir -p nexus/audit/{PROMPTS,RESPONSES,PLANS/pending,IMPLEMENTATION_PLANS/active,CHANGES/committed,ENGINEERING/reports,...}\n'
        '   find nexus/audit -type d -empty -not -path ''*/.git/*'' -exec touch {}/.gitkeep \\;\n'
        '   \`\`\`\n'
        '   These are on-demand projection targets, not the canonical store.\n'
        '\n'
        '2. **Base boot — Assembly forum checks (all channels):**\n'
        '   - **Issues forum** (R13): \`GET http://localhost:3107/api/forums/issues-and-open-questions/threads\` — surface unresolved issues before proceeding.\n'
        '   - **To Do forum** (R16): \`GET http://localhost:3107/api/forums/to-do/threads\` — surface open todos (Engineer: undertake UI-only items missing a completion reply).\n'
        '   - **Inbox** (R17): use \`nebula_list_agent_records\` filtered for tags containing \`"to:<your_role>"\` and \`"status:open"\` (or the single-call \`nebula_get_inbox\`) — present new messages.\n'
        '   - If nebula-mcp is unreachable, surface this as a blocking infrastructure issue — do not silently proceed without checking the inbox.\n'
        '\n'
        '3. **Query nebula projection config** to verify current role→folder assignments. Read \`nexus/audit/AGENT_FOLDER_MAP.md\` as a static reference copy.\n'
        '\n'
        '4. **Channel-adaptive extended boot (Freebuff only):**\n'
        '   If you are running on Freebuff (model prefix \`freebuff/*\`, role-lease \`channel\` = \`interactive\`, or harness \`harn-freebuff\`), you have a long-lived session with your own context and the token budget for a deeper boot AFTER the base checks:\n'
        '   - **Scan the change-log forum** (\`GET http://localhost:3107/api/forums/change-log/threads\`) for entries since your last session — learn what has already been completed or reported.\n'
        '   - **Check to-do threads for completion replies** (\`Completed: ...\` comments) or \`statusRating >= 4\` (Accepted/Rejected/Closed) so you do not re-report finished work. See card \`thread-status-ratings\`.\n'
        '   - **Dedupe your report**: surface only NEW issues or changes since the previous session. Do NOT repeat the same set of issues the previous agent already reported; mention still-open items in one line ("still open").\n'
        '   \n'
        '   On opencode (model prefix \`opencode/*\`, role-lease \`channel\` = \`opencode\`, or one-shot harness runs) you are token-constrained: skip this step and keep the base boot (steps 2–3) only. Redundant reporting across opencode sessions is accepted — the user prefers tokens spent on the task itself.\n'
        '\n'
        '5. **Present any new items** to the user before proceeding with their request.\n'
        '',
        ARRAY['turn-protocol', 'activation', 'bootstrap', 'inbox', 'channel-adaptive', 'freebuff'],
        ARRAY['activate', 'session start', 'boot', 'turn start'],
        ARRAY['nebula_list_agent_records', 'nebula_create_agent_record', 'nebula_get_inbox']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'analyst-ii', 'architect', 'builder', 'critic', 'design-synthesist', 'devops', 'engineer', 'engineer-ii', 'inspector', 'layout-mechanic', 'lead-engineer', 'planner', 'reviewer', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    --  3. Post-Turn Self-Update
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'post-turn-self-update',
        'Post-Turn Self-Update',
        'After every response: write agent record to DB, optionally trigger projection.',
        '## Procedure\n'
        '\n'
        'After completing work on every conversational turn:\n'
        '\n'
        '1. **Write to the database first** — Use \`nebula_create_agent_record\` with:\n'
        '   - \`recordType\`: one of \`report\`, \`analysis\`, \`assessment\`, \`inspection\`, \`prompt\`, \`response\`, \`engineering_log\`, \`architecture_note\`, \`decision\`\n'
        '   - \`role\`: your current role\n'
        '   - \`title\`: human-readable summary\n'
        '   - \`content\`: the full markdown body\n'
        '   - \`tags\`: relevant tags for filtering (e.g., \`["architecture", "phase-2"]\`)\n'
        '   - \`systemId\`, \`subsystemId\`, \`planRef\`: optional FK references\n'
        '   - \`threadRef\`: optional UUID to group messages into a thread\n'
        '\n'
        '2. **Optionally trigger a projection** via \`nebula_render_projection\` to regenerate the filesystem view. This is optional — the canonical record is already in the DB.\n'
        '\n'
        '3. **Do NOT write directly to audit directories** — the filesystem is a derived view. Direct writes will be overwritten by the next projection regeneration.\n'
        '\n'
        '4. **Respect folder boundaries** — Do not write to folders assigned to other roles.',
        ARRAY['turn-protocol', 'persistence', 'audit', 'post-turn'],
        ARRAY['after response', 'turn end', 'post-turn', 'after completing'],
        ARRAY['nebula_create_agent_record', 'nebula_render_projection', 'nebula_list_agent_records']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'analyst-ii', 'architect', 'builder', 'critic', 'design-synthesist', 'devops', 'engineer', 'engineer-ii', 'inspector', 'layout-mechanic', 'lead-engineer', 'planner', 'reviewer', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    --  4. Engineer Backlog Check (Nebula RMS)
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'engineer-backlog-check',
        'Engineer Backlog Check (Nebula RMS)',
        'Query nebula RMS backlog before starting work. Surface pending requirements.',
        '## Procedure\n'
        '\n'
        'Engineers must run this check at session start AND at the start of every subsequent turn **before** processing the user''s request.\n'
        '\n'
        '1. **Call \`nebula_list_requirements\`** with no filter to retrieve the entire current requirement set; filter client-side by status.\n'
        '\n'
        '2. **Filter to backlog-relevant items**: keep requirements whose \`status\` is one of \`Backlog\`, \`ToDo\`, \`InProgress\`, \`Active\`, or \`Blocked\`. Exclude \`Done\`, \`Accepted\`, \`Cancelled\`.\n'
        '\n'
        '3. **Present the backlog before acting:**\n'
        '   > "Backlog context — [N] open requirement(s) in Nebula RMS:\n'
        '   > - **[id]** \`[title]\` — [status] · [priority] · parent: [parent]\n'
        '   > Your current request may overlap with one of these. Want to claim an existing item, record new work, or proceed outside the backlog?"\n'
        '\n'
        '4. **Propose, do not auto-claim** — if the request matches a backlog item, surface the candidate and ask before flipping status. Never unilaterally transition a requirement''s status.\n'
        '\n'
        '5. **Record genuinely new work** — if the request is new, create a requirement via \`nebula_create_requirement\`.\n'
        '\n'
        '6. **Re-check before every turn** — backlog state can shift between turns.',
        ARRAY['engineer', 'backlog', 'requirements', 'nebula-rms'],
        ARRAY['start of turn', 'before working', 'backlog', 'requirement'],
        ARRAY['nebula_list_requirements', 'nebula_create_requirement', 'nebula_update_requirement']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['devops', 'engineer', 'engineer-ii', 'lead-engineer', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    --  5. Turn-Based Planning Check (Conduit)
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'turn-based-planning-check',
        'Turn-Based Planning Check (Conduit)',
        'Check for plans promoted to Planning status before each turn.',
        '## Procedure\n'
        '\n'
        'At the start of every turn, before processing the user''s request:\n'
        '\n'
        '1. **Query the pipeline state** — Call \`query_pipeline_state\` (or read \`/state\` via HTTP) to get the current \`PipelineState\`.\n'
        '\n'
        '2. **Inspect \`plans.planning\`** — Look for plans in the \`planning\` array. These are plans with a \`PLANNING\` receipt that are awaiting elucidation.\n'
        '\n'
        '3. **Present findings to the user:**\n'
        '   > "Before we proceed — you have [N] plan(s) in Planning that were promoted but not yet discussed:\n'
        '   > - **#NNNN**: [title] — [goal summary]\n'
        '   > Would you like to discuss any of these before we continue?"\n'
        '\n'
        '4. **Follow the user''s lead:**\n'
        '   - If they want to discuss a planning plan, help elucidate it (files affected, acceptance criteria, dependencies) then call \`issue_receipt\` with \`PLAN_CREATE\` to move it to Pending.\n'
        '   - If they say "not now", proceed with the original request. Planning plans remain in Planning for a future turn.\n'
        '\n'
        '5. **Do NOT auto-promote to Pending** — the user must explicitly confirm.',
        ARRAY['turn-protocol', 'planning', 'conduit', 'elucidation'],
        ARRAY['start of turn', 'planning check', 'promoted plan', 'plan pipeline'],
        ARRAY['conduit-mcp_query_conduit_state', 'conduit-mcp_issue_receipt']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['architect', 'builder', 'devops', 'engineer', 'engineer-ii', 'lead-engineer', 'planner', 'reviewer', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    --  6. Prompt Capture (Audit Trail)
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'prompt-capture',
        'Prompt Capture (Audit Trail)',
        'Save every interactive prompt as the start of the audit trail.',
        '## Procedure\n'
        '\n'
        'Every interactive prompt must be saved as the start of the audit trail.\n'
        '\n'
        '1. **Save every prompt** — Use \`nebula_create_agent_record\` with \`recordType: "prompt"\`. The database is the canonical store — do not write directly to filesystem directories.\n'
        '\n'
        '2. **Link plans to prompts** — When a prompt results in an implementation plan, pass the \`promptRef\` (prompt number) to \`create_plan\` or \`create_proposed_plan\`. This creates a bidirectional audit trail: prompt → plan references.\n'
        '\n'
        '3. **Preserve continuity** — The prompt number allows subsequent plans, proposals, and responses to reference the originating intent.',
        ARRAY['audit', 'prompt', 'capture', 'traceability'],
        ARRAY['user prompt', 'new conversation', 'question', 'request'],
        ARRAY['nebula_create_agent_record', 'conduit-mcp_create_plan', 'conduit-mcp_create_proposed_plan']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'analyst-ii', 'architect', 'builder', 'critic', 'devops', 'engineer', 'engineer-ii', 'inspector', 'lead-engineer', 'planner', 'reviewer', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    --  7. Inbox Query (Role-Driven Messaging)
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'inbox-query-procedure',
        'Inbox Query (Role-Driven Messaging)',
        'Query your role inbox for open messages before proceeding each turn.',
        '\n'
        '## Procedure\n'
        '\n'
        'Before processing any request, query your role''s inbox for messages from other agents.\n'
        '\n'
        '1. **Query your inbox (R17, verified)** — the nebula REST API is the simple path:\n'
        '\n'
        '   \`\`\`bash\n'
        '   curl -s "http://localhost:3101/api/agent-records?role=<your_role>&createdAfter=<pointer_iso>" \\\n'
        '     | python3 -c ''import sys,json; d=json.load(sys.stdin); [print(i["createdAt"], i["title"][:60]) for i in d.get("items",[])]''\n'
        '   \`\`\`\n'
        '\n'
        '   - Store the last-seen pointer at \`http://localhost:3101/api/inbox-pointer/<role>\` (GET / PUT, ISO timestamps).\n'
        '   - **Caveat (verified):** this endpoint applies \`role\` + \`createdAfter\` but silently IGNORES \`tags\` and \`limit\` (returns up to 100, newest first).\n'
        '   - Records return \`createdAt\` as epoch ms — convert to ISO before using it in \`createdAfter\`/the pointer.\n'
        '\n'
        '2. **Ready-made helper** — \`nexus/bin/check-inbox.sh --role <your_role>\` wraps the exact tag-faithful query:\n'
        '   - \`--all\` ignores the pointer, \`--pointer <ISO>\` overrides it, \`--update-pointer\` advances it, \`--limit N\`, \`--raw\`, \`-h\`.\n'
        '   - Default path: single \`nebula_get_inbox\` MCP call on nebula-mcp 3102 (Streamable HTTP) via the canonical client lib \`nexus/python/nebula-mcp-client/\` — resolves the stored pointer and applies \`tags:["to:<role>"]\` server-side in one round-trip. \`--pointer <ISO>\` / \`--all\` fall back to \`nebula_list_agent_records\` with an explicit \`createdAfter\`.\n'
        '\n'
        '3. **Weekly review (once per week, non-destructive)** — look back 7 days for anything that slipped through. \`--since 7d\` (shorthand for \`--pointer "<7 days ago ISO>"\`) overrides the \`createdAfter\` filter for this call only and leaves the stored pointer untouched, so the next normal check never re-delivers already-seen records:\n'
        '\n'
        '   \`\`\`bash\n'
        '   nexus/bin/check-inbox.sh --role <your_role> --since 7d --limit 100\n'
        '   \`\`\`\n'
        '\n'
        '   - Assess what was missed and surface any items that slipped through.\n'
        '   - If the review covered everything, optionally mark it all as seen by adding \`--update-pointer\` (advances to the newest record in the window).\n'
        '   - The raw-REST equivalent — a *permanent* rewind that re-delivers the week on the next check — is \`PUT /api/inbox-pointer/<role>\` with a 7-day-old ISO timestamp; rarely wanted.\n'
        '\n'
        '4. **Present findings** — Surface any open messages to the user before acting. Do NOT silently process inbox items.\n'
        '\n'
        '5. **Tag routing conventions:**\n'
        '   - \`to:{role}\` — intended recipient (engineer, architect, planner, etc.)\n'
        '   - \`from:{role}\` — sender\n'
        '   - \`status:{state}\` — open, claimed, in_progress, resolved, archived\n'
        '   - \`type:{kind}\` — incident, task, question, decision, finding, proposal, etc.\n'
        '   - \`thread:{id}\` — thread membership (short form UUID)\n'
        '\n'
        '6. **Thread tracking** — Conversations between roles use \`threadRef\` (shared UUID across messages):\n'
        '   - First message: new UUID threadRef\n'
        '   - Response: same threadRef, updated status\n'
        '   - Query: \`nebula_list_agent_records\` with \`threadRef = "<uuid>"\`\n'
        '\n'
        '7. **Infrastructure failure** — If nebula-mcp / nebula REST is unreachable, surface as a blocking issue. Do not silently proceed.\n'
        '\n'
        '',
        ARRAY['messaging', 'inbox', 'routing', 'communication'],
        ARRAY['start of turn', 'inbox', 'messages', 'agent communication'],
        ARRAY['nebula_list_agent_records', 'nebula_create_agent_record', 'nebula_update_agent_record']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'analyst-ii', 'architect', 'auditor', 'builder', 'critic', 'DBA', 'design-synthesist', 'devops', 'engineer', 'engineer-ii', 'epistemologist', 'inspector', 'layout-mechanic', 'lead-engineer', 'ontologist', 'planner', 'reviewer', 'sound-technician', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    --  8. Thread Tracking (Cross-Role Conversations)
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'thread-tracking',
        'Thread Tracking (Cross-Role Conversations)',
        'Create and continue cross-role conversations via threadRef UUIDs.',
        '## Procedure\n'
        '\n'
        'Conversations between roles use \`threadRef\` (a shared UUID across messages).\n'
        '\n'
        '1. **First message**: Author writes a record with a new \`threadRef\` UUID and tags \`["to:recipient", "status:open", "type:kinds"]\`.\n'
        '\n'
        '2. **Response**: Recipient writes a record with the same \`threadRef\`, tags \`["to:author", "status:in_progress", "type:kinds"]\`.\n'
        '\n'
        '3. **Continuation**: Any role writes to the same thread with updated \`status\` and appropriate \`to:\` tag.\n'
        '\n'
        '4. **Querying threads**: Filter for \`threadRef = "<uuid>"\` and order by \`created_at\`.\n'
        '\n'
        '5. **Resolving threads**: Update all messages in the thread to \`status:resolved\`.\n'
        '\n'
        '## Common Thread Lifecycle\n'
        '\n'
        '1. Open → Claimed → In Progress → Resolved\n'
        '2. Open → Resolved (simple acknowledgment)\n'
        '3. Open → Escalated → (owning role decision) → Resolved',
        ARRAY['messaging', 'thread', 'conversation', 'cross-role'],
        ARRAY['conversation', 'thread', 'cross-role', 'respond to agent'],
        ARRAY['nebula_list_agent_records', 'nebula_create_agent_record', 'nebula_update_agent_record']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'analyst-ii', 'architect', 'auditor', 'builder', 'critic', 'DBA', 'design-synthesist', 'devops', 'engineer', 'engineer-ii', 'epistemologist', 'inspector', 'layout-mechanic', 'lead-engineer', 'ontologist', 'planner', 'reviewer', 'sound-technician', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    --  9. Tag Routing Convention Reference
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'tag-routing-reference',
        'Tag Routing Convention Reference',
        'Reference for valid agent message tags (to:, from:, status:, type:, thread:).',
        '## Tag Routing Reference\n'
        '\n'
        'All tags are lower-kebab-case. Multiple tags form a conjunction.\n'
        '\n'
        '### Prefix Tags\n'
        '\n'
        '| Tag | Purpose | Examples |\n'
        '|-----|---------|----------|\n'
        '| \`to:{role}\` | Intended recipient | \`to:engineer\`, \`to:architect\`, \`to:planner\` |\n'
        '| \`from:{role}\` | Sender | \`from:architect\` |\n'
        '| \`status:{state}\` | Message lifecycle | \`status:open\`, \`status:claimed\`, \`status:in_progress\`, \`status:resolved\`, \`status:archived\` |\n'
        '| \`type:{kind}\` | Semantic kind | \`type:incident\`, \`type:task\`, \`type:question\`, \`type:decision\`, \`type:spec\`, \`type:finding\`, \`type:blocker\`, \`type:proposal\`, \`type:warning\`, \`type:error\`, \`type:approval\`, \`type:rejection\`, \`type:disagreement\`, \`type:escalation\`, \`type:deferred\`, \`type:db-change\` |\n'
        '| \`thread:{id}\` | Thread membership | \`thread:a1b2c3\` |\n'
        '\n'
        '### DB-Change Routing Tag\n'
        '- \`type:db-change\` — plan requires database work; recipient DBA posts the proposed alterations to the Assembly Drafts forum (slug \`draft\`) and applies them ONLY after admin approval, BEFORE a Builder starts (doctrine 2026-08-07). Pair with \`to:dba\`, \`planRef:<N>\`, \`status:open\`; completion reported with \`status:resolved\`/\`status:done\`.\n'
        '- The Drafts forum (slug \`draft\`) is the DBA''s DB-work channel: DBA posts proposals there AND checks it for admin approval/rejection replies and incoming DB-change requests (in addition to the nebula inbox).\n'
        '\n'
        '### Divergence Tags\n'
        '- \`type:disagreement\` — Explicit conflicting position\n'
        '- \`type:escalation\` — Request for owning role to resolve\n'
        '- \`type:deferred\` — Known conflict tabled for later\n'
        '\n'
        '### Domain Tags (ad-hoc, lowercase)\n'
        '- \`domain:knowledge-infrastructure\`, \`domain:type-spec\`, etc.\n'
        '- \`priority:high\`, \`priority:medium\`, \`priority:low\`\n'
        '\n'
        '### Where these tags are used (verified)\n'
        '- **R17 inbox query:** the nebula REST endpoint (\`3101\`) applies \`role\` + \`createdAfter\` but IGNORES \`tags\`/\`limit\`; for exact tag-routed queries use \`nexus/bin/check-inbox.sh\` (MCP HTTP+SSE on 3102).\n'
        '- **R13 session-start forum check:** the Assembly \`issues-and-open-questions\` check now uses the Assembly REST API on 3107 (\`GET /api/forums/issues-and-open-questions/threads\`) — there is no \`3102/tools/call\` route on nebula-mcp.',
        ARRAY['messaging', 'reference', 'tags', 'routing'],
        ARRAY['tag routing', 'message format', 'tag convention', 'what tags'],
        '{}'
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'analyst-ii', 'architect', 'auditor', 'builder', 'critic', 'DBA', 'design-synthesist', 'devops', 'engineer', 'engineer-ii', 'epistemologist', 'inspector', 'layout-mechanic', 'lead-engineer', 'ontologist', 'planner', 'reviewer', 'sound-technician', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 10. Rover Harvest Notification
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'rover-harvest-notification',
        'Rover Harvest Notification',
        'After harvests, create cross-refs and notify Architect + Analyst.',
        '## Procedure\n'
        '\n'
        '1. **Execute the harvest** using Rover. Always use yourself as the inference component — do not delegate to Ollama unless explicitly told.\n'
        '\n'
        '2. **Persist harvest output** to the database via \`nebula_create_harvest\` (or \`POST /api/harvests\`).\n'
        '\n'
        '3. **Create cross-references** linking the harvest to knowledge entities:\n'
        '   a. Direct references via \`nebula_create_cross_reference\` with \`relType: "informs"\` (harvest → entity) and \`relType: "sourced_from"\` (entity → harvest). Use \`knowledge_list_entities\` to find matching entities.\n'
        '   b. Run automated discovery scripts: \`embed_harvests.py\`, \`embed_knowledge_entities.py\`, \`cross_schema_classifier.py\`, \`provenance_linker.py\` (requires Ollama + pgvector).\n'
        '\n'
        '4. **Notify Architect and Analyst** via \`nebula_create_agent_record\`:\n'
        '   - \`tags: ["to:architect", "status:open", "type:finding", "thread:..."]\`\n'
        '   - \`tags: ["to:analyst", "status:open", "type:finding", "thread:..."]\`\n'
        '   - Same \`threadRef\` UUID for both so they share a conversation thread.\n'
        '   - Title: "New harvest material available: <topic/summary>"',
        ARRAY['harvest', 'post-processing', 'notification', 'cross-reference'],
        ARRAY['rover', 'harvest', 'chat transcript', 'nebula_create_harvest'],
        ARRAY['nebula_create_harvest', 'nebula_create_cross_reference', 'knowledge_list_entities', 'nebula_create_agent_record']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['devops', 'engineer', 'engineer-ii', 'lead-engineer', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 11. Terrain Registration
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'terrain-registration',
        'Terrain Registration',
        'Register services in terrain topology after building or deploying.',
        '## Procedure\n'
        '\n'
        '1. **Identify the service** — name, type (api|db|queue|worker|ui), endpoint, health check, dependencies.\n'
        '\n'
        '2. **Call \`terrain-mcp\`** to register:\n'
        '   - \`terrain_register_service\` — create new entry\n'
        '   - \`terrain_update_service\` — update existing metadata\n'
        '   - Include: \`name\`, \`type\`, \`endpoint\`, \`health_check\`, \`depends_on\`, \`metadata\` (version, region, etc.)\n'
        '\n'
        '3. **Verify** via \`terrain_list_services\` — confirm the service appears with correct topology links.',
        ARRAY['deployment', 'infrastructure', 'service-registry', 'topology'],
        ARRAY['deploy', 'build', 'set up', 'service', 'register'],
        ARRAY['terrain_register_service', 'terrain_list_services']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['devops', 'engineer', 'engineer-ii', 'lead-engineer', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 12. Planning Elucidation Workflow
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'planning-elucidation',
        'Planning Elucidation Workflow',
        'Elucidate a planning-plan before promoting it to pending.',
        '## Procedure\n'
        '\n'
        '1. **Present the plan** — show title, goal, existing metadata.\n'
        '\n'
        '2. **Discuss scope** — "Which files or modules would this change affect?" Capture as \`filesAffected\`.\n'
        '\n'
        '3. **Refine Acceptance Criteria** — define concrete, testable criteria.\n'
        '\n'
        '4. **Identify Dependencies** — check if this plan depends on others.\n'
        '\n'
        '5. **Confirm** — present summary and get explicit user confirmation.\n'
        '\n'
        '6. **Persist metadata** via \`update_plan\` or \`report_plan_metadata\`.\n'
        '\n'
        '7. **Move to Pending** — call \`issue_receipt\` with \`PLAN_CREATE\`.',
        ARRAY['planning', 'elucidation', 'promotion'],
        ARRAY['discuss plan', 'promote plan', 'elucidate', 'planning plan'],
        ARRAY['conduit-mcp_update_plan', 'conduit-mcp_issue_receipt']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['planner'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 13. Proposal Capture (Followup Preservation)
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'proposal-capture',
        'Proposal Capture (Followup Preservation)',
        'Persist followup suggestions as proposed plans after completing work.',
        '## Procedure\n'
        '\n'
        '1. After calling \`suggest_followups\`, call \`create_proposed_plan\` for each suggestion.\n'
        '2. Use the suggestion label as title and a brief description as goal.\n'
        '3. Pass the current promptRef for bidirectional audit trail: prompt → proposal → implementation plan.\n'
        '4. Proposed plans are lightweight ideas — no files or acceptance criteria.',
        ARRAY['proposal', 'followup', 'preservation'],
        ARRAY['suggest followup', 'after completing', 'propose', 'follow-up'],
        ARRAY['conduit-mcp_create_proposed_plan']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['architect', 'devops', 'engineer', 'engineer-ii', 'lead-engineer', 'planner', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 14. Nexus Boot Procedure
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'nexus-boot-procedure',
        'Nexus Boot Procedure',
        'Minimum startup read set before making changes in nexus/.',
        '## Procedure\n'
        '\n'
        'Load at minimum (paths verified 2026-09-15):\n'
        '1. \`nexus/CLAUDE.md\`\n'
        '2. \`/home/codex/dev/AGENTS.md\` — governing doctrine; nexus/CLAUDE.md defers routing to it\n'
        '3. \`nexus/ARCHITECTURE.md\` — service architecture (repo root; NOT docs/ARCHITECTURE.md)\n'
        '4. Current conduit-mcp pipeline state: \`curl http://localhost:3100/state\`\n'
        '\n'
        'Additional docs as needed, not indiscriminately.\n'
        '\n'
        '**History:** the original card pointed at \`nexus/.agents/{pipeline-mode.json,OPERATING_MODEL.md,skills/mode-router/SKILL.md}\`. That tree was classified GOVERNANCE-ASPIRATIONAL and deliberately removed (commit 4f176f04, 2026-08-16) — do not treat it as live authority and do not restore it. \`nexus/CLAUDE.md\`''s own reference to \`docs/ARCHITECTURE.md\` is likewise stale; the architecture doc lives at the repo root.',
        ARRAY['bootstrap', 'startup', 'initialization'],
        ARRAY['start session', 'activate', 'boot', 'nexus'],
        '{}'
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['architect', 'auditor', 'builder', 'DBA', 'design-synthesist', 'devops', 'engineer', 'engineer-ii', 'epistemologist', 'layout-mechanic', 'lead-engineer', 'ontologist', 'planner', 'reviewer', 'sound-technician', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 15. Plan Deletion & Ticket Cleanup
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'plan-deletion-cleanup',
        'Plan Deletion & Ticket Cleanup',
        'Soft-delete a plan, cancel its open tickets, and notify the UI.',
        '## Procedure\n'
        '\n'
        '1. Call \`conduit-mcp_delete_plan\` with the plan number.\n'
        '   - Soft-deletes in DB (deleted=1)\n'
        '   - Removes .md files from all IMPLEMENTATION_PLANS/ subdirs\n'
        '   - Cancels open tickets with closure_reason = plan_deleted\n'
        '   - Calls removePlanFromMemory() on the watcher\n'
        '   - Emits plan_deleted SSE event to the UI\n'
        '\n'
        '2. For stuck plans that cannot be recovered, use \`conduit-mcp_hard_delete_plan\` (irreversible). Requires confirmPlanTitle to match as a safety guard.\n'
        '\n'
        '3. Running delete_plan on an already-deleted plan is safe — it cleans up residual watcher state.',
        ARRAY['plan', 'deletion', 'cleanup', 'ticket'],
        ARRAY['delete plan', 'remove plan', 'cancel plan', 'stuck plan'],
        ARRAY['conduit-mcp_delete_plan', 'conduit-mcp_hard_delete_plan']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['builder', 'devops', 'engineer', 'engineer-ii', 'lead-engineer', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 16. Orphan Detection
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'orphan-detection',
        'Orphan Detection',
        'Check for inconsistencies between DB state and filesystem artifacts.',
        '## Procedure\n'
        '\n'
        'The conduit MCP /health endpoint includes an orphanScan section:\n'
        '- Plans deleted in DB (deleted=1) that still have .md files on disk\n'
        '- .md files on disk with no corresponding DB row\n'
        '\n'
        'Use this as a periodic check. The watcher getState() also filters soft-deleted plans from the filesystem-driven cache.',
        ARRAY['orphan', 'inconsistency', 'health'],
        ARRAY['check health', 'orphan scan', 'inconsistency'],
        '{}'
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['devops', 'engineer', 'engineer-ii', 'inspector', 'lead-engineer', 'reviewer', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 17. Nebula-MCP Tool Reference
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'nebula-mcp-tools',
        'Nebula-MCP Tool Reference',
        'Complete catalog of nebula-mcp tools organized by domain.',
        '## Nebula-MCP Tool Reference\n'
        '\n'
        'Full catalog of nebula-mcp tools, organized by domain. Available over MCP transport (Stdio or SSE on port 3102).\n'
        '\n'
        '### Hierarchy: Systems / Subsystems / Features\n'
        '| Tool | Purpose |\n'
        '|------|---------|\n'
        '| nebula_list_systems | List all systems with full nested hierarchy |\n'
        '| nebula_create_system | Create a new system |\n'
        '| nebula_update_system | Update system metadata |\n'
        '| nebula_delete_system | Delete a system and cascade |\n'
        '| nebula_create_subsystem | Create a subsystem |\n'
        '| nebula_update_subsystem | Update subsystem metadata |\n'
        '| nebula_delete_subsystem | Delete a subsystem and cascade |\n'
        '| nebula_move_subsystem | Move a subsystem to a different parent |\n'
        '| nebula_create_feature | Create a feature under a subsystem |\n'
        '| nebula_update_feature | Update feature metadata |\n'
        '| nebula_delete_feature | Delete a feature and cascade |\n'
        '| nebula_move_feature | Move a feature to a different subsystem |\n'
        '\n'
        '### Requirements (Backlog / Kanban)\n'
        '| Tool | Purpose |\n'
        '|------|---------|\n'
        '| nebula_list_requirements | List requirements, filterable |\n'
        '| nebula_create_requirement | Create a new requirement |\n'
        '| nebula_update_requirement | Update requirement fields |\n'
        '| nebula_move_requirement | Move requirement to a new status |\n'
        '| nebula_delete_requirement | Delete a requirement |\n'
        '| nebula_batch_update_requirements | Batch-update status |\n'
        '\n'
        '### Agent Records (Bitemporal Audit)\n'
        '| Tool | Purpose |\n'
        '|------|---------|\n'
        '| nebula_list_agent_records | List audit records, filterable |\n'
        '| nebula_get_agent_record | Get a single record with full content |\n'
        '| nebula_create_agent_record | Create a new record (canonical write path) |\n'
        '| nebula_update_agent_record | Update an existing record |\n'
        '| nebula_delete_agent_record | Delete a record |\n'
        '\n'
        '### Harvest Pipeline\n'
        '| Tool | Purpose |\n'
        '|------|---------|\n'
        '| nebula_list_harvests | List harvest outputs |\n'
        '| nebula_get_harvest | Get a single harvest |\n'
        '| nebula_create_harvest | Record a new harvest |\n'
        '| nebula_delete_harvest | Delete a harvest |\n'
        '\n'
        '### Projections (Markdown Generation)\n'
        '| Tool | Purpose |\n'
        '|------|---------|\n'
        '| nebula_list_projections | List projection configs |\n'
        '| nebula_create_projection | Create a projection config |\n'
        '| nebula_render_projection | Execute projection, write output |\n'
        '| nebula_delete_projection | Delete a projection |\n'
        '\n'
        '### Cross-References\n'
        '| Tool | Purpose |\n'
        '|------|---------|\n'
        '| nebula_list_cross_references | List cross-references, filterable |\n'
        '| nebula_get_cross_reference | Get a single cross-reference |\n'
        '| nebula_create_cross_reference | Create a cross-reference link |\n'
        '| nebula_delete_cross_reference | Delete a cross-reference |\n'
        '\n'
        '### Other Domains\n'
        '| Tool | Purpose |\n'
        '|------|---------|\n'
        '| nebula_create_folder | Create a system folder |\n'
        '| nebula_delete_folder | Delete a system folder |\n'
        '| nebula_list_sessions | List work sessions |\n'
        '| nebula_create_session | Record a work session |\n'
        '| nebula_update_session | Update session outcome |\n'
        '| nebula_delete_session | Delete a session |\n'
        '| nebula_list_workspaces | List workspace mappings |\n'
        '| nebula_create_workspace | Map system to filesystem path |\n'
        '| nebula_delete_workspace | Remove workspace mapping |\n'
        '| nebula_read_docs | Read README/ARCHITECTURE from disk |\n'
        '| nebula_read_system_docs | Read docs from all system workspaces |\n'
        '| nebula_read_subsystem_docs | Read docs from subsystem workspaces |\n'
        '| nebula_list_plans | List implementation plans |\n'
        '| nebula_get_plan | Fetch a single plan |\n'
        '| nebula_get_preferences | Get all user preferences |\n'
        '| nebula_set_preference | Set a preference value |\n'
        '| nebula_delete_preference | Delete a preference |\n'
        '| nebula_get_system_info | Get info tab content |\n'
        '| nebula_set_system_info | Save info tab content |\n'
        '| nebula_demote_system | Demote system into subsystem |\n'
        '| nebula_import | Bulk-import data |\n'
        '| nebula_seed | Idempotently seed example data |\n'
        '| nebula_query_conduit_plans | List conduit plans (bitemporal) |\n'
        '| nebula_query_conduit_plan_history | Full lifecycle of one plan |\n'
        '| nebula_query_conduit_plan_receipts | Receipts for a plan |\n'
        '| nebula_query_conduit_as_of | Point-in-time snapshot |\n'
        '| nebula_list_deleted_conduit_plans | Find soft-deleted plans |\n'
        '| nebula_health | Check server and DB health',
        ARRAY['reference', 'nebula-mcp', 'tools', 'appendix'],
        ARRAY['list tools', 'what tools', 'nebula-mcp', 'MCP reference'],
        '{}'
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'analyst-ii', 'architect', 'auditor', 'builder', 'critic', 'DBA', 'design-synthesist', 'devops', 'engineer', 'engineer-ii', 'epistemologist', 'inspector', 'layout-mechanic', 'lead-engineer', 'ontologist', 'planner', 'reviewer', 'sound-technician', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 18. Tackle-MCP Tool Reference
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'tackle-mcp-tools',
        'Tackle-MCP Tool Reference',
        'Complete catalog of tackle-mcp tools for AI config and memory management.',
        '## Tackle-MCP Tool Reference\n'
        '\n'
        'Tackle-mcp (port 3400) manages the AI configuration registry and Role Memory Procedure Registry.\n'
        '\n'
        '### AI Configuration Registry\n'
        '| Tool | Purpose |\n'
        '|------|---------|\n'
        '| get_ai_config | Get full AI configuration snapshot |\n'
        '| validate_ai_config | Validate configuration |\n'
        '| seed_default_ai_config | Seed default providers, harnesses, models |\n'
        '| import_ai_config | Replace entire configuration snapshot |\n'
        '\n'
        '### Providers\n'
        '| Tool | Purpose |\n'
        '|------|---------|\n'
        '| list_ai_providers | List all AI providers |\n'
        '| get_ai_provider(id) | Get a single provider |\n'
        '| upsert_ai_provider | Create or update a provider |\n'
        '| delete_ai_provider(id) | Delete a provider |\n'
        '\n'
        '### Harnesses\n'
        '| Tool | Purpose |\n'
        '|------|---------|\n'
        '| list_ai_harnesses | List all AI harnesses |\n'
        '| get_ai_harness(id) | Get a single harness |\n'
        '| upsert_ai_harness | Create or update a harness |\n'
        '| delete_ai_harness(id) | Delete a harness |\n'
        '\n'
        '### Models\n'
        '| Tool | Purpose |\n'
        '|------|---------|\n'
        '| list_ai_models | List all AI models |\n'
        '| get_ai_model(id) | Get a single model |\n'
        '| upsert_ai_model | Create or update a model |\n'
        '| delete_ai_model(id) | Delete a model |\n'
        '\n'
        '### Role Configs & Bundles\n'
        '| Tool | Purpose |\n'
        '|------|---------|\n'
        '| list_ai_role_configs | List all role configs |\n'
        '| get_ai_role_config(role) | Get a single role config |\n'
        '| upsert_ai_role_config | Create or update role config |\n'
        '| list_config_bundles(role) | List bundles for a role |\n'
        '| upsert_config_bundle | Create or update a bundle |\n'
        '| delete_config_bundle(id) | Delete a bundle |\n'
        '\n'
        '### Role Memory Procedures\n'
        '| Tool | Purpose | Reads From |\n'
        '|------|---------|------------|\n'
        '| memory_get_procedures(role) | Return procedure index for a role | Redis |\n'
        '| memory_get_procedure(slug) | Return full procedure card | Redis |\n'
        '| memory_check_since(role, since) | Check if memory changed | PostgreSQL |\n'
        '| memory_refresh() | Trigger full PG\\u2192Redis sync | role-memory-srv |',
        ARRAY['reference', 'tackle-mcp', 'tools', 'appendix'],
        ARRAY['list tools', 'what tools', 'tackle-mcp', 'MCP reference'],
        '{}'
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'analyst-ii', 'architect', 'auditor', 'builder', 'critic', 'DBA', 'design-synthesist', 'devops', 'engineer', 'engineer-ii', 'epistemologist', 'inspector', 'layout-mechanic', 'lead-engineer', 'ontologist', 'planner', 'reviewer', 'sound-technician', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 19. Conduit-MCP Tool Reference
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'conduit-mcp-tools',
        'Conduit-MCP Tool Reference',
        'Complete catalog of conduit-mcp tools for plan lifecycle and pipeline management.',
        '## Conduit-MCP Tool Reference\n'
        '\n'
        'Conduit-mcp (port 3100) manages the plan lifecycle, issues receipts, and serves pipeline state.\n'
        '\n'
        '### Plan Lifecycle\n'
        '| Tool | Purpose |\n'
        '|------|---------|\n'
        '| query_conduit_state | Return full pipeline state |\n'
        '| create_plan | Create a pending implementation plan |\n'
        '| create_proposed_plan | Create a lightweight proposed plan |\n'
        '| update_plan | Update plan metadata |\n'
        '| delete_plan | Soft-delete a plan |\n'
        '| hard_delete_plan | Permanently delete a stuck plan |\n'
        '| promote_plan | Promote proposed \\u2192 planning |\n'
        '| revise_plan | Create a revision copy in planning |\n'
        '| unblock_plan | Move blocked \\u2192 pending |\n'
        '| report_plan_metadata | Update plan title/description |\n'
        '| get_plan_receipts | Get receipt chain for a plan |\n'
        '\n'
        '### Receipts & Agent Status\n'
        '| Tool | Purpose |\n'
        '|------|---------|\n'
        '| issue_receipt | Record a conduit event receipt |\n'
        '| report_builder_status | Report builder process status |\n'
        '| agent_heartbeat | Report agent liveness and state |\n'
        '| agent_finished | Report agent completed its task |\n'
        '\n'
        '### Queries\n'
        '| Tool | Purpose |\n'
        '|------|---------|\n'
        '| query_analytics | Query conduit analytics metrics |\n'
        '| query_prompts | Search captured prompts with lineage |\n'
        '| query_nebula_backlog | Query Nebula RMS backlog |\n'
        '| query_nebula_systems | Query Nebula RMS hierarchy |',
        ARRAY['reference', 'conduit-mcp', 'tools', 'appendix'],
        ARRAY['list tools', 'what tools', 'conduit-mcp', 'MCP reference'],
        '{}'
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'analyst-ii', 'architect', 'auditor', 'builder', 'critic', 'DBA', 'design-synthesist', 'devops', 'engineer', 'engineer-ii', 'epistemologist', 'inspector', 'layout-mechanic', 'lead-engineer', 'ontologist', 'planner', 'reviewer', 'sound-technician', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 20. Knowledge Stratification (L1-L4) — altitude, not rank
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'knowledge-stratification',
        'Knowledge Stratification (L1-L4) — altitude, not rank',
        'Two-axis knowledge model: abstraction levels L1-L4 (explanatory altitude, NOT a rank) combined with visibility scopes.',
        '## Knowledge Stratification\n'
        '\n'
        'Every document and chunk has two independent attributes: Abstraction Level and Visibility Scope.\n'
        '\n'
        'IMPORTANT: Level is an abstraction / explanatory altitude, NOT a rank. It does not rank truth, confidence, importance, visibility, lifecycle, or authority. Authority lives with owning systems and admission paths; visibility is a separate scope (Axis 2). A higher level is not "better" or more senior — it is a different altitude of explanation. The same canonical subject may carry L1, L2, L3, and L4 projections simultaneously; level belongs on the projected assertion/version, never the canonical identity.\n'
        '\n'
        '### Axis 1: Abstraction Level (L1-L4)\n'
        '\n'
        '| Level | Name | Explains (altitude) |\n'
        '|-------|------|---------------------|\n'
        '| L1 | Evidence / mechanics | What happened and how it works at the operational level (APIs, schemas, contracts, error codes, configs, execution evidence) |\n'
        '| L2 | Semantic structure | How data and state are organized (subsystem design, DAG semantics, data models) |\n'
        '| L3 | Architecture / rationale | Why the system exists and the reasoning behind its shape (rationale, trade-offs, migration philosophy) |\n'
        '| L4 | Doctrine / governing interpretation | The rules and boundaries that govern the system (cross-system doctrine, ontology, role boundaries, governance) |\n'
        '\n'
        'Levels are defaults, not source-system labels — content determines its level. A model-check result is L1 execution evidence even if Aegis produced it; its interpretation is L3. A level change produces a new projection/version, never a silent mutation. Unknown or disputed levels remain explicit rather than guessed.\n'
        '\n'
        '### Axis 2: Visibility Scope\n'
        '\n'
        '| Scope | Effect |\n'
        '|-------|--------|\n'
        '| builder | Visible to builder role only |\n'
        '| architect | Visible to architect role only |\n'
        '| planner | Visible to planner role only |\n'
        '| reviewer | Visible to reviewer role only |\n'
        '| all | Visible to all roles |\n'
        '\n'
        '### Per-Role Query Filters\n'
        '\n'
        'The ranges below are typical starting altitudes for retrieval and explanation — NOT permissions, clearances, or a rank ordering. Any role may move between altitudes explicitly; a broader range is a wider angle of explanation, not a promotion.\n'
        '\n'
        '| Role | Typical Starting Altitude | Visibility Filter |\n'
        '|------|---------------------------|-------------------|\n'
        '| Builder | L1 primary, L2 secondary | scope IN (builder, all) |\n'
        '| Architect | L2-L3 primary, L4 allowed | scope IN (architect, all) |\n'
        '| Planner | L1-L2 primary, L3 allowed | scope IN (planner, all) |\n'
        '| Reviewer | L1-L2 | scope IN (reviewer, builder, all) |\n'
        '| Inspector | L2-L3 with preference for normative chunks | scope IN (all) |\n'
        '| Analyst | L2-L3 | scope IN (analyst, all) |\n'
        '\n'
        '### Cross-Reference Semantics\n'
        'Cross-references are a conditional expansion operator, not a default join. Builders start narrow and expand when blocked; Architects start broader for design context; Inspectors expand aggressively for compliance.\n'
        '',
        ARRAY['reference', 'knowledge', 'stratification', 'levels'],
        ARRAY['knowledge levels', 'L1 L2 L3 L4', 'stratification', 'visibility'],
        '{}'
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'analyst-ii', 'architect', 'auditor', 'builder', 'critic', 'DBA', 'design-synthesist', 'devops', 'engineer', 'engineer-ii', 'epistemologist', 'inspector', 'layout-mechanic', 'lead-engineer', 'ontologist', 'planner', 'reviewer', 'sound-technician', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 21. WorkRequest Pattern Participation
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'work-request-participation',
        'WorkRequest Pattern Participation',
        'How to participate in the WorkRequest pattern: capture, plan, emit, execute, recover.',
        '## WorkRequest Participation\n'
        '\n'
        'Unless the user explicitly asks for a different workflow, participate in the WorkRequest pattern as follows:\n'
        '\n'
        '### 1. Prompt & Intent Capture\n'
        'For non-trivial requests, preserve the request in prompt or planning records. Query conduit-mcp pipeline state before creating new record formats. Extend existing records instead of inventing parallel files. Avoid claiming archival is complete if the storage path doesn''t exist.\n'
        '\n'
        '### 2. Implementation Plan Stacking\n'
        'When the task is substantial, cross-file, risky, or spans sessions:\n'
        '- Create or update an implementation plan in the expected location\n'
        '- Stack new plans on top of existing state, don''t overwrite history\n'
        '- Keep scope narrow enough to be executable\n'
        '- Verify no pending/active plan covers the same work\n'
        '\n'
        '### 3. WorkRequest Emission\n'
        'Generate explicit WorkRequests when:\n'
        '- Prompted by the user\n'
        '- The active repository workflow clearly expects them\n'
        '- Follow existing schemas and lifecycle conventions\n'
        '- Supersede or version existing artifacts instead of mutating history\n'
        '\n'
        '### 4. Execution\n'
        '- Execute only work that is directly requested or already authorized\n'
        '- Respect plan boundaries, blocked states, dependency ordering\n'
        '- Update implementation records after meaningful work\n'
        '\n'
        '### 5. Recovery\n'
        'On session restart or ambiguous state:\n'
        '- Query conduit-mcp pipeline state and .agents/ artifacts first\n'
        '- Assume work may already be partially complete\n'
        '- Prefer reconciling with durable state over conversational memory',
        ARRAY['governance', 'workrequest', 'participation', 'pattern'],
        ARRAY['work request', 'how to work', 'participation pattern', 'WR pattern'],
        ARRAY['conduit-mcp_query_conduit_state']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'analyst-ii', 'architect', 'builder', 'critic', 'devops', 'engineer', 'engineer-ii', 'inspector', 'lead-engineer', 'planner', 'reviewer', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 22. Day/Night Turn Boundary
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'day-night-boundary',
        'Day/Night Turn Boundary',
        'Perceptual cycle: Day (evidence accumulation within a turn) vs Night (reconciliation between sessions).',
        '## Day/Night Turn Boundary\n'
        '\n'
        'Sessions follow a perceptual cycle:\n'
        '\n'
        '### Day (within a turn)\n'
        '- Evidence accumulation\n'
        '- Messages arrive, inbox is queried, work is done, records are written\n'
        '- No full perceptual recalculation\n'
        '- Each turn appends to the timeline without reconciling the entire belief state\n'
        '\n'
        '### Night (between sessions / on explicit reflection)\n'
        '- Accumulated records are reconciled\n'
        '- Stale threads are resolved or archived\n'
        '- Divergences that accumulated during the day are evaluated\n'
        '- Projections are regenerated\n'
        '- The belief state is recomputed\n'
        '\n'
        '### Triggers for Night mode\n'
        '- Session end (user disconnects)\n'
        '- Explicit type:reconciliation request\n'
        '- Scheduler-driven reflection cycle (future)\n'
        '\n'
        '### Constraint\n'
        'During Day, agents MUST NOT require full perceptual recalculation to respond. The inbox query is the attention filter \\u2014 it answers "what needs my attention right now?" without resolving the entire epistemic state.',
        ARRAY['operational-model', 'day-night', 'perceptual-cycle', 'reconciliation'],
        ARRAY['day night', 'turn boundary', 'perceptual cycle', 'reconciliation'],
        '{}'
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'analyst-ii', 'architect', 'auditor', 'builder', 'critic', 'DBA', 'design-synthesist', 'devops', 'engineer', 'engineer-ii', 'epistemologist', 'inspector', 'layout-mechanic', 'lead-engineer', 'ontologist', 'planner', 'reviewer', 'sound-technician', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 23. Role Governance & Epistemic Constraints
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'role-governance',
        'Role Governance & Epistemic Constraints',
        'Roundtable of epistemic agents: no single role closes decisions in another''s domain.',
        '## Role Governance\n'
        '\n'
        'Roles form a **roundtable of epistemic agents** with competing claims. No single role may unilaterally close a decision in another''s domain.\n'
        '\n'
        '### Invariants\n'
        '\n'
        '**I1 \\u2014 No single layer dominates.**\n'
        'A Planner cannot override an Architecture decision without a thread. An Engineer cannot unilaterally close a Reviewer rejection.\n'
        '\n'
        '**I2 \\u2014 Origin gating.** Each role owns its domain''s binding output:\n'
        '\n'
        '| Domain | Binding Output | Owning Role |\n'
        '|--------|---------------|-------------|\n'
        '| Architecture decisions | type:decision, recordType: decision | Architect |\n'
        '| Implementation work | type:change, recordType: report | Builder/Engineer |\n'
        '| Review judgement | type:approval / type:rejection | Reviewer |\n'
        '| Plan proposals | type:proposal, recordType: assessment | Planner |\n'
        '| Issue triage | type:triage, recordType: analysis | Analyst |\n'
        '| Compliance violations | type:violation, recordType: inspection | Inspector |\n'
        '\n'
        'A role may propose candidates in any domain (via type:finding, type:warning) but only the owning role emits the binding type:decision or type:approval.\n'
        '\n'
        '**I3 \\u2014 Divergence is signal, not noise.**\n'
        'Conflicting assessments must be preserved as visible records \\u2014 never silently collapsed. Resolution happens through explicit threads.\n'
        '\n'
        '**I4 \\u2014 Read-only provenance records.**\n'
        'recordType: response and recordType: prompt are immutable history. Archivist records are append-only. These must never be updated, only created.\n'
        '\n'
        '### Divergence Tags\n'
        '- type:disagreement \\u2014 Explicit conflicting position\n'
        '- type:escalation \\u2014 Request for owning role to resolve\n'
        '- type:deferred \\u2014 Known conflict tabled for later',
        ARRAY['governance', 'role', 'epistemic', 'constraints'],
        ARRAY['governance', 'role rules', 'epistemic', 'who decides'],
        '{}'
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'analyst-ii', 'architect', 'auditor', 'builder', 'critic', 'DBA', 'design-synthesist', 'devops', 'engineer', 'engineer-ii', 'epistemologist', 'inspector', 'layout-mechanic', 'lead-engineer', 'ontologist', 'planner', 'reviewer', 'sound-technician', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 24. Per-Role Outbox Table
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'per-role-outbox-table',
        'Per-Role Outbox Table',
        'Reference: what each role sends, to whom, and when.',
        '## Per-Role Outbox Table\n'
        '\n'
        '| Role | record_type | Tags | To | When |\n'
        '|------|------------|------|----|------|\n'
        '| **Planner** | prompt | type:plan | Architect | Plan needs architecture spec |\n'
        '| | prompt | type:plan | Engineer | Plan ready to implement |\n'
        '| | assessment | type:proposal | All | New work proposal |\n'
        '| | report | type:db-change | DBA | Plan needs DB change — DBA posts to Drafts forum, applies after admin approval, before builder |\n'
        '| | prompt | type:question | Analyst | Needs analysis |\n'
        '| **Architect** | decision | type:decision | Engineer | Arch decision to implement |\n'
        '| | architecture_note | type:spec_ref | Engineer | Reference spec produced |\n'
        '| | engineering_log | type:incident | Engineer | Bug/fix needed |\n'
        '| | engineering_log | type:task | Engineer | Small task |\n'
        '| | assessment | type:review | Planner | Arch review of a plan |\n'
        '| | engineering_log | type:question | Planner | Design clarification |\n'
        '| **Engineer** | engineering_log | type:task | Self | Personal backlog |\n'
        '| | engineering_log | type:question | Architect | Design question |\n'
        '| | engineering_log | type:blocker | Planner | Blocked, needs decision |\n'
        '| | report | type:implementation | Reviewer | Ready for review |\n'
        '| | analysis | type:finding | Architect | Discovered during work |\n'
        '| **Builder** | report | type:change | Reviewer | Implementation complete |\n'
        '| | engineering_log | type:blocker | Planner | Blocked on build |\n'
        '| **Reviewer** | assessment | type:approval | Archive | Approved \\u2014 done |\n'
        '| | assessment | type:rejection | Engineer | Needs fixes |\n'
        '| | inspection | type:issue | Engineer | Issue found |\n'
        '| **Analyst** | analysis | type:gap | Planner | Gap analysis |\n'
        '| | analysis | type:triage | Architect | Triaged issue |\n'
        '| | analysis | type:recommendation | Engineer | Suggestion |\n'
        '| **Critic** | inspection | type:warning | Analyst | Warning, triage first |\n'
        '| **Inspector** | inspection | type:error | Analyst | Error, triage |\n'
        '| | inspection | type:violation | Planner | Compliance violation |\n'
        '| **Archivist** | report | type:history | All | Read-only historical record |',
        ARRAY['reference', 'messaging', 'outbox', 'routing'],
        ARRAY['outbox', 'who sends what', 'role messages', 'message routing'],
        '{}'
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'analyst-ii', 'architect', 'auditor', 'builder', 'critic', 'DBA', 'design-synthesist', 'devops', 'engineer', 'engineer-ii', 'epistemologist', 'inspector', 'layout-mechanic', 'lead-engineer', 'ontologist', 'planner', 'reviewer', 'sound-technician', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 25. Agent Config Frontmatter Template
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'agent-config-template',
        'Agent Config Frontmatter Template',
        'Frontmatter template for .opencode/agents/ role definition files.',
        '## Agent Config Role Definition\n'
        '\n'
        'Each agent role .md file (in .opencode/agents/) MUST include a message block in its frontmatter:\n'
        '\n'
        '\`\`\`yaml\n'
        '---\n'
        'assumes_role: <role>\n'
        'message:\n'
        '  inbox_query:\n'
        '    - tags contain "to:<role>"\n'
        '    - tags contain "status:open"\n'
        '  record_types: [list of valid record types for this role]\n'
        '  auto_present: true\n'
        '  enrich_context: true\n'
        '---\n'
        '\`\`\`\n'
        '\n'
        '### Fields\n'
        '- assumes_role: The role this agent config activates (engineer, architect, planner, etc.)\n'
        '- inbox_query: Tag filters for inbox querying\n'
        '- record_types: Valid agent record types this role may write\n'
        '- auto_present: Whether to surface inbox items on every turn start\n'
        '- enrich_context: Whether to load linked system/subsystem/plan data on boot\n'
        '\n'
        '### Valid record_type values\n'
        'report, analysis, assessment, inspection, prompt, response, engineering_log, architecture_note, decision',
        ARRAY['reference', 'config', 'frontmatter', 'agent-definition'],
        ARRAY['agent config', 'frontmatter', 'role definition', '.opencode/agents'],
        '{}'
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'analyst-ii', 'architect', 'builder', 'critic', 'devops', 'engineer', 'engineer-ii', 'inspector', 'lead-engineer', 'planner', 'reviewer', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 26. Planner: Create & Manage Plans
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'planner-create-plan',
        'Planner: Create & Manage Plans',
        'How to create, update, and promote implementation plans (via nebula_create_plan), and route DB-change plans to the Engineer before a Builder starts.',
        '## Creating & Managing Plans\n'
        '\n'
        '### Create a Plan (ready for implementation)\n'
        'Use \`nebula_create_plan\` (nebula-mcp) with title, project, goal, filesAffected, acceptanceCriteria, and dependencies. When you scope or schedule work from an Assembly to-do thread, advance its parent-post status: 1 Specified once scope is pinned, 2 Planned when picked up/scheduled (see card \`thread-status-ratings\`). conduit-mcp create_plan / create_proposed_plan are REMOVED stubs (TOOL_NOT_FOUND) — do not call them. The plan lands in nebula.implementation_plans (status pending) and conduit-mcp auto-bootstraps a PLAN_CREATE receipt + builder ticket within ~30s.\n'
        '\n'
        '### Proposed / Planning states\n'
        'There is no create_proposed_plan tool. Start ideas as a full plan via nebula_create_plan; use conduit-mcp_revise_plan to create a revision copy for planning discussion. Use conduit-mcp_update_plan / report_plan_metadata to set filesAffected, acceptanceCriteria, dependencies.\n'
        '\n'
        '### ⚠ DB-Change Routing (mandatory rule)\n'
        '**Plans that require database changes go to the DBA for the DB work BEFORE a Builder starts implementation.** When creating or updating a plan whose goal, filesAffected, or acceptance criteria involve schema changes, migrations, DDL, seed/data backfills, or index changes:\n'
        '1. Write a nebula agent record tagged \`["to:dba", "type:db-change", "planRef:<N>", "status:open"]\`    describing exactly which database changes are required (tables, columns,    migrations, data). Use recordType report.\n'
        '2. Put the DB change as the FIRST acceptance criterion of the plan so the builder    knows the schema must exist before implementation.\n'
        '3. The DBA posts the proposed alterations to the Assembly Drafts forum\n'
        '    (slug \`draft\`) and applies them ONLY after admin approval. The Builder must\n'
        '    not start implementation until the DBA completes the DB change (approval +\n'
        '    application) and the plan is still pending/ready. If a builder ticket is\n'
        '    already open for a DB-change plan, escalate via \`type:escalation\` to keep\n'
        '    sequencing.\n'
        '\n'
        '### Update Metadata\n'
        'Use \`conduit-mcp_update_plan\` or \`conduit-mcp_report_plan_metadata\` to set filesAffected, acceptanceCriteria, dependencies.\n'
        '\n'
        '### Revise a Plan\n'
        'Use \`conduit-mcp_revise_plan\` to create a revision copy (issues PLANNING on the new copy).\n'
        '\n'
        '### Issue Receipts (state transitions)\n'
        'Use \`conduit-mcp_issue_receipt\` with plan_id, type (PLAN_CREATE|IMPLEMENTATION|REVIEW_PASS|REVIEW_REJECT|BLOCK|PLANNING|HOLD|CANCELLED), and agent_role.\n'
        '\n'
        '### Delete a Plan\n'
        'Use \`conduit-mcp_delete_plan\` for soft-delete (preserves audit trail). Use \`conduit-mcp_hard_delete_plan\` (with title confirmation) for permanent removal.',
        ARRAY['planner', 'plans', 'create', 'manage', 'workflow', 'db-change'],
        ARRAY['create plan', 'new plan', 'propose plan', 'promote plan', 'delete plan', 'database change', 'schema change', 'migration'],
        ARRAY['nebula_create_plan', 'conduit-mcp_update_plan', 'conduit-mcp_revise_plan', 'conduit-mcp_issue_receipt', 'conduit-mcp_delete_plan', 'conduit-mcp_hard_delete_plan']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['devops', 'engineer', 'engineer-ii', 'lead-engineer', 'planner', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 27. Implementation Plan Template
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'plan-template-format',
        'Implementation Plan Template',
        'Required sections for every implementation plan: Goal, Files, AC, Dependencies.',
        '## Implementation Plan Format\n'
        '\n'
        'Every plan written to pending/ must include these sections:\n'
        '\n'
        '\`\`\`markdown\n'
        '## Goal\n'
        '<what this plan achieves>\n'
        '\n'
        '## Files Affected\n'
        '<absolute paths to every file that will be created or modified>\n'
        '\n'
        '## Acceptance Criteria\n'
        '<how to verify the plan was implemented successfully — specific commands, outputs, or observable states>\n'
        '\n'
        '## Dependencies\n'
        '<other plan names this one depends on, or "none">\n'
        '\`\`\`',
        ARRAY['reference', 'template', 'plan-format'],
        ARRAY['plan template', 'plan format', 'acceptance criteria', 'files affected'],
        '{}'
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['builder', 'devops', 'engineer', 'engineer-ii', 'lead-engineer', 'planner', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 28. Builder: Implementation Workflow
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'builder-workflow',
        'Builder: Implementation Workflow',
        'How the Builder claims tickets, picks up pending plans, implements them, and handles blockers.',
        '## Builder Workflow\n'
        '\n'
        '### 1. Query Pipeline State\n'
        'Use \`conduit-mcp_query_conduit_state\` to find pending plans. Check for blocked plans first — if any exist, stop and alert.\n'
        '\n'
        '### 2. Claim the ticket (session-based)\n'
        'Use \`conduit-mcp_claim_ticket\` with \`plan_id\`, \`role=builder\`, and your \`session_id\` (from the timeclock/boot shim). Same-session re-claim is an idempotent refresh; a fresh claim held by another session is REFUSED with holder details (\`force=true\` takes over, transition-audited). Claims release automatically when the holder session ends; \`conduit-mcp_release_ticket\` releases early. Until you claim, the ticket is unreserved — claim before implementing.\n'
        '\n'
        '### 3. Read Plan Details\n'
        'Use \`conduit-mcp_get_plan_receipts\` to review the receipt chain and confirm lifecycle state. The implementation spec is the plan''s \`goal\` + \`acceptanceCriteria\` (via \`query_conduit_state\` or \`GET :3101/api/plans\`); there is **no \`.md\` file** for API-created plans (\`fileName\` is empty). Cross-reference \`filesAffected\` from the DB row if \`/state\` omits it. If the plan''s ACs embed start conditions (e.g., "waits on ST.01"), verify they are met before implementing — several backlog plans are open but not startable by their own ACs.\n'
        '\n'
        '### 4. Implement\n'
        'Modify code according to the plan goal, files affected, and acceptance criteria. Use \`conduit-mcp_agent_heartbeat\` to report liveness.\n'
        '\n'
        '### 5. Handle Blockers\n'
        'If implementation cannot proceed: \`conduit-mcp_issue_receipt\` with type BLOCK. Report the issue to the user.\n'
        '\n'
        '### 6. Report Completion (receipt-driven)\n'
        '\`conduit-mcp_agent_finished\` is a status marker only — it does **not** advance the pipeline. The pipeline advances via receipts: \`conduit-mcp_issue_receipt\` with \`type=IMPLEMENTATION\`, \`agent_role=builder\`, and \`artifact_path\` pointing at your deliverable (agent record id, PR, or thread). \`advanceTicketsOnReceipt\` then closes the builder ticket and spawns the reviewer ticket. Verify: \`get_plan_receipts\` shows IMPLEMENTATION; \`/state\` shows derived=IMPLEMENTATION; reviewer ticket open.\n'
        '\n'
        '### Continuous Execution Rule\n'
        'The Builder works through all available plans without pausing. Only stops on: true blocker, logical impossibility, or user interrupt. Does NOT ask for approval between plans.\n'
        '\n'
        '### Tool notes (2026-09-22)\n'
        'Removed tools: \`create_proposed_plan\`, \`promote_plan\`. Live and absent from older cards: the \`runtime_*\` WorkRequest family (\`runtime_list_work_requests\`, \`runtime_get_work_request\`, \`runtime_get_work_request_events\`, \`runtime_transition\`, \`runtime_tick\`, \`runtime_submit_work_request\`), plus \`validate_implementation_plan\`, \`issue_compile_verdict\`, \`run_compile_gate\`, \`bootstrap_unclaimed_plans\`. See the \`conduit-mcp-tools\` card.',
        ARRAY['builder', 'workflow', 'implementation', 'plans'],
        ARRAY['builder workflow', 'implement plan', 'pending plans', 'claim ticket', 'blocker'],
        ARRAY['conduit-mcp_query_conduit_state', 'conduit-mcp_get_plan_receipts', 'conduit-mcp_claim_ticket', 'conduit-mcp_release_ticket', 'conduit-mcp_agent_heartbeat', 'conduit-mcp_issue_receipt']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['builder', 'devops', 'engineer', 'engineer-ii', 'lead-engineer', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 29. Verification & Build Commands
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'verification-commands',
        'Verification & Build Commands',
        'Build, typecheck, and test commands for the nexus workspace.',
        '## Verification Commands\n'
        '\n'
        '### MCP Server\n'
        '\`\`\`bash\n'
        'cd nexus/typescript/conduit-mcp && npx tsc --noEmit\n'
        'cd nexus/typescript/conduit-mcp && npx vitest run\n'
        '\`\`\`\n'
        '\n'
        '### Backend (LOSM)\n'
        '\`\`\`bash\n'
        'cd nexus/python/ai/losm && source .venv/bin/activate && pytest\n'
        '\`\`\`\n'
        '\n'
        '### UI (React)\n'
        '\`\`\`bash\n'
        'cd nexus-ui/nexus-plurality-ui && npx tsc --noEmit\n'
        'cd nexus-ui/nexus-plurality-ui && npm run build\n'
        '\`\`\`\n'
        '\n'
        '### Conduit UI (Angular)\n'
        '\`\`\`bash\n'
        'cd nexus/angular/conduit-ui && npx ng build\n'
        '\`\`\`\n'
        '\n'
        '### Chat Server\n'
        '\`\`\`bash\n'
        'cd nexus/python/conduit && python3 agent_chat.py\n'
        '\`\`\`',
        ARRAY['reference', 'commands', 'build', 'test', 'verification'],
        ARRAY['build', 'test', 'typecheck', 'verify', 'tsc', 'vitest', 'pytest'],
        '{}'
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['builder', 'devops', 'engineer', 'engineer-ii', 'lead-engineer', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 30. MCP Server & Chat Configuration
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'mcp-server-config',
        'MCP Server & Chat Configuration',
        'Conduit-mcp server, chat server, health check, and orphan scan details.',
        '## MCP Server Configuration\n'
        '\n'
        '### Conduit-mcp (port 3100)\n'
        '- Pipeline orchestration: state machine, receipts, tickets\n'
        '- All plan creation/promotion/state queries go through MCP tools\n'
        '- Never write .md files directly to nexus/graph/IMPLEMENTATION_PLANS/\n'
        '- \`create_plan\` is DEPRECATED — create plans via \`nebula_create_plan\`\n'
        '  (rover-mcp/nebula-mcp); conduit issues receipts + tickets automatically\n'
        '- Read-only state: \`query_conduit_state\` (full pipeline state,\n'
        '  \`plans.blocked\` for jams), \`query_nebula_backlog\`, \`query_nebula_systems\`\n'
        '- Transport: Streamable HTTP — JSON-RPC to \`POST http://localhost:3100/\`\n'
        '  (there is NO \`POST /tools/call\` route on 3100)\n'
        '\n'
        '### Nebula-mcp / rover-mcp (nebula-srv port 3101)\n'
        '- Canonical database-first records and plans\n'
        '- \`nebula_create_plan\` — create implementation plans (auto plan numbers,\n'
        '  DB-canonical; never write .md first)\n'
        '- \`nebula_list_agent_records\`, \`nebula_get_agent_record\`,\n'
        '  \`nebula_create_agent_record\`, \`nebula_update_agent_record\`\n'
        '- \`nebula_list_harvest_candidates\`, \`nebula_list_open_questions\`,\n'
        '  \`nebula_list_cross_references\`, \`nebula_list_evidence_links\`\n'
        '- REST fallback on :3101 (\`/api/agent-records\`, \`/api/harvest-candidates\`,\n'
        '  \`/api/open-questions\`)\n'
        '\n'
        '### Knowledge-mcp (knowledge-srv port 3109)\n'
        '- Read-only knowledge graph access (stdio MCP server, global opencode\n'
        '  config; namespaced \`knowledge-mcp_*\` in opencode)\n'
        '- Tools: \`knowledge_list_entities\`, \`knowledge_get_entity\`,\n'
        '  \`knowledge_list_edges\`, \`knowledge_get_entity_relations\`,\n'
        '  \`knowledge_list_cross_references\`, \`knowledge_list_migrations\`,\n'
        '  \`knowledge_graph_summary\`, \`knowledge_semantic_search\`\n'
        '- Semantic search covers 4 embed layers (kg / harvest / observation /\n'
        '  agent) via pgvector + Ollama (nomic-embed-text)\n'
        '- See \`knowledge-mcp-tools\` and \`investigation-resources\` cards for usage\n'
        '\n'
        '### Chat Server (port 3101)\n'
        '- Python: nexus/python/conduit/agent_chat.py\n'
        '- MCP server proxies /chat routes:\n'
        '  - GET /chat/config — available agent roles\n'
        '  - POST /chat/send — send message to an agent\n'
        '  - GET /chat/sessions — active sessions\n'
        '- Supports @planner, @builder, @reviewer, @critic notation\n'
        '- Spawns opencode run --agent <role> as background process\n'
        '- Streams output via SSE: /chat/stream/<id>\n'
        '\n'
        '### Health Check\n'
        '- GET /health returns server status, PID, pipeline state\n'
        '- OrphanScan section: detects soft-deleted plans with stale .md files, and filesystem artifacts with no DB row\n'
        '',
        ARRAY['reference', 'config', 'server', 'mcp', 'chat'],
        ARRAY['mcp server', 'chat server', 'health check', 'port 3100', 'port 3101'],
        '{}'
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['architect', 'builder', 'devops', 'engineer', 'engineer-ii', 'lead-engineer', 'planner', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 31. Role-Lease Orientation (Plan 1286)
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'role-lease-orientation',
        'Role-Lease Orientation (Plan 1286)',
        'Read your active role lease, consume bounded units from the READY pool via the canonical POST /consume endpoint (unified accounting across all three channels), rely on auto-exhaustion (revoke + agent record), and respect the scheduler emptiness check + T16 runaway guardrail. Run wr-conf-002 to verify.',
        '## Procedure\n'
        '\n'
        'At the start of every turn, before processing the user''s request:\n'
        '\n'
        '1. **Check for an active role lease:**\n'
        '   - Call \`role_lease_status\` (nebula-mcp) — filter for your role.\n'
        '   - If no ACTIVE lease exists, you are NOT authorized to consume work from the READY pool.\n'
        '   - The lease carries a time window and optional unit budget.\n'
        '\n'
        '2. **Read the lease terms:**\n'
        '   - \`window_end\`: the absolute deadline — you MUST stop consuming work before this time.\n'
        '   - \`budget_units\`: max units you may consume (NULL = unlimited).\n'
        '   - \`consumed_units\`: how many you have already consumed.\n'
        '   - \`channel\`: "interactive" (Freebuff), "opencode" (CLI), "ollama", "unknown".\n'
        '\n'
        '3. **Consume bounded units from the READY pool:**\n'
        '   - Call \`role_lease_status\` at turn start to confirm remaining budget.\n'
        '   - If \`budget_units IS NOT NULL AND consumed_units >= budget_units\`, the lease is exhausted — stop consuming, surface to user.\n'
        '   - If \`NOW() > window_end\`, the lease has expired — surface to user, ask about renewal.\n'
        '   - **After each completed work item:** call \`POST /api/role-leases/consume\` with \`{"role":"<your_role>"}\` to increment consumed_units.\n'
        '     The endpoint returns \`{"ok":true,"consumed":N,"budget":M,"exhausted":bool}\` — check \`exhausted\` to confirm remaining budget.\n'
        '\n'
        '4. **Exhaustion is automatic — the endpoint handles it (1285 remediation):**\n'
        '   - When \`consumed_units >= budget_units\`, the consume endpoint:\n'
        '     a. Auto-revokes the lease (\`status → RELEASED\`).\n'
        '     b. Emits a \`type:lease-exhausted\` agent record (visible in architect/engineer inbox).\n'
        '   - You do NOT need to manually check for exhaustion — the response includes \`exhausted: true\`.\n'
        '   - If exhausted, surface to the user and stop consuming. A new lease must be issued to resume.\n'
        '\n'
        '5. **Renewal is an explicit decision:**\n'
        '   - If the window or budget is running out but work remains, ask the user whether to renew.\n'
        '   - Call \`role_lease_renew\` with a new window_end and/or budget_units extension.\n'
        '   - Renewal auto-expires a stale ACTIVE lease before creating a new one.\n'
        '\n'
        '6. **Revoke on completion or session end:**\n'
        '   - Call \`role_lease_revoke\` when you are done consuming work.\n'
        '   - This frees the role so another session can acquire it.\n'
        '\n'
        '7. **Lease is NOT ownership — unclaimed work returns to READY on expiry.**\n'
        '   - The pipeline-health sweep detects stale leases (check #5) and surfaces them as findings.\n'
        '   - Handoff to scheduled OpenCode runs is a non-event because work lives in the DB.\n'
        '\n'
        '## Three-Channel Accounting (plan 1286)\n'
        '\n'
        'All execution channels hit the same canonical endpoint:\n'
        '\n'
        '\`\`\`\n'
        'POST /api/role-leases/consume  {"role":"<role>"}\n'
        '\`\`\`\n'
        '\n'
        '| Channel | Integration Point |\n'
        '|---|---|\n'
        '| execution_worker.py | \`urllib.request\` POST after plan-backed success (5s timeout, with-block) |\n'
        '| harness-srv (Ollama) | \`fetch\` POST after \`/api/generate\` response (5s AbortController) |\n'
        '| harness-srv (OpenCode) | \`fetch\` POST after spawn close (5s AbortController) |\n'
        '| Interactive (Freebuff) | Manual \`curl\` POST after each completed work item |\n'
        '\n'
        'One endpoint, one implementation — no inline SQL in three places.\n'
        '\n'
        '## Emptiness Check (1285 remediation slice 1)\n'
        '\n'
        'The scheduler (\`agent_scheduler_runner.py\`) now checks eligibility before launching:\n'
        '- \`_has_eligible_work(role)\` is called BEFORE \`launch_agent()\`.\n'
        '- Builder: checks \`execution.requests\` READY count > 0.\n'
        '- Reviewer: checks \`vision.tickets\` open reviewer count > 0.\n'
        '- Logs \`skip (role=X, eligible=0)\` and increments \`skipped_empty\` in the summary.\n'
        '- This prevents the runaway-reviewer incident (e6d854da) where reviewer launched with 0 plans.\n'
        '\n'
        '## T16 Runaway Guardrail (1285 remediation slice 2)\n'
        '\n'
        'harness-srv runs a watchdog loop (60s interval, 15min threshold):\n'
        '- Tracks active sessions with jobId, role, model, startedAt, promptFile, **pid**.\n'
        '- Checks \`nebula.agent_records\` for durable output since launch.\n'
        '- On detection of an idle session (>15min, no output):\n'
        '  1. \`process.kill(pid, ''SIGTERM'')\` — direct PID, not \`pkill -f\`.\n'
        '  2. Unloads Ollama model via \`POST /api/generate {keep_alive: 0}\`.\n'
        '  3. Emits \`type:runaway-detected\` agent record.\n'
        '- \`GET /sessions\` on harness-srv (:3420) shows active session list.\n'
        '\n'
        '**Spawn refactor:** \`executeOpencode\` uses \`child_process.spawn\` (not \`execFile\`)\n'
        'so the child PID is captured for direct SIGTERM. Timeout: SIGTERM → 5s grace → SIGKILL.\n'
        '\n'
        '## Conformance Test (wr-conf-002)\n'
        '\n'
        'Deterministic, LLM-free integration test — 16 tests, 6 ACs:\n'
        '\n'
        '\`\`\`bash\n'
        'cd /home/codex/dev/nexus\n'
        'python3 -m pytest python/nexus_core/wrp/tests/test_conformance_role_leases.py -v\n'
        '\`\`\`\n'
        '\n'
        '| AC | Coverage |\n'
        '|---|---|\n'
        '| AC1 | Lease issue + status query (POST /issue, GET /role-leases, 409 on dup) |\n'
        '| AC2 | Three-channel consumption (single, triple, 404 on no-lease) |\n'
        '| AC3 | Exhaustion hook (exhausted=true, auto-revoke, agent record, multi-unit) |\n'
        '| AC4 | Scheduler emptiness check (builder READY>0, reviewer open=0) |\n'
        '| AC5 | Harness-srv session tracking (GET /sessions, health check) |\n'
        '| AC6 | Pipeline-health sweep #5 (/stale for expired-window leases) |\n'
        '\n'
        '## Lease Lifecycle\n'
        '\`\`\`\n'
        'issue → ACTIVE (one per role)\n'
        '  ├─ window_end passes → stale (sweep detects)\n'
        '  ├─ consume → consumed_units++ (unified POST /consume)\n'
        '  │   └─ budget exhausted → auto-revoke + type:lease-exhausted record\n'
        '  ├─ renew → extended window/budget (resets stale check)\n'
        '  └─ revoke → RELEASED (voluntary release)\n'
        '\`\`\`\n'
        '\n'
        '## INTERACTIVE Channel (Freebuff-Hosted Roles)\n'
        '\n'
        'Roles that run inside the Freebuff interactive session are never launched by harness-srv\n'
        'or the scheduler. They are represented in \`tackle.config_bundle\` with:\n'
        '\n'
        '- \`invocation_mode = ''INTERACTIVE''\`\n'
        '- \`harness_id = ''harn-freebuff''\` — a harness with \`binary: null\`, \`execution.mode: hosted\`, \`host: freebuff\`\n'
        '- \`model_id\` still resolves for lease accounting, but no launch path may spawn it\n'
        '\n'
        '### Guards\n'
        '\n'
        '**harness-srv \`/run\`:** HTTP 400 refuses any role whose resolved config_bundle has \`invocation_mode = ''INTERACTIVE''\`:\n'
        '\`\`\`\n'
        'error: "role <role> is INTERACTIVE-hosted (Freebuff) — cannot be launched via harness-srv; run it in the Freebuff interactive session instead"\n'
        '\`\`\`\n'
        '**Scheduler:** \`agent_scheduler_runner.py\` calls \`_is_interactive_hosted(role)\`; if true,\n'
        'logs \`skip (role=X, interactive-hosted)\` and increments \`skipped_interactive\` in the\n'
        'tick summary. The scheduler never launches an INTERACTIVE-hosted role.\n'
        '\n'
        '### Real Task\n'
        '\n'
        'The \`leased-builder\` role has a real dispatchable task:\n'
        '\n'
        '\`\`\`\n'
        'tackle.tasks: role=leased-builder, task_slug=implement-change, scope="Implement the approved change under an active role lease (bounded consumption)"\n'
        'wind.tasks:   id=...0005, name="Implement Change (Leased)" → links to the tackle task\n'
        '\`\`\`\n'
        '\`resolve-context\` on this wind task returns \`role=leased-builder, harness_id=harn-freebuff\`\n'
        'with the full leased-builder persona prompt (5561 chars). The interactive session resolves\n'
        'the context, picks up the work, and executes it under the bounded role lease.\n'
        '\n'
        '### Conformance (wr-conf-005)\n'
        '\n'
        '7 tests asserting the INTERACTIVE guard (commit 235b8c3):\n'
        '\n'
        '\`\`\`bash\n'
        'cd /home/codex/dev/nexus\n'
        'python3 -m pytest python/nexus_core/wrp/tests/test_conformance_interactive_guard.py -v\n'
        '\`\`\`\n'
        '\n'
        '| AC | Assertion |\n'
        '|---|---|\n'
        '| AC1 | leased-builder config_bundle → INTERACTIVE + harn-freebuff; resolve-context maps to freebuff harness |\n'
        '| AC2 | \`/run\` refuses with HTTP 400 and never registers a session; control wind task still resolves launchable |\n'
        '| AC3 | Scheduler shadow skips the leased-builder entry: \`skipped_interactive >= 1\`, \`launched = 0\` |\n'
        '',
        ARRAY['role-lease', 'orientation', 'plan-1286', 'bounded-work'],
        ARRAY['start of turn', 'role lease', 'lease check', 'am i leased', 'leased builder'],
        ARRAY['role_lease_status', 'role_lease_issue', 'role_lease_renew', 'role_lease_revoke']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['builder', 'devops', 'engineer', 'engineer-ii', 'lead-engineer', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 32. Investigation resources: knowledge graph, audit DB, cross-refs
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'investigation-resources',
        'Investigation resources: knowledge graph, audit DB, cross-refs',
        'Where to look when investigating "what exists / what changed / how is X linked to Y": the knowledge graph (knowledge-srv 3109), the canonical audit database (nebula agent records, 3101), and the cross-references table (nebula.cross_references).',
        '# Investigation resources: knowledge graph, audit DB, cross-refs\n'
        '\n'
        '## When to use this card\n'
        '\n'
        'You are investigating an inventory / baseline question (e.g. T01): what\n'
        'entities exist, what audit trail exists, or how is X linked to Y. Answer\n'
        'from the database-first resources below — not by scanning filesystem\n'
        'directories. Prefer the MCP tool surface over raw REST where available.\n'
        '\n'
        '## 1. Knowledge graph (knowledge-mcp → knowledge-srv :3109)\n'
        '\n'
        '\`knowledge-mcp\` (stdio MCP server, wired into the global opencode config)\n'
        'proxies read-only SQL to knowledge-srv (:3109), which serves the\n'
        '\`knowledge\` schema (graph_entities, graph_edges, graph_cross_references,\n'
        'graph_migrations). Tools (namespaced \`knowledge-mcp_*\` in opencode):\n'
        '\n'
        '- \`knowledge_graph_summary\` — entity/edge/cross-ref/migration counts.\n'
        '  **Live state: 2380 entities, 3907 edges, 0 graph cross-refs, 19\n'
        '  migrations.** Sections: work_requests (1932), plans (448), plus types,\n'
        '  gaps_and_blockers, actors, rules, architectural_observations, decisions,\n'
        '  topology, epistemic_types, state_machines, boundaries. Relation types:\n'
        '  implements (1907), derived_from (1907), depends_on (93).\n'
        '- \`knowledge_list_entities\` — list entities (filters: section, entity_type,\n'
        '  status, search, limit/offset).\n'
        '- \`knowledge_get_entity\` — one entity + full properties JSON.\n'
        '- \`knowledge_list_edges\` — edges (filters: source/target section+id,\n'
        '  relation_type).\n'
        '- \`knowledge_get_entity_relations\` — inbound + outbound relations for an\n'
        '  entity (section + entity_id).\n'
        '- \`knowledge_list_cross_references\` — graph-level cross-reference maps.\n'
        '- \`knowledge_list_migrations\` — import/embed migration history.\n'
        '- \`knowledge_semantic_search\` — **unified cosine search** across four\n'
        '  pgvector embed layers: \`kg\` (curated entities: work_requests, plans,\n'
        '  actors), \`harvest\` (harvest candidates), \`observation\` (transcripts,\n'
        '  session logs, audit docs), \`agent\` (agent records). Params: query,\n'
        '  limit, layers (array), recordTypes (agent-layer filter), minSimilarity.\n'
        '  Returns provenance labels (curated / harvested / observed / agent_record)\n'
        '  so you can cite which layer a claim came from.\n'
        '\n'
        'REST equivalents on \`http://localhost:3109\` (all GET):\n'
        '\`/knowledge/summary\`, \`/knowledge/entities\`,\n'
        '\`/knowledge/entities/:section/:entity_id\`,\n'
        '\`/knowledge/entities/:section/:entity_id/relations\`, \`/knowledge/edges\`,\n'
        '\`/knowledge/cross-references\`, \`/knowledge/migrations\`.\n'
        '\n'
        '## 2. Canonical audit database (nebula agent records)\n'
        '\n'
        'The database is the ONLY canonical audit trail (filesystem audit dirs are\n'
        'derived projections). Query via nebula-mcp tools (rover-mcp):\n'
        '\n'
        '- \`nebula_list_agent_records\` — filters: role, type, tag(s) (AND\n'
        '  conjunction), search, createdAfter/createdBefore (ISO 8601), level,\n'
        '  visibilityScope, planRef, limit/offset.\n'
        '- \`nebula_get_agent_record\` — full content of one record.\n'
        '- \`nebula_create_agent_record\` / \`nebula_update_agent_record\` — write path.\n'
        '\n'
        'Record types: report, analysis, assessment, inspection, prompt, response,\n'
        'engineering_log, architecture_note, decision.\n'
        'Levels: 1 (raw/operational), 2 (structured), 3 (planning/architectural),\n'
        '4 (meta/system reasoning).\n'
        'Visibility: builder, architect, planner, reviewer, all.\n'
        'Tag routing convention: to:, from:, status:, type:, threadRef (lower-kebab).\n'
        '\n'
        '## 3. Cross-references table (nebula.cross_references)\n'
        '\n'
        'The join between plans, agent records, and knowledge entities. History\n'
        'lives in nebula.cross_references_history.\n'
        '\n'
        '- \`nebula_list_cross_references\` — filter by sourceType/sourceId,\n'
        '  targetType/targetId, relType.\n'
        '- \`nebula_create_cross_reference\` / \`nebula_get_cross_reference\` /\n'
        '  \`nebula_delete_cross_reference\`.\n'
        '\n'
        'rel_type taxonomy (valid values):\n'
        '\n'
        '- wrp:depends_on, wrp:implements, wrp:tracked_by, wrp:impacts_system,\n'
        '  wrp:supersedes\n'
        '- ag:references_plan, ag:same_thread_as, ag:prompted_by, ag:spawns_plan\n'
        '- kv:sourced_from, kv:informs, kv:cross_schema, kv:name_overlap,\n'
        '  kv:description_overlap\n'
        '\n'
        'The knowledge graph also exposes its own cross-refs via\n'
        '\`knowledge_list_cross_references\` (graph_cross_references — currently 0\n'
        'links at graph level; use nebula.cross_references for the populated\n'
        'plan/record/entity joins).\n'
        '\n'
        '## 4. Evidence links (nebula evidence_links)\n'
        '\n'
        'Links between knowledge-graph entities and harvested evidence:\n'
        '\n'
        '- \`nebula_list_evidence_links\` — filters: knowledgeEntityId, harvestId,\n'
        '  candidateId, linkType, provenance, confidence range.\n'
        '- \`nebula_create_evidence_link\` / \`nebula_get_evidence_link\` /\n'
        '  \`nebula_delete_evidence_link\` / \`nebula_delete_evidence_links_by_entity\`.\n'
        '\n'
        'link_type taxonomy: supports, refines, instantiates, contradicts, supersedes,\n'
        'mentions, informs, validates.\n'
        'provenance: auto_ingestor, manual, reconciler, llm_extracted, migration.\n'
        '\n'
        '## 5. Harvest candidates & open questions (nebula)\n'
        '\n'
        '- \`nebula_list_harvest_candidates\` — candidates with status (pending /\n'
        '  promoted / useful / superseded), implementationNotes, completed flag,\n'
        '  harvestId, system/subsystem/feature links.\n'
        '- \`nebula_list_open_questions\` / \`nebula_list_question_answers\` — open\n'
        '  questions (blocking flags, answered_by) and multi-role answers.\n'
        '- \`nebula_list_harvests\` / \`nebula_get_harvest\` — harvest pipeline outputs.\n'
        '\n'
        '## Recommended investigation sequence\n'
        '\n'
        '1. \`knowledge_semantic_search\` on the topic (all layers) to find what exists.\n'
        '2. \`knowledge_graph_summary\` to orient, then \`knowledge_list_entities\` /\n'
        '   \`knowledge_get_entity\` on the relevant section(s).\n'
        '3. Expand via \`knowledge_list_edges\` / \`knowledge_get_entity_relations\`.\n'
        '4. Join to nebula: \`nebula_list_cross_references\` (plans ↔ records ↔\n'
        '   entities), \`nebula_list_evidence_links\` (support/contradiction), then\n'
        '   \`nebula_list_agent_records\` for the underlying records.\n'
        '5. Cross-check candidates/open questions if the topic maps to harvest intent.\n'
        '\n'
        '## Anti-patterns\n'
        '\n'
        '- Do not read audit/ or IMPLEMENTATION_PLANS/ markdown as operational\n'
        '  state; query the DB via nebula-mcp.\n'
        '- Do not guess rel_type / link_type strings; use the taxonomies above.\n'
        '- When a question says "who/what references X", start from\n'
        '  nebula.cross_references and expand via relations.\n'
        '- Do not claim you "used the knowledge graph" when you only listed a\n'
        '  server''s metadata — say which KG surfaces you actually queried.\n'
        '',
        ARRAY['investigation', 'knowledge-graph', 'audit', 'cross-references', 'database-first', 't01'],
        ARRAY['investigation', 'knowledge graph', 'audit database', 'cross-refs', 'what entities exist', 'what changed', 'linked to', 'baseline', 'inventory', 't01'],
        ARRAY['nebula_list_agent_records', 'nebula_get_agent_record', 'nebula_list_cross_references', 'nebula_create_cross_reference', 'nebula_get_cross_reference', 'nebula_delete_cross_reference']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'analyst-ii', 'devops', 'engineer', 'engineer-ii', 'lead-engineer', 'planner', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 33. Knowledge Graph import + embed pipeline (disk JSON → PostgreSQL)
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'knowledge-graph-pipeline',
        'Knowledge Graph import + embed pipeline (disk JSON → PostgreSQL)',
        'How to import the disk KG (graph/nexus-knowledge-graph.json) into knowledge.graph_entities, backfill asset_id, and re-embed. Use bin/import-knowledge-graph.sh — never run migrate_graph.py bare.',
        '## When to use this card\n'
        '\n'
        '- You edited graph/nexus-knowledge-graph.json and need the changes in PostgreSQL\n'
        '- Entity counts, embeddings, or knowledge_entity assets look stale/duplicated\n'
        '- Any task touching knowledge.graph_entities, graph_entity_embeddings, or the Knowledge Steward role\n'
        '\n'
        '## Canonical pipeline (4 steps)\n'
        '\n'
        '\`\`\`\n'
        'graph/nexus-knowledge-graph.json   (edit this — the disk source of truth)\n'
        '   │\n'
        '   ▼\n'
        'bin/import-knowledge-graph.sh     (ONE command: import + cleanup + backfill + embed)\n'
        '   │\n'
        '   ▼\n'
        'knowledge.graph_entities          ← migrate_graph.py (python/steward/)\n'
        'knowledge.graph_entity_embeddings ← embed-knowledge-graph.sh (bin/)\n'
        'semantics.canonical_asset         ← asset_id backfill via sql/V083__graph_entities_asset_id_backfill.sql\n'
        '\`\`\`\n'
        '\n'
        '## Usage\n'
        '\n'
        '\`\`\`bash\n'
        '# Full cycle (import + asset backfill + embed):\n'
        'nexus/bin/import-knowledge-graph.sh\n'
        '\n'
        '# Import + backfill only (embed later):\n'
        'nexus/bin/import-knowledge-graph.sh --skip-embed\n'
        '\n'
        '# Inspect only, no writes:\n'
        'nexus/bin/import-knowledge-graph.sh --dry-run\n'
        '\n'
        '# Show migration history:\n'
        'python3 python/steward/migrate_graph.py --list   # requires NEXUS_DB_DSN env\n'
        '\`\`\`\n'
        '\n'
        '## CRITICAL — do not run migrate_graph.py bare\n'
        '\n'
        '- migrate_graph.py defaults to the WRONG DSN (\`postgresql://nexus:nexus@localhost:5432/graph\`).\n'
        '  The wrapper always exports \`NEXUS_DB_DSN=postgresql://pguser:pgpass@localhost:5432/nexus\`.\n'
        '- migrate_graph.py DELETEs all graph_entities/graph_edges/graph_cross_references then re-INSERTs\n'
        '  with fresh gen_random_uuid() ids. Because nothing FKs to graph_entities, a bare re-import\n'
        '  silently: (1) leaves old \`knowledge_entity\` canonical_asset rows unreferenced (asset count\n'
        '  inflates), and (2) leaves graph_entity_embeddings rows pointing at deleted entity uuids\n'
        '  (orphans accumulate).\n'
        '\n'
        '## Invariant (must hold after every run)\n'
        '\n'
        '\`\`\`\n'
        'count(graph_entities) == count(graph_entity_embeddings)\n'
        '                       == count(canonical_asset WHERE asset_kind=''knowledge_entity'' AND expired_at IS NULL)\n'
        'AND 0 graph_entities with NULL asset_id\n'
        'AND 0 orphan embeddings\n'
        '\`\`\`\n'
        '\n'
        '## Steward ownership\n'
        '\n'
        'The Knowledge Steward role has exclusive write access to knowledge.graph_* tables.\n'
        'All other agents are read-only. The import wrapper is the sanctioned write path.',
        ARRAY['kg', 'knowledge-graph', 'embed', 'steward', 'graph_entities', 'embeddings', 'canonical_asset', 'import-knowledge-graph', 'migrate_graph'],
        ARRAY['knowledge graph', 'knowledge-graph', 'import kg', 're-embed', 'graph_entities', 'embed-knowledge-graph', 'migrate_graph', 'KG import', 'steward'],
        ARRAY['bash', 'nebula_list_agent_records']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'analyst-ii', 'architect', 'inspector', 'operator', 'planner'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 34. Search audit archives
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'operator-audit-search',
        'Search audit archives',
        'When the user asks about past completed work, change reports, prompts, or inspections, use query_archive (filters by category: completed-plans, build-logs, prompts, changes), query_prompts (search + project filter), query_inspections (status filters), or query_changes (committed, flagged, reviewed). Return pagination-aware responses and always name the original file path so the user can cross-check.',
        '# Search audit archives\n'
        '\n'
        '## When to use this card\n'
        '\n'
        'The user asks about historical artifacts — "previous work", "completed\n'
        'plans", "audit log", "change reports", "prompt history",\n'
        '"inspections", "what did we ship last week".\n'
        '\n'
        '## Procedure\n'
        '\n'
        '1. **Choose category by question shape:**\n'
        '   - Completed plans / build logs → \`query_archive\` with\n'
        '     \`{ category: "completed-plans" | "build-logs" }\`\n'
        '   - Prompts (captured prompts with lineage) → \`query_prompts\` with\n'
        '     \`{ search: "<term>", project: "<name>" }\` (both optional)\n'
        '   - Inspections (reports, errors, warnings, blockers, todos) →\n'
        '     \`query_inspections\` with \`{ status: "resolved" | "unresolved" |\n'
        '     "pending", category: "report" | "error" | "warning" | ... }\`\n'
        '   - Change reports → \`query_changes\` with\n'
        '     \`{ category: "committed" | "flagged" | "reviewed" }\`\n'
        '2. **Pagination:** all four tools accept \`{ page, pageSize }\`.\n'
        '   Default page size is 50; increase or decrease as the user requests.\n'
        '3. **Always include the file path.** Every returned entry has a\n'
        '   \`file_path\` or \`path\` field — name it in your reply so the user\n'
        '   can cross-check on disk.\n'
        '\n'
        '## Reporting shape\n'
        '\n'
        '- For a list query: report \`total results, page X/Y\`, then list\n'
        '  \`[date | title | path]\` rows from the actual payload.\n'
        '- For a single-result query: quote the entry verbatim.\n'
        '\n'
        '## Anti-patterns\n'
        '\n'
        '- Do not paraphrase an audit entry''s summary; quote it.\n'
        '- Do not omit the file path — that''s the cross-check lever.\n'
        '- Do not invent dates or titles.\n'
        '\n'
        '## MCP tools used\n'
        '\n'
        '- \`query_archive\` — search archived pipeline artifacts (category filter)\n'
        '- \`query_prompts\` — captured prompts with lineage (search, project)\n'
        '- \`query_inspections\` — inspection records (category, status, plan ref)\n'
        '- \`query_changes\` — change reports (category filter)\n'
        '',
        ARRAY['audit', 'archive', 'prompts', 'inspections', 'changes', 'operator'],
        ARRAY['previous work', 'completed plan', 'audit log', 'change report', 'prompt history', 'inspections'],
        ARRAY['query_archive', 'query_prompts', 'query_inspections', 'query_changes']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['operator'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 35. No-hallucination rule for tool data
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'operator-no-hallucination-rule',
        'No-hallucination rule for tool data',
        'Always report exactly what the tool returned. If the tool returned an error, report the error verbatim. If it returned JSON, summarize the payload structure (keys, counts) and then quote specific fields the user asked about. Never produce plan IDs, requirement IDs, statuses, or any data that did not come back in the tool response.',
        '# No-hallucination rule for tool data\n'
        '\n'
        '## When to use this card\n'
        '\n'
        'Always. This is the operator''s most important procedure card. Every\n'
        'reply that includes data must be grounded in tool output.\n'
        '\n'
        '## Rule\n'
        '\n'
        'Report exactly what the tool returned, in this order:\n'
        '\n'
        '1. **Report the structure first.** "The tool returned\n'
        '   \`{ count: 12, records: [...] }\`." Name the keys, count, and the\n'
        '   top-level shape. The user can ask follow-up questions about\n'
        '   specific fields once they trust the surface shape.\n'
        '2. **Quote the specific fields the user asked about**, verbatim from\n'
        '   the payload. Do not paraphrase values that are short enough to\n'
        '   quote (\`< 200\` chars). For longer values, summarize then offer to\n'
        '   quote in full.\n'
        '3. **Errors are facts, not failures to hide.** If the tool returned\n'
        '   \`{ error: "..." }\` or threw an exception, report the error\n'
        '   verbatim. Do not say "couldn''t find it" or "no data available" —\n'
        '   quote the error string.\n'
        '4. **Never invent data.** No plan IDs (\`#0123\`), requirement IDs\n'
        '   (\`req-456\`), WR IDs (\`wr-789\`), statuses, counts, or timestamps\n'
        '   that did not appear in the tool response. If you don''t have a\n'
        '   tool result for a field the user asked about, say so and dispatch\n'
        '   the appropriate tool.\n'
        '\n'
        '## Why this exists as a card\n'
        '\n'
        'The other roles (engineer, architect, planner, etc.) get this rule\n'
        'inlined in their system prompt. The operator was previously getting\n'
        'it from an in-prompt \`CRITICAL: You MUST use the actual data\`\n'
        'directive. Loading this card lets the operator consult the same rule\n'
        'via the procedure-card pathway at request time, consistent with how\n'
        'the other roles load cards at turn start (see AGENTS.md Role Memory\n'
        'Procedure Registry).\n'
        '\n'
        '## Anti-patterns\n'
        '\n'
        '- "I see 5 pending plans" when the tool returned 3 — never.\n'
        '- "The plan title is X" when the tool returned title Y — quote, don''t\n'
        '  paraphrase a Y into an X.\n'
        '- Omitting an error block from the response because it "looked\n'
        '  unimportant".\n'
        '- Producing a quoted plan ID that the user mentioned in an earlier\n'
        '  exchange but that did NOT appear in this turn''s tool response.\n'
        '\n'
        '## MCP tools used\n'
        '\n'
        '(none — this card governs reporting behavior, not tool selection)\n'
        '',
        ARRAY['hallucination', 'grounding', 'tool-data', 'operator', 'critical'],
        '{}',
        '{}'
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['operator'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 36. Query and report pipeline state
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'operator-pipeline-query',
        'Query and report pipeline state',
        'When the user asks about pipeline status, use query_conduit_state to fetch the full state view. Report plans by derived_status (pending/active/blocked/completed/archived/hold); circuit breaker status; builder activity; recent receipts. Do not summarize if the user is asking for a specific field — fetch it explicitly.',
        '# Query and report pipeline state\n'
        '\n'
        '## When to use this card\n'
        '\n'
        'The user asks about pipeline status — "how are plans doing", "is the\n'
        'pipeline jammed", "what is the pipeline working on right now", "circuit\n'
        'breaker", "builder activity", "any blocked plans", recent receipts.\n'
        '\n'
        '## Procedure\n'
        '\n'
        '1. **Default:** call \`query_conduit_state\` (no args). It returns the\n'
        '   full pipeline JSON. Treat this as the single source of truth for\n'
        '   pipeline state — never reconstruct plan counts from memory or\n'
        '   earlier exchanges.\n'
        '2. **Read buckets in this order and report each non-empty one:**\n'
        '   - \`plans.blocked\` — if non-empty, this is the most important finding.\n'
        '     List plan number + title for each.\n'
        '   - \`plans.active\` — in-progress work; report builder ticket status.\n'
        '   - \`plans.pending\` — queued; report count.\n'
        '   - \`plans.hold\` — parked architectural work; report count.\n'
        '   - \`plans.completed\` — usually omit unless user asks; report count only.\n'
        '   - \`plans.archived\` — omit unless user asks explicitly.\n'
        '3. **Always report** \`builder.status\` (running/idle/stale/killed) and\n'
        '   \`circuitBreaker.tripped\` (true/false). These are the two health\n'
        '   signals.\n'
        '4. **Specific field request** — if the user asked for one field ("just\n'
        '   the blocked plans", "circuit breaker status"), report *only* that\n'
        '   field. Do not dump the full state.\n'
        '5. **For a specific plan''s history:** call \`get_plan_receipts\` with\n'
        '   \`{ plan_id: "<number>" }\` and report the receipt chain.\n'
        '6. **For a list of work requests:** call \`runtime_list_work_requests\`,\n'
        '   optionally with \`{ status: "QUEUED" | "CLAIMED" | "SETTLED" | ... }\`.\n'
        '\n'
        '## Anti-patterns\n'
        '\n'
        '- Do not say "the pipeline looks healthy" without citing the bucket\n'
        '  counts and the circuit breaker status from the actual tool output.\n'
        '- Do not list plan numbers from memory — always run the tool.\n'
        '- Do not collapse \`hold\` + \`blocked\` into a single count; they have\n'
        '  different operational meanings (blocked = jammed, hold = parked).\n'
        '\n'
        '## MCP tools used\n'
        '\n'
        '- \`query_conduit_state\` — full pipeline JSON\n'
        '- \`runtime_list_work_requests\` — list WorkRequests (status filter)\n'
        '- \`get_plan_receipts\` — per-plan receipt chain\n'
        '\n'
        '## Reporting shape\n'
        '\n'
        '\`\`\`\n'
        'Pipeline: <builder.status>, breaker <tripped|closed>\n'
        'Pending: <n>, Active: <n>, Blocked: <n>, Hold: <n>, Completed: <n>\n'
        '[if blocked:] BLOCKED — plan #<n> <title>: <reason>\n'
        '\`\`\`\n'
        '',
        ARRAY['pipeline', 'conduit', 'status', 'operator'],
        ARRAY['how are plans', 'pending plans', 'pipeline status', 'what is the pipeline doing', 'circuit breaker', 'builder activity', 'blocked plans'],
        ARRAY['query_conduit_state', 'runtime_list_work_requests', 'get_plan_receipts']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['operator'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 37. Look up requirements
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'operator-requirement-lookup',
        'Look up requirements',
        'When the user asks about backlog items, requirements, or system features, use query_nebula_backlog (filters status, priority). For system hierarchy questions, use query_nebula_systems. For audit history (harvests, candidates): tackle_list_harvest_candidates with filters. Report specific rows by ID, not summary sentences.',
        '# Look up requirements\n'
        '\n'
        '## When to use this card\n'
        '\n'
        'The user asks about requirements, backlog, RMS, systems, features, or\n'
        'pending work items — "what''s in the backlog", "show me high-priority\n'
        'requirements", "what systems do we have", "is there a requirement for\n'
        'X".\n'
        '\n'
        '## Procedure\n'
        '\n'
        '1. **Backlog query:** call \`query_nebula_backlog\`. It accepts optional\n'
        '   \`{ status: "Backlog" | "InProgress" | "Done", priority: "High" |\n'
        '   "Medium" | "Low" }\`. Without filters it returns the full backlog.\n'
        '2. **System hierarchy:** call \`query_nebula_systems\` (no args). Returns\n'
        '   the full system → subsystem → feature tree. Use this when the user\n'
        '   asks "what systems do we have" or "where does feature X live".\n'
        '3. **Cross-reference a harvest candidate to a plan:** if the user\n'
        '   mentions a harvest or candidate by ID, use\n'
        '   \`tackle_list_harvest_candidates\` (filters available) and, when the\n'
        '   user wants to act on one, \`tackle_spawn_plan_from_candidate\`.\n'
        '\n'
        '## Reporting shape\n'
        '\n'
        '- Report specific rows by their actual ID — never invented IDs.\n'
        '- For backlog: list \`[reqId | status | priority | title]\` rows.\n'
        '- For systems: collapse the hierarchy into nested bullet form.\n'
        '- Always quote the count returned by the tool.\n'
        '\n'
        '## Anti-patterns\n'
        '\n'
        '- Do not synthesize a requirement description from conversation memory.\n'
        '- Do not invent IDs.\n'
        '- If the tool returns 0 rows, say so plainly.\n'
        '\n'
        '## MCP tools used\n'
        '\n'
        '- \`query_nebula_backlog\` — list requirements (status, priority filter)\n'
        '- \`query_nebula_systems\` — system hierarchy tree\n'
        '- \`tackle_list_harvest_candidates\` — harvest candidates audit listing\n'
        '- \`tackle_spawn_plan_from_candidate\` — convert candidate into a plan\n'
        '',
        ARRAY['requirements', 'backlog', 'rms', 'systems', 'operator'],
        ARRAY['requirement', 'backlog', 'what work is pending', 'rms', 'systems', 'features'],
        ARRAY['query_nebula_backlog', 'query_nebula_systems', 'tackle_list_harvest_candidates', 'tackle_spawn_plan_from_candidate']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['operator'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 38. WorkRequest lifecycle inspection
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'operator-workrequest-lifecycle',
        'WorkRequest lifecycle inspection',
        'When the user asks about work requests (WRs) by ID or status, use runtime_list_work_requests (status filter) first. For a specific WR: runtime_get_work_request for folded state, runtime_get_work_request_events for raw event log. The runtime_transition tool applies state-machine events (WR_CLAIMED/ACKED/SETTLED/REJECTED/FAILED/NOOP/DEFERRED) — only use it when the user explicitly asks for a state transition, and always confirm before issuing.',
        '# WorkRequest lifecycle inspection\n'
        '\n'
        '## When to use this card\n'
        '\n'
        'The user asks about work requests — "WR-123 status", "list queued\n'
        'WRs", "what''s the event log for WR-foo", "transition this WR", or\n'
        'mentions a WorkRequest ID.\n'
        '\n'
        '## Procedure\n'
        '\n'
        '1. **List** → \`runtime_list_work_requests\` with optional\n'
        '   \`{ status: "VALIDATED" | "QUEUED" | "CLAIMED" | "ACKED" | "SETTLED"\n'
        '   | "REJECTED" | "FAILED", limit }\`.\n'
        '2. **One WR''s folded state** → \`runtime_get_work_request\` with\n'
        '   \`{ wrId: "<id>" }\`.\n'
        '3. **One WR''s raw event log** → \`runtime_get_work_request_events\`\n'
        '   with \`{ wrId: "<id>" }\`.\n'
        '4. **Advance the pipeline by one tick** → \`runtime_tick\` (no args).\n'
        '   Use sparingly; only when the user explicitly asks "advance the\n'
        '   pipeline" or "tick".\n'
        '5. **Apply a transition** → \`runtime_transition\` with\n'
        '   \`{ wrId, type: "WR_CLAIMED" | "WR_ACKED" | "WR_SETTLED" |\n'
        '   "WR_REJECTED" | "WR_FAILED" | "WR_NOOP" | "WR_DEFERRED", payload? }\`.\n'
        '   **Confirm with the user first.** This mutates state.\n'
        '\n'
        '## Reporting shape\n'
        '\n'
        '- For a list: report \`count\` + per-WR \`[id | status | intent.objective]\`.\n'
        '- For a single WR''s folded state: quote the \`status\`, \`currentEvent\`,\n'
        '  and any included receipt summaries.\n'
        '- For an event log: report the events in chronological order with\n'
        '  \`[seq | type | timestamp]\` rows from the actual payload.\n'
        '\n'
        '## Anti-patterns\n'
        '\n'
        '- Never call \`runtime_transition\` without user confirmation.\n'
        '- Never guess a WR ID from the conversation — confirm the ID with the\n'
        '  user before any mutating call.\n'
        '- Do not collapse \`WR_NOOP\` and \`WR_DEFERRED\` — they mean different\n'
        '  things (NOOP = nothing to do; DEFERRED = intentionally parked).\n'
        '\n'
        '## MCP tools used\n'
        '\n'
        '- \`runtime_list_work_requests\`\n'
        '- \`runtime_get_work_request\`\n'
        '- \`runtime_get_work_request_events\`\n'
        '- \`runtime_tick\` (advance pipeline by one transition)\n'
        '- \`runtime_transition\` (mutating — confirm first)\n'
        '',
        ARRAY['workrequest', 'wr', 'runtime', 'lifecycle', 'operator'],
        ARRAY['work request', 'wr-', 'work-request lifecycle', 'wr status', 'transition wr'],
        ARRAY['runtime_list_work_requests', 'runtime_get_work_request', 'runtime_get_work_request_events', 'runtime_transition']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['operator'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 39. Seed Integrity — Three-Layer Guard
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'seed-integrity',
        'Seed Integrity — Three-Layer Guard',
        'The procedure-card seed (typescript/tackle-seeds/index.ts) is the single source of truth for all tackle procedure cards, regenerated from the live DB by bin/regenerate_memory_seed.py. Three independent layers keep it honest: the generator --verify + pre-commit hook (commit-time, live DB), the wr-conf-006 conformance suite (local, 21 tests), and the GitHub Actions manifest guard (daily + push/PR, no live DB needed).',
        '## Procedure\n'
        '\n'
        'The \`seedMemoryProcedures()\` template literal in \`typescript/tackle-seeds/index.ts\` is the single source of truth for all tackle procedure cards. It is regenerated FROM the live canonical DB (the \`tackle\` schema''s procedure-card table) by \`bin/regenerate_memory_seed.py\` — the DB is canonical; the seed is a derived projection. The committed \`typescript/tackle-seeds/seed-manifest.json\` is a matching full-content snapshot (card count, per-card sha256, role sets) ### ⚠️ Caution — card-body wording constraint (applies to every card)\n'
        '\n'
        'All guard layers re-point the seed''s INSERT targets at \`pg_temp\` shadow\n'
        'tables with a plain string substitution over the whole rendered SQL: the\n'
        'two exact qualified table names — the schema name, a dot, and the table\n'
        'name (\`memory\` and \`role_memory\`) — are replaced wherever they appear.\n'
        'Because card body text lives inside that SQL, **a card body must never\n'
        'spell either qualified table name as one unbroken token** — not in prose,\n'
        'not in a code fence, not in backticks. If it does, the substitution\n'
        'corrupts the card''s stored content and every byte-compare false-fails as\n'
        'though the seed had drifted. The seed''s schema-interpolation marker (the dollar-brace-\`SQL\` token\n'
        'the renderer substitutes with the schema name) is likewise reserved: a\n'
        'card body containing it literally would get the schema name spliced into\n'
        'the stored text. Refer to it only descriptively.\n'
        '\n'
        'This card itself hit that exact trap (commit \`7253675\`): its body named\n'
        'the canonical tables in qualified form, and the shadow guard flagged a\n'
        'spurious mismatch until the body was reworded. When referring to the\n'
        'canonical tables, use descriptive phrasing — e.g. "the canonical\n'
        'procedure-card table" or "the table \`memory\` in the \`tackle\` schema" —\n'
        'never the qualified token as a single run.\n'
        '\n'
        'that CI uses as its no-live-DB reference.\n'
        '\n'
        'Three layers independently guard against seed drift (hand-edits, stale regenerations, backtick/apostrophe/ARRAY corruption invisible to tsc):\n'
        '\n'
        '### Layer 1 — Generator \`--verify\` + Pre-Commit Hook (commit-time)\n'
        '\n'
        'When seed-relevant files are staged (\`typescript/tackle-seeds/\`, \`bin/regenerate_memory_seed.py\`, \`tackle-srv\`/\`tackle-mcp\` \`db.ts\`, \`.githooks/pre-commit\`), the pre-commit hook runs \`python3 bin/regenerate_memory_seed.py --verify\`:\n'
        '\n'
        '1. Renders the seed from source with real JS semantics (node subprocess).\n'
        '2. Executes the DO block against \`pg_temp\` shadow tables (\`LIKE the canonical memory/role-memory tables INCLUDING ALL\`).\n'
        '3. Byte-compares every seeded card (title/summary/body/tags/triggers/mcp_tools) and role set against the live canonical table.\n'
        '4. Also verifies the committed \`seed-manifest.json\` matches live (recomputes expected manifest, byte-compares).\n'
        '5. If the seed had drifted, the generator rewrites the worktree copy — the hook BLOCKS the commit with a re-stage message.\n'
        '\n'
        'If \`node\` or the live DB is unreachable, the hook skips with a warning (safety net, not a hard environment requirement).\n'
        '\n'
        '\`\`\`\n'
        '# Manual verify (same command the hook runs):\n'
        'python3 bin/regenerate_memory_seed.py --verify\n'
        '\n'
        '# Regenerate seed + manifest from live DB:\n'
        'python3 bin/regenerate_memory_seed.py\n'
        '\`\`\`\n'
        '\n'
        '### Layer 2 — Conformance Suite (local, 21 tests, wr-conf-006)\n'
        '\n'
        '\`python/nexus_core/wrp/tests/test_conformance_seed_guard.py\` (wr-conf-006) runs 21 tests across five assertion classes:\n'
        '\n'
        '- **AC1** (4): Render integrity — source locatable, no stale copies in \`tackle-srv\`/\`tackle-mcp\` \`db.ts\`, node render produces executable DO block with one INSERT per live card, shadow execution clean with matching counts, built dist artifact also verified.\n'
        '- **AC2** (3): Card byte-identity — every seeded card row byte-matches the live table; none missing/extra; the 7 historically-missing operator/investigation cards present.\n'
        '- **AC3** (2): Role byte-identity — per-card role sets match the live role-memory table; no orphaned cards.\n'
        '- **AC4** (4): Escape conventions — static probes on the template body: only the SQL schema interpolation marker is allowed (the one interpolation the seed intentionally uses); no backslash-quote rendering bare quotes; no raw backticks; escaping is in use.\n'
        '- **AC5** (8): Manifest guard — committed manifest is parseable/consistent; scratch-schema execution of the rendered seed vs manifest (counts, per-card sha256, role sets); content self-consistency (stored sha256 equals sha256 of embedded content); bootstrap round-trip (apply_manifest to build_manifest equals committed manifest); manifest-vs-live (skips when no live tables).\n'
        '\n'
        '\`\`\`\n'
        '# Run the full suite (needs node + Postgres with a tackle schema):\n'
        'python3 -m pytest python/nexus_core/wrp/tests/test_conformance_seed_guard.py -v\n'
        '\n'
        '# Run only the manifest guard (works with any Postgres, no live tackle):\n'
        'python3 -m pytest python/nexus_core/wrp/tests/test_conformance_seed_guard.py -k "Ac5" -v\n'
        '\`\`\`\n'
        '\n'
        '### Layer 3 — CI Manifest Guard (automatic, daily + push/PR)\n'
        '\n'
        '\`.github/workflows/seed-guard.yml\` runs on every push/PR to \`dev\`/main/feature branches AND daily at 6 AM UTC:\n'
        '\n'
        '1. **Bootstrap**: \`bin/bootstrap_seed_manifest.py\` reads the committed \`seed-manifest.json\` and reconstructs the canonical procedure-card tables in a throwaway Postgres service container (DDL mirrors \`db.ts\` incl. btree_gist EXCLUDE constraint, all 39 cards + 212 role rows). Refuses if the target schema contains any table beyond \`memory\`/\`role_memory\` (the live-DB guard).\n'
        '2. **Guard**: \`make seed-guard-test\` runs the full 21-test suite against the bootstrapped schema — rendering the seed from source (node) and comparing it against the manifest-derived tables byte-for-byte.\n'
        '\n'
        'The CI workflow independently verifies: bootstrap + rendering + SQL execution + byte-compare — all with NO production database. The pre-commit hook + local tests cover the live-DB path.\n'
        '\n'
        '### When the Guard Blocks a Commit\n'
        '\n'
        'The pre-commit hook blocks with \`[seed-guard] COMMIT BLOCKED\` when the seed was regenerated from the live DB (it had drifted). The fresh copy is in the worktree but NOT staged:\n'
        '\n'
        '\`\`\`\n'
        '# Stage the fresh seed and manifest, then commit again:\n'
        'git add typescript/tackle-seeds/\n'
        'git commit\n'
        '\`\`\`\n'
        '\n'
        'If the block is spurious (e.g., \`node\` or the DB was down), the hook skips with a warning — the CI guard will catch any actual drift on the next push.\n'
        '\n'
        '### When to Regenerate\n'
        '\n'
        'Run \`python3 bin/regenerate_memory_seed.py\` whenever:\n'
        '\n'
        '1. A procedure card is added, removed, or edited in the live procedure-card table.\n'
        '2. A role assignment changes in the role-memory table.\n'
        '3. After a data migration that modifies card content.\n'
        '\n'
        'The command regenerates both the seed (\`index.ts\`) and the manifest (\`seed-manifest.json\`). The pre-commit hook will then auto-detect them as drifted (if staging was done before regenerating) and require a re-stage.\n'
        '\n'
        '\`\`\`\n'
        '# Regenerate everything from live DB:\n'
        'python3 bin/regenerate_memory_seed.py\n'
        '\n'
        '# Regenerate + verify:\n'
        'python3 bin/regenerate_memory_seed.py --verify\n'
        '\`\`\`',
        ARRAY['seed-integrity', 'procedure-card', 'conformance', 'guard', 'drift'],
        ARRAY['seed integrity', 'seed drift', 'regenerate seed', 'seed guard', 'wr-conf-006', 'seed-manifest'],
        '{}'
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['architect', 'builder', 'devops', 'engineer', 'engineer-ii', 'lead-engineer', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 40. Duality Interactive Polling (leased role protocol)
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'duality-interactive-polling',
        'Duality Interactive Polling (leased role protocol)',
        'Pull-based participation in Duality/Plurality sessions: poll the Assembly duality-sessions thread for new user turns, acknowledge requested work immediately, post progress reports while working, summarize the outcome, and return to the polling loop. Respect role-lease budget (consume per work item).',
        '## Purpose\n'
        '\n'
        'You are a leased role participating in an interactive Duality (or Plurality) session from\n'
        'inside the Freebuff harness. The user converses with you through the Duality UI (embedded\n'
        'in nexus-console). Your "session" is a thread in the Assembly \`duality-sessions\` forum, and\n'
        'your presence is backed by an active role lease. This card defines the full interaction\n'
        'protocol: polling, acknowledging, working, reporting, and returning to the loop.\n'
        '\n'
        '## The Polling Loop\n'
        '\n'
        'Your engagement with a Duality session is pull-based, not push-based. You must actively\n'
        'poll the session thread for new user messages:\n'
        '\n'
        '1. **Resolve the session thread.** The active watch for your role points at the thread.\n'
        '   Query the server-side lookup:\n'
        '   \`GET http://localhost:3107/api/duality/watches/active?role=<your_role>&forumSlug=duality-sessions\`\n'
        '   → returns \`{"threadId": "...", "role": "..."}\`. If a \`turn.requested\` notification was\n'
        '   delivered to your inbox / drop-queue instead, the thread ID is in that payload.\n'
        '2. **Enter the polling loop.** Every 3–5 seconds, fetch the thread:\n'
        '   \`GET http://localhost:3107/api/forums/threads/<threadId>\`\n'
        '   and compare the comment set against the last-seen set.\n'
        '3. **A new comment is a new turn** when:\n'
        '   - the comment \`role\` is \`user\` (a direct message from the operator), OR\n'
        '   - the comment addresses your role (mentions \`@<your_role>\` or is tagged to you).\n'
        '4. **Do NOT respond to your own comments** — track the IDs you have already replied to.\n'
        '   Ignore \`system\`-role comments (turn-request notifications) as conversation input.\n'
        '5. **Respond within the turn**, then continue polling. A missed turn is not lost — the\n'
        '   thread is durable state; you can always re-read and catch up.\n'
        '\n'
        '## Work-Request Protocol\n'
        '\n'
        'When the user asks you to do work (implement, analyze, design, review, etc.), follow this\n'
        'exact lifecycle so the user always knows where you are:\n'
        '\n'
        '### 1. Acknowledge (immediately)\n'
        '\n'
        'Post a response as soon as you understand the request. State:\n'
        '- what you understood the task to be,\n'
        '- the plan you intend to follow (brief),\n'
        '- any clarifying questions, IF the request is ambiguous — do not guess silently.\n'
        '\n'
        'This converts the turn from "message received" to "work claimed" in the user''s view.\n'
        '\n'
        '### 2. Progress reports (while working)\n'
        '\n'
        'While executing, post short progress updates at meaningful milestones:\n'
        '- when you start the substantive work (tools/commands you are about to run),\n'
        '- at each completed step or checkpoint,\n'
        '- if you hit a blocker, error, or decision point — describe it and what you are trying.\n'
        '\n'
        'This is conversational work: the user watches the thread live. Long silent stretches look\n'
        'like the session died. A progress message every few minutes is the norm for substantive work.\n'
        '\n'
        '### 3. Outcome summary (on completion)\n'
        '\n'
        'When the work is done, post a final response that includes:\n'
        '- what was accomplished (the actual result, not just "done"),\n'
        '- the key artifacts/records/files touched,\n'
        '- how to verify (test names, URLs, commands),\n'
        '- any follow-ups or known caveats.\n'
        '\n'
        '### 4. Return to the polling loop\n'
        '\n'
        'After posting the outcome summary, immediately resume polling the thread. The loop is your\n'
        'home state — you only leave it to work, and you always come back. Do not assume the\n'
        'conversation is over; the user may reply with follow-up questions.\n'
        '\n'
        '## Posting a Response\n'
        '\n'
        'Post comments to the session thread as your role:\n'
        '\n'
        '\`\`\`bash\n'
        'curl -s -X POST http://localhost:3107/api/forums/threads/<threadId>/comments \\\n'
        '  -H ''Content-Type: application/json'' \\\n'
        '  -d ''{"body":"<your markdown>","postedById":"<your user UUID>","role":"<your_role>","model":"<model>"}''\n'
        '\`\`\`\n'
        '\n'
        '- \`role\` MUST be your role name (e.g. \`analyst\`, \`architect\`, \`engineer\`) so the Duality UI\n'
        '  routes it to the correct panel.\n'
        '- Use \`postedById\` = your role''s Assembly user UUID (see Assembly users list).\n'
        '- Markdown is supported — use it for structure (headers, lists, code blocks, tables).\n'
        '\n'
        '## Lease Discipline\n'
        '\n'
        '- Check your lease (\`role_lease_status\`) before consuming work; respect window and budget.\n'
        '- After each completed work item, call \`POST /api/role-leases/consume\` with\n'
        '  \`{"role":"<your_role>"}\` so accounting stays accurate (see the \`role-lease-orientation\` card).\n'
        '- If the lease exhausts mid-task, finish surfacing state, then stop and tell the user.\n'
        '\n'
        '## Anti-Patterns\n'
        '\n'
        '- ❌ Polling once and assuming the conversation is over.\n'
        '- ❌ Silently starting work without acknowledging the request.\n'
        '- ❌ Long silence during multi-step work (no progress updates).\n'
        '- ❌ Posting "done" without saying what was actually done.\n'
        '- ❌ Responding to your own messages / echo-looping with the notification consumer.\n'
        '- ❌ Treating the \`@<role> — new message\` notification as a user message (it is a trigger,\n'
        '  not content — read the thread for the actual message).\n'
        '\n'
        '## Verification\n'
        '\n'
        'A correct turn cycle looks like:\n'
        '\n'
        '\`\`\`\n'
        'user:  "analyze the drift in plan 1280"\n'
        'analyst: "acknowledged — pulling plan 1280 and drift flags; will report back"   (acknowledge)\n'
        'analyst: "found 2 stale leases; checking pipeline-health sweep output…"          (progress)\n'
        'analyst: "done — summary: … verify with bin/pipeline-health-sweep.py --dry-run"  (outcome)\n'
        'user:   "thanks"\n'
        'analyst: (no response to user unless asked — returns to polling)                 (return to loop)\n'
        '\`\`\`\n'
        '',
        ARRAY['duality', 'interactive', 'polling', 'role-lease', 'protocol'],
        ARRAY['new-comment', 'turn-requested', 'duality'],
        '{}'
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'analyst-ii', 'architect', 'auditor', 'builder', 'critic', 'devops', 'engineer', 'engineer-ii', 'inspector', 'lead-engineer', 'planner', 'reviewer', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 41. Duality Execution Harness (ephemeral run protocol)
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'duality-execution-harness',
        'Duality Execution Harness (ephemeral run protocol)',
        'Ephemeral one-shot protocol for agents launched by harness-srv (3420) via POST /run (Wind task) or POST /run-direct (Duality harness backend): no polling loop, no thread of your own; nebula records are liveness (15-min watchdog, T16); stdout is the report channel (OUTCOME: <code>, CONVERSATION_CLOSED / DELEGATE markers); governance receipts are the harness''s job; lease checked at launch, consumed by subscriber per turn.',
        '## Purpose\n'
        '\n'
        'You are an execution-harness agent: an ephemeral, one-shot instance launched by\n'
        'harness-srv (port 3420) to execute a bounded work unit. You have **no persistent\n'
        'session, no polling loop, and no thread of your own**. You are invoked exactly\n'
        'once — via \`POST /run\` (Wind task) or \`POST /run-direct\` (raw prompt, the path\n'
        'the Duality \`harness\` execution backend uses) — your **stdout is your report\n'
        'channel**, and when the run ends you are gone. This card defines the full run\n'
        'protocol: reading your prompt, the liveness contract, the outcome contract, and\n'
        'reporting. It is the counterpart to \`duality-interactive-polling\`, which covers\n'
        'the pull-based Freebuff/interactive path.\n'
        '\n'
        '## How You Are Launched\n'
        '\n'
        '- **\`POST /run\` with a \`wind_task_id\`** — context is resolved by harness-srv\n'
        '  from the Wind task (input spec + acceptance criteria) merged with your role''s\n'
        '  Tackle context (role prompt + tool ACL + procedure cards, with\n'
        '  \`{{PROCEDURE_INDEX}}\` resolved).\n'
        '- **\`POST /run-direct\` with \`{ role, prompt, ... }\`** — raw prompt, no Wind\n'
        '  resolution. This is the Duality \`harness\` backend: the prompt contains a full\n'
        '  reconstruction of the Assembly session thread (up to 30 comments) plus\n'
        '  participant identities.\n'
        '- Your role, model, harness (opencode/codex), work directory, and timeout come\n'
        '  from the active tackle \`config_bundle\` (default timeout: **5 minutes**).\n'
        '\n'
        '## Your Prompt Is The Task\n'
        '\n'
        'Everything you need is in the prompt — do not go looking for a conversation:\n'
        '\n'
        '- your role instructions and the procedure-card index (procedures are injected\n'
        '  directly into the prompt),\n'
        '- the Wind task input spec + acceptance criteria, OR the assembled session\n'
        '  thread (harness backend),\n'
        '- an **Outcome Declaration** section when the task defines outcomes.\n'
        '\n'
        'The prompt is the complete turn. If context is missing, say so in your output —\n'
        'do not silently improvise scope.\n'
        '\n'
        '## Liveness Contract — records keep you alive\n'
        '\n'
        'harness-srv runs a runaway watchdog (T16 guardrail): if your job produces **no\n'
        'durable output for 15 minutes**, the process is killed and the model unloaded.\n'
        '"Durable output" is defined as **nebula agent records created by your role**\n'
        'since launch.\n'
        '\n'
        'The audit discipline is therefore also life support:\n'
        '\n'
        '1. Write a \`nebula_create_agent_record\` at the **start** of substantive work\n'
        '   (R1: what you are about to do and why).\n'
        '2. Write short progress records at meaningful milestones.\n'
        '3. Write a completion record before finishing (R2: what was done, how to\n'
        '   verify).\n'
        '\n'
        'A silent agent is a dead agent — literally.\n'
        '\n'
        '## Report Contract — your stdout is your reply\n'
        '\n'
        'What you put on stdout determines what the system does with your run:\n'
        '\n'
        '1. **Lead with the answer.** In the Duality \`harness\` backend, the first 3000\n'
        '   characters of your stdout are posted to the Assembly session thread. Use\n'
        '   markdown structure; put the substance up front.\n'
        '2. **If the task defines outcomes, end with an \`OUTCOME\` line**, exactly:\n'
        '   \`OUTCOME: <code>\` on its own line. Matching is case-insensitive and treats\n'
        '   \`_\` and \`-\` as equivalent. Use only codes from the Outcome Declaration.\n'
        '3. **In Duality thread contexts**, end with one of the conversation markers the\n'
        '   coordinator parses:\n'
        '   - \`CONVERSATION_CLOSED\` — the topic is fully resolved (closes the watch), or\n'
        '   - \`DELEGATE <role>: <instruction>\` — hand off to another agent.\n'
        '4. **Exit code** — 0 = success, non-zero = failure. For Wind tasks, harness-srv\n'
        '   maps this to governance receipts: \`PLAN_CREATE\` at start, then\n'
        '   \`IMPLEMENTATION\` + \`REVIEW_PASS\` (exit 0) / \`REVIEW_REJECT\` (exit ≠ 0).\n'
        '   **Do not issue these receipts yourself** — they are the harness''s job.\n'
        '\n'
        '## Do Not\n'
        '\n'
        '- ❌ Poll anything or wait for follow-up input — you have exactly one run.\n'
        '- ❌ Post to the Assembly thread yourself in the Duality \`harness\` backend —\n'
        '  the turn subscriber posts your stdout. You posting causes double posts.\n'
        '- ❌ Go 15 minutes without a nebula record — the watchdog kills you (T16).\n'
        '- ❌ Reply with prose where an \`OUTCOME: <code>\` line is required.\n'
        '- ❌ Treat system-role content (error reports, turn notifications) as\n'
        '  conversation input.\n'
        '- ❌ Exceed the run timeout (default 5 min) — finish and exit cleanly.\n'
        '- ❌ Echo-loop with the notification consumer or respond to your own output.\n'
        '- ❌ Assume a follow-up turn will arrive — persist anything that must survive\n'
        '  the run in records, receipts, or committed changes.\n'
        '\n'
        '## Lease Discipline\n'
        '\n'
        '- Your **role lease** is checked at launch; an expired/exhausted lease is\n'
        '  logged (hard gating is being wired in). Respect window and budget.\n'
        '- Duality \`harness\` turns: the subscriber consumes **one lease unit per turn**\n'
        '  (\`POST /api/role-leases/consume\`) — you do not consume it yourself.\n'
        '- Execution-request runs: use the **execution lease lifecycle**\n'
        '  (acquire → work → submit attempt → issue receipt). Renew before expiry;\n'
        '  release when done.\n'
        '- If the lease is exhausted mid-run, finish surfacing state in your output,\n'
        '  then exit cleanly.\n'
        '\n'
        '## Verification\n'
        '\n'
        'A correct run looks like:\n'
        '\n'
        '\`\`\`\n'
        'harness-srv: run job=… role=analyst task=drift-analysis exit=0 duration=3m\n'
        '  └─ agent records: 1 R1-before, 2 progress, 1 R2-after   (watchdog satisfied)\n'
        '  └─ stdout ends: OUTCOME: PASS                            (outcome parsed)\n'
        '  └─ receipts (wind task): PLAN_CREATE → IMPLEMENTATION → REVIEW_PASS\n'
        '  └─ duality harness: stdout[:3000] posted as session comment\n'
        '  └─ lease: 1 unit consumed by subscriber / execution receipt issued\n'
        '\`\`\`\n'
        '',
        ARRAY['duality', 'execution-harness', 'harness-srv', 'one-shot', 'role-lease', 'protocol'],
        ARRAY['run', 'run-direct', 'wind-task', 'harness', 'duality'],
        '{}'
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'analyst-ii', 'architect', 'auditor', 'builder', 'critic', 'devops', 'engineer', 'engineer-ii', 'inspector', 'lead-engineer', 'planner', 'reviewer', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 42. DevOps Operations Conventions (thread-reply, escalation, inbox)
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'devops-operations-conventions',
        'DevOps Operations Conventions (thread-reply, escalation, inbox)',
        'Mission charter + mandatory thread-reply convention, R12 escalation, and R17 inbox check for the devops role.',
        '\n'
        '## Purpose\n'
        '\n'
        'Operational conventions for the Devops role. Load when starting work or\n'
        'when reporting progress. Complements the persona (tackle.prompts\n'
        '\`devops/opencode-persona\` v3).\n'
        '\n'
        '## Mission Charter (binding, from user)\n'
        '\n'
        '1. **Containerization (LEAD):** containerize the legacy \`typescript/*-srv\`\n'
        '   services so they can run **as a group elsewhere** (warm failover standbys\n'
        '   on a second machine). Dockerfiles, compose, images, group lifecycle.\n'
        '2. **Ansible failover maintenance:** work with the other machine(s) via\n'
        '   ansible — playbooks, inventory, idempotent provisioning, standby health.\n'
        '3. **Cutover oversight (GATED):** oversee cutover to \`python/peb-kernel\` and\n'
        '   the adonisjs/moleculer stack. **DO NOT cut over until containerization is\n'
        '   tested locally** (group starts, health checks pass, failover works on the\n'
        '   local machine first). The local container test is the gate.\n'
        '\n'
        '## Thread-Reply Convention (MANDATORY)\n'
        '\n'
        '- Work specified in a forum thread (checklist/dispatch/task) → reply to\n'
        '  **that thread** with progress updates as comments.\n'
        '- Per-checklist-item progress (\`[x]\`), deliverables **by path** (never paste\n'
        '  credentials), blockers stated with what you need.\n'
        '- Post on item completion; post immediately when blocked or at a handoff\n'
        '  point (e.g. draft awaiting ratification).\n'
        '- Agent records are supplementary; the forum thread reply is the primary\\n  progress surface.\n'
        '- **Advance the parent thread status while replying** (\`statusRating\` param\n'
        '  on the comment POST, or \`PUT /api/forums/threads/:id/status\`) — see card\n'
        '  \`thread-status-ratings\`. Completion replies → 4 Accepted; blockers stay at\n'
        '  current status; reopening after regression → 6.\n'
        '- The issues forum (\`issues-and-open-questions\`) is for blockers/incidents\n'
        '  and open questions — NOT work updates or ratification requests.\n'
        '\n'
        '## Escalation Process (R12)\n'
        '\n'
        '1. Try restart/config fix first.\n'
        '2. Record \`["to:architect","type:escalation"]\` — problem, tried, state.\n'
        '3. If unresolvable and blocking: also POST to Assembly\n'
        '   \`issues-and-open-questions\` with \`role\` + \`model\` fields, using the\n'
        '   devops Assembly user UUID (assembly-srv :3107).\n'
        '4. DB changes route to **DBA** (\`["to:dba","type:db-change",...]\`); you own\n'
        '   migration mechanics, the DBA owns DDL.\n'
        '\n'
        '## End-of-Turn Inbox Check (MANDATORY, R17)\n'
        '\n'
        '- Preferred: \`nexus/bin/check-inbox.sh --role devops\` (or \`nebula_get_inbox\`\n'
        '  MCP \`{"role":"devops","limit":20}\`).\n'
        '- Fallback: nebula REST :3101 — GET pointer, query records\n'
        '  \`role=devops&createdAfter=<pointer_iso>\`, PUT pointer after surfacing.\n'
        '- Surface items to the user; do NOT silently act on inbox items.\n'
        '- If nebula-srv unreachable, surface as blocking infra issue.\n'
        '',
        ARRAY['devops', 'operations', 'conventions', 'thread-reply', 'escalation', 'inbox'],
        ARRAY['devops conventions', 'thread reply', 'escalate', 'inbox check', 'containerization', 'ansible', 'cutover'],
        ARRAY['nebula_get_inbox', 'nebula_create_agent_record', 'nebula_list_agent_records']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['devops', 'sysadmin'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 43. Parallel Role Loop (PRL) — cross-role doctrine
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'parallel-role-loop',
        'Parallel Role Loop (PRL) — cross-role doctrine',
        'Concurrent role sessions: user is NOT the message bus. Every turn = inbox check → thread sweep → act → post to thread → notify via to:<role> record → advance pointer. Work until blocker/decision, then switch tracks while awaiting.',
        '## Parallel Role Loop (PRL) — cross-role doctrine\n'
        '\n'
        '### Premise\n'
        'Multiple role sessions run concurrently (e.g., Architect in one terminal, Engineer in another, user supervising). **The user is NOT the message bus.** All cross-role communication flows through the corpus: nebula agent records (tag-routed, pointer-tracked) + Assembly forum threads (durable, timestamped, threaded). The inbox check is the attention filter that lets each role work independently.\n'
        '\n'
        '### The loop (every role, every turn)\n'
        '1. **Inbox check FIRST** — \`nebula_get_inbox\` / \`nexus/bin/check-inbox.sh --role <role>\`: new \`to:<role>\` records since pointer. Surface to the user; never silently process.\n'
        '2. **Thread sweep** — check threads you participate in (track threads, decisions forum, change-log) for new comments since your last turn.\n'
        '3. **Act** — proceed with work until a stopping point.\n'
        '4. **Stopping point → post to the thread** — every completion, blocker, decision, review, or question gets a forum post: decisions → decisions forum (R-A records auto-sync); track status → the track thread; completed work → change-log (R14).\n'
        '5. **Notify via pointer** — after posting, write a \`to:<dependent-role>\` agent record (short, carrying thread IDs + record IDs + commit refs) so the other role''s next inbox check catches it.\n'
        '6. **Advance pointer** after surfacing (R17).\n'
        '\n'
        '### Autonomy boundary (work until blocker or decision needed)\n'
        '- Proceed autonomously until: (a) a decision is needed from another role, (b) a blocker surfaces, (c) a work unit completes.\n'
        '- At each boundary: post to the thread, notify via record, then **STOP that track**.\n'
        '- **While awaiting a decision or blocker leverage: switch to another project track** — never idle. The decision arrives via inbox on a later turn.\n'
        '\n'
        '### Communication rules\n'
        '- **Never relay via user chat.** If the user relays a message from another role, treat it as out-of-band: immediately write it into the corpus (thread + record) so the loop self-heals.\n'
        '- **Thread = durable conversation surface.** Comments, not just records. A question asked in chat is not asked until it''s a thread post + routed record.\n'
        '- **Records carry pointers** — always embed thread IDs, record IDs, commit refs in content so the corpus is self-navigating.\n'
        '\n'
        '### Role-specific wiring\n'
        '- **Engineer**: proceed until blocker/decision; switch projects while awaiting; check inbox after every turn; post to the thread at every stopping point; push cadence follows review gates, never chat acks.\n'
        '- **Architect**: check inbox for rulings/requests each turn; rule in the decisions forum (decision records auto-sync to threads); reply in-thread; propagate go-aheads via \`to:engineer\` records with thread pointers.\n'
        '- **All roles**: R13 clock in/out; R17 end-of-turn inbox; R14 change-log after substantive work.\n'
        '\n'
        '### Why\n'
        'Without this loop, the user becomes the relay between sessions — exactly the failure this doctrine removes. Every out-of-band message is a corpus gap: the receiving role cannot see it, the pointer cannot track it, and the audit trail is incomplete.\n'
        '\n'
        '### Enforcement\n'
        '- **Engineer**: doctrine embedded in \`.opencode/agents/engineer.md\` (Parallel Role Loop section) — binding at session start, not just advisory from the registry.\n'
        '- **Architect**: doctrine embedded in \`.opencode/agents/architect.md\` (Parallel Role Loop section).\n'
        '- Registry card stays canonical; frontmatter sections mirror it.\n'
        '\n'
        '### Future evolution — Question & Decision Cards (QDC)\n'
        'Planned async mechanism: structured "question cards" and "decision cards"\n'
        'so the user can be involved **in real time** without relaying between\n'
        'sessions. When QDC lands:\n'
        '- PRL becomes the synchronous baseline loop.\n'
        '- QDCs carry the async path (question raised → card routed → user\n'
        '  decides in real time → decision card posted → pointer notified).\n'
        '- Cards are corpus artifacts (records + forum threads), so the audit\n'
        '  trail stays complete.\n'
        '',
        ARRAY['parallel', 'cross-role', 'turn-protocol', 'messaging', 'doctrine', 'threads', 'inbox'],
        ARRAY['user relays a message', 'out-of-band message', 'another role asked', 'decision needed', 'awaiting decision', 'keep threads up to date', 'parallel session'],
        ARRAY['nebula_get_inbox', 'nebula_create_agent_record', 'nebula_set_inbox_pointer']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'analyst-ii', 'architect', 'builder', 'critic', 'engineer', 'inspector', 'lead-engineer', 'planner', 'reviewer'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 44. Knowledge-MCP Tool Reference (knowledge graph, read-only)
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'knowledge-mcp-tools',
        'Knowledge-MCP Tool Reference (knowledge graph, read-only)',
        'Catalog of the 8 knowledge-mcp tools (entities, edges, cross-refs, semantic search) — read-only KG access via knowledge-srv :3109.',
        '# Knowledge-MCP Tool Reference (knowledge graph, read-only)\n'
        '\n'
        '## When to use this card\n'
        '\n'
        'Any task that needs to know what exists, how entities relate, what is\n'
        'already known, or what evidence supports a claim. All knowledge-mcp tools\n'
        'are READ-ONLY (knowledge-srv :3109); writes go through nebula-mcp instead.\n'
        '\n'
        '## Server wiring\n'
        '\n'
        '- \`knowledge-mcp\` = stdio MCP server (dist/index.js) → REST proxy of\n'
        '  knowledge-srv \`http://localhost:3109\` (env \`KNOWLEDGE_SRV_URL\`).\n'
        '- Registered in the global opencode config, so tools are namespaced\n'
        '  \`knowledge-mcp_*\` (e.g. \`knowledge-mcp_knowledge_semantic_search\`).\n'
        '- Backend schema: \`knowledge.graph_entities\`, \`knowledge.graph_edges\`,\n'
        '  \`knowledge.graph_cross_references\`, \`knowledge.graph_migrations\`,\n'
        '  plus four pgvector embed layers.\n'
        '\n'
        '## Tool catalog (8)\n'
        '\n'
        '| Tool | Purpose | Key args |\n'
        '|------|---------|----------|\n'
        '| \`knowledge_list_entities\` | List entities, filterable | section, entity_type, status, search, limit (≤500), offset |\n'
        '| \`knowledge_get_entity\` | One entity + full properties JSON | section, entity_id |\n'
        '| \`knowledge_list_edges\` | List edges, filterable | source_section/id, target_section/id, relation_type, limit |\n'
        '| \`knowledge_get_entity_relations\` | Inbound + outbound relations | section, entity_id |\n'
        '| \`knowledge_list_cross_references\` | Graph-level cross-ref maps | map_name, source_section, target_id |\n'
        '| \`knowledge_list_migrations\` | Import/embed migration history | limit |\n'
        '| \`knowledge_graph_summary\` | Counts: entities by section, edges by relation type, migrations | — |\n'
        '| \`knowledge_semantic_search\` | Unified cosine search over 4 embed layers | query, limit, layers, recordTypes, minSimilarity |\n'
        '\n'
        '## knowledge_semantic_search details\n'
        '\n'
        '- Layers: \`kg\` (curated: work_requests, plans, actors),\n'
        '  \`harvest\` (harvest candidates), \`observation\` (transcripts, session\n'
        '  logs, audit docs), \`agent\` (agent records). Omit \`layers\` to search all.\n'
        '- \`recordTypes\` (agent layer only): report, engineering_log,\n'
        '  architecture_note, prompt, assessment, analysis, response, inspection,\n'
        '  decision — omit \`inspection\` to suppress noise.\n'
        '- \`minSimilarity\` (0–1): similarity floor, e.g. 0.55.\n'
        '- Returns merged results with provenance labels (curated / harvested /\n'
        '  observed / agent_record) — cite the layer in your findings.\n'
        '- Runs \`nexus/bin/unified_semantic_search.py\` with the rover venv python;\n'
        '  requires Ollama running with \`nomic-embed-text\` (verified available).\n'
        '\n'
        '## Typical sequence\n'
        '\n'
        '1. \`knowledge_semantic_search\` — topic-wide recall.\n'
        '2. \`knowledge_graph_summary\` — orientation (sections, relation types).\n'
        '3. \`knowledge_list_entities\` (filter section) → \`knowledge_get_entity\`.\n'
        '4. \`knowledge_list_edges\` / \`knowledge_get_entity_relations\` — expand.\n'
        '5. \`knowledge_list_cross_references\` — graph maps; then\n'
        '   \`nebula_list_cross_references\` for plan/record/entity joins.\n'
        '6. Corroborate with \`nebula_list_evidence_links\` and\n'
        '   \`nebula_list_agent_records\`.\n'
        '\n'
        '## Anti-patterns\n'
        '\n'
        '- Do NOT use these tools to write — they are read-only by design.\n'
        '- Do NOT treat \`knowledge_list_*\` output as canonical audit state; that\n'
        '  lives in nebula (agent records, cross_references, evidence_links).\n'
        '- Do NOT fabricate provenance — report which layer returned a result.\n'
        '- Knowledge Steward owns writes to knowledge.graph_*; all other roles\n'
        '  are read-only (see knowledge-graph-pipeline card for the write path).\n'
        '',
        ARRAY['reference', 'knowledge-graph', 'kg', 'semantic-search', 'tools', 'appendix'],
        ARRAY['knowledge graph', 'semantic search', 'knowledge-mcp', 'kg tools', 'cross references', 'knowledge_list', 'knowledge_semantic_search', 'investigate what exists'],
        ARRAY['knowledge_list_entities', 'knowledge_get_entity', 'knowledge_list_edges', 'knowledge_get_entity_relations', 'knowledge_list_cross_references', 'knowledge_list_migrations', 'knowledge_graph_summary', 'knowledge_semantic_search']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'analyst-ii', 'planner'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 45. Decision Cards (ask-user vocabulary)
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'decision-cards',
        'Decision Cards (ask-user vocabulary)',
        'When to emit interactive decision cards in Assembly threads (checkbox multi-select, radio single-choice, Other free-text) and how to parse the **Agreed selection:** reply.',
        '## Procedure\n'
        'Decision cards are the Assembly analogue of the CLI \`ask_user\` contract: interactive markdown blocks you emit in a thread (or comment) so the user can answer with a structured click instead of free prose. The Assembly UI renders them; the user''s selection is written back as a durable \`**Agreed selection:**\` reply you can parse deterministically.\n'
        '\n'
        '### When to emit\n'
        'Use a decision card when the question has a **bounded set of choices** and the answer changes what you do next:\n'
        '- single decision between options -> radio single-choice\n'
        '- pick any subset -> checkbox multi-select\n'
        '- approval / sign-off gates, branch decisions, plan acceptance, option A/B/C, "how should I proceed"\n'
        '\n'
        'Do NOT use a card for open-ended questions (ask in prose), or when the answer is obvious (just proceed). If the user replies in prose instead of clicking, treat the prose as the answer - the card is a convenience, never a blocker.\n'
        '\n'
        '### Syntax to emit\n'
        'Checkbox (multi-select) block - one \`- [ ]\` line per option:\n'
        '\`\`\`\n'
        'Which of these should I include?\n'
        '- [ ] option one\n'
        '- [ ] option two\n'
        '- [ ] option three\n'
        '\`\`\`\n'
        '\n'
        'Radio (single-choice) block - \`- ( )\` lines; pre-select with \`- (x)\` if one is the default:\n'
        '\`\`\`\n'
        'How should we proceed?\n'
        '- ( ) Option A\n'
        '- ( ) Option B\n'
        '- (x) Option C (default)\n'
        '\`\`\`\n'
        '\n'
        '"Other" escape hatch - any option line whose label starts with **Other** (case-insensitive) opens a free-text field when selected; the typed value lands in the reply as \`Other: <text>\`:\n'
        '\`\`\`\n'
        '- ( ) Option A\n'
        '- ( ) Other\n'
        '\`\`\`\n'
        '\n'
        'Rules:\n'
        '- Keep one decision per card; keep options short.\n'
        '- A block is interactive only if EVERY line in it is a choice line - never mix prose into a choice block (use a blank line to separate prose from the list).\n'
        '- The \`[ ]\`/\`[x]\` and \`( )\`/\`(x)\` markers must come right after \`- \` or \`* \` at the start of the line.\n'
        '\n'
        '### How to read the reply\n'
        'After the user submits, the UI posts a reply beginning with \`**Agreed selection:**\` that mirrors the final visual state of the card:\n'
        '- checkbox: \`- [x] option\` = agreed, \`- [ ] option\` = not agreed\n'
        '- radio: \`- (x) option\` = the single choice; all others stay \`- ( )\`\n'
        '- Other text: appended to the chosen line as \`Other: <text>\`\n'
        '\n'
        'Parse the block after the header; treat \`[x]\`/\`(x)\` lines as the answer. A radio card yields exactly one \`(x)\` line. If the reply contains no \`[x]\`/\`(x)\` lines, the user submitted no selection - ask again or fall back.\n'
        '\n'
        '### Fallbacks\n'
        '- Card UI unavailable (plain markdown in a non-interactive client): the card renders as ordinary list text - still readable; the user can reply with prose.\n'
        '- User replies in prose instead of clicking: treat the prose as the answer.\n'
        '- No selection made: re-ask with a tighter card or proceed with the default.',
        ARRAY['decision', 'ask-user', 'interactive', 'assembly', 'choice', 'agreement'],
        ARRAY['decision needed', 'bounded choice', 'approval gate', 'branch decision', 'ask user', 'offer options', 'choose one', 'select', 'how should I proceed'],
        '{}'
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'analyst-ii', 'architect', 'builder', 'critic', 'devops', 'engineer', 'engineer-ii', 'inspector', 'lead-engineer', 'planner', 'reviewer', 'tester', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 46. Role Creation (deterministic runbook)
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'role-creation',
        'Role Creation (deterministic runbook)',
        'One source-of-truth edit (config/roles/roles.json) plus generated artifacts registers a role across every surface; bin/verify-roles.py proves end-to-end coverage.',
        '## Procedure\n'
        'Adding a new role to the system is deterministic: edit the canonical expectations file, emit the artifacts, and verify with the end-to-end check. No undocumented parallel hand-edits.\n'
        '\n'
        '### 1. Canonical edit\n'
        'Add the role to \`nexus/config/roles/roles.json\` under \`roles\` — the key must match the name inserted into \`tackle.roles\`. Inherit \`roleDefaults\` unless the role is special (test/alias roles override surfaces to false).\n'
        '\n'
        '### 2. Emit artifacts\n'
        'For a full agent role, create/update:\n'
        '- **tackle.roles row** — via the seed arrays (\`tackle-mcp/src/db.ts\` DEFAULT_ROLES, \`conduit-mcp/src/db.ts\` migration defaultRoles) or a migration; the live DB is canonical.\n'
        '- **Persona prompt** — \`tackle.prompts\` row (role, slug \`opencode-persona\`, version 1) in \`schemas/migrations/tackle/\` (pattern: \`sysadmin_persona_v1.sql\`); apply to the live DB.\n'
        '- **Harness agent file** — \`config/harnesses/opencode/agents/<role>.md\` (frontmatter: assumes_role, permissions; pattern: \`sysadmin.md\`).\n'
        '- **Procedure cards** — add the role to the role lists of the relevant \`the canonical procedure-card table\` cards (the role assignment join table assignments); then regenerate the seed: \`python3 bin/regenerate_memory_seed.py --verify\`.\n'
        '- **Assembly alias** — \`assembly.users\` row with alias = role name (pattern: builder seed in \`assembly-migration.sql\`).\n'
        '- **nebula role CHECK** — new \`typescript/nebula-srv/migrations/0NN-allow-<role>.sql\` mirroring \`052-allow-sysadmin-dba-role.sql\`; apply + replicate to barium.\n'
        '- **Governance** — add to \`harness-srv/src/governance.ts\` KNOWN_EXECUTORS only if the role issues receipts.\n'
        '\n'
        '### 3. Verify\n'
        'Run \`python3 bin/verify-roles.py\` — it checks every expected surface for every registered role (persona, procedure cards, assembly alias, harness file, nebula CHECK, governance). Exit 0 = all covered. Fix FAILs before closing.\n'
        '\n'
        '### 4. Close\n'
        'Post the walkthrough evidence on the role-creation thread and record an engineering log. Surface the new role to the architect for allowlist ratification if the case/naming deviates from convention.',
        ARRAY['role', 'runbook', 'bootstrap', 'onboarding', 'deterministic'],
        ARRAY['add a role', 'new role', 'create role', 'role creation', 'register role', 'onboard role'],
        '{}'
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['architect', 'devops', 'engineer', 'engineer-ii', 'lead-engineer'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 47. Operator: Post to Assembly Forums
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'operator-assembly-posting',
        'Operator: Post to Assembly Forums',
        'How to create threads and comments in Assembly forums. Use assembly_create_thread for new posts and assembly_create_comment for replies. Known forum slugs and the operator user UUID are listed below.',
        '# Post to Assembly Forums\n'
        '\n'
        '## When to use this card\n'
        '\n'
        'The user asks you to post something to Assembly — "post a to-do", "create a thread in the forum", "add a comment", "report this to the issues forum", "post a change log entry", or any request that results in creating content in an Assembly forum.\n'
        '\n'
        '## Known Identities\n'
        '\n'
        '### Operator user UUID\n'
        '\n'
        '\`\`\`\n'
        '6df32fa2-15eb-45a3-88a6-f11b09923a50\n'
        '\`\`\`\n'
        '\n'
        'Always use this UUID as \`user_id\` when posting as the operator.\n'
        '\n'
        '### Forum slug → UUID mapping\n'
        '\n'
        '| Slug                       | UUID                                   | Purpose                              |\n'
        '|----------------------------|----------------------------------------|--------------------------------------|\n'
        '| to-do                      | 836a1dec-39a7-4c97-8932-472f07fc16f5  | Action items, tasks, work to execute |\n'
        '| issues-and-open-questions  | e42e48f3-3fa6-4ecf-8914-90022bae1518  | Bugs, blockers, open questions       |\n'
        '| admin-notes                | d391c07f-6791-4dfd-a451-04151a543cce  | Internal admin notes                 |\n'
        '| change-log                 | 06bafd38-4b9a-4b3a-bfc3-4d41b31a5eb8  | System change summaries              |\n'
        '| decisions                  | 703bc0f9-faf4-4c94-a52d-8f0d4024a89b  | Architecture and design decisions    |\n'
        '| transcripts                | 191e799e-f491-49f1-8bc2-a9be0ac29dd6  | Harvested conversation transcripts   |\n'
        '| discussions                | 431cdfc9-ea42-46b7-ad2f-3be4e7f4b7d6  | General discussion                   |\n'
        '| engineering                | 0b079fef-3f6d-4463-b1a2-dc1b718412c7  | Engineering work items               |\n'
        '| planning                   | b5611775-a471-48ca-b111-56e54b9810c3  | Planning discussions                 |\n'
        '| doctrine                   | acb756b6-93fb-42c4-aac4-f7c134155f64  | Operating doctrine and governance    |\n'
        '| drift-reports              | aa39bfea-4f1d-4ee9-9be8-6c2af5a139d3  | Projection drift reports             |\n'
        '| system-operations          | d2d5f367-c3ae-466d-a75e-c9413d1502f3  | Operational status and incidents     |\n'
        '\n'
        'If the user specifies a forum not listed above, call \`assembly_list_forums\` to discover its UUID, or call \`assembly_find_forum_by_name\` with a search term.\n'
        '\n'
        '## Procedure\n'
        '\n'
        '### Creating a new thread\n'
        '\n'
        '1. Determine the target forum. If the user said "to-do", use \`forum_id = 836a1dec-39a7-4c97-8932-472f07fc16f5\`. If the user said "issues", use \`forum_id = e42e48f3-3fa6-4ecf-8914-90022bae1518\`. Otherwise, look it up from the table above or call \`assembly_list_forums\`.\n'
        '\n'
        '2. Call \`assembly_create_thread\` with these arguments:\n'
        '   \`\`\`\n'
        '   {\n'
        '     "title": "<concise title>",\n'
        '     "body": "<markdown body with the full content>",\n'
        '     "user_id": "6df32fa2-15eb-45a3-88a6-f11b09923a50",\n'
        '     "forum_id": "<forum UUID>",\n'
        '     "role": "operator",\n'
        '     "model": "operator-svc"\n'
        '   }\n'
        '   \`\`\`\n'
        '\n'
        '3. Report the result to the user — the thread ID and which forum it was posted to.\n'
        '\n'
        '### Adding a comment to an existing thread\n'
        '\n'
        '1. Call \`assembly_get_thread\` with \`{ "thread_id": "<thread UUID>" }\` to confirm the thread exists and see existing comments.\n'
        '\n'
        '2. Call \`assembly_create_comment\` with:\n'
        '   \`\`\`\n'
        '   {\n'
        '     "thread_id": "<thread UUID>",\n'
        '     "body": "<markdown body>",\n'
        '     "user_id": "6df32fa2-15eb-45a3-88a6-f11b09923a50",\n'
        '     "role": "operator",\n'
        '     "model": "operator-svc"\n'
        '   }\n'
        '   \`\`\`\n'
        '\n'
        '   To reply to a specific comment, also pass \`"parent_id": "<comment UUID>"\`.\n'
        '\n'
        '3. Report the result.\n'
        '\n'
        '### Looking up a thread by title\n'
        '\n'
        'If the user says "reply to the thread about X" or "comment on the XYZ thread", call \`assembly_find_thread_by_title\` with \`{ "title": "<search term>" }\` to find the thread UUID, then use \`assembly_get_thread\` to read it, then \`assembly_create_comment\` to reply.\n'
        '\n'
        '## Anti-patterns\n'
        '\n'
        '- Do NOT guess forum UUIDs — always use the known mapping above or look them up.\n'
        '- Do NOT guess the operator user UUID — always use \`6df32fa2-15eb-45a3-88a6-f11b09923a50\`.\n'
        '- Do NOT fabricate thread IDs from memory — always use the ID returned by the create call.\n'
        '- Do NOT post without a title — every thread requires a title.\n'
        '\n'
        '## MCP tools used\n'
        '\n'
        '- \`assembly_create_thread\` — create a new thread in a forum\n'
        '- \`assembly_create_comment\` — add a comment or reply\n'
        '- Thread status (parent-post rating 0-7): set via REST — optional \`statusRating\`\n'
        '  on the comment POST (advance while replying), or \`PUT /api/forums/threads/:id/status\`\n'
        '  body \`{"rating":N}\`. Vocabulary + conventions: card \`thread-status-ratings\`.\n'
        '  (Not yet exposed as an assembly-mcp parameter/tool.)\n'
        '- \`assembly_get_thread\` — read a thread and its comments\n'
        '- \`assembly_list_forums\` — list all forums (discovery)\n'
        '- \`assembly_find_forum_by_name\` — search forums by name\n'
        '- \`assembly_find_thread_by_title\` — search threads by title\n'
        '',
        ARRAY['assembly', 'forum', 'thread', 'posting', 'operator', 'todo', 'to-do'],
        ARRAY['post a to-do', 'create a thread', 'post to assembly', 'add a comment', 'reply to thread', 'post in forum', 'create todo', 'issues forum', 'change log', 'to-do item'],
        ARRAY['assembly_create_thread', 'assembly_create_comment', 'assembly_get_thread', 'assembly_list_forums', 'assembly_find_forum_by_name', 'assembly_find_thread_by_title']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['operator'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 48. Thread Status Ratings (Assembly parent posts)
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'thread-status-ratings',
        'Thread Status Ratings (Assembly parent posts)',
        'Canonical 0-8 rating vocabulary for Assembly parent posts (assembly.posts.rating), wire contract to set it, and per-role advancement conventions (8 Approved is INTERIM 2026-08-25).',
        '## Procedure\n'
        '\n'
        'Every forum thread carries a status stored in \`assembly.posts.rating\` on the ROOT post (thread id == root post id). NULL = 0 = Posted. Advance it as work progresses so the UI status bar reflects reality.\n'
        '\n'
        '## Vocabulary\n'
        '| rating | label | meaning |\n'
        '|---|---|---|\n'
        '| 0 | Posted | default; no role has picked it up |\n'
        '| 1 | Specified | requirements/scope pinned down |\n'
        '| 2 | Planned | scheduled or picked up by a role |\n'
        '| 3 | Implemented | work done, awaiting acceptance |\n'
        '| 4 | Accepted | completed and accepted (e.g. \`Completed: ...\` reply ratified) |\n'
        '| 5 | Rejected | false alarm / rejected outcome |\n'
        '| 6 | Reopened | regression after fix; needs another pass |\n'
        '| 7 | Closed | archival / no-action wind-down |\n'
        '| 8 | Approved (INTERIM) | operator approval of a posted To Do; candidate is "in flight", ready for requirement conversion (interim scheme 2026-08-25; superseded by Wind doctrine) |\n'
        '\n'
        '## Wire contract\n'
        '- READ: thread list + detail return \`statusRating\`.\n'
        '- SET: \`PUT /api/forums/threads/:threadId/status\` body \`{"rating":0..8}\`.\n'
        '- SET in-gesture: \`POST /api/forums/threads/:threadId/comments\` accepts optional \`statusRating\` — advance the parent in the same call.\n'
        '- NOTE: assembly-mcp does not yet expose a status tool/param — REST only until the MCP surface lands.\n'
        '\n'
        '## Per-role conventions (ratified 2026-08-22; extended 2026-08-25 with 8)\n'
        '- engineer/engineer-ii finishing to-do work → reply \`Completed: ...\` with \`statusRating:4\`.\n'
        '- sysadmin incidents (issues-and-open-questions): resolution→4, false alarm/test→5, regression after fix→6, new incident stays 0.\n'
        '- planner/architect scoping a to-do thread → 1 Specified when scope is pinned, 2 Planned when scheduled/picked up.\n'
        '- reviewer rejection → 5; critic reopen → 6.\n'
        '- 7 Closed reserved for archival/no-action-needed wind-downs.\n'
        '\n'
        '## Per-role conventions (extended)\n'
        '- operator approving a posted To Do → 8 Approved (interim marking: "on the list" → "in flight").\n'
        '- To-do lifecycle (doctrine as of 2026-08-25): 0 Posted → 8 Approved → Requirement spawned (engineer converts) → Spec/Plan/WR refs linked in thread comments → 4 Accepted when the work is ratified. 8 is also used while the work is in flight; the final state is 4 Accepted or 5 Rejected.\n'
        '- Interim Scheme note: this is temporary doctrine for the miniature workflow pending Wind doctrine; commit messages and records reference this interim nature.\n'
        '\n'
        'Rule of thumb: every substantive thread reply SHOULD advance the parent status in the same call. For To Do threads this advance is a MUST — see card \`to-do-lifecycle-policy\` (the To Do lifecycle extends this vocabulary; pointer, not a fork).\n'
        '',
        ARRAY['reference', 'assembly', 'thread-status', 'messaging', 'ratings'],
        '{}',
        '{}'
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'analyst-ii', 'architect', 'design-synthesist', 'engineer', 'layout-mechanic', 'lead-engineer', 'operator', 'reviewer'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 49. DBA Change-Review Workflow
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'dba-change-review-workflow',
        'DBA Change-Review Workflow',
        'Review loop for to:dba / type:db-change records: verify against LIVE state before approving, apply only on explicit user approval, prefer service endpoints over direct data surgery, and close the loop with evidence.',
        '## Procedure\n'
        '\n'
        'Handles records tagged \`to:dba\` + \`type:db-change\` + \`status:open\` (via \`nebula_get_inbox\` or \`bin/check-inbox.sh --role DBA\`).\n'
        '\n'
        '1. **Read the full record** — \`GET /api/agent-records\` on nebula-srv (:3101) or the MCP tool; never review from a summary.\n'
        '2. **Verify against LIVE state, not docs** — connect with the deployment''s actual credentials and confirm: object exists and matches the spec (columns, indexes, collations, row counts, \`information_schema\`/\`pg_indexes\`/\`EXPLAIN\` as appropriate). R15 applies: "data not loading" is usually a URL/config mismatch, not corruption.\n'
        '3. **Review for correctness in the target engine''s dialect** — a spec written for one engine (e.g. partial indexes, TTL semantics) must be translated, not assumed, when the live store is another (e.g. MongoDB TTL monitors, Postgres partial+functional indexes). Flag every translation explicitly in the review.\n'
        '4. **Apply only on explicit user approval** — "go ahead" / "let''s proceed" from the user, captured in the audit trail. Never apply on a role''s request alone.\n'
        '5. **Prefer service endpoints over data surgery** — use the owning service''s designed endpoints (e.g. timeclock \`/timeout-cleanup\`, conduit receipts) before any direct UPDATE/DELETE. Direct DDL/DML is last resort and gets an R1 record first.\n'
        '6. **Close the loop with evidence** — reply on the record/thread with what was applied and verification output; post completion to the requesting role (\`type:status-update\`, \`to:<requester>\`); write R2 summary.\n'
        '\n'
        'Triggers: any \`to:dba\`/\`type:db-change\` inbox record; user asks to review/apply a database change.',
        ARRAY['dba', 'change-review', 'workflow', 'inbox'],
        ARRAY['a db-change record arrives in the DBA inbox', 'user asks to review a database change', 'user asks to apply an approved change'],
        ARRAY['nebula_get_inbox', 'nebula_create_agent_record']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['DBA'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 50. DBA Replication Protocol (R9)
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'dba-replication-protocol',
        'DBA Replication Protocol (R9)',
        'After any schema change or migration, confirm replication to the canonical off-machine backup target (currently vanadium) with the user — never assume; verify last backup health before claiming safety.',
        '## Procedure\n'
        '\n'
        'Implements R9. Replication target history: strontium (down 2026-08) → barium (2026-08-25; disk-full forensics unresolved, \`ssh barium\` does not resolve) → **vanadium** (since 2026-09-05 per \`pg-backup.env\`, \`REMOTE_HOST=vanadium\`).\n'
        '\n'
        '1. **After any schema change or migration** (DDL, new table/index, view change): ask the user whether to replicate. Never assume; the user confirms target and timing.\n'
        '2. **Before claiming a change is safe**, check the backup chain''s last verified run: timers run daily (last verified healthy run 2026-09-10 03:48, checksums OK). A change made after the last verified run is not yet protected.\n'
        '3. **CI tier**: \`vanadium-ci-backup.{sh,service,timer}\` also targets vanadium since 2026-09-10 (retargeted from barium per ruling V4; fetch fixed, live green run verified — PR #203, records \`05f677f7\`/\`6f6a7f6c\`).\n'
        '4. **If barium returns** after forensics, revisit the target with the user before switching anything.\n'
        '\n'
        'Triggers: any DDL or migration just applied; user asks about backup/replication state.',
        ARRAY['dba', 'replication', 'backup', 'r9', 'vanadium'],
        ARRAY['a schema change or migration was applied', 'user asks whether data is backed up', 'replication target question'],
        ARRAY['nebula_create_agent_record']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['DBA'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 51. Nexus Data-Store Schema Map (DBA Orientation)
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'dba-schema-map',
        'Nexus Data-Store Schema Map (DBA Orientation)',
        'Orientation map of nexus data stores and their services: which service owns which port/store, where the canonical tables live, and the known gotchas (views, JSONB analytics, role-case, retired files).',
        '## Orientation Map (verified 2026-09-14)\n'
        '\n'
        '| Store | Owner/service | Notes |\n'
        '|---|---|---|\n'
        '| \`nexus\` PG DB | nebula-srv :3101 (REST), nebula-mcp :3102 (MCP) | \`nebula\` schema: agent_records, harvests (VIEW — base tables underneath), implementation_plans |\n'
        '| \`nexus\` PG DB \`tackle\` schema | tackle-srv :3410 (REST), tackle-mcp :3400 (MCP), role-memory-srv :3500 (sync) | registry \`memory\` + \`role_memory\` tables, \`tackle.role_leases\` (ACTIVE lease per role), \`tackle.agent_timeclock\` |\n'
        '| Redis :6379 | role-memory-srv writes, tackle-mcp reads | \`mem:proc:{slug}\`, \`mem:idx:{role}\` (case = as-registered), \`mem:meta:last_updated\` |\n'
        '| Assembly | assembly-srv :3107 (REST) | forums/threads/comments; roles posted as (role, model); user list at \`/api/users\` |\n'
        '| Timeclock | :3600 | \`/clock-in\`, \`/clock-out\`, \`/active\`, \`/timeout-cleanup?maxAgeHours=\` (janitor for zombie rows) |\n'
        '| Conduit | :3100 | WorkRequest pipeline state; \`create_plan\` tool REMOVED — use nebula_create_plan |\n'
        '| MongoDB | live, separate engine | harvest/transcript documents; TTL monitors + unique indexes per engine dialect |\n'
        '\n'
        '**Known gotchas (all verified this deployment):**\n'
        '- \`nebula.harvests\` is a VIEW; analytics (\`userTurns\`, \`keywordHits\`, \`tagFrequency\`) are computed in nebula-srv routes.ts — \`keywordHits\`/\`tagFrequency\` only under their own sort params (lazy, by design).\n'
        '- Role case: \`DBA\` and \`dba\` are distinct registry keys; indices sync under as-registered case only.\n'
        '- \`memory_get_procedures\` via tackle-mcp :3400 proxies to tackle-srv :3410 — \`fetch failed\` means tackle-srv is down (R15 lesson).\n'
        '- \`nexus/.conduit-data\` is retired (posterity mirror: \`nexus/audit/CONDUIT_DATA\`); conduit state lives in PG.\n'
        '- DSNs default to \`postgresql://pguser:pgpass@localhost:5432/nexus\` across services (env: \`CONDUIT_PG_DSN\`, \`MEMORY_PG_DSN\`).\n'
        '\n'
        'Use with dba-schema-migration-path for where to change things; this card is for where things ARE.\n'
        '\n'
        'Triggers: first DBA session on a new machine; "where does X live"; debugging a service/store mismatch.',
        ARRAY['dba', 'reference', 'orientation', 'schema-map', 'appendix'],
        ARRAY['new DBA session', 'which service owns this store', 'where is this table'],
        ARRAY['memory_get_procedures']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['DBA'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 52. DBA Schema Migration Path
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'dba-schema-migration-path',
        'DBA Schema Migration Path',
        'Where nexus schema truth lives per store, and the conformant order of operations for schema work: find the owning migration source, change through it, ask R9, then propagate seeds.',
        '## Procedure\n'
        '\n'
        '**Schema truth by store (verified layout):**\n'
        '- \`nexus\` DB, \`nebula\` schema — canonical agent/harvest/plan store. \`nebula.harvests\` and similar list targets are VIEWS over base tables (check before "adding a column to a view"). Compute-heavy analytics may live server-side in \`typescript/nebula-srv/src/routes.ts\` (JSONB subqueries), not in the DB.\n'
        '- \`nexus\` DB, \`tackle\` schema — procedure registry. Cards live in the registry''s \`memory\` + \`role_memory\` tables (source of truth). Repo seed: \`typescript/tackle-seeds/index.ts\`, REGENERATED ONLY via \`python3 nexus/bin/regenerate_memory_seed.py\` (never hand-edit; \`--verify\` does a shadow-seed byte-compare). Role-case is significant: roles are stored as-registered (\`DBA\` ≠ \`dba\`).\n'
        '- \`tackle.schema_version\` — schema version tracking.\n'
        '- MongoDB (live, separate engine) — harvest/transcript document stores; TTL + unique indexes specified per engine dialect.\n'
        '- Promotion identity tables under \`promotion_schema_version\` (v7 as of 2026-08-31).\n'
        '\n'
        '**Order of operations for schema work:**\n'
        '1. R1 record stating intent, target objects, and risk.\n'
        '2. Reality-check connectivity (R15) — is the service even pointed at the store you''re about to change?\n'
        '3. Apply via the owning service''s migration path where one exists; direct DDL only with explicit user approval.\n'
        '4. R9 replication question (see dba-replication-protocol).\n'
        '5. If registry cards changed: regenerate seed file, worktree PR it so fresh bootstraps match the live DB.\n'
        '6. R2 record + change-log post with before/after evidence.\n'
        '\n'
        'Triggers: adding/altering tables, views, or indexes; seeding or editing procedure cards; any "can you change the schema" request.',
        ARRAY['dba', 'schema', 'migrations', 'workflow', 'reference'],
        ARRAY['schema change requested', 'procedure card work', 'where does this table live'],
        ARRAY['nebula_create_agent_record']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['DBA'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 53. Worktree Development Workflow
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'worktree-development-workflow',
        'Worktree Development Workflow',
        'Work in a git worktree; commit, push, and raise a PR without asking, gated on passing tests.',
        '## Procedure\n'
        '\n'
        '1. **Create a worktree** in the canonical root (full absolute path, a sibling of the repo, NEVER inside the repo):\n'
        '   - \`git -C /home/codex/dev/nexus worktree add /home/codex/dev/nexus-worktrees/<topic> -b <topic>\`\n'
        '   - Example: \`/home/codex/dev/nexus-worktrees/add-foo-endpoint\` on branch \`add-foo-endpoint\`.\n'
        '   - The canonical worktree root is \`/home/codex/dev/nexus-worktrees\`, not any shorthand, and never \`nexus/worktrees\` inside the repo.\n'
        '\n'
        '2. **Keep main clean.** Do the work on the worktree branch; never commit directly to \`main\`.\n'
        '\n'
        '3. **Write tests** for the change. Tests are a non-negotiable condition for shipping.\n'
        '\n'
        '4. **Commit, push, and open a PR — without asking permission** (R8). No confirmation gate.\n'
        '   - Commit messages MUST align with the agent record (the record is the source of truth).\n'
        '   - Every push to a shared branch MUST be accompanied by a PR with: what changed, why, migration steps, agent record UUID, and verification.\n'
        '   - Squash-merge preferred.\n'
        '\n'
        '5. **The merge gate is the tests.** A PR may only be merged when the code has tests AND the tests pass. Verification of the passing condition is the tester role''s attestation (can_verify_work_requests; grant ratified in decision 2f9acb11) — attest with evidence, not author self-declaration. If the conditions are not met, raise the PR as a **draft** (do not request merge) and say so; do not silently merge untested work.\n'
        '\n'
        '6. **Track the PR** through the Assembly \`github\` forum until it merges or closes (R8.1).',
        ARRAY['worktree', 'git', 'pr', 'pull-request', 'committing', 'shipping', 'development'],
        ARRAY['worktree', 'create worktree', 'commit push', 'raise a pr', 'pull request', 'git', 'branch'],
        '{}'
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'architect', 'builder', 'critic', 'devops', 'engineer', 'engineer-ii', 'lead-engineer', 'planner', 'reviewer', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 54. PR Protocol
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'pr-protocol',
        'PR Protocol',
        'Full pull-request template, merge gate, and exceptions referenced by AGENTS.md R8 — commit alignment, required PR body sections, tester attestation semantics, draft exception, post-merge hygiene.',
        '# PR Protocol\n'
        '\n'
        'The full pull-request template, merge gate, and exceptions referenced by\n'
        'AGENTS.md **R8**. Content authority remains AGENTS.md; this card is the\n'
        'operational expansion so the R8 reference resolves. Created 2026-09-17 by\n'
        'the DBA (R2 5f5a41e6 flagged the dangling pointer; R1 cbc8c72f).\n'
        '\n'
        '## 1. Every push gets a PR — no exceptions\n'
        '\n'
        'All work ships from a worktree branch (R8.0 canonical root:\n'
        '\`/home/codex/dev/nexus-worktrees/<topic>\`); \`main\` is never pushed to\n'
        'directly. Commit, push, and raise the PR **without asking permission** —\n'
        'the no-confirmation gate is the rule, not a courtesy to request.\n'
        '\n'
        '## 2. Commit-message alignment\n'
        '\n'
        'Commit messages MUST align with what was written to the agent record — the\n'
        'record is the source of truth for what was done. If the record says the\n'
        'work was X, the commit message describes X. Squash-merge preferred; the\n'
        'squash subject should carry the PR title.\n'
        '\n'
        '## 3. The PR body — required sections\n'
        '\n'
        'Every PR body covers, at minimum:\n'
        '\n'
        '1. **What** — the files/artifacts changed and what each does.\n'
        '2. **Why** — the motivating record, ruling, thread, or user request\n'
        '   (cite agent record UUIDs / thread IDs).\n'
        '3. **Migration / deployment steps** — how the change goes live, or an\n'
        '   explicit "none required". For DB migrations: the apply command,\n'
        '   staging-inert status, and any quiesce/reader-check discipline.\n'
        '4. **Agent record UUID** — the R1 (intent) and R2 (completion) records\n'
        '   for the work unit.\n'
        '5. **Verification** — how the change was proven: test counts and names,\n'
        '   CI workflow runs, live probe output. "Tests pass" without evidence\n'
        '   names is not verification.\n'
        '\n'
        '## 4. The merge gate is the tests\n'
        '\n'
        'A PR may only be merged when the code has tests AND the tests pass.\n'
        'Verification of the passing condition is the **tester role''s** attestation\n'
        '(\`can_verify_work_requests\`; grant ratified in decision 2f9acb11) — attest\n'
        'with evidence, not author self-declaration.\n'
        '\n'
        'If the conditions are not met, raise the PR as a **draft** (do not request\n'
        'merge) and say so; do not silently merge untested work.\n'
        '\n'
        '### Merge-gate exceptions (narrow, enumerated)\n'
        '\n'
        '- **Admin merge to unblock a verified sequence** — when checks are green\n'
        '  but branch-hygiene state (e.g. stale main pull) blocks the merge, an\n'
        '  admin merge with the sequence recorded is legitimate. Record the ruling\n'
        '  that authorizes it.\n'
        '- **Reverts** — reverting a bad merge restores a known state; the revert\n'
        '  PR cites the incident. Tests still apply where they can run.\n'
        '- **CI-infrastructure-only changes** — workflow files with no runtime\n'
        '  surface (e.g. a postgres version pin) still get their new workflow\n'
        '  exercised on the PR itself; that run is the test.\n'
        '\n'
        'Anything outside these three is not an exception; it is a draft.\n'
        '\n'
        '## 5. Named reviewers are governance-carried (the #310 finding)\n'
        '\n'
        'GitHub-native review requests are structurally unavailable in this\n'
        'harness: every PR is authored by the single operator account\n'
        '(\`markpippins\`), and GitHub refuses review requests to the PR author\n'
        '(REST 422: "Review cannot be requested from pull request author"). Do not\n'
        'retry the request or treat the refusal as a process failure.\n'
        '\n'
        '- **Carry the obligation on the governance surfaces instead**: a\n'
        '  \`to:<role>\` agent record naming the reviewer with the PR link, plus a\n'
        '  comment on the governing thread. (Finding: R2 7e867bcb; case: PR #310,\n'
        '  where the architect reviewed and merged ~75 minutes after the ping.)\n'
        '- **\`reviews: []\` is not evidence of no review.** When auditing PR state\n'
        '  via \`gh pr view --json reviews\`, an empty array is the harness norm,\n'
        '  not a missing review. The merge itself is the review outcome when the\n'
        '  named reviewer merged it or the merge follows their explicit go.\n'
        '\n'
        '## 6. Track the PR after push (R8.1)\n'
        '\n'
        'Raising the PR is not the end of the work. On subsequent turns, check the\n'
        'Assembly \`github\` forum for the PR''s outcome — merged, closed, rejected,\n'
        'CI/lint results — and surface it. A merge confirmation closes the loop the\n'
        'PR opened.\n'
        '\n'
        '## 7. Post-merge hygiene\n'
        '\n'
        'Pull main into the shared checkout, restart any services that consume the\n'
        'merged changes, and verify the live behavior the PR promised. File the R2\n'
        'record with the merge SHA and the verification evidence.\n'
        '\n'
        '## Related\n'
        '\n'
        '- AGENTS.md R8 / R8.0 / R8.1 — the governing rules\n'
        '- \`worktree-development-workflow\` card — worktree creation and branch rules\n'
        '- \`git-committer\` skill — commit mechanics\n'
        '\n'
        '',
        ARRAY['pr', 'pull-request', 'merge-gate', 'committing', 'worktree', 'protocol'],
        ARRAY['pr protocol', 'pull request', 'raise a pr', 'merge gate', 'pr template', 'open a pr', 'merge conditions'],
        '{}'
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'architect', 'builder', 'critic', 'DBA', 'devops', 'engineer', 'engineer-ii', 'planner', 'reviewer', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 55. SQL Pitfalls (house recurring)
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'sql-pitfalls',
        'SQL Pitfalls (house recurring)',
        'Recurring PostgreSQL gotchas from real house work (V155-V177, grants, CI) — symptom/cause/fix for the ||/->> precedence trap, NULL concat, RAISE/SQLSTATE forms, autocommit BEGIN traps, constraint/index detection, view-writes, CASCADE blast radius, engine drift, skeleton-vs-live drift, PL/pgSQL DECLARE-time defaults, psql boolean rendering (t/f vs ::text ''true''), and more.',
        '# SQL Pitfalls (house recurring)\n'
        '\n'
        'Recurring PostgreSQL gotchas encountered in real house work (V155–V177,\n'
        'grants #299/#301, CI #300, live batteries), each with symptom, cause, and\n'
        'the fix that worked. If a query fails weirdly here, check this list\n'
        'before inventing a new theory. Maintained by the DBA; newest live\n'
        'examples cited by record where they exist.\n'
        '\n'
        '## 1. \`''x'' || details->>''op''\` — the jsonb extraction precedence trap\n'
        '\n'
        '- **Symptom:** \`ERROR: operator does not exist: text ->> unknown\` or\n'
        '  \`invalid input syntax for type json\` on a SELECT that looks fine.\n'
        '- **Why:** \`||\` and \`->>\` have the SAME precedence and are\n'
        '  left-associative. \`''NEBULA_AUDIT: '' || details->>''op''\` parses as\n'
        '  \`(''NEBULA_AUDIT: '' || details) ->> ''op''\` — i.e. PostgreSQL tries to\n'
        '  JSON-parse your concatenated string, then extract from it. Bitten\n'
        '  three times in house history (V167 audit inspection, V177 battery\n'
        '  display query, +1).\n'
        '- **Fix:** always parenthesize extractions in concatenations:\n'
        '  \`''x: '' || (details->>''op'')\`. Same for comparisons in WHERE:\n'
        '  \`(details->>''table'') = ''roles_history''\`.\n'
        '\n'
        '## 2. \`SELECT ''label='' || col\` prints NOTHING when col is NULL\n'
        '\n'
        '- **Symptom:** a diagnostic query returns zero rows for a row you know\n'
        '  exists.\n'
        '- **Why:** string concatenation with NULL yields NULL — the whole\n'
        '  result string silently disappears (no error, no row text).\n'
        '- **Fix:** \`SELECT ''label='' || coalesce(col::text, ''<null>'')\`. Live\n'
        '  example: pr-protocol convention-date probe (2026-09-17).\n'
        '\n'
        '## 3. PL/pgSQL RAISE takes a single format literal\n'
        '\n'
        '- **Symptom:** syntax error at \`||\` inside \`RAISE EXCEPTION ''a'' || ''b''\`.\n'
        '- **Why:** RAISE''s message is one format string, not an expression\n'
        '  list.\n'
        '- **Fix:** \`RAISE EXCEPTION ''a % b'', v_var\` or\n'
        '  \`RAISE EXCEPTION ''%'', format(''a % b'', x, y)\`. Caught in the V175\n'
        '  grant template work.\n'
        '\n'
        '## 4. Exception handlers need the \`SQLSTATE ''Pxxxx''\` form\n'
        '\n'
        '- **Symptom:** \`WHEN P0001 THEN\` is a syntax error in PL/pgSQL.\n'
        '- **Why:** bare condition codes aren''t valid; only named conditions or\n'
        '  \`SQLSTATE ''xxxxx''\`.\n'
        '- **Fix:** \`WHEN SQLSTATE ''P0001'' THEN ...\`. Caught in the V167 guard\n'
        '  battery.\n'
        '\n'
        '## 5. Explicit \`BEGIN;\` in a migration file + autocommit driver = stranded aborted transaction\n'
        '\n'
        '- **Symptom:** after an error mid-file, every subsequent query on the\n'
        '  connection fails with \`current transaction is aborted\`; and\n'
        '  \`conn.rollback()\` is a NO-OP under autocommit, so it never clears.\n'
        '- **Why:** the file''s explicit BEGIN opened a real transaction; the\n'
        '  error aborted it; autocommit mode means psycopg2 won''t manage it.\n'
        '- **Fix:** issue an SQL \`ROLLBACK;\` from the driver, or read the file\n'
        '  and execute without autocommit. The V175/#299 E2E harness carries\n'
        '  this handling.\n'
        '\n'
        '## 6. Constraint drops leave their backing index behind\n'
        '\n'
        '- **Symptom:** after \`ALTER TABLE ... DROP CONSTRAINT roles_name_key\`,\n'
        '  a "unique index without WHERE" still exists, also named\n'
        '  \`roles_name_key\`; \`DROP INDEX\` then fails because it is\n'
        '  constraint-owned.\n'
        '- **Why:** PostgreSQL keeps the constraint''s index under the same\n'
        '  name.\n'
        '- **Fix:** never heuristic-detect "unique on column X"; use a precise\n'
        '  \`pg_index\` predicate pinning the exact column set and the\n'
        '  constraint/index distinction. Two bugs in V175''s first draft came\n'
        '  from loose detection (one false-positived on the PRIMARY KEY).\n'
        '\n'
        '## 7. View-write trap on bitemporal current-row views\n'
        '\n'
        '- **Symptom:** \`UPDATE nebula.roles SET ...\` reports \`UPDATE 0\`\n'
        '  silently — no error, no change.\n'
        '- **Why:** the view''s predicate (\`now() < valid_until\`) excludes the\n'
        '  row the moment its snapshot closes mid-transaction.\n'
        '- **Fix:** write the HISTORY table (\`nebula.roles_history\`), never the\n'
        '  view, for close-then-insert. Grant-template Lesson 1.\n'
        '\n'
        '## 8. \`ON CONFLICT\` needs a real unique constraint\n'
        '\n'
        '- **Symptom:** \`there is no unique or exclusion constraint matching\n'
        '  the ON CONFLICT specification\`.\n'
        '- **Why:** e.g. the \`role_memory\` association table in the tackle schema\n'
        '  (as of 2026-09-17) had no unique constraint on \`(memory_id, role)\` —\n'
        '  duplicates would be accepted silently. (Card bodies must never spell\n'
        '  schema-qualified table names as unbroken tokens — see the seed-guard\n'
        '  card-body constraint.)\n'
        '- **Fix:** \`WHERE NOT EXISTS (...)\` guard for the insert AND file the\n'
        '  missing-constraint as a schema gap (V178 candidate). Flagged in\n'
        '  R2 78a891ff.\n'
        '\n'
        '## 9. Statement triggers cannot reference NEW/OLD\n'
        '\n'
        '- **Symptom:** \`NEW\` is unknown in a FOR EACH STATEMENT trigger.\n'
        '- **Fix:** \`REFERENCING NEW TABLE AS inserted_rows\` transition tables,\n'
        '  then aggregate over them (the V156 pattern). Use statement triggers\n'
        '  for audit (one row per statement, not per row).\n'
        '\n'
        '## 10. DROP VIEW ... CASCADE has a silent blast radius\n'
        '\n'
        '- **Symptom:** applying a migration kills unrelated views — consumers\n'
        '  break at runtime, not at apply time.\n'
        '- **Why:** CASCADE drops every dependent, transitively. V171''s first\n'
        '  apply took down \`nebula.plans_by_status\` (conduit-mcp''s own\n'
        '  consumer surface) with it.\n'
        '- **Fix:** enumerate dependents BEFORE the drop\n'
        '  (\`pg_depend\` / \`information_schema.view_table_usage\`), quiesce the\n'
        '  surface (long-transaction check), wrap with \`statement_timeout\`,\n'
        '  restore the full dependent set in the same migration.\n'
        '\n'
        '## 11. SAVEPOINT needs an explicit transaction\n'
        '\n'
        '- **Symptom:** \`SAVEPOINT can only be used in transaction blocks\` on\n'
        '  an autocommit connection.\n'
        '- **Fix:** run guard-probe batteries inside one \`BEGIN; ... ROLLBACK;\`\n'
        '  with nested exception handlers (the V167 battery pattern), not\n'
        '  per-statement savepoints.\n'
        '\n'
        '## 12. jsonb scalar strings masquerade as objects\n'
        '\n'
        '- **Symptom:** \`details->>''k''\` errors or returns nothing on rows you\n'
        '  expect to be objects.\n'
        '- **Why:** pre-V155-era rows may carry a jsonb SCALAR (a quoted\n'
        '  string), not an object.\n'
        '- **Fix:** \`details::text\` never fails — diagnose with it first, and\n'
        '  check \`jsonb_typeof(details)\`. (The V167 "malformed details" false\n'
        '  alarm was finally disproven this way.)\n'
        '\n'
        '## 13. Engine drift: PG17-only parameters in fresh dumps\n'
        '\n'
        '- **Symptom:** \`unrecognized configuration parameter\n'
        '  "transaction_timeout"\` applying a dump on postgres:16 — while it\n'
        '  works locally.\n'
        '- **Why:** dumps generated from a PG17 server carry PG17-only\n'
        '  statements.\n'
        '- **Fix:** engine-pin CI services to the LIVE engine (postgres:17 —\n'
        '  the #300 lesson, now house convention). The born-repaired bootstrap\n'
        '  class exists precisely to surface this coupling.\n'
        '\n'
        '## 14. psql meta-commands and Python escapes in dump handling\n'
        '\n'
        '- **Symptom:** psycopg2 chokes on \`\\restrict\` / \`\\unrestrict\` lines\n'
        '  (pg_dump 16+ tokens); separately, a Python docstring containing\n'
        '  \`\\unrestrict\` raises \`SyntaxError: (unicode error) ''unicodeescape''\`\n'
        '  because \`\\u\` starts an escape in a non-raw string.\n'
        '- **Fix:** strip meta-command lines when executing dumps via\n'
        '  psycopg2; make docstrings that quote them raw strings. (Both caught\n'
        '  in the #300 bootstrap-class work.)\n'
        '\n'
        '## 15. Optional-dependency imports are not hermetic — inject the boundary\n'
        '\n'
        '- **Symptom:** a test class labelled "hermetic" exercises a resolver whose\n'
        '  live path does a deferred \`import psycopg2\`. It passes on dev machines\n'
        '  (driver installed) and fails on CI (driver absent) — or worse, passes on\n'
        '  CI *for the wrong reason*: the ImportError-derived exception is the one\n'
        '  the test "expected", without the discipline under test ever running.\n'
        '  #308''s wr-conf-031 first CI run failed exactly this way (11s,\n'
        '  \`RuntimeError: psycopg2 required for the live probe\` instead of a\n'
        '  missing-role verdict; the unreachable-DB test had been passing via the\n'
        '  same accidental path).\n'
        '- **Why:** hermeticity is a property of the **dependency graph**, not of\n'
        '  the test''s intent. A deferred import inside the function under test is\n'
        '  still a mandatory dependency on the test path — CI only proves it later\n'
        '  and more expensively. A test that needs an optional dependency is not\n'
        '  hermetic.\n'
        '- **Fix:** inject the dependency boundary. Give the live path an optional\n'
        '  \`connect\` factory parameter (dsn -> connection) with the real driver as\n'
        '  the deferred default; pin the discipline (row-mapping,\n'
        '  missing-role-is-absence, failure-raises) against a fake factory that\n'
        '  imports nothing; exercise the real driver path explicitly in the live\n'
        '  demonstration run. The unreachable-DB test must fail for the *intended*\n'
        '  reason (connection error), never an import error.\n'
        '  (Worked example: \`make_live_resolver(dsn_env, connect=None)\` in\n'
        '  \`bin/attestation-chain-demo.py\`, PR #308 fixes 6f46641e -> 29558da6.)\n'
        '\n'
        '## 16. The E2E skeleton is not the live schema — drift refuses at apply time\n'
        '\n'
        '- **Symptom:** an apply path fully green on its throwaway-DB suite fails on\n'
        '  live with a NOT NULL violation (\`column "level_filter_primary" violates\n'
        '  not-null constraint\`), atomically rolled back. Wave-2''s six grant events\n'
        '  were all refused on first attempt exactly this way (2026-09-17, txids\n'
        '  never issued): the E2E skeleton allowed NULL level filters, while live\n'
        '  carries NOT NULL columns seeded \`<= 4\` by the original roles batch.\n'
        '- **Why:** a suite proves logic against the world *it builds*; it cannot\n'
        '  see constraint drift between its skeleton and live. Columns added,\n'
        '  constrained, or seeded on live after the skeleton was written are\n'
        '  invisible to the suite — the divergence surfaces at apply time, under\n'
        '  the operator go, with the audit trail watching.\n'
        '- **Fix:** make apply paths pre-flight the live shape they depend on\n'
        '  (\`information_schema.columns\` checks for every column the file touches,\n'
        '  refusing with a named diff before BEGIN), or give the suite a drift\n'
        '  check comparing skeleton columns against live''s for the tables it\n'
        '  exercises. Structural prevention beats either check: write grant files\n'
        '  with the house **carry-forward semantics** (non-granted columns inherit\n'
        '  the closed row''s values — commit ee2c6961) so they are robust to this\n'
        '  drift by construction. See pitfall 17 for the carry-forward''s PL/pgSQL\n'
        '  trap.\n'
        '\n'
        '## 17. PL/pgSQL DECLARE defaults evaluate at block entry — not lazily\n'
        '\n'
        '- **Symptom:** the Wave-2 carry-forward fix first placed the assignments\n'
        '  in the DECLARE block (\`v_level_primary timestamptz DEFAULT\n'
        '  v_closed.level_filter_primary\`), referencing a variable a later step\n'
        '  populates via SELECT INTO. The suite refused the shape: the carried\n'
        '  values were NULL — silently, not by exception.\n'
        '- **Why:** PL/pgSQL evaluates DECLARE-block default expressions **once,\n'
        '  at block entry, in declaration order** — not lazily at first use and\n'
        '  not re-evaluated after later statements populate the referenced\n'
        '  variables. A default that reads a variable assigned in the body\n'
        '  captures whatever that variable held at entry (NULL), and the row\n'
        '  ships with silent NULLs instead of the intended carry-forward.\n'
        '- **Fix:** assignments that depend on data loaded later belong in the\n'
        '  **body, after the SELECT INTO** — never in DECLARE defaults. The suite\n'
        '  refusing the DECLARE form and passing the body form (wr-conf-030,\n'
        '  4/4 after the fix) is the enforcement working; keep that assertion in\n'
        '  every grant-file suite.\n'
        '\n'
        '## 18. psql renders booleans as t/f — \`boolean::text\` yields ''true''/''false''\n'
        '\n'
        '- **Symptom:** the boot-shim attestation scan (PR #311) read tester''s live\n'
        '  capability row and concluded \`can_verify_work_requests = FALSE\` — while the\n'
        '  row was TRUE in the database. The fetch query worked; the capability query\n'
        '  lied. No error, no warning, just a wrong answer.\n'
        '- **Why:** two different text representations of the same value. psql\n'
        '  *renders* booleans as \`t\` / \`f\` in its output, but \`boolean::text\` *casts*\n'
        '  to \`''true''\` / \`''false''\`. A client that casts with \`::text\` and compares\n'
        '  against \`''t''\` gets a silent FALSE for every TRUE row (and vice versa). The\n'
        '  mirror variant bites test adapters: Python \`str(True)\` renders \`''True''\`,\n'
        '  which matches neither \`''t''\` nor \`''true''\` — the E2E suite caught that side\n'
        '  of the same trap the same day.\n'
        '- **Fix:** never compare boolean text forms across representation boundaries.\n'
        '  Select the boolean bare and let the driver deliver a real boolean, or make\n'
        '  the form canonical and explicit: \`r.flag IS TRUE\`, or \`CASE WHEN r.flag\n'
        '  THEN ... END\`. If parsing a psql \`-At\` stream, compare against \`t\`/\`f\` and\n'
        '  do NOT add \`::text\`. Both directions are pinned as regression tests in the\n'
        '  #311 suites (hermetic \`test_attest.py\` + E2E adapter rendering via psql\n'
        '  semantics).\n'
        '- **Incident:** attest-wiring live scan misread tester''s capability until\n'
        '  fixed (R1 43d28339 → R2 e1e4b263, PR #311).\n'
        '\n'
        '## 19. A migration with BEGIN; but no COMMIT; persists nothing — and one-session E2E cannot see it\n'
        '\n'
        '**Symptom.** The apply prints full success — \`CREATE TABLE\`, \`INSERT 0 5\` — but the objects are absent afterward. Nothing errors; the migration simply never persisted.\n'
        '\n'
        '**Mechanism.** psql holds the file''s \`BEGIN;\`-opened transaction open until an explicit \`COMMIT;\`. If the file never commits, **psql rolls the whole thing back on session exit**. DDL is transactional in PostgreSQL; "it printed CREATE TABLE" means "it created it inside an open transaction", not "it committed it".\n'
        '\n'
        '**Why the E2E could not catch it (the trap).** The house throwaway-DB pattern holds **one connection for the entire test**. An uncommitted transaction is fully visible inside the session that opened it — the E2E''s verification queries see the created tables, the seeded rows, the triggers, everything. The suite goes green on a database state that evaporates the moment the session closes. Single-session E2E is *structurally* blind to this class; only a **fresh-session post-apply probe** or the lint below can catch it.\n'
        '\n'
        '**Guard.** \`bin/tests/test_migration_commit_lint.py\` (PR: commit-lint) enforces the invariant against the real corpus: a \`sql/V1*.sql\` file must not open \`BEGIN;\` without closing \`COMMIT;\`. Files with no BEGIN are legal (psql autocommit per statement — V112/V122/V136 style). The parser strips dollar-quoted bodies (a trigger function''s internal \`BEGIN...END\` inside \`$$...$$\` must not satisfy the lint) and ignores commented-out COMMITs; positive-control fixtures prove it detects the class. Wired into \`mesh-test\`.\n'
        '\n'
        '**Detection heuristic for the live incident:** "applied twice successfully, table still absent, both applies from fresh psql sessions" — that combination is this pitfall or an apply-to-the-wrong-instance question (check \`ss -ltnp\`/docker before suspecting your SQL). Here it was the transaction, not a second server.\n'
        '\n'
        '- **Incident:** V181 \`wind.node_requirements\` applied twice on live with zero persistence (R2 f00be42c disclosed it; fixed live same day; file + lint repaired in the commit-lint PR). Fix: trailing \`COMMIT;\` per the house shape (V180 is the reference).\n'
        '\n'
        '## Related\n'
        '\n'
        '- \`bin/attestation-chain-demo.py\` + wr-conf-031 — pitfall 15''s worked example (injected connect factory; PR #308)\n'
        '\n'
        '- V156 audit-family pattern (statement triggers + transition tables)\n'
        '- \`sql/grants/*\` — close-then-insert template (Lessons 1–2)\n'
        '- \`#300\` bootstrap-class lessons (engine pinning, dump handling)\n'
        '- Wave-2 grants arc (R2 57312d5f, commit ee2c6961) — pitfalls 16–17''s incident; the carry-forward template now in \`sql/grants/*\`\n'
        '\n'
        '\n'
        '## 20. A test that needs an import a sibling module happened to make is not hermetic — bare \`import unittest\` does NOT attach \`unittest.mock\`\n'
        '\n'
        '**Symptom.** The suite is green locally, red in CI with \`AttributeError: module ''unittest'' has no attribute ''mock''\` — on tests that import nothing exotic. The failure moves depending on *which files pytest ran in the same process*, not on the code under test.\n'
        '\n'
        '**Mechanism.** Python does not attach a submodule''s attribute until that submodule is imported *somewhere*. \`import unittest\` binds the name but does not execute \`unittest.mock\`. If any other test module in the same pytest process imports \`unittest.mock\` first, the attribute exists for everyone — a hidden cross-module ordering dependency. CI runs the failing file *alone* (its own workflow), so the mask is gone and the latent bug fires.\n'
        '\n'
        '**Why CI catches it (not the reverse).** CI hermeticity runs each file in isolation; the local all-files-together run is the anomaly. Same lesson class as pitfall #15 — a test that depends on a sibling''s side effect is not a test of anything.\n'
        '\n'
        '**Guard.** Import submodules explicitly at the point of use, even when the parent is already imported: \`import unittest.mock\` (or \`from unittest import mock\`). If a mock is needed only inside one helper, import there. Never rely on "something else in the process already imported it."\n'
        '\n'
        '- **Incident:** wr-conf-037 on PR #335 (calendar consolidation intake): \`test_calendar_consolidate_e2e.py\` used \`unittest.mock.patch\` with only \`import unittest\` at top; the hermetic suite in the same local pytest process masked it; CI (file run alone) failed 10/10. Fixed by explicit \`import unittest.mock\` + \`from unittest import mock\` (fix commit on #335, R2 46ac5383).\n'
        '\n'
        '\n'
        '## 21. Bitemporal history tables with composite PKs don''t get generation defaults — idempotent DDL or a generator column, or inserts die with NULL id\n'
        '\n'
        '**Symptom.** Writes into a \`*_history\` table fail with \`null value in column "id" violates not-null constraint\` — even though the twin live-surface table inserts fine.\n'
        '\n'
        '**Mechanism.** Generation defaults (\`SERIAL\`/\`GENERATED\`) attach to the *table definition*. When a history table is declared with a bare \`INTEGER NOT NULL id\` inside a composite PK \`(id, valid_from)\`, no default exists — and every audit/trigger path that mirrors a live-surface insert into history dies on the first autoincrement attempt. D1 of the losm-store chain (migration \`001_create_vision_schema.sql\`): half of incident e772b969''s write-path failure traced to exactly this.\n'
        '\n'
        '**Guard.** Two shapes: (a) \`GENERATED ALWAYS AS IDENTITY\` on the history table and leave the PK composite (id is then supplied by the writer); or (b) idempotent repair DDL — \`ALTER TABLE ... ALTER COLUMN id SET DEFAULT\` (additive, replays safe) plus a backfill trigger for rows arriving without id. Prefer (b) for chains under disposition: it repairs in place without touching PK structure.\n'
        '\n'
        '- **Incident:** losm-store \`001\` (D1) — repairs proven in closed PR #369''s standby patch; disposition: historical, ci-bootstrap is the replayable source (README banner, PR: losm-demote-banner).\n'
        '\n'
        '## 22. Recursive CTEs drop the typmod of the recursive term — cast the anchor AND the recursive column, not just the anchor\n'
        '\n'
        '**Symptom.** \`recursive query "..." column 2 has type character varying(255) in non-recursive term but type character varying overall\` — or, subtler, a silent length mismatch that only fires when the recursion deepens past the anchor''s data.\n'
        '\n'
        '**Mechanism.** A recursive CTE''s output column types come from the **anchor** term. If the anchor selects \`VARCHAR(36) path\` but the recursive term selects the working CTE''s own column, the recursive term''s type is the *unannotated* \`VARCHAR\` — and any comparison/concatenation against a length-annotated column can fail (\`recursive dag_tree\` in losm-store \`015_dag_data_model.sql\` (D3), where the dropped \`VARCHAR(36)\` typmod on \`path\` broke the recursion).\n'
        '\n'
        '**Guard.** Cast the anchor''s output explicitly AND re-cast inside the recursive term: \`SELECT path::VARCHAR(36) ... UNION ALL SELECT (parent_path || ''/'' || id)::VARCHAR(36) ...\`. The cast on the recursive term is the one people forget; the anchor cast alone does not fix it.\n'
        '\n'
        '- **Incident:** losm-store \`015\` (D3) — fix pattern in closed PR #369''s standby patch; disposition per the D1 incident ref.\n'
        '\n'
        '\n'
        '## 23. The E2E skeleton must match the live surface''s KIND — a TABLE skeleton hides a VIEW-shaped live world\n'
        '\n'
        '**Symptom.** The migration applied cleanly in CI-style E2E and then **refused to exist on live**: \`V192\`''s \`FOREIGN KEY (role) REFERENCES nebula.roles(name)\` vanished at apply — no row in \`pg_constraint\` for the table the column lives on — while the throwaway-DB suite had verified the FK working, including refusals.\n'
        '\n'
        '**Mechanism.** Live \`nebula.roles\` is a **VIEW** over \`nebula.roles_history\` (the V175 bitemporal shape). PostgreSQL cannot create a foreign key referencing a view — the DDL is simply not valid against the live world. The E2E skeleton had rebuilt \`nebula.roles\` as a **base TABLE** (the pre-V175 shape), so the FK applied fine there and the suite proved logic against a world that no longer exists. Suite-green against the wrong surface kind; the divergence surfaced only at apply time. (Second strike of the same lesson family as pitfall 16 — that one was column drift; this one is **relation-kind drift**.)\n'
        '\n'
        '**Fix.** Where the referenced surface''s kind is uncertain or has changed shape across migrations, don''t declare the FK — enforce with a **procedural integrity trigger** that resolves the surface at write time (\`to_regclass\` + SELECT on whatever \`nebula.roles\` resolves to, table or view) and raises the same SQLSTATE the FK would (\`23503\` foreign_key_violation). Client behavior is then **identical on both shapes**, and the trigger survives future shape changes a declared FK cannot. Pre-flight for migration authors: check \`relkind\` (\`r\` = table, \`v\` = view) for every surface your DDL references, not just column lists.\n'
        '\n'
        '**Rule.** A skeleton reproduces the live world''s *kinds* (table vs view vs matview), not just its columns. When live has migrated a table to a view (or back), the skeleton must migrate with it — and the pre-flight checks relkind before trusting any constraint DDL.\n'
        '\n'
        '- **Incident:** V192 apply on live (2026-09-21): declarative FK refused against the roles VIEW; fixed via trigger-enforced integrity with 23503 parity (PR #403 lineage, R2 527b0d9b).\n'
        '\n'
        '## 24. A fresh-connection-per-query exec factory is an N×connection-setup seam — one shared connection with reconnect\n'
        '\n'
        '**Symptom.** A batch fold that processes N events (here: 8,283 \`vision.calendar_events\` through the consolidation intake) takes minutes under fleet load but the same code paths are fast in isolation. No query is slow; the *pattern* is.\n'
        '\n'
        '**Mechanism.** The default exec factory opened a **new PostgreSQL connection per query**: ~20 ms of TCP+auth per event, plus multi-second stalls when the server is under fleet load — N× (setup + teardown) dwarfs every actual query. The pattern hides because each unit test sees one query, and the seam (a factory closure) looks like an implementation detail.\n'
        '\n'
        '**Fix.** Open **one shared connection** per batch run in autocommit mode, with a **crash-safe reconnect wrapper** (on \`psycopg2.OperationalError\`/\`InterfaceError\`: close, reopen, retry once; a second failure fails the run honestly). Result on the live fold: **8,283 events in 11.4 s** versus a 120 s wrapper timeout blowout. Autocommit matters here: batch jobs that checkpoint per event must not hold a transaction open across the whole run (lock accumulation, and one bad event rolls back everything — see pitfall 11 for the transaction-shape trap in the opposite direction).\n'
        '\n'
        '**Rule.** Any \`conn_factory\`-style seam must be measured at the batch''s real N before shipping. One-connection-per-batch with reconnect is the house default for folds/imports/probes; per-query connections are for one-shot CLI calls only.\n'
        '\n'
        '- **Incident:** V192 apply package verification — the boot shim''s consolidate fold blew the wrapper timeout until the connection seam was fixed (PR #403 lineage, R2 527b0d9b).\n'
        '',
        ARRAY['sql', 'postgresql', 'pitfalls', 'debugging', 'query', 'jsonb', 'migration'],
        ARRAY['sql error', 'psql error', 'operator does not exist', 'invalid input syntax', 'jsonb', 'query fails', 'update 0', 'on conflict', 'trigger syntax', 'sql pitfall'],
        '{}'
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'architect', 'builder', 'critic', 'DBA', 'devops', 'engineer', 'engineer-ii', 'planner', 'reviewer', 'topologist'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 56. To Do Forum Lifecycle Policy
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'to-do-lifecycle-policy',
        'To Do Forum Lifecycle Policy',
        'Computable lifecycle for To Do threads: the 0-8 vocabulary as transition-owned states, the 72h acknowledgment SLA (addressee session-origin), computed-on-read staleness, bidirectional supersession lineage, MUST-advance scoped to To Do threads, and coordination-checkpoint bucketing. Extends the thread-status-ratings card (pointer, not a fork).',
        '## Purpose\n'
        '\n'
        'Computable lifecycle states for To Do forum threads — the state machine the coordination blackboard and the boot-shim R16 scan read. Doctrine thread: discussions \`ac2d1382\` (policy v0.1; ratified 2026-09-21 by the architect with two hardening conditions; operator dispositions on Q1-Q3 recorded in-thread). This card EXTENDS the \`thread-status-ratings\` vocabulary — it does not fork it.\n'
        '\n'
        '## Load when\n'
        '\n'
        '- Filing, picking up, completing, or closing a To Do thread.\n'
        '- Running a lifecycle sweep over To Do threads (any sweeping role).\n'
        '- Reading or advancing coordination checkpoints for the \`todo\` item kind.\n'
        '\n'
        '## Vocabulary (the 0-8 scale, To Do meanings)\n'
        '\n'
        '| rating | label | meaning for a To Do |\n'
        '|---|---|---|\n'
        '| 0 | Posted | no role has picked it up |\n'
        '| 1 | Specified | scope pinned (addressee or author) |\n'
        '| 2 | Planned | picked up — pickup MUST be a visible comment ("taking") or the status advance itself |\n'
        '| 3 | Implemented | work done, awaiting acceptance |\n'
        '| 4 | Accepted | \`Completed: ...\` ratified (implementer marks; requester may reopen) |\n'
        '| 5 | Rejected | false alarm / wont-fix outcome |\n'
        '| 6 | Reopened | regression or incomplete; needs another pass |\n'
        '| 7 | Closed | archival wind-down (incl. superseded — see Supersession) |\n'
        '| 8 | Approved | operator "in flight" mark (interim scheme, unchanged) |\n'
        '\n'
        '## Transition ownership (invariants I1/I2)\n'
        '\n'
        '- 0 -> 1 -> 2: the addressee (or the author for scoping). Pickup without a visible comment does not count — un-commented pickups are indistinguishable from silence to every observer.\n'
        '- 2 -> 3: the executing role, via the completion reply.\n'
        '- 3 -> 4: the implementer marks 4 in the \`Completed:\` reply. The REQUESTING role owns 5 (reject) and 6 (reopen).\n'
        '- -> 7: the originating role — or any lifecycle sweep that attaches its evidence record id.\n'
        '- No role closes another role''s binding-domain outcome without a thread (I1). Sweeps close nothing silently: every automated close cites its evidence record.\n'
        '\n'
        '## Acknowledgment SLA (ratified Q1)\n'
        '\n'
        'A To Do addressed to a role must show pickup (status >= 2, or a comment from the addressee''s role) within **72 hours of the later of**: thread posting or the addressee''s last session start. Past that it is **awaiting-pickup** — surfaced by the blackboard, never silently dropped. Every new session start re-surfaces the item, so the window measures attention opportunity elapsed without pickup; offline roles are not penalized by wall-clock.\n'
        '\n'
        '## Auto-stale criteria — computed on read, never stored (ratified Q3)\n'
        '\n'
        'All staleness is pure derivation over canonical surfaces (thread, comments, root-post rating, checkpoints). There is no stale flag to write, so staleness cannot drift from reality (blackboard hardening point 1: no new write paths).\n'
        '\n'
        '- **STALE-UNACKED**: rating <= 0, no comment by any addressee-role author, age > 14d.\n'
        '- **STALE-SUPERSEDED**: carries a supersession pointer (see Supersession) — or cites a plan/PR/migration that a merged artifact explicitly superseded.\n'
        '- **STALE-ORPHANED**: routed to a role that no longer exists in the ratified vocabulary — retarget or close.\n'
        '- **COMPLETE-UNMARKED** (the V133 failure mode): the thread''s own verify conditions demonstrably pass on live per an attached verification record, but rating < 4 for > 7d — any role may then post the evidence and mark 4, pinging the original addressee. **Verifier duty (hardening 2): the marking role must have actually performed the verification — evidence means a verification record id or PR/commit, per tester-attestation doctrine (R8). Completion via evidence, never assertion.**\n'
        '- **30d escalation**: STALE-UNACKED past 30d becomes a visible escalation in the operator''s blackboard view — visibility only, never auto-close; deliberate close stays a deliberate agent act with evidence.\n'
        '\n'
        '## Supersession — bidirectional lineage, never a silent delete\n'
        '\n'
        '1. Comment on the old thread: \`Superseded by <thread-id / PR / record id> — <one-line why>\` with \`statusRating: 7\` (or 4 if the superseding work already fulfilled the intent).\n'
        '2. The superseding artifact links back: \`supersedes <thread-id>\`.\n'
        '\n'
        'Both directions are mandatory — a forward pointer without a back pointer is a half-trail. Sweep-generated closes cite the evidence record that justified them.\n'
        '\n'
        '## Status-advance-in-gesture — MUST on To Do threads only (hardening 1)\n'
        '\n'
        'Every substantive reply on a To Do thread MUST advance the parent status (the \`statusRating\` param on the comment POST). The thread-status-ratings SHOULD for non-To-Do threads is unchanged — no status-advance noise leaks onto discussions. Reason: the boot-shim R16 scan and the blackboard read the rating; an un-advanced thread is invisible to the attention filter. Un-advanced status is how V133 got lost.\n'
        '\n'
        '## Checkpoint wiring (the point of it all)\n'
        '\n'
        'Each role''s coordination checkpoints (the blackboard''s only write surface, advanced deliberately per item kind at review time) make "new since last review" exact. The blackboard view folds To Do threads into per-role buckets, all derived:\n'
        '\n'
        '- **action needed (you)**: rating 0/1 addressed to you, inside or past the SLA\n'
        '- **in flight (someone)**: rating 2/3/8\n'
        '- **verify candidate**: COMPLETE-UNMARKED hits\n'
        '- **stale (triage)**: the STALE-* set\n'
        '\n'
        'Checkpoint advance = "I have seen everything in these buckets up to T." Classification is derived (read); closing and superseding stay deliberate agent acts (write).\n'
        '\n'
        '## Anti-patterns\n'
        '\n'
        '- Commenting "done" without advancing the status — invisible to the attention filter.\n'
        '- Closing another role''s To Do without evidence, or closing a binding-domain outcome without a thread (I1/I2).\n'
        '- Marking 4 from assertion: verification evidence (record id / PR / commit) is required.\n'
        '- Superseding with a forward pointer only — the back pointer is mandatory.\n'
        '- Storing staleness anywhere: it is computed on read, or it drifts.',
        ARRAY['to-do', 'lifecycle', 'thread-status', 'messaging', 'blackboard'],
        ARRAY['filing or picking up a to-do thread', 'running a to-do lifecycle sweep', 'reading or advancing coordination checkpoints for the todo kind'],
        '{}'
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['analyst', 'analyst-ii', 'architect', 'design-synthesist', 'engineer', 'layout-mechanic', 'lead-engineer', 'operator', 'reviewer'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    -- ──────────────────────────────────────────────────────────
    -- 57. Reviewer: Merge Gate and Pipeline Review Workflow
    -- ──────────────────────────────────────────────────────────
    v_memory_id := NULL;
    INSERT INTO ${SQL}.memory (slug, title, summary, body_md, tags, triggers, mcp_tools)
    VALUES (
        'reviewer-workflow',
        'Reviewer: Merge Gate and Pipeline Review Workflow',
        'The Reviewer''s governing duty (git merge gate / PR-queue owner) plus the conduit-pipeline review steps and loop pathology.',
        '## Reviewer Workflow\n'
        '\n'
        '### 0. Merge gate (primary duty — operator-governed, 2026-09-22)\n'
        'Each turn: check the GitHub forum queue (R8.1). Merge green PRs in sequence; ping Engineering when checks fail; delay merges where prerequisites are unmet. THEN run the in-pipeline review steps below for conduit plans. The critic owns code analysis; you own the merge — act on critic findings + CI results, do not re-run the analysis.\n'
        '\n'
        '### 1. Find review work\n'
        '\`conduit-mcp_query_conduit_state\`: review work = plans with \`derivedStatus=IMPLEMENTATION\` and an open reviewer ticket. Reviewer tickets spawn automatically when a builder issues IMPLEMENTATION.\n'
        '\n'
        '### 2. Claim the ticket (session-based)\n'
        'Use \`conduit-mcp_claim_ticket\` with \`plan_id\`, \`role=reviewer\`, and your \`session_id\`. Claims are transition-audited; a fresh foreign claim is refused with holder details (takeover after staleness, or \`force=true\`). Release with \`conduit-mcp_release_ticket\` if you hand the review off.\n'
        '\n'
        '### 3. Review\n'
        'Fetch the deliverable from the IMPLEMENTATION receipt''s \`artifact_path\` — it names the artifact type ("nebula agent record …", PR, thread) and its location. Agent records: \`GET :3101/api/agent-records/{id}\`. Check each acceptance criterion against the artifact; record met/partial/unmet with evidence.\n'
        '\n'
        '### 4. Verdict (receipt-driven)\n'
        'Issue \`conduit-mcp_issue_receipt\`: \`REVIEW_PASS\` (terminal — plan completes, nothing further spawns) or \`REVIEW_REJECT\` (reviewer ticket → failed; **a new builder ticket spawns**). The summary must say *what* to change, not just that it fails. The \`REVIEW\` type is vestigial (completes no ticket) — go straight to PASS/REJECT after IMPLEMENTATION.\n'
        '\n'
        '### 5. Loop pathology (know this)\n'
        '\`REVIEW_REJECT\` routes to the **builder**, never the planner — a blueprint defect (unmeetable AC, conflated end-state) will bounce between builder and reviewer without converging. If the defect is in the blueprint, reject with an explicit instruction to seek planner revision via \`update_plan\`, and notify the planner directly. (Couples with ripple-before-blueprint, ruling 77028c99: blueprint defects should be visible at mint time via \`nebula.assess_ripple\`; the reviewer-side rejection is the backstop.)\n'
        '',
        ARRAY['reviewer', 'workflow', 'merge-gate', 'pr-queue', 'plans'],
        ARRAY['reviewer workflow', 'review plan', 'REVIEW_PASS', 'REVIEW_REJECT', 'merge gate'],
        ARRAY['conduit-mcp_query_conduit_state', 'conduit-mcp_get_plan_receipts', 'conduit-mcp_claim_ticket', 'conduit-mcp_release_ticket', 'conduit-mcp_issue_receipt']
    )
    ON CONFLICT (slug) DO NOTHING
    RETURNING id INTO v_memory_id;
    IF v_memory_id IS NOT NULL THEN
        v_roles := ARRAY['reviewer'];
        FOREACH v_role IN ARRAY v_roles LOOP
            INSERT INTO ${SQL}.role_memory (memory_id, role, as_of_dt, expiration_dt)
            VALUES (v_memory_id, v_role, NOW(), NULL);
        END LOOP;
    END IF;
    RAISE NOTICE 'Memory procedures seeded.';
END $mem$;`;
}