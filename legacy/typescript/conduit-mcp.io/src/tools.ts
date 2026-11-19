import crypto from "crypto";
import http from "http";
import { PipelineWatcher } from "./watcher";
import { createError, createSuccess } from "./errors";
import { validate } from "./validate";
import { validateReceipt } from "./receipts";
import {
  insertReceipt,
  createTicketIfMissing,
  getPlan,
  getPlanById,
  getLatestReceiptType,
  getPlanReceipts,
  upsertPlan,
  softDeletePlan,
  checkpointWal,
  cancelTicketsByPlan,
  deleteReceiptsByPlanAndType,
  undeletePlan,
  hardDeletePlan,
} from "./db";
import fs from "fs";
import path from "path";

import {
  AgentRole,
  AgentStatus,
  PipelineMetrics,
  PlanCard,
} from "./types";
export interface MCPToolDefinition {
  name: string;
  description: string;
  inputSchema: Record<string, any>;
}

export const toolDefinitions: MCPToolDefinition[] = [
  {
    name: "query_conduit_state",
    description:
      "Returns the full conduit state JSON including all plans, builder status, and circuit breaker status",
    inputSchema: {
      type: "object",
      properties: {},
    },
  },
  {
    name: "report_plan_metadata",
    description: "Report or update metadata for a specific plan",
    inputSchema: {
      type: "object",
      properties: {
        planId: {
          type: "string",
          description: 'Plan ID (e.g., "0030")',
        },
        title: {
          type: "string",
          description: "Optional new title",
        },
        description: {
          type: "string",
          description: "Optional description",
        },
      },
      required: ["planId"],
    },
  },
  {
    name: "report_builder_status",
    description: "Report or update builder process status",
    inputSchema: {
      type: "object",
      properties: {
        status: {
          type: "string",
          description: "Builder status (running, idle, stale, killed)",
        },
        pid: {
          type: "number",
          description: "Optional PID",
        },
        note: {
          type: "string",
          description: "Optional note",
        },
      },
      required: ["status"],
    },
  },
  {
    name: "agent_heartbeat",
    description: "Report agent liveness and current activity",
    inputSchema: {
      type: "object",
      properties: {
        role: {
          type: "string",
          description:
            "Agent role (planner, builder, reviewer, critic, analyst, architect)",
        },
        state: {
          type: "string",
          description: "Agent state (idle, working, blocked)",
        },
        detail: {
          type: "string",
          description: 'Optional detail (e.g., "Executing plan 0029")',
        },
        pid: { type: "number", description: "Optional OS process ID" },
      },
      required: ["role", "state"],
    },
  },
  {
    name: "agent_finished",
    description: "Report agent has finished its current task",
    inputSchema: {
      type: "object",
      properties: {
        role: { type: "string", description: "Agent role" },
        exitCode: {
          type: "number",
          description: "Optional exit code (0=success)",
        },
        summary: { type: "string", description: "Optional summary" },
      },
      required: ["role"],
    },
  },
  {
    name: "query_analytics",
    description: "Query conduit analytics metrics",
    inputSchema: { type: "object", properties: { range: { type: "string" } } },
  },
  {
    name: "create_plan",
    description:
      "Create a new pending implementation plan in the database",
    inputSchema: {
      type: "object",
      properties: {
        title: {
          type: "string",
          description: 'Plan title (e.g., "Dark/light theme toggle")',
        },
        project: {
          type: "string",
          description: 'Project name (e.g., "conduit-ui")',
        },
        goal: { type: "string", description: "Goal description" },
        filesAffected: {
          type: "array",
          items: { type: "string" },
          description: "List of files that will be affected",
        },
        acceptanceCriteria: {
          type: "array",
          items: { type: "string" },
          description: "List of acceptance criteria",
        },
        dependencies: {
          type: "array",
          items: { type: "string" },
          description: "List of dependency plan numbers",
        },
        promptRef: {
          type: "string",
          description:
            'Optional prompt number this plan was spawned from (e.g., "0001")',
        },
      },
      required: ["title"],
    },
  },
  {
    name: "update_plan",
    description: "Update metadata for an existing plan",
    inputSchema: {
      type: "object",
      properties: {
        planNumber: {
          type: "string",
          description: 'Plan number to update (e.g., "0051")',
        },
        title: { type: "string", description: "New title" },
        project: { type: "string", description: "New project" },
        goal: { type: "string", description: "New goal description" },
        filesAffected: {
          type: "array",
          items: { type: "string" },
          description: "New files affected list",
        },
        acceptanceCriteria: {
          type: "array",
          items: { type: "string" },
          description: "New acceptance criteria",
        },
        dependencies: {
          type: "array",
          items: { type: "string" },
          description: "New dependencies",
        },
      },
      required: ["planNumber"],
    },
  },
  {
    name: "issue_receipt",
    description:
      "Record a conduit event receipt. Required for state transitions.",
    inputSchema: {
      type: "object",
      properties: {
        plan_id: { type: "string", description: 'Plan number (e.g. "0053")' },
        type: {
          type: "string",
          description:
            "PLAN_CREATE|IMPLEMENTATION|REVIEW_PASS|REVIEW_REJECT|BLOCK|PROPOSED|PLANNING|API_LIMIT|CANCELLED|ABANDONED",
        },
        agent_role: {
          type: "string",
          description: "planner|builder|reviewer|watchdog",
        },
        session_id: { type: "string", description: "Optional session ID" },
        artifact_path: {
          type: "string",
          description: "Optional path to proof artifact",
        },
        summary: { type: "string", description: "Optional one-line summary" },
        metadata: {
          type: "object",
          description: "Optional arbitrary metadata",
        },
      },
      required: ["plan_id", "type", "agent_role"],
    },
  },
  {
    name: "get_plan_receipts",
    description: "Get the full receipt chain for a plan",
    inputSchema: {
      type: "object",
      properties: {
        plan_id: { type: "string", description: 'Plan number (e.g. "0053")' },
      },
      required: ["plan_id"],
    },
  },
  {
    name: "revise_plan",
    description:
      "Create a revision copy of an existing plan in planning state. Copies title/goal/acceptance criteria but strips filesAffected (Planner will add those). Issues a PLANNING receipt on the new plan.",
    inputSchema: {
      type: "object",
      properties: {
        planNumber: {
          type: "string",
          description: 'Plan number to revise (e.g. "0053")',
        },
        title: {
          type: "string",
          description: "Optional new title (defaults to original)",
        },
        goal: { type: "string", description: "Optional updated goal" },
        acceptanceCriteria: {
          type: "array",
          items: { type: "string" },
          description: "Optional updated acceptance criteria",
        },
        dependencies: {
          type: "array",
          items: { type: "string" },
          description: "Optional updated dependencies",
        },
      },
      required: ["planNumber"],
    },
  },
  {
    name: "unblock_plan",
    description:
      "Move a blocked plan back to pending: deletes all BLOCK/PLAN_BLOCK receipts, issues a PLAN_CREATE receipt, and spawns a builder ticket so the conduit can pick it up again.",
    inputSchema: {
      type: "object",
      properties: {
        planNumber: {
          type: "string",
          description: 'Plan number to unblock (e.g. "0076")',
        },
      },
      required: ["planNumber"],
    },
  },
  {
    name: "promote_plan",
    description:
      "Promote a proposed plan to planning state. Accepts optional title/goal edits that are saved before promoting. Issues a PLANNING receipt so the Planner can elucidate and prepare it for implementation.",
    inputSchema: {
      type: "object",
      properties: {
        planNumber: {
          type: "string",
          description: 'Plan number to promote (e.g. "0067")',
        },
        title: {
          type: "string",
          description: "Optional updated title to save before promoting",
        },
        goal: {
          type: "string",
          description: "Optional updated goal to save before promoting",
        },
      },
      required: ["planNumber"],
    },
  },
  {
    name: "delete_plan",
    description:
      "Soft-delete a plan: marks it deleted in the database so it disappears from all views. Receipts and audit trail are preserved.",
    inputSchema: {
      type: "object",
      properties: {
        planNumber: {
          type: "string",
          description: 'Plan number to delete (e.g. "0053")',
        },
      },
      required: ["planNumber"],
    },
  },
  {
    name: "hard_delete_plan",
    description:
      "Permanently delete a plan and ALL associated tickets and receipts from the database. Irreversible — use only for stuck plans that cannot be recovered via unblock_plan or delete_plan. Requires confirmPlanTitle to match the plan's actual title as a safety guard.",
    inputSchema: {
      type: "object",
      properties: {
        planNumber: {
          type: "string",
          description: 'Plan number to permanently delete (e.g. "0081")',
        },
        confirmPlanTitle: {
          type: "string",
          description: 'Must match the exact plan title to confirm deletion (e.g. "Test plan with new ticket bootstrap")',
        },
      },
      required: ["planNumber", "confirmPlanTitle"],
    },
  },
  {
    name: "create_proposed_plan",
    description:
      "Create a new proposed plan with a PROPOSED receipt. Simplified form — only title, project, and goal are accepted. Files affected and acceptance criteria are deferred to planning.",
    inputSchema: {
      type: "object",
      properties: {
        title: {
          type: "string",
          description: 'Plan title (e.g., "Add dark mode toggle")',
        },
        project: {
          type: "string",
          description: 'Project name (e.g., "conduit-ui")',
        },
        goal: { type: "string", description: "Goal description" },
        promptRef: {
          type: "string",
          description:
            'Optional prompt number this idea was spawned from (e.g., "0001")',
        },
      },
      required: ["title"],
    },
  },
  {
    name: "query_nebula_backlog",
    description:
      "Query the Nebula RMS backlog — returns all requirements with their status, priority, system, and subsystem. Useful for engineers to see what work is pending. Optionally filter by status or priority.",
    inputSchema: {
      type: "object",
      properties: {
        status: {
          type: "string",
          description: 'Optional status filter (e.g., "Backlog", "InProgress", "Done")',
        },
        priority: {
          type: "string",
          description: 'Optional priority filter (e.g., "High", "Medium", "Low")',
        },
      },
    },
  },
  {
    name: "query_nebula_systems",
    description:
      "Query the Nebula RMS systems — returns the full hierarchy of systems, subsystems, features, and folders. Useful for understanding the project landscape.",
    inputSchema: {
      type: "object",
      properties: {},
    },
  },
  {
    name: "query_prompts",
    description:
      "Search captured prompts with lineage. Scans the PROMPTS directory for markdown files with YAML frontmatter and returns them as PromptEntry objects with support for search and location filters.",
    inputSchema: {
      type: "object",
      properties: {
        search: {
          type: "string",
          description: "Optional search term to filter by title, summary, or prompt number",
        },
        location: {
          type: "string",
          description: 'Optional location filter: "active" or "archived"',
        },
        project: {
          type: "string",
          description: "Optional project name filter",
        },
      },
    },
  },
  {
    name: "query_archive",
    description:
      "Search archived pipeline artifacts. Scans the audit/ARCHIVES directory for markdown files and returns them as ArchiveEntry objects with pagination, category, and date range filters.",
    inputSchema: {
      type: "object",
      properties: {
        category: { type: "string", description: "Filter by category: completed-plans, build-logs, prompts, changes" },
        search: { type: "string", description: "Free-text search across titles and summaries" },
        dateFrom: { type: "string", description: "Filter entries modified after this ISO date" },
        dateTo: { type: "string", description: "Filter entries modified before this ISO date" },
        page: { type: "number", description: "Page number (1-based), default 1" },
        pageSize: { type: "number", description: "Items per page, default 50" },
      },
    },
  },
  {
    name: "query_inspections",
    description:
      "Search inspection records. Scans the audit/INSPECTIONS directory for markdown files and returns them as InspectionEntry objects with category, status, and date range filters.",
    inputSchema: {
      type: "object",
      properties: {
        category: { type: "string", description: "Filter by category: report, error, warning, blocker-report, todo, triage" },
        status: { type: "string", description: "Filter by status: resolved, unresolved, pending" },
        search: { type: "string", description: "Free-text search across titles and summaries" },
        planRef: { type: "string", description: "Filter by associated plan number" },
        dateFrom: { type: "string", description: "Filter entries modified after this ISO date" },
        dateTo: { type: "string", description: "Filter entries modified before this ISO date" },
        page: { type: "number", description: "Page number (1-based), default 1" },
        pageSize: { type: "number", description: "Items per page, default 50" },
      },
    },
  },
  {
    name: "query_changes",
    description:
      "Search change reports. Scans the audit/CHANGES directory for markdown files and returns them as ChangeReportEntry objects with category filters.",
    inputSchema: {
      type: "object",
      properties: {
        category: { type: "string", description: "Filter by category: committed, flagged, reviewed" },
      },
    },
  },
];

export function registerToolHandlers(
  watcher: PipelineWatcher,
  emitter?: (event: any) => void,
): Record<string, Function> {
  return {
    query_conduit_state: async (_args: any) => {
      return watcher.getState();
    },
    report_plan_metadata: async (args: {
      planId: string;
      title?: string;
      description?: string;
    }) => {
      const errs = validate(args, [
        { field: "planId", type: "string", required: true },
      ]);
      if (errs.length > 0)
        throw createError("INVALID_ARGUMENTS", "Validation failed", errs);
      const updates: Record<string, any> = {};
      if (args.title !== undefined) updates.title = args.title;
      if (args.description !== undefined) updates.goal = args.description;
      const result = await watcher.updatePlanMetadata(args.planId, updates);
      return {
        acknowledged: true,
        planId: args.planId,
        updated: result.found,
        timestamp: new Date().toISOString(),
      };
    },
    report_builder_status: async (args: {
      status: string;
      pid?: number;
      note?: string;
    }) => {
      const errs = validate(args, [
        { field: "status", type: "string", required: true },
      ]);
      if (errs.length > 0)
        throw createError("INVALID_ARGUMENTS", "Validation failed", errs);
      return {
        acknowledged: true,
        status: args.status,
        timestamp: new Date().toISOString(),
      };
    },
    agent_heartbeat: async (args: {
      role: string;
      state: string;
      detail?: string;
      pid?: number;
    }) => {
      const errs = validate(args, [
        { field: "role", type: "string", required: true },
        { field: "state", type: "string", required: true },
      ]);
      if (errs.length > 0)
        throw createError("INVALID_ARGUMENTS", "Validation failed", errs);
      watcher.updateAgentHeartbeat(
        args.role as AgentRole,
        args.state as AgentStatus,
        args.detail || null,
        args.pid || null,
      );
      return { acknowledged: true, timestamp: new Date().toISOString() };
    },
    agent_finished: async (args: {
      role: string;
      exitCode?: number;
      summary?: string;
    }) => {
      const errs = validate(args, [
        { field: "role", type: "string", required: true },
      ]);
      if (errs.length > 0)
        throw createError("INVALID_ARGUMENTS", "Validation failed", errs);
      watcher.updateAgentFinished(args.role as AgentRole);
      return { acknowledged: true, timestamp: new Date().toISOString() };
    },
    query_analytics: async (_args: any) => {
      return watcher.computeAnalytics();
    },
    create_plan: async (args: {
      title: string;
      project?: string;
      goal?: string;
      filesAffected?: string[];
      acceptanceCriteria?: string[];
      dependencies?: string[];
      promptRef?: string;
    }) => {
      const errs = validate(args, [
        { field: "title", type: "string", required: true },
        { field: "filesAffected", type: "array" },
        { field: "acceptanceCriteria", type: "array" },
        { field: "dependencies", type: "array" },
      ]);
      if (errs.length > 0)
        throw createError("INVALID_ARGUMENTS", "Validation failed", errs);
      const result = await watcher.createPlan({
        title: args.title,
        project: args.project || "conduit-ui",
        goal: args.goal || "",
        filesAffected: args.filesAffected || [],
        acceptanceCriteria: args.acceptanceCriteria || [],
        dependencies: args.dependencies || [],
        promptRef: args.promptRef,
      });

      // Upsert plan into DB immediately so the FK constraint on receipts is satisfied
      const now = new Date().toISOString();
      await upsertPlan({
        id: result.planNumber,
        file_name: result.fileName,
        title: args.title,
        project: args.project || "conduit-ui",
        goal: args.goal || "",
        content: "",
        files_affected: JSON.stringify(args.filesAffected || []),
        acceptance_criteria: JSON.stringify(args.acceptanceCriteria || []),
        dependencies: JSON.stringify(args.dependencies || []),
        prompt_ref: args.promptRef || "",
        notes: "",
        priority: 0,
        created_at: now,
        updated_at: now,
      });

      // Bootstrap initial builder ticket so the conduit can pick this up
      // PLAN_CREATE means the plan is ready for execution, not planning.
      const receiptId = crypto.randomUUID();
      const ticketId = await createTicketIfMissing(
        result.planNumber,
        "builder",
        receiptId,
        now,
        args.title,
        "",
        "builder",
      );
      if (ticketId) {
        console.log(
          `[${now}] Bootstrapped builder ticket ${ticketId} for plan ${result.planNumber}`,
        );
      }

      // Issue PLAN_CREATE receipt with ticket reference
      await insertReceipt({
        id: receiptId,
        plan_id: result.planNumber,
        type: "PLAN_CREATE",
        agent_role: "planner",
        session_id: "",
        ticket_id: ticketId, // link receipt to the bootstrap ticket
        artifact_path: null,
        summary: `Created: ${args.title}`,
        metadata_json: JSON.stringify(
          args.promptRef ? { promptRef: args.promptRef } : {},
        ),
        tokens_used: 0,
        created_at: now,
      });

      checkpointWal(); // durable across abrupt restarts

      if (emitter) {
        emitter({
          type: "plan_state_changed",
          data: {
            planNumber: result.planNumber,
            planTitle: result.fileName,
            receiptType: "PLAN_CREATE",
            agentRole: "planner",
            newDerivedStatus: "PLAN_CREATE",
            timestamp: now,
          },
        });
      }

      return {
        created: true,
        planNumber: result.planNumber,
        fileName: result.fileName,
        status: "pending",
        timestamp: now,
      };
    },
    update_plan: async (args: {
      planNumber: string;
      title?: string;
      project?: string;
      goal?: string;
      filesAffected?: string[];
      acceptanceCriteria?: string[];
      dependencies?: string[];
    }) => {
      const errs = validate(args, [
        { field: "planNumber", type: "string", required: true },
        { field: "title", type: "string" },
        { field: "project", type: "string" },
        { field: "goal", type: "string" },
        { field: "filesAffected", type: "array" },
        { field: "acceptanceCriteria", type: "array" },
        { field: "dependencies", type: "array" },
      ]);
      if (errs.length > 0)
        throw createError("INVALID_ARGUMENTS", "Validation failed", errs);
      const updates: Record<string, any> = {};
      if (args.title !== undefined) updates.title = args.title;
      if (args.project !== undefined) updates.project = args.project;
      if (args.goal !== undefined) updates.goal = args.goal;
      if (args.filesAffected !== undefined)
        updates.filesAffected = args.filesAffected;
      if (args.acceptanceCriteria !== undefined)
        updates.acceptanceCriteria = args.acceptanceCriteria;
      if (args.dependencies !== undefined)
        updates.dependencies = args.dependencies;
      const result = await watcher.updatePlanMetadata(args.planNumber, updates);
      return {
        updated: result.found,
        planNumber: args.planNumber,
        timestamp: new Date().toISOString(),
      };
    },
    issue_receipt: async (args: {
      plan_id: string;
      type: string;
      agent_role: string;
      session_id?: string;
      ticket_id?: string;
      artifact_path?: string;
      summary?: string;
      metadata?: Record<string, any>;
    }) => {
      // Validate the receipt
      const validation = await validateReceipt(args.plan_id, args.type);
      if (!validation.valid) {
        return {
          issued: false,
          error: validation.error,
          plan_id: args.plan_id,
        };
      }

      // Check plan exists
      const plan = await getPlan(args.plan_id);
      if (!plan) {
        return {
          issued: false,
          error: `Plan ${args.plan_id} not found in database`,
          plan_id: args.plan_id,
        };
      }

      // ── Bootstrapping and receipt insertion ──────────────────
      // For PLAN_CREATE, create the ticket first so the receipt can
      // reference it (bi-directional link: ticket↔receipt).
      const receiptId = crypto.randomUUID();
      const now = new Date().toISOString();
      let ticketId: string | null = null;
      if (args.type === "PLAN_CREATE") {
        // Bootstrap a builder ticket so the conduit can pick this up
        ticketId = await createTicketIfMissing(
          args.plan_id,
          "builder",
          receiptId,
          now,
          args.summary || "",
          "",
          args.agent_role,
        );
        if (ticketId) {
          console.log(
            `[${now}] Bootstrapped builder ticket ${ticketId} for plan ${args.plan_id}`,
          );
        }
      }

      // Insert receipt (references the bootstrap ticket if PLAN_CREATE)
      await insertReceipt({
        id: receiptId,
        plan_id: args.plan_id,
        type: args.type,
        agent_role: args.agent_role,
        session_id: args.session_id || "",
        ticket_id: ticketId || args.ticket_id || null,
        artifact_path: args.artifact_path || null,
        summary: args.summary || "",
        metadata_json: JSON.stringify(args.metadata || {}),
        tokens_used: 0,
        created_at: now,
      });

      checkpointWal();

      // Force watcher to re-evaluate from DB receipts (not filesystem cache)
      if (args.type === "PLAN_CREATE") {
        watcher.removePlanFromMemory(args.plan_id);
      }

      // SSE: notify all connected clients that state changed
      try {
        const plan = await getPlan(args.plan_id);
        if (plan && emitter) {
          emitter({
            type: "plan_state_changed",
            data: {
              planNumber: args.plan_id,
              planTitle: plan.title,
              receiptType: args.type,
              agentRole: args.agent_role,
              newDerivedStatus: await getLatestReceiptType(args.plan_id),
              timestamp: now,
            },
          });
        }
      } catch {
        // SSE emission is best-effort
      }

      return {
        issued: true,
        receipt_id: receiptId,
        plan_id: args.plan_id,
        type: args.type,
        new_derived_status: await getLatestReceiptType(args.plan_id),
        timestamp: now,
      };
    },
    get_plan_receipts: async (args: { plan_id: string }) => {
      const receipts = await getPlanReceipts(args.plan_id);
      return {
        plan_id: args.plan_id,
        count: receipts.length,
        receipts,
      };
    },
    revise_plan: async (args: {
      planNumber: string;
      title?: string;
      goal?: string;
      acceptanceCriteria?: string[];
      dependencies?: string[];
    }) => {
      const errs = validate(args, [
        { field: "planNumber", type: "string", required: true },
        { field: "title", type: "string" },
        { field: "goal", type: "string" },
        { field: "acceptanceCriteria", type: "array" },
        { field: "dependencies", type: "array" },
      ]);
      if (errs.length > 0)
        throw createError("INVALID_ARGUMENTS", "Validation failed", errs);

      // Find the original plan
      const allPlans = (await watcher.getState()).plans;
      let original: PlanCard | undefined;
      for (const col of [
        "completed",
        "blocked",
        "pending",
        "active",
      ] as const) {
        original = allPlans[col].find(
          (p: PlanCard) => p.planNumber === args.planNumber,
        );
        if (original) break;
      }
      if (!original) {
        throw createError(
          "PLAN_NOT_FOUND",
          `Plan ${args.planNumber} not found`,
          null,
        );
      }

      // Create the revised plan (no filesAffected — Planner adds those)
      const revised = await watcher.createPlan({
        title: args.title || `[Revise] ${original.title}`,
        project: original.project || "conduit-ui",
        goal: args.goal || original.goal || "",
        filesAffected: [], // intentionally stripped
        acceptanceCriteria:
          args.acceptanceCriteria || original.acceptanceCriteria || [],
        dependencies: args.dependencies || original.dependencies || [],
        promptRef: original.promptRef, // carry forward the original prompt reference
      });

      // Upsert plan into DB so it's durable
      const now = new Date().toISOString();
      await upsertPlan({
        id: revised.planNumber,
        file_name: revised.fileName,
        title: args.title || `[Revise] ${original.title}`,
        project: original.project || "conduit-ui",
        goal: args.goal || original.goal || "",
        content: "",
        files_affected: "[]",
        acceptance_criteria: JSON.stringify(
          args.acceptanceCriteria || original.acceptanceCriteria || [],
        ),
        dependencies: JSON.stringify(
          args.dependencies || original.dependencies || [],
        ),
        prompt_ref: original.promptRef || "",
        notes: "",
        priority: 0,
        created_at: now,
        updated_at: now,
      });

      // Issue PLANNING receipt (no ticket_id — this is a revision, not a bootstrap)
      const receiptId = crypto.randomUUID();
      await insertReceipt({
        id: receiptId,
        plan_id: revised.planNumber,
        type: "PLANNING",
        agent_role: "planner",
        session_id: "",
        ticket_id: null,
        artifact_path: null,
        summary: `Revision of plan ${args.planNumber}`,
        metadata_json: JSON.stringify({ revised_from: args.planNumber }),
        tokens_used: 0,
        created_at: now,
      });
      checkpointWal();

      if (emitter) {
        emitter({
          type: "plan_state_changed",
          data: {
            planNumber: revised.planNumber,
            planTitle: revised.fileName,
            receiptType: "PLANNING",
            agentRole: "planner",
            newDerivedStatus: "PLANNING",
            revisedFrom: args.planNumber,
            timestamp: now,
          },
        });
      }

      return {
        created: true,
        planNumber: revised.planNumber,
        fileName: revised.fileName,
        revisedFrom: args.planNumber,
        status: "planning",
        timestamp: now,
      };
    },
    create_proposed_plan: async (args: {
      title: string;
      project?: string;
      goal?: string;
      promptRef?: string;
    }) => {
      const errs = validate(args, [
        { field: "title", type: "string", required: true },
        { field: "project", type: "string" },
        { field: "goal", type: "string" },
      ]);
      if (errs.length > 0)
        throw createError("INVALID_ARGUMENTS", "Validation failed", errs);

      // Create the plan in the database
      const result = await watcher.createPlan({
        title: args.title,
        project: args.project || "conduit-ui",
        goal: args.goal || "",
        filesAffected: [],
        acceptanceCriteria: [],
        dependencies: [],
        promptRef: args.promptRef,
      });

      // Upsert plan into DB immediately so the FK constraint on receipts is satisfied
      const now = new Date().toISOString();
      await upsertPlan({
        id: result.planNumber,
        file_name: result.fileName,
        title: args.title,
        project: args.project || "conduit-ui",
        goal: args.goal || "",
        content: "",
        files_affected: "[]",
        acceptance_criteria: "[]",
        dependencies: "[]",
        prompt_ref: args.promptRef || "",
        notes: "",
        priority: 0,
        created_at: now,
        updated_at: now,
      });

      // Issue PROPOSED receipt (no ticket_id — the planner will bootstrap tickets)
      const receiptId = crypto.randomUUID();
      await insertReceipt({
        id: receiptId,
        plan_id: result.planNumber,
        type: "PROPOSED",
        agent_role: "planner",
        session_id: "",
        ticket_id: null,
        artifact_path: null,
        summary: `Proposed: ${args.title}`,
        metadata_json: JSON.stringify({
          proposed_from_ui: true,
          ...(args.promptRef ? { promptRef: args.promptRef } : {}),
        }),
        tokens_used: 0,
        created_at: now,
      });
      checkpointWal(); // durable across abrupt restarts

      if (emitter) {
        emitter({
          type: "plan_state_changed",
          data: {
            planNumber: result.planNumber,
            planTitle: result.fileName,
            receiptType: "PROPOSED",
            agentRole: "planner",
            newDerivedStatus: "PROPOSED",
            timestamp: now,
          },
        });
      }

      return {
        created: true,
        planNumber: result.planNumber,
        fileName: result.fileName,
        status: "proposed",
        timestamp: now,
      };
    },
    save_prompt: async (args: {
      title: string;
      content: string;
      project?: string;
      session?: string;
    }) => {
      const errs = validate(args, [
        { field: "title", type: "string", required: true },
        { field: "content", type: "string", required: true },
        { field: "project", type: "string" },
        { field: "session", type: "string" },
      ]);
      if (errs.length > 0)
        throw createError("INVALID_ARGUMENTS", "Validation failed", errs);

      const promptsDir = path.join(watcher.baseDir, "PROMPTS");
      if (!fs.existsSync(promptsDir))
        fs.mkdirSync(promptsDir, { recursive: true });

      // Auto-increment prompt number from existing files
      let maxNum = 0;
      for (const f of fs.readdirSync(promptsDir)) {
        const m = f.match(/^(\d+)/);
        if (m) {
          const n = parseInt(m[1], 10);
          if (n > maxNum) maxNum = n;
        }
      }
      const nextNum = String(maxNum + 1).padStart(4, "0");
      const slug = args.title
        .toLowerCase()
        .replace(/[^a-z0-9\s-]/g, "")
        .trim()
        .replace(/\s+/g, "-")
        .slice(0, 50);
      const fileName = `${nextNum}-${slug}.md`;
      const filePath = path.join(promptsDir, fileName);

      const lines: string[] = [];
      lines.push("---");
      lines.push(`project: ${args.project || ""}`);
      lines.push(`session: ${args.session || ""}`);
      lines.push("---");
      lines.push(`# Prompt ${nextNum}: ${args.title}`);
      lines.push("");
      lines.push("## Summary");
      lines.push("");
      lines.push(args.content);
      lines.push("");

      fs.writeFileSync(filePath, lines.join("\n"), "utf-8");

      const now = new Date().toISOString();
      return {
        saved: true,
        promptNumber: nextNum,
        fileName,
        title: args.title,
        timestamp: now,
      };
    },

    save_response: async (args: { promptNumber: string; response: string }) => {
      const errs = validate(args, [
        { field: "promptNumber", type: "string", required: true },
        { field: "response", type: "string", required: true },
      ]);
      if (errs.length > 0)
        throw createError("INVALID_ARGUMENTS", "Validation failed", errs);

      const promptsDir = path.join(watcher.baseDir, "PROMPTS");
      if (!fs.existsSync(promptsDir)) {
        throw createError(
          "FILE_NOT_FOUND",
          "PROMPTS directory does not exist",
          null,
        );
      }

      // Find the prompt file by number prefix (exact delimiter match)
      const padded = args.promptNumber.padStart(4, "0");
      let filePath: string | null = null;
      for (const f of fs.readdirSync(promptsDir)) {
        if (!f.endsWith(".md")) continue;
        if (f.startsWith(padded + "-") || f === padded + ".md") {
          filePath = path.join(promptsDir, f);
          break;
        }
      }
      if (!filePath) {
        throw createError(
          "FILE_NOT_FOUND",
          `Prompt ${args.promptNumber} not found`,
          null,
        );
      }

      let content = fs.readFileSync(filePath, "utf-8");
      const ts = new Date().toISOString();

      // Append or replace the ## Response section
      if (content.includes("\n## Response\n")) {
        content = content.replace(
          /\n## Response\n[\s\S]*/,
          `\n## Response\n\n${args.response}\n\n---\n*Response recorded: ${ts}*\n`,
        );
      } else {
        content =
          content.trimEnd() +
          `\n\n## Response\n\n${args.response}\n\n---\n*Response recorded: ${ts}*\n`;
      }

      fs.writeFileSync(filePath, content, "utf-8");

      const now = new Date().toISOString();
      return {
        saved: true,
        promptNumber: args.promptNumber,
        timestamp: now,
      };
    },

    delete_plan: async (args: { planNumber: string }) => {
      const errs = validate(args, [
        { field: "planNumber", type: "string", required: true },
      ]);
      if (errs.length > 0)
        throw createError("INVALID_ARGUMENTS", "Validation failed", errs);

      // Use getPlanById (raw plans table) to find the plan even if it has no receipts
      const plan = await getPlanById(args.planNumber);
      if (!plan) {
        throw createError(
          "PLAN_NOT_FOUND",
          `Plan ${args.planNumber} not found`,
          null,
        );
      }

      if (plan.deleted) {
        // Cancel any non-terminal tickets for this plan so they don't
        // remain orphaned in open/claimed/stale state.
        const ticketsCancelled = await cancelTicketsByPlan(
          args.planNumber,
          "plan_deleted",
        );
        // Proactively clean the watcher's in-memory cache even if already
        // soft-deleted. This handles cases where SQL was used directly or
        // a previous delete_plan call returned early without cleanup.
        watcher.removePlanFromMemory(args.planNumber);
        const now = new Date().toISOString();
        if (emitter) {
          emitter({
            type: "plan_deleted",
            data: {
              planNumber: args.planNumber,
              planTitle: plan.title,
              wasBlocked: false,
              cleanedArtifacts: [],
              timestamp: now,
            },
          });
        }
        return {
          deleted: false,
          planNumber: args.planNumber,
          alreadyDeleted: true,
          ticketsCancelled,
          timestamp: now,
        };
      }

      // Determine if the plan is blocked (check derived status)
      const derivedStatus = await getLatestReceiptType(args.planNumber);
      const isBlocked =
        derivedStatus === "BLOCK" || derivedStatus === "PLAN_BLOCK";

      // Soft-delete in DB first
      const deleted = await softDeletePlan(args.planNumber);
      if (!deleted) {
        return {
          deleted: false,
          planNumber: args.planNumber,
          error: "Failed to soft-delete plan",
          timestamp: new Date().toISOString(),
        };
      }
      checkpointWal();

      // Cancel any non-terminal tickets so they don't remain orphaned
      // in open/claimed/stale state on the now-deleted plan.
      const ticketsCancelled = await cancelTicketsByPlan(
        args.planNumber,
        "plan_deleted",
      );

      const cleanedPaths: string[] = [];

      // Remove from in-memory plan-watcher state immediately
      watcher.removePlanFromMemory(args.planNumber);

      const now = new Date().toISOString();

      // Emit SSE event so UI removes the plan immediately
      if (emitter) {
        emitter({
          type: "plan_deleted",
          data: {
            planNumber: args.planNumber,
            planTitle: plan.title,
            wasBlocked: isBlocked,
            cleanedArtifacts: cleanedPaths,
            timestamp: now,
          },
        });
      }

      return {
        deleted: true,
        planNumber: args.planNumber,
        wasBlocked: isBlocked,
        cleanedArtifacts: cleanedPaths,
        ticketsCancelled,
        timestamp: now,
      };
    },

    hard_delete_plan: async (args: { planNumber: string; confirmPlanTitle: string }) => {
      const errs = validate(args, [
        { field: "planNumber", type: "string", required: true },
        { field: "confirmPlanTitle", type: "string", required: true },
      ]);
      if (errs.length > 0)
        throw createError("INVALID_ARGUMENTS", "Validation failed", errs);

      // Use getPlanById to find the plan even if soft-deleted
      const plan = await getPlanById(args.planNumber);
      if (!plan) {
        throw createError(
          "PLAN_NOT_FOUND",
          `Plan ${args.planNumber} not found`,
          null,
        );
      }

      // Guard: require the caller to confirm the plan title to prevent accidents
      if (args.confirmPlanTitle !== plan.title) {
        throw createError(
          "TITLE_MISMATCH",
          `Title mismatch — expected "${plan.title}", got "${args.confirmPlanTitle}". Provide the exact plan title to confirm deletion.`,
          null,
        );
      }

      // Hard-delete from DB (plan + all tickets + all receipts)
      const result = await hardDeletePlan(args.planNumber);
      checkpointWal();

      const now = new Date().toISOString();

      const cleanedPaths: string[] = [];

      // Remove from watcher's in-memory cache
      watcher.removePlanFromMemory(args.planNumber);

      console.log(
        `[${now}] hard_delete_plan: plan=${args.planNumber} ` +
          `tickets=${result.ticketsDeleted} receipts=${result.receiptsDeleted} ` +
          `files=${cleanedPaths.length}`,
      );

      // Emit SSE event so UI removes the plan immediately
      if (emitter) {
        emitter({
          type: "plan_deleted",
          data: {
            planNumber: args.planNumber,
            planTitle: plan.title,
            hardDeleted: true,
            ticketsDeleted: result.ticketsDeleted,
            receiptsDeleted: result.receiptsDeleted,
            cleanedPaths,
            timestamp: now,
          },
        });
      }

      return {
        hardDeleted: true,
        planNumber: args.planNumber,
        ticketsDeleted: result.ticketsDeleted,
        receiptsDeleted: result.receiptsDeleted,
        cleanedPaths,
        timestamp: now,
      };
    },

    promote_plan: async (args: {
      planNumber: string;
      title?: string;
      goal?: string;
    }) => {
      const errs = validate(args, [
        { field: "planNumber", type: "string", required: true },
        { field: "title", type: "string" },
        { field: "goal", type: "string" },
      ]);
      if (errs.length > 0)
        throw createError("INVALID_ARGUMENTS", "Validation failed", errs);

      const plan = await getPlan(args.planNumber);
      if (!plan) {
        throw createError(
          "PLAN_NOT_FOUND",
          `Plan ${args.planNumber} not found`,
          null,
        );
      }

      const newTitle = args.title?.trim() || plan.title;
      const newGoal = args.goal?.trim() || plan.goal;

      // Issue PLANNING receipt to promote from PROPOSED → PLANNING
      const now = new Date().toISOString();
      await upsertPlan({
        id: args.planNumber,
        file_name: plan.file_name,
        title: newTitle,
        project: plan.project,
        goal: newGoal,
        content: "",
        files_affected: plan.files_affected,
        acceptance_criteria: plan.acceptance_criteria,
        dependencies: plan.dependencies,
        prompt_ref: plan.prompt_ref || "",
        notes: plan.notes,
        priority: plan.priority,
        created_at: plan.created_at,
        updated_at: now,
      });

      const receiptId = crypto.randomUUID();
      await insertReceipt({
        id: receiptId,
        plan_id: args.planNumber,
        type: "PLANNING",
        agent_role: "planner",
        session_id: "",
        ticket_id: null,
        artifact_path: null,
        summary: "Promoted from proposed to planning",
        metadata_json: JSON.stringify({ promoted: true }),
        tokens_used: 0,
        created_at: now,
      });
      checkpointWal();

      if (emitter) {
        emitter({
          type: "plan_state_changed",
          data: {
            planNumber: args.planNumber,
            planTitle: newTitle,
            receiptType: "PLANNING",
            agentRole: "planner",
            newDerivedStatus: "PLANNING",
            timestamp: now,
          },
        });
      }

      return {
        promoted: true,
        planNumber: args.planNumber,
        newStatus: "planning",
        timestamp: now,
      };
    },

    query_nebula_backlog: async (args: { status?: string; priority?: string }) => {
      return new Promise((resolve, reject) => {
        const url = new URL("http://localhost:3101/api/requirements");
        if (args.status) url.searchParams.set("status", args.status);
        if (args.priority) url.searchParams.set("priority", args.priority);
        http.get(url.toString(), (res) => {
          let data = "";
          res.on("data", (chunk: string) => (data += chunk));
          res.on("end", () => {
            try {
              const requirements = JSON.parse(data);
              resolve({
                count: Array.isArray(requirements) ? requirements.length : 0,
                requirements: Array.isArray(requirements)
                  ? requirements.map((r: any) => ({
                      id: r.id,
                      title: r.title,
                      status: r.status,
                      priority: r.priority,
                      systemId: r.systemId,
                      subsystemId: r.subsystemId,
                      featureId: r.featureId,
                      dueDate: r.dueDate,
                      createdAt: r.createdAt,
                    }))
                  : requirements,
                filters: { status: args.status || null, priority: args.priority || null },
                timestamp: new Date().toISOString(),
              });
            } catch {
              reject(createError("PARSE_ERROR", "Failed to parse nebula response", null));
            }
          });
        }).on("error", (err: Error) => {
          reject(createError("NEBULA_UNAVAILABLE", `Cannot reach nebula-srv: ${err.message}`, null));
        });
      });
    },
    query_prompts: async (args: { search?: string; location?: string; project?: string }) => {
      const promptsDir = path.join(watcher.baseDir, "PROMPTS");

      // Return empty results if the directory doesn't exist yet
      if (!fs.existsSync(promptsDir)) {
        return { results: [], count: 0 };
      }

      const files = fs.readdirSync(promptsDir).filter((f) => f.endsWith(".md"));
      const results: any[] = [];

      for (const fileName of files) {
        const filePath = path.join(promptsDir, fileName);
        const content = fs.readFileSync(filePath, "utf-8");
        const stat = fs.statSync(filePath);
        const mtime = stat.mtime.toISOString();

        // Parse YAML frontmatter
        let project = "";
        let session = "";
        let body = content;

        const frontmatterMatch = content.match(/^---\n([\s\S]*?)\n---\n([\s\S]*)$/);
        if (frontmatterMatch) {
          const frontmatter = frontmatterMatch[1];
          body = frontmatterMatch[2];

          const projectMatch = frontmatter.match(/^project:\s*(.*)$/m);
          if (projectMatch) project = projectMatch[1].trim();

          const sessionMatch = frontmatter.match(/^session:\s*(.*)$/m);
          if (sessionMatch) session = sessionMatch[1].trim();
        }

        // Extract prompt number from filename (NNNN-*.md format)
        const numMatch = fileName.match(/^(\d{4})/);
        const promptNumber = numMatch ? numMatch[1] : "";

        // Extract title from # Prompt NNNN: Title
        let title = "";
        const titleMatch = body.match(/^#\s+Prompt\s+\d+[.:]\s*(.*)$/m);
        if (titleMatch) {
          title = titleMatch[1].trim();
        }

        // Extract summary from ## Summary section
        let summary = "";
        const summaryMatch = body.match(/^##\s+Summary\n+([\s\S]*?)(?:\n##|$)/m);
        if (summaryMatch) {
          summary = summaryMatch[1].trim();
        }

        // Extract response from ## Response section
        let response = "";
        const responseMatch = body.match(/^##\s+Response\n+([\s\S]*?)(?:\n---|$)/m);
        if (responseMatch) {
          response = responseMatch[1].trim();
        }

        // Determine location: anything in subdirs like 'archived/' or 'bak/' is archived
        const location: "active" | "archived" =
          fileName.includes("/archived/") ||
          fileName.includes("/bak/")
            ? "archived"
            : "active";

        // Build planRefs from content (links to plans)
        const planRefs: {
          planNumber: string;
          wrLabel?: string;
          title?: string;
          status?: string;
        }[] = [];
        const planRefPattern = /plan\s+#(\d{4,})/gi;
        let refMatch;
        while ((refMatch = planRefPattern.exec(content)) !== null) {
          const pn = refMatch[1];
          if (!planRefs.find((r) => r.planNumber === pn)) {
            planRefs.push({ planNumber: pn });
          }
        }

        // Build invocationOrder from ## Invocation Order section (if present)
        const invocationOrder: string[] = [];
        const invocationMatch = body.match(/^##\s+Invocation\s+Order\n+([\s\S]*?)(?:\n##|$)/m);
        if (invocationMatch) {
          const lines = invocationMatch[1]
            .split("\n")
            .map((l) => l.replace(/^-\s*/, "").trim())
            .filter(Boolean);
          invocationOrder.push(...lines);
        }

        results.push({
          path: filePath,
          fileName,
          promptNumber,
          project,
          session,
          title,
          summary,
          response,
          location,
          mtime,
          planRefs,
          invocationOrder,
          fullContent: content,
        });
      }

      // Sort by prompt number descending (newest first)
      results.sort((a, b) => {
        const na = parseInt(a.promptNumber, 10);
        const nb = parseInt(b.promptNumber, 10);
        if (isNaN(na) && isNaN(nb)) return 0;
        if (isNaN(na)) return 1;
        if (isNaN(nb)) return -1;
        return nb - na;
      });

      // Apply filters
      let filtered = results;

      if (args.search) {
        const q = args.search.toLowerCase();
        filtered = filtered.filter(
          (p) =>
            p.title.toLowerCase().includes(q) ||
            p.summary.toLowerCase().includes(q) ||
            p.promptNumber.includes(q) ||
            p.project.toLowerCase().includes(q),
        );
      }

      if (args.location) {
        filtered = filtered.filter((p) => p.location === args.location);
      }

      if (args.project) {
        filtered = filtered.filter(
          (p) => p.project.toLowerCase() === args.project!.toLowerCase(),
        );
      }

      return { results: filtered, count: filtered.length };
    },

    query_archive: async (args: {
      category?: string;
      search?: string;
      dateFrom?: string;
      dateTo?: string;
      page?: number;
      pageSize?: number;
    }) => {
      const archiveDir = path.join(watcher.baseDir, 'ARCHIVES');
      if (!fs.existsSync(archiveDir)) {
        return { results: [], total: 0, page: args.page || 1, pageSize: args.pageSize || 50 };
      }
      const files = fs.readdirSync(archiveDir).filter((f) => f.endsWith('.md'));
      const results: any[] = [];
      for (const fileName of files) {
        const filePath = path.join(archiveDir, fileName);
        const content = fs.readFileSync(filePath, 'utf-8');
        const stat = fs.statSync(filePath);
        const mtime = stat.mtime.toISOString();
        let title = '';
        const titleMatch = content.match(/^#\s+(.+?)\s*$/m);
        if (titleMatch) title = titleMatch[1].trim();
        // Simple category detection from subdirectory or filename pattern
        let category = 'build-logs';
        if (fileName.includes('plan') || fileName.includes('completed')) category = 'completed-plans';
        else if (fileName.includes('prompt')) category = 'prompts';
        else if (fileName.includes('change')) category = 'changes';
        results.push({
          path: filePath,
          fileName,
          category,
          mtime,
          size: stat.size,
          title,
          summary: content.slice(0, 300),
        });
      }
      results.sort((a, b) => new Date(b.mtime).getTime() - new Date(a.mtime).getTime());
      let filtered = results;
      if (args.category && args.category !== 'all') {
        filtered = filtered.filter((r) => r.category === args.category);
      }
      if (args.search) {
        const q = args.search.toLowerCase();
        filtered = filtered.filter((r) => r.title.toLowerCase().includes(q) || r.summary.toLowerCase().includes(q));
      }
      if (args.dateFrom) {
        filtered = filtered.filter((r) => r.mtime >= args.dateFrom!);
      }
      if (args.dateTo) {
        filtered = filtered.filter((r) => r.mtime <= args.dateTo!);
      }
      const page = args.page || 1;
      const pageSize = args.pageSize || 50;
      const start = (page - 1) * pageSize;
      const paged = filtered.slice(start, start + pageSize);
      return { results: paged, total: filtered.length, page, pageSize };
    },

    query_inspections: async (args: {
      category?: string;
      status?: string;
      search?: string;
      planRef?: string;
      dateFrom?: string;
      dateTo?: string;
      page?: number;
      pageSize?: number;
    }) => {
      const inspectionsDir = path.join(watcher.baseDir, 'INSPECTIONS');
      if (!fs.existsSync(inspectionsDir)) {
        return { results: [], total: 0, page: args.page || 1, pageSize: args.pageSize || 50 };
      }
      // Recursively scan subdirs (errors, warnings, triage, etc.)
      const results: any[] = [];
      const scanDir = (dir: string, prefix: string) => {
        if (!fs.existsSync(dir)) return;
        for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
          const fullPath = path.join(dir, entry.name);
          if (entry.isDirectory()) {
            scanDir(fullPath, path.join(prefix, entry.name));
          } else if (entry.name.endsWith('.md')) {
            const content = fs.readFileSync(fullPath, 'utf-8');
            const stat = fs.statSync(fullPath);
            let title = '';
            const titleMatch = content.match(/^#\s+(.+?)\s*$/m);
            if (titleMatch) title = titleMatch[1].trim();
            let severity: string = prefix.includes('error') ? 'error' : prefix.includes('warning') ? 'warning' : 'info';
            let status = prefix.includes('resolved') ? 'resolved' : prefix.includes('unresolved') ? 'unresolved' : 'pending';
            results.push({
              path: fullPath,
              fileName: entry.name,
              category: prefix.split(path.sep)[0] || 'report',
              mtime: stat.mtime.toISOString(),
              status,
              severity,
              title,
              planRefs: [],
              summary: content.slice(0, 300),
              fullContent: content,
            });
          }
        }
      };
      scanDir(inspectionsDir, '');
      let filtered = results;
      if (args.category && args.category !== 'all') {
        filtered = filtered.filter((r) => r.category === args.category);
      }
      if (args.status) {
        filtered = filtered.filter((r) => r.status === args.status);
      }
      if (args.search) {
        const q = args.search.toLowerCase();
        filtered = filtered.filter((r) => r.title.toLowerCase().includes(q) || r.summary.toLowerCase().includes(q));
      }
      if (args.planRef) {
        filtered = filtered.filter((r) => r.fullContent.includes(args.planRef!));
      }
      const page = args.page || 1;
      const pageSize = args.pageSize || 50;
      const start = (page - 1) * pageSize;
      const paged = filtered.slice(start, start + pageSize);
      return { results: paged, total: filtered.length, page, pageSize };
    },

    query_changes: async (args: { category?: string }) => {
      const changesDir = path.join(watcher.baseDir, 'CHANGES');
      if (!fs.existsSync(changesDir)) {
        return { results: [], count: 0 };
      }
      const results: any[] = [];
      const scanDir = (dir: string, prefix: string) => {
        if (!fs.existsSync(dir)) return;
        for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
          const fullPath = path.join(dir, entry.name);
          if (entry.isDirectory()) {
            scanDir(fullPath, path.join(prefix, entry.name));
          } else if (entry.name.endsWith('.md')) {
            const content = fs.readFileSync(fullPath, 'utf-8');
            const stat = fs.statSync(fullPath);
            let title = '';
            const titleMatch = content.match(/^#\s+(.+?)\s*$/m);
            if (titleMatch) title = titleMatch[1].trim();
            results.push({
              path: fullPath,
              fileName: entry.name,
              category: prefix.split(path.sep)[0] || 'committed',
              location: 'active',
              mtime: stat.mtime.toISOString(),
              title,
              agent: '',
              sessionId: null,
              plansProcessed: 0,
              planRefs: [],
              summary: content.slice(0, 300),
              totalTests: null,
              testsPassing: null,
              allAcceptancePassing: false,
              fullContent: content,
              newFiles: 0,
              modifyFiles: 0,
            });
          }
        }
      };
      scanDir(changesDir, '');
      let filtered = results;
      if (args.category && args.category !== 'all') {
        filtered = filtered.filter((r) => r.category === args.category);
      }
      return { results: filtered, count: filtered.length };
    },

    query_nebula_systems: async (_args: any) => {
      return new Promise((resolve, reject) => {
        http.get("http://localhost:3101/api/systems", (res) => {
          let data = "";
          res.on("data", (chunk: string) => (data += chunk));
          res.on("end", () => {
            try {
              const systems = JSON.parse(data);
              const summary = Array.isArray(systems)
                ? systems.map((s: any) => ({
                    id: s.id,
                    name: s.name,
                    description: s.description,
                    subsystemCount: s.subsystems?.length || 0,
                    featureCount: s.subsystems?.reduce(
                      (acc: number, sub: any) => acc + (sub.features?.length || 0), 0,
                    ) || 0,
                    folderCount: s.folders?.length || 0,
                  }))
                : systems;
              resolve({
                count: Array.isArray(systems) ? systems.length : 0,
                systems: summary,
                timestamp: new Date().toISOString(),
              });
            } catch {
              reject(createError("PARSE_ERROR", "Failed to parse nebula response", null));
            }
          });
        }).on("error", (err: Error) => {
          reject(createError("NEBULA_UNAVAILABLE", `Cannot reach nebula-srv: ${err.message}`, null));
        });
      });
    },
    unblock_plan: async (args: { planNumber: string }) => {
      const errs = validate(args, [
        { field: "planNumber", type: "string", required: true },
      ]);
      if (errs.length > 0)
        throw createError("INVALID_ARGUMENTS", "Validation failed", errs);

      // Find the plan (use getPlanById to include soft-deleted plans)
      const plan = await getPlanById(args.planNumber);
      if (!plan) {
        throw createError(
          "PLAN_NOT_FOUND",
          `Plan ${args.planNumber} not found`,
          null,
        );
      }

      const now = new Date().toISOString();

      // Undelete the plan if it was soft-deleted, so it reappears in views
      if (plan.deleted) {
        await undeletePlan(args.planNumber);
        console.log(
          `[${now}] unblock_plan: undeleted soft-deleted plan ${args.planNumber}`,
        );
      }

      // Delete all BLOCK / PLAN_BLOCK receipts
      const deleted = await deleteReceiptsByPlanAndType(args.planNumber, [
        "BLOCK",
        "PLAN_BLOCK",
        "CANCELLED",
        "ABANDONED",
      ]);
      console.log(
        `[${now}] unblock_plan: deleted ${deleted} BLOCK/PLAN_BLOCK receipts for plan ${args.planNumber}`,
      );

      // Cancel any orphaned tickets FIRST so the plan gets fresh tickets.
      // Must run BEFORE createTicketIfMissing — otherwise cancelTicketsByPlan
      // would cancel the newly-created open ticket (it matches status='open').
      const ticketsCancelled = await cancelTicketsByPlan(
        args.planNumber,
        "plan_unblocked",
      );
      if (ticketsCancelled > 0) {
        console.log(
          `[${now}] unblock_plan: cancelled ${ticketsCancelled} orphaned tickets for plan ${args.planNumber}`,
        );
      }

      // Issue a PLAN_CREATE receipt (moves plan back to pending)
      const receiptId = crypto.randomUUID();
      const ticketId = await createTicketIfMissing(
        args.planNumber,
        "builder",
        receiptId,
        now,
        plan.title,
        "",
        "builder",
      );
      if (ticketId) {
        console.log(
          `[${now}] unblock_plan: bootstrapped builder ticket ${ticketId} for plan ${args.planNumber}`,
        );
      }
      await insertReceipt({
        id: receiptId,
        plan_id: args.planNumber,
        type: "PLAN_CREATE",
        agent_role: "planner",
        session_id: "",
        ticket_id: ticketId,
        artifact_path: null,
        summary: `Unblocked: ${plan.title}`,
        metadata_json: JSON.stringify({ unblocked: true }),
        tokens_used: 0,
        created_at: now,
      });

      checkpointWal();

      // Remove from watcher's in-memory cache so it rescans
      watcher.removePlanFromMemory(args.planNumber);

      // SSE: notify clients
      if (emitter) {
        emitter({
          type: "plan_state_changed",
          data: {
            planNumber: args.planNumber,
            planTitle: plan.title,
            receiptType: "PLAN_CREATE",
            agentRole: "planner",
            newDerivedStatus: "PLAN_CREATE",
            timestamp: now,
          },
        });
      }

      return {
        unblocked: true,
        planNumber: args.planNumber,
        deletedBlockReceipts: deleted,
        ticketsCancelled,
        newStatus: "pending",
        timestamp: now,
      };
    },
  };
}


