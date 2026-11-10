# Parallel convergence tracks — decomposition blueprint (plan 8261645)

**Plan:** 8261645 — *Parallel convergence tracks: Moleculer, Night Shift, ontology, and Resolution*
**WorkRequest:** `wr-8261645-1794345779-9cd52843` (derived from 8261645)
**State:** decomposition blueprint complete. **No runtime claim, no state mutation.**
**Owner of this document:** builder · **Vocabulary/contract ratification:** architect
**Review:** reviewer · **Tracks cited as continuity:** see §2

This blueprint decomposes the current Nexus convergence into parallel, auditable
work packages. It is deliberately **non-authoritative**: every package stops at a
package-level boundary and hands the system-level outcome to a child execution
WorkRequest. It does not create receipts, does not start or stop services, does
not seed the knowledge graph, does not edit the port registry, and does not
change PEB activation.

Artifacts are additive. No historical plan artifact, receipt, audit record or
`nexus/audit/CONDUIT_DATA/` mirror is deleted or rewritten by this decomposition.

---

## 0. Boundary — what this is, and what it is not

**Is:** four continuity tracks (A Moleculer, B Night Shift, C ontology/Brainfood,
D Resolution/Keychains/KG projection), a work-package registry in which *every*
package has an owner, dependencies, evidence outputs and a non-authoritative
stop condition (`work-packages.v1.json`), a versioned interaction vocabulary
(`interaction-vocabulary.v1.json`), a bounded Brainfood slice contract
(`brainfood-slice.v1.json`), a Resolution JSONB classification
(`resolution-jsonb-classification.v1.json`), and a frozen KG projection contract
(`typespec/convergence/kg-projection-contract-freeze.v1.json`).

**Is not:** a new convergence wave. Nothing here restarts Moleculer, Lilac,
Conduit, Night Shift, Shrapnel or Resolution work that is already in flight. The
tracks exist to make in-flight work *legible and auditable in parallel*, not to
re-plan it.

**Authority rule for the whole decomposition:** a document may *describe* and
*classify*; only the owning subsystem may *change* governed state. Section §4 is
the closed ownership set. **PEB activation is out of scope for every package.**

---

## 1. Continuity principle (why these are tracks, not waves)

A track is a citation surface over work that already exists. Each track names the
artifacts it stands on, so a reader can audit the claim without re-running
anything:

| Track | Stands on (existing, unchanged) |
|---|---|
| A — Moleculer | `moleculer/ports.yaml` (single source of truth, 22 declared ports), `moleculer/PORT-MAP.md` (generated mirror, architect Ruling 4 / decision `8ae4761b`), `moleculer/M1-SERVICE-MANIFEST.md`, `moleculer/M1-TRAFFIC-CANARY.md` |
| B — Night Shift | `docs/night-shift-doctrine.md` (approved 2026-09-05), conduit ticket/receipt pipeline (`typescript/conduit-mcp`), `python/conduit` plan state machine |
| C — ontology / Brainfood / Aegis-Wind | `schemas/ontology/` (JSON-LD vocabulary), `schemas/protocol/wrp-state-machine.json` (11-state machine), `python/aegis/aegis.py` (TLA+ registry), `typespec/v1/wind-srv`, `typespec/v1/aegis-srv` |
| D — Resolution / Keychains / KG projection | `schemas/migrations/resolution/` (canonical `resolution` schema, incl. v38 Lilac wave 1), `python/keychains/trigger.py` (shared trigger contract, catalogue `332d6831`), `moleculer/nexus-broker/services/keychain-snapshot.service.ts`, `schemas/decision-b-freeze/` (Decision B v2 freeze package), `typescript/shrapnel/` (EAV catalog), `typespec/v1/resolution-srv` |

In-flight plans that this decomposition **must not duplicate** (they are the
execution carriers; the tracks only reference them): `8261655`, `8261665` (W-C
consumer migration, active), `8261657` (peb-srv twin :4111), `8261658` (aegis-srv
twin :4116), `8261659` (nebula-srv canary twin :4101), `8261639` (Lilac wave 1,
blocked), `8261661` (shrapnel 0009 tombstone verb).

---

## 2. Tracks and packages

| Package | Track | Owner | Deps | Evidence output | Stop condition (non-authoritative) |
|---|---|---|---|---|---|
| **A1** twin-port continuity register | A | builder | — | `moleculer/convergence/track-a-moleculer-continuity.md` | register published; **no** edit to `ports.yaml`, no service started |
| **A2** canary promotion ladder continuity | A | planner | A1 | plan refs 8261657/58/59 + canary-diff record | delegated to those plans; this package never merges/cuts over |
| **B1** bounded Night-Shift rehearsal harness | B | builder | doctrine + conduit pipeline | `python/conduit/convergence/track-b-night-shift-rehearsal.md` + transcript | rehearsal runs against an isolated test DB; **no** live receipt written |
| **B2** receipt/rollback proof for the rehearsal | B | reviewer | B1 | receipt-chain snapshot + rollback note | rollback demonstrated; stop before any merge/attestation decision |
| **C1** interaction vocabulary v1 | C | architect ratifies / builder produces | — | `schemas/convergence/interaction-vocabulary.v1.json` | vocabulary frozen at `1.0.0`; change requires version bump |
| **C2** Brainfood slice contract | C | builder | C1 | `schemas/convergence/brainfood-slice.v1.json` | slices are bounded JSON with identity/fingerprint/time/status; no slice is written to a governed table |
| **C3** Aegis/Wind vocabulary consumption binding | C | architect | C1 | `python/aegis/convergence/track-c-aegis-wind-interaction-binding.md` | mapping published; **no** authority created, PEB activation untouched |
| **D1** trigger-class → event binding map | D | builder | C1 | `keychain-snapshot.service.ts` binding block + registry entry | mapping documented; projection behaviour unchanged |
| **D2** Resolution JSONB classification | D | architect | D1 | `schemas/convergence/resolution-jsonb-classification.v1.json` | each governed key classified; **no** migration applied |
| **D3** KG projection contract freeze | D | architect | D2 | `typespec/convergence/kg-projection-contract-freeze.v1.json` + `typescript/shrapnel/convergence/kg-projection-ownership.md` | freeze published; **no** seeding performed |
| **D4** Vision trigger-evidence registration | D | builder | D1 | `python/vision/convergence/track-d-resolution-trigger-evidence.md` | the vision emitter is documented as evidence source only |

Machine-readable form: `work-packages.v1.json` (same package IDs). Any divergence
between this table and the JSON is a defect in the JSON consumer, not a licence
to pick one.

---

## 3. Evidence slots (what every package must leave behind)

A package is *closed* only when all four slots have content. Slots are text, not
claims of runtime truth:

| Slot | Meaning | Required shape |
|---|---|---|
| `observed` | what the owner actually read/ran | command or file path + result summary |
| `artifact` | the durable output | repo-relative path |
| `timestamp` | when the observation was made | ISO-8601 |
| `verification_owner` | who is allowed to confirm | one role from §4 |

An unfilled slot is reported as unfilled. It is never back-filled with intent.

---

## 4. Ownership matrix (closed set — no new authority)

| Surface | Owner | This decomposition may | This decomposition may not |
|---|---|---|---|
| `resolution.*` (authority, temporal columns, `canonical_asset`, `execution_claim`) | Resolution | classify JSONB, cite migrations | migrate, seed, rewrite governed keys |
| `shrapnel.*` (EAV catalog, `field_type` 6 JSONB) | Shrapnel | declare ownership + default class | promote shrapnel rows to authority |
| `aegis.*` (state-machine registry) | Aegis | map vocabulary, cite transitions | alter invariants/states |
| `semantics.*` (asset identity spine) | Semantics | cite `canonical_asset_id` | reassign identity |
| Keychains projection / checkpoints | Keychains | document trigger bindings | make projection authoritative |
| PEB (`peb.*`, activation, admission) | PEB | classify existing `peb.*` triggers | create activation, change deny/promotion rules |
| Conduit plans/tickets/receipts | Conduit | reference plan numbers | issue receipts, move plan state |
| Ontology vocabulary | Architect (ratifies) | propose v1 | silently extend a frozen vocabulary |
| Night-shift doctrine | Architect | rehearse inside its bounds | relax hermetic/prod-contact rules |

Closed authority vocabulary for envelopes is the ratified set already encoded in
`schemas/decision-b-freeze/jsonb_contract_test.py`:
`{resolution, shrapnel, aegis, semantics, keychains, peb, operator, conduit,
wind, vision}`. This decomposition adds **no** member.

---

## 5. Artifact index

| Path | Track | Purpose |
|---|---|---|
| `schemas/convergence/README.md` | all | this blueprint |
| `schemas/convergence/work-packages.v1.json` | all | package registry (owner/deps/evidence/stop) |
| `schemas/convergence/interaction-vocabulary.v1.json` | C | versioned actors/roles/states/transitions/rules/invariants/context |
| `schemas/convergence/brainfood-slice.v1.json` | C | bounded slice contract |
| `schemas/convergence/resolution-jsonb-classification.v1.json` | D | JSONB class per governed column |
| `moleculer/convergence/track-a-moleculer-continuity.md` | A | twin continuity register |
| `python/conduit/convergence/track-b-night-shift-rehearsal.md` | B | rehearsal bound + evidence plan |
| `python/aegis/convergence/track-c-aegis-wind-interaction-binding.md` | C | vocabulary consumption binding |
| `python/vision/convergence/track-d-resolution-trigger-evidence.md` | D | vision trigger emitter as evidence source |
| `typespec/convergence/kg-projection-contract-freeze.v1.json` | D | frozen sections/relations/provenance + seed gate |
| `typescript/shrapnel/convergence/kg-projection-ownership.md` | D | Shrapnel ownership + JSONB storage rule |
| `moleculer/nexus-broker/services/keychain-snapshot.service.ts` | D | trigger-class → event binding (comment-only) |

---

## 6. Stops and rollback (every package)

Every package rollback is **additive-only**: delete or supersede the artifact it
added, and nothing else. Because no package mutates governed state, there is no
data rollback path to design — the stop condition is "publish the artifact, then
stop". If a package is found to have mutated state, the escalation is a planner
BLOCK record, not a compensating write.

| Package | Rollback / stop action |
|---|---|
| A1/A2 | revert the markdown; port registry untouched, so no service state to unwind |
| B1/B2 | discard the isolated rehearsal DB; drop the branch; no receipt to retract |
| C1/C2 | supersede with a version bump; slices are derived artifacts, never canonical |
| C3 | revert the mapping doc; aegis/wind/PEB state untouched |
| D1/D4 | revert the comment/doc; projection behaviour identical |
| D2/D3 | freeze documents are declarative; no migration/seed executed, so nothing to undo |

---

## 7. Explicitly not claimed

- No claim that Moleculer twins are running: on 2026-11-10 the local port scan
  showed **0 of 22** declared ports bound (local `ss -ltn` filtered to the
  declared ports; consistent with `docs/system-pulse-revised-2026-11-10.md`,
  which reported 0 bound on an earlier port count).
  Track A is a register, not a bring-up.
- No claim that Conduit `/state` and Nebula projections agree. That is W-B
  (8261654) territory.
- No claim that Night Shift has been rehearsed yet. B1/B2 define the bounded
  rehearsal and its evidence slots; the slots are currently **unfilled**.
- No claim that the KG projection has been seeded. D3 publishes the freeze that
  must precede seeding.
