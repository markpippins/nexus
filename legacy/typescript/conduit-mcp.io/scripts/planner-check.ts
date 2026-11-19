/**
 * Standalone Planner Check — Turn-Based Planning Check
 *
 * Queries the pipeline MCP server's /state endpoint and reports any
 * plans currently in Planning status (promoted by the user but not
 * yet elucidated or moved to Pending).
 *
 * Usage:
 *   npx tsx scripts/planner-check.ts [--json] [--url http://localhost:3100]
 *
 * Options:
 *   --json      Machine-readable JSON output (for CI / agent integration)
 *   --url       MCP server base URL (default: http://localhost:3100)
 *
 * Environment:
 *   MCP_URL    Override default server URL (same as --url flag)
 *
 * Exit codes:
 *   0 = No planning plans (safe to proceed with user request)
 *   1 = Planning plans found (agent should pause and present them)
 *   2 = Connection error (server unreachable — proceed, but warn)
 */

const SERVER_URL = process.env.MCP_URL || 'http://localhost:3100';

interface PlanCard {
  planNumber: string;
  title: string;
  goal: string;
  project: string;
  fileName: string;
  filesAffected: string[];
  acceptanceCriteria: string[];
  dependencies: string[];
  promptRef?: string;
  createdAt?: string;
}

interface PipelineState {
  plans: {
    proposed: PlanCard[];
    planning: PlanCard[];
    pending: PlanCard[];
    active: PlanCard[];
    blocked: PlanCard[];
    completed: PlanCard[];
    archived: PlanCard[];
  };
  lastUpdated: string;
}

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  const jsonMode = args.includes('--json');
  const urlIdx = args.indexOf('--url');
  const urlVal = urlIdx !== -1 ? args[urlIdx + 1] : null;
  const baseUrl =
    urlVal && !urlVal.startsWith('--') ? urlVal : SERVER_URL;

  let state: PipelineState;
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    const res = await fetch(`${baseUrl}/state`, { signal: controller.signal });
    clearTimeout(timeout);

    if (!res.ok) {
      if (jsonMode) {
        console.log(JSON.stringify({ error: `HTTP ${res.status}`, plans: [] }));
      } else {
        console.error(`\n⚠ Planner Check: Server returned HTTP ${res.status}`);
      }
      process.exit(2);
    }

    state = (await res.json()) as PipelineState;
  } catch (err: any) {
    if (jsonMode) {
      console.log(
        JSON.stringify({
          error: err.code === 'ECONNREFUSED' ? 'Server not running' : err.message,
          plans: [],
        }),
      );
    } else {
      console.error(`\n⚠ Planner Check: Cannot reach pipeline server at ${baseUrl}`);
      if (err.code === 'ECONNREFUSED') {
        console.error('  Is the MCP server running? (npm run dev)');
      }
    }
    process.exit(2);
  }

  const planning = state.plans.planning || [];

  if (planning.length === 0) {
    if (jsonMode) {
      console.log(JSON.stringify({ count: 0, plans: [], lastUpdated: state.lastUpdated }));
    }
    // Silent success — no planning plans, proceed with turn
    process.exit(0);
  }

  // Planning plans found — report them
  if (jsonMode) {
    console.log(
      JSON.stringify({
        count: planning.length,
        plans: planning.map((p) => ({
          planNumber: p.planNumber,
          title: p.title,
          goal: p.goal,
          project: p.project,
          promptRef: p.promptRef,
        })),
        lastUpdated: state.lastUpdated,
      }),
    );
  } else {
    // Human-readable output for agent consumption
    console.log('');
    console.log('═══════════════════════════════════════════');
    console.log('  📋 PLANNER: Planning Check');
    console.log('═══════════════════════════════════════════');
    console.log('');
    console.log(
      `  You have ${planning.length} plan(s) in Planning that were promoted but not yet discussed:`,
    );
    console.log('');

    for (const p of planning) {
      const goalSummary = p.goal
        ? p.goal.length > 100
          ? p.goal.slice(0, 100) + '…'
          : p.goal
        : '(no goal)';

      console.log(`  ─────────────────────────────────────`);
      console.log(`  Plan:  #${p.planNumber}`);
      console.log(`  Title: ${p.title || '(untitled)'}`);
      console.log(`  Goal:  ${goalSummary}`);
      if (p.project) console.log(`  Proj:  ${p.project}`);
      if (p.promptRef) console.log(`  From:  Prompt #${p.promptRef}`);
      console.log('');
    }

    console.log('  ─────────────────────────────────────');
    console.log('');
    console.log('  Next: Discuss these plans with the user before proceeding.');
    console.log('  ■ Present the plans and ask: "Would you like to discuss');
    console.log('    any of these before we continue?"');
    console.log('  ■ If yes → elucidate (files, criteria, deps) → PLAN_CREATE');
    console.log('  ■ If no  → proceed with the user\'s request');
    console.log('');
    console.log('═══════════════════════════════════════════');
    console.log('');

    // Also print the pipeline snapshot for context
    const s = state.plans;
    console.log(`  Pipeline snapshot: proposed=${s.proposed.length} planning=${s.planning.length} pending=${s.pending.length} active=${s.active.length} blocked=${s.blocked.length} completed=${s.completed.length}`);
    console.log('');
  }

  process.exit(1);
}

main().catch((err) => {
  console.error('Unexpected error:', err);
  process.exit(2);
});
