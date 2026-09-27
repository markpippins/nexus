# Port Receipt Template

Required for any work that implements a contract in a new tier. The port's deliverable is a
**defect report on the source implementation**, not a service that replaces it. See
`ARCHITECTURE.md` §8.3.

Record the receipt as a `nebula_create_agent_record` (database-first — never write a `.md`
file into `audit/` as the persistence path).

---

## 1. Identity

| Field | Value |
|---|---|
| Contract | `typespec/v1/<service>/<language>/` |
| Source implementation (X) | path, tier, port |
| Probe implementation (Y) | path, tier, port |
| Merge / commit | SHA — **never a PR body** |
| Receipt type | `r-port-receipt` |

## 2. Independence argument — answer before claiming conformance

A port that surfaces zero discrepancies has **failed**: it means Y was too similar to X to be
an independent probe. Complete this section *first*.

- [ ] Y was written against the **contract**, not transliterated from X's source
- [ ] Y's language/runtime differs in a way that could surface real disagreement
- [ ] Y's test suite asserts on **observable behaviour**, not on X's internals
- [ ] If 0 discrepancies were found, the reason is stated here and the receipt is escalated
      to the architect rather than filed as conformant

## 3. Falsification count

Discrepancies found, and the adjudication for each. **Zero is not an acceptable result
without section 2.**

| # | Discrepancy | Kind | Adjudication | Owner | Fix |
|---|---|---|---|---|---|
| 1 | | contract / implementation / latent bug | fix forward / document / escalate | | |

**Kinds:** `contract` (the contract was wrong) · `implementation` (X diverged from contract) ·
`latent-bug` (X has a real defect Y exposed) · `untrue-assumption` (documented belief,
disproved) · `dead-code` (unreferenced path found in another team's script)

A `latent-bug` finding is the highest-value outcome. Record it as a result, not an
embarrassment — the contract being load-bearing for the first time is the point.

## 4. Untrue assumptions

Assumptions the port disproved. These propagate as **contract** changes per §8.4, not as
implementation patches.

| Assumption | Source | Disproved by | Correction lands in |
|---|---|---|---|
| | | | |

## 5. Contract changes

| Change | Propagates to | Breaking? |
|---|---|---|
| | contract / sibling impls / legacy impl | yes/no |

A correction to the contract requires the same scrutiny as any breaking change, and the
per-implementation TypeSpec directory is added beside the existing one — never in place of it
(§8.4).

## 6. Disposition of the source implementation — required

The previous framing is not optional. Record both axes explicitly (§8.2):

| Axis | Value |
|---|---|
| Liveness of X | `live` (default) / `legacy` (requires explicit move) |
| Host affinity of X on this host | `preferred` / `not-preferred` |
| Running on another host? | yes (where) / no / unknown |

A port does **not** retire, deprecate, or delete X. "Soon to be retired" means "soon to stop
running on *this* host." If you cannot state X's disposition, the port is not complete.

## 7. Verification

- [ ] Y builds/typechecks clean
- [ ] Tests pass, and a **negative** case proves the suite can fail
- [ ] Suite runs in CI, not only under local attestation
- [ ] Route/surface inventory is 1:1 against the **merged** tree, not the PR description
- [ ] Tester's attestation recorded (engineer self-attestation is not sufficient)
