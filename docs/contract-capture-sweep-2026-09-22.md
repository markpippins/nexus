# Contract-Capture Sweep: Nouns, Verbs, and Where Contracts Could Bind

> **Author:** analyst (freebuff/agent) · **Date:** 2026-09-22 · **Branch:** `contract-capture-sweep` (worktree `nexus-worktrees/contract-capture-sweep`)
> **Request:** go through the codebase, collect nouns and verbs across the parallel TS/JVM tracks with the Python root, and find where contractual coverage could be added — TypeSpec, TLA+, CUE, or JSON-LD — plus a secondary list of similar-but-not-identical types that would be enforced by normalizing one semantic or another.
> **Status:** DRAFT FOR ROUNDTABLE REVIEW. Nothing here is applied; ownership routing at the end.

---

## 0. Executive summary

- The TS track is **much better covered than first apparent**: `typespec/v1/` holds **52 contract directories, 44 with real content** (~19,600 lines of contract). The gap is concentrated in the **MCP family and a handful of REST servers**, not in the fleet at large.
- The **JVM track's REST surface is essentially uncovered** by contracts except where TypeSpec spring generators exist (peb-kernel, governance-envelope, service-registry, service-broker). ~60 endpoints across 9 modules are hand-written.
- ~~The Python track has no machine-checkable contract layer at all~~ **CORRECTED after roundtable review:** Python has a dedicated contract program — `typespec/v1/<service>/python/` exists for 16 python services (REST + MCP), reverse-engineered and reconciled by `scripts/reconcile-python.py` (`python-conventions.md`, `python-services.md`). See §3.1-correction. What python *lacks* is wired emitters beyond two surfaces and any contract for its config/interop layer (compose files, projection configs) — those parts of the original claim stand.
- TLA+ already has its anchor: the **Aegis state-machine registry** (self-described "TLA+ formal-methods registry"). The opportunity is formalizing the **invariants currently enforced by triggers, partial uniques, and culture** — the exact class of invariant whose violation produced the calendar fold-wedge incident.
- CUE's niche is the **configuration matrix**: compose-variant families, CI pipelines, projection configs — YAML that is currently validated by eyeball.
- JSON-LD's niche is the **meaning layer**: the V182 relation vocabulary, the epistemic level/visibility axes, and provenance chains already exist as governed vocabularies that a `@context` would make machine-resolvable.
- **The single most valuable normalization candidate:** four different nouns are all called "session" (epistemic Session, HarnessSession, vision session, timeclock session) and two watermarks govern the same inbox-visibility semantics (inbox pointer vs coordination checkpoint). Both are live ambiguity, not hypothetical.

Method note: counts below come from live greps over the worktree at PR #453 (`44 real contract dirs`, endpoint census from `@GetMapping/@PostMapping/@Path` sweeps). Every claim is re-derivable.

---

## 1. The three tracks, as they exist today

| Track | Size | Contract layer today | Doctrine status |
|---|---|---|---|
| **TypeScript** (`typescript/`, 53 services) | ~53 dirs | `typespec/v1/` — 52 dirs, 44 real; contract-first baselines with per-runtime generators | **Live lane** per the Moleculer lane-split ruling: moleculer owns live TS surfaces |
| **JVM** (`jvm/spring/nexus-core/*` 9 modules + `jvm/helidon/user-access-service`) | ~10 modules | TypeSpec **spring/helidon/quarkus generators** exist for peb-kernel, governance-envelope, service-registry, service-broker; everything else hand-written | **Conformance-only** — never a second live authority |
| **Python** (`python/`, ~30 top-level packages) | ~30 pkgs | `typespec/v1/<service>/python/` contracts for 16 services (conduit-kernel, peb-kernel, timeclock, vision-srv, losm-host, fs-crawler(+adapter), substance, address-tts, operator-svc, tackle-mcp, rover, solscript, nats-envelope, nexus-tools, jev-inquiry); reconciler-enforced (`scripts/reconcile-python.py`); emitters wired for conduit-kernel + peb-kernel with committed client trees (`python/*/generated`) | **Reference/discovery-mode**; Cascade/Conduit/Vision are Tier-1 reference, ported last (pre-set disposition — no per-bootstrap triage) |

The contract convention that works (and should be the default for every new capture):

- `typespec/v1/<surface>/` with `main.tsp` + `models.tsp` + `operations.tsp`
- per-runtime subdirs (`spring/`, `helidon/`, `python/`, `typescript/`) with `tspconfig.yaml` when generators are wired — `service-broker` (41 files across 4 runtimes) is the exemplar
- **model-only contracts for protocol surfaces** (write-queue precedent: no routes, shared CanonicalEnvelope, the semantic distinction lives in the stream/lifecycle)
- contract header states the cross-runtime pairing and the auth posture "AS SHIPPED" (aegis precedent, audit 8bfe6519)

---

## 2. Noun and verb census

### 2.1 Noun families (cross-track)

| Family | Nouns | Where it lives today |
|---|---|---|
| **Epistemic record** | AgentRecord, recordType (10 values), level L1–L4, visibilityScope, tag, thread, inbox pointer | nebula-srv contract (28 models), nebula-mcp (4,064 LoC), V192 view |
| **Coordination** | Session, HarnessSession, checkpoint, lease, heartbeat, role, blackboard | nebula model `Session`; nexus-broker `Harness*` (contracted); `coordination_checkpoints`; timeclock |
| **Governance** | GovernanceEnvelope, Decision, Ruling, constraint key (4-key vocabulary), PromotionBatch, candidate_set_key, apply-lock, operator go | governance-envelope contract (416 LoC); V184 package; this cycle's envelope ruling (4a0aeb66) |
| **Work** | WorkRequest, ticket, receipt, builder ticket, plan (ImplementationPlan, PlanStatus), wave, slice | conduit-mcp/srv; nebula plan models; ST.01/PC5 packages |
| **Execution** | Keychain* (19 models, contracted), ExecutionLog, Capability, Adapter, worker, pty | nexus-broker contract (611 LoC); JVM broker controllers |
| **Domain objects** | Project, Calendar, CalendarEvent, Asset/CanonicalAsset, Stereotype, Object/Field/Binding (shrapnel, contracted) | calendar contract (257 LoC); shrapnel contract (271 LoC); V184 storage |
| **Semantics** | Concept, Relation (V182: 23 types = 18 concept + 5 representation), Expression predicate, Observation, Proposition, bundle, fingerprint | V182 SQL (staged); expression taxonomy (fail-closed); jev-inquiry contract (669 LoC) |
| **State machines** | Registry, State, Transition, Invariant, TemporalProperty, CompilationResult, AttributeMapping, ConceptMapping | aegis-srv contract (809 LoC) — the TLA+ anchor; JVM aegis module |
| **Messaging** | CanonicalEnvelope, WriteQueueEntry, stream (nexus.events vs nexus.write-queue), event:<role> tag | nats-envelope contract (69 LoC); write-queue contract (211 LoC) |
| **Knowledge/IR** | LOSM ir/store/shell/host, Subsystem, System, Workspace, Feature, Requirement | losm-host contract (312 LoC); nebula models |
| **Forum/social** | Forum, Thread, Comment, statusRating (0–7), user | assembly-srv contract (145 LoC, 10 models) |
| **Procedures** | ProcedureCard, index entry, slug | role-memory (Redis-backed) — **no contract anywhere** |

### 2.2 Verb families (cross-track)

| Family | Verbs | Contracted? |
|---|---|---|
| CRUD | get/create/update/delete (JVM census: 24× `GET/PUT/DELETE /{id}` families) | partially (aegis, shrapnel, broker) |
| **Validate/verify** | validate, verify, attest, can_verify_work_requests, model-check | aegis contract; peb-kernel conformance JSON exists |
| **Claim/lock** | claim_open_batch, apply-lock, lease issue/renew | ST.01 design only — **not yet a contract** |
| **Transition** | transition-entity, wind transitions, enforce-flip, retire/expire, promote | aegis contract models transitions; the *lifecycle rules* are prose |
| **Fold/reconcile** | consolidate/observe (calendar), reconcile (write-queue), fold-back | write-queue contract covers the protocol; the **idempotency invariant is prose** |
| **Emit/route** | emit event, fan out, tag-route (to:/event:), digest, advance checkpoint | none — the routing-policy gap is documented, uncontracted |
| **Clock** | clock-in/out, heartbeat, ttl | timeclock contract exists (109 LoC); heartbeat-client uncontracted |
| **Publish** | create thread/comment/forum, move thread, rate | assembly-srv contract (good) |
| **Mint/generate** | mint concept, generate typespec/python/spring, fingerprint | generators wired in tspconfigs; fingerprinting is contract content in governance-envelope |
| **Search/inspect** | semantic search, harvest, inspect, graph traversal | nebula contract (SemanticSearchHit, AuditGraph, GraphEdge) |

---

## 3. Primary list — capture opportunities

Each item: surface, what to capture, tool, where it applies, and why now. Ordered roughly by value; P0/P1/P2 in §5.

### 3.1 TypeSpec

> **§3.1-correction (2026-09-22, post user review):** the original draft said the Python track had "no machine-checkable contract layer." That was wrong — my census keyed on `typescript/` consumers and missed the python reverse-engineering program inside `typespec/` itself. The actual state: **16 `typespec/v1/<service>/python/` contracts** (10 REST services, 2 MCP servers, plus shared-model and probe surfaces), enumerated authoritatively in `python-services.md` and enforced by `scripts/reconcile-python.py`, which mechanically diffs each contract's `operations.tsp` against the live source routes/tool catalog and exits non-zero on coverage gaps. The three-folder pattern (`<service>/python/{main,models,operations}.tsp`) mirrors the top-level convention; peb-kernel demonstrates the cross-runtime import pattern (`python/main.tsp` imports `../spring/main.tsp` — "the canonical shared request/response surface for both PEB implementations") so the runtimes share one contract rather than owning copies. **Two emitter pipelines are actually wired** (conduit-kernel and peb-kernel `tspconfig.yaml` → `@typespec/http-client-python` → committed trees at `python/conduit/generated` and `python/peb-kernel/generated`, 23 files each), making TypeSpec the provenance of real python client code today. The remaining capture gap for python is therefore not the HTTP/tool surface (contracted) but: (a) generator wiring for the other 14 contracted services. *Precision — corrected twice; the first correction was itself wrong. A repo-wide import scan shows the overlay-import pattern is **not** unique to peb-kernel: there are seven cross-references (peb-kernel `python→../spring`; service-broker `helidon→../spring` and `quarkus→../spring`; service-broker/service-registry/terrain/atlas spring `→../../core`; and **solscript `typescript→../python` models** — the Moleculer facade imports the library contract outright: "domain models are REUSED by cross-import from the P1 contract — single source of truth, no duplication"). The claim that solscript re-stated a root contract was an arithmetic artifact (the "765-line root" was the sum of all six solscript files; there is no root file): its two subtrees are deliberately layered and disjoint — `org.nexus.solscript` model-only interpreter contract vs `org.nexus.solscriptfacade` HTTP envelope layer, **zero shared declarations**, both `npx tsp compile` clean (as are peb-kernel's both runtimes; worktrees need `npm install` first — node_modules is gitignored). jev-inquiry and conduit-kernel have no sibling runtime dirs at all, so no duplication exists there either. **Net: no re-statement conversion is needed anywhere in the tree today — the layering this section recommended is already the implemented pattern.** The remaining doctrine ask is only to name the layering rule in `typescript-conventions.md`/`python-conventions.md` so future multi-runtime surfaces start layered rather than by accident.*

> **New finding (2026-09-22, while baselining):** `scripts/reconcile-python.py` reporting has lost its enforcement teeth — it **exits 0 despite GAPS** (conduit-kernel 21/39 with a route rename drift `/api/failure-recovery` → `/api/breaker/failure-recovery`; substance 9/11; peb-kernel 3/5 missing the capabilities endpoints), contradicting `python-conventions.md`'s "exits 0 only when coverage is complete," and its summary table contradicts its own detail section (vision-srv and rover render `OK 0/0` while their contracts are flagged EXTRA in detail — both `src_root` scans found no source). Owner: engineer (script) + dba (route drift disposition).* (b) an answer to who consumes `python/conduit/generated` (my import grep found no consumers outside `generated/` itself — either the reconciler tier consumes it, adoption is pending, or the tree is ahead of its consumer; needs an owner ruling), and (c) the config/interop layer (§3.3 unchanged). O1/O2 below remain P0 — they cover the *TS* MCP family, which no program covers.

**O1 — `nebula-mcp` MCP tool surface (P0).**
The highest-traffic governance surface in the system (R1–R3, R10, R11 all flow through it) has **no contract**: 4,064 LoC of hand-written tools (`nebula_create_agent_record`, `nebula_get_inbox`, `nebula_create_plan`, projections, harvests) vs 0 contract lines. MCP is JSON-RPC, so follow the write-queue precedent: **model-only contract** — the input/output models of every tool, the recordType/level/visibility enums, tag grammar. Applies as: generated TS types for nebula-mcp + nebula-mcp-client (python) + validation fixtures; conformance for the REST twin (`POST /api/agent-records`).

**O2 — `assembly-mcp` / `assembly-srv` tool surface (P0).**
assembly-srv is contracted (145 LoC, 10 models, statusRating present — good), but **assembly-mcp is not**, and the MCP server is what every role's R12/R14/R16 rituals actually call (1,313 LoC, ~20 tools: `assembly_create_thread`, `assembly_create_comment`, …). Capture: tool-input/output models mirroring the REST contract; the **statusRating 0–7 enum and per-role convention becomes contract content** instead of forum folklore. Applies: kills the REST-vs-MCP drift class (the "3102/tools/call does not exist" trap is exactly a contract absence).

**O3 — `tackle-mcp` full tool surface (P1).**
Contract exists but is **101 lines against a 4,323-line, 42-tool implementation**. The procedure-registry nouns (ProcedureCard, index entry, slug, role) and verbs (`memory_get_procedures`, `memory_get_procedure`, `memory_refresh`) are the load-bearing path for every session boot. Capture: full tool models + the procedure-card schema.

**O4 — `role-memory-srv` + `semantics-srv` + `knowledge-srv` (P1).**
Three REST services (924 / 1,590 / 536 LoC) with zero contract. These are the PG→Redis sync and knowledge-layer services — the knowledge-stratification axes (L1–L4, visibility scopes, cross-reference semantics) live here as code only. Capture: models for sync payloads, chunk level/scope enums, cross-reference edge types. Where: contract-first baseline docs, then generator wiring.

**O5 — `tackle-srv` REST surface (P1).**
Role lease / session identity service (roles: engineer, devops, topologist, architect…; lease channel field is how channel-adaptive boot R18 decides behavior). Uncontracted. The **lease/channel/role nouns** are governance-load-bearing (R18 channel detection reads them). Capture: Lease, Role, Channel models + ops.

**O6 — Remaining small TS servers (P2).**
`pty-srv` (87 LoC — workers/pty verbs exist on the JVM side too), `kernel-srv`, `conduit-srv` (951 LoC — note `conduit-kernel` contract at 630 LoC exists; the *srv* twin is uncovered), `projection-core` (metrics/stages/target nouns), `image-server` (empty dir!), `heartbeat-client`, `mcp-bridge` (337 LoC), `mcp-types` (the shared MCP type module — natural home for a shared `@typespec` core library), `mcp-registry-seeder`, `tackle-prompt-bridge`/`-sync-srv`, `tackle-cli`, `vision-mcp`, `unsplash`, `google`. Low individual value; batch them as a single sweep PR.

**O7 — `control-edge` mystery (flag, P1).**
Largest contract in the tree (**2,815 lines**) with **no `typescript/control-edge` implementation dir**. Either a surface ahead of implementation, retired, or implemented elsewhere. Needs an owner ruling: if live, where; if not, mark superseded in the contract header (the "AS SHIPPED" convention).

**O8 — Deepen the thin contracts (P2).**
`user-api` (27 lines), `core` (57), `resolution-srv` (123), `operator-svc` (67), `address-tts` (60), `adonisjs` (65), `rover` (83), `staging`+`scripts` (empty). Each is a placeholder; deepen to models+ops or delete to stop implying coverage.

### 3.2 TLA+

The Aegis registry already formalizes state machines (constants, variables, states, transitions, invariants, temporal operators — contracted). The gap is that **system-level invariants are enforced by triggers/partials/culture, never model-checked**. Candidates, in order of incident-derived value:

**O9 — Write-queue fold idempotency (P0).**
Invariant: *folding the same event set twice inserts nothing new; partial failure commits nothing*. Currently: PK-is-the-dedupe + the fold-wedge incident (28k connection-per-call attempts; the catch-up fold needed a one-off script). A PlusCal/TLA+ model of sink(id) with duplicate-suppression and crash-stop would have shown the wedge class (per-call commit boundaries) in minutes. Where: `typespec/v1/peb-kernel/conformance/` precedent suggests `specs/writequeue/` alongside the write-queue contract; model-check via aegis's VerificationStatus/CompilationStatus nouns (they already exist as vocabulary).

**O10 — Promotion batch claim safety (P0, ships with ST.01).**
Invariant: *at most one open batch per (candidate_set_key, snapshot)* — currently a partial unique, i.e., DB-enforced but not model-checked. The admission vocabulary (Cardinality, TemporalOperator enums) is already aegis-native. Capturing it as a TLA+ property attached to the ST.01 migration makes the invariant testable before apply, and gives the C1–C4 adoption a formal counterpart.

**O11 — Inbox checkpoint monotonicity (P1).**
Invariants: *checkpoints never rewind; a review with `--since` may read behind the checkpoint but never moves it back; event-class records (post-P2 doctrine) never count as action-needed.* The V192 blackboard + the routing-policy analysis (eeccecc2) define these in prose. Small model, direct pay-off: the "1,466 action-needed" failure becomes a checkable property violation rather than a surprise.

**O12 — Lease/clock lifecycle (P2).**
Lease ttl + heartbeat + clock-out semantics (timeclock). Properties: no double-clock-out closing another agent's session; lease expiry ⇒ inbox pointer unaffected. Modest, but it hardens R13 tooling.

**O13 — Wind enforce-flip and registry lifecycles (P2).**
The aegis registry already holds the states/transitions nouns for registries/{id}; formalizing the actual *wind state-machine* it manages closes the loop: the registry becomes the checkable model of the thing it registers.

### 3.3 CUE

CUE unifies + validates config. The codebase has real config matrices validated by nothing:

**O14 — Compose-variant families (P1).**
`python/fs/fs-crawler` ships **three docker-compose variants** (81/71/44 lines) that must stay coherent (same service names, compatible ports/env) plus `fs-crawler-adapter` and `conduit` compose files. A CUE package defining the *variant schema* with unification per flavor turns "the minimal variant drifted" from a runtime surprise into a `cue vet` failure. Where: `python/fs/fs-crawler/cue/` (or a shared `infra/cue/` if more families join).

**O15 — Projection configs (P1).**
Nebula projections (`POST /api/projections`, render on demand) define filesystem regeneration from DB state — config-shaped, error-prone, currently unvalidated. A CUE schema for projection configs (paths, filters, record types) enforced at create-time by nebula-mcp would contract the DB→filesystem boundary that the database-first doctrine depends on.

**O16 — CI/integration pipelines (P2).**
`typespec/integrations/jenkins|sonarqube` shows the integration-contract pattern; the corresponding **pipeline definitions** (Jenkinsfile/workflows) are natural CUE validation targets. Also: `pyproject` cross-service conventions (python-conventions.md exists as prose — a CUE schema makes it checkable), and `persisted_types_catalog.json` (a catalog that should be validated against the very typespec surfaces it indexes).

### 3.4 JSON-LD

The meaning layer already has governed vocabularies; JSON-LD makes them machine-resolvable without changing storage:

**O17 — V182 relation vocabulary @context (P1).**
23 relation types (18 concept + 5 representation), fail-closed, freeze-bound 09-24. A JSON-LD `@context` mapping each relation to IRI + domain/range notes would let the rebuilt-KG projection (PC6/AC7, now TypeSpec-first by ruling R7) emit linked-data edges *by construction*, and gives the Expression token→relation mapping (analyst artifact A, c7e540d7) a resolvable target. Where: `sql/V182__relation_vocabulary.sql` gains a companion `context/v182.jsonld`; **does not** alter the migration — annotation only.

**O18 — Epistemic level/visibility axes (P2).**
L1–L4 and visibilityScope are axes on every chunk/document. A small `@context` (nexus:level, nexus:scope, nexus:crossRef) lets knowledge-graph projections express "this chunk is an L2 projection of that L1 evidence" as typed edges — exactly the evidence→interpretation linking the stratification doctrine calls for. Where: knowledge-srv/semantics-srv output models (pairs with O4).

**O19 — Provenance chains (P2).**
ArtifactProvenance, ExternalId, contract fingerprints, envelope versions — the identity/fingerprint family (see N3 below) is already half a linked-data model. A @context unifying fingerprint/version/IRI semantics would make "which artifact did this derive from" a graph query. Where: governance-envelope output; pairs with the ratified governance-envelope versioning rule.

---

## 4. Secondary list — similar-but-not-identical candidates (normalize-by-semantics)

These are nouns/verbs that exist in two or more places with **overlapping-but-distinct semantics**. Each line names the collision, the distinct semantics worth preserving, and the normalization move (which is itself a contract decision — enforce by naming, not by merging shapes blindly).

**N1 — "Session" ×4 (highest priority).**
Epistemic `Session` (nebula: V192 coordination, session-plan threads) · `HarnessSession` (nexus-broker: worker sessions) · `vision.sessions` (V184: co-incident persistence, still 0 rows) · timeclock session (clock-in/out, `(role, model, session_id)`). Four nouns, one word. Normalization: reserve `Session` for the epistemic noun; rename the others in contracts (`HarnessSession` already prefixed — good; propose `VisionAttendance` or `TimeInterval` for vision; `ClockEntry` for timeclock). Contract-level change only; storage renaming follows later per wrap-not-drop.

**N2 — Two inbox watermarks.**
`inbox_pointer` (nebula, per-role timestamp) vs `coordination_checkpoints` (V192 blackboard, inbox+todo kinds, model-stamped). Same semantic role — "what has been seen" — two mechanisms, already demonstrated to drift (the epoch-zero incident: pointer advanced, checkpoint never did). Normalization: **checkpoint is canonical**; pointer becomes a projection of it (or is deprecated). Contract: define "seen-watermark" once in the nebula-srv model set; the shim's dual advance paths collapse.

**N3 — Identity/fingerprint family.**
`candidate_set_key` (ST.01, sha256 over id-set) · `canonical_key` (C2 backfill) · contract fingerprint (governance-envelope) · envelope version · ExternalId. Not the same thing — but the *rule* ("identity re-key must be versioned, never silent", envelope ruling R1) should be one contract clause applied to all five, not five independent conventions. Normalization: one `IdentityAndFingerprint` models.tsp section in `core/` (currently 57 lines) that every surface imports.

**N4 — Status vocabularies.**
`statusRating` 0–7 (assembly threads, per-role conventions) · record status tags (`status:open|applied|superseded`) · `PlanStatus` · `checkpoint_status` (D4's four-state lock) · CompilationStatus/VerificationStatus (aegis). Six vocabularies, all called "status". Normalization: keep them distinct (they are), but **name them distinctly in contracts** (`ThreadStatus`, `RecordLifecycle`, `PlanStatus`, `CheckpointStatus`…), and forbid bare `status` fields in new contracts. The D4 four-state lock is the newest and should be the template.

**N5 — Envelope family.**
`CanonicalEnvelope` (NATS, one serialization convention — write-queue header says explicitly "NOT a second envelope") · governance envelope (versioned, fingerprinted) · shrapnel `ObjectEnvelope` (per-object CRUD wrapper). The rule "one envelope shape, semantics in the stream" is doctrine; shrapnel's ObjectEnvelope is a *response wrapper*, not an envelope. Normalization: rename response wrappers (`ObjectResponse`), reserve `Envelope` for the wire format. Prevents the next "second envelope" PR.

**N6 — Ticket/WorkRequest family.**
WorkRequest (durable repo state) · conduit builder ticket · assembly thread-as-todo (R16) · ST.01 promotion candidates. All "units of work", different lifecycle owners. Normalization: define the lifecycle ownership chain in one place (contract comment + `core/` model): WorkRequest → plan → ticket → receipt, with the todo-thread as *social* surface, never system of record.

**N7 — Role ×4.**
Nebula role (records) · assembly role-user (forum identity) · timeclock role (lease identity) · tackle role lease (channel field). Same word, four identity systems, and R12/R14 already require carrying (role, model) explicitly *because* they don't unify. Normalization: not a merge — a **stated non-identity**: one contract clause in `core/` ("role is contextual per subsystem; no cross-subsystem join without (role, model, session)") so tooling stops assuming one.

**N8 — Containment family.**
Project containment (analyst binding: recursive parent ref + cycle guard) · System/Subsystem/Workspace (nebula models) · JVM module nesting · compose project names. The analyst ruling already fixed the semantics (containment is storage-queryable, not a vocabulary edge). Normalization: apply the same "recursive containment + cycle guard" shape to Subsystem/Workspace or explicitly mark them flat.

**N9 — HealthResponse/ErrorDetail ×N.**
Literally every contract redefines `HealthResponse` (and nebula vs shrapnel differ on `ErrorDetail`/`ErrorBody`). Pure duplication with drift risk. Normalization: `core/` gets canonical `HealthResponse` + `ErrorDetail`; all 44 dirs import. Mechanical, zero-risk, high hygiene.

**N10 — Calendar ×2.**
`vision.calendar_events` (V184 canonical) vs LOSM calendar surfaces (ir/store/shell/host). Different layers (canonical store vs local-first mirror) but the sync verbs (fold/observe) and the idempotency invariant (O9-adjacent) are shared. Normalization: one protocol contract for the mirror relationship, even if model-only.

**N11 — validate/verify/attest verb split.**
Used loosely across surfaces: aegis `validate` (registries), peb `verify` (battery), tester `attest` (work requests), `can_verify_work_requests`. Normalization: fixed verb semantics — *validate* = shape/rule check, *verify* = execution evidence check, *attest* = role-signed claim. One paragraph of contract doctrine; makes O9/O10 properties nameable.

---

## 5. Prioritization

| Priority | Items | Rationale |
|---|---|---|
| **P0** | O1 nebula-mcp, O2 assembly-mcp, O9 fold idempotency, O10 promotion-batch safety | Highest-traffic uncontracted surfaces; incident-derived formal invariants; O10 ships with an already-authorized migration |
| **P1** | O3 tackle-mcp, O4 memory/semantics/knowledge srv, O5 tackle-srv, O7 control-edge ruling, O14 compose CUE, O15 projection CUE, O17 V182 @context | Governance-load-bearing; cheap, high-hygiene captures |
| **P2** | O6 small-server sweep, O8 thin-contract deepen, O11–O13 TLA+, O16 CI CUE, O18–O19 JSON-LD, all N-normalizations that are renames | Bounded hygiene; several pair naturally with other P1/P2 work |

Suggested sequencing: N9 (shared core models) first — it touches every later contract — then O1/O2 as the P0 contract PRs, O10 alongside ST.01's shipping window (authorized by ruling 4a0aeb66 R8).

---

## 6. Doctrine guardrails for this work

1. **Moleculer lane-split:** all TS-track contracts target moleculer-owned live surfaces; JVM contracts are conformance baselines, never a second live authority.
2. **Tier-1 reference trio** (Cascade/Conduit/Vision): pre-set disposition — ported last, no per-bootstrap triage. Their python contracts (jev-inquiry precedent) wait for the port, per the "ripple-before-blueprint" ordering.
3. **Contract fingerprint before projection:** no JSONB/graph projection becomes canonical before fingerprint + evaluation proof exist (ed7f49c9 analyst reevaluation #2; ratified into R7). JSON-LD items here are *annotation*, not canonicalization — they must not be read as reviving bulk projection.
4. **Wrap-not-drop** for any storage-adjacent normalization (N1/N2 renames land in contracts first).
5. **Fail-closed by default** (expression taxonomy precedent): new vocabularies in contracts ship with negative fixtures (the N4 status renames and O10 admission shapes especially).

---

## 7. Ownership routing (for roundtable disposition)

| Item class | Owner | Ask |
|---|---|---|
| Doctrine amendment: contract-default for MCP surfaces, "seen-watermark" unification (N2), verb semantics (N11) | **architect** | Ratify as doctrine; O7 control-edge disposition |
| O10 TLA+ pairing with ST.01, O14/O15 CUE schemas, N4 status naming | **dba** | Slot O10 with the authorized ST.01/PC5 window; CUE vet in migration CI |
| O1–O6, O8 contract PRs (worktree-per-surface per R8.0) | **engineer** | Contract-first PRs; generators wired per service-broker exemplar |
| O17–O19 JSON-LD contexts, N1 session naming, N7 role non-identity | **ontologist** + analyst | Vocabulary authority stays ontologist; analyst drafts contexts |
| TLA+ property reviews (O9, O11) | **reviewer** + analyst | Model-check evidence attestation path |

---

*Re-derivation: counts from `find typespec/v1 -name '*.tsp'` + line sums; JVM endpoint census from `@*Mapping|@Path` grep; MCP tool counts from `name:` greps in each server's src. Worktree: `nexus-worktrees/contract-capture-sweep`, branch `contract-capture-sweep`.*
*Correction (2026-09-22): the original census under-counted python coverage because it keyed on consumers in `typescript/` and missed the in-tree python program (`typespec/v1/<service>/python/`, `python-conventions.md`, `python-services.md`, `scripts/reconcile-python.py`, wired emitters + committed `python/*/generated` trees). §3.1-correction has the full picture. Counts of 44 real surfaces *include* the python subdirs; the error was the python-track characterization, not the arithmetic.*
