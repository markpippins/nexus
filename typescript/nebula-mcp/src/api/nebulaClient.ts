/**
 * HTTP client for nebula-srv REST API.
 * All nebula-mcp tools proxy through this client to nebula-srv (port 3101).
 */
import * as http from "http";

const NEBULA_SRV_URL = process.env.NEBULA_SRV_URL || "http://localhost:3101";

function httpGet(path: string): Promise<any> {
  return new Promise((resolve, reject) => {
    http.get(`${NEBULA_SRV_URL}${path}`, (res) => {
      let data = "";
      res.on("data", (chunk: string) => (data += chunk));
      res.on("end", () => {
        try {
          resolve(JSON.parse(data));
        } catch {
          reject(new Error(`Failed to parse nebula-srv response: ${data.slice(0, 200)}`));
        }
      });
    }).on("error", (err: Error) => {
      reject(new Error(`Cannot reach nebula-srv at ${NEBULA_SRV_URL}: ${err.message}`));
    });
  });
}

const BASE_URL = new URL(NEBULA_SRV_URL);

function httpRequest(method: string, path: string, body?: any): Promise<any> {
  return new Promise((resolve, reject) => {
    const bodyStr = body ? JSON.stringify(body) : undefined;
    const headers: Record<string, string> = {};
    if (bodyStr) {
      headers["Content-Type"] = "application/json";
      headers["Content-Length"] = Buffer.byteLength(bodyStr).toString();
    }
    const options: http.RequestOptions = {
      hostname: BASE_URL.hostname,
      port: BASE_URL.port,
      path,
      method,
      headers,
    };

    const req = http.request(options, (res) => {
      let data = "";
      res.on("data", (chunk: string) => (data += chunk));
      res.on("end", () => {
        try {
          const parsed = JSON.parse(data);
          resolve(parsed);
        } catch {
          resolve(data);
        }
      });
    });
    req.on("error", (err: Error) => {
      reject(new Error(`Cannot reach nebula-srv: ${err.message}`));
    });
    if (bodyStr) req.write(bodyStr);
    req.end();
  });
}

export const NebulaClient = {
  /** GET /health */
  health: () => httpGet("/health"),

  // ── Systems ────────────────────────────────────────────────
  /** GET /api/systems */
  listSystems: () => httpGet("/api/systems"),
  /** POST /api/systems */
  createSystem: (body: { name: string; description?: string; readme?: string | null; architecture?: string | null }) =>
    httpRequest("POST", "/api/systems", body),
  /** PATCH /api/systems/:id */
  updateSystem: (id: string, body: { name?: string; description?: string; readme?: string | null; architecture?: string | null }) =>
    httpRequest("PATCH", `/api/systems/${id}`, body),
  /** DELETE /api/systems/:id */
  deleteSystem: (id: string) => httpRequest("DELETE", `/api/systems/${id}`),

  // ── Subsystems ─────────────────────────────────────────────
  /** POST /api/subsystems */
  createSubsystem: (body: { systemId: string; name: string; description?: string; readme?: string | null }) =>
    httpRequest("POST", "/api/subsystems", body),
  /** PATCH /api/subsystems/:id */
  updateSubsystem: (id: string, body: { name?: string; description?: string; readme?: string | null; color?: string }) =>
    httpRequest("PATCH", `/api/subsystems/${id}`, body),
  /** DELETE /api/subsystems/:id */
  deleteSubsystem: (id: string) => httpRequest("DELETE", `/api/subsystems/${id}`),
  /** POST /api/subsystems/move */
  moveSubsystem: (body: { subsystemId: string; targetSystemId: string }) =>
    httpRequest("POST", "/api/subsystems/move", body),

  // ── Features ───────────────────────────────────────────────
  /** POST /api/features */
  createFeature: (body: { subsystemId: string; name: string; description?: string; readme?: string | null }) =>
    httpRequest("POST", "/api/features", body),
  /** PATCH /api/features/:id */
  updateFeature: (id: string, body: { name?: string; description?: string; readme?: string | null }) =>
    httpRequest("PATCH", `/api/features/${id}`, body),
  /** DELETE /api/features/:id */
  deleteFeature: (id: string) => httpRequest("DELETE", `/api/features/${id}`),
  /** POST /api/features/move */
  moveFeature: (body: { featureId: string; targetSystemId: string; targetSubsystemId: string }) =>
    httpRequest("POST", "/api/features/move", body),

  // ── Requirements ───────────────────────────────────────────
  /** GET /api/requirements?systemId=&subsystemId=&featureId= */
  listRequirements: (query?: { systemId?: string; subsystemId?: string; featureId?: string }) => {
    const params = new URLSearchParams();
    if (query?.systemId) params.set("systemId", query.systemId);
    if (query?.subsystemId) params.set("subsystemId", query.subsystemId);
    if (query?.featureId) params.set("featureId", query.featureId);
    const qs = params.toString();
    return httpGet(`/api/requirements${qs ? `?${qs}` : ""}`);
  },
  /** POST /api/requirements */
  createRequirement: (body: {
    systemId: string; subsystemId?: string | null; featureId?: string | null;
    title: string; description?: string; status?: string; priority?: string;
    startDate?: string | null; completionDate?: string | null;
    parentId?: string | null; reqType?: string | null;
    acceptanceCriteria?: string[] | null; candidateId?: string | null;
  }) => httpRequest("POST", "/api/requirements", body),
  /** PATCH /api/requirements/:id */
  updateRequirement: (id: string, body: {
    title?: string; description?: string; status?: string; priority?: string;
    startDate?: string | null; completionDate?: string | null;
    systemId?: string; subsystemId?: string; featureId?: string | null;
    parentId?: string | null; reqType?: string | null;
    acceptanceCriteria?: string[] | null; candidateId?: string | null;
  }) => httpRequest("PATCH", `/api/requirements/${id}`, body),
  /** DELETE /api/requirements/:id */
  deleteRequirement: (id: string) => httpRequest("DELETE", `/api/requirements/${id}`),
  /** PATCH /api/requirements/batch */
  batchUpdateRequirements: (body: { ids: string[]; status: string }) =>
    httpRequest("PATCH", "/api/requirements/batch", body),

  // ── Kanban Move (Plan 0131) ────────────────────────────────────
  /** POST /api/requirements/:id/move — single-id kanban move with optimistic-concurrency option */
  moveRequirement: (id: string, body: { targetStatus: string; expectedCurrentStatus?: string }) =>
    httpRequest("POST", `/api/requirements/${id}/move`, body),

  // ── System Folders ─────────────────────────────────────────
  /** POST /api/systems/:id/folders */
  createFolder: (systemId: string, body: { name: string; category: string; note?: string }) =>
    httpRequest("POST", `/api/systems/${systemId}/folders`, body),
  /** DELETE /api/systems/:systemId/folders/:folderId */
  deleteFolder: (systemId: string, folderId: string) =>
    httpRequest("DELETE", `/api/systems/${systemId}/folders/${folderId}`),

  // ── Work Sessions ──────────────────────────────────────────
  /** GET /api/sessions */
  listSessions: () => httpGet("/api/sessions"),
  /** POST /api/sessions */
  createSession: (body: {
    parentId: string; parentType: string; parentName?: string;
    context?: string; platform?: string; model?: string;
    outcome?: string | null; status?: string;
  }) => httpRequest("POST", "/api/sessions", body),
  /** PATCH /api/sessions/:id */
  updateSession: (id: string, body: { outcome?: string | null; status?: string }) =>
    httpRequest("PATCH", `/api/sessions/${id}`, body),
  /** DELETE /api/sessions/:id */
  deleteSession: (id: string) => httpRequest("DELETE", `/api/sessions/${id}`),

  // ── Workspaces ─────────────────────────────────────────────
  /** GET /api/workspaces */
  listWorkspaces: () => httpGet("/api/workspaces"),
  /** POST /api/workspaces */
  createWorkspace: (body: { systemId: string; subsystemId?: string | null; workspacePath: string }) =>
    httpRequest("POST", "/api/workspaces", body),
  /** DELETE /api/workspaces/:id */
  deleteWorkspace: (id: string) => httpRequest("DELETE", `/api/workspaces/${id}`),

  // ── Docs ───────────────────────────────────────────────────
  /** GET /api/docs?workspacePath=... */
  readDocs: (workspacePath: string) => httpGet(`/api/docs?workspacePath=${encodeURIComponent(workspacePath)}`),
  /** GET /api/systems/:id/docs */
  readSystemDocs: (systemId: string) => httpGet(`/api/systems/${systemId}/docs`),
  /** GET /api/subsystems/:id/docs */
  readSubsystemDocs: (subsystemId: string) => httpGet(`/api/subsystems/${subsystemId}/docs`),

  // ── Plans (Plan 0134) ────────────────────────────────────────
  /** GET /api/plans?status=pending|planning|proposed|completed|all */
  listPlans: (query?: { status?: 'pending' | 'planning' | 'proposed' | 'completed' | 'all' }) => {
    const params = new URLSearchParams();
    if (query?.status) params.set('status', query.status);
    const qs = params.toString();
    return httpGet(`/api/plans${qs ? `?${qs}` : ''}`);
  },
  /** GET /api/plans/:id (collision-resilient: first match in pending→planning→proposed→completed) */
  getPlan: (id: string) => httpGet(`/api/plans/${encodeURIComponent(id)}`),

  // ── Preferences ────────────────────────────────────────────
  /** GET /api/preferences */
  getPreferences: () => httpGet("/api/preferences"),
  /** PUT /api/preferences/:key */
  setPreference: (key: string, value: any) => httpRequest("PUT", `/api/preferences/${key}`, { value }),
  /** DELETE /api/preferences/:key */
  deletePreference: (key: string) => httpRequest("DELETE", `/api/preferences/${key}`),

  // ── System Info Tabs ───────────────────────────────────────
  /** GET /api/systems/:id/info */
  getSystemInfo: (systemId: string) => httpGet(`/api/systems/${systemId}/info`),
  /** PUT /api/systems/:id/info/:tabId */
  setSystemInfo: (systemId: string, tabId: string, content: string) =>
    httpRequest("PUT", `/api/systems/${systemId}/info/${tabId}`, { content }),

  // ── Complex Operations ─────────────────────────────────────
  /** POST /api/systems/demote/:id */
  demoteSystem: (sourceSystemId: string, targetSystemId: string) =>
    httpRequest("POST", `/api/systems/demote/${sourceSystemId}`, { targetSystemId }),

  // ── Harvests ───────────────────────────────────────────────
  /** GET /api/harvests?model=&version=&sourceHash=&level=&limit=&offset= */
  listHarvests: (query?: { model?: string; version?: number; sourceHash?: string; level?: number; visibilityScope?: string; limit?: number; offset?: number }) => {
    const params = new URLSearchParams();
    if (query?.model) params.set("model", query.model);
    if (query?.version !== undefined) params.set("version", String(query.version));
    if (query?.sourceHash) params.set("sourceHash", query.sourceHash);
    if (query?.level !== undefined) params.set("level", String(query.level));
    if (query?.visibilityScope) params.set("visibilityScope", query.visibilityScope);
    if (query?.limit) params.set("limit", String(query.limit));
    if (query?.offset) params.set("offset", String(query.offset));
    const qs = params.toString();
    return httpGet(`/api/harvests${qs ? `?${qs}` : ""}`);
  },
  /** GET /api/harvests/:id */
  getHarvest: (id: string) => httpGet(`/api/harvests/${encodeURIComponent(id)}`),
  /** POST /api/harvests */
  createHarvest: (body: {
    sourcePath: string; sourceFilename?: string; model?: string;
    totalCandidates?: number; candidates?: any[]; sourceText?: string;
    tags?: string[]; metadata?: any;
    level?: number; visibilityScope?: string;
    sourceHash?: string; runMetadata?: any;
  }) => httpRequest("POST", "/api/harvests", body),
  /** DELETE /api/harvests/:id */
  deleteHarvest: (id: string) => httpRequest("DELETE", `/api/harvests/${encodeURIComponent(id)}`),

  // ── Harvest Candidates ──────────────────────────────────────
  /** GET /api/harvest-candidates?harvestId=&systemId=&subsystemId=&featureId=&limit=&offset= */
  listHarvestCandidates: (query?: {
    harvestId?: string; systemId?: string; subsystemId?: string; featureId?: string;
    limit?: number; offset?: number;
  }) => {
    const params = new URLSearchParams();
    if (query?.harvestId) params.set("harvestId", query.harvestId);
    if (query?.systemId) params.set("systemId", query.systemId);
    if (query?.subsystemId) params.set("subsystemId", query.subsystemId);
    if (query?.featureId) params.set("featureId", query.featureId);
    if (query?.limit) params.set("limit", String(query.limit));
    if (query?.offset) params.set("offset", String(query.offset));
    const qs = params.toString();
    return httpGet(`/api/harvest-candidates${qs ? `?${qs}` : ""}`);
  },
  /** GET /api/harvest-candidates/:id */
  getHarvestCandidate: (id: string) => httpGet(`/api/harvest-candidates/${encodeURIComponent(id)}`),
  /** PATCH /api/harvest-candidates/:id */
  updateHarvestCandidate: (id: string, body: {
    title?: string; intentDescription?: string; status?: string;
    systemId?: string | null; subsystemId?: string | null; featureId?: string | null;
    tags?: string[]; planRef?: string;
    workRequestId?: string | null; completed?: boolean;
  }) => httpRequest("PATCH", `/api/harvest-candidates/${encodeURIComponent(id)}`, body),
  /** POST /api/harvest-candidates */
  createHarvestCandidate: (body: {
    harvestId: string; title: string; intentDescription?: string;
    implementationNotes?: any[]; codeSnippets?: any[]; openQuestions?: any[];
    tags?: string[]; status?: string;
    systemId?: string | null; subsystemId?: string | null; featureId?: string | null;
    planRef?: string;
  }) => httpRequest("POST", "/api/harvest-candidates", body),
  /** POST /api/harvest-candidates/discover — semantic search matching of unlinked candidates to hierarchy */
  discoverHarvestCandidates: (body: {
    candidateIds?: string[]; limit?: number; threshold?: number;
  }) => httpRequest("POST", "/api/harvest-candidates/discover", body),

  // ── Agent Records ──────────────────────────────────────────
  /** GET /api/agent-records?type=&role=&systemId=&subsystemId=&featureId=&planRef=&tag=&tags=&search=&createdAfter=&createdBefore=&level=&visibilityScope=&limit=&offset= */
  listAgentRecords: (query?: {
    type?: string; role?: string; systemId?: string; subsystemId?: string; featureId?: string; planRef?: string;
    tag?: string | string[]; search?: string;
    createdAfter?: string; createdBefore?: string;
    level?: number; visibilityScope?: string;
    limit?: number; offset?: number;
  }) => {
    const params = new URLSearchParams();
    if (query?.type) params.set("type", query.type);
    if (query?.role) params.set("role", query.role);
    if (query?.systemId) params.set("systemId", query.systemId);
    if (query?.subsystemId) params.set("subsystemId", query.subsystemId);
    if (query?.featureId) params.set("featureId", query.featureId);
    if (query?.planRef) params.set("planRef", query.planRef);
    // Support both single string and array of tags
    if (query?.tag) {
      const tags = Array.isArray(query.tag) ? query.tag : [query.tag];
      for (const t of tags) {
        params.append("tag", t);
      }
    }
    if (query?.search) params.set("search", query.search);
    if (query?.createdAfter) params.set("createdAfter", query.createdAfter);
    if (query?.createdBefore) params.set("createdBefore", query.createdBefore);
    if (query?.level !== undefined) params.set("level", String(query.level));
    if (query?.visibilityScope) params.set("visibilityScope", query.visibilityScope);
    if (query?.limit) params.set("limit", String(query.limit));
    if (query?.offset) params.set("offset", String(query.offset));
    const qs = params.toString();
    return httpGet(`/api/agent-records${qs ? `?${qs}` : ""}`);
  },
  /** GET /api/agent-records/:id */
  getAgentRecord: (id: string) => httpGet(`/api/agent-records/${encodeURIComponent(id)}`),
  /** POST /api/agent-records */
  createAgentRecord: (body: {
    recordType: string; role?: string; title?: string; content?: string;
    sourcePath?: string; metadata?: any; tags?: string[];
    systemId?: string; subsystemId?: string; featureId?: string; planRef?: string;
    level?: number; visibilityScope?: string; model?: string;
    /** Harness run uuid (HARNESS_JOB_ID) that wrote this record — V210. */
    harnessJobId?: string;
  }) => httpRequest("POST", "/api/agent-records", body),
  /** PATCH /api/agent-records/:id */
  updateAgentRecord: (id: string, body: {
    title?: string; content?: string; metadata?: any; tags?: string[];
    systemId?: string | null; subsystemId?: string | null; featureId?: string | null; planRef?: string | null;
    level?: number; visibilityScope?: string; model?: string | null;
  }) => httpRequest("PATCH", `/api/agent-records/${encodeURIComponent(id)}`, body),
  /** DELETE /api/agent-records/:id */
  deleteAgentRecord: (id: string) => httpRequest("DELETE", `/api/agent-records/${encodeURIComponent(id)}`),

  // ── Inbox Pointers ─────────────────────────────────────────
  /** GET /api/inbox-pointer/:role */
  getInboxPointer: (role: string) => httpGet(`/api/inbox-pointer/${encodeURIComponent(role)}`),
  /** PUT /api/inbox-pointer/:role */
  setInboxPointer: (role: string, timestamp: string) =>
    httpRequest("PUT", `/api/inbox-pointer/${encodeURIComponent(role)}`, { timestamp }),
  /** GET /api/inbox-pointers */
  getAllInboxPointers: () => httpGet("/api/inbox-pointers"),

  // ── Projections ────────────────────────────────────────────
  /** GET /api/projections */
  listProjections: () => httpGet("/api/projections"),
  /** POST /api/projections */
  createProjection: (body: {
    name: string; type: 'deterministic' | 'inference'; description?: string;
    sourceQuery?: string; template?: string; targetPath?: string;
    model?: string; schedule?: string; metadata?: any;
  }) => httpRequest("POST", "/api/projections", body),
  /** POST /api/projections/:id/render */
  renderProjection: (id: string) => httpRequest("POST", `/api/projections/${encodeURIComponent(id)}/render`),
  /** DELETE /api/projections/:id */
  deleteProjection: (id: string) => httpRequest("DELETE", `/api/projections/${encodeURIComponent(id)}`),

  // ── Cross-References ───────────────────────────────────────
  /** GET /api/cross-references?sourceType=&sourceId=&targetType=&targetId=&relType= */
  listCrossReferences: (query?: {
    sourceType?: string; sourceId?: string;
    targetType?: string; targetId?: string; relType?: string;
  }) => {
    const params = new URLSearchParams();
    if (query?.sourceType) params.set("sourceType", query.sourceType);
    if (query?.sourceId) params.set("sourceId", query.sourceId);
    if (query?.targetType) params.set("targetType", query.targetType);
    if (query?.targetId) params.set("targetId", query.targetId);
    if (query?.relType) params.set("relType", query.relType);
    const qs = params.toString();
    return httpGet(`/api/cross-references${qs ? `?${qs}` : ""}`);
  },
  /** GET /api/cross-references/:id */
  getCrossReference: (id: string) => httpGet(`/api/cross-references/${encodeURIComponent(id)}`),
  /** POST /api/cross-references */
  createCrossReference: (body: {
    sourceType: string; sourceId: string;
    targetType: string; targetId: string; relType: string; metadata?: any;
  }) => httpRequest("POST", "/api/cross-references", body),
  /** DELETE /api/cross-references/:id */
  deleteCrossReference: (id: string) => httpRequest("DELETE", `/api/cross-references/${encodeURIComponent(id)}`),

  // ── Evidence Links ──────────────────────────────────────────
  /** GET /api/evidence-links?knowledgeEntityId=&linkType=&provenance=&minConfidence=&maxConfidence=&limit=&offset= */
  listEvidenceLinks: (query?: {
    knowledgeEntityId?: string; nebulaHarvestId?: string; nebulaCandidateId?: string;
    linkType?: string; provenance?: string;
    minConfidence?: number; maxConfidence?: number;
    limit?: number; offset?: number;
  }) => {
    const params = new URLSearchParams();
    if (query?.knowledgeEntityId) params.set("knowledgeEntityId", query.knowledgeEntityId);
    if (query?.nebulaHarvestId) params.set("nebulaHarvestId", query.nebulaHarvestId);
    if (query?.nebulaCandidateId) params.set("nebulaCandidateId", query.nebulaCandidateId);
    if (query?.linkType) params.set("linkType", query.linkType);
    if (query?.provenance) params.set("provenance", query.provenance);
    if (query?.minConfidence != null) params.set("minConfidence", String(query.minConfidence));
    if (query?.maxConfidence != null) params.set("maxConfidence", String(query.maxConfidence));
    if (query?.limit) params.set("limit", String(query.limit));
    if (query?.offset) params.set("offset", String(query.offset));
    const qs = params.toString();
    return httpGet(`/api/evidence-links${qs ? `?${qs}` : ""}`);
  },
  /** GET /api/evidence-links/:id */
  getEvidenceLink: (id: string) => httpGet(`/api/evidence-links/${encodeURIComponent(id)}`),
  /** POST /api/evidence-links */
  createEvidenceLink: (body: {
    knowledgeEntityId: string;
    nebulaHarvestId?: string; nebulaCandidateId?: string;
    linkType: string;
    confidence?: number; provenance?: string; rationale?: string;
    sourceSpan?: any; metadata?: any;
  }) => httpRequest("POST", "/api/evidence-links", body),
  /** DELETE /api/evidence-links/:id */
  deleteEvidenceLink: (id: string) => httpRequest("DELETE", `/api/evidence-links/${encodeURIComponent(id)}`),
  /** DELETE /api/evidence-links?knowledgeEntityId= — bulk delete all links for an entity */
  deleteEvidenceLinksByEntity: (knowledgeEntityId: string) =>
    httpRequest("DELETE", `/api/evidence-links?knowledgeEntityId=${encodeURIComponent(knowledgeEntityId)}`),

  // ── Plan → Candidate reverse lookup ────────────────────────
  /** GET /api/plans/:planRef/candidates */
  getPlanCandidates: (planRef: string) =>
    httpGet(`/api/plans/${encodeURIComponent(planRef)}/candidates`),

  /** GET /api/systems/:id/harvest-candidates */
  getSystemHarvestCandidates: (systemId: string) =>
    httpGet(`/api/systems/${encodeURIComponent(systemId)}/harvest-candidates`),

  /** GET /api/subsystems/:id/harvest-candidates */
  getSubsystemHarvestCandidates: (subsystemId: string) =>
    httpGet(`/api/subsystems/${encodeURIComponent(subsystemId)}/harvest-candidates`),

  /** GET /api/features/:id/harvest-candidates */
  getFeatureHarvestCandidates: (featureId: string) =>
    httpGet(`/api/features/${encodeURIComponent(featureId)}/harvest-candidates`),

  // ── Candidate → Requirement spawn (full flow) ─────────────
  /** POST /api/harvest-candidates/:id/spawn-requirement — link + requirement + cross-reference
   * (renamed from spawn-plan per decision 319defa5) */
  spawnRequirementFromCandidate: (id: string, body: {
    systemId: string; subsystemId?: string | null; featureId?: string | null;
    planRef?: string; priority?: string; status?: string;
    title?: string; description?: string;
    parentId?: string | null; reqType?: string | null;
    acceptanceCriteria?: string[] | null;
  }) => httpRequest("POST", `/api/harvest-candidates/${encodeURIComponent(id)}/spawn-requirement`, body),

  // ── Conduit History (Plan 0169 recovery queries) ──────────
  /** GET /api/conduit/plans?includeDeleted=&asOf=&status=&limit=&offset= */
  listConduitPlans: (query?: {
    includeDeleted?: boolean; asOf?: string; status?: string;
    limit?: number; offset?: number;
  }) => {
    const params = new URLSearchParams();
    if (query?.includeDeleted) params.set("includeDeleted", "true");
    if (query?.asOf) params.set("asOf", query.asOf);
    if (query?.status) params.set("status", query.status);
    if (query?.limit) params.set("limit", String(query.limit));
    if (query?.offset) params.set("offset", String(query.offset));
    const qs = params.toString();
    return httpGet(`/api/conduit/plans${qs ? `?${qs}` : ""}`);
  },
  /** GET /api/conduit/plans/as-of?timestamp=&includeDeleted= */
  getConduitPlansAsOf: (timestamp: string, includeDeleted?: boolean) => {
    const params = new URLSearchParams();
    params.set("timestamp", timestamp);
    if (includeDeleted) params.set("includeDeleted", "true");
    return httpGet(`/api/conduit/plans/as-of?${params.toString()}`);
  },
  /** GET /api/conduit/plans/:id/history */
  getConduitPlanHistory: (planId: string) =>
    httpGet(`/api/conduit/plans/${encodeURIComponent(planId)}/history`),
  /** GET /api/conduit/plans/:id/receipts */
  getConduitPlanReceipts: (planId: string) =>
    httpGet(`/api/conduit/plans/${encodeURIComponent(planId)}/receipts`),
  /** GET /api/conduit/deleted-plans */
  listDeletedConduitPlans: () => httpGet("/api/conduit/deleted-plans"),

  // ── Op Mapping Registry ────────────────────────────────────
  /** POST /api/op-registry — create a new registry entry */
  createOpRegistryEntry: (body: {
    id: string; intent_id: string; version?: string; status?: string; label?: string;
    match_patterns?: string[]; opcode_template?: any[];
    required_params?: string[]; optional_params?: string[];
    preconditions?: string[]; postconditions?: string[];
    idempotency_key?: string; successor_id?: string; notes?: string;
  }) => httpRequest("POST", "/api/op-registry", body),
  /** GET /api/op-registry?intent_id=&status=&search=&limit=&offset= */
  listOpRegistry: (query?: {
    intent_id?: string; status?: string; search?: string;
    limit?: number; offset?: number;
  }) => {
    const params = new URLSearchParams();
    if (query?.intent_id) params.set("intent_id", query.intent_id);
    if (query?.status) params.set("status", query.status);
    if (query?.search) params.set("search", query.search);
    if (query?.limit) params.set("limit", String(query.limit));
    if (query?.offset) params.set("offset", String(query.offset));
    const qs = params.toString();
    return httpGet(`/api/op-registry${qs ? `?${qs}` : ""}`);
  },
  /** GET /api/op-registry/:id — get a single registry entry */
  getOpRegistryEntry: (id: string) =>
    httpGet(`/api/op-registry/${encodeURIComponent(id)}`),
  /** PATCH /api/op-registry/:id/deprecate — deprecate an entry */
  deprecateOpRegistryEntry: (id: string, successor_id?: string) =>
    httpRequest("PATCH", `/api/op-registry/${encodeURIComponent(id)}/deprecate`, { successor_id }),
  /** PATCH /api/op-registry/:id/supersede — mark as superseded */
  supersedeOpRegistryEntry: (id: string, successor_id: string) =>
    httpRequest("PATCH", `/api/op-registry/${encodeURIComponent(id)}/supersede`, { successor_id }),
  /** DELETE /api/op-registry/:id — soft-delete an entry */
  deleteOpRegistryEntry: (id: string) =>
    httpRequest("DELETE", `/api/op-registry/${encodeURIComponent(id)}`),
  /** POST /api/op-registry/fork — create new version of an intent mapping */
  forkOpRegistryEntry: (body: {
    source_id: string; new_version: string; label?: string; notes?: string;
    opcode_template?: any[]; required_params?: string[];
  }) => httpRequest("POST", "/api/op-registry/fork", body),
  /** GET /api/op-registry/:id/lineage — show version lineage */
  getOpRegistryLineage: (id: string) =>
    httpGet(`/api/op-registry/${encodeURIComponent(id)}/lineage`),

  // ── Review State (Plan 1041) ─────────────────────────────
  /** GET /api/review-state/:artifactId */
  getReviewState: (artifactId: string) =>
    httpGet(`/api/review-state/${encodeURIComponent(artifactId)}`),
  /** POST /api/review-state/:artifactId */
  setReviewState: (artifactId: string, body: {
    reviewStatus: string; reviewPriority: string; needsAttention: boolean;
    lastReviewedAt: string | null; reviewSummary: string | null;
    attentionReasons: string[]; annotatorRole: string;
    annotationNote?: string | null; previousStatus?: string | null;
  }) => httpRequest("POST", `/api/review-state/${encodeURIComponent(artifactId)}`, body),
  /** GET /api/review-state/:artifactId/annotations */
  listReviewAnnotations: (artifactId: string, query?: { limit?: number; offset?: number }) => {
    const params = new URLSearchParams();
    if (query?.limit) params.set("limit", String(query.limit));
    if (query?.offset) params.set("offset", String(query.offset));
    const qs = params.toString();
    return httpGet(`/api/review-state/${encodeURIComponent(artifactId)}/annotations${qs ? `?${qs}` : ""}`);
  },
  /** POST /api/review-state/bulk */
  bulkSetReviewState: (body: {
    artifactIds: string[]; reviewStatus: string; reviewPriority: string;
    needsAttention: boolean; lastReviewedAt: string | null;
    reviewSummary: string | null; attentionReasons: string[];
    annotatorRole: string;
  }) => httpRequest("POST", "/api/review-state/bulk", body),

  // ── Import / Seed ──────────────────────────────────────────
  /** POST /api/import */
  importData: (body: {
    systems?: any[]; requirements?: any[]; workSessions?: any[];
    preferences?: Record<string, any>; infoTabs?: Record<string, Record<string, string>>;
  }) => httpRequest("POST", "/api/import", body),
  /** POST /api/seed */
  seedData: () => httpRequest("POST", "/api/seed"),

  // ── Execution Authority (ADR-006) ──────────────────────────
  /** POST /api/execution/requests */
  createExecutionRequest: (body: {
    businessKey: string; title?: string; intentType?: string; objective?: string;
    inputs?: any; deterministic?: boolean; maxRetries?: number;
    timeoutPolicy?: string; resourceHints?: string[]; opTrace?: any;
    status?: string; sourcePlanId?: string; sourceWrId?: string;
  }) => httpRequest("POST", "/api/execution/requests", body),

  /** GET /api/execution/requests */
  listExecutionRequests: (query?: { status?: string; limit?: number }) => {
    const params = new URLSearchParams();
    if (query?.status) params.set("status", query.status);
    if (query?.limit) params.set("limit", String(query.limit));
    const qs = params.toString();
    return httpGet(`/api/execution/requests${qs ? `?${qs}` : ""}`);
  },

  /** GET /api/execution/requests/:id */
  getExecutionRequest: (id: string) =>
    httpGet(`/api/execution/requests/${encodeURIComponent(id)}`),

  /** PATCH /api/execution/requests/:id/transition */
  transitionExecutionRequest: (id: string, body: {
    targetStatus: string; reason?: string;
  }) => httpRequest("PATCH", `/api/execution/requests/${encodeURIComponent(id)}/transition`, body),

  /** POST /api/execution/leases/acquire */
  acquireLease: (body: {
    requestId: string; executorId: string; ttlSeconds?: number;
  }) => httpRequest("POST", "/api/execution/leases/acquire", body),

  /** POST /api/execution/leases/:id/renew */
  renewLease: (id: string, body: { ttlSeconds?: number }) =>
    httpRequest("POST", `/api/execution/leases/${encodeURIComponent(id)}/renew`, body),

  /** POST /api/execution/leases/:id/release */
  releaseLease: (id: string) =>
    httpRequest("POST", `/api/execution/leases/${encodeURIComponent(id)}/release`),

  /** POST /api/execution/attempts */
  submitAttempt: (body: {
    leaseId: string; status?: string; result?: any;
    error?: string; exitCode?: number;
  }) => httpRequest("POST", "/api/execution/attempts", body),

  /** POST /api/role-leases/issue (RoleLeases, plan 1286) */
  issueRoleLease: (body: {
    role: string; channel?: string; model?: string;
    ttlSeconds?: number; budgetUnits?: number; windowEnd?: string;
  }) => httpRequest("POST", "/api/role-leases/issue", body),

  /** POST /api/role-leases/:id/renew */
  renewRoleLease: (id: string, body: { ttlSeconds?: number; budgetUnits?: number }) =>
    httpRequest("POST", `/api/role-leases/${encodeURIComponent(id)}/renew`, body),

  /** POST /api/role-leases/:id/revoke */
  revokeRoleLease: (id: string) =>
    httpRequest("POST", `/api/role-leases/${encodeURIComponent(id)}/revoke`),

  /** GET /api/role-leases?role=&status=&limit= */
  listRoleLeases: (params?: { role?: string; status?: string; limit?: number }) =>
    httpRequest("GET", `/api/role-leases?${new URLSearchParams(
      Object.entries(params || {}).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])
    )}`),

  /** POST /api/execution/receipts */
  issueReceipt: (body: {
    attemptId: string; type?: string; agentRole?: string;
    summary?: string; metadata?: any;
  }) => httpRequest("POST", "/api/execution/receipts", body),

  /** GET /api/execution/receipts */
  listExecutionReceipts: (query?: { requestId?: string; type?: string; limit?: number }) => {
    const params = new URLSearchParams();
    if (query?.requestId) params.set("requestId", query.requestId);
    if (query?.type) params.set("type", query.type);
    if (query?.limit) params.set("limit", String(query.limit));
    const qs = params.toString();
    return httpGet(`/api/execution/receipts${qs ? `?${qs}` : ""}`);
  },

  /** GET /api/execution/state */
  getExecutionState: () => httpGet("/api/execution/state"),

  // ── Open Questions ─────────────────────────────────────────
  /** GET /api/open-questions */
  listOpenQuestions: (query?: { requirementId?: string; candidateId?: string; status?: string }) => {
    const params = new URLSearchParams();
    if (query?.requirementId) params.set("requirementId", query.requirementId);
    if (query?.candidateId) params.set("candidateId", query.candidateId);
    if (query?.status) params.set("status", query.status);
    const qs = params.toString();
    return httpGet(`/api/open-questions${qs ? `?${qs}` : ""}`);
  },

  /** PUT /api/open-questions/:id/answer */
  answerQuestion: (id: string, body: { answer: string; answeredBy: string }) =>
    httpRequest("PUT", `/api/open-questions/${encodeURIComponent(id)}/answer`, body),

  /** PUT /api/open-questions/:id/resolve */
  resolveQuestion: (id: string, body: { resolvedBy: string }) =>
    httpRequest("PUT", `/api/open-questions/${encodeURIComponent(id)}/resolve`, body),

  /** GET /api/open-questions/:id/answers */
  listQuestionAnswers: (questionId: string) =>
    httpGet(`/api/open-questions/${encodeURIComponent(questionId)}/answers`),

  /** POST /api/open-questions/:id/answers */
  addQuestionAnswer: (questionId: string, body: { answer: string; role: string; confidence?: string; reasoning?: string }) =>
    httpRequest("POST", `/api/open-questions/${encodeURIComponent(questionId)}/answers`, body),

  // ════════════════════════════════════════════════════════════════
  //  IMPLEMENTATION PLANS (Plan 040)
  // ════════════════════════════════════════════════════════════════

  /** POST /api/plans */
  createPlan: (body: {
    title: string;
    project?: string;
    goal?: string;
    filesAffected?: string[];
    acceptanceCriteria?: string[];
    dependencies?: string[];
    promptRef?: string;
  }) => httpRequest("POST", "/api/plans", body),
};
