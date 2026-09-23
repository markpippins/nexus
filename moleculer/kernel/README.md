# moleculer/kernel — port of typescript/kernel-srv

Moleculer reimplementation of the event-sourced kernel API
(`typescript/kernel-srv`, :8100) as a canary twin on **:4100**, pinned to the
incumbent's committed `openapi.yaml` by the CI drift gate — the same
"one contract, two implementations, one gate" discipline as
`moleculer/voyager` and `moleculer/cascade`.

| | incumbent | port |
|---|---|---|
| Source | `typescript/kernel-srv` | `moleculer/kernel` |
| Port | `:8100` | `:4100` (canary twin) |
| Contract | `typescript/kernel-srv/openapi.yaml` (14 endpoints) | shared — no spec of its own |
| Gate | `make apidocs-validate` | same (`check_drift.MOLECULER_MIRRORS`) |

## Layout

```
services/kernel.service.ts   actions mirroring routes.ts statement-for-statement
services/api.service.ts      moleculer-web gateway — the alias map IS the parity surface
services/notify.ts           verbatim port of src/notify.ts (pg_notify LISTEN + SSE)
moleculer.config.ts          dev/test broker config (jest/tsc; runner never loads it)
moleculer.config.js          PROD config (NATS mesh, namespace "kernel") — runner loads ONLY this
moleculer.config.standalone.js  same broker, NATS off (local canary)
tools/canary-diff.py         side-by-side response differ vs the incumbent
```

## Run

```bash
# standalone canary on :4100 against the incumbent on :8100
SERVICE_PORT=4100 npx moleculer-runner --config dist/moleculer.config.js "dist/services/*.service.js"
# (build first: npx tsc — strip-types cannot resolve the relative notify.ts
#  import without explicit extensions, so the canary runs from compiled dist/)

python3 tools/canary-diff.py            # incumbent :8100 vs port :4100
```

## Parity evidence

1. **Surface** — `make apidocs-validate` extracts the gateway alias map and
   diffs it against `typescript/kernel-srv/openapi.yaml`:
   `OK moleculer/kernel: 14 endpoints (contract typescript/kernel-srv)`.
   Negative-tested: renaming an alias fails with the exact missing/extra pair;
   `--update` refuses to regenerate mirrored ports.
2. **Behavior** — `tools/canary-diff.py`: **25/25 byte-identical**, covering
   every read path with live rows (transition get/causality, receipt chain,
   plan receipts, aggregates, policy active/maturity, recent-events incl.
   garbage/over-max limit clamps, receipt-integrity), every validation 400
   (uuid gates, required-field ladders), the PG-45000→403 class via enum-cast
   500 envelope parity, the gateway 404 (Express HTML, byte-for-byte), and the
   SSE ready frame. Write-path POSTs are intentionally NOT fired with real
   payloads — they would mutate the shared kernel schema. A bounded
   write-path canary (synthetic transition + receipt + cleanup) is a listed
   cutover prerequisite, as on cascade.
3. **Unit** — jest 10/10: uuid gates, limit clamp arithmetic, 45000→403
   mapping, required-field ordering, notify listener lifecycle (connect /
   LISTEN / notification fan-out / reconnect-on-failure / stop).

## Deliberate parity quirks preserved

- **500 envelopes carry `code`** (`KERNEL_WRITE_FAILED`, `RECEIPT_ISSUE_FAILED`,
  …); 4xx envelopes do not — exactly like the incumbent's helpers.
- **Unmatched routes return Express's default HTML error page**
  (`<!DOCTYPE html>… Cannot GET /path…`), byte-for-byte. moleculer-web's
  built-in `NotFoundError` (name `"NotFoundError"`) is detected inside route
  `onError` (moleculer-web's `send404` dispatches there) and the path is
  reconstructed as `req.$route.path + req.url` because the gateway strips the
  route prefix before dispatch. Action-level 404s (MoleculerError) stay JSON.
- **`subscribers` in /health** counts only THIS process's SSE clients; the
  incumbent's count includes its own live subscribers. Not a behavioral
  divergence — a live gauge, compared as shape not value.
- **SSE headers** are set via `ctx.meta.$responseHeaders` (moleculer-web's
  mechanism) instead of Express's `res.writeHead`; the wire bytes are the same.
- **Analytics-free but enum-cast-aware**: the kernel write paths cast
  `event_type` to a PG enum; garbage values 500 with the cast message — the
  canary pins that envelope rather than "fixing" it into a 400.

## Cutover prerequisites (not done here)

1. Owner ruling on cutover order (architect lane; Q1 lane-split).
2. Bounded write-path canary (synthetic transition/receipt with cleanup).
3. Caller inventory: kernel-srv has near-zero in-tree HTTP callers (conduit
   uses the same pg_notify channel directly); confirm no out-of-tree callers
   before retiring :8100.
4. systemd unit + manifest row (as with cascade).
