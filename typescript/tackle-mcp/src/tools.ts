import { createError } from "./errors";
import * as api from "./tackle-client";

// ── Nebula RMS API helpers (HTTP calls to nebula-srv) ───────────────

const NEBULA_API = process.env.NEBULA_API_URL || "http://localhost:3101/api";

async function nebulaGet(path: string): Promise<any> {
  try {
    const res = await fetch(`${NEBULA_API}${path}`);
    if (!res.ok) {
      const body = await res.text();
      throw createError("NEBULA_ERROR", `Nebula API ${res.status}: ${body}`);
    }
    return res.json();
  } catch (err: any) {
    if (err?.error?.code === "NEBULA_ERROR") throw err;
    throw createError("NEBULA_ERROR", `Cannot reach Nebula API at ${NEBULA_API}: ${err.message}`);
  }
}

async function nebulaPost(path: string, body: any): Promise<any> {
  try {
    const res = await fetch(`${NEBULA_API}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text();
      throw createError("NEBULA_ERROR", `Nebula API ${res.status}: ${text}`);
    }
    return res.json();
  } catch (err: any) {
    if (err?.error?.code === "NEBULA_ERROR") throw err;
    throw createError("NEBULA_ERROR", `Cannot reach Nebula API at ${NEBULA_API}: ${err.message}`);
  }
}

async function nebulaPatch(path: string, body: any): Promise<any> {
  try {
    const res = await fetch(`${NEBULA_API}${path}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text();
      throw createError("NEBULA_ERROR", `Nebula API ${res.status}: ${text}`);
    }
    return res.json();
  } catch (err: any) {
    if (err?.error?.code === "NEBULA_ERROR") throw err;
    throw createError("NEBULA_ERROR", `Cannot reach Nebula API at ${NEBULA_API}: ${err.message}`);
  }
}

// ── Tool Definitions ────────────────────────────────────────────────

export interface MCPToolDefinition {
  name: string;
  description: string;
  inputSchema: Record<string, any>;
}

export const toolDefinitions: MCPToolDefinition[] = [
  {
    name: "get_ai_config",
    description: "Get the full AI configuration snapshot including all providers, harnesses, models, role configs, and role model priorities.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "validate_ai_config",
    description: "Validate the AI configuration — checks for missing references, broken harness binaries, and missing fallback models.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "seed_default_ai_config",
    description: "Seed the AI config tables with default providers, harnesses, and models if they are empty. Set force=true to overwrite existing data.",
    inputSchema: {
      type: "object",
      properties: {
        force: { type: "boolean", description: "Overwrite existing data if true" },
      },
    },
  },
  {
    name: "import_ai_config",
    description: "Replace the entire AI configuration with a full snapshot. Clears all existing data and bulk-inserts the provided providers, harnesses, models, roles, and config bundles.",
    inputSchema: {
      type: "object",
      properties: {
        providers: { type: "array", description: "Array of provider objects" },
        harnesses: { type: "array", description: "Array of harness objects" },
        models: { type: "array", description: "Array of model objects" },
        roles: { type: "array", description: "Array of role_config objects" },
        bundles: { type: "array", description: "Array of config_bundle objects" },
      },
    },
  },
  {
    name: "list_ai_providers",
    description: "List all AI providers configured in the tackle registry.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "get_ai_provider",
    description: "Get a single AI provider by ID.",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "string", description: "Provider ID (e.g. 'prov-openai')" },
      },
      required: ["id"],
    },
  },
  {
    name: "upsert_ai_provider",
    description: "Create or update an AI provider.",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "string", description: "Provider ID (e.g. 'prov-openai')" },
        name: { type: "string", description: "Display name" },
        type: { type: "string", description: "Provider type: openai, anthropic, google, ollama, opencode, codex, spring_ai, lm_server, custom" },
        endpoint_url: { type: "string", description: "API endpoint URL" },
        api_key: { type: "string", description: "API key (stored encrypted at rest)" },
        config_json: { type: "string", description: "Optional JSON config string" },
      },
      required: ["id", "name", "type"],
    },
  },
  {
    name: "delete_ai_provider",
    description: "Delete an AI provider by ID.",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "string", description: "Provider ID to delete" },
      },
      required: ["id"],
    },
  },
  {
    name: "list_ai_harnesses",
    description: "List all AI harnesses configured in the tackle registry.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "get_ai_harness",
    description: "Get a single AI harness by ID.",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "string", description: "Harness ID (e.g. 'harn-opencode')" },
      },
      required: ["id"],
    },
  },
  {
    name: "upsert_ai_harness",
    description: "Create or update an AI harness.",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "string", description: "Harness ID (e.g. 'harn-opencode')" },
        name: { type: "string", description: "Display name" },
        invocation_semantics: { type: "string", description: "JSON string describing binary, capabilities, semantics, and execution mode" },
      },
      required: ["id", "name"],
    },
  },
  {
    name: "delete_ai_harness",
    description: "Delete an AI harness by ID.",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "string", description: "Harness ID to delete" },
      },
      required: ["id"],
    },
  },
  {
    name: "list_ai_models",
    description: "List all AI models in the tackle registry.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "get_ai_model",
    description: "Get a single AI model by ID.",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "string", description: "Model ID (e.g. 'mod-gpt4o')" },
      },
      required: ["id"],
    },
  },
  {
    name: "upsert_ai_model",
    description: "Create or update an AI model.",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "string", description: "Model ID (e.g. 'mod-gpt4o')" },
        name: { type: "string", description: "Display name" },
        harness_id: { type: "string", description: "Harness ID this model uses" },
        provider_id: { type: "string", description: "Optional provider ID for API routing" },
        model_identifier: { type: "string", description: "The model identifier string (e.g. 'gpt-4o')" },
      },
      required: ["id", "name", "harness_id", "model_identifier"],
    },
  },
  {
    name: "delete_ai_model",
    description: "Delete an AI model by ID.",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "string", description: "Model ID to delete" },
      },
      required: ["id"],
    },
  },
  {
    name: "list_ai_role_configs",
    description: "List all AI role configurations.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "get_ai_role_config",
    description: "Get a single AI role configuration by role name.",
    inputSchema: {
      type: "object",
      properties: {
        role: { type: "string", description: "Role name: planner, builder, reviewer, critic, analyst, architect, inspector, engineer, engineer-ii, engineer-iii, devops, topologist, rover" },
      },
      required: ["role"],
    },
  },
  {
    name: "upsert_ai_role_config",
    description: "Create or update an AI role configuration with optional fallback model priorities.",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "string", description: "Role config ID (e.g. 'rc-builder')" },
        role: { type: "string", description: "Role name: planner, builder, reviewer, critic, analyst, architect, inspector, engineer, engineer-ii, engineer-iii, devops, topologist, rover" },
        provider_id: { type: "string", description: "Provider ID for the primary model" },
        harness_id: { type: "string", description: "Harness ID for the primary model" },
        model_id: { type: "string", description: "Primary model ID" },
        extra_params: { type: "string", description: "Optional JSON extra parameters" },
        model_priorities: {
          type: "array",
          description: "Optional fallback model priority list",
          items: {
            type: "object",
            properties: {
              model_id: { type: "string" },
              priority: { type: "number" },
              provider_id: { type: "string" },
              harness_id: { type: "string" },
            },
          },
        },
      },
      required: ["id", "role", "provider_id", "harness_id", "model_id"],
    },
  },
  {
    name: "list_config_bundles",
    description: "List config bundles for a role. Bundles are the atomic unit of model+provider+harness+invocation config.",
    inputSchema: {
      type: "object",
      properties: {
        role: { type: "string", description: "Role name" },
      },
      required: ["role"],
    },
  },
  {
    name: "upsert_config_bundle",
    description: "Create or update a config bundle — the atomic unit of model+provider+harness+invocation config.",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "string", description: "Bundle ID (e.g. 'cb-builder-mod-gpt4o')" },
        name: { type: "string", description: "Human-readable name" },
        role: { type: "string", description: "Role this bundle belongs to" },
        model_id: { type: "string", description: "Model ID" },
        provider_id: { type: "string", description: "Optional provider ID override" },
        harness_id: { type: "string", description: "Optional harness ID override" },
        priority: { type: "number", description: "Priority (0 = primary)" },
        invocation_mode: { type: "string", description: "CLI | HTTP | SDK | MCP | INTERACTIVE", enum: ["CLI", "HTTP", "SDK", "MCP", "INTERACTIVE"] },
        command: { type: "string", description: "CLI command override" },
        endpoint_url: { type: "string", description: "HTTP endpoint override" },
        timeout_ms: { type: "number", description: "Timeout in milliseconds" },
      },
      required: ["id", "name", "role", "model_id"],
    },
  },
  {
    name: "delete_config_bundle",
    description: "Delete a config bundle by ID.",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "string", description: "Bundle ID to delete" },
      },
      required: ["id"],
    },
  },

  // ── Roles Registry ────────────────────────────────────────────

  {
    name: "list_roles",
    description: "List all roles registered in the tackle roles table. Returns role metadata including id, name, and description.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "get_role",
    description: "Get a single role by ID or name from the tackle roles table.",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "string", description: "Role UUID or name (e.g. 'engineer', 'architect')" },
      },
      required: ["id"],
    },
  },

  // ── Memory Procedure Registry ─────────────────────────────────

  {
    name: "memory_get_procedures",
    description: "Return the procedure index for a given role (list of procedure summaries). Reads from Redis cache.",
    inputSchema: {
      type: "object",
      properties: {
        role: { type: "string", description: "Role name (engineer, planner, architect, etc.)" },
      },
      required: ["role"],
    },
  },
  {
    name: "memory_get_procedure",
    description: "Return the full procedure card for a given slug. Reads from Redis cache.",
    inputSchema: {
      type: "object",
      properties: {
        slug: { type: "string", description: "Procedure slug (e.g. 'handle-review-rejection')" },
      },
      required: ["slug"],
    },
  },
  {
    name: "memory_check_since",
    description: "Check whether role memory procedures have changed since a given timestamp for a specific role. Queries PostgreSQL directly.",
    inputSchema: {
      type: "object",
      properties: {
        role: { type: "string", description: "Role name" },
        since: { type: "string", description: "ISO 8601 timestamp (e.g. '2026-06-23T00:00:00Z')" },
      },
      required: ["role", "since"],
    },
  },
  {
    name: "memory_refresh",
    description: "Trigger a full PG->Redis sync on the role-memory-srv. Reads all active procedures from PostgreSQL and repopulates the Redis cache.",
    inputSchema: {
      type: "object",
      properties: {},
    },
  },

  // ── Agent Scheduler ─────────────────────────────────────────────

  {
    name: "tackle_list_scheduler_entries",
    description: "List all agent scheduler entries that define cron-scheduled agent runs.",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "tackle_get_scheduler_entry",
    description: "Get a single agent scheduler entry by ID.",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "number", description: "Scheduler entry ID" },
      },
      required: ["id"],
    },
  },
  {
    name: "tackle_create_scheduler_entry",
    description: "Create a new agent scheduler entry to schedule periodic agent runs.",
    inputSchema: {
      type: "object",
      properties: {
        role: { type: "string", description: "Agent role (builder, planner, reviewer, critic, etc.)" },
        model_id: { type: "string", description: "Model ID for opencode harness runs" },
        harness: { type: "string", description: "opencode or conduit (default opencode)", enum: ["opencode", "conduit"] },
        agent_config: { type: "string", description: "Optional JSON agent config (title, extra_args, etc.)" },
        schedule_type: { type: "string", description: "interval or cron (default interval)", enum: ["interval", "cron"] },
        schedule_value: { type: "number", description: "Interval in seconds (default 3600), or cron expression" },
        project_dir: { type: "string", description: "Working directory (default /home/codex/dev)" },
        enabled: { type: "number", description: "1 to enable, 0 to disable (default 1)" },
      },
      required: ["role"],
    },
  },
  {
    name: "tackle_update_scheduler_entry",
    description: "Update an existing agent scheduler entry.",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "number", description: "Scheduler entry ID" },
        role: { type: "string", description: "Agent role" },
        model_id: { type: "string", description: "Model ID" },
        harness: { type: "string", description: "opencode or conduit", enum: ["opencode", "conduit"] },
        agent_config: { type: "string", description: "JSON agent config" },
        schedule_type: { type: "string", description: "interval or cron", enum: ["interval", "cron"] },
        schedule_value: { type: "number", description: "Interval seconds or cron expression" },
        project_dir: { type: "string", description: "Working directory" },
        enabled: { type: "number", description: "1 enabled, 0 disabled" },
        last_run_at: { type: "string", description: "ISO timestamp of last run" },
        last_run_status: { type: "string", description: "Status of last run" },
      },
      required: ["id"],
    },
  },
  {
    name: "tackle_delete_scheduler_entry",
    description: "Delete an agent scheduler entry by ID.",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "number", description: "Scheduler entry ID" },
      },
      required: ["id"],
    },
  },

  // ── Nebula RMS — Harvest Candidates (via nebula-srv REST API) ──

  {
    name: "tackle_list_harvest_candidates",
    description: "List harvest candidates from the Nebula RMS. Each candidate is a specification extracted from a harvest pipeline run. Optionally filter by harvest, system, subsystem, or feature.",
    inputSchema: {
      type: "object",
      properties: {
        harvestId: { type: "string", description: "Filter by parent harvest UUID" },
        systemId: { type: "string", description: "Filter by linked system UUID" },
        subsystemId: { type: "string", description: "Filter by linked subsystem UUID" },
        featureId: { type: "string", description: "Filter by linked feature UUID" },
        limit: { type: "number", description: "Max results (default 100)" },
        offset: { type: "number", description: "Pagination offset" },
      },
    },
  },
  {
    name: "tackle_get_harvest_candidate",
    description: "Get a single harvest candidate with full detail from the Nebula RMS.",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "string", description: "Harvest candidate UUID" },
      },
      required: ["id"],
    },
  },
  {
    name: "tackle_update_harvest_candidate",
    description: "Update a harvest candidate — primarily to link it to a system, subsystem, or feature in the Nebula project hierarchy. Also supports setting a planRef to create a cross-reference (rel_type=spawns_plan) to a conduit plan.",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "string", description: "Harvest candidate UUID" },
        title: { type: "string", description: "New title" },
        intentDescription: { type: "string", description: "Revised intent description" },
        status: { type: "string", description: "Status (e.g. promoted, reviewed, discarded)" },
        systemId: { type: "string", description: "Link to system UUID (or null to unlink)" },
        subsystemId: { type: "string", description: "Link to subsystem UUID (or null to unlink)" },
        featureId: { type: "string", description: "Link to feature UUID (or null to unlink)" },
        tags: { type: "array", items: { type: "string" }, description: "New tags array" },
        planRef: { type: "string", description: "Conduit plan reference (e.g. '0136') — creates a cross-reference linking this candidate to the plan" },
      },
      required: ["id"],
    },
  },
  {
    name: "tackle_create_harvest_candidate",
    description: "Create a standalone harvest candidate in the Nebula RMS (e.g. manually linked to a harvest and/or project hierarchy).",
    inputSchema: {
      type: "object",
      properties: {
        harvestId: { type: "string", description: "Parent harvest UUID" },
        title: { type: "string", description: "Candidate title" },
        intentDescription: { type: "string", description: "What this candidate proposes" },
        implementationNotes: { type: "array", description: "Implementation notes array" },
        codeSnippets: { type: "array", description: "Extracted code snippets" },
        openQuestions: { type: "array", description: "Open questions raised" },
        tags: { type: "array", items: { type: "string" }, description: "Tags for filtering" },
        status: { type: "string", description: "Status string" },
        systemId: { type: "string", description: "Pre-linked system UUID" },
        subsystemId: { type: "string", description: "Pre-linked subsystem UUID" },
        featureId: { type: "string", description: "Pre-linked feature UUID" },
        planRef: { type: "string", description: "Conduit plan reference — creates spawns_plan cross-reference" },
      },
      required: ["harvestId", "title"],
    },
  },
  {
    name: "tackle_spawn_plan_from_candidate",
    description: "Full spawn-plan flow in the Nebula RMS: link a harvest candidate to a system/subsystem, create a requirement derived from the candidate's title and intent, auto-upsert a harvest_context info tab, and optionally cross-reference a conduit plan — all in one atomic transaction. This is the primary tool for turning harvest specifications into actionable work.",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "string", description: "Harvest candidate UUID" },
        systemId: { type: "string", description: "System UUID to link the candidate to" },
        subsystemId: { type: "string", description: "Optional subsystem UUID — requirement can live at system level" },
        featureId: { type: "string", description: "Optional feature UUID" },
        planRef: { type: "string", description: "Optional conduit plan reference (e.g. '0136') — creates spawns_plan cross-reference" },
        priority: { type: "string", description: "Requirement priority: Low, Medium, High (default Medium)" },
        status: { type: "string", description: "Requirement status (default Backlog)" },
        title: { type: "string", description: "Requirement title (defaults to candidate title)" },
        description: { type: "string", description: "Requirement description (defaults to candidate intent)" },
      },
      required: ["id", "systemId"],
    },
  },
  {
    name: "tackle_get_system_harvest_candidates",
    description: "List all harvest candidates linked to a specific system in the Nebula RMS hierarchy.",
    inputSchema: {
      type: "object",
      properties: {
        systemId: { type: "string", description: "System UUID" },
      },
      required: ["systemId"],
    },
  },
  {
    name: "tackle_get_plan_candidates",
    description: "Reverse lookup: find all harvest candidates linked to a conduit plan via cross_references (rel_type=spawns_plan) in the Nebula RMS.",
    inputSchema: {
      type: "object",
      properties: {
        planRef: { type: "string", description: "Conduit plan reference (e.g. '0136')" },
      },
      required: ["planRef"],
    },
  },
  {
    name: "tackle_list_systems",
    description: "List all systems from the Nebula RMS with their full hierarchy (subsystems, features, folders). Useful for finding system/subsystem IDs for spawn-plan operations.",
    inputSchema: {
      type: "object",
      properties: {},
    },
  },
  {
    name: "tackle_list_requirements",
    description: "List requirements from the Nebula RMS, optionally filtered by system, subsystem, or feature.",
    inputSchema: {
      type: "object",
      properties: {
        systemId: { type: "string", description: "Filter by system UUID" },
        subsystemId: { type: "string", description: "Filter by subsystem UUID" },
        featureId: { type: "string", description: "Filter by feature UUID" },
      },
    },
  },
];

// ── Tool Handler Registration ───────────────────────────────────────

export function registerToolHandlers(): Record<string, Function> {
  return {
    get_ai_config: async (_args: any) => {
      return await api.getAIConfigSnapshot();
    },

    validate_ai_config: async (_args: any) => {
      const result = await api.validateAIConfig();
      return result;
    },

    seed_default_ai_config: async (args: { force?: boolean }) => {
      return await api.seedDefaultAIConfig(!!args.force);
    },

    import_ai_config: async (args: {
      providers?: any[];
      harnesses?: any[];
      models?: any[];
      roles?: any[];
      bundles?: any[];
    }) => {
      if (!args.providers && !args.harnesses && !args.models && !args.roles) {
        throw createError("INVALID_ARGUMENTS", "No import data provided");
      }
      return await api.importAIConfig({
        providers: args.providers || [],
        harnesses: args.harnesses || [],
        models: args.models || [],
        roles: args.roles || [],
        bundles: args.bundles || [],
      });
    },

    list_ai_providers: async (_args: any) => {
      const providers = await api.getAIProviders();
      return { count: providers.length, providers };
    },

    get_ai_provider: async (args: { id: string }) => {
      if (!args.id) throw createError("INVALID_ARGUMENTS", "id is required");
      const provider = await api.getAIProvider(args.id);
      if (!provider) throw createError("NOT_FOUND", `Provider '${args.id}' not found`);
      return provider;
    },

    upsert_ai_provider: async (args: {
      id: string; name: string; type: string;
      endpoint_url?: string; api_key?: string; config_json?: string;
    }) => {
      if (!args.id || !args.name || !args.type) {
        throw createError("INVALID_ARGUMENTS", "id, name, and type are required");
      }
      const validTypes = ["openai","anthropic","google","ollama","opencode","codex","spring_ai","lm_server","custom"];
      if (!validTypes.includes(args.type)) {
        throw createError("INVALID_ARGUMENTS", `Invalid provider type '${args.type}'. Must be one of: ${validTypes.join(", ")}`);
      }
      await api.upsertAIProvider(args as any);
      return { saved: true, id: args.id };
    },

    delete_ai_provider: async (args: { id: string }) => {
      if (!args.id) throw createError("INVALID_ARGUMENTS", "id is required");
      await api.deleteAIProvider(args.id);
      return { deleted: true, id: args.id };
    },

    list_ai_harnesses: async (_args: any) => {
      const harnesses = await api.getAIHarnesses();
      return { count: harnesses.length, harnesses };
    },

    get_ai_harness: async (args: { id: string }) => {
      if (!args.id) throw createError("INVALID_ARGUMENTS", "id is required");
      const harness = await api.getAIHarness(args.id);
      if (!harness) throw createError("NOT_FOUND", `Harness '${args.id}' not found`);
      return harness;
    },

    upsert_ai_harness: async (args: {
      id: string; name: string; invocation_semantics?: string;
    }) => {
      if (!args.id || !args.name) {
        throw createError("INVALID_ARGUMENTS", "id and name are required");
      }
      await api.upsertAIHarness(args);
      return { saved: true, id: args.id };
    },

    delete_ai_harness: async (args: { id: string }) => {
      if (!args.id) throw createError("INVALID_ARGUMENTS", "id is required");
      await api.deleteAIHarness(args.id);
      return { deleted: true, id: args.id };
    },

    list_ai_models: async (_args: any) => {
      const models = await api.getAIModels();
      return { count: models.length, models };
    },

    get_ai_model: async (args: { id: string }) => {
      if (!args.id) throw createError("INVALID_ARGUMENTS", "id is required");
      const model = await api.getAIModel(args.id);
      if (!model) throw createError("NOT_FOUND", `Model '${args.id}' not found`);
      return model;
    },

    upsert_ai_model: async (args: {
      id: string; name: string; harness_id: string;
      provider_id?: string; model_identifier: string;
    }) => {
      if (!args.id || !args.name || !args.harness_id || !args.model_identifier) {
        throw createError("INVALID_ARGUMENTS", "id, name, harness_id, and model_identifier are required");
      }
      await api.upsertAIModel(args);
      return { saved: true, id: args.id };
    },

    delete_ai_model: async (args: { id: string }) => {
      if (!args.id) throw createError("INVALID_ARGUMENTS", "id is required");
      await api.deleteAIModel(args.id);
      return { deleted: true, id: args.id };
    },

    list_ai_role_configs: async (_args: any) => {
      const roles = await api.getAIRoleConfigs();
      return { count: roles.length, roles };
    },

    get_ai_role_config: async (args: { role: string }) => {
      if (!args.role) throw createError("INVALID_ARGUMENTS", "role is required");
      const rc = await api.getAIRoleConfig(args.role);
      if (!rc) throw createError("NOT_FOUND", `Role config '${args.role}' not found`);
      return rc;
    },

    upsert_ai_role_config: async (args: {
      id: string; role: string; provider_id: string;
      harness_id: string; model_id: string;
      extra_params?: string; bundles?: any[];
    }) => {
      if (!args.id || !args.role || !args.provider_id || !args.harness_id || !args.model_id) {
        throw createError("INVALID_ARGUMENTS", "id, role, provider_id, harness_id, and model_id are required");
      }
      return await api.upsertAIRoleConfig(args);
    },

    list_config_bundles: async (args: { role: string }) => {
      if (!args.role) throw createError("INVALID_ARGUMENTS", "role is required");
      const bundles = await api.getConfigBundles(args.role);
      return { role: args.role, count: bundles.length, bundles };
    },

    upsert_config_bundle: async (args: {
      id: string; name: string; role: string; model_id: string;
      provider_id?: string; harness_id?: string; priority?: number;
      invocation_mode?: string; command?: string; endpoint_url?: string; timeout_ms?: number;
    }) => {
      if (!args.id || !args.name || !args.role || !args.model_id) {
        throw createError("INVALID_ARGUMENTS", "id, name, role, and model_id are required");
      }
      await api.upsertConfigBundle({
        ...args,
        invocation_mode: (args.invocation_mode || "CLI") as "CLI" | "HTTP" | "SDK" | "MCP" | "INTERACTIVE",
      });
      return { saved: true, id: args.id };
    },

    delete_config_bundle: async (args: { id: string }) => {
      if (!args.id) throw createError("INVALID_ARGUMENTS", "id is required");
      await api.deleteConfigBundle(args.id);
      return { deleted: true, id: args.id };
    },

    // ── Roles Registry ──────────────────────────────────────────

    list_roles: async (_args: any) => {
      const result = await api.getRoles();
      return result;
    },

    get_role: async (args: { id: string }) => {
      if (!args.id) throw createError("INVALID_ARGUMENTS", "id is required");
      const role = await api.getRole(args.id);
      if (!role) throw createError("NOT_FOUND", `Role '${args.id}' not found`);
      return role;
    },

    // ── Memory Procedure Registry ───────────────────────────────

    memory_get_procedures: async (args: { role: string }) => {
      if (!args.role) throw createError("INVALID_ARGUMENTS", "role is required");
      const result = await api.getProceduresForRole(args.role);
      return result;
    },

    memory_get_procedure: async (args: { slug: string }) => {
      if (!args.slug) throw createError("INVALID_ARGUMENTS", "slug is required");
      const card = await api.getProcedureBySlug(args.slug);
      if (!card) throw createError("NOT_FOUND", `Procedure '${args.slug}' not found`);
      return card;
    },

    memory_refresh: async (_args: any) => {
      return await api.triggerMemoryRefresh();
    },

    // ── Agent Scheduler ──────────────────────────────────────────

    tackle_list_scheduler_entries: async (_args: any) => {
      const result = await api.listSchedulerEntries();
      return result;
    },

    tackle_get_scheduler_entry: async (args: { id: number }) => {
      if (args.id == null) throw createError("INVALID_ARGUMENTS", "id is required");
      const entry = await api.getSchedulerEntry(args.id);
      if (!entry) throw createError("NOT_FOUND", `Scheduler entry '${args.id}' not found`);
      return entry;
    },

    tackle_create_scheduler_entry: async (args: {
      role: string; model_id?: string; harness?: string;
      agent_config?: string; schedule_type?: string; schedule_value?: number;
      project_dir?: string; enabled?: number;
    }) => {
      if (!args.role) throw createError("INVALID_ARGUMENTS", "role is required");
      return await api.createSchedulerEntry(args);
    },

    tackle_update_scheduler_entry: async (args: {
      id: number; role?: string; model_id?: string | null; harness?: string;
      agent_config?: string; schedule_type?: string; schedule_value?: number;
      project_dir?: string; enabled?: number;
      last_run_at?: string; last_run_status?: string;
    }) => {
      if (args.id == null) throw createError("INVALID_ARGUMENTS", "id is required");
      return await api.updateSchedulerEntry(args.id, args);
    },

    tackle_delete_scheduler_entry: async (args: { id: number }) => {
      if (args.id == null) throw createError("INVALID_ARGUMENTS", "id is required");
      await api.deleteSchedulerEntry(args.id);
      return { deleted: true, id: args.id };
    },

    // ── Nebula RMS — Harvest Candidates ──────────────────────────

    tackle_list_harvest_candidates: async (args: {
      harvestId?: string; systemId?: string; subsystemId?: string;
      featureId?: string; limit?: number; offset?: number;
    }) => {
      const params = new URLSearchParams();
      if (args.harvestId) params.set("harvestId", args.harvestId);
      if (args.systemId) params.set("systemId", args.systemId);
      if (args.subsystemId) params.set("subsystemId", args.subsystemId);
      if (args.featureId) params.set("featureId", args.featureId);
      if (args.limit) params.set("limit", String(args.limit));
      if (args.offset) params.set("offset", String(args.offset));
      const qs = params.toString();
      return await nebulaGet(`/harvest-candidates${qs ? "?" + qs : ""}`);
    },

    tackle_get_harvest_candidate: async (args: { id: string }) => {
      return await nebulaGet(`/harvest-candidates/${encodeURIComponent(args.id)}`);
    },

    tackle_update_harvest_candidate: async (args: {
      id: string; title?: string; intentDescription?: string; status?: string;
      systemId?: string | null; subsystemId?: string | null; featureId?: string | null;
      tags?: string[]; planRef?: string;
    }) => {
      const { id, ...body } = args;
      return await nebulaPatch(`/harvest-candidates/${encodeURIComponent(id)}`, body);
    },

    tackle_create_harvest_candidate: async (args: {
      harvestId: string; title: string; intentDescription?: string;
      implementationNotes?: any[]; codeSnippets?: any[]; openQuestions?: any[];
      tags?: string[]; status?: string;
      systemId?: string | null; subsystemId?: string | null; featureId?: string | null;
      planRef?: string;
    }) => {
      return await nebulaPost("/harvest-candidates", args);
    },

    tackle_spawn_plan_from_candidate: async (args: {
      id: string; systemId: string; subsystemId: string;
      featureId?: string | null; planRef?: string;
      priority?: string; status?: string; title?: string; description?: string;
    }) => {
      const { id, ...body } = args;
      return await nebulaPost(`/harvest-candidates/${encodeURIComponent(id)}/spawn-plan`, body);
    },

    tackle_get_system_harvest_candidates: async (args: { systemId: string }) => {
      return await nebulaGet(`/systems/${encodeURIComponent(args.systemId)}/harvest-candidates`);
    },

    tackle_get_plan_candidates: async (args: { planRef: string }) => {
      return await nebulaGet(`/plans/${encodeURIComponent(args.planRef)}/candidates`);
    },

    tackle_list_systems: async (_args: any) => {
      const systems = await nebulaGet("/systems");
      return { count: Array.isArray(systems) ? systems.length : 0, systems };
    },

    tackle_list_requirements: async (args: {
      systemId?: string; subsystemId?: string; featureId?: string;
    }) => {
      const params = new URLSearchParams();
      if (args.systemId) params.set("systemId", args.systemId);
      if (args.subsystemId) params.set("subsystemId", args.subsystemId);
      if (args.featureId) params.set("featureId", args.featureId);
      const qs = params.toString();
      const reqs = await nebulaGet(`/requirements${qs ? "?" + qs : ""}`);
      return { count: Array.isArray(reqs) ? reqs.length : 0, requirements: reqs };
    },
  };
}
