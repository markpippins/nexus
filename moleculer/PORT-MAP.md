# Moleculer — Port Map (mirror of the ratified convergence artifact)

> **Status: RATIFIED** — mirror side of the architect-owned port map (Ruling 4,
> decision `8ae4761b`; operator endorsement 2026-09-22). The canonical table
> lives in `jvm/ARCHITECTURE.md` (Port Allocation); this file is the moleculer
> distribution's view of the same ownership map. The registry
> (terrain-mcp / terrain :8084) is the authoritative "what runs where" surface.
>
> **Moleculer tier alignment (Rule 5):** moleculer services register on the
> NATS mesh (`:4222`) in their own namespace and expose REST via moleculer-web
> on their deployment port. They do NOT claim JVM-band ports (8080-8099), the
> shared service-registry 8085, or any TS-owned surface.

## Moleculer-deployed services (REST via moleculer-web)

| Port | Service | Namespace | Legacy counterpart | Registry status |
|------|---------|-----------|--------------------|-----------------|
| 4050 | `api` gateway + `google-search` (`simpleSearch`, `health`) | `search` | `broker-gateway :8081` op-dispatch (`googlePublicSearch`); search-service embedded, no HTTP surface of its own | ONLINE — live per M1 (slice 1 parity proven 2026-09-11) |
| 4060 | `solscript` facade (resolution-domain evaluation API) | `solscript` | JVM `@nexus/solscript` (no direct legacy HTTP twin) | OFFLINE (systemd `moleculer-solscript.service`; built, not running) |
| 4080 | `nexus-broker` worker tier (`worker.harness`, `worker.pty`, `worker.execution`, `keychain-snapshot`) | `broker` | `harness-srv :3420`, `execution-srv :3110`, `pty-srv :3121` (**retired M3** → `worker.pty-transport` WS `:3130`) | ONLINE — partial cutovers |
| 4114 | `voyager` (canary twin of `typescript/voyager-srv`) | `voyager` | `typescript/voyager-srv :3114` (read-only filesystem/entity voyager; contract pinned to incumbent `openapi.yaml`) | CANARY — merged (PR #453), canary-diffed byte-identical, **not cut over / not deployed** |
| 4106 | `cascade` (canary twin of `typescript/cascade-srv`) | `cascade` | `typescript/cascade-srv :3106` (pipeline telemetry + lineage; contract pinned to incumbent `openapi.yaml`; first ported write path — PATCH subscriber toggle gated for cutover) | CANARY — merged (PR #457), canary-diffed byte-identical, **not cut over / not deployed** |
| 4100 | `kernel` (canary twin of `typescript/kernel-srv`) | `kernel` | `typescript/kernel-srv :8100` (resolution kernel: transitions, receipts, pg_notify SSE; contract pinned to incumbent `openapi.yaml`) | CANARY — merged (PR #463), canary-diffed byte-identical, **not cut over / not deployed** |
| 4170 | `draft` (canary twin of `typescript/draft-srv`) | `draft` | `typescript/draft-srv :3140` (DB workbench API behind `data-explorer-ui`'s server-to-server proxy; fail-closed `X-Nexus-Internal` gate replicated; contract pinned to incumbent `openapi.yaml`) | CANARY — PR #479, canary-diffed byte-identical, **not cut over / not deployed** |
| 4109 | `knowledge` (canary twin of `typescript/knowledge-srv`) | `knowledge` | `typescript/knowledge-srv :3109` (knowledge graph REST; sole caller knowledge-mcp REST proxy; no auth gate on incumbent — CORS only; contract pinned to incumbent `openapi.yaml`) | CANARY — PR #480, canary-diffed byte-identical, **not cut over / not deployed** |
| 4150 | `role-memory` (canary twin of `typescript/role-memory-srv`) | `role-memory` | `typescript/role-memory-srv :3500` (Role Memory Procedure Registry: PG→Redis sync + cache reads; callers tackle-srv memory.ts, harness-srv, operator-svc, mesh-register; no auth gate on incumbent; contract pinned to incumbent `openapi.yaml`) | CANARY — submitted with this row (PR for the port), canary-diffed byte-identical, **not cut over / not deployed**; 4150 row submitted for Ruling 4 ratification |
| 4160 | `semantics` (canary twin of `typescript/semantics-srv`) | `semantics` | `typescript/semantics-srv :3160` (Semantics Topology Legend: REST over `semantics.*` — table-driven CRUD via stored procs, T02 asset identity spine, evidence filters, drift lifecycle; caller `semantics-ui`; no auth gate on incumbent — CORS only; contract pinned to incumbent `openapi.yaml`) | CANARY — merged (PR #509), canary-diffed byte-identical (12/12 read/negative + 6/6 live-data envelope routes), **not cut over / not deployed** |
| 4410 | `tackle` (canary twin of `typescript/tackle-srv`) | `tackle` | `typescript/tackle-srv :3410` (roundtable read/write backbone: AI config, roles, prompts, tasks, scheduler, memory registry, projections, sessions, logs, audit; dispatch-through-Express — verbatim incumbent app behind `tackle.dispatch`, 86 aliases; callers tackle-ui proxy chain + role-memory twin; real writes (sessions kill, projections render, config mutations) stay incumbent-owned; contract pinned to incumbent `openapi.yaml`) | CANARY — submitted with this row (PR for the port), canary-diffed byte-identical (54/54 read/negative + SSE header parity), **not cut over / not deployed** |
| 4501 | `prompt-sync` (canary twin of `typescript/tackle-prompt-sync-srv`) | `prompt-sync` | `typescript/tackle-prompt-sync-srv :3501` (Prompt Registry: PG→Redis sync — tackle.prompts/tackle.tasks → prompt:proc/idx, task:idx, prompt:meta; callers tackle-prompt-bridge, tackle-cli; no auth gate on incumbent; contract pinned to incumbent `openapi.yaml`) | CANARY — submitted with this row (PR for the port), canary-diffed byte-identical (44/44 incl. POST /refresh convergence), **not cut over / not deployed** |
| 4110 | `execution` (canary twin of `typescript/execution-srv`) | `execution` | `typescript/execution-srv :3110` (Execution Observability: read-only REST over the `execution` schema — paginated request/lease/attempt/receipt catalogs, lifecycle state, lease integrity, cross-table integrity scan, fleet views, witnessed-run projections + diagnostics, pipeline-origin lineage; no auth gate on incumbent; contract pinned to incumbent `openapi.yaml`. Note: the moleculer broker tier (:4080 `worker.execution`) already mirrors a SUBSET for the UI — this twin mirrors the FULL legacy REST surface) | CANARY — submitted with this row (PR for the port), dispatch-through-Express, full-surface read-only canary, **not cut over / not deployed** |
| 4104 | `conduit` (canary twin of `typescript/conduit-srv`) | `conduit` | `typescript/conduit-srv :3104` (WorkRequest pipeline REST: workflows, ticket detection/lineage, tokens, config, SSE session logs, governance replay/events, vision requests/receipts, projection drift; contract pinned to incumbent `openapi.yaml`) | CANARY — submitted with this row (PR for the port), dispatch-through-Express with reads/validation-negative canary only, **not cut over / not deployed** |
| 4420 | `harness` (canary twin of `typescript/harness-srv`) | `harness` | `typescript/harness-srv :3420` (Generic agent execution harness: POST /run + /run-direct spawn opencode/ollama with failover ladders + runaway watchdog, /resolve-context dry-run, async job registry with replayable SSE; 8 routes; contract pinned to incumbent `openapi.yaml`. Note: nexus-broker `worker.harness` on :4080 mirrors a subset — same pattern as execution/worker.execution) | CANARY — submitted with this row (PR for the port), dispatch-through-Express, reads + validation negatives canary ONLY (execute routes spawn agents/mutate state), **not cut over / not deployed** |

## Shared infrastructure (NOT moleculer-owned — do not claim)

| Port | Owner | Why moleculer must not touch it |
|------|-------|--------------------------------|
| 8085 | service-registry (JVM) | shared infrastructure across distributions (Ruling 3) |
| 8081 | broker-gateway (JVM) | live until its moleculer twin reaches zero-traffic cutover; M1 owns "zero SEARCH traffic" not "zero broker traffic" |
| 8082 | nexus-control-edge (TS) | TS-owned |
| 8092 | thallium-search (JVM nexus-core lane on **thallium** `192.168.1.82:8092`, `/search/api`) | thallium hosts JVM nexus-core (Ruling 1); helium JVM retired |
| 8098 | peb-kernel (Python) | Python owns 8098; JVM peb-bootstrap defers to 8099 |

## NATS mesh

| Component | Address |
|-----------|---------|
| NATS broker | `nats://localhost:4222` (services default via `NATS_URL` env, `nats://localhost:4222`) |
| Namespaces | one per service family: `search`, `solscript`, `broker`, `voyager`, `cascade`, `kernel`, `draft`, `knowledge`, `role-memory`, `semantics`, `tackle`, `prompt-sync`, `execution`, `conduit`, `harness` |

## Host posture on titanium (DBA gate, 2026-09-23)

Moleculer does **not run locally** on titanium — enforced, not assumed:

- The candidate-tier lane runs **containerized** (`cand-broker` on host
  `:14080`, NATS mesh on `:4222`) — outside the moleculer band.
- Both launch units (`moleculer-search.service`, `moleculer-solscript.service`)
  are **disabled-by-default**; there are no system-level moleculer units.
- The gate is executable: `bin/assert_moleculer_ports.sh` fails loudly if any
  mapped port above is bound, a moleculer runner is on the host (containerized
  runners are detected via `/proc/<pid>/cgroup` and permitted), or a unit is
  enabled. Wire it into canary runs and CI before anything that depends on
  "no local moleculer" being true.
- Exception path (documented, time-boxed): a locally-running canary app needs
  an operator-approved exception — point
  `NEXUS_MOLECULER_EXCEPTION_DOC` at a file containing
  `EXCEPTION-UNTIL: <ISO date>`; the gate passes with a loud notice inside
  the window and fails again the day after. Canary evidence produced under
  an exception must cite the window.

## Freeze discipline

- The M1 cutover manifest (`M1-SERVICE-MANIFEST.md`, frozen 2026-09-09) is the
  single inventory M1 gates cutover against; this port map records present
  ownership, not cutover authorization.
- Any port change ratified in `jvm/ARCHITECTURE.md` must be mirrored here and
  in the owning service's config + launch units in the same change; the
  terrain registry entry follows at the next registration heartbeat.
- Canary/deployment ports (4100/4104/4106/4109/4110/4114/4150/4160/4170/4410/4420/4501) are the exception to
  one-live-authority: a canary twin may co-listen on its 41xx twin while its
  incumbent stays live, and must be removed from the map when cutover completes
  (dead routes die). Canary rows in this table require architect ratification
  under Ruling 4 (port map is architect-owned). The 4114 voyager row was the
  originally ratified exception (decision 8ae4761b / port-map ping 422bc879);
  rows 4100/4106/4114/4170 were RATIFIED 2026-09-23 (architect decision on
  the port-band), 4150/4160 ratified with their merges, 4410 (+1000 band,
  submitted with the tackle PR) and 4501 (+1000 band, submitted with the
  prompt-sync PR) extend the same scheme; 4104
  (conduit, submitted with the conduit port PR) follows that pattern; 4420
  (harness, submitted with the harness port PR) follows that pattern; 4110
  (execution, submitted with the execution port PR) follows that pattern;
  record); row 4109 (PR #480) follows the same pattern and is submitted for
  ratification in the same pass.