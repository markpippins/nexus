# System Pulse (revised 2026-11-10) — off-network edition

## 0. Provenance and evidence rule

- Written from this host while **remote/off-LAN** (`192.168.1.18/24` via wifi,
  gateway `192.168.1.1`; fleet addresses `.209`, `.82`, `.229` all unresponsive).
- The `/24` overlap is coincidental RFC1918 reuse on a different physical
  network. **Every prior ping/SSH/port claim against Vanadium, Thallium, or
  Helium is therefore marked NOT VERIFIABLE, and no new host-down claim is made.**
- Only local evidence is used: local `git log` with `merge-base --is-ancestor`
  checks against `refs/remotes/origin/main` (`5f517965e`, 2026-11-09; local
  `HEAD` `b1d5780a6` is one commit behind, so it is not the landing reference),
  localhost APIs
  (`:3171` resolution, `:3101` nebula), on-disk logs under
  `/home/codex/dev/pgsql/`, `ss -ltn` on this host, and locally executed pytest.
- The standing Vanadium pipeline verdict rests **only** on timestamped local logs.

## 1. The CI emergency wave and what it cost

The stabilization wave is real and its tail is on main. Local proof (all
ancestors of `refs/remotes/origin/main` unless noted):

| PR | Commit (local) | Date | Subject |
|---|---|---|---|
| #903 | `f913d6407` | 2026-11-06 | canonical attestation request prompt |
| #932 | `6576aa6ac` | 2026-11-09 | fail CI when a suite skips for a missing host tool |
| #933 | `ea0b81069` | 2026-11-09 | one shared host-tool guard for the 26 suites |
| #934 | `b1d5780a6` (= HEAD) | 2026-11-09 | route every host-tool skip through the shared guard |
| #935 | `5f517965e` | 2026-11-09 | schema-coverage scan stops reading virtualenvs |

Cost, unchanged from the prior summation: the architectural roadmap froze
mid-stride while the team fought harness exits, watchdog kills, dependency
drift, and attestation-gate rejections. The frozen items are §2–§4c below.

**Correction (this revision):** PR #935 (`5f517965e`) **is landed** — it is the tip of
`refs/remotes/origin/main`. An earlier draft called it unverified because the
check ran against local `HEAD`, which is 0 ahead / 1 behind origin/main. See §2c.

## 2. Ghost plans — git landed, DB frozen (local proof each)

Local DB (`:3101`) reports 45 plans: **20 completed, 19 pending, 6 archived**.
Seven claimed-landed plans were checked commit-by-commit against `refs/remotes/origin/main`
(`git merge-base --is-ancestor`). Result: **three true ghosts**, not seven —
four of the seven are already `completed` in the local DB, so no drift there:

| Plan | Implementation commit (local, ON-MAIN) | Plan status (local DB) |
|---|---|---|
| 8261648 (frame_values) | `19b997689` (#352) | completed → no drift |
| 8261657 (peb-srv :4111) | `57bf068e3` (#579) | completed → no drift |
| 8261658 (aegis-srv :4116) | `28be32f47` (#581) | completed → no drift |
| 8261659 (nebula-srv :4101) | `5e7a70e0b` (Merge PR #685) | completed → no drift |
| 8261653 (W-A) | `3e4420312` (Merge PR #780, w-a-duplicate-reconciliation) | pending → **ghost, confirmed** |
| 8261654 (W-B) | `db8664988` (#799, plan 8261654 in subject) | pending → **ghost, confirmed** |
| 8261665 (W-C re-run) | `b99e65a7d` (#892, plan 8261665 in subject) | pending → **ghost, confirmed** |

**Corrections to the prior draft:** W-A landed via **#780**, not #782 —
`731083cf1` (Merge PR #782) is on main but its branch is the
unblock_plan/LOSM-PG-suite work, not W-A. W-B landed via **#799**, not #798 —
no commit referencing #798 exists in the local object store outside unrelated
seed text. Structure S1–S6 (`3fc556377`, `26bfb326f`, `03e9de3ce`,
`c4d20f1b8`, `c4ae889d9`, `22825ae97`) are all on main; the S3 fail-closed
follow-up is on main as `ca7974d5a` (#659) — a rebased variant of the
earlier-cited `0ad63e7c5`, which is not itself an ancestor of `HEAD`.

The other 16 pending plans (8261666, 8261664, 8261663, 8261650, 8261649,
8261662, 8261661, 8261660, 8261656, 8261655, 8261651, 8261647, 8261646,
8261645, 8261639, 8261638) have no merged implementation commit found locally
and remain genuinely open.
Plan 8261650 (WR canonicalization, Option B) is pending although its blocking
architect ruling landed (record `886d63c0`); its dependency string is stale —
verified from local agent records, not from a probe.

## 2b. PR #700 — adjudicating the "NOT green, NOT attested" claim

Claim under review (tester resolution record `1cdc4d39`, 2026-10-01T21:40:39Z):
"#700 NOT green and NOT attested — head moved to `4ca5342b55a3`".

Local verdict: **true as a PR-state statement, and not evidence of orphaned work.**

- The head is real and is the branch tip: `4ca5342b55a3` (2026-10-01 15:38:58 -0400,
  "move the durability test out of the unit tier; it was red in CI") is the tip of
  `refs/{external,fetched,remotes/github}/cdlc-inbox-pointer-durability`.
- It is **not** an ancestor of `refs/remotes/origin/main` (`5f517965e`), so PR #700 never merged as a unit;
  "NOT green and NOT attested" stays accurate, and no attestation should be
  reconstructed for it after the fact.
- It is **not** orphaned work. `git cherry refs/remotes/origin/main <ref>` lists all 8 branch commits with
  `-` (patch-equivalent on main) and **0** with `+`. Two named pairs are patch-identical:
  `35d81d11e` ≡ `6f78eedc4` (patch-id `74176f4f…`) and `4ca5342b55a3` ≡ `e23f195e5`
  (patch-id `e86e5eb4…`), with `6f78eedc4`, `e23f195e5`, and DBA follow-up `634053884`
  all ancestors of `refs/remotes/origin/main`.
- The capability is live, not deploy-pending: `migrations/071-role-inbox-pointers.sql`
  and `bin/drafts/071-role-inbox-pointers.sql` exist; `block-segmentation-redis.service.ts`
  binds `nebula.role_inbox_pointers`; `GET :3101/api/inbox-pointer/planner` returns
  `{"pointer":"2026-11-01T16:27:16.011Z","pointerMissing":false}`.
- Footprint correction: the engineer note's "726 files, -140,512 lines" is the two-dot
  `main..branch` view (what a merge would revert). The PR's own change since divergence
  is **22 files, +2,805/-18** (three-dot). Neither figure changes the finding above.
- Lifecycle comes from agent records `c9ac73de` (reopened, verified redundant) and
  `60d91648` (closed again 2026-11-08T13:37:29Z; audit of 44 closed PRs found no
  orphaned work). Live GitHub state is **NOT VERIFIABLE off-network**; those records
  are the local evidence, and they agree with local git.

Net: PR #700 must not be listed as stuck, landed, or an unmerged obligation. It is a
closed PR whose patch set is fully present on main and whose defect fix is serving
live local traffic.

## 2c. PR citation audit — every PR number in this document

**Landing reference is `refs/remotes/origin/main` at `5f517965e` (#935).** Local
`HEAD` is `b1d5780a6` (#934), **0 ahead / 1 behind** origin/main, so a check
against `HEAD` reports one already-merged PR as unlanded. Re-run any row with:

```bash
R=refs/remotes/origin/main
git merge-base --is-ancestor <commit> $R && echo "merged as a unit"
git cherry -v $R <refs/pr/N or branch ref>   # '-' patch-equivalent, '+' unique
```

| PR | Cited for | Local commit | Verdict | Re-run evidence |
|---|---|---|---|---|
| #352 | plan 8261648 | `19b997689` | **merged as a unit** | ancestor of origin/main; subject names "inspector C2, plan 8261648" |
| #579 | plan 8261657 | `57bf068e3` | **merged as a unit** | ancestor; "peb-srv (:3111 → :4111) as thirteenth twin (#579)" |
| #581 | plan 8261658 | `28be32f47` | **merged as a unit** | ancestor; "aegis-srv (:3116 → :4116) as fourteenth twin (#581)" |
| #685 | plan 8261659 | `5e7a70e0b` | **merged via merge commit** | `git log -1 5e7a70e0b` → "Merge pull request #685 …/moleculer-nebula-port" |
| #780 | plan 8261653 (W-A) | `3e4420312` | **merged via merge commit — the real W-A landing** | `git log -1 3e4420312` → "Merge pull request #780 …/w-a-duplicate-reconciliation" |
| #782 | earlier draft cited as W-A merge | `731083cf1` | **merged, but NOT the work claimed** | second parent `f90d276c2` = "fix(conduit-mcp): unblock_plan appends REQUEUED instead of deleting receipts" |
| #798 | earlier draft cited as W-B merge | `refs/pr/798` tip `8985160e0` | **PR never merged; zero unique content (superseded by #799)** | `git cherry -v origin/main refs/pr/798` → 2 `-`, 1 `+` = `d47adbc32`, which changes **0 files** (empty commit); `8985160e0` patch-id `e3b9715f…` = `db8664988` |
| #799 | W-B landing | `db8664988` | **merged as a unit** | ancestor; subject carries "plan 8261654 (#799)" |
| #892 | plan 8261665 (W-C) | `b99e65a7d` | **merged as a unit** | ancestor; subject carries "plan 8261665 (#892)" |
| #903 | canonical attestation prompt | `f913d6407` | **merged as a unit; effect live** | ancestor; `git ls-tree origin/main -- docs/attestation-request-prompt.md` present |
| #932 | fail CI on host-tool skip | `6576aa6ac` | **merged as a unit** | ancestor |
| #933 | shared host-tool guard | `ea0b81069` | **merged as a unit** | ancestor |
| #934 | route every skip through guard | `b1d5780a6` | **merged as a unit** (= local HEAD) | ancestor |
| #935 | schema-coverage virtualenv scan | `5f517965e` | **merged as a unit — origin/main tip** | ancestor; **corrected** (see §1) |
| #659 | S3 fail-closed follow-up | `0ad63e7c5` → `ca7974d5a` | **rebased: PR commit not an ancestor, patch-identical twin on main** | patch-id `6e3a4b3f…` on both; `ca7974d5a` is an ancestor |
| #700 | durable inbox pointers | tip `4ca5342b55a3`; twins `e23f195e5`, `6f78eedc4` | **closed, not merged; zero unique content; effect live** | `git cherry` → `0` `+` lines; twin commits and `634053884` are ancestors; `:3101/api/inbox-pointer/planner` returns a stored pointer |

Verdict counts: **13 merged as a unit or via merge commit**, **1 merged but
mislabelled (#782)**, **1 patch-equivalent superseded PR (#798)**, **1 rebased
(#659)**, **1 closed-but-equivalent (#700)**, **0 genuinely unlanded**. No PR
cited in this document is stuck or blocking.

### Claims corrected in this revision

- **#782 ≠ W-A.** `731083cf1` merged the unblock_plan fix (`f90d276c2`), not the
  duplicate-history work. W-A landed via **#780** (`3e4420312`).
- **#798 ≠ W-B, and #798 never merged.** W-B landed via **#799** (`db8664988`),
  whose patch is identical to #798's tip (`e3b9715f…`). #798's only
  non-equivalent commit is empty, so nothing was stranded by its closure.
- **#935 is landed, not unverifiable.** It is origin/main's tip; the earlier
  "not an ancestor of `HEAD`" reading was local checkout lag (0 ahead / 1 behind).
- **#659 was rebased.** The PR commit `0ad63e7c5` is not an ancestor; its
  patch-identical twin `ca7974d5a` is.
- **Subject-grep hazard:** `git log --grep="(#352)"` and similar also match
  unrelated "seed guards-db" commits whose *bodies* cite the number, and
  `git log --all <commit> -1` unions refs and can print the wrong commit. Anchor
  the subject (`--grep="(#N)$"`) and drop `--all` when naming a commit.

### Single open verification item

Live GitHub metadata for these 16 PRs — close-vs-merge reasons and comments for
**#700** and **#798**, and **#782**'s own title/body — is **NOT VERIFIABLE
off-network**. One consolidated item (see §6 item 3), not one per PR: local git
settles what landed; only the GitHub-side record is missing.

## 3. The stuck `resolution.work_request` queue (local `:3171`)

37 rows: **36 DRAFT, 1 COMPLETED** (July WRP test). `plan_id` is NULL on
**37/37**. Full draft breakdown (titles truncated at 70 chars):

| Count | Title |
|---|---|
| 15 | W-C decomposition: consumer migration (writer fencing + …) |
| 3 | Separate execution, work, and governance outcomes in the Con… |
| 2 | Implement requirement compile-to-plan capability with confor… |
| 2 | W-A decomposition: duplicate-history reconciliation into M-s… |
| 2 | T19 end-to-end correlation/causation trace |
| 1 each (12) | Ruling 32 cross-runtime parity guard; drain probe; resolved-op probe; Require positive builder work evidence; W-B decomposition; `t`; Parallel convergence tracks; T26 double-submit; Post maintenance summary; D-T19-1 single-envelope; double-append check; T19 live NATS envelope observation |

Only ~8 of the 36 drafts are independent intents; 18 are duplicates or child
fragments and 10 are probes/tests. The derivation package (8 intent cards,
19 WR drafts, 37/37 disposition ledger; records `ea9ea3f8`…`895e66c2`,
ledger `39443dee`, thread `3dd1987d`) already exists as an awaiting-ratification
proposal and is not re-argued here.

## 4. Sidelined sub-projects — local state only

- **Structure / AST project: BUILT, TESTED, UNCONSUMED (local proof).**
  S1–S6 commits on main (see §2); `python/structure` present with
  `contract.py`, `registry.py`, `bridge.py`, `conformance.py`, `sql_parser.py`;
  `PYTHONPATH=python python3 -m pytest python/structure -q` → **116 passed**
  (run on this host, 2026-11-10). No consumer imports the contract outside
  `python/structure` (verified by grep in a prior session).
- **Moleculer twins: code present, posture enforced, runtime NOT VERIFIABLE.**
  `moleculer/` holds **23 directories with `package.json`** (aegis, assembly,
  cascade, conduit, draft, execution, harness, kernel, knowledge, nebula,
  nexus-broker, peb, prompt-sync, resolution, role-memory, search, semantics,
  solscript, substance, tackle, voyager, wind — the untruncated text's "18"
  depends on which set counts as a service). `ports.yaml` declares 19 ports and
  `ss -ltn` shows **0 bound** (checklist T1 PASS, 0/19, 2026-11-10).
  `bin/assert_moleculer_ports.sh` is the enforcement: the operator directive of
  2026-09-22 says moleculer does **not** run locally, and the gate fails if any
  declared port is bound. Whether twins run anywhere else is a same-LAN question.
- **JVM conformance: artifacts present, runtime NOT VERIFIABLE.**
  `jvm/{spring,quarkus,helidon,shared}` exist; the **main checkout** carries
  at least **10** untracked `*-SNAPSHOT.jar` artifacts under `*/target/`
  (none tracked, none present in this clean worktree) — the untruncated "three"
  undercounts. Thallium (`:8092`) conformance results cannot be checked from
  here, and no local log mentions the claimed probe route.
- **Aspects / Expression: library-only, local proof of existence.**
  `python/aspects` (`binding_port.py`, `contract.py`, tests) and
  `python/expression` (`boundary.py`) are present in the tree. Wiring to
  Structure is unwritten. The untruncated dates hold: `python/aspects` was last
  touched by `9d0c4f53d` (2026-09-23, #505), and `python/expression` by
  `2e96e8568` (#886, 2026-11-06).
- **Helium: NOT VERIFIABLE.** No reachability claim is made; the earlier
  "No route to host" observation is withdrawn as an off-network artifact.
- **Vanadium: pipeline verdict below; host state NOT VERIFIABLE.**

## 4b. The untruncated §3 inventory — claim by claim

The truncated text cut off inside §3, so the first revision never assessed the
five sub-project narratives. Each is now checked against local evidence:

| Claim (untruncated §3) | Verdict | Local evidence |
|---|---|---|
| Moleculer: 18 twins scaffolded, gated/disabled-by-default on titanium (`7b66c196`), canary verification halted | **posture VERIFIED, handle UNVERIFIED** | 23 dirs with `package.json`; `ports.yaml` 19 ports, **0 bound** (T1 PASS 0/19, run 2026-11-10); `bin/assert_moleculer_ports.sh` enforces no-local-moleculer. `7b66c196` is **not a commit object** in the local store — a record-style handle, so the specific gating citation is not verifiable here. |
| JVM: fat-jar rollout abandoned; thallium `:8092` nexus-core probe failing on `/api/workers/execution/health` | **artifacts VERIFIED, failure NOT VERIFIABLE** | `jvm/{spring,quarkus,helidon,shared}` exist; ≥10 untracked `-SNAPSHOT.jar` files in the main checkout. No local log under `/home/codex/dev/pgsql` or `nexus/logs` mentions `workers/execution/health`, so the probe failure remains a same-LAN claim. |
| Aspects untouched since 2026-09-23 | **CONFIRMED** | last commit touching `python/aspects` on origin/main: `9d0c4f53d` (2026-09-23, #505). |
| Expression resumed Nov 6 via PR #886 | **CONFIRMED** | `2e96e8568` (#886, 2026-11-06) is an ancestor of origin/main and is `python/expression`'s last change. Integration as semantic evaluator is still unwritten (§4). |
| CDLC: PR #700 (22 files) closed without merging, leaving execution emission semantics fragmented | **closure CONFIRMED, loss REFUTED** | #700 closed, unmerged, 22 files, +2,805/-18 — but `git cherry` finds **0** unique commits; twins are ancestors and the inbox-pointer feature serves live traffic (§2b). What remains is a design/ownership gap, not stranded code. |
| Continuity: `attest.py` patched by #906; blackboard↔keychain integration (`5c5f65ed`) unresolved | **#906 CONFIRMED, integration claim UNVERIFIED** | `9385ef353` (#906, 2026-11-06) is an ancestor. `5c5f65ed` is not a commit object, so the integration state cannot be settled from that handle. |

## 4c. The untruncated §4 friction list — what is still true

| Item (untruncated §4) | Status on 2026-11-10 | Evidence |
|---|---|---|
| #908, #909, #913 "waiting on tester" | **STALE — all three merged** | #909 `441b81fe6` 2026-11-07T13:15Z; #908 `c6afed3d4` 2026-11-07T14:00Z; #913 `13f8c73bc` 2026-11-08T00:45Z. All ancestors of `refs/remotes/origin/main`. |
| vision-srv-py alert cascade continues because #908 is stuck | **CAUSE FALSE, symptom PARTLY TRUE** | #908 merged and removed the service from the *monitored plane*, but the newest issues-forum thread is still `2026-11-08T13:38Z` "vision-srv-py down on :8003 — no systemd unit" (sysadmin, `replyCount: 0`), reporting terrain `OFFLINE` with `activeFlag=true`. The residual issue is terrain/unit decommissioning, not attestation. |
| Thallium JVM probe failing, no incident | **NOT VERIFIABLE off-network** | `.82:8092` is unreachable from here and no local log references the probe route. No incident (or all-clear) claim is made. |
| Dashboard/queues do not reflect git | **CONFIRMED** | §2: three true ghosts among seven claimed-landed plans. §3: 36/37 WorkRequests DRAFT, `plan_id` NULL on all. |

**Revised Phase 1.** "Attest and merge #908/#909/#913" is already done — the
only remaining Phase-1 work is (a) dataset reconciliation: mark the three ghost
plans complete and dispose of the 36 WR drafts against the existing ledger, and
(b) retire or restore vision-srv-py's terrain entry/unit so the sysadmin cycle
stops re-reporting a service that #908 removed from monitoring. **Phase 2 is
unchanged**, with one ordering constraint: Moleculer/JVM runtime state must be
resolved on-LAN first, because every claim about their liveness is
unverifiable from this host.

## 5. Standing Vanadium pipeline verdict — timestamped local logs only

`/home/codex/dev/pgsql/` logs (sizes/dates from the filesystem):

- `sync-vanadium.log` (4,869 bytes, Nov 9 02:59): successes
  `2026-11-08 02:52` and `06:55:37`, then `2026-11-09 02:59:40` —
  `psql: error: could not translate host name "vanadium"`.
- `pg-backup-to-vanadium.log` (187,831 bytes, Nov 9 03:30):
  `[2026-11-09 03:30:19] FAIL(preflight): vanadium is not reachable —
  aborting before any dump`; the log itself notes a dead/off-LAN target
  takes its name with it.
- `vanadium-ci-backup.log` (17,062 bytes, Nov 9 04:21): skipped run —
  target unreachable **and** the expected drive path absent (two independent
  conditions; restoring the drive alone will not bring the tier back).

Verdict: the off-machine pipeline was **broken as of 2026-11-09 ~04:21** by
local-log evidence. Whether it has recovered since **cannot be determined
from this host**; no conclusion about Vanadium the machine is drawn.

## 6. Re-verification checklist for titanium / same-LAN

1. Reachability of `.209`, `.82`, `.229` and the `41xx`/`8092` listeners.
2. Whether the Vanadium backup/CI/sync timers have succeeded since Nov 9
   (read the same three logs from titanium or the fleet log host).
3. **One consolidated GitHub-state item for all 16 cited PRs** (§2c): close-vs-merge
   reasons and comments for #700 and #798, and #782's own title/body. Local git
   settles what landed; only the GitHub-side record is missing off-network.
4. Re-run of the Moleculer twin and JVM conformance checks against live ports.
5. Thallium probe: read the nexus-core probe logs / hit
   `/api/workers/execution/health` from `.82:8092` or titanium; no local log
   records this failure, so the untruncated §4 claim is still open.
6. vision-srv-py terrain entry: confirm whether `activeFlag=true`/missing unit
   was retired after #908, or whether the 2026-11-08T13:38Z sysadmin report is
   the last word on a service that is still half-decommissioned.
## Plan and draft housekeeping (2026-11-10)

- Ghost plans 8261653, 8261654, 8261665 reconciled to `completed` via `nebula.blueprints_history` (guarded, with `payload.reconciliation`). No receipts forged. 8261665 still differs from the receipt-derived `nebula.plan_status`.
- Stale probe 8261638 reconciled to `completed`.
- Stale plan 8261647 (assembly comment attribution) reconciled to `completed`. The fix shipped in #350 (`c42053414`); `test_conformance_attribution_c1.py` passes 16/16 on `origin/main`.
- 15 W-C duplicate `resolution.work_request` drafts set DRAFT→CANCELLED (audit row in `tackle.system_logs`).
- Four plans closed as `completed` on merged evidence (each merge commit verified an ancestor of `refs/remotes/origin/main`): 8261663 (#810 `815a959aa`, REVIEW_PASS + tester attestation), 8261664 (#824 `9d1e0ec49`, supersedes closed #812), 8261662 (#826 `f36569438`, #748 closed superseded), 8261666 (#924 `dddaa3417`, plus #923/#925).
- Three tester attestation to-dos advanced to Accepted (4) with the tester record plus merged commit as evidence: PR #913 (`8390f303`), PR #909 (`647c7175`), PR #908 (`4edea33c`).
- WorkRequest 8261651 stays `pending` and re-scoped to run after the consolidated kernel deploy (proposal record `caf36174`); the live kernel still serves only legacy routes, so repointing callers now would break routing.
- Still pending, each with an expired builder ticket and no disposable evidence: 8261646, 8261649, 8261650, 8261655 (REVIEW_REJECT), 8261656, 8261660, 8261661. These are real work awaiting a re-armed ticket, not ghost plans.
- Role dispatch is blocked: no ACTIVE lease for engineer, tester or reviewer, and `POST /run` with `resolve_only` returns `ADMISSION_DENIED / ROLE_EXPIRED`. Lease renewal is an operator action (request record `c32ea7f8`).
- 21 other DRAFT work requests remain undecided.
- The main checkout's untracked pre-#936 copies of the checklist script, its test, the run capture and the older system-pulse text were moved to `/tmp/stale-same-lan-20261110T062106Z/`; `origin/main` carries the newer tracked versions.
