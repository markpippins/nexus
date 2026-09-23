# moleculer-cascade — cascade-srv port (Moleculer)

Moleculer port of `typescript/cascade-srv` (:3106) — the event cascade: events,
causation lineage, analytics, subscriber registry, assessment resolutions.

**Parity contract:** `typescript/cascade-srv/openapi.yaml` (12 endpoints). No
spec of its own — pinned to the incumbent's committed contract by
`tools/api-docs/check_drift.py` (`MOLECULER_MIRRORS`), so
`make apidocs-validate` fails CI if the alias map diverges. Second app on this
gate; see `../voyager/` for the pattern and `../README.md` for the traps.

## Topology

| | Incumbent | This port |
|---|---|---|
| Runtime | Express + `pg` (`npx tsx src/index.ts`) | moleculer-broker + moleculer-web |
| Port | `:3106` | `:4106` (canary twin) |
| Mesh | — | NATS namespace `cascade` (standalone config for local diffing) |
| Contract | `typescript/cascade-srv/openapi.yaml` | same file (mirrored) |

Surface notes vs voyager: this one has a **write** (PATCH
`/cascade/subscribers/:pattern` → subscriber enable/disable) and a root index
(`GET /` → `{name,version,port}`), both ported.

## Layout

```
services/cascade.service.ts   11 actions (SQL mirrors the incumbent, incl. recursive-CTE lineage)
services/api.service.ts       moleculer-web gateway: contract-exact alias map
moleculer.config.js           PROD config (moleculer-runner loads ONLY this one)
moleculer.config.standalone.js  same broker, NATS off (local canary)
moleculer.config.ts           jest/tsc config
test/cascade.service.test.ts  9 hermetic tests (clamping, allowlists, lineage builder)
tools/canary-diff.py          side-by-side response differ vs the incumbent
```

## Commands

```bash
npm install
npm test
npx tsc --noEmit

# standalone canary on :4106 against the incumbent on :3106
SERVICE_PORT=4106 npx moleculer-runner --config moleculer.config.standalone.js "services/**/*.service.ts"

python3 tools/canary-diff.py --incumbent http://localhost:3106 --port http://localhost:4106
```

## Parity evidence

1. **Route surface** — `OK moleculer/cascade: 12 endpoints (contract
   typescript/cascade-srv)`; negative-tested (renamed alias → exit 1 with the
   exact missing/extra pair; `--update` refuses to touch the contract).
2. **Behavior** — `tools/canary-diff.py`: **24/24 identical**, covering every
   path plus pagination clamps (`limit=500` → 200, `limit=abc` → 50), filter
   composition, analytics window fallbacks (`range=bogus` → 24 hours), 404/400
   envelopes, the 500 db-error envelope, the custom `edgeType` lineage param,
   and the no-field PATCH 400 (rejected before any write; the real update path
   is deliberately NOT exercised against shared state).
3. Jest 9/9 (`tsc --noEmit` clean) pin the pure semantics: clamp arithmetic,
   the INTERVAL/TRUNC allowlists (the safety property behind the incumbent's
   SQL interpolation — preserved as-is, not parameterized), and the lineage
   graph builder including the `truncated >= depth*10` heuristic and nodeMap
   dedup.

## Notes

- The analytics SQL interpolates `interval`/`truncUnit` into the query text.
  That is safe *only* because of the allowlists; the port keeps the identical
  mechanism rather than parameterizing, so the parity surface is exact.
- Env names mirror `db.ts` (`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`,
  `DB_PASS`) — same EnvironmentFile feeds either implementation.
- `/cascade/events` ordering (timestamp DESC) makes list bodies
  time-sensitive; the differ re-checks mismatches once and normalizes the
  health `time` field.
- `GET /cascade/health` echoes `port: 3106` and the root index echoes
  `{name: "cascade-srv", ..., port: 3106}` on the port too — verbatim
  incumbent values, kept for byte parity (the gateway host port differs).
