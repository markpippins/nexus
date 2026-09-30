# wind

Moleculer twin of `typescript/wind-srv` (:3300) — scheduling/DAG orchestration
over the `wind.*` schema, canary on **:4300** (+1000 band, `cd3b8419` scheme).

| Gate | `make apidocs-validate` | same (`check_drift.MOLECULER_MIRRORS`) |
|------|------------------------|----------------------------------------|

- **Contract:** `typescript/wind-srv/openapi.yaml` (57 paths / 87 endpoints) —
  the alias map is GENERATED from it, so gateway surface == contract surface
  by construction; `check_drift.py` holds the equality from the other side.
- **Pattern:** dispatch-through-Express — the incumbent's route stack is
  copied verbatim (plain-JS ESM under `allowJs`, peb-twin style) and hosted
  behind one `wind.dispatch` action (`ctx.meta.$req/$res` stashed in
  `onBeforeCall`).
- **Day-one additions:** global rate limiter (300 req/min/IP; the incumbent
  rate-limits only 3 write surfaces — same CodeQL alert-delta rationale as
  PR #575). Background subsystems (rover scheduler, events processor,
  NATS/pg-notify listeners) are deliberately NOT started: they are not part
  of the HTTP contract and a canary must not double-publish
  `harvest.created` or double-poll `wind.events` alongside the incumbent.
- **Canary posture:** reads + validation negatives only — instance lifecycle
  (`advance/execute/pause/resume/run/stop`) and execution-request `dispatch`
  drive execution state; canary probes are malformed/UUID-absent negatives
  that reject before DB work.
- **Not cut over / not deployed.** Incumbent stays authoritative.

## Verbatim deviations from `typescript/wind-srv/src/index.js`

1. no `app.listen` / heartbeat-client / process handlers / shutdown
   coordinator (broker owns lifecycle; parity surface is HTTP-only)
2. no scheduler/event-processor/listener startup (see above)
3. imports carry `.js` extensions (NodeNext)
4. day-one global rate limiter

Envelopes below the 429 ceiling are byte-identical.

## Commands

```bash
npm install
npm test                  # jest, hermetic (db.js mocked at the module boundary)
npm start                 # moleculer-runner, mesh-joined (NATS :4222, namespace wind)
npx moleculer-runner --config ./moleculer.config.standalone.js 'services/**/*.service.ts'
                          # standalone canary — no NATS
python3 ../../tools/api-docs/check_drift.py   # from the repo root
```
