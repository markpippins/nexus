# substance

Moleculer twin of `typescript/substance-srv` (:3115) — segment-set resolution
over `nebula.segment_sets`, canary on **:4115** (fills the free slot in the
ratified 41xx band between voyager 4114 and aegis 4116).

- **Contract:** `typescript/substance-srv/openapi.yaml` (8 paths / 11
  endpoints) — the alias map is GENERATED from it, so gateway surface ==
  contract surface by construction; `check_drift.py` holds the equality from
  the other side.
- **Pattern:** dispatch-through-Express — the incumbent's TS route stack
  (http/db/cache/config/schemas/repository/listener + routes) is copied
  verbatim and hosted behind one `substance.dispatch` action
  (`ctx.meta.$req/$res` stashed in `onBeforeCall`).
- **FastAPI lineage (pinned by tests):** `{detail}` error envelopes, 422 for
  body-validation AND non-UUID path params, 422 `json_invalid` for malformed
  JSON, 404 for unknown `domain_type` (the one deliberate TS-port divergence,
  documented in routes/links.ts), static `/healthz` ({status:"ok"}, no DB
  probe), bare-array list responses.
- **Day-one additions:** global rate limiter (300 req/min/IP; the incumbent
  carries none — same CodeQL alert-delta rationale as the PR #575 family).
- **Deliberately excluded background subsystems** (see services/express-app.ts
  header): the registry heartbeat (:8085, service id 117) and the
  `segment_expired` pg-notify cache-invalidation listener. Process subsystems,
  not HTTP contract; the Redis TTL remains the staleness safety net exactly as
  the listener module itself documents for missed notifications.
- **Canary posture:** reads + validation negatives only — writes mutate
  `nebula.segment_sets` and invalidate Redis cache entries; canary probes are
  malformed/UUID-absent negatives that reject before DB or cache work.
- **Not cut over / not deployed.** Incumbent stays authoritative.

## Commands

```bash
npm install
npm test                  # jest, hermetic (db.js mocked; Redis via the factory seam)
npm start                 # moleculer-runner, mesh-joined (NATS :4222, namespace substance)
npx moleculer-runner --config ./moleculer.config.standalone.js 'services/**/*.service.ts'
                          # standalone canary — no NATS
python3 ../../tools/api-docs/check_drift.py   # from the repo root
```
