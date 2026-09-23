# Role Consumer Inventory (T11)

**Date:** 2026-08-16
**Source:** T11 — Inventory and classify Nebula, Tackle, and Wind role identities.
Role-plane classification and canonical naming were adopted earlier; this is the
**consumer inventory** reconciliation (T11 remaining work), materialized live.

## Canonical role set (nebula.v_role_capabilities / wind.v_roles — 8 roles)

`analyst, architect, builder, engineer, inspector, planner, reviewer, topologist`
(plus runtime personas not in the canonical set: critic, operator, sysadmin, engineer-iii,
devops, engineer-ii, DBA, auditor, epistemologist, claim-extractor, bp, test,
audit-probe — see per-surface usage below).

## Consumer surfaces

### 1. Scheduler — `tackle.agent_scheduler`

| role | scheduled entries |
|---|---|
| builder | 2 |
| reviewer | 2 |
| architect | 1 |
| devops | 1 |
| engineer-ii | 1 |
| topologist | 1 |

### 2. Timeclock — `tackle.agent_timeclock` (sessions)

| role | sessions | active |
|---|---|---|
| engineer | 143 | 9 |
| architect | 28 | 2 |
| analyst | 18 | 0 |
| sysadmin | 9 | 2 |
| claim-extractor | 3 | 1 |
| dba | 3 | 1 |
| engineer-ii | 2 | 1 |
| bp | 1 | 1 |
| planner | 1 | 0 |
| audit-probe | 1 | 0 |

(tackle.sessions is a separate surface: only `test` role, 38 sessions, last
2026-08-12 — tackle session tracking, distinct from the timeclock MCP.)

### 3. Role-memory indexes — Redis `mem:idx:{role}` (15)

`analyst, architect, auditor, builder, critic, DBA, devops, engineer,
engineer-ii, epistemologist, inspector, operator, planner, reviewer,
topologist`

### 4. MCP permissions / capability enforcement

- `nebula.v_role_capabilities`: the 8 canonical roles with owns_domains
  (planner: plan_proposals/requirement_readiness; architect:
  architecture_decisions/specifications/implementation_plans; analyst:
  issue_triage/ambiguity_resolution; engineer: implementation/
  build_verification; topologist: topology_verification/system_landscape;
  reviewer: review_judgment; inspector: compliance/governance; builder:
  execution).
- `peb.capabilities`: **0 rows** (empty — capability enforcement boundary is
  currently the nebula view, not peb).

### 5. Wind — `wind.v_roles`

Mirror of the 8 canonical roles (same name/display/owns_domains/cron fields as
nebula.v_role_capabilities; planner+analyst+inspector have cron enabled).

## Reconciliation notes / drift

1. **Timeclock** carries 5 non-canonical personas (sysadmin, claim-extractor,
   dba, bp, audit-probe) and is missing several canonical roles (reviewer,
   inspector, builder, topologist, critic never clock in).
2. **Redis indexes** (15) include auditor, DBA, epistemologist, operator,
   critic — personas with no canonical capability row in nebula.v_role_capabilities.
3. **peb.capabilities is empty** — the peb capability surface is not yet a live
   enforcement boundary; nebula.v_role_capabilities is.
4. Canonical naming (lower-kebab) holds on all surfaces; no case-split observed
   (e.g. `DBA` in Redis vs `dba` in timeclock is a case variance worth
   normalizing).

No role/FK/identity changes were made — inventory only, per T11 non-overlap.
