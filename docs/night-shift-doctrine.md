# Night-Shift Doctrine (2026-09-05)

> **Status: approved.** Encodes the night-shift flow agreed on 2026-09-04/05
> (thread `2c333168-e56f-4094-9674-c287cf5c5139`, plus the architect's
> receipt-isolation decision). Supersedes any ad-hoc night-shift habits
> described in individual role files. The active system is **Conduit**;
> the Nexus WRP document is aspirational and not used for this flow.

## Purpose

Night shift is the **scheduled, agent-driven** counterpart to the
interactive (day) workflow. During the night, a bounded POC cycle runs
automatically: SonarQube findings are triaged into coherent batches,
Batches become conduit plans/WorkRequests, a Builder implements them in
a worktree, a Critic reviews each PR as a change, and a Reviewer makes
the CI/CD-adjacent merge judgement. The flow is deliberately **narrow**
in v1 (same-file batching, ≤5 work requests) and grows only by explicit
decision.

The night shift is governed by the same doctrine CI proved in PR chain
#141–#148: **hermetic by default, prod contact forbidden** in test
stages; **receipt isolation** via a dedicated test DB (option A — see
below), never the live conduit DB.

## Roles

| Role | Night-shift responsibility | Boundary |
|---|---|---|
| **Planner** | SonarQube-severity triage grouped by scope; produce conduit work batches; inbox check | Groups, does NOT implement |
| **Builder** | Implement a batch in a worktree; push a branch with an open PR; fix-forward on CI failure | Bats nothing back to Planner for routine issues |
| **Critic** | Review the PR as a change (does the diff close the claimed sonar items? over-reach? clean trails?) | Gate to Review, not a merge authority |
| **Reviewer** | CI/CD-adjacent judgement: merge on GitHub+Jenkins green; failing PRs bounce to Planner as rework | Merge only when green; never silence-fix |

## Flow (POC v1)

1. **Planner — triage & grouping.**
   Input: SonarQube items (via Assembly `sonar` forum and/or sonar-mcp).
   Steps:
   - Run the **inbox check** first (`nebula_get_inbox {"role":"planner"}`
     or `nexus/bin/check-inbox.sh --role planner`).
   - Examine sonar items; group into coherent batches before anything
     reaches Conduit. Grouping keys, in order:
     1. **Scope first** — one repo+area per batch (e.g. `nexus/typescript/*`
        vs `nexus/ballerina/*`). Keeps PRs reviewable and keeps the Sonar
        leak-period attribution sane.
     2. **Severity/rule-class second** — false-positive hotspots batch
        separately from real reliability bugs; batch `new-code` gate
        blockers (fail PRs immediately) ahead of outside-leak-period debt.
     3. **Risk third** — anything touching auth/DB boundaries gets its own
        small batch.
   - Each batch becomes a plan/WorkRequest with **explicit acceptance
     criteria** ("sonar issue X closed on the PR branch, quality gate
     green"), never a vague "fix sonar stuff".
   - **POC constraint: same-file items batch together; ≤5 work requests
     per cycle.**
   - Grouping keys confirmed by roundtable 2026-09-05 (thread `2c333168`):
     scope → severity/rule-class → risk, exactly as listed above.

2. **Builder — implement, end with a PR.**
   - Take the batch from Conduit; work in a **worktree**
     (`~/dev/nexus-worktrees/<topic>`, branch `<topic>`) — never on `main`.
   - Definition of done: code fixed, local verification done, **branch
     pushed with an open PR**.
   - Treat PR checks (typecheck results, hermetic-PG test verdicts, Sonar
     scan) as **part of your own loop** — fix-forward on CI failure rather
     than handing the mess to the Reviewer.
   - Commit messages MUST align with the agent record (record is the
     source of truth). Commit, push, and raise the PR **without asking**
     (R8). The merge gate is the tests: PRs without passing tests are
     raised as **draft** and marked as such.

3. **Critic — gate to review.**
   - Review the **PR as a change**, not the pipeline: does the diff
     actually close the sonar items claimed? Is anything over-reached? Are
     the commit/record trails clean?
   - Verdict vocabulary (roundtable answer 2026-09-05): emit
     `type:gate-pass` or `type:gate-bounce` via nebula-mcp. A `gate-pass`
     forwards the PR to the Reviewer; a `gate-bounce` returns it to the
     Builder with specifics. The Critic is a gate, never a merge authority.

4. **Reviewer — CI/CD-adjacent judgement and merge.**
   - Merge only when **GitHub+Jenkins green** (build status, result, and
     quality-gate verdict available read-only via jenkins-sync :9097, the
     Assembly `jenkins` forum, and GitHub PR checks).
   - **Failing PRs bounce to the Planner as rework** (Planner regroups),
     not directly back to the Builder.

## Scheduling (2026-11-20)

The night-shift evaluator (`nexus-nightshift-scheduler.timer`, one-minute tick,
`nexus_nightshift` only) runs the four cycle entries on a **staggered cron
cadence**, not four equal intervals:

| Stage | Fires (each hour) | Responsibility |
|---|---|---|
| Planner | `:00`, `:30` | Sonar triage and batching |
| Builder | `:05`, `:35` | implement a batch in a worktree + PR |
| Critic | `:10`, `:40` | gate the PR as a change |
| Reviewer | `:15`, `:45` | CI/CD-adjacent merge judgement |

Four `interval 1800` rows with a NULL `last_run_at` were all due on the same
tick and every 30 minutes thereafter, so Builder/Critic/Reviewer raced the
Planner's output instead of following it.

Each launch resolves its bound `task_slug` against `tackle.tasks` +
`tackle.prompts`; the task template body and acceptance criteria become the
agent's prompt. An unbound entry falls back to the `agent_config` title (the
pre-2026-11-20 behaviour, where a bound row launched with only that title as
its instruction).

The Planner is additionally gated on its **work source**: the runner probes
SonarQube read-only (`/api/system/status`, then
`/api/issues/search?projectKeys=nexus&resolved=false`) and refuses to launch
when the server is unreachable, no token is configured, or zero findings are
open. Builder and Reviewer keep their queue gates
(`execution.requests`, `vision.tickets`). The probe performs no Sonar writeback.

## Receipt isolation (architect decision 2026-09-04, option A)

Night shift makes agent-driven Conduit runs scheduled and frequent —
receipt noise in the prod conduit DB stops being background and becomes
the dominant traffic. Decision: the pipeline's DB target is an **explicit
config surface** (env-driven, landed as PR #149) so pointing the pipeline
at a dedicated test DB is a **one-line flip, not a rework**.

- Doctrine: hermetic by default. Prod contact forbidden in scheduled
  night-shift runs, exactly as CI enforces for test stages.
- Near-term: night-shift runs target a dedicated test DB
  (`CONDUIT_PG_DSN` pointing at `nexus_nightshift` or equivalent); the
  flip drill lives in `python/conduit/DEPLOY_DB_CONFIG.md` (Option-A flip
  drill).
- Do **not** write night-shift receipts into the live conduit DB while
  the isolation flip is in place.

## Go-live preconditions

Before the night shift becomes a scheduled (non-manual) cycle:

1. **vanadium-ci backup incident `3c75f9b1` flagged and resolved** —
   `vanadium-ci-backup` failed 2026-09-04 04:16 (rsync FAIL), recovered
   2026-09-04 19:48–19:59 (checksum OK, `=== vanadium-ci backup complete
   (ok) ===`). The incident record (`3c75f9b1-3bb8-421e-9d58-497b7747ce4b`,
   to:sysadmin, type:incident, status:open) must be updated to resolved
   before the night shift is scheduled. A CI/Jenkins outage would strand
   the Reviewer's merge loop.
2. **sonar-mcp deployed** — fleet wiring merged (PR #150, #151). The
   six core tools (`sonar_search_issues`, `sonar_get_hotspot`,
   `sonar_mark_fp`, `sonar_add_comment`, `sonar_set_tags`,
   `sonar_quality_gate`) are the agent-facing surface for this flow.
3. **jenkins-mcp** — NOT on the v1 critical path. Schedule after
   sonar-mcp; the read-only merge signals are already covered. The write
   path (`jenkins_trigger_build`, `jenkins_reload_job_config`,
   `jenkins_stop_build`, `jenkins_tail_console`) is future work for
   Builder/Reviewer self-service, not a v1 blocker.

## Day/Night boundary

- **Day** (interactive turns): evidence accumulation; agents follow the
  interactive session boot (clock-in, issues forum, to-do forum, inbox).
- **Night** (scheduled cycles): the bounded POC flow above; after the
  cycle, agents record outcomes (R2) and post a change-log summary (R14)
  so the next day starts from reconciled state.

## Verification of this document

Docs-only change. No code tests apply. The PR body carries the agent
record UUID (`88d4e1a5-51c0-47bd-a87e-702b2f9c84cd`, R1 plan) and this
doctrine's lifecycle note. Role file pointers (one line each) added to
`config/harnesses/opencode/agents/{planner,builder,critic,reviewer}.md`.