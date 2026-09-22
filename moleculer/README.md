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

## Contract coverage

`tools/api-docs/check_drift.py` treats a moleculer app as a second
implementation of an existing contract when it appears in
`MOLLECULER_MIRRORS` (key → incumbent service): the app carries no
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
