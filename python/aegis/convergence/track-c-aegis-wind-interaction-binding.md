# Track C — Aegis/Wind consume the interaction vocabulary (plan 8261645)

**Track:** C — ontology, Brainfood slices, Aegis/Wind consumption
**Package:** C3 (this binding) · depends on C1 (vocabulary) and C2 (slices)
**Owner:** architect (ratification) · **State:** binding published.

> **Non-authoritative by construction.** This document maps existing Aegis
> components and Wind workflow candidates onto
> `schemas/convergence/interaction-vocabulary.v1.json`. It creates no role, no
> state, no invariant, and no authority. PEB activation is untouched.

---

## 1. Why Aegis/Wind are consumers, not authorities

- **Aegis** holds a TLA+-style state-machine registry (`python/aegis/aegis.py`)
  and already *bridges* it into the resolution schema via
  `StateMachineToResolutionBridge.bridge_registry()` — which emits concepts,
  attributes, relationships, rules and state transitions. That bridge is a
  **projection**, not an authority: it describes a machine, it does not decide
  what the machine may do in production.
- **Wind** holds workflow definitions whose nodes carry `workRefs` (candidates,
  WRs, PRs — see `typespec/v1/wind-srv/typescript/models.tsp`). A Wind workflow
  is a plan of scheduling; it does not own candidate identity or WR state.

Therefore both consume the vocabulary **descriptively**. A vocabulary id in an
Aegis mapping or a Wind node is a *reference*, never a grant.

## 2. Aegis → vocabulary mapping (existing components only)

| Aegis component (`aegis.py`) | Vocabulary kind | Bridge output (`bridge_registry`) | Authority note |
|---|---|---|---|
| `StateMachineRegistry.id/name/version` | `actor` + `context` (machine identity) | main concept | identity is Aegis-owned; not an actor role |
| `StateDefinition` | `state` (`state.*`) | state concept | states do not become WRP states |
| `TransitionDefinition` (from→to) | `transition` | relationship + state transition | a machine edge is not a permitted WRP edge |
| `InvariantDefinition` | `invariant` (`invariant.*`) | rule | an Aegis invariant never overrides `schemas/protocol/wrp-state-machine.json` rules |
| `PropertyDefinition` | `rule` | — (declarative) | properties do not gate Conduit |
| `TemporalProperty` | `rule` (temporal) | — | TLC/TLA+ semantics stay with Aegis |
| `ConceptMapping` / `AttributeMapping` / `RelationshipMapping` | `context` (mapping scope) | concepts/attributes | mapping never renames a governed key |
| execution log | `evidence` | — | evidence is descriptive |

**Mapping rule (frozen):** a mapping is by *name*, one-way, and total — if a
registry component has no vocabulary kind, it stays unmapped and is listed as
`unmapped`, never coerced into the nearest kind.

## 3. Wind → vocabulary mapping

| Wind surface | Vocabulary kind | Rule |
|---|---|---|
| workflow node `workRefs` | `context` | refs are identities; a node may not assert a state it cannot observe |
| node/edge definition | `transition` (plan-level) | a Wind edge orders work; it does not permit a Conduit state change |
| office / title / v-role (`typespec/v1/wind-srv`) | `actor` reference | organisational staging, bound not collapsed (per the models.tsp comment) |
| task outcome / receipt | `evidence` | outcomes are evidence; merge authority stays with the Reviewer |

## 4. Brainfood slices (C2) consumption

A slice may carry an `interactions: [<vocabulary id>...]` array. That array is a
**citation set**: it says which vocabulary entries the slice is about. It does
not assert that those interactions occurred; that assertion belongs to the
trigger/evidence rows in `resolution.keychain_event_outbox` (Track D).

The slice contract (identity, source refs, fingerprints, valid/recorded time,
explicit status, bounds) is frozen in
`schemas/convergence/brainfood-slice.v1.json`.

## 5. What this binding must never do

1. Add, rename or deprecate a vocabulary entry (that is C1 + architect).
2. Let an Aegis invariant become a Conduit gate, or a Wind edge become a
   permitted WRP transition.
3. Change PEB activation, admission rules or deny-contract-promotion behaviour.
4. Write a slice to a governed table.
5. Treat the Aegis→resolution bridge as a source of authority for
   `resolution.*` rows.

## 6. Evidence slots

| Slot | Value |
|---|---|
| `observed` | static read of `python/aegis/aegis.py` (classes at lines 11–157, bridge at 439+, mapping loops at 473–520) and `typespec/v1/wind-srv/typescript/models.tsp` (`workRefs`, line 148) |
| `artifact` | `python/aegis/convergence/track-c-aegis-wind-interaction-binding.md` |
| `timestamp` | 2026-11-10 |
| `verification_owner` | architect |
| `unfilled` | a live Aegis registry bridged end to end (would require a running aegis-srv and a populated registry; not attempted here) |

## 7. Stop condition

Stop after publishing the mapping. Implementing a consumer is a separate,
child-bounded change against `typespec/v1/aegis-srv` / `typespec/v1/wind-srv`,
and it must not be bundled into this decomposition.

Related: `schemas/convergence/README.md` §2 and §4,
`schemas/convergence/work-packages.v1.json` (C3).
