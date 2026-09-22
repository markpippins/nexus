# resolution-srv

REST API over the `resolution.*` Postgres schema — the canonical store for
identity, lineage, disposition, evaluation joins, receipts, and admission
(Lilac / Consolidation Wave 1 direction).

Parallel to `semantics-srv` (:3160), which serves `semantics.*`. This service
serves `resolution.*` on **port 3171**.

## v1 scope: READ-ONLY

`resolution.*` deliberately has no generic `add_/update_/soft_delete_`
stored-proc surface: writes are producer-authorized paths (SOLScript
evaluation, receipts, admission receipts) with their own idempotency
contracts. Exposing generic writes over HTTP before a per-table
authorization decision would create a bypass around those contracts.

v1 therefore ships a read surface only. Any POST/PATCH/DELETE under `/api`
returns `405 read_only` with the policy explanation. Per-table write
exposure is gated on the authorization discussion in To Do `6b3ca700`.

## Routes

```
GET /health              DB connectivity + required-table presence
GET /api/meta            registry overview: table, group, active/total counts
GET /api/<table>         active rows (?limit=100&offset=0, max 1000)
GET /api/<table>/:id     single row by PK (uuid)
```

Tables are registry-driven (`src/tables.ts`), grouped by governance concern:

- **registry** — semantic types, concepts, attributes, state transitions, frame dimensions
- **entity_proposition** — entities, propositions, assertions, frame values, evaluations, observations
- **reasoning** — rules, expressions, representations
- **outcome** — execution claims/evidence, receipts, governance thresholds, enforcement posture
- **lineage** — producer registry, tickets, work requests, contracts, specs

## Position in the fleet

| Service | Port | Serves |
|---|---|---|
| `semantics-srv` | 3160 | `semantics.*` (type-level legend) |
| `resolution-srv` | 3171 | `resolution.*` (canonical governance store) |
| `sol_api` | 8111 | `sol` database (standalone cutover) |

`semantics-srv` currently reaches into `resolution.*` via a direct
INSERT/UPDATE shim for 4 retired ontology tables (concept,
concept_relationship, representation, representation_relationship). Those
tables are readable here; retiring the shim onto resolution-srv write routes
is part of the 6b3ca700 scope decision, not v1.

## Run

```bash
npm install
npm run dev            # tsx watch, port 3171
npm run build && npm start

# systemd (user units)
cp resolution-srv.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now resolution-srv
```

Environment: `RESOLUTION_PG_DSN` (or `NEXUS_PG_DSN`), `RESOLUTION_SRV_PORT`
(default 3171), `RESOLUTION_SRV_SERVICE_ID` (service-registry heartbeat id;
0 = disabled until registered).
