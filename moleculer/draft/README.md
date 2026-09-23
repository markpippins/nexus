# moleculer/draft — port of typescript/draft-srv

Moleculer reimplementation of the DB Workbench API (`typescript/draft-srv`,
:3170) as a canary twin on **:4170**, pinned to the incumbent's committed
`openapi.yaml` by the CI drift gate — the same "one contract, two
implementations, one gate" discipline as `voyager`, `cascade`, and `kernel`.

This is the port with the **livest caller** of any in the queue:
`angular/data-explorer-ui` (`dbEngine.ts` via the `server.ts` proxy), which is
under active development.

| | incumbent | port |
|---|---|---|
| Source | `typescript/draft-srv` | `moleculer/draft` |
| Port | `:3170` (loopback bind) | `:4170` (canary twin) |
| Contract | `typescript/draft-srv/openapi.yaml` (6 endpoints) | shared — no spec of its own |
| Gate | `make apidocs-validate` | same (`check_drift.MOLLECULER_MIRRORS`) |
| Auth | `X-Nexus-Internal` fleet secret, fail-closed | **replicated exactly** |

## Layout

```
services/draft.service.ts        actions mirroring routes/db.ts incl. envelope parity
services/api.service.ts          gateway + the fleet-secret gate (onBeforeCall/onNotFound/onError)
services/drivers/{types,postgres,mysql,registry}.ts   COPIED VERBATIM from the incumbent (diff-verified)
moleculer.config.ts              dev/test broker config (jest/tsc; runner never loads it)
moleculer.config.js              PROD config (NATS mesh, namespace "draft") — runner loads ONLY this
moleculer.config.standalone.js   same broker, NATS off (local canary)
tools/canary-diff.py             side-by-side response differ vs the incumbent
```

## Drivers are files, not reimplementations

Unlike the other ports (where SQL is mirrored statement-for-statement by hand),
the drivers here are **copied verbatim** (`diff -q` clean): pool-cache TTL and
eviction, TLS policy (`NEXUS_PG_TLS_INSECURE`), PG_TYPES map, parallel catalog
probes. The moleculer app is the transport seam; the data path is the same
code. Any driver fix belongs in `typescript/draft-srv` first, then re-copy.

## The fleet-secret gate (Security Pass Alpha, audit C1, decision 22fe12bc)

The incumbent fails closed: 503 `service misconfigured: missing internal
secret` when `NEXUS_INTERNAL_SECRET` is unset, 403 `{"error":"forbidden"}` on
missing/mismatched header, `/api/health` exempt. Replicated exactly —
including **ordering**: the gate precedes routing, so an unauthenticated
request to an unknown path is a **403, not a 404**.

Two moleculer-web mechanics this cost debugging time to establish:

1. **There is no `onRequest` route option in moleculer-web 0.10** — an
   `onRequest` key in the route settings is silently ignored (the first canary
   run got 200s past a non-existent gate). The real hook is `onBeforeCall`,
   which runs after alias resolution but before the action.
2. Unmatched aliases dispatch through `onError` as moleculer-web's built-in
   `NotFoundError` (same kernel-port finding), not `onNotFound` — the 403 gate
   for unknown paths therefore lives in the `onError` NotFoundError branch.
   `onNotFound` only fires when no route matches the prefix at all.

Callers are unchanged: `data-explorer-ui/server.ts` already injects
`X-Nexus-Internal` on its server-to-server proxy.

## Run

```bash
npx tsc   # strip-types cannot resolve the drivers' relative imports
set -a; source /home/codex/dev/nexus/etc/fleet-internal.env; set +a
SERVICE_PORT=4170 NEXUS_INTERNAL_SECRET="$NEXUS_INTERNAL_SECRET" \
  npx moleculer-runner --config dist/moleculer.config.js "dist/services/*.service.js"

python3 tools/canary-diff.py   # incumbent :3170 vs port :4170
```

## Parity evidence

1. **Surface** — drift gate: `OK moleculer/draft: 6 endpoints (contract
   typescript/draft-srv)`; negative-tested (renamed alias → exact
   missing/extra pair; `--update` refuses mirrored regeneration).
2. **Behavior** — `tools/canary-diff.py`: **15/15 byte-identical** (normalized
   where noted for genuinely per-process values: timestamp/latency/rowCount/
   version). Covers the secret gate (403 no-auth, health exemption, unknown-
   route-before-auth ordering), the engine ladders (unknown → 400 with
   per-route message shapes, mysql stub → 501 fail-visible), bad-credential
   refusal (502 envelope), and live read-only SELECTs against the shared local
   DB (types map, error-as-result bodies). No DDL and no writes — the query
   surface executes user SQL by contract, so canary payloads are SELECT-only.
3. **Unit** — jest 10/10: gate semantics and ordering, ladder messages,
   registry defaulting, ENGINE_NOT_IMPLEMENTED classification, query error
   body shape; drivers imported directly (verbatim files, not mocked).

## Cutover prerequisites (not done here)

1. Owner ruling on cutover order (architect lane; Q1 lane-split).
2. Caller cutover: `data-explorer-ui/server.ts` `DB_WORKBENCH_URL` repoint is
   the entire redirect surface (one env var, one consumer in-tree).
3. systemd unit + manifest row; decide loopback-bind parity for the port
   (the canary binds 0.0.0.0 for probing; cutover should re-enable loopback).
