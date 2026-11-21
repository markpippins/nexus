# Inventory — `argon-ten-turns.md` reconciled against completed work

Static reconciliation at `f78443b45` (branch `req-port-p4a`), 2026-11-21.
Read-only: no database was contacted, no migration was applied, and
`sql/V222__port_requirements_to_resolution.sql` was not modified.

## Verdict

`argon-ten-turns.md` contains **no proposal or directive that names V222, P4a,
or the requirements port**. The relation appears twice in 877 lines: once in a
list of controllers a future JVM distribution would need (`argon-ten-turns.md:623`)
and once as an executable ontology representation (`:679`). P4a is therefore not
*directed* by this document; it is downstream of the one directive the file does
carry that touches its substrate — apply `V221` (T5, `:446`–`:450`) — and is
governed by the standing R9 replication rule (`:458`–`:460`).

So the honest reconciliation is: **0 requirements-port items directed, 8
port-adjacent items (2 done — one of them the operator-gate doctrine — 4 partial,
2 not started), and 75 items belonging to other workstreams**, enumerated in §5 so the boundary is
explicit. One of the port-adjacent findings is a material contradiction between
`V221` and `V222` (§4) that neither the runbook nor the previous consumer
inventory caught.

## 1. What the file actually is

- 877 lines, eight analyst turn records (the filename says ten; only eight turn
  bodies with their R1/R2/R14/R17 audit blocks are present).
- T1 `:1`–`:112` — reconciliation of PRs `#959`–`#989` and self-corrections.
- T2 `:113`–`:208` — Gestalt: pressure aggregation doctrine.
- T3 `:209`–`:267` — Ballerina / `service-broker` / Git-CI history.
- T4 `:268`–`:417` — `nebula-ssf.md`, `atlas-ui`, `throttler-ui` absorption.
- T5 `:418`–`:512` — ground truth 2026-11-16 and five ranked next steps.
- T6 `:513`–`:654` — `jvm/spring` inventory and tiered gap analysis.
- T7 `:655`–`:790` — `nexus-core` as an executable-ontology runtime.
- T8 `:791`–`:877` — GitHub branch accounting and cleanup.
- Every turn is a report *about* work done elsewhere, with directives embedded in
  its "what remains" / "next steps" / "recommended" sections. Almost nothing in
  it is a specification for code in this worktree.

## 2. Method and status vocabulary

Each numbered step, bulleted proposal, named mechanism and "next step" was
extracted and given an ID. Where a section enumerates sub-modules of a single
directive, the sub-modules appear in the evidence cell rather than as separate
rows; items repeated across turns are listed once, at first occurrence, with all
citations.

Status values:

- **Done** — the deliverable exists in this worktree and I read it.
- **Partial** — some deliverable exists; the claimed state is not corroborated.
- **Not started** — no deliverable here, and none claimed to be in this branch.
- **Not assessable here** — completion depends on live systems, another repo, or
  a submodule that is not checked out. Recorded so the item is not silently
  dropped.
- **Asserted only** — the file states a fact or decision but the worktree carries
  no artifact that can confirm it.

"Port-adjacent" means the item names the requirement relation, the migrations
immediately upstream of it (`V220`, `V221`, `V187`), the executable
`Requirement` representation, or replication of P4a schema. Everything else is
out of scope by construction.

## 3. Port-adjacent items

| ID | Turn:line | Proposal / directive | Status | Evidence | Demonstrated here? |
|---|---|---|---|---|---|
| P1 | T5 `:449`–`:461` | Re-attest and merge PR `#1017` at `49405e09d`, then apply `V221` live (drop `resolution.work_request.source_requirement_id`, satisfy AC11) | Partial | Migration present: `sql/V221__ac11_drop_source_requirement_shortcut.sql` (drop at `:104`, absence postcondition at `:247`). Consumer repoint is demonstrably in the tree: `typescript/nebula-srv/src/routes.ts:4494` and `moleculer/nebula/src/routes.ts:3947` join `nebula.v_work_request_requirement`. The live apply itself is not evidenced anywhere in the worktree. | Artifact + repoint yes; live apply **asserted only** |
| P2 | T5 `:452`–`:455` | Close the `V187` ledger gap: insert the `V187` row into `resolution.migration_ledger` | Partial | `sql/V187__work_request_view_demotion.sql` present, but its header still reads `STAGED INERT · NOT APPLIED TO LIVE` (`:3`) with four apply gates (`:6`–`:12`), while T5 `:427`–`:430` asserts `V187` is live. The ledger row is a database fact this worktree cannot show. | File yes; header **contradicts** the live claim; ledger **asserted only** |
| P3 | T5 `:427`–`:430` | `V220` (`request_requirement_link`, `artifact`, `request_artifact_link`) is live and is the substrate P4a builds on | Partial | `sql/V220__work_request_requirement_and_artifact_links.sql` present; `resolution.request_requirement_link` is consumed by V222 (`sql/V222…:706`–`:708`) and by the `V221` view (`sql/V221…:73`–`:79`). Live status unverifiable here. | Artifact yes; live **asserted only** |
| P4 | T1 `:65`–`:67` | Ratify the 46-row ledger and Authority Model B: `nebula.implementation_plans` authoritative, `resolution.work_request` a derived read model, dispositions append-only, no `business_status` mutation | Done | `docs/workrequest-disposition-amendment.md:3` (ratified) and `:7` (model B, read-only verifier, admin-gated DDL); `bin/verify-workrequest-disposition.py` present. | Yes (document + verifier). Live DDL/backfill remain admin-gated, as the document says |
| P5 | T7 `:679` | `Requirement → resolution.requirement` is one of six `executable` representations (`V217`), i.e. the relation is an ontology-bound surface, not just a legacy table | Partial | `sql/V216__nebula_sol_concept_registry.sql:1` and `sql/V217__representation_scoped_bindings.sql:1` both declare themselves *staged inert*; T5 `:427`–`:429` asserts `V216`/`V217` live. No live check performed. | Files yes; "6 executable live" **asserted only** |
| P6 | T5 `:458`–`:460`, T1 `:93` | R9: decide whether the migrated schema (`V215`, `V219`, `V220`, `V187`, `V221`) is replicated to `vanadium` | Not started | R9 still requires an explicit operator ask; no decision is recorded here. The list predates P4a, so the V222 objects are now enumerated separately in the runbook §12 checklist (`requirement_port_ledger`, `requirement.work_request_dco`, the guard functions/trigger, the projection and its write-through trigger), so the decision covers them. | n/a — decision still open; scope now complete |
| P7 | T6 `:620`–`:623` | A standalone JVM distribution needs `nebula.requirements` controllers/repositories (listed with agent records, inbox pointers, SCD4 hierarchy, harvest candidates) | Not started | No JVM `nebula` service exists; `jvm/spring/nexus-core/nexus-core-solscript/.../SolScriptConfig.java:16` still returns `new InMemorySolStorage()`. | Yes (absence is demonstrable) |
| P8 | T1 `:93` | Operator-gated live SQL application doctrine: migrations merge and test on throwaway databases, then wait for explicit operator authorization | Done (doctrine honoured) | `sql/V222…:3` still reads `STAGED · NOT APPLIED TO LIVE`; `git status` shows the file untouched; the runbook keeps the gate. | Yes |

### P4a in one sentence

The port is compatible with P4 (`resolution.requirement` is the canonical
requirement table the ontology binds, `Requirement` is `executable` per P5) and
with P1/P3 (it consumes `resolution.request_requirement_link`), but it is
**not** compatible with P1's `V221` drop as the code stands — see §4.

## 4. Material contradiction: `V221` removes the column `V222`'s delete guard reads

This is the one place where the ten turns and this worktree's P4a code cannot
both be right.

- `sql/V222__port_requirements_to_resolution.sql:712`–`:716` makes a live work
  request a delete blocker:
  ```sql
  SELECT jsonb_agg(w.id::text ORDER BY w.id) INTO v_ids
    FROM resolution.work_request w
   WHERE w.source_requirement_id = p_id;
  ```
- `sql/V221__ac11_drop_source_requirement_shortcut.sql:95`–`:104` drops exactly
  that column, and `:245`–`:247` raises `V221-POST-001` if it still exists.
- T5 `:449`–`:461` directs that `V221` be applied. So on the plan of record the
  column is gone before `V222` runs.
- The V222 test fixture never applies `V221`; it recreates the pre-V221 shape on
  purpose — `bin/tests/test_v222_requirements_port.py:221`–`:226`
  (`source_requirement_id uuid …`, comment "the single-source shortcut V220
  supersedes but does not drop yet") — and exercises it at `:1109`. The suite
  therefore cannot catch the conflict.

**Consequence.** `CREATE FUNCTION` succeeds (plpgsql resolves table and column
references at first execution, not at creation), so V222's postconditions pass
and the apply looks clean. The first requirement delete, cascade pre-check
(`BLOCKERS_SQL` in `requirement-delete.ts`) or `resolution.delete_requirements()`
call then raises `column w.source_requirement_id does not exist` — a 500, not the
intended `P4A01` refusal — for every delete, not only conflicting ones.

**Resolved 2026-11-21 (operator-approved).** V222's guard now reads
`resolution.request_requirement_link` where `relation = 'source'` (mirroring
`V221`'s own view, `sql/V221…:73`–`:79`) and keeps the legacy column visible
through `to_jsonb`, so the function is valid on both sides of V221.
`test_delete_guard_is_v221_safe` drops the column before applying the port and
proves both the `P4A01` refusal and a clean unreferenced delete; the full module
passes 40/40. The delete-guard behaviour is therefore demonstrated on the schema
V221 leaves behind, not only on the pre-V221 fixture.

## 5. Out-of-scope inventory

These are proposals and directives in the file that are **not about the
requirements port**. Each is recorded so the scope boundary is explicit, with
the status of its deliverable *in this worktree*.

### T1 — PR reconciliation, candidates, recovery (`:1`–`:112`)

| ID | Cite | Item | Status here | Evidence |
|---|---|---|---|---|
| G1 | `:54`–`:56` | Track A (`CPF ≥ 0.70` + `system_mapped`) shipped as an inert read-only evaluator | Done | `python/promotion-flow/candidate_track_a.py:5` (READ ONLY transaction), `:33`, `:52` |
| G2 | `:57`–`:62` | Track B (`Delta × Gravity × Leverage`) formally deferred until deterministic inputs exist | Done (deferral honoured) | No Track B module in `python/promotion-flow`; only `candidate_track_a.py`, `stage0_map.py`, `promotion_gate.py` |
| G3 | `:88`–`:97` | `V215`–`V218` merged, tested, awaiting operator live apply | Partial | `sql/V216`–`V218` present; headers `:1` say "staged inert"; live claim at T5 `:427`–`:430` unverified |
| G4 | `:93`–`:94` | Close plans `8261650` and `8261668` (last two blocked/pending) | Not started | Live queue state; no artifact |
| G5 | `:95` | Worktree residue `workrequest-residue-holding-2026-11-10` (57 dirty paths) and the `structure.bridge → Expression/Aspects/SOLScript` wiring | Not started | Not in this worktree |
| G6 | `:96` | Candidate Gate 0/Gate 2 batch execution | Not started | `python/promotion-flow/stage0_map.py` present, unchanged |
| G7 | `:97` | Clear `sonar-sync :9096` and `execution-ui :4205` incidents before automated Sonar batching | Not assessable here | Live services; `bin/sonar-forum-sync.py` present |
| G8 | `:75`–`:80` | Correct the ISA narrative to live vocabulary (36 opcodes, `nebula.compilation_receipts` 0 rows) | Asserted only | Prose correction; no artifact |
| G9 | `:81`–`:84` | Decision B's 12-section `kg.*` schema is contract text, not deployed | Asserted only | Live schema fact |
| G10 | `:73` | Restore 14 lost `analysis` bodies in place | Not assessable here | Live database rows |
| G11 | `:58`–`:62` | Do not pollute the promotion gate with unverified heuristic scores (Track B constraint) | Done (constraint honoured) | Same as G2 |

### T2 — Gestalt doctrine (`:113`–`:208`)

| ID | Cite | Item | Status here | Evidence |
|---|---|---|---|---|
| GS1 | `:122`–`:127` | Gestalt = aggregated perception of unresolved divergence, in the Pressure layer, authority left with the source | Asserted only | Doctrine text |
| GS2 | `:129`–`:133` | Hard vs Soft pressure taxonomy | Asserted only | Doctrine text |
| GS3 | `:134`–`:140` | Four lifecycle quadrants (Compile / Push / Smell / Observe) | Asserted only | Doctrine text |
| GS4 | `:141`–`:147` | Event-sourced recurrence contours (Progress / Regression / Churn) and the tightening loop | Asserted only | Doctrine text |
| GS5 | `:174` | `gestalt.pressure_signals` schema | Not started | `rg` finds no such table in `sql/`, `schemas/` or `docs/`; the system card in the file says it was never migrated |
| GS6 | `:190` | Decision 27: raising a pressure baseline floor in the commit that trips it is void | Partial | `tools/api-docs/check_contract_pressure.py` and `tools/api-docs/contract-pressure-baseline.json` exist; the upward-move rule itself is policy, not enforced code |
| GS7 | `:181`–`:190` | Candidate-under-pressure dispositions (representation question / reconcile ontology / architectural candidate) | Not started | No implementation |
| GS8 | `:186` | Planner selects high-Gestalt-pressure, low-ripple clusters and emits WorkRequests | Not started | No reducer |

### T3 — Ballerina and service-broker (`:209`–`:267`)

| ID | Cite | Item | Status here | Evidence |
|---|---|---|---|---|
| B1 | `:231`–`:240` | Stop using Git/CI as a Ballerina exploration vehicle; keep CI plumbing in Python + Actions | Done | `ballerina/` last commit `2026-09-19 22403eabf` — exactly the boundary the file claims; `bin/merge_pr.py`, `bin/sonar-forum-sync.py`, `bin/jenkins-forum-sync.py` present |
| B2 | `:250`–`:256` | Ballerina becomes the JVM tier's ISA execution provider behind the ISA contract | Not started | No ISA/Ballerina wiring; `ballerina/` unchanged since the boundary |
| B3 | `:246`–`:248` | `service-broker` narrows to auth-provider adapter + service-discovery projection | Not assessable here | `jvm/spring/service-broker/broker-gateway` present; runtime role is a live fact |

### T4 — UI absorption into `throttler-ui` (`:268`–`:417`)

Important qualification: `angular/throttler-ui` is a **submodule** pinned at
`e54695a6a6455d1671e0c2ea36cbd7dcf1026f50` and is not checked out here, and
`angular/atlas-ui` has no history in this repository. The `file:///…` paths the
turn cites for `atlas-ui` and `throttler-ui/src/...` therefore cannot be
inspected. Statuses below mean "not started in this repository".

| ID | Cite | Item | Status here | Evidence |
|---|---|---|---|---|
| U1 | `:275`–`:292` | Five-layer inventory model (Concept / Representation / Asset / Instance Relation / Assessment) | Not started | Design only |
| U2 | `:284`–`:290` | Keep `nebula.systems` and `registry.systems` distinct; bridge via `semantics.canonical_asset` + `asset_relation` | Not started | Design only |
| U3 | `:290`–`:294` | Health as epistemic disagreement + freshness, not an averaged boolean | Not started | Design only |
| U4 | `:316`–`:323` | Mechanic 1: SOLScript containment lenses replacing `childrenMap` collapse/explode | Not started | `angular/atlas-ui` absent from history; `throttler-ui` submodule not initialised |
| U5 | `:325`–`:331` | Mechanic 2: Shift+Drag mutation guards through `SolScriptEvaluator` + `AuthorizedInvocationRequest` | Not started | Same |
| U6 | `:332`–`:341` | Mechanic 3: epistemic shaders (`CONSENSUS_LIVE`, `AUTHORITY_CONFLICT`, `STALE_OBSERVATION`, `UNBOUND_ORPHAN`) | Not started | Same |
| U7 | `:342`–`:349` | Mechanic 4: saved views as `ViewSpec` + `ReadSetSnapshot` | Not started | Same |
| U8 | `:352`–`:390` | Six widget families and the complete absorption map | Not started | Design only |
| U9 | `:391` | Retire `nebula-operations-ui` first | Not assessable here | Path absent from `HEAD` |
| U10 | `:392` | Absorb `nebula-control-plane` panels; `SystemsView` becomes `HierarchyNavigator & SsfTreeMutator` | Not started | `nebula-control-plane` is a submodule, not checked out |
| U11 | `:393` | Absorb `nebula-ui` `SystemInfoComponent` as `Scd4DocumentInspector` | Not started | `nebula-ui` is a submodule, not checked out |
| U12 | `:394`–`:398` | Port `barbie` tables into `UnifiedAssetCatalogTables` / `GestaltPressureTables` | Not started | `angular/barbie` present but not inspected |
| U13 | `:399` | Extract `atlas-ui` Three.js into `@nexus/atlas-webgl` | Not started | No such package in this worktree |
| U14 | `:648` | Throttler-UI work routed to `layout-mechanic` | Done (routing) | Inbox decision recorded; no artifact expected |

### T5 — Five ranked next steps (`:418`–`:512`)

| ID | Cite | Item | Status here | Evidence |
|---|---|---|---|---|
| Q1 | `:463`–`:466` | Land builder grant `builder-grant-v0-1` and author the reviewer grant | Not assessable here | Lives in separate worktrees, not this one |
| Q2 | `:467`–`:469` | Merge `wr-event-vocabulary` and `wr-ledger-ratification` | Not assessable here | Separate worktrees |
| Q3 | `:469` | Promote the first wave of the 19 compiled WorkRequests | Not started | Live queue |
| Q4 | `:472`–`:480` | Merge the high-value unmerged worktrees (governance, promotion, Mongo, UI fixes) | Not assessable here | Separate worktrees |
| Q5 | `:481` | Prune ~40 stale post-merge worktrees | Not assessable here | Worktree inventory, not repo content |
| Q6 | `:484`–`:486` | `pg_wal`/PITR deadline 2026-11-17 and `vanadium` backup reachability | Not assessable here | Live infrastructure |
| Q7 | `:487`–`:489` | Unblock `absorb` ingestion (three `absorb` worktrees) | Not assessable here | Separate worktrees; `bin/transcript_rebuild.py` present |
| Q8 | `:490`–`:492` | Reconcile 13 Terrain drift anomalies and complete `vision-srv` retirement | Not assessable here | Live infrastructure |
| Q9 | `:494`–`:496` | Replace string-similarity Stage-0 asset mapping with a deterministic reviewed binding pass | Not started | `python/promotion-flow/stage0_map.py` unchanged |
| Q10 | `:497`–`:498` | Resume the halted Stage-3 promotion gate | Not started | `python/promotion-flow/promotion_gate.py` present |
| Q11 | `:499` | Stand up the Planner SonarQube + Terrain → low-ripple WorkRequest reducer | Not started | No reducer |
| Q12 | `:500` | Bind the first upgraded widget (`AtlasTopologyCanvas` / `HierarchyNavigator`) to the 5-layer snapshot | Not started | Submodule not checked out |

### T6 — Standalone JVM distribution (`:513`–`:654`)

| ID | Cite | Item | Status here | Evidence |
|---|---|---|---|---|
| J1 | `:538`–`:547` | Tier I: wire `nexus-core-solscript` to PostgreSQL | Not started | `SolScriptConfig.java:16` returns `new InMemorySolStorage()` |
| J2 | `:548` | Tier I: give `nexus-core-wrp` JDBC/JPA + REST | Not started | `KernelEngine.java` present; no repositories |
| J3 | `:548` | Tier I: replace `nexus-core-broker` hardcoded 405s | Not started | `BrokerWriteController.java` present |
| J4 | `:549` | Tier I: wire `nexus-core-search` to the existing search-service | Not started | `SearchWriteController.java` present |
| J5 | `:550` | Tier I: complete `tackle-registry-service` | Not started | Present, read-only |
| J6 | `:552`–`:565` | Tier II plane 1: WorkRequest/Lilac runtime in JVM | Not started | No `nexus-core-conduit`/`lilac` module |
| J7 | `:566`–`:580` | Tier II plane 2: reactive event spine (LISTEN/NOTIFY + NATS) | Not started | No Spring listeners |
| J8 | `:581`–`:592` | Tier II plane 3: harness, blackboard, keychains, timeclock | Not started | Python owns it |
| J9 | `:593`–`:605` | Tier II plane 4: ingestion, `Expression`, promotion pipeline | Not started | Python owns it |
| J10 | `:606`–`:610` | Tier II plane 5: filesystem crawling, media, speech | Not started | Python owns it |
| J11 | `:624`–`:626` | Tier III: port `assembly` CRUD | Not started | No JVM assembly module |
| J12 | `:627`–`:628` | Tier III: port `resolution`/`semantics`/`knowledge`/`wind` CRUD | Not started | No such modules |
| J13 | `:632`–`:634` | Sequencing step 1: `JdbcSolStoragePort` | Not started | Same as J1 |
| J14 | `:635`–`:639` | Sequencing step 2: WRP persistence + REST | Not started | Same as J2 |
| J15 | `:640`–`:642` | Sequencing step 3: `nexus-core-nebula/assembly/semantics` | Not started | Includes P7 |
| J16 | `:643`–`:645` | Sequencing step 4: harness supervisor + LISTEN/NOTIFY bridge | Not started | No implementation |

### T7 — Executable ontology runtime (`:655`–`:790`)

| ID | Cite | Item | Status here | Evidence |
|---|---|---|---|---|
| E1 | `:713`–`:717` | Primitive 1: `JdbcSolStoragePort` + one generic concept projections controller | Not started | No port implementation |
| E2 | `:719`–`:726` | Primitive 2: universal transition + Lilac reducer (~300 lines) | Not started | No service |
| E3 | `:728`–`:736` | Primitive 3: reactive sweep scheduler (`run_staleness_sweep`, `run_reconciliation_sweep`, `evaluate_proposition`) | Not started | No scheduler |
| E4 | `:737`–`:755` | Primitive 4: `InvocationRequest` → `nebula.op_registry` ISA executor | Not started | No executor |
| E5 | `:705`–`:710` | Flip `Ticket`, `Receipt`, `AgentRecord`, `Decision`, `Assessment`, `Agenda`, `System`, `Subsystem`, `Feature` to `executable` by authoring bindings | Not started | Only the 6 named representations are referenced; no binding SQL here |
| E6 | `:765`–`:766` | First slice: load 44 concepts + 6 executable representations from PostgreSQL | Not started | Same as J1 |
| E7 | `:767` | First slice: run WorkRequest transitions purely from ontology rows using `check_transition_guard` + rule `126a6421` | Not started | No transition endpoint |

### T8 — GitHub/local branch cleanup (`:791`–`:877`)

| ID | Cite | Item | Status here | Evidence |
|---|---|---|---|---|
| R1 | `:862`–`:875` | Delete 45 merged/superseded remote branches (+ `s10-core-packaging`) | Not assessable here | Requires GitHub state |
| R2 | `:862`–`:875` | Enable `delete_branch_on_merge: true` | Not assessable here | Repository setting |
| R3 | `:862`–`:875` | Prune 265 local squash-merged branches and their worktrees on `titanium` | Not assessable here | Host-local state |
| R4 | `:868` | Keep `test-clock-hygiene-guard` and `cdlc-casing-registry` if still wanted (qualified) | Not assessable here | Separate branches |

## 6. Reconciliation with `docs/p4a-requirements-consumer-inventory.md`

The two documents describe the same P4a work and now agree after one correction.

| Consumer inventory claim | What the ten turns add | Resolution |
|---|---|---|
| §0: a requirement delete is refused while "a spec, plan, segment set, request link, **work request**, child or live blueprint names it" | T5 directs `V221`, which drops `work_request.source_requirement_id` (`V221:104`) | **Corrected.** The work-request branch only works pre-V221; §4 above and a new hazard in the inventory record the ordering dependency |
| §0 / §2: consumers need no code change at apply | P1/P3 show the upstream `V220`/`V221` substrate the port assumes | Consistent — the port reads `resolution.request_requirement_link`, which `V220` creates and `V221` keeps |
| §3: DB objects that name the relation | P5 shows `Requirement → resolution.requirement` is an `executable` representation | **Added** cross-reference: the port fills the table the ontology binds, which is why identity preservation matters beyond the legacy readers |
| §7 hazard 6: cross-domain candidate/asset links | None | Unchanged |
| §9: no database contacted, V222 untouched | P8's operator gate | Unchanged and still true |
| Runbook §8 row 19 (rebuild job emits no SQL) | Not mentioned in the ten turns | The previous turn's correction stands; nothing here contradicts it |

No item is complete in one document and pending in the other: P4a itself is
**not started** (staged, not applied) in both accounts; the consumer repoints are
"no change required at apply" in both; and the delete-guard capability is now
"partial — blocked by the V221 ordering defect" in both.

## 7. Demonstrated vs asserted

**Demonstrated in this worktree** (file read, line cited): the `V221` migration
and its consumer repoint (P1), its column drop; the `V187`/`V220`/`V216`–`V218`
migrations; the 46-row amendment and verifier (P4); the inert Track A evaluator;
`ballerina/`'s 2026-09-19 boundary; the JVM stubs (`InMemorySolStorage`,
`BrokerWriteController`, `SearchWriteController`); the absence of a
`gestalt.pressure_signals` table; and the `V221`/`V222` column conflict and its fix (§4).

**Asserted only** (no artifact can confirm or refute it here): every live-database
claim — `V215`–`V221` and `V187` applied, `V216`/`V217` executable
representations live, 41/2/8 plan counts, `12,240` unreviewed candidates,
`9,117` graph entities, `0/46` `plan_id`, the 46-row ledger DDL, the ISA 36-opcode
vocabulary; every live-service claim (`sonar-sync`, `execution-ui`, Terrain
drift, Mongo fleet, LAN reachability); every cross-repo claim (`throttler-ui`
submodule contents, `atlas-ui`); and every GitHub/host cleanup item in T8.

## 8. Verification log

- Read all 877 lines of `argon-ten-turns.md`; extracted 83 discrete items (8
  port-adjacent, 75 out of scope).
- Checked every cited repository path for existence at `f78443b45`; read the
  in-scope artifacts and the relevant V222/V221/test lines.
- Confirmed `angular/throttler-ui` is a gitlink to `e54695a6…` (submodule,
  uninitialised) and `angular/atlas-ui` has no history in this repository, so
  T4's file paths are not inspectable here.
- No database connection was opened, no query was run, and no migration —
  live, shared or throwaway — was applied.
- `sql/V222__port_requirements_to_resolution.sql` is unmodified
  (`git status --porcelain` returns nothing for it).
