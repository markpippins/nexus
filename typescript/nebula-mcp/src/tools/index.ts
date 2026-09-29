import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { NebulaClient } from "../api/nebulaClient.js";

/**
 * Normalize a role name for inbox addressing.
 *
 * The routing tag `to:<role>` and the pointer key `inbox:pointer:<role>` are
 * both compared EXACTLY (nebula-srv filters with `= ANY(tags)`, and the
 * pointer is a raw Redis key), so a case variant silently addresses a different,
 * usually empty, mailbox instead of erroring.
 *
 * That is not hypothetical: the corpus carried 694 records tagged `to:dba`
 * against 7 tagged `to:DBA`, and Redis held two live pointer keys
 * (`inbox:pointer:dba` and `inbox:pointer:DBA`) six days apart. A session
 * booted as `--role DBA` read the 7-record mailbox and never saw the 694.
 *
 * Lowercasing at this boundary is lossless for every role: the canonical
 * vocabulary is already lowercase (`config/roles/roles.json`, the
 * `roles_history` name key, and `roles_name_check` which enforces
 * `^[a-z0-9_-]+$`). Uppercase survives in display surfaces only
 * (`display_name`, Assembly `alias`).
 *
 * This normalizes the QUERY, not the stored data — no record is rewritten.
 */
function normalizeRole(role: string): string {
  return role.trim().toLowerCase();
}

/**
 * Registers all Nebula RMS MCP Tools.
 */
export function registerTools(server: McpServer) {

  // ════════════════════════════════════════════════════════════════
  //  SYSTEMS
  // ════════════════════════════════════════════════════════════════

  server.tool(
    "nebula_list_systems",
    "List all systems with their full nested hierarchy (subsystems, features, folders).",
    {},
    async () => {
      const result = await NebulaClient.listSystems();
      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({ systems: result, count: Array.isArray(result) ? result.length : 0 }, null, 2),
        }],
      };
    }
  );

  server.tool(
    "nebula_create_system",
    "Create a new system in Nebula RMS.",
    {
      name: z.string().describe("System name (required)"),
      description: z.string().optional().describe("System description"),
      readme: z.string().nullable().optional().describe("Markdown readme content"),
      architecture: z.string().nullable().optional().describe("Architecture notes"),
    },
    async (args) => {
      const result = await NebulaClient.createSystem({
        name: args.name,
        description: args.description,
        readme: args.readme,
        architecture: args.architecture,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_update_system",
    "Update an existing system's metadata.",
    {
      id: z.string().describe("System UUID"),
      name: z.string().optional().describe("New system name"),
      description: z.string().optional().describe("New description"),
      readme: z.string().nullable().optional().describe("New readme content"),
      architecture: z.string().nullable().optional().describe("New architecture notes"),
    },
    async (args) => {
      const { id, name, description, readme, architecture } = args;
      const result = await NebulaClient.updateSystem(id, { name, description, readme, architecture });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_delete_system",
    "Delete a system and all its subsystems, features, folders, and requirements (cascade).",
    {
      id: z.string().describe("System UUID to delete"),
    },
    async (args) => {
      const result = await NebulaClient.deleteSystem(args.id);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  // ════════════════════════════════════════════════════════════════
  //  SUBSYSTEMS
  // ════════════════════════════════════════════════════════════════

  server.tool(
    "nebula_create_subsystem",
    "Create a new subsystem under a system.",
    {
      systemId: z.string().describe("Parent system UUID"),
      name: z.string().describe("Subsystem name"),
      description: z.string().optional().describe("Subsystem description"),
      readme: z.string().nullable().optional().describe("Markdown readme for this subsystem"),
    },
    async (args) => {
      const result = await NebulaClient.createSubsystem({
        systemId: args.systemId,
        name: args.name,
        description: args.description,
        readme: args.readme,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_update_subsystem",
    "Update a subsystem's metadata.",
    {
      id: z.string().describe("Subsystem UUID"),
      name: z.string().optional().describe("New name"),
      description: z.string().optional().describe("New description"),
      readme: z.string().nullable().optional().describe("New readme content"),
      color: z.string().optional().describe("Hex color string (e.g. '#3B82F6')"),
    },
    async (args) => {
      const { id, name, description, readme, color } = args;
      const result = await NebulaClient.updateSubsystem(id, { name, description, readme, color });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_delete_subsystem",
    "Delete a subsystem and its features and requirements (cascade).",
    {
      id: z.string().describe("Subsystem UUID"),
    },
    async (args) => {
      const result = await NebulaClient.deleteSubsystem(args.id);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_move_subsystem",
    "Move a subsystem to a different parent system (transactional).",
    {
      subsystemId: z.string().describe("Subsystem UUID to move"),
      targetSystemId: z.string().describe("Target parent system UUID"),
    },
    async (args) => {
      const result = await NebulaClient.moveSubsystem({
        subsystemId: args.subsystemId,
        targetSystemId: args.targetSystemId,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  // ════════════════════════════════════════════════════════════════
  //  FEATURES
  // ════════════════════════════════════════════════════════════════

  server.tool(
    "nebula_create_feature",
    "Create a new feature under a subsystem.",
    {
      subsystemId: z.string().describe("Parent subsystem UUID"),
      name: z.string().describe("Feature name"),
      description: z.string().optional().describe("Feature description"),
      readme: z.string().nullable().optional().describe("Markdown readme for this feature"),
    },
    async (args) => {
      const result = await NebulaClient.createFeature({
        subsystemId: args.subsystemId,
        name: args.name,
        description: args.description,
        readme: args.readme,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_update_feature",
    "Update a feature's metadata.",
    {
      id: z.string().describe("Feature UUID"),
      name: z.string().optional().describe("New name"),
      description: z.string().optional().describe("New description"),
      readme: z.string().nullable().optional().describe("New readme content"),
    },
    async (args) => {
      const { id, name, description, readme } = args;
      const result = await NebulaClient.updateFeature(id, { name, description, readme });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_delete_feature",
    "Delete a feature and its requirements (cascade).",
    {
      id: z.string().describe("Feature UUID"),
    },
    async (args) => {
      const result = await NebulaClient.deleteFeature(args.id);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_move_feature",
    "Move a feature to a different subsystem (transactional, re-parents requirements).",
    {
      featureId: z.string().describe("Feature UUID to move"),
      targetSystemId: z.string().describe("Target system UUID"),
      targetSubsystemId: z.string().describe("Target subsystem UUID"),
    },
    async (args) => {
      const result = await NebulaClient.moveFeature({
        featureId: args.featureId,
        targetSystemId: args.targetSystemId,
        targetSubsystemId: args.targetSubsystemId,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  // ════════════════════════════════════════════════════════════════
  //  REQUIREMENTS
  // ════════════════════════════════════════════════════════════════

  server.tool(
    "nebula_list_requirements",
    "List requirements, optionally filtered by system, subsystem, or feature.",
    {
      systemId: z.string().optional().describe("Filter by system UUID"),
      subsystemId: z.string().optional().describe("Filter by subsystem UUID"),
      featureId: z.string().optional().describe("Filter by feature UUID"),
    },
    async (args) => {
      const result = await NebulaClient.listRequirements({
        systemId: args.systemId,
        subsystemId: args.subsystemId,
        featureId: args.featureId,
      });
      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({ requirements: result, count: Array.isArray(result) ? result.length : 0 }, null, 2),
        }],
      };
    }
  );

  server.tool(
    "nebula_create_requirement",
    "Create a new requirement in the backlog.",
    {
      systemId: z.string().describe("System UUID"),
      subsystemId: z.string().nullable().optional().describe("Optional subsystem UUID — requirement can live at system level"),
      featureId: z.string().nullable().optional().describe("Optional feature UUID"),
      title: z.string().describe("Requirement title"),
      description: z.string().optional().describe("Requirement description"),
      status: z.string().optional().describe("Status: Backlog, ToDo, InProgress, Active, Blocked, Done, Cancelled, Accepted"),
      priority: z.string().optional().describe("Priority: Low, Medium, High"),
      startDate: z.string().nullable().optional().describe("Start date string"),
      completionDate: z.string().nullable().optional().describe("Completion date string"),
      parentId: z.string().nullable().optional().describe("Parent requirement UUID (for hierarchy)"),
      reqType: z.string().nullable().optional().describe("Requirement type: Epic, Story, Task, Bug"),
      acceptanceCriteria: z.array(z.string()).nullable().optional().describe("Acceptance criteria list"),
      candidateId: z.string().nullable().optional().describe("Originating harvest candidate UUID"),
    },
    async (args) => {
      const result = await NebulaClient.createRequirement({
        systemId: args.systemId,
        subsystemId: args.subsystemId,
        featureId: args.featureId,
        title: args.title,
        description: args.description,
        status: args.status,
        priority: args.priority,
        startDate: args.startDate,
        completionDate: args.completionDate,
        parentId: args.parentId,
        reqType: args.reqType,
        acceptanceCriteria: args.acceptanceCriteria,
        candidateId: args.candidateId,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_update_requirement",
    "Update a requirement's fields.",
    {
      id: z.string().describe("Requirement UUID"),
      title: z.string().optional().describe("New title"),
      description: z.string().optional().describe("New description"),
      status: z.string().optional().describe("New status"),
      priority: z.string().optional().describe("New priority"),
      startDate: z.string().nullable().optional().describe("Start date"),
      completionDate: z.string().nullable().optional().describe("Completion date"),
      systemId: z.string().optional().describe("Reassign to system"),
      subsystemId: z.string().optional().describe("Reassign to subsystem"),
      featureId: z.string().nullable().optional().describe("Reassign to feature"),
      parentId: z.string().nullable().optional().describe("Parent requirement UUID"),
      reqType: z.string().nullable().optional().describe("Requirement type: Epic, Story, Task, Bug"),
      acceptanceCriteria: z.array(z.string()).nullable().optional().describe("Acceptance criteria list"),
      candidateId: z.string().nullable().optional().describe("Originating harvest candidate UUID"),
    },
    async (args) => {
      const { id, title, description, status, priority, startDate, completionDate, systemId, subsystemId, featureId, parentId, reqType, acceptanceCriteria, candidateId } = args;
      const result = await NebulaClient.updateRequirement(id, {
        title, description, status, priority, startDate, completionDate, systemId, subsystemId, featureId, parentId, reqType, acceptanceCriteria, candidateId,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_move_requirement",
    "Move a requirement to a new status (kanban-friendly). Optionally pass expectedCurrentStatus to assert the current value; the call returns 409 on mismatch.",
    {
      id: z.string().describe("Requirement UUID"),
      targetStatus: z.enum([
        "Backlog", "ToDo", "InProgress", "Active", "Blocked", "Done", "Cancelled", "Accepted",
      ]).describe("Status to move the requirement into"),
      expectedCurrentStatus: z.enum([
        "Backlog", "ToDo", "InProgress", "Active", "Blocked", "Done", "Cancelled", "Accepted",
      ]).optional().describe("Optional optimistic-concurrency check. If supplied and the current status differs, the call fails with 409."),
    },
    async (args) => {
      const result = await NebulaClient.moveRequirement(args.id, {
        targetStatus: args.targetStatus,
        expectedCurrentStatus: args.expectedCurrentStatus,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_delete_requirement",
    "Delete a requirement.",
    {
      id: z.string().describe("Requirement UUID"),
    },
    async (args) => {
      const result = await NebulaClient.deleteRequirement(args.id);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_batch_update_requirements",
    "Batch update the status of multiple requirements at once.",
    {
      ids: z.array(z.string()).describe("Array of requirement UUIDs"),
      status: z.string().describe("New status to apply to all (e.g. 'Done', 'InProgress')"),
    },
    async (args) => {
      const result = await NebulaClient.batchUpdateRequirements({ ids: args.ids, status: args.status });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  // ════════════════════════════════════════════════════════════════
  //  SYSTEM FOLDERS
  // ════════════════════════════════════════════════════════════════

  server.tool(
    "nebula_create_folder",
    "Create a folder under a system (for organizing workspaces by category).",
    {
      systemId: z.string().describe("Parent system UUID"),
      name: z.string().describe("Folder name"),
      category: z.enum(["UI", "Service", "Library", "Documentation", "Config", "data", "api"]).describe("Folder category"),
      note: z.string().optional().describe("Optional note about this folder"),
    },
    async (args) => {
      const result = await NebulaClient.createFolder(args.systemId, {
        name: args.name,
        category: args.category,
        note: args.note,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_delete_folder",
    "Delete a folder from a system.",
    {
      systemId: z.string().describe("Parent system UUID"),
      folderId: z.string().describe("Folder UUID to delete"),
    },
    async (args) => {
      const result = await NebulaClient.deleteFolder(args.systemId, args.folderId);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  // ════════════════════════════════════════════════════════════════
  //  WORK SESSIONS
  // ════════════════════════════════════════════════════════════════

  server.tool(
    "nebula_list_sessions",
    "List recent work sessions across all entities.",
    {},
    async () => {
      const result = await NebulaClient.listSessions();
      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({ sessions: result, count: Array.isArray(result) ? result.length : 0 }, null, 2),
        }],
      };
    }
  );

  server.tool(
    "nebula_create_session",
    "Record a new work session against a system, subsystem, feature, or requirement.",
    {
      parentId: z.string().describe("Parent entity UUID"),
      parentType: z.enum(["system", "subsystem", "feature", "requirement"]).describe("Type of parent entity"),
      parentName: z.string().optional().describe("Name of parent entity"),
      context: z.string().optional().describe("Work context description"),
      platform: z.string().optional().describe("Platform used (e.g. 'codebuff', 'claude')"),
      model: z.string().optional().describe("AI model used"),
      outcome: z.string().nullable().optional().describe("Session outcome notes"),
      status: z.string().optional().describe("Session status: Pending, Completed"),
    },
    async (args) => {
      const result = await NebulaClient.createSession({
        parentId: args.parentId,
        parentType: args.parentType,
        parentName: args.parentName,
        context: args.context,
        platform: args.platform,
        model: args.model,
        outcome: args.outcome,
        status: args.status,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_update_session",
    "Update a work session's outcome or status.",
    {
      id: z.string().describe("Session UUID"),
      outcome: z.string().nullable().optional().describe("Outcome notes"),
      status: z.string().optional().describe("New status: Pending, Completed"),
    },
    async (args) => {
      const { id, outcome, status } = args;
      const result = await NebulaClient.updateSession(id, { outcome, status });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_delete_session",
    "Delete a work session.",
    {
      id: z.string().describe("Session UUID"),
    },
    async (args) => {
      const result = await NebulaClient.deleteSession(args.id);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  // ════════════════════════════════════════════════════════════════
  //  WORKSPACES
  // ════════════════════════════════════════════════════════════════

  server.tool(
    "nebula_list_workspaces",
    "List all workspace path mappings (system/subsystem → filesystem path).",
    {},
    async () => {
      const result = await NebulaClient.listWorkspaces();
      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({ workspaces: result, count: Array.isArray(result) ? result.length : 0 }, null, 2),
        }],
      };
    }
  );

  server.tool(
    "nebula_create_workspace",
    "Map a system or subsystem to a filesystem workspace path.",
    {
      systemId: z.string().describe("System UUID"),
      subsystemId: z.string().nullable().optional().describe("Optional subsystem UUID"),
      workspacePath: z.string().describe("Relative path from nexus root (e.g. 'typescript/conduit-mcp')"),
    },
    async (args) => {
      const result = await NebulaClient.createWorkspace({
        systemId: args.systemId,
        subsystemId: args.subsystemId,
        workspacePath: args.workspacePath,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_delete_workspace",
    "Remove a workspace path mapping.",
    {
      id: z.string().describe("Workspace UUID"),
    },
    async (args) => {
      const result = await NebulaClient.deleteWorkspace(args.id);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  // ════════════════════════════════════════════════════════════════
  //  DOCS (reads README.md / ARCHITECTURE.md from disk)
  // ════════════════════════════════════════════════════════════════

  server.tool(
    "nebula_read_docs",
    "Read README.md and ARCHITECTURE.md from a workspace directory on disk.",
    {
      workspacePath: z.string().describe("Relative path from nexus root (e.g. 'typescript/nebula-mcp')"),
    },
    async (args) => {
      const result = await NebulaClient.readDocs(args.workspacePath);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_read_system_docs",
    "Read docs from all workspace directories linked to a system.",
    {
      systemId: z.string().describe("System UUID"),
    },
    async (args) => {
      const result = await NebulaClient.readSystemDocs(args.systemId);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_read_subsystem_docs",
    "Read docs from all workspace directories linked to a subsystem.",
    {
      subsystemId: z.string().describe("Subsystem UUID"),
    },
    async (args) => {
      const result = await NebulaClient.readSubsystemDocs(args.subsystemId);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  // ════════════════════════════════════════════════════════════════
  //  PLANS DISPLAY (Plan 0134)
  // ════════════════════════════════════════════════════════════════

  server.tool(
    "nebula_list_plans",
    "List implementation plans from nexus/graph/IMPLEMENTATION_PLANS/{pending,planning,proposed,completed}/. Returns metadata only — for full markdown body use nebula_get_plan.",
    {
      status: z.enum(["pending", "planning", "proposed", "completed", "all"]).optional()
        .describe("Filter by status directory. Defaults to 'all' (all four directories)."),
    },
    async (args) => {
      const result = await NebulaClient.listPlans({ status: args.status });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_get_plan",
    "Fetch one implementation plan by id (filename basename without .md). Collisions across status dirs resolve to the first match in order: pending → planning → proposed → completed.",
    {
      id: z.string().describe("Plan id (without .md extension), e.g. 'add-plans-display-endpoint-v0134'"),
    },
    async (args) => {
      const result = await NebulaClient.getPlan(args.id);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  // ════════════════════════════════════════════════════════════════
  //  USER PREFERENCES
  // ════════════════════════════════════════════════════════════════

  server.tool(
    "nebula_get_preferences",
    "Get all user preferences (dark mode, UI state, etc.).",
    {},
    async () => {
      const result = await NebulaClient.getPreferences();
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_set_preference",
    "Set a user preference value.",
    {
      key: z.string().describe("Preference key (e.g. 'theme', 'sidebarCollapsed')"),
      value: z.any().describe("Value to store (any JSON-serializable value)"),
    },
    async (args) => {
      const result = await NebulaClient.setPreference(args.key, args.value);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_delete_preference",
    "Delete a user preference (reset to default).",
    {
      key: z.string().describe("Preference key to delete"),
    },
    async (args) => {
      const result = await NebulaClient.deletePreference(args.key);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  // ════════════════════════════════════════════════════════════════
  //  SYSTEM INFO TABS
  // ════════════════════════════════════════════════════════════════

  server.tool(
    "nebula_get_system_info",
    "Get all info tab content for a system.",
    {
      systemId: z.string().describe("System UUID"),
    },
    async (args) => {
      const result = await NebulaClient.getSystemInfo(args.systemId);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_set_system_info",
    "Save content to a system info tab.",
    {
      systemId: z.string().describe("System UUID"),
      tabId: z.string().describe("Tab identifier (e.g. 'overview', 'dependencies')"),
      content: z.string().describe("Tab content (Markdown or text)"),
    },
    async (args) => {
      const result = await NebulaClient.setSystemInfo(args.systemId, args.tabId, args.content);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  // ════════════════════════════════════════════════════════════════
  //  HARVESTS
  // ════════════════════════════════════════════════════════════════

  server.tool(
    "nebula_list_harvests",
    "List harvest pipeline outputs, optionally filtered by model, version, or source hash.",
    {
      model: z.string().optional().describe("Filter by model name (e.g. 'DeepSeek V4')"),
      version: z.number().optional().describe("Filter by harvest version number"),
      sourceHash: z.string().optional().describe("Filter by source content hash (MD5)"),
      level: z.number().optional().describe("Filter by abstraction level (1-4)"),
      visibilityScope: z.string().optional().describe("Filter by visibility scope (builder, architect, planner, reviewer, all)"),
      limit: z.number().optional().describe("Max results (default 100, max 500)"),
      offset: z.number().optional().describe("Offset for pagination"),
    },
    async (args) => {
      const result = await NebulaClient.listHarvests({
        model: args.model,
        version: args.version,
        sourceHash: args.sourceHash,
        level: args.level,
        visibilityScope: args.visibilityScope,
        limit: args.limit,
        offset: args.offset,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_get_harvest",
    "Get a single harvest with full candidate data.",
    {
      id: z.string().describe("Harvest UUID"),
    },
    async (args) => {
      const result = await NebulaClient.getHarvest(args.id);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_create_harvest",
    "Record a new harvest pipeline output in the database. Version auto-increments per source_path+model.",
    {
      sourcePath: z.string().describe("Path to the source chat transcript"),
      sourceFilename: z.string().optional().describe("Display filename for the source"),
      model: z.string().optional().describe("Model used for harvest (e.g. 'DeepSeek V4')"),
      totalCandidates: z.number().optional().describe("Total number of candidates extracted"),
      candidates: z.array(z.any()).optional().describe("Array of candidate objects"),
      sourceText: z.string().optional().describe("Raw markdown text of the harvest file"),
      tags: z.array(z.string()).optional().describe("Tags for filtering"),
      metadata: z.any().optional().describe("Optional metadata object"),
      level: z.number().optional().describe("Abstraction level 1-4 (default 1)"),
      visibilityScope: z.string().optional().describe("Visibility scope: builder, architect, planner, reviewer, all (default 'all')"),
      sourceHash: z.string().optional().describe("Override source content hash (MD5); auto-computed if omitted"),
      runMetadata: z.any().optional().describe("Optional JSON metadata about this specific harvest run"),
    },
    async (args) => {
      const result = await NebulaClient.createHarvest({
        sourcePath: args.sourcePath,
        sourceFilename: args.sourceFilename,
        model: args.model,
        totalCandidates: args.totalCandidates,
        candidates: args.candidates,
        sourceText: args.sourceText,
        tags: args.tags,
        metadata: args.metadata,
        level: args.level,
        visibilityScope: args.visibilityScope,
        sourceHash: args.sourceHash,
        runMetadata: args.runMetadata,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_delete_harvest",
    "Delete a harvest record.",
    {
      id: z.string().describe("Harvest UUID to delete"),
    },
    async (args) => {
      const result = await NebulaClient.deleteHarvest(args.id);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  // ════════════════════════════════════════════════════════════════
  //  HARVEST CANDIDATES
  // ════════════════════════════════════════════════════════════════

  server.tool(
    "nebula_list_harvest_candidates",
    "List harvest candidates, optionally filtered by harvest, system, subsystem, or feature. Each candidate is an individually addressable specification extracted from a harvest.",
    {
      harvestId: z.string().optional().describe("Filter by parent harvest UUID"),
      systemId: z.string().optional().describe("Filter by linked system UUID"),
      subsystemId: z.string().optional().describe("Filter by linked subsystem UUID"),
      featureId: z.string().optional().describe("Filter by linked feature UUID"),
      limit: z.number().optional().describe("Max results (default 100, max 500)"),
      offset: z.number().optional().describe("Offset for pagination"),
    },
    async (args) => {
      const result = await NebulaClient.listHarvestCandidates({
        harvestId: args.harvestId,
        systemId: args.systemId,
        subsystemId: args.subsystemId,
        featureId: args.featureId,
        limit: args.limit,
        offset: args.offset,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_get_harvest_candidate",
    "Get a single harvest candidate with full detail (implementation notes, code snippets, open questions).",
    {
      id: z.string().describe("Harvest candidate UUID"),
    },
    async (args) => {
      const result = await NebulaClient.getHarvestCandidate(args.id);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_update_harvest_candidate",
    "Update a harvest candidate — primarily used to link it to a system, subsystem, or feature in the Nebula project hierarchy. Also supports updating title, status, intent description, tags, work request linkage, and completion status.",
    {
      id: z.string().describe("Harvest candidate UUID"),
      title: z.string().optional().describe("New title"),
      intentDescription: z.string().optional().describe("Revised intent description"),
      status: z.string().optional().describe("Status (e.g. 'promoted', 'reviewed', 'discarded')"),
      systemId: z.string().nullable().optional().describe("Link to system UUID (or null to unlink)"),
      subsystemId: z.string().nullable().optional().describe("Link to subsystem UUID (or null to unlink)"),
      featureId: z.string().nullable().optional().describe("Link to feature UUID (or null to unlink)"),
      tags: z.array(z.string()).optional().describe("New tags array"),
      planRef: z.string().optional().describe("Conduit plan reference (e.g. '0136') — creates a cross-reference linking this candidate to the plan with rel_type='spawns_plan'"),
      workRequestId: z.string().uuid().nullable().optional().describe("Link to WRP runtime WorkRequest UUID — set when a WorkRequest is created for this candidate"),
      completed: z.boolean().optional().describe("Mark candidate as completed (independent of work_request_id — useful for backfilling conduit-era work)"),
    },
    async (args) => {
      const { id, ...body } = args;
      const result = await NebulaClient.updateHarvestCandidate(id, body);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_create_harvest_candidate",
    "Create a standalone harvest candidate (e.g. manually linked to a harvest or project).",
    {
      harvestId: z.string().describe("Parent harvest UUID"),
      title: z.string().describe("Candidate title"),
      intentDescription: z.string().optional().describe("What this candidate proposes to build or change"),
      implementationNotes: z.array(z.any()).optional().describe("Implementation notes array"),
      codeSnippets: z.array(z.any()).optional().describe("Extracted code snippets"),
      openQuestions: z.array(z.any()).optional().describe("Open questions raised"),
      tags: z.array(z.string()).optional().describe("Tags for filtering"),
      status: z.string().optional().describe("Status string"),
      systemId: z.string().nullable().optional().describe("Pre-linked system UUID"),
      subsystemId: z.string().nullable().optional().describe("Pre-linked subsystem UUID"),
      featureId: z.string().nullable().optional().describe("Pre-linked feature UUID"),
      planRef: z.string().optional().describe("Conduit plan reference (e.g. '0136') — creates a cross-reference with rel_type='spawns_plan'"),
    },
    async (args) => {
      const result = await NebulaClient.createHarvestCandidate({
        harvestId: args.harvestId,
        title: args.title,
        intentDescription: args.intentDescription,
        implementationNotes: args.implementationNotes,
        codeSnippets: args.codeSnippets,
        openQuestions: args.openQuestions,
        tags: args.tags,
        status: args.status,
        systemId: args.systemId,
        subsystemId: args.subsystemId,
        featureId: args.featureId,
        planRef: args.planRef,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_discover_harvest_candidates",
    "Discover which existing systems, subsystems, or features match unlinked harvest candidates. Uses semantic search (knowledge_semantic_search) to find curated knowledge entities similar to each candidate, plus direct text matching against hierarchy names. Candidates with top curated similarity >= threshold (default 0.75) are returned as \"matches\" — they can be linked to existing projects. Candidates below threshold are returned as \"undocumented\" — they may represent new projects not yet in the hierarchy.",
    {
      candidateIds: z.array(z.string()).optional().describe("Optional list of specific candidate UUIDs to check. If omitted, all unlinked candidates are processed (up to limit)."),
      limit: z.number().optional().describe("Max unlinked candidates to process (default 50, max 200)"),
      threshold: z.number().min(0).max(1).optional().describe("Confidence threshold for curated semantic matches (default 0.75). Candidates with top similarity >= threshold go to 'matches'; below go to 'undocumented'."),
    },
    async (args) => {
      const result = await NebulaClient.discoverHarvestCandidates({
        candidateIds: args.candidateIds,
        limit: args.limit,
        threshold: args.threshold,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_get_plan_candidates",
    "Reverse lookup: find all harvest candidates linked to a given conduit plan via cross_references (rel_type='spawns_plan'). Returns candidates with their hierarchy links, harvest source, and the timestamp they were linked to the plan.",
    {
      planRef: z.string().describe("Conduit plan reference (e.g. '0136')"),
    },
    async (args) => {
      const result = await NebulaClient.getPlanCandidates(args.planRef);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_get_system_harvest_candidates",
    "List all harvest candidates linked to a specific system (filtered by system_id). Returns candidates with their hierarchy links and harvest source.",
    {
      systemId: z.string().describe("System UUID"),
    },
    async (args) => {
      const result = await NebulaClient.getSystemHarvestCandidates(args.systemId);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_get_subsystem_harvest_candidates",
    "List all harvest candidates linked to a specific subsystem (filtered by subsystem_id). Returns candidates with their hierarchy links and harvest source.",
    {
      subsystemId: z.string().describe("Subsystem UUID"),
    },
    async (args) => {
      const result = await NebulaClient.getSubsystemHarvestCandidates(args.subsystemId);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_get_feature_harvest_candidates",
    "List all harvest candidates linked to a specific feature (filtered by feature_id). Returns candidates with their hierarchy links and harvest source.",
    {
      featureId: z.string().describe("Feature UUID"),
    },
    async (args) => {
      const result = await NebulaClient.getFeatureHarvestCandidates(args.featureId);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_spawn_requirement_from_candidate",
    "Full flow: link a harvest candidate to a system/subsystem, create a requirement derived from the candidate's title and intent, and optionally cross-reference a conduit plan — all in one atomic transaction. Returns the updated candidate, the new requirement, and the cross-reference (if planRef was provided). (Renamed from nebula_spawn_plan_from_candidate per decision 319defa5: candidates promote only to requirements.)",
    {
      id: z.string().describe("Harvest candidate UUID"),
      systemId: z.string().describe("System UUID to link the candidate to (also used for the requirement and info tab)"),
      subsystemId: z.string().nullable().optional().describe("Optional subsystem UUID — requirement can live at system level"),
      featureId: z.string().nullable().optional().describe("Optional feature UUID to link candidate and requirement to"),
      planRef: z.string().optional().describe("Optional conduit plan reference (e.g. '0136') — creates a cross-reference with rel_type='spawns_plan'"),
      priority: z.string().optional().describe("Requirement priority: Low, Medium, High (default Medium)"),
      status: z.string().optional().describe("Requirement status: Backlog, ToDo, InProgress, Active, Blocked, Done, Cancelled, Accepted (default Backlog)"),
      title: z.string().optional().describe("Requirement title (defaults to candidate title)"),
      description: z.string().optional().describe("Requirement description (defaults to candidate intent_description)"),
      parentId: z.string().nullable().optional().describe("Parent requirement UUID (for hierarchy)"),
      reqType: z.string().nullable().optional().describe("Requirement type: Epic, Story, Task, Bug"),
      acceptanceCriteria: z.array(z.string()).nullable().optional().describe("Acceptance criteria list"),
    },
    async (args) => {
      const { id, ...body } = args;
      const result = await NebulaClient.spawnRequirementFromCandidate(id, body);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  // DEPRECATED alias — decision 319defa5 renamed spawn_plan -> spawn_requirement.
  // Kept as a loud pointer so old callers migrate instead of failing mysteriously
  // (same pattern as conduit-mcp's removed create_plan deprecation).
  server.tool(
    "nebula_spawn_plan_from_candidate",
    "DEPRECATED — renamed to nebula_spawn_requirement_from_candidate (decision 319defa5). This stub only returns the migration pointer; it does not execute.",
    {
      id: z.string().describe("Harvest candidate UUID"),
    },
    async () => {
      const notice = {
        error: "nebula_spawn_plan_from_candidate was renamed nebula_spawn_requirement_from_candidate (decision 319defa5: candidates promote ONLY to requirements; plans come from requirements).",
        useInstead: "nebula_spawn_requirement_from_candidate",
        restEndpoint: "POST /api/harvest-candidates/:id/spawn-requirement",
        note: "Arguments unchanged — re-invoke under the new tool name.",
      };
      return { content: [{ type: "text" as const, text: JSON.stringify(notice, null, 2) }] };
    }
  );

  // ════════════════════════════════════════════════════════════════
  //  AGENT RECORDS
  // ════════════════════════════════════════════════════════════════

  server.tool(
    "nebula_list_agent_records",
    "List agent audit records, optionally filtered by type, role, system/subsystem/feature, plan, multi-tag (AND conjunction), text search, date range, level, or visibility scope.",
    {
      type: z.string().optional().describe("Filter by record type (report, analysis, assessment, inspection, prompt, response, engineering_log, architecture_note, decision)"),
      role: z.string().optional().describe("Filter by agent role (architect, planner, builder, reviewer, critic, analyst, inspector, engineer)"),
      systemId: z.string().optional().describe("Filter by associated system UUID"),
      subsystemId: z.string().optional().describe("Filter by associated subsystem UUID"),
      featureId: z.string().optional().describe("Filter by associated feature UUID"),
      planRef: z.string().optional().describe("Filter by conduit plan reference (e.g. '0136')"),
      tag: z.union([z.string(), z.array(z.string())]).optional().describe("Filter by tag(s). Single string or array for AND conjunction (e.g. ['to:engineer', 'type:response'])"),
      tags: z.union([z.string(), z.array(z.string())]).optional().describe("Alias for `tag`. Accepted for callers that used the plural key (issue #75); merged under the same semantics as `tag`. Pass `tag` (singular) for new call sites."),
      search: z.string().optional().describe("Free-text search across title and content (case-insensitive ILIKE)"),
      createdAfter: z.string().optional().describe("Filter records created at or after this ISO 8601 timestamp"),
      createdBefore: z.string().optional().describe("Filter records created at or before this ISO 8601 timestamp"),
      level: z.number().optional().describe("Filter by abstraction level (1-4)"),
      visibilityScope: z.string().optional().describe("Filter by visibility scope (builder, architect, planner, reviewer, all)"),
      limit: z.number().optional().describe("Max results (default 100, max 500)"),
      offset: z.number().optional().describe("Offset for pagination"),
    },
    async (args) => {
      const result = await NebulaClient.listAgentRecords({
        type: args.type,
        role: args.role,
        systemId: args.systemId,
        subsystemId: args.subsystemId,
        featureId: args.featureId,
        planRef: args.planRef,
        tag: args.tag ?? args.tags,
        search: args.search,
        createdAfter: args.createdAfter,
        createdBefore: args.createdBefore,
        level: args.level,
        visibilityScope: args.visibilityScope,
        limit: args.limit,
        offset: args.offset,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_get_agent_record",
    "Get a single agent record with full content.",
    {
      id: z.string().describe("Agent record UUID"),
    },
    async (args) => {
      const result = await NebulaClient.getAgentRecord(args.id);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_get_inbox_pointer",
    "Get the inbox pointer (watermark) for a role. Returns the ISO timestamp of the last-seen record, or null if no pointer exists.",
    {
      role: z.string().describe("Role name (e.g., architect, engineer, planner)"),
    },
    async (args) => {
      const result = await NebulaClient.getInboxPointer(normalizeRole(args.role));
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_set_inbox_pointer",
    "Set the inbox pointer (watermark) for a role. Call this after surfacing new messages to mark them as seen.",
    {
      role: z.string().describe("Role name (e.g., architect, engineer, planner)"),
      timestamp: z.string().describe("ISO timestamp of the last-seen record"),
    },
    async (args) => {
      const result = await NebulaClient.setInboxPointer(normalizeRole(args.role), args.timestamp);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_get_inbox",
    "Get a role's inbox in one call: the stored pointer plus agent records addressed to that role (tags ['to:<role>']) created at or after the pointer. Replaces the manual pointer + list + filter dance (R17) with a single MCP call. Pass advance: true to ALSO advance the role's pointer to the newest returned record's createdAt (the deliberate, opt-in R17 end-of-turn pointer advance — the MCP equivalent of check-inbox.sh --update-pointer; no-op when no records are returned). Advancing is REFUSED (advanceError) when the returned window looks truncated (more matches than records returned, or returned == limit) — same guard as check-inbox.sh --update-pointer (#638) — and when no record carries a numeric createdAt (reported, never coerced — #657 shape).",
    {
      role: z.string().describe("Role name (e.g., architect, engineer, planner)"),
      limit: z.number().optional().describe("Max records to return (default 20, max 100)"),
      advance: z.boolean().optional().describe("After listing, advance the role's inbox pointer to the newest returned record's createdAt (opt-in; no-op with no records; REFUSED on a truncated window or when no record has a numeric createdAt)"),
    },
    async (args) => {
      const role = args.role;
      const roleKey = normalizeRole(role);
      const limit = Math.min(args.limit ?? 20, 100);
      const advance = args.advance === true;
      // REST returns { role, pointer } — extract the ISO timestamp string.
      const pointerResult = (await NebulaClient.getInboxPointer(roleKey)) as
        | { role?: string; pointer?: string | null }
        | string
        | null;
      const pointer =
        typeof pointerResult === "object" && pointerResult !== null
          ? pointerResult.pointer ?? null
          : pointerResult;
      const items = await NebulaClient.listAgentRecords({
        tag: [`to:${roleKey}`],
        createdAfter: typeof pointer === "string" ? pointer : undefined,
        limit,
      });
      // REST list responses are wrapped as { items, total, page, pageSize }.
      // `total` is the true match count — used to detect window truncation
      // (mirrors check-inbox.sh `_truncation_note`, #638).
      const total =
        (items as { total?: number } | null)?.total ??
        (Array.isArray(items) ? items.length : undefined);
      const records = Array.isArray(items)
        ? items
        : ((items as { items?: unknown[] })?.items ?? []);
      // A returned window equal to (or, with total, short of) the match set
      // means there may be records below the fold. Advancing to the newest
      // returned createdAt would mark those unseen records as seen — the
      // exact failure #638 fixed in check-inbox.sh (exit 3 refusal).
      const truncated =
        total !== undefined
          ? total > records.length
          : records.length >= limit;

      // ── opt-in pointer advance ─────────────────────────────────────────
      // Mirrors check-inbox.sh --update-pointer: newest-first is assumed, so
      // max(createdAt) over the (possibly limited) window is the true newest
      // (an unsorted list would only under-advance → duplicate delivery next
      // turn, never lost records). createdAt is epoch ms; the pointer is ISO.
      //
      // Missing createdAt is REPORTED, never coerced (the `?? 0` shape #657
      // removed from the merge gate): a record without a timestamp has no
      // place in the newest computation, and an all-missing window is a
      // no-op rather than a bad write. `missingCreatedAt` in the response
      // makes the anomaly visible to the caller instead of silent.
      let advancedTo: string | null = null;
      let advanceError: string | null = null;
      if (advance && records.length > 0) {
        if (truncated) {
          // Advancing now would mark below-fold records seen without ever
          // showing them — the exact failure mode #638 guarded in
          // check-inbox.sh. Withhold the write; the caller must re-run with
          // a higher limit before advancing.
          advanceError =
            "refused: inbox window looks truncated (" + records.length +
            " of " + (total ?? ">=limit") + " records returned) — advancing " +
            "would mark below-fold records seen without showing them. Re-run " +
            "with a higher limit before advancing.";
        } else {
          const withTs = records.filter(
            (r) => typeof (r as { createdAt?: unknown })?.createdAt === "number"
          ) as { createdAt: number }[];
          if (withTs.length > 0) {
            const newest = Math.max(...withTs.map((r) => r.createdAt));
            const iso = new Date(newest).toISOString();
            try {
              await NebulaClient.setInboxPointer(role, iso);
              advancedTo = iso;
            } catch (e) {
              advanceError = e instanceof Error ? e.message : String(e);
            }
          } else {
            advanceError =
              "refused: no returned record has a numeric createdAt — cannot " +
              "determine the newest record to advance to. Records without " +
              "timestamps are reported, not coerced (#657 shape).";
          }
        }
      }

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify(
            {
              role,
              pointer,
              items: records,
              count: records.length,
              total,
              truncated,
              missingCreatedAt: records.filter(
                (r) => typeof (r as { createdAt?: unknown })?.createdAt !== "number"
              ).length,
              advancedTo,
              advanceError,
            },
            null,
            2
          ),
        }],
      };
    }
  );

  server.tool(
    "nebula_create_agent_record",
    "Create a new agent record in the database (canonical write path for all agent audit artifacts). Use this instead of writing to the filesystem.",
    {
      recordType: z.enum([
        "report", "analysis", "assessment", "inspection",
        "prompt", "response", "engineering_log",
        "architecture_note", "decision",
      ]).describe("Type of record"),
      role: z.string().optional().describe("Agent role (architect, planner, builder, reviewer, critic, analyst, inspector, engineer)"),
      title: z.string().optional().describe("Record title"),
      content: z.string().optional().describe("Markdown content body"),
      sourcePath: z.string().optional().describe("Original filesystem path if migrating from audit/"),
      metadata: z.any().optional().describe("Flexible JSON metadata"),
      tags: z.array(z.string()).optional().describe("Tags for filtering"),
      systemId: z.string().optional().describe("Associated system UUID"),
      subsystemId: z.string().optional().describe("Associated subsystem UUID"),
      featureId: z.string().optional().describe("Associated feature UUID"),
      planRef: z.string().optional().describe("Conduit plan reference (e.g. '0136')"),
      level: z.number().optional().describe("Abstraction level 1-4 (default 1)"),
      visibilityScope: z.string().optional().describe("Visibility scope: builder, architect, planner, reviewer, all (default 'all')"),
      model: z.string().optional().describe("AI model identifier (e.g. 'deepseek/deepseek-v4-pro')"),
    },
    async (args) => {
      const result = await NebulaClient.createAgentRecord({
        recordType: args.recordType,
        role: args.role,
        title: args.title,
        content: args.content,
        sourcePath: args.sourcePath,
        metadata: args.metadata,
        tags: args.tags,
        systemId: args.systemId,
        subsystemId: args.subsystemId,
        featureId: args.featureId,
        planRef: args.planRef,
        level: args.level,
        visibilityScope: args.visibilityScope,
        model: args.model,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_update_agent_record",
    "Update an existing agent record's fields.",
    {
      id: z.string().describe("Agent record UUID"),
      title: z.string().optional().describe("New title"),
      content: z.string().optional().describe("New markdown content"),
      metadata: z.any().optional().describe("New JSON metadata"),
      tags: z.array(z.string()).optional().describe("New tags array"),
      systemId: z.string().nullable().optional().describe("Associated system UUID"),
      subsystemId: z.string().nullable().optional().describe("Associated subsystem UUID"),
      featureId: z.string().nullable().optional().describe("Associated feature UUID"),
      planRef: z.string().nullable().optional().describe("Conduit plan reference"),
      level: z.number().optional().describe("New abstraction level (1-4)"),
      visibilityScope: z.string().optional().describe("New visibility scope (builder, architect, planner, reviewer, all)"),
      model: z.string().nullable().optional().describe("AI model identifier"),
    },
    async (args) => {
      const { id, ...body } = args;
      const result = await NebulaClient.updateAgentRecord(id, body);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_delete_agent_record",
    "Delete an agent record from the database.",
    {
      id: z.string().describe("Agent record UUID to delete"),
    },
    async (args) => {
      const result = await NebulaClient.deleteAgentRecord(args.id);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  // ════════════════════════════════════════════════════════════════
  //  PROJECTIONS
  // ════════════════════════════════════════════════════════════════

  server.tool(
    "nebula_list_projections",
    "List all on-demand markdown folder generation configs.",
    {},
    async () => {
      const result = await NebulaClient.listProjections();
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_create_projection",
    "Create a new projection config for on-demand markdown folder generation.",
    {
      name: z.string().describe("Unique projection name"),
      type: z.enum(["deterministic", "inference"]).describe("deterministic (SQL+template) or inference (LLM)"),
      description: z.string().optional().describe("Description of what this projection generates"),
      sourceQuery: z.string().optional().describe("SQL SELECT query that feeds the template (deterministic only)"),
      template: z.string().optional().describe("Markdown template with {{placeholder}} syntax"),
      targetPath: z.string().optional().describe("Relative output path under audit/ (e.g. 'ARCHITECTURE/reports/{{id}}.md')"),
      model: z.string().optional().describe("LLM model for inference type projections"),
      schedule: z.string().optional().describe("Optional cron expression for auto-regeneration"),
      metadata: z.any().optional().describe("Optional metadata"),
    },
    async (args) => {
      const result = await NebulaClient.createProjection(args);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_render_projection",
    "Execute a projection and write output markdown files to the audit/ folder.",
    {
      id: z.string().describe("Projection UUID to render"),
    },
    async (args) => {
      const result = await NebulaClient.renderProjection(args.id);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_delete_projection",
    "Delete a projection config.",
    {
      id: z.string().describe("Projection UUID to delete"),
    },
    async (args) => {
      const result = await NebulaClient.deleteProjection(args.id);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  // ════════════════════════════════════════════════════════════════
  //  CROSS-REFERENCES
  // ════════════════════════════════════════════════════════════════

  // Canonical enum lives in nebula-srv/src/crossref-taxonomy.ts (plan #0175).
  // nebula-srv re-validates at the write boundary; this client-side list only
  // fails fast with a clean MCP error and must stay in sync with the enum.
  const CROSSREF_TYPES: readonly string[] = [
    "wrp:depends_on", "wrp:implements", "wrp:tracked_by", "wrp:impacts_system", "wrp:supersedes",
    "ag:references_plan", "ag:same_thread_as", "ag:prompted_by", "ag:spawns_plan", "ag:evidences_candidate",
    "kv:sourced_from", "kv:informs", "kv:cross_schema", "kv:name_overlap", "kv:description_overlap",
    "req:blocks", "req:depends_on", "spec:defines_req",
  ];
  const CROSSREF_TYPES_HINT = `Valid types: ${CROSSREF_TYPES.join(", ")}`;

  server.tool(
    "nebula_list_cross_references",
    "List cross-references between entities, optionally filtered by source/target type/id or relation type.",
    {
      sourceType: z.string().optional().describe("Filter by source entity type (e.g. 'plan', 'agent_record')"),
      sourceId: z.string().optional().describe("Filter by source entity UUID"),
      targetType: z.string().optional().describe("Filter by target entity type"),
      targetId: z.string().optional().describe("Filter by target entity UUID"),
      relType: z.string().optional().describe(`Filter by relation type. ${CROSSREF_TYPES_HINT}`),
    },
    async (args) => {
      const result = await NebulaClient.listCrossReferences({
        sourceType: args.sourceType,
        sourceId: args.sourceId,
        targetType: args.targetType,
        targetId: args.targetId,
        relType: args.relType,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_get_cross_reference",
    "Get a single cross-reference by ID.",
    {
      id: z.string().describe("Cross-reference UUID"),
    },
    async (args) => {
      const result = await NebulaClient.getCrossReference(args.id);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_create_cross_reference",
    "Create a cross-reference link between two entities. Validates rel_type against the formal taxonomy.",
    {
      sourceType: z.string().describe("Source entity type (e.g. 'plan', 'agent_record')"),
      sourceId: z.string().describe("Source entity UUID"),
      targetType: z.string().describe("Target entity type"),
      targetId: z.string().describe("Target entity UUID"),
      relType: z.string().describe(`Relation type. ${CROSSREF_TYPES_HINT}`),
      metadata: z.any().optional().describe("Optional JSON metadata for the link"),
    },
    async (args) => {
      if (!CROSSREF_TYPES.includes(args.relType)) {
        return {
          content: [{ type: "text" as const, text: `Invalid rel_type "${args.relType}". ${CROSSREF_TYPES_HINT}` }],
          isError: true,
        };
      }
      const result = await NebulaClient.createCrossReference({
        sourceType: args.sourceType,
        sourceId: args.sourceId,
        targetType: args.targetType,
        targetId: args.targetId,
        relType: args.relType,
        metadata: args.metadata,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_delete_cross_reference",
    "Delete a cross-reference link.",
    {
      id: z.string().describe("Cross-reference UUID to delete"),
    },
    async (args) => {
      const result = await NebulaClient.deleteCrossReference(args.id);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  // ════════════════════════════════════════════════════════════════
  //  EVIDENCE LINKS — typed harvest→knowledge bridge
  // ════════════════════════════════════════════════════════════════

  const EVIDENCE_LINK_TYPES_HINT = "Valid types: supports, refines, instantiates, contradicts, supersedes, mentions, informs, validates";
  const EVIDENCE_PROVENANCE_HINT = "Valid provenance: auto_ingestor, manual, reconciler, llm_extracted, migration";

  server.tool(
    "nebula_list_evidence_links",
    "List evidence links between knowledge entities and harvested evidence, with optional filters.",
    {
      knowledgeEntityId: z.string().optional().describe("Filter by knowledge graph entity UUID"),
      nebulaHarvestId: z.string().optional().describe("Filter by harvest UUID"),
      nebulaCandidateId: z.string().optional().describe("Filter by harvest candidate UUID"),
      linkType: z.string().optional().describe(`Filter by link type. ${EVIDENCE_LINK_TYPES_HINT}`),
      provenance: z.string().optional().describe(`Filter by provenance. ${EVIDENCE_PROVENANCE_HINT}`),
      minConfidence: z.number().optional().describe("Minimum confidence filter (0–1)"),
      maxConfidence: z.number().optional().describe("Maximum confidence filter (0–1)"),
      limit: z.number().optional().describe("Max results, default 100"),
      offset: z.number().optional().describe("Pagination offset"),
    },
    async (args) => {
      const result = await NebulaClient.listEvidenceLinks({
        knowledgeEntityId: args.knowledgeEntityId,
        nebulaHarvestId: args.nebulaHarvestId,
        nebulaCandidateId: args.nebulaCandidateId,
        linkType: args.linkType,
        provenance: args.provenance,
        minConfidence: args.minConfidence,
        maxConfidence: args.maxConfidence,
        limit: args.limit,
        offset: args.offset,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_get_evidence_link",
    "Get a single evidence link by ID.",
    {
      id: z.string().describe("Evidence link UUID"),
    },
    async (args) => {
      const result = await NebulaClient.getEvidenceLink(args.id);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_create_evidence_link",
    "Create an evidence link connecting a knowledge entity to harvested evidence. Validates link_type against the formal taxonomy.",
    {
      knowledgeEntityId: z.string().describe("Knowledge graph entity UUID"),
      nebulaHarvestId: z.string().optional().describe("Harvest UUID (required if nebulaCandidateId not provided)"),
      nebulaCandidateId: z.string().optional().describe("Harvest candidate UUID (required if nebulaHarvestId not provided)"),
      linkType: z.string().describe(`Link type. ${EVIDENCE_LINK_TYPES_HINT}`),
      confidence: z.number().optional().describe("Confidence score (0–1)"),
      provenance: z.string().optional().describe(`How the link was established. ${EVIDENCE_PROVENANCE_HINT}`),
      rationale: z.string().optional().describe("Free-text explanation of why this link exists"),
      sourceSpan: z.any().optional().describe("Source span coordinates (JSON object with start_offset, end_offset, chunk_index)"),
      metadata: z.any().optional().describe("Optional JSON metadata"),
    },
    async (args) => {
      const result = await NebulaClient.createEvidenceLink({
        knowledgeEntityId: args.knowledgeEntityId,
        nebulaHarvestId: args.nebulaHarvestId,
        nebulaCandidateId: args.nebulaCandidateId,
        linkType: args.linkType,
        confidence: args.confidence,
        provenance: args.provenance,
        rationale: args.rationale,
        sourceSpan: args.sourceSpan,
        metadata: args.metadata,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_delete_evidence_link",
    "Delete a single evidence link by ID.",
    {
      id: z.string().describe("Evidence link UUID to delete"),
    },
    async (args) => {
      const result = await NebulaClient.deleteEvidenceLink(args.id);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_delete_evidence_links_by_entity",
    "Delete all evidence links for a given knowledge entity (bulk delete).",
    {
      knowledgeEntityId: z.string().describe("Knowledge entity UUID whose links should be deleted"),
    },
    async (args) => {
      const result = await NebulaClient.deleteEvidenceLinksByEntity(args.knowledgeEntityId);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  // ════════════════════════════════════════════════════════════════
  //  COMPLEX OPERATIONS
  // ════════════════════════════════════════════════════════════════

  server.tool(
    "nebula_demote_system",
    "Demote a system into a subsystem of another system (merges hierarchy).",
    {
      sourceSystemId: z.string().describe("System UUID to demote"),
      targetSystemId: z.string().describe("Target system UUID that will become the parent"),
    },
    async (args) => {
      const result = await NebulaClient.demoteSystem(args.sourceSystemId, args.targetSystemId);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  // ════════════════════════════════════════════════════════════════
  //  IMPORT / SEED
  // ════════════════════════════════════════════════════════════════

  server.tool(
    "nebula_import",
    "Bulk import systems, requirements, sessions, preferences, and info tabs from a migration payload.",
    {
      systems: z.array(z.any()).optional().describe("Array of system objects to import"),
      requirements: z.array(z.any()).optional().describe("Array of requirement objects to import"),
      workSessions: z.array(z.any()).optional().describe("Array of work session objects to import"),
      preferences: z.record(z.string(), z.any()).optional().describe("Preferences key/value map"),
      infoTabs: z.record(z.string(), z.record(z.string(), z.string())).optional().describe("Info tabs map: systemId -> { tabId -> content }"),
    },
    async (args) => {
      const result = await NebulaClient.importData({
        systems: args.systems,
        requirements: args.requirements,
        workSessions: args.workSessions,
        preferences: args.preferences,
        infoTabs: args.infoTabs,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_seed",
    "Seed the database with example data (idempotent — safe to call repeatedly).",
    {},
    async () => {
      const result = await NebulaClient.seedData();
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  // ════════════════════════════════════════════════════════════════
  //  CONDUIT HISTORY — plan history & point-in-time queries
  // ════════════════════════════════════════════════════════════════

  server.tool(
    "nebula_query_conduit_plans",
    "List conduit pipeline plans. Optionally include soft-deleted plans or query state as-of a past timestamp. " +
    "Use this to answer 'what plans exist?' or 'what plans existed yesterday?'.",
    {
      includeDeleted: z.boolean().optional()
        .describe("Include soft-deleted plans (deleted=1). Default false (only active plans)."),
      asOf: z.string().optional()
        .describe("ISO 8601 timestamp to query historical plan state. Returns state derived from receipts up to that time."),
      status: z.string().optional()
        .describe("Filter by derived status (e.g. PLAN_CREATE, IMPLEMENTATION, BLOCK, REVIEW_PASS)."),
      limit: z.number().optional().describe("Max results (default 100, max 500)"),
      offset: z.number().optional().describe("Offset for pagination"),
    },
    async (args) => {
      const result = await NebulaClient.listConduitPlans({
        includeDeleted: args.includeDeleted,
        asOf: args.asOf,
        status: args.status,
        limit: args.limit,
        offset: args.offset,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_query_conduit_plan_history",
    "Get the full lifecycle history of a single conduit plan — plan metadata, all receipts, all tickets, " +
    "linked sessions, and token usage. Use this to answer 'what happened to plan X?'.",
    {
      planId: z.string().describe("The plan number or ID (e.g. '0169', '0075')"),
    },
    async (args) => {
      const result = await NebulaClient.getConduitPlanHistory(args.planId);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_query_conduit_plan_receipts",
    "Get all receipts (state transitions) for a specific conduit plan.",
    {
      planId: z.string().describe("The plan number or ID (e.g. '0169')"),
    },
    async (args) => {
      const result = await NebulaClient.getConduitPlanReceipts(args.planId);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_query_conduit_as_of",
    "Get a point-in-time snapshot of all plan states. Use this to answer 'what was in conduit yesterday?' " +
    "or 'what was the pipeline state last Tuesday?'.",
    {
      timestamp: z.string().describe("ISO 8601 timestamp (e.g. '2026-06-21T12:00:00Z' or '2026-06-21')"),
      includeDeleted: z.boolean().optional()
        .describe("Include plans that were soft-deleted as of that time."),
    },
    async (args) => {
      const result = await NebulaClient.getConduitPlansAsOf(args.timestamp, args.includeDeleted);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_list_deleted_conduit_plans",
    "Find all soft-deleted conduit plans that are no longer visible in the live pipeline. " +
    "Use this to recover plans that were deleted but still have data in the database.",
    {},
    async () => {
      const result = await NebulaClient.listDeletedConduitPlans();
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  // ════════════════════════════════════════════════════════════════
  //  OP MAPPING REGISTRY
  // ════════════════════════════════════════════════════════════════

  server.tool(
    "nebula_create_op_registry_entry",
    "Create a new Op Mapping Registry entry. Maps an Implementation Plan intent pattern " +
    "to a WorkRequest opcode sequence. Entries are versioned and immutable after creation.",
    {
      id: z.string().describe("Unique entry ID (e.g. 'INIT_SERVICE_SCAFFOLD:v1')"),
      intent_id: z.string().describe("Intent identifier (e.g. 'INIT_SERVICE_SCAFFOLD')"),
      version: z.string().optional().describe("Semantic version (default: 'v1')"),
      status: z.string().optional().describe("Status: active, deprecated, superseded (default: active)"),
      label: z.string().optional().describe("Human-readable label"),
      match_patterns: z.array(z.string()).optional().describe("Goal patterns to match against"),
      opcode_template: z.array(z.any()).optional().describe("JSON array of opcode sequence templates"),
      required_params: z.array(z.string()).optional().describe("Required parameter names"),
      optional_params: z.array(z.string()).optional().describe("Optional parameter names"),
      preconditions: z.array(z.string()).optional().describe("Precondition descriptions"),
      postconditions: z.array(z.string()).optional().describe("Postcondition descriptions"),
      idempotency_key: z.string().optional().describe("Default idempotency key template"),
      notes: z.string().optional().describe("Human-readable notes"),
    },
    async (args) => {
      const result = await NebulaClient.createOpRegistryEntry(args);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_list_op_registry",
    "List Op Mapping Registry entries with optional filters by intent_id, status, or text search.",
    {
      intent_id: z.string().optional().describe("Filter by intent identifier"),
      status: z.string().optional().describe("Filter by status (active, deprecated, superseded)"),
      search: z.string().optional().describe("Free-text search across label, intent_id, notes"),
      limit: z.number().optional().describe("Max results (default 100)"),
      offset: z.number().optional().describe("Offset for pagination"),
    },
    async (args) => {
      const result = await NebulaClient.listOpRegistry(args);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_get_op_registry_entry",
    "Get a single Op Mapping Registry entry by ID.",
    {
      id: z.string().describe("Registry entry ID (e.g. 'INIT_SERVICE_SCAFFOLD:v1')"),
    },
    async (args) => {
      const result = await NebulaClient.getOpRegistryEntry(args.id);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_deprecate_op_registry_entry",
    "Deprecate a registry entry. Soft-retires it so existing WorkRequests still work, " +
    "but new compilations should use the replacement.",
    {
      id: z.string().describe("Registry entry ID to deprecate"),
      successor_id: z.string().optional().describe("Replacement entry ID"),
    },
    async (args) => {
      const result = await NebulaClient.deprecateOpRegistryEntry(args.id, args.successor_id);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_supersede_op_registry_entry",
    "Mark a registry entry as superseded (replaced by a fork). Requires successor_id.",
    {
      id: z.string().describe("Registry entry ID to supersede"),
      successor_id: z.string().describe("Replacement entry ID (required)"),
    },
    async (args) => {
      const result = await NebulaClient.supersedeOpRegistryEntry(args.id, args.successor_id);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_fork_op_registry_entry",
    "Create a new version of an existing intent mapping (fork). " +
    "The source entry is superseded and the new version becomes active.",
    {
      source_id: z.string().describe("Source entry ID to fork from"),
      new_version: z.string().describe("New version string (e.g. 'v2')"),
      label: z.string().optional().describe("New label (defaults to source label with version suffix)"),
      notes: z.string().optional().describe("Notes about what changed in this version"),
      opcode_template: z.array(z.any()).optional().describe("Updated opcode template (defaults to source)"),
      required_params: z.array(z.string()).optional().describe("Updated required params (defaults to source)"),
    },
    async (args) => {
      const result = await NebulaClient.forkOpRegistryEntry(args);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_get_op_registry_lineage",
    "Show the version lineage of an intent mapping. Returns all versions in order.",
    {
      id: z.string().describe("Registry entry ID to get lineage for"),
    },
    async (args) => {
      const result = await NebulaClient.getOpRegistryLineage(args.id);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  // ════════════════════════════════════════════════════════════════
  //  EXECUTION AUTHORITY (ADR-006)
  // ════════════════════════════════════════════════════════════════

  server.tool(
    "execution_create_request",
    "Create a new WorkRequest in the execution domain. Returns the created request with UUID id.",
    {
      businessKey: z.string().describe("Unique business key (e.g. 'legacy-plan-0053' or 'wr-2026-07-12-001')"),
      title: z.string().optional().describe("Human-readable title"),
      intentType: z.string().optional().describe("Type of intent (default: 'task')"),
      objective: z.string().optional().describe("What is desired"),
      inputs: z.any().optional().describe("Intent inputs (any JSON)"),
      deterministic: z.boolean().optional().describe("Whether this is deterministic (default: true)"),
      maxRetries: z.number().optional().describe("Max retry hint"),
      timeoutPolicy: z.string().optional().describe("Timeout policy hint"),
      resourceHints: z.array(z.string()).optional().describe("Resource hints"),
      opTrace: z.any().optional().describe("Op resolution trace"),
      status: z.string().optional().describe("Initial status (default: DRAFT)"),
      sourcePlanId: z.string().optional().describe("Source conduit plan ID"),
      sourceWrId: z.string().optional().describe("Source vision.work_requests.wr_id"),
    },
    async (args) => {
      const result = await NebulaClient.createExecutionRequest(args);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "execution_list_requests",
    "List execution requests, optionally filtered by status.",
    {
      status: z.string().optional().describe("Filter by status (DRAFT, COMPILED, VALIDATED, ADMITTED, READY, COMPLETED, FAILED, CANCELLED)"),
      limit: z.number().optional().describe("Max results (default 50, max 200)"),
    },
    async (args) => {
      const result = await NebulaClient.listExecutionRequests(args);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "execution_get_request",
    "Get a single execution request with its full lifecycle (leases, attempts, receipts).",
    {
      id: z.string().describe("Request UUID"),
    },
    async (args) => {
      const result = await NebulaClient.getExecutionRequest(args.id);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "execution_transition_request",
    "Transition a WorkRequest to a new status. Enforces valid state transitions per ADR-006.",
    {
      id: z.string().describe("Request UUID"),
      targetStatus: z.enum(["COMPILED","VALIDATED","ADMITTED","READY","COMPLETED","FAILED","CANCELLED"])
        .describe("Target status"),
      reason: z.string().optional().describe("Reason for transition"),
    },
    async (args) => {
      const result = await NebulaClient.transitionExecutionRequest(args.id, {
        targetStatus: args.targetStatus,
        reason: args.reason,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "execution_acquire_lease",
    "Acquire a temporal lease on an execution request. Only one active lease per request at a time.",
    {
      requestId: z.string().describe("Request UUID to lease"),
      executorId: z.string().describe("Executor identity (e.g. 'conduit', 'cli', 'jenkins')"),
      ttlSeconds: z.number().optional().describe("Time-to-live in seconds (default: 300)"),
    },
    async (args) => {
      const result = await NebulaClient.acquireLease(args);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "execution_renew_lease",
    "Renew an active lease (extend TTL). Fails if lease is expired or released.",
    {
      id: z.string().describe("Lease UUID"),
      ttlSeconds: z.number().optional().describe("New TTL in seconds (default: 300)"),
    },
    async (args) => {
      const result = await NebulaClient.renewLease(args.id, { ttlSeconds: args.ttlSeconds });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "execution_release_lease",
    "Release an active lease. The executor voluntarily gives back execution permission.",
    {
      id: z.string().describe("Lease UUID"),
    },
    async (args) => {
      const result = await NebulaClient.releaseLease(args.id);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "role_lease_issue",
    "Issue an ACTIVE role lease (RoleLeases, plan 1286): a bounded window + budget under which a role on a channel may consume work. Mandatory time limit. One ACTIVE lease per role at a time.",
    {
      role: z.string().describe("Role name (e.g. 'leased-builder', 'engineer')"),
      channel: z.enum(["interactive","opencode","ollama","unknown"]).optional()
        .describe("Harness channel (default: interactive)"),
      model: z.string().optional().describe("Model identifier (e.g. 'freebuff/glm-5.2')"),
      ttlSeconds: z.number().optional().describe("Window/lease length in seconds (default: 3600 — one hour)"),
      budgetUnits: z.number().optional().describe("Max work units the role may consume under this lease"),
      windowEnd: z.string().optional().describe("Explicit window end ISO timestamp (overrides ttlSeconds)"),
    },
    async (args) => {
      const result = await NebulaClient.issueRoleLease(args);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "role_lease_renew",
    "Renew an ACTIVE role lease (extend window/budget). Fails if expired or released. Renewal is an explicit decision, not automatic.",
    {
      id: z.string().describe("Role lease UUID"),
      ttlSeconds: z.number().optional().describe("Extension in seconds (default: 3600)"),
      budgetUnits: z.number().optional().describe("New budget ceiling"),
    },
    async (args) => {
      const result = await NebulaClient.renewRoleLease(args.id, { ttlSeconds: args.ttlSeconds, budgetUnits: args.budgetUnits });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "role_lease_revoke",
    "Revoke an ACTIVE role lease (voluntary release, e.g. dispenser empty or session ending). Unclaimed work returns to the pool.",
    {
      id: z.string().describe("Role lease UUID"),
    },
    async (args) => {
      const result = await NebulaClient.revokeRoleLease(args.id);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "role_lease_status",
    "List role leases (optionally filtered by role/status) — for orientation: does my role have an active lease right now?",
    {
      role: z.string().optional().describe("Filter by role"),
      status: z.string().optional().describe("Filter by status (ACTIVE/RELEASED/EXPIRED)"),
      limit: z.number().optional().describe("Max rows (default: 50)"),
    },
    async (args) => {
      const result = await NebulaClient.listRoleLeases(args);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "execution_submit_attempt",
    "Submit an execution attempt result. Creates an attempt record linked to the lease.",
    {
      leaseId: z.string().describe("Lease UUID (must be ACTIVE)"),
      status: z.enum(["SUCCEEDED","FAILED","TIMED_OUT"]).optional()
        .describe("Attempt outcome (default: SUCCEEDED)"),
      result: z.any().optional().describe("Result payload (any JSON)"),
      error: z.string().optional().describe("Error message if failed"),
      exitCode: z.number().optional().describe("Exit code"),
    },
    async (args) => {
      const result = await NebulaClient.submitAttempt(args);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "execution_issue_receipt",
    "Issue an immutable receipt from a completed attempt. Consumed by the Kernel.",
    {
      attemptId: z.string().describe("Attempt UUID"),
      type: z.string().optional().describe("Receipt type (default: EXECUTION_COMPLETE or EXECUTION_FAILED based on attempt status)"),
      agentRole: z.string().optional().describe("Agent role (defaults to executor_id)"),
      summary: z.string().optional().describe("Human-readable summary"),
      metadata: z.any().optional().describe("Additional metadata"),
    },
    async (args) => {
      const result = await NebulaClient.issueReceipt(args);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "execution_list_receipts",
    "List execution receipts, optionally filtered by request or type.",
    {
      requestId: z.string().optional().describe("Filter by request UUID"),
      type: z.string().optional().describe("Filter by receipt type"),
      limit: z.number().optional().describe("Max results"),
    },
    async (args) => {
      const result = await NebulaClient.listExecutionReceipts(args);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "execution_state",
    "Get a summary of the execution domain state (request/lease attempt/receipt counts by status).",
    {},
    async () => {
      const result = await NebulaClient.getExecutionState();
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  // ════════════════════════════════════════════════════════════════
  //  OPEN QUESTIONS
  // ════════════════════════════════════════════════════════════════

  server.tool(
    "nebula_list_open_questions",
    "List open questions, optionally filtered by requirement, candidate, or status.",
    {
      requirementId: z.string().optional().describe("Filter by requirement UUID"),
      candidateId: z.string().optional().describe("Filter by candidate UUID"),
      status: z.string().optional().describe("Filter by status (default: OPEN)"),
    },
    async (args) => {
      const result = await NebulaClient.listOpenQuestions(args);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_answer_question",
    "Record an answer to an open question without resolving it. Status stays OPEN.",
    {
      questionId: z.string().describe("Question UUID to answer"),
      answer: z.string().describe("The answer text"),
      answeredBy: z.string().describe("Who answered (role name, e.g. 'analyst')"),
    },
    async (args) => {
      const result = await NebulaClient.answerQuestion(args.questionId, {
        answer: args.answer,
        answeredBy: args.answeredBy,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_resolve_question",
    "Resolve an open question that has been answered. Requires an existing answer.",
    {
      questionId: z.string().describe("Question UUID to resolve"),
      resolvedBy: z.string().describe("Who resolved it (role name, e.g. 'planner')"),
    },
    async (args) => {
      const result = await NebulaClient.resolveQuestion(args.questionId, {
        resolvedBy: args.resolvedBy,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_list_question_answers",
    "List all answers for an open question (multi-role deliberation).",
    {
      questionId: z.string().describe("Question UUID"),
    },
    async (args) => {
      const result = await NebulaClient.listQuestionAnswers(args.questionId);
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  server.tool(
    "nebula_add_question_answer",
    "Add a new answer to an open question (multi-role deliberation). Multiple roles can answer the same question.",
    {
      questionId: z.string().describe("Question UUID to answer"),
      answer: z.string().describe("The answer text"),
      role: z.string().describe("Who answered (role name, e.g. 'analyst', 'architect')"),
      confidence: z.string().optional().describe("Confidence level: HIGH, MEDIUM, LOW (default: MEDIUM)"),
      reasoning: z.string().optional().describe("Step-by-step reasoning behind the answer"),
    },
    async (args) => {
      const result = await NebulaClient.addQuestionAnswer(args.questionId, {
        answer: args.answer,
        role: args.role,
        confidence: args.confidence,
        reasoning: args.reasoning,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  // ════════════════════════════════════════════════════════════════
  //  IMPLEMENTATION PLANS
  // ════════════════════════════════════════════════════════════════

  server.tool(
    "nebula_create_plan",
    "Create a new implementation plan in nebula.implementation_plans. " +
    "Generates the next plan number, inserts the record, and returns the created plan. " +
    "Receipts and tickets are handled downstream by conduit-mcp.",
    {
      title: z.string().describe("Plan title (required)"),
      project: z.string().optional().describe("Project name (default: 'nexus')"),
      goal: z.string().optional().describe("Goal description"),
      filesAffected: z.array(z.string()).optional().describe("List of files that will be affected"),
      acceptanceCriteria: z.array(z.string()).optional().describe("List of acceptance criteria"),
      dependencies: z.array(z.string()).optional().describe("List of dependency plan numbers"),
      promptRef: z.string().optional().describe("Optional prompt reference"),
    },
    async (args) => {
      const result = await NebulaClient.createPlan({
        title: args.title,
        project: args.project,
        goal: args.goal,
        filesAffected: args.filesAffected,
        acceptanceCriteria: args.acceptanceCriteria,
        dependencies: args.dependencies,
        promptRef: args.promptRef,
      });
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );

  // ════════════════════════════════════════════════════════════════
  //  HEALTH
  // ════════════════════════════════════════════════════════════════

  server.tool(
    "nebula_health",
    "Check nebula-srv health and database connectivity.",
    {},
    async () => {
      const result = await NebulaClient.health();
      return { content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }] };
    }
  );
}
