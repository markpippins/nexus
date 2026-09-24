/**
 * governance.ts — Governance receipts for harness-srv (side-effect-free).
 *
 * Every execution channel leaves a governance event on completion:
 *   interactive → tackle/agent_chat.py → issue_receipt
 *   worker      → conduit/execution_worker.py → db.insert_receipt
 *                (+ trg_receipt_governance on vision.receipts)
 *   cli_executor→ conduit/cli_executor.py → _emit_vision_receipt
 * The harness/Wind channel was the last unverified one: it only wrote
 * cascade.events and left zero peb.governance_events. This module closes
 * that gap by POSTing LOSM-typed receipts to the canonical conduit-mcp
 * route (the same one tackle.vision_bridge.issue_receipt uses), so the
 * trg_receipt_governance trigger records the run in peb.governance_events.
 *
 * Receipt-type chain per run (conduit validateReceipt requires valid
 * transitions — a fresh plan_id only accepts PLAN_CREATE/BLOCK/PLANNING):
 *   run start      → PLAN_CREATE      (wind_task_id is a fresh plan id)
 *   run completion → IMPLEMENTATION → REVIEW_PASS | REVIEW_REJECT
 *   watchdog kill  → BLOCK            (always-valid override receipt)
 *
 * Artifact-critique edge (plan 0016): a CRITIQUE may also follow
 * IMPLEMENTATION (the second Critic review before the Reviewer). This is
 * additive — the harness may emit CRITIQUE after IMPLEMENTATION and the
 * conduit validateReceipt resolves its position (admission vs artifact)
 * from the last non-CRITIQUE receipt. The harness itself performs no state
 * validation (pure pass-through); it relies on conduit's validateReceipt.
 *
 * All POSTs are best-effort: failures are logged, never thrown, so the
 * run result is never held hostage by the governance ledger.
 *
 * This module intentionally has NO db/redis imports — pure functions only,
 * so unit tests can load it without constructing connection clients.
 */

const CONDUIT_MCP_URL = (process.env.CONDUIT_MCP_URL || "http://localhost:3100").replace(/\/$/, "");

// Mirror of tackle.vision_bridge.issue_receipt's pass-through whitelist
// (canonical Python source): losm_ir.executor_registry
// DEFAULT_KNOWN_EXECUTORS plus the harness-internal "watchdog" executor
// (T16) that does not own DAG nodes but does issue receipts. Roles
// outside this set fall back to "builder" for the agent_role field.
const KNOWN_EXECUTORS = new Set([
  // keep in lockstep with moleculer/nexus-broker harness.worker.ts (port-canary
  // doctrine: the twins must agree or the canary diff is lying)
  "planner", "builder", "reviewer", "analyst", "analyst-ii",
  "critic", "inspector", "architect", "engineer", "engineer-ii", "engineer-iii", "lead-engineer", "devops", "topologist", "leased-builder",
  "tester",
  "watchdog",
]);

export interface GovernanceReceiptParams {
  planId: string;
  /** One of: PLAN_CREATE | IMPLEMENTATION | REVIEW_PASS | REVIEW_REJECT | BLOCK | CRITIQUE | CRITIQUE_PASS | CRITIQUE_REJECT | PLANNING | HOLD */
  type: string;
  agentRole: string;
  sessionId: string;
  summary: string;
  metadata?: Record<string, any>;
}

/**
 * Build the /vision/receipts payload (pure — unit-tested).
 */
export function buildGovernanceReceiptPayload(params: GovernanceReceiptParams): Record<string, any> {
  const { planId, type, agentRole, sessionId, summary, metadata } = params;
  const agent_role = KNOWN_EXECUTORS.has(agentRole) ? agentRole : "builder";
  return {
    id: `rec-${planId}-${type}-${Date.now()}`,
    plan_id: planId,
    type,
    agent_role,
    session_id: sessionId,
    summary,
    metadata_json: JSON.stringify({ harness_channel: true, ...metadata }),
    tokens_used: 0,
    created_at: new Date().toISOString(),
  };
}

/**
 * Best-effort POST of a governance receipt to conduit-mcp /vision/receipts.
 * Never throws. Logs success/failure so runs are auditable via the
 * harness-srv log even when the ledger is unreachable.
 */
export async function emitGovernanceReceipt(params: GovernanceReceiptParams): Promise<void> {
  const payload = buildGovernanceReceiptPayload(params);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5_000);
  try {
    const resp = await fetch(`${CONDUIT_MCP_URL}/vision/receipts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!resp.ok) {
      const body = await resp.text();
      console.warn(
        `[harness-srv] governance receipt ${params.type} for ${params.planId} rejected: HTTP ${resp.status}: ${body.slice(0, 200)}`
      );
    } else {
      console.log(
        `[harness-srv] governance receipt ${params.type} issued for ${params.planId} (session ${params.sessionId.slice(0, 8)})`
      );
    }
  } catch (err: any) {
    console.warn(
      `[harness-srv] governance receipt ${params.type} for ${params.planId} failed: ${err?.message || err}`
    );
  } finally {
    clearTimeout(timer);
  }
}
