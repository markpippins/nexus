# moleculer/knowledge — canary twin of typescript/knowledge-srv

Moleculer port of `typescript/knowledge-srv` (:3109), the knowledge-graph REST
API (graph_entities, graph_edges, graph_cross_references, graph_migrations).
Listens on **:4109**; contract pinned to the incumbent's committed
`openapi.yaml` via `tools/api-docs/check_drift.py` (`MOLECULER_MIRRORS`).

## Callers (why this port matters)

- `typescript/knowledge-mcp` — thin REST proxy (`KNOWLEDGE_SRV_URL`, default
  `http://localhost:3109`), both read AND write paths. This is the only
  in-tree HTTP caller; it is also the seeder of the `.directory` queue.
- systemd `knowledge-srv.service` (incumbent unit; untouched while canary).
- NO UI callers. NO auth gate on the incumbent (CORS only) — the port
  replicates that exactly; do not add Security-Pass-Alpha-style gating here.

## Deliberately NOT ported

- **service-registry heartbeat** (incumbent `heartbeat-client`, serviceId 109,
  30s interval). A second heartbeating twin would poison the registry's
  liveness view of knowledge-srv. Heartbeat re-registration is a cutover step.

## Parity evidence (2026-09-23)

- Live canary: **28/28 byte-identical** vs the running incumbent :3109 —
  root index (with the incumbent's `port: 3109` echo), health, all entity /
  edge / cross-reference / migration / summary reads with live rows (9117
  entities / 13012 edges), filter ladders, `limit` clamps (0 → 1 floor,
  99999 → 500 ceiling, `abc` → default), validation 400s, misses 404s,
  unmatched-route Express HTML (both under /knowledge and at root), and the
  PATCH method-not-allowed case.
- jest 16/16 (hermetic: clamps, exact validation strings, envelope shapes,
  root-index contract, health contract shape).
- Drift gate: `OK moleculer/knowledge: 17 endpoints (contract
  typescript/knowledge-srv)`; negative-tested (renamed alias → exact
  missing/extra pair, exit 1).

## Run

```bash
# standalone canary on :4109 against the incumbent on :3109
npx tsc
SERVICE_PORT=4109 npx moleculer-runner --config moleculer.config.standalone.js "dist/services/*.service.js"

python3 tools/canary-diff.py            # incumbent :3109 vs port :4109
```

NOTE: moleculer-runner needs the service glob as a positional arg when using
`--config` (kernel README finding). `moleculer.config.js` (NATS namespace
`knowledge`) is the mesh-joined deployment config; the standalone variant is
for side-by-side canaries.

## Cutover notes

- Write-path parity (POST/PUT/DELETE against shared state) remains a listed
  cutover prerequisite — same posture as cascade/kernel/draft.
- Cutover repoints `KNOWLEDGE_SRV_URL` (knowledge-mcp) and the systemd unit;
  the port-map row (`moleculer/PORT-MAP.md`) must be amended in the same
  change (Ruling 4: architect-owned map).
