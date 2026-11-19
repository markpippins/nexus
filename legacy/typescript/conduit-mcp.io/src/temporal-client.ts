/**
 * Temporal Client singleton for conduit-mcp.
 *
 * Phase 3: Provides a lazy-initialized Temporal connection used by the MCP
 * server to start workflows, signal cancellation, and query status.
 *
 * Temporal address and namespace come from environment variables:
 *   TEMPORAL_ADDRESS  — default "localhost:7233"
 *   TEMPORAL_NAMESPACE — default "conduit"
 */

import { Connection, Client } from "@temporalio/client";

let _connection: Connection | null = null;
let _client: Client | null = null;

const TEMPORAL_ADDRESS =
  process.env.TEMPORAL_ADDRESS || "localhost:7233";
const TEMPORAL_NAMESPACE =
  process.env.TEMPORAL_NAMESPACE || "conduit";

/** Connect to Temporal and return a Client.  Lazy-initialized and cached. */
export async function getTemporalClient(): Promise<Client> {
  if (_client) return _client;

  _connection = await Connection.connect({
    address: TEMPORAL_ADDRESS,
    connectTimeout: 5000,
  });
  _client = new Client({
    connection: _connection,
    namespace: TEMPORAL_NAMESPACE,
  });

  console.log(
    `[temporal] Connected to ${TEMPORAL_ADDRESS} namespace=${TEMPORAL_NAMESPACE}`
  );
  return _client;
}

/**
 * Start a TestInvokeWorkflow for a given model and test prompt.
 *
 * Returns the workflow handle so the caller can track or signal it.
 * Uses "test" task queue so the worker can pick up test workflows
 * independently from plan execution workflows.
 */
export async function startTestInvokeWorkflow(
  modelId: string,
  testPrompt: string,
  sessionId: string,
): Promise<{ workflowId: string; runId: string }> {
  const client = await getTemporalClient();
  const workflowId = `test-invoke-${modelId}-${Date.now()}`;

  const handle = await client.workflow.start("TestInvokeWorkflow", {
    taskQueue: "test",
    workflowId,
    args: [modelId, testPrompt, sessionId],
  });

  return {
    workflowId,
    runId: (handle as any).runId || (await handle.describe()).runId || "",
  };
}

/**
 * Ensure the Temporal connection is established (eager-connect).
 *
 * Unlike a passive check, this triggers connect() if not yet connected.
 * Returns true if Temporal is reachable, false otherwise.
 */
export async function ensureTemporalConnected(): Promise<boolean> {
  try {
    await getTemporalClient();
    return true;
  } catch (e: any) {
    console.warn(`[temporal] Connection failed: ${e.message}`);
    return false;
  }
}

/**
 * Start a PlanExecutionWorkflow for a given plan and role.
 *
 * Returns the workflow handle so the caller can track or signal it.
 */
export async function startPlanWorkflow(
  planId: string,
  role: string,
  force: boolean = false
): Promise<{ workflowId: string; runId: string }> {
  const client = await getTemporalClient();
  const workflowId = `plan-${planId}-${role}`;

  const handle = await client.workflow.start("PlanExecutionWorkflow", {
    taskQueue: role,
    workflowId,
    args: [planId, role, force],
  });

  return {
    workflowId,
    runId: (handle as any).runId || (await handle.describe()).runId || "",
  };
}

/**
 * Signal a running workflow to cancel gracefully.
 *
 * The PlanExecutionWorkflow cancel handler calls release_ticket_activity
 * and close_session_activity, so no manual cleanup is needed on the MCP side.
 */
export async function signalWorkflowCancel(
  workflowId: string
): Promise<void> {
  const client = await getTemporalClient();
  const handle = client.workflow.getHandle(workflowId);
  await handle.signal("cancel");
}

/**
 * Query a workflow's status.
 *
 * Returns the PlanExecutionWorkflow.status() dict:
 *   { plan_id, role, current_step, cancelled }
 */
export async function queryWorkflowStatus(
  workflowId: string
): Promise<Record<string, unknown>> {
  const client = await getTemporalClient();
  const handle = client.workflow.getHandle(workflowId);
  return (await handle.query("status")) as Record<string, unknown>;
}

/**
 * List workflows matching an optional Lucene query.
 *
 * Returns an array of workflow execution summaries.
 * Each item has .workflowId, .status, .runId, .startTime, .closeTime, etc.
 *
 * Common queries:
 *   'ExecutionStatus = "Running"'
 *   'WorkflowType = "PlanExecutionWorkflow"'
 */
export async function listWorkflows(
  query?: string
): Promise<WorkflowExecutionSummary[]> {
  const client = await getTemporalClient();
  const results: WorkflowExecutionSummary[] = [];

  const opts: { query?: string } = {};
  if (query) opts.query = query;

  const iter = client.workflow.list(opts);
  for await (const wf of iter) {
    // Temporal SDK returns status as { name: string } or string depending on version
    const statusName: string =
      (wf.status && typeof wf.status === "object"
        ? (wf.status as any).name
        : wf.status) || "UNKNOWN";

    const toISO = (d: any): string | null => {
      if (!d) return null;
      return d instanceof Date ? d.toISOString() : String(d);
    };

    results.push({
      workflowId: wf.workflowId || "",
      runId: wf.runId || "",
      status: statusName,
      startTime: toISO(wf.startTime),
      closeTime: toISO(wf.closeTime),
    });
  }

  return results;
}

/**
 * Resolve a session ID to the corresponding Temporal workflow ID.
 *
 * The session's plans_processed column (JSON array) and agent_role give us
 * the planId and role needed to build the workflow ID `plan-{planId}-{role}`.
 *
 * Returns null if the session has no plans_processed or agent_role.
 */
export function sessionToWorkflowId(session: {
  plans_processed: string; // JSON array, e.g. '["0112"]'
  agent_role: string;
}): string | null {
  try {
    const plans: string[] = JSON.parse(session.plans_processed || "[]");
    if (plans.length === 0 || !session.agent_role) return null;
    return `plan-${plans[0]}-${session.agent_role}`;
  } catch {
    return null;
  }
}

// ── Types ──────────────────────────────────────────────────────────

export interface WorkflowExecutionSummary {
  workflowId: string;
  runId: string;
  status: string;
  startTime: string | null;
  closeTime: string | null;
}

export interface WorkflowCounts {
  running: number;
  completed: number;
  failed: number;
  cancelled: number;
  total: number;
}
