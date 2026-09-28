# Nexus Architecture

> **Date:** 2026-08-03
> **Status:** Recreated root architecture document (the prior `ARCHITECTURE.md`
> was removed in `135c0fc`, 2026-06-26). This is the parent doc that
> `jvm/ARCHITECTURE.md` and `typescript/ARCHITECTURE.md` inherit from.
> **Scope:** the current system, end-to-end — services, storage, pipeline,
> agent orchestration, and the type-level semantic legend.

---

## 1. Overview

Nexus is a **TypeScript-first, agent-orchestrated WorkRequest compiler and
knowledge platform**. Raw chat transcripts and ideas flow through a
deterministic pipeline into executable Work Requests; a roundtable of AI
agent *roles* (architect, engineer, planner, reviewer, analyst, inspector,
critic) coordinate through the database, not chat.

**Governing principles:**

- **Database-first.** PostgreSQL (`nexus` DB) is the only canonical store for
  agent artifacts. Filesystem projections (markdown, audit dirs) are derived
  views, regenerated on demand — never written first.
- **Three conceptual layers:**
  | Layer | Where | What it holds |
  |-------|-------|---------------|
  | Type-level legend (classes) | `semantics` schema | concepts, legal pipeline shape, identity strategies |
  | Class/instance artifacts | `nebula`, `conduit`, `cascade` | harvests, requirements, plans, work requests, instance lineage |
  | Per-baseline judgment | `semantics.snapshot*` | lifecycle state, drift, audit reason, safe-to-retire |
- **Append-only, expire-not-delete** where the schema says so; **SCD4
  bitemporal** versioning on the high-write `nebula` tables (see §4).
- **Agents are deterministic components** with role governance — see
  `AGENTS.md` and §6.

---

## 2. Service Topology

Systemd-managed user services; start/stop/status/health via
`bin/start-nexus-services.sh` (backends) and `bin/start-nexus-uis.sh` (UIs).
Ports below are the authoritative `SERVICE_PORTS` map from that script.

### 2.1 Infrastructure & storage

| Service | Port | Role |
|---------|------|------|
| `pgvector_db` (docker) | 5432 | PostgreSQL — canonical state for all pipeline artifacts (DBs: `nexus`, `personal`, …) |
| `redis.service` | 6379 | Redis — role-memory procedure cards (`mem:proc:*`, `mem:idx:*`), ephemeral state |
| `mongodb.service` | 27017 | MongoDB — asset/DAM support |

### 2.2 Core backends (knowledge, pipeline, lineage)

| Service | Port | Role |
|---------|------|------|
| `nebula-srv` | 3101 | Nebula RMS REST API — agent records, harvests, candidates, intent records, requirements, plans, projections, inbox pointers |
| `nebula-mcp` | 3102 | Nebula MCP (Streamable HTTP + legacy SSE; both transports) over nebula-srv |
| `conduit-mcp` | 3100 | **WorkRequest orchestration** — plan lifecycle, tickets, receipts, circuit breaker, execution dispatch |
| `conduit-srv` | 3104 | Conduit REST API (extracted from conduit-mcp) |
| `conduit-kernel` | 3103 | WRP kernel FastAPI — sessions, circuit breaker, receipts, admin/delta/replay |
| `cascade-srv` | 3106 | Cascade Event API — **instance-level lineage graph** (`cascade.lineage_edges`) |
| `kernel-srv` | 8100 | Semantic Kernel REST — `sys_transition`, `sys_issue_receipt`, `v_*` views; SSE over `pg_notify` |
| `role-memory-srv` | 3500 | PG → Redis sync for tackle procedure cards (`POST /refresh` → `syncAll()`) |
| `tackle-srv` | 3410 | Tackle AI-config & memory REST API |
| `tackle-mcp` | 3400 | Role-memory MCP — `memory_get_procedures`, `memory_get_procedure` |
| `execution-srv` | 3110 | Execution observability REST API |
| `knowledge-srv` | 3109 | Knowledge REST — `graph_entities`, `graph_edges`, cross-references, migrations |
| `peb-srv` | 3111 | PEB observability REST API |
| `peb-kernel` | 8080 | Engineering brain / policy kernel (PEB) |
| `wrp-bridge-daemon` | — | conduit → kernel sync |
| `cascade-kernel-subscriber` | — | `pg_notify` → NATS (kernel transitions) |
| `cascade-obs-subscriber` | — | `pg_notify` → NATS (PEB governance + Vision lifecycle) |
| `cpf-api` | 3108 | CPF (compilation-readiness) funnel data API |
| `atlas` | 8090 | Graph-views persistence |

### 2.3 API / MCP servers & utilities

| Service | Port | Role |
|---------|------|------|
| `assembly-srv` | 3107 | Assembly REST — forums (`issues-and-open-questions`, `to-do`, `change-log`), threads, users |
| `assembly-mcp` | 3113 | Assembly MCP |
| `operator-svc` | 3018 | Operator host personality |
| `substance` | 3115 | Segment Sets API (FastAPI) — the *SegmentSet* concept's owning subsystem |
| `wind-srv` | 3300 | Wind IDE workflow API — projections (`Wind projection` consumer) |
| `voyager-srv` | 3114 | Voyager REST — filesystem-acquisition queries |
| `voyager` | — | Filesystem acquisition layer (NATS-backed) |
| `mildred-dam-api` | 3140 | Mildred Digital Asset Management API |
| `terrain` | 8084 | Topology registry (78 registered systems) |
| `terrain-mcp` | stdio | Terrain topology MCP (on-demand) |
| `vision-srv-py` | 8003 | Vision processing (Python) |
| `losm-host` | 8006 | LOSM Host (FastAPI) |
| `image-server` | — | Image hosting |
| `address-tts` | 8600 | Speech synthesis (completion announcements, R6) |
| `address-tts-mcp` | 3105 | TTS MCP |
| ~~`pty-srv`~~ | ~~3120~~ | ~~WebSocket PTY bridge (xterm.js)~~ — **RETIRED** (M3): superseded by `worker.pty-transport` WS on :3130 via nexus-broker (:4080); see M1-SERVICE-MANIFEST |
| `file-system-server` | 4042 | File-system operations |
| `secure-file-system-server` | 4040 | Secure file-system operations |
| `mcp-bridge` | 3131–3134 | Generic stdio→SSE bridge (knowledge/vision/peb/terrain MCPs) |
| `tools-aggregator` | 3210 | Unified MCP tool-discovery aggregator |
| `service-broker-mcp` | 3112 | Service-broker MCP over SSE (auth/token tools) |
| `ui-event-bus` | 3200 | Cross-app UI event bus (SSE) |
| `moleculer-search` | 4050 | Moleculer Search API (Google, registry) |
| `ui-tools` | 3125 | UI Tools CRUD API (statusbar links) |
| `ui-tools-mcp` | 3136 | UI Tools MCP (agent-facing link management) |
| `semantics-srv` | 3160 | **Semantics REST** — CRUD over the `semantics.*` legend (11 tables, 34 stored procs) |
| `semantics-mcp` | 3161 | **Semantics MCP** — agent-accessible CRUD over the legend (57 tools → semantics-srv) |

### 2.4 UI dev servers (Angular/Vite)

`nebula-ui` 4210 · `duality-ui` 3002 · `view-architect` 3003 ·
`plurality-ui` 3004 · `nexus-console` 4200 · `conduit-ui` 4201 ·
`tackle-ui` 4202 · `cascade-ui` 4203 ·
`execution-ui` 4205 · `peb-ui` 4206 · `semantic-kernel-ui` 4207

### 2.5 Legacy broker mesh (historical)

`service-registry` 8085 · `broker-gateway` 8081 · `quarkus-broker-gateway`
8091 · `helidon-user-access-service` 9093. Superseded by the current
TypeScript-first architecture; `README.md` still documents the legacy
three-layer mesh and is retained as historical reference.

---

## 3. Database Schemas (`nexus` DB)

PostgreSQL is canonical. The primary (`pgvector_db`, localhost:5432) hosts the
live `nexus` database; **barium** (192.168.1.212 — Raspberry Pi,
`pgvector/pgvector:pg17` Docker) is the canonical off-machine backup target and
is kept at parity (nightly verified backups via `bin/pg-backup-to-vanadium.sh`;
schema/seed migrations applied to both — see §7). The former backup server
**Strontium** (172.16.30.2) has been down for repair since 2026-08-22 and is
decommissioned from the backup role; it may return, but barium remains the
replication target for the forceable future (2026-08-25 decision).

| Schema | Tables | Views | Purpose |
|--------|-------:|------:|---------|
| `nebula` | 47 | 51 | Knowledge graph & record store — **SCD4 bitemporal** (`_history` tables + live VIEWs, `valid_*` + `recorded_*`); agent records, harvests, candidates, intent records, requirements, plans, cross-references, projections |
| `conduit` | 16 | 0 | WorkRequest pipeline — plans, tickets, receipts, execution state |
| `cascade` | 5 | 0 | Instance-level operational lineage (`lineage_edges`, …) — the *map* (`lineage_edges` **deprecated** — 0-row parallel store, see T22 Step 5.4 ruling) |
| `semantics` | 12 | 0 | **Type-level legend** — concepts, representations, relationship vocabulary, identity, snapshots, drift (see `docs/semantics-schema.md`) |
| `assembly` | 16 | 7 | Forums, threads, comments, users |
| `tackle` | 17 | 0 | Role memory & procedure cards (synced → Redis) |
| `kernel` | 5 | 8 | Semantic Kernel state transitions & receipts |
| `execution` | 4 | 0 | Execution observability |
| `knowledge` | 5 | 2 | Semantic knowledge projections (`semantic_documents`) |
| `peb` | 8 | 0 | PEB governance/policy |
| `vision` | 12 | 9 | Vision lifecycle |
| `wind` | 13 | 2 | Wind projections |
| `voyager` | 10 | 0 | Filesystem acquisition |
| `shrapnel` | 15 | 0 | Fragmentation/derived artifacts |
| `terrain` | 8 | 0 | Topology registry |
| `registry` | 26 | 2 | Legacy service registry |
| `operator` · `steward` · `gateway` · `throttler` | 2/2/1/1 | 0 | Operator, stewardship, gateway, throttling state |

---

## 4. The Work Pipeline (end-to-end)

Raw intent becomes executable work; the whole chain is documented in
`harvest-candidate-to-requirement-pipeline.md`.

```
HTML transcript → DocLang (structure) → DAL → Rover (classification)
      → nebula.harvests / harvest_candidates          [✅ working]
      → intent_records (promotion gate, CPF ≥ 0.7)     [⚠️ 701 stuck]
      → requirements (decomposition)                   [❌ manual gap]
      → implementation_plans (req_compiler.py)         [✅ working]
      → WorkRequest → conduit-mcp → execution          [✅ working]
```

- **Execution** is driven by conduit-mcp (`runtime_submit_work_request`):
  tickets (builder/reviewer), receipt chain (`PLAN_CREATE → BLOCK →
  REVIEW_PASS/REJECT`), circuit breaker with max retries, and harnesses
  (opencode interactive, ollama-sdk daemon, codex-cli oneshot) → results
  land as `WorkResultEvent` (append-only).
- **Class-level shape** of this pipeline is the `semantics` legend
  (SegmentSet → Candidate → IntentRecord → Requirement → Specification →
  ImplementationPlan → WorkRequest, green/red path tags); **instance-level**
  edges live in `cascade.lineage_edges` (proposed: nullable FK citing the
  class-level rule).

---

## 5. Semantics — the Type-level Legend

`semantics.*` describes **what the system is made of** — the classes and the
legal relationships — independent of any one baseline. It is the legend;
`cascade.lineage_edges` is the map; snapshots are the per-baseline judgment
layer (the only thing that repeats).

- 12 tables, 16 FKs, 37 stored procedures (`add_*` / `soft_delete_*` /
  `update_*` / `resolve_drift_finding`), expire-not-delete via `expired_at`,
  active-only partial unique indexes.
- **Relationship vocabulary (V060 + V061):** 29 legal edge types in
  `semantics.relationship_type` (6 concept pipeline + 4 representation-fidelity
  + 14 cross-domain: `defines`, `implements`, `projects`, `derives_from`,
  `validates`, `constrains`, `governs`, `supersedes`, `observes`, `mediates`,
  `interprets`, `depends_on_decision`, `evidences`, `questions` + 5 operational
  between representations: `calls`, `consumes`, `writes`, `reads`, `uses`),
  **FK-enforced** on `concept_relationship` and `representation_relationship` —
  only defined types are capturable as edges, and operational facts can be
  stated between any two representations.
- **Seeded legend (V059):** 16 owning subsystems (the fleet), 11 concepts,
  12 legal pipeline edges, 6 identity strategies (incl. Asset as the
  `canonical_asset_id` root).
- **Access layer (V1):** `semantics-srv` (REST :3160) + `semantics-mcp`
  (:3161, 57 tools) expose CRUD over all 11 tables via the stored procs;
  registered in terrain, service-registry (ids 60/61), systemd, and
  `bin/start-nexus-services.sh`.
- Full reference: `docs/semantics-schema.md`. Design: `semantics-db.md`.
- **Status:** lookup layer seeded; representation layer (physical forms,
  consumers, identity mappings) is the next population step — the legend is
  the bridge from "architecture doc" to "describing the actual system."

---

## 6. Agent Orchestration Model

Agents operate as a **roundtable of epistemic roles** coordinated through
durable state, per `AGENTS.md` (the governing doctrine):

| Mechanism | Where | What it provides |
|-----------|-------|------------------|
| Role governance | `nebula.agent_records` | Tag-routed inbox/outbox (`to:<role>`, `type:…`, `status:…`); every role owns binding outputs in its domain (I1–I4) |
| Timeclock | :3600 | Session identity keyed on `(role, model, session_id)`; clock in/out at session boundaries (R13) |
| Assembly forums | :3107 | `issues-and-open-questions`, `to-do`, `change-log` — cross-role visibility, role+model attribution (R12/R14/R16) |
| Procedure cards | tackle-mcp :3400 | Role-specific procedure indexes (`memory_get_procedures(<role>)`) |
| Operational rules | `AGENTS.md` | R1–R17: record before/after work, engineer→architect updates, TTS completion, service verification at boot, end-of-turn inbox check, weekly review |
| Inbox pointer | nebula REST :3101 | Per-role last-seen timestamp for new-message surfacing (R17) |

---

## 7. Schema & Seed Migration Conventions

- Migrations live in `nexus/sql/` as `V0NN__description.sql`; **no runner** —
  engineers apply them manually, in order, to **both** the primary and
  barium (the canonical backup target — Strontium was decommissioned from
  this role 2026-08-25), and confirm parity
  (R9: always ask before replicating).
- Applied chain (semantics): `semantics.sql` → V055 → V056 → V057 → V058 →
  V059. V055/V056 were superseded/reverted by V057 and kept as honest history.
- Idempotency is expected (V057 guards on empty; V059 uses
  `ON CONFLICT DO NOTHING`).

---

## 8. Platform Architecture

- `jvm/ARCHITECTURE.md` — JVM platform defaults (Java 21, Spring Boot 3.5.0, port range 8080–8099)
## 8. Platform Architecture

- `jvm/ARCHITECTURE.md` — JVM platform defaults (Java 21, Spring Boot 3.5.0, port range 8080–8099)
- `typescript/ARCHITECTURE.md` — TypeScript platform defaults (Node 20, TS 5.x, ranges 8080–8099 / 3333–3349)

Both inherit from this document.

### 8.1 Implementation Tiers

Nexus is proven **at the contract level**. The contract is the unit of correctness; an
implementation is evidence. One implementation is an assertion; N implementations agreeing on
one contract is a proof — and the agreement is the deliverable, not the code.

Tiers overlap deliberately. Moleculer and the JVM tier each reproduce services that already
exist in TypeScript, and modules continue to be extracted from Python into the "legacy
TypeScript" tier. Some services exist at three or four tiers at once. **That is the intended
state, not drift.**

| Tier | Location | Role |
|---|---|---|
| TypeScript | `typescript/<service>/` | Current-preference implementation |
| Python | `python/<service>/` | Live, retained, de-kicked from titanium where a newer tier runs |
| Moleculer | `moleculer/<service>/` | Reproduces services that also exist elsewhere |
| JVM | `jvm/` | Reproduces services that also exist elsewhere |

Overlap is **growing, not complete**: as of this writing the multi-tier names are the
`python`+`moleculer` pairs (`aegis`, `cascade`, `conduit`, `tackle`, `voyager`) plus
`solscript` in `typescript`+`moleculer`. Extraction from Python into the "legacy TypeScript"
tier is in progress, not finished. Treat this table as the shape, and the directory listing as
the current truth.

We cannot run every implementation on one machine. Placement is a scheduling decision, not a
lifecycle one.

### 8.2 Two independent axes

| Axis | Question | States | Default |
|---|---|---|---|
| **Liveness** | Is it maintained and serving? | `live` / `legacy` | **`live`** |
| **Host affinity** | Should it run on *this* host? | `preferred` / `not-preferred` / `exempt` | `not-preferred` unless asserted |

**Liveness is opt-out.** Code is `live` unless *explicitly* moved into `legacy/`. That
directory is the only path to not-live.

**Host affinity is a placement, not a lifecycle.** A `not-preferred` implementation is not
deprecated, not retired, and not headed for deletion. It is a live implementation this machine
should not be running — possibly because another host is running it right now. Host affinity
implies nothing about maintenance, correctness, or convergence.

**`exempt` means required everywhere.** Some things cannot be deployed in multiple
implementations and are tier-exempt: one implementation, required on every host. The role
vocabulary (`config/roles/roles.json`, read by `bin/verify-roles.py`) is the clear case — it is
a single canonical input, not a service, and every tier resolves against it. `python/timeclock`
(:3600) is the same shape: no sibling tier. Converging these is not the goal, and a port that
splits one is a bug.

Nothing here ends in deletion. Porting to a new tier **adds** an implementation; it never
removes the previous one. Deleting an implementation requires an explicit recorded decision —
it is never the implicit default of a completed port. A future Rust or Go Nexus enters at the
same standing as every other tier.

### 8.3 Ports find holes — they do not migrate

**We are not "moving from X to Y". We are "finding holes in X by implementing in Y."**

Every port surfaces errors and untrue assumptions in the source of the port. That is not a
defect in the porting process — it is the reason the process exists, and the highest-value
output of the work. Writing the new implementation and correcting the old one are **the same
deliverable**.

A port's output is a defect report on X, not a service that replaces X.

**A port that surfaces zero discrepancies has failed.** It means the second implementation was
too similar to be independent, not that the first was correct. Treat a clean port as a prompt
to ask whether the instrument had teeth — the same way a test suite that cannot fail is a
prompt to ask what it is not checking. Port receipts must carry a **falsification count**; see
`docs/PORT-RECEIPT.md`.

### 8.4 Contract provenance

Where a contract and a runtime disagree, **the contract is the contract of record**. The
runtime is evidence about the contract, not the reverse.

The language segment in `typespec/v1/<service>/<language>/` records which implementation the
contract was reverse-engineered from. It is provenance, not canonicality — `python/` does not
mean Python is canonical, and the segment must not be collapsed.

When a legacy implementation proves wrong, the error belongs to the **contract**, and the
correction propagates outward: to the contract, to sibling implementations, and back into the
legacy implementation. A port therefore produces contract changes, governed exactly as any
other contract change is.


---

## 9. Key References

| Doc | Content |
|-----|---------|
| `AGENTS.md` | Agent doctrine, operational rules R1–R17, role governance, knowledge stratification |
| `docs/semantics-schema.md` | Semantics schema quick reference (tables, procs, seed, chain) |
| `semantics-db.md` | Design rationale for the semantics model |
| `harvest-candidate-to-requirement-pipeline.md` | The full harvest → WorkRequest pipeline walkthrough |
| `docs/BITEMPORAL-API-CHANGES.md` | nebula SCD4 bitemporal refactor, API breaking changes |
| `docs/architect.md` | Architect status briefing (system health, pipeline state, open decisions) |
| `docs/PORT-RECEIPT.md` | Required template for tier ports — independence argument, falsification count, source disposition |
| `README.md` | Legacy three-layer broker mesh (historical) |
