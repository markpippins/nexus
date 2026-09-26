# atomic-moleculer

Moleculer apps — the broker-tier reimplementation track for `nexus/typescript`
services ("one distro per machine": TypeSpec *or* Moleculer *or* JVM). A port
lives beside its incumbent and is cut over only behind a contract gate.

| App | Port | Counterpart | Parity gate | Status |
|---|---|---|---|---|
| `search/` | 4050 | broker search service (JVM `service-broker/search-service`) | `typespec/v1/moleculer/typescript/` (route-count only) | M1 slices 1 done; 2–4 open |
| `solscript/` | 4060 | — (facade over `@nexus/solscript`) | `typespec/v1/solscript/typescript/operations.tsp` | app built; no manifest row |
| `nexus-broker/` | 4080 | worker tier (`worker.pty` retired pty-srv) | `typespec/v1/nexus-broker/typescript/` (known drift) | partial cutovers |
| `voyager/` | 4114 | `typescript/voyager-srv` (:3114) | **`typescript/voyager-srv/openapi.yaml`** via `tools/api-docs/check_drift.py` | port complete, canary-diffed, not cut over |
| `cascade/` | 4106 | `typescript/cascade-srv` (:3106) | **`typescript/cascade-srv/openapi.yaml`** via `tools/api-docs/check_drift.py` | port complete, canary-diffed 24/24, not cut over |
| `kernel/` | 4100 | `typescript/kernel-srv` (:8100) | **`typescript/kernel-srv/openapi.yaml`** via `tools/api-docs/check_drift.py` | port complete, canary-diffed 25/25, not cut over |
| `draft/` | 4170 | `typescript/draft-srv` (:3170) | **`typescript/draft-srv/openapi.yaml`** via `tools/api-docs/check_drift.py` | port complete, canary-diffed 15/15, not cut over (drivers copied verbatim; X-Nexus-Internal gate replicated) |
| `knowledge/` | 4109 | `typescript/knowledge-srv` (:3109) | **`typescript/knowledge-srv/openapi.yaml`** via `tools/api-docs/check_drift.py` | port complete, canary-diffed 28/28, not cut over (no auth gate on incumbent — CORS only; registry heartbeat deliberately not ported) |
| `role-memory/` | 4150 | `typescript/role-memory-srv` (:3500) | **`typescript/role-memory-srv/openapi.yaml`** via `tools/api-docs/check_drift.py` | port complete, canary-diffed 30/30, not cut over (shared Redis/PG parity — refresh converges the same cache; no auth gate on incumbent) |
| `semantics/` | 4160 | `typescript/semantics-srv` (:3160) | **`typescript/semantics-srv/openapi.yaml`** via `tools/api-docs/check_drift.py` | port complete, canary-diffed 12/12 + live-data envelopes, not cut over (table-driven CRUD via stored procs + T02 asset spine; no auth gate on incumbent — CORS only) |
| `tackle/` | 4410 | `typescript/tackle-srv` (:3410) | **`typescript/tackle-srv/openapi.yaml`** via `tools/api-docs/check_drift.py` | port complete, canary-diffed 54/54, not cut over (dispatch-through-Express: verbatim incumbent app behind one `tackle.dispatch` action, 86 aliases; real writes stay incumbent-owned) |
| `prompt-sync/` | 4501 | `typescript/tackle-prompt-sync-srv` (:3501) | **`typescript/tackle-prompt-sync-srv/openapi.yaml`** via `tools/api-docs/check_drift.py` | port complete, canary-diffed 44/44, not cut over (Prompt Registry PG→Redis sync — role-memory twin pattern, shared-cache convergence; no auth gate on incumbent) |
| `execution/` | 4110 | `typescript/execution-srv` (:3110) | **`typescript/execution-srv/openapi.yaml`** via `tools/api-docs/check_drift.py` | port complete, dispatch-through-Express; incumbent is read-only so the canary exercises every endpoint live; not cut over (moleculer broker tier already carries a partial `worker.execution` subset on :4080 — this twin mirrors the FULL legacy REST surface) |
| `conduit/` | 4104 | `typescript/conduit-srv` (:3104) | **`typescript/conduit-srv/openapi.yaml`** via `tools/api-docs/check_drift.py` | port complete, dispatch-through-Express; canary is reads + validation negatives only because four routes mutate shared pipeline state; not cut over |

## Contract coverage

`tools/api-docs/check_drift.py` treats a moleculer app as a second
implementation of an existing contract when it appears in
`MOLECULER_MIRRORS` (key → incumbent service): the app carries no
`openapi.yaml` of its own, its gateway alias map is compared against the
incumbent's committed spec, and `--update` refuses to "fix" the port by
rewriting that spec. `voyager/` is the first app wired this way.

Two traps when porting another service — both cost debugging time and are easy
to repeat:

1. **Whitelist masks do not span dots.** moleculer-web matches `whitelist`
   entries as *paths*, so `["svc.*"]` allows `svc.health` but rejects
   `svc.grouped.action` — as a 404 `ServiceNotFoundError`, which looks like a
   missing action rather than a policy denial. Use `svc.**`.
2. **`TRANSPORTER=null` does not disable the transporter.** moleculer-runner
   parses that variable itself and passes the literal string `"null"`
   (`Invalid transporter type 'null'`). Use a config file: see
   `voyager/moleculer.config.standalone.js`.
3. **Dispatch-through-Express for wide Express incumbents.** When the
   incumbent is many Express routers with verbatim middleware semantics
   (query parsing, SSE, finalhandler HTML 404s), do not hand-shim handlers:
   host the incumbent's real `app` behind one action (`tackle.dispatch`)
   and point every alias at it — `ctx.meta.$req/$res` stashed in
   `onBeforeCall` gives the gateway the real req/res; local callers pass
   meta by reference. See `tackle/services/express-app.ts`.

## Commands

```bash
cd moleculer/<app>
npm install
npm test                  # jest (hermetic where the app allows it)
npm start                 # moleculer-runner, services/**/*.service.ts
python3 ../../tools/api-docs/check_drift.py   # from the repo root
```

No CI workflow currently runs a moleculer app's `npm test`; the apidocs drift
gate is what CI enforces for contract-bearing ports.
