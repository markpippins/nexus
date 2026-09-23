# moleculer/semantics — twin of typescript/semantics-srv (:4160)

Moleculer port of `typescript/semantics-srv` (:3160) — REST over the
`semantics.*` PostgreSQL schema (the type-level semantic topology legend):
table-driven CRUD through stored procedures (`add_`/`update_`/
`soft_delete_<table>`), the T02 asset identity spine (canonical_asset
envelopes, revisions, identity claims, relations, cross-schema external
IDs), evidence filters, and the drift lifecycle.

## Parity contract

- `services/store.ts` + `services/tables.ts` + `services/handlers.ts` are
  verbatim ports of the incumbent's `db.ts` / `src/tables.ts` /
  `routes/semantics.ts` — same SQL, same envelopes, same status codes. The
  twin reads and writes the SAME database (nexus DB, `semantics` schema
  fully-qualified; `resolution.*` for the 4 ontology tables). The database
  is the arbiter (write-canary ruling).
- `services/api.service.ts` carries the LITERAL 92-entry alias map, checked
  against `typescript/semantics-srv/openapi.yaml` by
  `make apidocs-validate` (`check_drift.MOLECULER_MIRRORS`) — a renamed
  alias fails CI exactly like a renamed Express route.
- Override semantics (incumbent registration order) live inside the table
  actions: `canonical_asset.get` serves the envelope, `asset_revision.get`
  the revision envelope, `evidence_item.list` / `statement_evidence.list`
  the filters. `evidence_item` is immutable — no PATCH route (incumbent
  parity), enforced by a static test.

## Actions

Table CRUD (flat dotted names — Moleculer action schemas are FLAT; a nested
object under an action name is parsed as one action missing its handler,
SERVICE_SCHEMA_ERROR at boot):

    semantics.<table>.list | .get | .create | .update | .remove

Fixed routes under `semantics.x.*` (health, meta, sub-resources, lifecycle
transitions) — see `services/semantics.service.ts`.

## Run

    npm run build                      # tsc → dist/
    # standalone canary (no mesh registration):
    SERVICE_PORT=4160 \
    SEMANTICS_PG_DSN="postgresql://…/nexus" \
    npx moleculer-runner --config dist/moleculer.config.standalone.js dist/services/*.service.js
    # mesh (NATS :4222, namespace "semantics"):
    npm start

## Verify

    npx jest                            # 51 hermetic tests (stubbed ./store)
    python3 tools/canary-diff.py        # 12 read/negative cases vs :3160
    # + the live-data envelope script in the PR description

The canary normalizes only genuinely volatile fields (health
port/pid/timestamp). Write routes are NOT exercised by canary-diff — the
write-canary protocol (marked synthetic rows, A/B against live state)
governs those separately.
