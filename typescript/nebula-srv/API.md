# nebula-srv — Nebula Knowledge-Graph API

> Port: **3101**  
> REST reference: `API.md` · OpenAPI spec: [`openapi.yaml`](./openapi.yaml)

Canonical asset graph: systems, subsystems, features, documents, harvests, agent records, projections, knowledge graph, and cross-references.

**227 endpoints** — inventory generated from source route registrations (`nexus/tools/api-docs/`).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/agendas` | GET /api/agendas — list ALL agendas (unscoped, when no hierarchy selected) |
| POST | `/api/agendas` | AGENDAS (scoped by hierarchy via agenda_items → requirements) POST /api/agendas — create a new agenda. INTERIM scheme 2026-08-25: the engineering loop's spec-row path is agenda → items → finalize (agenda_to_specification); superseded by Wind doctrine. |
| GET | `/api/agendas/:id` | GET /api/agendas/:id — single agenda with items |
| POST | `/api/agendas/:id/finalize` | POST /api/agendas/:id/finalize — create a specification from an agenda |
| DELETE | `/api/agendas/:id/items` | DELETE /api/agendas/:id/items — remove an agenda item by source_id Query: ?sourceId=<uuid> — finds and deletes the item matching that source |
| POST | `/api/agendas/:id/items` | POST /api/agendas/:id/items — add a single item to an existing agenda |
| GET | `/api/agent-records` |  |
| POST | `/api/agent-records` | POST /api/agent-records — create a new agent record (canonical write path) |
| DELETE | `/api/agent-records/:id` | DELETE /api/agent-records/:id |
| GET | `/api/agent-records/:id` | GET /api/agent-records — list records with optional filters and pagination GET /api/agent-records/:id — full single record INCLUDING content. Fixes finding 6d731551: REST had no content-bearing read (list omits content; ?id= was fuzzy search), so REST-only consumers misread persisted records as empt |
| PATCH | `/api/agent-records/:id` | PATCH /api/agent-records/:id — update record fields |
| POST | `/api/agent-records/search` | POST /api/agent-records/search — multi-tag AND/OR agent record search |
| GET | `/api/architect-specs` | ARCHITECT SPECS GET /api/architect-specs — list with pagination |
| POST | `/api/architect-specs` | POST /api/architect-specs — create |
| DELETE | `/api/architect-specs/:id` | DELETE /api/architect-specs/:id |
| GET | `/api/architect-specs/:id` | GET /api/architect-specs/:id — detail |
| GET | `/api/artifact-provenance` | ARTIFACT PROVENANCE GET /api/artifact-provenance — list with pagination |
| POST | `/api/artifact-provenance` | POST /api/artifact-provenance — create |
| DELETE | `/api/artifact-provenance/:id` | DELETE /api/artifact-provenance/:id |
| GET | `/api/artifact-provenance/:id` | GET /api/artifact-provenance/:id — detail |
| GET | `/api/assessments` | ASSESSMENTS GET /api/assessments — list with pagination |
| GET | `/api/assessments/:id` | GET /api/assessments/:id — single assessment |
| GET | `/api/attestations` | 84ca2388 — canonical rows are record_type=assessment with type:approval + status:done; legacy rows carry type:attestation. Intent/status-update/finding records never qualify, so the gate's match is exact and index-backed (GIN on tags, migration 055) instead of a bounded newest-N scan. Tester role is |
| GET | `/api/audit` | GET /api/audit — list all audit files with pagination |
| GET | `/api/audit/:id` | GET /api/audit/:id — get single audit file with content |
| POST | `/api/audit/:id/regenerate` | POST /api/audit/:id/regenerate — re-read this specific file from disk into DB |
| GET | `/api/audit/graph` | GET /api/audit/graph — agent records as nodes, cross-references as edges ⚠ MUST be before /audit/:id to avoid Express matching 'graph' as a UUID |
| POST | `/api/audit/sync` | POST /api/audit/sync — scan filesystem and upsert all audit files |
| GET | `/api/candidates` | /candidates alias — mirrors /harvest-candidates for Assembly UI |
| GET | `/api/candidates/:id` |  |
| GET | `/api/cascade/subscriber-status` | cascade interactive-turn subscriber (the daemon that turns duality comments into agent turns). The subscriber tags its PG connection with application_name='cascade-interactive-turn'; when the daemon dies its socket closes and the backend disappears from pg_stat_activity. The duality-ui TopBar polls  |
| GET | `/api/conduit/deleted-plans` | GET /api/conduit/deleted-plans — shortcut to find all soft-deleted plans |
| GET | `/api/conduit/plans` | CONDUIT — plan history & point-in-time queries (conduit + vision schemas) Reads from nebula.plans, nebula.receipts_unified, vision.tickets via fully qualified table names (pool search_path=nebula). GET /api/conduit/plans — list all conduit plans, option to include soft-deleted Query params: includeD |
| GET | `/api/conduit/plans/:id/history` | GET /api/conduit/plans/:id/history — full lifecycle history for one plan Returns plan metadata (even if deleted), all receipts, all tickets, linked sessions, token usage |
| GET | `/api/conduit/plans/:id/receipts` | GET /api/conduit/plans/:id/receipts — receipts for a specific plan |
| GET | `/api/conduit/plans/as-of` | GET /api/conduit/plans/as-of — point-in-time snapshot of plan states Query params: timestamp (ISO 8601, required), includeDeleted |
| GET | `/api/conversations` | GET /api/conversations — paginated list of conversation snapshots |
| GET | `/api/conversations/:id/blocks` | GET /api/conversations/:id/blocks — get blocks for the latest snapshot of a conversation |
| GET | `/api/conversations/:id/snapshots` | GET /api/conversations/:id/snapshots — list all snapshots for a conversation |
| GET | `/api/conversations/by-snapshot/:snapshotId` | for a *conversation_id*); this returns the single snapshot whose `id` equals the supplied UUID — the exact semantics assembly-srv's former `GET /api/conversations/:id` provided (where `:id` was the snapshot id). Returns the snapshot row enriched with `source_filename` from the harvests join (same co |
| GET | `/api/conversations/by-snapshot/:snapshotId/blocks` | a cross-reference, or a previous /api/snapshots fetch). Returning the same envelope shape as `/api/conversations/:id/blocks` and `/api/snapshots/:id/blocks` (`{ snapshotId, blocks }`) lets assembly-srv act as a transparent proxy without a response-shape transform — see assembly-srv `routes/conversat |
| GET | `/api/counts` | COUNTS (aggregate row counts) GET /api/counts |
| GET | `/api/cpf` | CPF — COMPILATION READINESS FUNNEL (replaces cpf_api.py port 3108) GET /api/cpf — query candidates with readiness scores |
| GET | `/api/cpf/count` | GET /api/cpf/count — readiness band counts |
| POST | `/api/cpf/promote` | POST /api/cpf/promote — promote a candidate |
| GET | `/api/cross-references` | GET /api/cross-references |
| POST | `/api/cross-references` | CROSS-REFERENCES POST /api/cross-references |
| DELETE | `/api/cross-references/:id` | DELETE /api/cross-references/:id — soft-delete (expire) |
| GET | `/api/cross-references/:id` | GET /api/cross-references/:id |
| GET | `/api/docs` | GET /api/docs — read README.md and ARCHITECTURE.md from workspace directory Query params: workspacePath (relative to nexus root), e.g. typescript/conduit-mcp |
| DELETE | `/api/evidence-links` | DELETE /api/evidence-links?knowledgeEntityId=... — bulk delete all links for an entity |
| GET | `/api/evidence-links` | GET /api/evidence-links |
| POST | `/api/evidence-links` | EVIDENCE LINKS — typed harvest→knowledge bridge POST /api/evidence-links |
| DELETE | `/api/evidence-links/:id` | DELETE /api/evidence-links/:id |
| GET | `/api/evidence-links/:id` | GET /api/evidence-links/:id |
| POST | `/api/execution/attempts` | POST /api/execution/attempts — submit an attempt (create + set outcome) |
| POST | `/api/execution/leases/:id/release` | POST /api/execution/leases/:id/release — release an active lease |
| POST | `/api/execution/leases/:id/renew` | POST /api/execution/leases/:id/renew — renew an active lease |
| POST | `/api/execution/leases/acquire` | POST /api/execution/leases/acquire — acquire a lease on a request |
| GET | `/api/execution/receipts` | GET /api/execution/receipts — list receipts |
| POST | `/api/execution/receipts` | POST /api/execution/receipts — issue a receipt from an attempt |
| GET | `/api/execution/requests` | GET /api/execution/requests — list requests |
| POST | `/api/execution/requests` | EXECUTION AUTHORITY (ADR-006) POST /api/execution/requests — create a new WorkRequest |
| GET | `/api/execution/requests/:id` | GET /api/execution/requests/:id — get a single request |
| PATCH | `/api/execution/requests/:id/transition` | PATCH /api/execution/requests/:id/transition — transition WorkRequest status |
| GET | `/api/execution/state` | GET /api/execution/state — summary of execution domain state |
| GET | `/api/external-ids` | GET /api/external-ids — reverse lookup via asset_relation |
| PATCH | `/api/external-ids/:id` | PATCH /api/external-ids/:id — deprecated |
| POST | `/api/features` | FEATURES POST /api/features |
| DELETE | `/api/features/:id` | DELETE /api/features/:id — cascade deletes requirements with feature_id |
| GET | `/api/features/:id` | GET /api/features/:id — single feature |
| PATCH | `/api/features/:id` | PATCH /api/features/:id |
| GET | `/api/features/:id/agendas` | GET /api/features/:id/agendas — list agendas scoped to a feature, with nested items |
| GET | `/api/features/:id/harvest-candidates` | GET /api/features/:id/harvest-candidates — list all harvest candidates linked to a specific feature (filter by feature_id). |
| GET | `/api/features/:id/implementation-plans` | GET /api/features/:id/implementation-plans — plans linked to a feature |
| GET | `/api/features/:id/specifications` | GET /api/features/:id/specifications — list specification revisions scoped to a feature |
| GET | `/api/features/:id/work-requests` | GET /api/features/:id/work-requests — list work requests scoped to a feature |
| POST | `/api/features/move` | COMPLEX OPERATIONS (transactional) POST /api/features/move — re-parent a feature to a different subsystem |
| GET | `/api/harvest-candidates` | GET /api/harvest-candidates — list candidates, filterable by harvest or hierarchy Sweep-tooling extras (to-do 7e2d116f): ?completed=true\|false, ?status=<value>, and an endpoint-local page cap of 1000 (global default stays 100) so a sweep can pull the full ~100+ candidate set in one call. `total` is  |
| POST | `/api/harvest-candidates` | POST /api/harvest-candidates — create a standalone candidate (e.g. manually linked). When systemId is set, auto-upserts a harvest_context info tab on the target system. |
| GET | `/api/harvest-candidates/:id` | GET /api/harvest-candidates/:id — full candidate with all fields |
| PATCH | `/api/harvest-candidates/:id` | PATCH /api/harvest-candidates/:id — update candidate (primarily for linking to hierarchy) When systemId is set, auto-upserts the candidate's intent into a harvest_context info tab. |
| GET | `/api/harvest-candidates/:id/completion` |  |
| GET | `/api/harvest-candidates/:id/dependencies` | CANDIDATE DEPENDENCIES sub-resource GET /api/harvest-candidates/:id/dependencies |
| POST | `/api/harvest-candidates/:id/promote` | POST /api/harvest-candidates/:id/promote — mark candidate as useful |
| POST | `/api/harvest-candidates/:id/spawn-plan` | POST /api/harvest-candidates/:id/spawn-plan — DEPRECATED alias (decision 319defa5): candidates promote ONLY to requirements; verb renamed to spawn-requirement. Fails loudly with a pointer so callers migrate. |
| POST | `/api/harvest-candidates/:id/spawn-requirement` | POST /api/harvest-candidates/:id/spawn-requirement — full flow: link candidate to system, create a requirement derived from the candidate, and optionally cross-reference a conduit plan — all in one atomic transaction. (Renamed from spawn-plan per decision 319defa5.) |
| POST | `/api/harvest-candidates/completion-sweep` | Batch variant: POST /api/harvest-candidates/completion-sweep { ids: [...] } Missing/unknown ids are reported per-id rather than failing the batch. |
| POST | `/api/harvest-candidates/discover` | HARVEST CANDIDATE DISCOVERY — semantic search against project hierarchy POST /api/harvest-candidates/discover — match unlinked candidates to systems/subsystems/features via semantic search, flagging undocumented projects below confidence threshold. |
| POST | `/api/harvest-candidates/promote-to-plan` | POST /api/harvest-candidates/promote-to-plan — RETIRED (architect ruling on to-do e68449f2): the backing nebula.candidates_to_plan() procedure was dropped in a migration and must NOT be resurrected — spawn_requirement_from_candidate is the canonical promotion path (MCP-surfaced, sets requirements.ca |
| GET | `/api/harvests` | HARVESTS — database-first harvest pipeline output GET /api/harvests — list all harvests with sort/filter support + pagination sort options: candidate_count, code_blocks, turns, block_density, collaboration, created_at |
| POST | `/api/harvests` | POST /api/harvests — create a new harvest record AND unpack candidates into harvest_candidates (dual-write: JSONB preserved for Rover + relational for linking) |
| DELETE | `/api/harvests/:id` | DELETE /api/harvests/:id |
| GET | `/api/harvests/:id` | GET /api/harvests/:id — full harvest with candidates |
| GET | `/api/harvests/:id/transcript` | GET /api/harvests/:id/transcript — reconstructed conversation with code/diagrams |
| GET | `/api/harvests/distribution` | GET /api/harvests/distribution — analytics histograms across all harvests |
| GET | `/api/health` | Mount /api/health too — the Nebula UI proxy forwards /api/health here |
| GET | `/api/implementation-plans/statuses` | GET /api/implementation-plans/statuses — distinct status values for filter tabs |
| POST | `/api/import` | IMPORT / SEED POST /api/import — bulk import from localStorage migration |
| GET | `/api/inbox-pointer/:role` | INBOX POINTERS — per-role watermark for unread messages GET /api/inbox-pointer/:role — get the inbox pointer for a role |
| PUT | `/api/inbox-pointer/:role` | PUT /api/inbox-pointer/:role — set the inbox pointer for a role |
| GET | `/api/inbox-pointers` | GET /api/inbox-pointers — list all inbox pointers (debugging) |
| GET | `/api/inventory` | GET /api/inventory — rollup counts for the full hierarchy tree Returns per-node counts (systems/subsystems/features) for tree badges plus global totals. Single query, no per-node N+1. |
| GET | `/api/knowledge/cross-references` | GET /api/knowledge/cross-references — list cross-references for graph overlay with pagination. Also includes harvest_candidate spawn-requirement cross-references from nebula.cross_references. |
| GET | `/api/knowledge/edges` | GET /api/knowledge/edges — list graph edges with optional filters and pagination |
| GET | `/api/knowledge/entities` | KNOWLEDGE GRAPH — read-only queries for graph visualization GET /api/knowledge/entities — list knowledge graph entities with optional filters and pagination |
| GET | `/api/knowledge/entities/:section/:entityId` | GET /api/knowledge/entities/:section/:entityId — get single entity |
| GET | `/api/knowledge/entities/:section/:entityId/relations` | GET /api/knowledge/entities/:section/:entityId/relations — inbound + outbound with pagination |
| GET | `/api/knowledge/summary` | GET /api/knowledge/summary — entity counts by section (with embedded), edge counts by relation type |
| GET | `/api/knowledge/view` | GET /api/knowledge/view — combined data payload for graph visualization Returns all entities (including linked harvest_candidates) + all edges in one call, with optional limit. Harvest_candidates are unioned so spawn-requirement cross-references render as dashed edges in graph-view X-Refs mode. |
| GET | `/api/observations` | OBSERVATIONS GET /api/observations — list with pagination |
| GET | `/api/observations/:id` | GET /api/observations/:id — single observation |
| GET | `/api/op-registry` | GET /api/op-registry — list registry entries with optional filters and pagination |
| POST | `/api/op-registry` | OP MAPPING REGISTRY — versioned intent→opcode mapping table Schema: nebula.op_registry POST /api/op-registry — create a new registry entry |
| DELETE | `/api/op-registry/:id` | DELETE /api/op-registry/:id — soft-delete a registry entry |
| GET | `/api/op-registry/:id` | GET /api/op-registry/:id — get a single registry entry |
| PATCH | `/api/op-registry/:id/deprecate` | PATCH /api/op-registry/:id/deprecate — deprecate a registry entry |
| GET | `/api/op-registry/:id/lineage` | GET /api/op-registry/:id/lineage — show the version lineage of an intent |
| PATCH | `/api/op-registry/:id/supersede` | PATCH /api/op-registry/:id/supersede — mark as superseded (replaced by fork) |
| POST | `/api/op-registry/fork` | POST /api/op-registry/fork — create a new version of an existing intent mapping |
| GET | `/api/open-questions` | OPEN QUESTIONS GET /api/open-questions?requirementId=&candidateId=&status=&entityType=&entityId= |
| POST | `/api/open-questions` | POST /api/open-questions — create a new open question |
| GET | `/api/open-questions/:id` | OPEN QUESTIONS — detail (list already exists above) GET /api/open-questions/:id — single open question |
| PUT | `/api/open-questions/:id/answer` | PUT /api/open-questions/:id/answer — legacy single-answer endpoint (backwards compat) Now also inserts into open_question_answers table. |
| GET | `/api/open-questions/:id/answers` | GET /api/open-questions/:id/answers — list only currently-valid answers Queries the open_question_answers VIEW (which enforces bitemporal filtering). Excludes temporal housekeeping columns from the response. |
| POST | `/api/open-questions/:id/answers` | POST /api/open-questions/:id/answers — record answer via stored procedure The procedure handles: expire old answer, version increment, INSERT, answered_by pointer update, and pg_notify('open_question_answered'). |
| GET | `/api/open-questions/:id/participants` | OPEN QUESTIONS — participants sub-resource GET /api/open-questions/:id/participants |
| POST | `/api/open-questions/:id/participants` | POST /api/open-questions/:id/participants |
| PUT | `/api/open-questions/:id/resolve` | PUT /api/open-questions/:id/resolve |
| GET | `/api/open-questions/:id/timeline` | GET /api/open-questions/:id/timeline — deliberation history |
| GET | `/api/plans` | PLANS DISPLAY (Plan 0134) GET /api/plans — list implementation plans with pagination ?status=archived\|pending\|... & ?page=N & ?pageSize=N |
| POST | `/api/plans` | POST /api/plans — create a new implementation plan Writes directly to nebula.implementation_plans (the TABLE, not the view). Receipts and tickets are handled downstream by conduit-mcp. |
| GET | `/api/plans/:id` | GET /api/plans/:id — fetch a single implementation plan by plan_number |
| GET | `/api/plans/:planRef/candidates` | HARVEST CANDIDATES — normalized relational access to harvest data GET /api/plans/:planRef/candidates — reverse lookup: find all harvest_candidates linked to a given conduit plan via cross_references. |
| GET | `/api/preferences` | USER PREFERENCES GET /api/preferences — get all preferences for the default user |
| DELETE | `/api/preferences/:key` | DELETE /api/preferences/:key — delete a single preference (reset to default) |
| PUT | `/api/preferences/:key` | PUT /api/preferences/:key — set a single preference |
| POST | `/api/projection-overrides` | POST /api/projection-overrides — add a suppression/deprioritization override |
| DELETE | `/api/projection-overrides/:id` | DELETE /api/projection-overrides/:id — remove an override |
| GET | `/api/projections` | PROJECTIONS — on-demand markdown folder generation GET /api/projections — list all projection configs |
| POST | `/api/projections` | POST /api/projections — create a projection config |
| DELETE | `/api/projections/:id` | DELETE /api/projections/:id |
| POST | `/api/projections/:id/render` | POST /api/projections/:id/render — execute a projection and write output files |
| POST | `/api/refresh-stats` | POST /api/refresh-stats — refresh materialized views SECURITY: matviewname is validated against a strict PostgreSQL identifier pattern before interpolation. REFRESH MATERIALIZED VIEW does not accept parameterized identifiers ($1), so we sanitize via regex instead. |
| GET | `/api/requirements` | REQUIREMENTS GET /api/requirements — filterable with pagination |
| POST | `/api/requirements` | POST /api/requirements |
| DELETE | `/api/requirements/:id` | DELETE /api/requirements/:id |
| GET | `/api/requirements/:id` | GET /api/requirements/:id — single requirement by ID |
| PATCH | `/api/requirements/:id` | PATCH /api/requirements/:id |
| GET | `/api/requirements/:id/children` | GET /api/requirements/:id/children — fetch direct child requirements with pagination |
| POST | `/api/requirements/:id/compile` | REQUIREMENT → WORKREQUEST COMPILATION (Plan 1062) POST /api/requirements/:id/compile — compile a requirement into a WorkRequest IR Runs the two-stage compiler (Stage 1 normalization + Stage 2 op_registry compilation). Optionally creates a conduit plan if createPlan=true. |
| GET | `/api/requirements/:id/dependencies` | GET /api/requirements/:id/dependencies — list blockers and blocked-by with pagination |
| POST | `/api/requirements/:id/dependencies` | POST /api/requirements/:id/dependencies — create a dependency link |
| DELETE | `/api/requirements/:id/dependencies/:depId` | DELETE /api/requirements/:id/dependencies/:depId — remove a dependency link |
| POST | `/api/requirements/:id/move` | SYSTEM FOLDERS POST /api/requirements/:id/move — kanban-friendly single-id status move (Plan 0131) |
| PATCH | `/api/requirements/batch` | PATCH /api/requirements/batch — batch status update (BEFORE /:id!) |
| GET | `/api/role-leases` | GET /api/role-leases — list role leases (filters: role, status) |
| POST | `/api/role-leases/:id/renew` | POST /api/role-leases/:id/renew — renew an ACTIVE lease (window + budget) |
| POST | `/api/role-leases/:id/revoke` | POST /api/role-leases/:id/revoke — release an ACTIVE role lease |
| GET | `/api/role-leases/:role/status` | GET /api/role-leases/:role/status — derived lease state for (role, channel) (D-2026-08-16-008 R1). NEVER_LEASED / ACTIVE / REVOKED / EXPIRED. |
| POST | `/api/role-leases/consume` | POST /api/role-leases/consume — increment consumed_units (all channels) Unified accounting: execution_worker, harness-srv, and interactive Freebuff all hit this one endpoint for lease consumption. When the budget is exhausted, the endpoint auto-revokes the lease and emits a type:lease-exhausted agen |
| POST | `/api/role-leases/issue` | ROLE LEASES (RoleLeases / plan 1286) — session-level leases in tackle schema: a bounded window + budget under which a role on a channel may consume work. Mirrors execution.leases (per-request) at role scope. POST /api/role-leases/issue — issue an ACTIVE role lease |
| GET | `/api/role-leases/stale` | GET /api/role-leases/stale — ACTIVE leases past window/budget (for sweep) |
| POST | `/api/role-leases/sweep` | POST /api/role-leases/sweep — transition stale ACTIVE leases → EXPIRED (D-2026-08-16-007 R5). Idempotent; non-destructive (never RELEASED — that stays the explicit-revoke/auto-exhaust path). Each swept lease emits a type:drift-finding record so the operator sees it. Wired at nebula-srv startup + on  |
| GET | `/api/roles` | ROLES GET /api/roles — list all roles (governance roles with capabilities) |
| POST | `/api/roles` | POST /api/roles — create role metadata (Gap 2: nebula.roles create API) |
| DELETE | `/api/roles/:id` | DELETE /api/roles/:id — remove role metadata (Gap 2). Accepts a UUID or a role name (architect review: previously UUID-only). Hard delete guarded: FK references from wind.titles / nebula.roles_history surface as 23503 → 409 with a hint instead of a raw PG error. |
| GET | `/api/roles/:id` | GET /api/roles/:id — single role |
| PATCH | `/api/roles/:id` | PATCH /api/roles/:id — update capabilities/visibility/description (Gap 2) |
| GET | `/api/roles/drift` | (D-2026-08-16-009 R5). Three planes: governance = nebula.roles (current bitemporal view) + roles_history runtime = tackle.roles (runtime personas) execution = tackle.role_leases (ACTIVE leases) Read-only: surfaces drift findings, never silently reconciles. Registered BEFORE /roles/:id so 'drift' is  |
| GET | `/api/search` | SEARCH (cross-entity full-text) GET /api/search?q=... |
| POST | `/api/search/semantic` | SEMANTIC SEARCH POST /api/search/semantic — vector similarity search against knowledge graph Accepts a pre-embedded query vector (768-dim, matching nomic-embed-text) and returns similar entities from knowledge.graph_entity_embeddings. |
| POST | `/api/seed` | POST /api/seed — seed default example data (Plan 0087, idempotent, atomic) |
| POST | `/api/segments` | POST /api/segments — commit a user-defined segment |
| DELETE | `/api/segments/:id` | DELETE /api/segments/:id — supersede (bitemporal expire) a segment |
| PATCH | `/api/segments/:id` | PATCH /api/segments/:id — update segment (type, state, title, notes) |
| GET | `/api/sessions` | WORK SESSIONS GET /api/sessions — list with pagination |
| POST | `/api/sessions` | POST /api/sessions |
| DELETE | `/api/sessions/:id` | DELETE /api/sessions/:id |
| PATCH | `/api/sessions/:id` | PATCH /api/sessions/:id |
| POST | `/api/snapshots` | POST /api/snapshots — create a new conversation snapshot with blocks |
| GET | `/api/snapshots/:id/blocks` | GET /api/snapshots/:id/blocks — list blocks with optional diff from a previous snapshot |
| GET | `/api/snapshots/:id/projection` | GET /api/snapshots/:id/projection — get the BP projection for a snapshot |
| GET | `/api/snapshots/:id/references` | GET /api/snapshots/:id/references — get harvest references for a snapshot |
| GET | `/api/specifications` | SPECIFICATIONS (settled output from agendas — scoped via specs view) GET /api/specifications — list ALL specification revisions (unscoped, from nebula.specifications versioned snapshots) |
| GET | `/api/specifications/:id` | GET /api/specifications/:id — single specification revision |
| POST | `/api/specifications/:id/link-requirements` | POST /api/specifications/:id/link-requirements — create cross-references from specification to requirements by matching candidate_ids in the item_snapshot |
| GET | `/api/specs` | SPECS (flattened agenda_items WHERE included=true — distinct from /api/specifications which returns revision snapshots from nebula.active_specifications) GET /api/specs — paginated list of spec items (flattened agenda_items) |
| GET | `/api/specs/:id` | GET /api/specs/:id — single spec item |
| POST | `/api/subsystems` | SUBSYSTEMS POST /api/subsystems |
| DELETE | `/api/subsystems/:id` | DELETE /api/subsystems/:id — cascade deletes features and requirements |
| GET | `/api/subsystems/:id` | GET /api/subsystems/:id — single subsystem with features |
| PATCH | `/api/subsystems/:id` | PATCH /api/subsystems/:id |
| GET | `/api/subsystems/:id/agendas` | GET /api/subsystems/:id/agendas — list agendas scoped to a subsystem, with nested items |
| GET | `/api/subsystems/:id/docs` | GET /api/subsystems/:id/docs — read docs from workspace path for a subsystem |
| GET | `/api/subsystems/:id/harvest-candidates` | GET /api/subsystems/:id/harvest-candidates — list all harvest candidates linked to a specific subsystem (filter by subsystem_id). |
| GET | `/api/subsystems/:id/implementation-plans` | GET /api/subsystems/:id/implementation-plans — plans linked to a subsystem |
| GET | `/api/subsystems/:id/specifications` | GET /api/subsystems/:id/specifications — list specification revisions scoped to a subsystem |
| GET | `/api/subsystems/:id/work-requests` | GET /api/subsystems/:id/work-requests — list work requests scoped to a subsystem |
| POST | `/api/subsystems/move` | POST /api/subsystems/move — re-parent a subsystem to a different system |
| GET | `/api/systems` | SYSTEMS GET /api/systems — full nested hierarchy with pagination |
| POST | `/api/systems` | POST /api/systems |
| DELETE | `/api/systems/:id` | DELETE /api/systems/:id — cascade deletes subsystems, features, folders, requirements |
| GET | `/api/systems/:id` | GET /api/systems/:id — single system with full nested hierarchy |
| PATCH | `/api/systems/:id` | PATCH /api/systems/:id — name, description, readme, architecture |
| GET | `/api/systems/:id/agendas` | GET /api/systems/:id/agendas — list agendas scoped to a system, with nested items |
| GET | `/api/systems/:id/docs` | GET /api/systems/:id/docs — read docs from all workspaces for a system |
| GET | `/api/systems/:id/external-ids` | The system_external_ids junction has been replaced by asset_relation (system-asset OWNS service-asset). These endpoints now query asset_relation instead. Full history remains in system_external_ids_history (append-only). GET /api/systems/:id/external-ids — list owned services via asset_relation |
| POST | `/api/systems/:id/external-ids` | POST /api/systems/:id/external-ids — create asset_relation edge (deprecated junction) |
| DELETE | `/api/systems/:id/external-ids/:eid` | DELETE /api/systems/:id/external-ids/:eid — deprecated |
| POST | `/api/systems/:id/folders` | POST /api/systems/:id/folders |
| GET | `/api/systems/:id/harvest-candidates` | GET /api/systems/:id/harvest-candidates — list all harvest candidates linked to a specific system (direct filter by system_id). |
| GET | `/api/systems/:id/implementation-plans` | GET /api/systems/:id/implementation-plans — plans linked to a system via cross-refs |
| GET | `/api/systems/:id/info` | SYSTEM INFO TABS GET /api/systems/:id/info — get all info tabs for a system with pagination |
| DELETE | `/api/systems/:id/info/:tabId` | DELETE /api/systems/:id/info/:tabId — delete an info tab When tabId='harvest_context', also unlinks all candidates from this system. |
| PUT | `/api/systems/:id/info/:tabId` | PUT /api/systems/:id/info/:tabId — save an info tab |
| GET | `/api/systems/:id/inventory` | SYSTEM INVENTORY (unified cross-schema view) GET /api/systems/:id/inventory — unified inventory via asset_relation V076 migration: joins through asset_relation (system OWNS service) instead of the deprecated system_external_ids junction. |
| GET | `/api/systems/:id/specifications` | GET /api/systems/:id/specifications — list specification revisions scoped to a system |
| GET | `/api/systems/:id/work-requests` | GET /api/systems/:id/work-requests — list work requests scoped to a system |
| DELETE | `/api/systems/:systemId/folders/:folderId` | DELETE /api/systems/:systemId/folders/:folderId |
| POST | `/api/systems/demote/:id` | POST /api/systems/demote/:id — demote a system into a subsystem of another system |
| GET | `/api/work-requests` | WORK REQUESTS (scoped via requirements OR specifications → agenda items → harvest_candidates) GET /api/work-requests — list ALL work requests with pagination |
| GET | `/api/work-requests/:id` | GET /api/work-requests/:id — single work request |
| GET | `/api/workspaces` | WORKSPACES GET /api/workspaces — list all workspace paths with pagination |
| POST | `/api/workspaces` | POST /api/workspaces |
| DELETE | `/api/workspaces/:id` | DELETE /api/workspaces/:id |
| GET | `/health` |  |

## Regeneration

```bash
cd nexus && python3 tools/api-docs/extract_routes.py --out /tmp/api_inventory.json
python3 tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json   # (vision-srv also refreshes from the live FastAPI spec)
```

<!-- API-SPEC-BEGIN -->

---

# nebula-srv — REST & Envelope Spec

> **Hand-authored section — preserved across regeneration.** Base URL:
> `http://localhost:3101`. JSON in/out (CORS). Canonical knowledge graph over
> the `nebula` PostgreSQL schema. **This is the database-first API** — the
> canonical write path for harvests, agent records, projections, plans, and
> most knowledge artifacts. Filesystem audit files are projections, never the
> source of truth.
>
> **Conventions:**
> - List endpoints are paginated; most return `{ items: [...], total, limit, offset }` (or family-specific keys like `records`, `harvests`).
> - Agent records support filters: `role`, `recordType`, `level`, `tags`, `createdAfter` (ISO), `limit` (≤100), plus full-text `q`.
> - Errors: `{ error: "<message>" }` with 400/404/500.

## Agent records (database-first audit trail)

The core messaging + audit primitive. List fields (id, recordType, role,
model, title, sourcePath, tags, systemId, subsystemId, featureId, planRef,
createdAt, level, visibilityScope); detail adds the full `content`.

| Endpoint | Purpose |
|----------|---------|
| `GET /api/agent-records` | List with filters + pagination. |
| `POST /api/agent-records` | **Create** — canonical write path. Body: `{ recordType, role, title, content, tags[], level?, visibilityScope? }`. `recordType` ∈ `report, analysis, assessment, inspection, prompt, response, engineering_log, architecture_note, decision`. `level` ∈ 1–4. |
| `GET /api/agent-records/:id` | Full record **with content**. |
| `PATCH /api/agent-records/:id` | Update fields. |
| `DELETE /api/agent-records/:id` | Delete. |
| `POST /api/agent-records/search` | Multi-tag AND/OR search. |

## Hierarchy: systems → subsystems → features

Each level has CRUD + scoped children lists:

| Resource | CRUD | Scoped children |
|----------|------|-----------------|
| Systems | `GET/POST /api/systems`, `GET/PATCH/DELETE /api/systems/:id`, `POST /api/systems/demote/:id` | `…/:id/agendas`, `docs`, `harvest-candidates`, `implementation-plans`, `specifications`, `work-requests`, `inventory`, `info`, `external-ids`, `folders` |
| Subsystems | `POST /api/subsystems`, `GET/PATCH/DELETE /api/subsystems/:id`, `POST /api/subsystems/move` | `…/:id/agendas`, `docs`, `harvest-candidates`, `implementation-plans`, `specifications`, `work-requests` |
| Features | `POST /api/features`, `GET/PATCH/DELETE /api/features/:id`, `POST /api/features/move` | `…/:id/agendas`, `harvest-candidates`, `implementation-plans`, `specifications`, `work-requests` |

## Harvests

| Endpoint | Purpose |
|----------|---------|
| `GET /api/harvests` | List with pagination. |
| `POST /api/harvests` | **Create harvest** (canonical write path). |
| `GET /api/harvests/:id` | Single harvest. |
| `DELETE /api/harvests/:id` | Delete. |
| `GET /api/harvests/:id/transcript` | Conversation transcript for the harvest. |
| `GET /api/harvests/distribution` | Distribution stats. |

## Harvest candidates

| Endpoint | Purpose |
|----------|---------|
| `GET /api/harvest-candidates` | List with filters + pagination (CPF scores, status). |
| `POST /api/harvest-candidates` | Create. |
| `GET /api/harvest-candidates/:id` | Single candidate. |
| `PATCH /api/harvest-candidates/:id` | Update. |
| `GET /api/harvest-candidates/:id/dependencies` | Candidate dependencies. |
| `POST /api/harvest-candidates/:id/promote` | Promote → mark candidate as useful (stage). |
| `POST /api/harvest-candidates/:id/spawn-plan` | Spawn an implementation plan. |
| `POST /api/harvest-candidates/discover` | Batch discovery. |
| `POST /api/harvest-candidates/promote-to-plan` | Bulk promote-to-plan. |

`/api/candidates` aliases `/api/harvest-candidates` (Assembly UI compat).

## Requirements

| Endpoint | Purpose |
|----------|---------|
| `GET /api/requirements` | List with filters + pagination. |
| `POST /api/requirements` | Create. |
| `GET /api/requirements/:id` · `PATCH` · `DELETE` | Single / update / delete. |
| `PATCH /api/requirements/batch` | Batch update. |
| `GET /api/requirements/:id/children` | Child requirements. |
| `POST /api/requirements/:id/compile` | Compile → implementation plan. |
| `GET/POST/DELETE /api/requirements/:id/dependencies[/:depId]` | Dependency edges. |
| `POST /api/requirements/:id/move` | Reparent. |

## Plans, specifications, agendas

- **Plans:** `GET/POST /api/plans`, `GET /api/plans/:id`, `GET /api/plans/:planRef/candidates`.
- **Specifications:** `GET /api/specifications`, `GET /api/specifications/:id`, `POST /api/specifications/:id/link-requirements`.
- **Agendas:** `GET /api/agendas`, `GET /api/agendas/:id`, `POST /api/agendas/:id/items` (body includes `sourceId`), `DELETE /api/agendas/:id/items?sourceId=<uuid>`, `POST /api/agendas/:id/finalize`.

## Open questions

`GET/POST /api/open-questions`, `GET /api/open-questions/:id`,
`PUT /api/open-questions/:id/answer` (mark answered), `GET/POST …/answers`,
`GET/POST …/participants`, `PUT /api/open-questions/:id/resolve`,
`GET /api/open-questions/:id/timeline`.

## Projections (DB → markdown)

| Endpoint | Purpose |
|----------|---------|
| `GET /api/projections` | List projection configs. |
| `POST /api/projections` | Create config (recordType/role/template/scope). |
| `DELETE /api/projections/:id` | Delete config. |
| `POST /api/projections/:id/render` | Render one projection (DB → audit markdown file). |

## Audit (filesystem projection of agent records)

| Endpoint | Purpose |
|----------|---------|
| `GET /api/audit` | List audit files with pagination. |
| `GET /api/audit/:id` | Single audit file with content. |
| `GET /api/audit/graph` | Agent records as nodes, cross-references as edges. |
| `POST /api/audit/sync` | Scan filesystem, upsert all audit files. |
| `POST /api/audit/:id/regenerate` | Re-read one file from disk into DB. |

## Knowledge graph

`GET /api/knowledge/entities`, `GET /api/knowledge/entities/:section/:entityId`,
`GET /api/knowledge/entities/:section/:entityId/relations`,
`GET /api/knowledge/edges`, `GET /api/knowledge/cross-references`,
`GET /api/knowledge/summary`, `GET /api/knowledge/view`.
Also `GET/POST/DELETE /api/cross-references[/:id]`.

## Role leases (dispenser)

| Endpoint | Purpose |
|----------|---------|
| `GET /api/role-leases?role=` | List leases (optionally by role). |
| `GET /api/role-leases/stale` | Leases past their window still ACTIVE. |
| `POST /api/role-leases/issue` | Issue a lease (role, channel, model, window, budget). |
| `POST /api/role-leases/:id/renew` | Renew window + budget. |
| `POST /api/role-leases/:id/revoke` | Revoke. |
| `POST /api/role-leases/consume` | **Canonical accounting**: `consumed_units += 1` for a role's active lease (body `{ role }`). |

## Execution bridge

| Endpoint | Purpose |
|----------|---------|
| `GET/POST /api/execution/requests` | List / create execution requests. |
| `GET /api/execution/requests/:id` | Single request. |
| `PATCH /api/execution/requests/:id/transition` | State transition. |
| `POST /api/execution/leases/acquire` | Acquire a lease. |
| `POST /api/execution/leases/:id/renew` · `…/release` | Renew / release. |
| `GET/POST /api/execution/attempts` | List / create attempts. |
| `GET/POST /api/execution/receipts` | List / create receipts. |
| `GET /api/execution/state` | Execution state snapshot. |

## Conduit projection

`GET /api/conduit/plans`, `GET /api/conduit/deleted-plans`,
`GET /api/conduit/plans/:id/history`, `GET /api/conduit/plans/:id/receipts`,
`GET /api/conduit/plans/as-of` — conduit plan receipts/history mirrored for
UI (normalized field set differs from conduit-srv's native shapes).

## Other families

| Family | Endpoints |
|--------|-----------|
| Roles | `GET/POST /api/roles`, `GET/PATCH/DELETE /api/roles/:id` |
| Sessions | `GET/POST /api/sessions`, `PATCH/DELETE /api/sessions/:id` |
| Observations / assessments | `GET /api/observations[/:id]`, `GET /api/assessments[/:id]` |
| Architect specs / artifact provenance / op-registry | CRUD per family; op-registry adds `…/:id/lineage`, `…/:id/supersede`, `…/:id/deprecate`, `POST /api/op-registry/fork` |
| Evidence links | `GET/POST/DELETE /api/evidence-links`, `DELETE /api/evidence-links` (bulk by query) |
| CPF | `GET /api/cpf`, `GET /api/cpf/count`, `POST /api/cpf/promote` |
| Segments / workspaces | CRUD |
| Snapshots | `POST /api/snapshots`, `GET /api/snapshots/:id/blocks|projection|references` |
| Conversations | `GET /api/conversations`, `GET /api/conversations/:id/blocks|snapshots`, `GET /api/conversations/by-snapshot/:snapshotId[/blocks]` |
| Inbox pointer | `GET /api/inbox-pointer/:role`, `PUT /api/inbox-pointer/:role` (body `{ timestamp: "<ISO>" }`) |
| Preferences | `GET /api/preferences`, `PUT/DELETE /api/preferences/:key` |
| External ids | `GET /api/external-ids`, `PATCH /api/external-ids/:id` |
| Projection overrides | `POST /api/projection-overrides`, `DELETE …/:id` |
| Search | `GET /api/search`, `POST /api/search/semantic` |
| Cascade subscriber status | `GET /api/cascade/subscriber-status` (interactive-turn subscriber liveness) |

## Health

- `GET /api/health` · `GET /health` — process + DB health.

## Notes

- **Database-first:** never write `.md` files directly to audit dirs as the
  initial persistence path — create records/harvests/plans through this API;
  the filesystem is a derived projection (`POST /api/projections/:id/render`).
- Timestamps are epoch-millis in list responses; inbox-pointer `createdAfter`
  filters expect ISO 8601 strings.
