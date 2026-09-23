# moleculer-voyager — voyager-srv port (Moleculer, read-only)

Moleculer port of `typescript/voyager-srv` (:3114) — the read-only voyager over
the `voyager` schema (scan epochs, file/directory observations, topology
signals + edge hints, entities, metadata spans, stats).

**Parity contract:** `typescript/voyager-srv/openapi.yaml` (17 endpoints). The
port carries **no spec of its own** — it is pinned to the incumbent's committed
contract by `tools/api-docs/check_drift.py` (`MOLLECULER_MIRRORS`), so
`make apidocs-validate` fails CI if the gateway alias map gains, loses or
renames a path, exactly as it would for a renamed Express route.

## Topology

| | Incumbent | This port |
|---|---|---|
| Runtime | Express + `pg` (`node dist/index.js`) | moleculer-broker + moleculer-web |
| Port | `:3114` | `:4114` (canary twin; nothing else uses 41xx) |
| Mesh | — | NATS namespace `voyager` (standalone config for local diffing) |
| Contract | `typescript/voyager-srv/openapi.yaml` | same file (mirrored) |

## Layout

```
services/voyager.service.ts  16 actions over the voyager schema (SQL mirrors the incumbent)
services/api.service.ts      moleculer-web gateway: contract-exact alias map + error envelopes
moleculer.config.js          PROD config (moleculer-runner loads ONLY this one)
moleculer.config.standalone.js  same broker, NATS transporter off (local canary)
moleculer.config.ts          jest/tsc config (runner never loads it)
test/voyager.service.test.ts 10 hermetic tests (shaping + page window)
tools/canary-diff.py         side-by-side response differ vs the incumbent
```

## Commands

```bash
npm install
npm test          # jest, hermetic: no DB, no broker, no HTTP bind
npx tsc --noEmit

# mesh-joined (prod shape)
npm start

# standalone canary on :4114 against the incumbent on :3114
SERVICE_PORT=4114 npx moleculer-runner --config moleculer.config.standalone.js "services/**/*.service.ts"

# behavior diff (see "Parity evidence")
python3 tools/canary-diff.py --incumbent http://localhost:3114 --port http://localhost:4114
```

## Parity evidence

1. **Route surface** — `python3 tools/api-docs/check_drift.py`:
   `OK moleculer/voyager: 17 endpoints (contract typescript/voyager-srv)`.
   Verified to *fail* when an alias is renamed (it reports the exact
   missing/extra pair) and `--update` deliberately refuses to regenerate a
   mirrored port: the drift is in the alias map, not in the spec.
2. **Behavior** — `tools/canary-diff.py` issues 35 identical requests to both
   implementations (all 17 paths plus filters, pagination clamps, unknown-id
   and bad-uuid cases) and byte-compares status + body: **34/35 identical**,
   including byte-identical 500 envelopes on the drifted paths and identical
   epoch-millis timestamp rendering.

### The one difference, and it is not a port defect

`GET /api/stats`: the **deployed** incumbent returns 11 keys, the committed
source (and this port) returns 8. The deployed `dist/` predates commit
`135e5565` *"refactor(voyager-srv): prune identity/entity/requirement routes
(T04 — physical observer only)"*, so it still serves the pruned surface:

| | deployed `:3114` | committed source / this port |
|---|---|---|
| paths | 19 (incl. `/api/identity/candidates`, `/api/requirements`) | 17 (contract) |
| `stats` keys | 11 (incl. `identity_candidates`, `entities`, `entity_drifts`, `requirement_candidates`) | 8 |

Nothing has rebuilt/redeployed voyager-srv since that prune. This port
implements **the committed source** — the reproducible definition of the
service and the one the drift gate polices. Consequence to declare before
cutover: switching to the port *removes* those two routes and four stats keys
for any consumer still reading them. (They are dead on arrival today anyway —
both pruned routes return `500 column "discovered_at" does not exist`.)

## Pre-existing incumbent defects reproduced bug-for-bug

The port is faithful, not corrective: these SQL failures are the incumbent's,
and reproduce with identical messages on both sides. They are **not** introduced
here and should be fixed (or the schema reconciled) before any cutover is
called clean:

| Path | Both implementations return |
|---|---|
| `/api/observations/files?*` | `500 column "discovered_at" does not exist` |
| `/api/observations/directories?*` | `500 column "discovered_at" does not exist` |
| `/api/topology/signals?*` | `500 column "structure" does not exist` |
| `/api/topology/edge-hints?*` | `500 column "discovered_at" does not exist` |
| `/api/spans?*`, `/api/spans/:id` | `500 column "discovered_at" does not exist` |
| `/api/scan-epochs/:id` (non-numeric id) | `500 invalid input syntax for type bigint` |
| `/api/entities/:id`, `/api/entities/by-id/:entityId` | `500 invalid input syntax for type bigint/uuid` |

Working paths (byte-identical on both): `/health`, `/api/health`,
`/api/scan-epochs`, `/api/scan-epochs/:id` (numeric), `/api/entities`,
`/api/observations/files/by-id/:observationId`, `/api/observations/files/:id`,
`/api/stats`.

## Trap worth knowing before porting another service

moleculer-web matches `whitelist` masks with a **path** matcher, where `*` does
not cross a `.`. `whitelist: ["voyager.*"]` therefore authorises only
single-segment actions (`voyager.health`) and rejects every grouped one
(`voyager.scanEpochs.list`) with a **404 `ServiceNotFoundError`** — which reads
exactly like a missing action, not a policy rejection. Use `voyager.**`.
The sibling apps (search, solscript) never hit this because all their actions
are single-segment.

## Test coverage note

Jest covers response shaping (camelCase, Date → epoch millis, per-endpoint page
window). The **route surface** is covered by the apidocs drift gate in CI; it is
deliberately not duplicated in jest. No CI workflow currently runs a moleculer
app's `npm test` — the gate above is what CI enforces for this port.
