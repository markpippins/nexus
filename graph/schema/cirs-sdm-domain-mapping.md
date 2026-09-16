# CIRS Rule Families → SDM Domain Mapping

> **Purpose:** Closes open item (b) from the governance & identity stack thread
> (ded5b0de): *"Map the un-enumerated CIRS rule families (IR/CORE/AUD/CAUSAL)
> into the SDM domain table for the data-level enforcement pass."*
>
> **Sources:** `audit/SPECS/cognitive-integrity-rule-system.md` (800 lines,
> 11 rule families, 4 axioms, 3 hard boundaries) and
> `tools/arl/classification.py` (the CIR-SDM artifact classifier with 8
> domains).
>
> **Status:** mapping only — no enforcement code. Feeds the architect's
> action item 3 (data-level CIR-SDM enforcement over the CER event log),
> which remains sequenced after item 1 (now delivered: entity_key emission).

## The SDM domain table (from ARL, `tools/arl/classification.py`)

| Domain | Meaning |
|---|---|
| `SCHEMA` | Schema/contract files |
| `CONFIG` | Configuration (e.g. pipeline-mode.json) |
| `LEDGER` | Append-only ledgers (e.g. transition_ledger.json) |
| `STATE_MACHINE` | State-machine definitions (pgv.state_machine.json) |
| `CODE` | Implementation |
| `METADATA` | Epistemic artifacts (md/yaml/yml/toml) |
| `DATA` | Vectors/goldens/testdata |
| `BUILD` | Build output (node_modules, __pycache__, .git) |

## Rule-family → domain mapping (full CIRS inventory, spec §5–§15)

| Family | Rules (spec §) | Governs | Primary SDM domain(s) | Data-level check over CER log |
|---|---|---|---|---|
| **IR** (ProjectionIR Integrity) | IR-01..IR-10 + IR-META-01 (§5) | Epistemic artifacts: no authority, no escalation, ephemerality, execution isolation | `METADATA`, `DATA` | CER must never carry IR payloads; IR-derived nodes never in execution edges |
| **CORE** (Cross-Domain Separation) | CORE (§6) | Synthesis↔Execution separation | `CODE`, `METADATA`, `DATA` (all) | Cross-domain edges in CER graph flagged (ARL already computes INTRA/INTER_DOMAIN edge classes) |
| **AUD** (Audit Non-Influence) | AUD-01..03 (§7) | Audit must not influence outcome; no reverse projection | `LEDGER`, `STATE_MACHINE` | CER audit records append-only; no CER → state mutation |
| **CAUSAL** (Causal Integrity) | CAUSAL-CORE, -01, -02 (§8) | Parent-requirement rule, no upstream injection | `LEDGER`, `DATA` | CER `parent_event_ids` must resolve; no causal edges that inject upstream |
| **VEL** (Verification Execution Ledger) | VEL-01..CORE (§9) | Ledger append-only, non-influence | `LEDGER` | CER event log append-only (directly maps to conduit.work_request_events) |
| **MED** (Merkle Integrity) | MED-01 (Projection) | Cryptographic non-influence | `LEDGER`, `DATA` | entity_key/hash binding (wr-conf-010 guards the derivation) |
| **SPoE** (Proof Integrity) | SPoE-01 (§11) | Proof non-influence | `DATA` | Proof payloads in CER have no execution authority |
| **PAL** (Proof Access Layer) | PAL-01 (§12) | Query non-influence | `CODE`, `DATA` | Query path cannot write CER |
| **CTS** (Causal Type System) | CTS-01 (§13) | Query non-epistemic | `SCHEMA`, `STATE_MACHINE` | WRP transition legality (already guarded by cross-language contract + wr-conf-001) |
| **SYN/PLN/EXE** (Pipeline Stage Integrity) | SYN-01, PLN-01, EXE-01 (§14) | Synthesis purity, plan IR-free, execution purity | `STATE_MACHINE`, `CONFIG`, `CODE` | CER per-stage boundaries: WR admission carries execution contract only |
| **BOOT** (Bootstrap Integrity) | BOOT-01..02 (§15) | Bootstrap authority, no self-modifying bootstrap | `CONFIG`, `CODE` | Not CER-expressible — config/fs-level |

## How this maps to the three hard boundaries (§16)

| Boundary | CIRS rules | SDM domains touched | CER/logic expression |
|---|---|---|---|
| Observation → Operator | axioms 1–2 | `DATA` → `METADATA` | Observation rows immutable; operator derives projections |
| Synthesis → WorkRequest | IR-01, IR-09, SYN-01 | `METADATA` → `STATE_MACHINE` | Only WR nodes carry execution contracts |
| WorkRequest → Conduit Execution | IR-05, IR-09 | `STATE_MACHINE` → `CODE` | WR admission → conduit.work_request_events fold |

## Enforcement priority (aligned with architect sequence)

1. **`LEDGER`-domain rules first** (VEL, AUD, MED) — the CER event log gives
   them a substrate today: append-only (VEL-01), audit non-influence (AUD-01..03),
   hash binding (MED-01 via entity_key).
2. **`STATE_MACHINE`-domain rules** (CAUSAL, CTS, SYN/PLN/EXE) — transition
   legality already guarded; extend with causal-parent resolution over the log.
3. **`CONFIG`/`CODE`/`BUILD`-domain rules** (CORE, BOOT, PAL) — schema/file
   level, ARL's existing artifact classification.

**Caveat for future cards:** the SDM domains are a *file-level* classification
today. Data-level enforcement needs the CER event log as the runtime substrate
(action item 3) — this mapping is the bridge between the two, not a claim that
either exists yet.
