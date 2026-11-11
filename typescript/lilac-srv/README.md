# lilac-srv

The single typed, idempotent write surface for Lilac Resolution persistence:
ticket lifecycle, receipt issuance, claim/evidence linkage, timeline and parity
diagnostics.

**Staged-inert.** No live caller is repointed at this service. It writes only
canonical `resolution.*` tables and changes no behaviour by existing.

## Why it exists

Conduit already knew how to write the canonical Resolution tables
(`python/conduit/lilac.py`). What did not exist was one place where the
*protocol* lived: the provenance envelope, the idempotency key, the outcome
vocabulary and the refusal codes. Every caller had to re-derive them. This
service owns them once.

## Contract

Every mutation carries a provenance envelope:

| field | meaning |
|---|---|
| `source_implementation` | repo-relative file that performed the mutation |
| `runtime_route` | `"<METHOD> <path>"` the mutation arrived on |
| `contract_version` | `1` |
| `source_identity` | stable identity of the writer |
| `idempotency_key` | unique per mutation, never reusable for a different target |

A missing, incomplete or ambiguous envelope is refused **before any statement
runs** — `PROVENANCE_MISSING`, `PROVENANCE_INCOMPLETE`, `PROVENANCE_AMBIGUOUS`,
`CONTRACT_VERSION_UNSUPPORTED` — and nothing is written.

### Outcomes

| outcome | meaning |
|---|---|
| `accepted` | new mutation, one row per surface |
| `duplicate-equivalent` | identical replay of an existing identity |
| `conflict` | identity reused with a different payload — **refused**, both fingerprints recorded |
| `refused` | envelope or request invalid; nothing written |

Idempotency is enforced by the database's natural unique indexes, not by
in-process memory, so a replay classifies the same way after a restart or on a
second replica:

| surface | identity |
|---|---|
| receipt | `(source_system, source_receipt_id)` |
| ticket | `(workflow_ref, role, position, generation)` |
| ticket_transition | `(ticket_id, from_status, to_status, input_receipt_id, fanout_policy_version)` |
| fanout_transition | `(input_receipt_id, kind, fan_out_policy_version)` |

A fan-out conflict is persisted on the ledger row (`conflict_fingerprints`)
because that table has a home for the pair; the other three surfaces carry both
fingerprints in the typed refusal body.

## Endpoints

Documented in `openapi.yaml`, and the server is built from the same table the
contract test compares against, so a route cannot exist on one side only.

```
GET  /health
GET  /v1/meta            service identity, contract version, staging posture
POST /v1/receipts
POST /v1/tickets
POST /v1/tickets/{ticket_id}/transitions
POST /v1/fanouts
GET  /v1/tickets/{ticket_id}/timeline
GET  /v1/parity
```

## Boundaries

- **Writes only `resolution.*`** — receipts, tickets, transitions, the fan-out
  ledger, `mutation_provenance`, `parity_evidence`, `executable_topology`. A
  static guard parses every statement and fails on anything else.
- **No admission route.** PEB owns admission.
- **No workflow-definition route.** Wind owns workflow definitions.
- **No caller repointing switch.** The service never decides who talks to it;
  that decision belongs to the caller, and conduit's default path is pinned by
  its own guard.

## Run

```bash
npm install
npm run dev        # tsx watch, port 3172
npm test           # hermetic: no database required
npm run typecheck
```

`LILAC_PG_DSN` (falls back to `RESOLUTION_PG_DSN`, then `NEXUS_PG_DSN`).

## Tests, and what each layer actually proves

`npm test` drives the repository through an in-memory stand-in with real
rollback semantics, so it proves the **classification logic**: per-surface
idempotency, conflict refusal, and that a refusal writes nothing.

`bin/tests/test_lilac_srv_schema_contract.py` (db tier, `guards-db`) closes the
gap that a stand-in cannot: it clones the production `resolution` shape into a
throwaway database, applies V215, and asks PostgreSQL to `PREPARE` each
statement the repository issues — so a renamed column or a wrong parameter
count fails there rather than in production — then executes a duplicate receipt
insert and asserts the real unique index returns `INSERT 0 0`.

`python/conduit/tests/test_lilac_service_contract.py` guards the proxy: no SQL,
every mutation delegates to `LilacClient`, the surface is not mounted without
`CONDUIT_LILAC_URL`, and no admission or workflow-definition authority appears.
