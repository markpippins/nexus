# P4a consumer inventory — `nebula.requirements` and its compatibility projection

Static inventory at `f78443b45` (branch `req-port-p4a`), 2026-11-21.

**Scope.** Every service, UI, job and integration in this repository that reads
or writes `nebula.requirements`, plus every database object whose body or
definition names it. The pre-port audit table `nebula.requirements_history` is
included only where something still touches it.

**Method.** Enumerated with `rg` over the worktree for `nebula.requirements`,
unqualified `requirements` in SQL positions, and `requirements_history`, then
each hit was classified by reading its statement. No database was contacted, no
query was run, and V222 was not applied anywhere. The `pg_depend` / `pg_proc`
enumeration in the runbook §8 has therefore **not** been re-run — see
§9 for what remains unverified.

**Result.** 4 services, 13 jobs/scripts, 5 database objects, 2 UI/API clients
and 11 documentation or contract files. All of them keep working unchanged at
apply time; the projection is load-bearing by design. What changes at apply is
delete semantics and the identity of the table behind every read.

---

## 0. The premise: what the apply changes

V222 (`sql/V222__port_requirements_to_resolution.sql`) replaces the relation in
place; it does not cut readers over. After the apply:

| Aspect | Before | After V222 |
|---|---|---|
| `nebula.requirements` | auto-updatable view over `requirements_history` | 22-column read-only-looking view over `resolution.requirement` |
| Columns / order | 22 | identical 22, identical order |
| Visibility | current segment only | identical predicate (`recorded_on_dt`/`recorded_until_dt`, `valid_from`/`valid_until`) |
| Open-ended sentinel | `9999-12-31 23:59:59+00` | same literal emitted by the view |
| INSERT / UPDATE / DELETE | view triggers writing `requirements_history` | `INSTEAD OF` trigger writing `resolution.requirement` |
| `id` on INSERT | caller-supplied or default | same; preserved by value |
| `source_type` / `compilation_status` | n/a | derived (`candidate` when `candidate_id` is present, else `manual`) / `'draft'` |
| `work_request_dco` | legacy column | carried 1:1 as a nullable canonical column |
| DELETE | always succeeds | refused with SQLSTATE `P4A01` while a spec, plan, segment set, request link (and, until V221 is applied, the legacy `work_request.source_requirement_id`), child requirement or live blueprint names it |
| `requirements_history` | written by the view triggers | frozen; retained as pre-port audit, no longer receives rows |

Consequences for every consumer below:

1. **Reads need no change at apply.** Same columns, same visibility, same
   sentinels. The statement is simply served from `resolution.requirement`.
2. **INSERT/UPDATE need no change at apply.** The write-through trigger derives
   the two non-defaulted canonical columns and normalizes the open-end markers.
   Explicit-id inserts (`/import`, docs import) keep working because their
   `WHERE NOT EXISTS (SELECT 1 FROM requirements WHERE id = ...)` guard reads the
   same relation they write.
3. **DELETE changes meaning.** A delete now goes through
   `resolution.requirement_delete_guard()`, and the answer can be "no" with the
   blocking surfaces named. Services already map that to a 409
   (`requirement-delete.ts` / `requirement_delete_guard.ts`); scripts that issue
   a bare `DELETE` do not and will abort.
4. **`requirements_history` is no longer live.** Nothing in runtime code reads
   it today, so no reader breaks — but any report, audit query or re-created
   migration function that reads it stops seeing new requirements.

---

## 1. Services (direct SQL)

| # | Consumer | Sites | Operations | Transition at apply | Work required before the projection is dropped |
|---|---|---|---|---|---|
| S1 | `typescript/nebula-srv/src/routes.ts` (Express, `:3101`) | `369`, `1058`, `1062`, `1119`, `1163`, `1167`, `1262`, `1329`, `1359`, `1400`, `1491`, `1507`, `1763`, `1798`, `1982`, `2131`, `2158`, `2197`, `2204`, `2211`, `3219`, `4044`–`4157`, `4327`–`4417`, `5046`, `5151` | list, count, get, children, dependency-target validation, create, batch status, patch, kanban move, compile read, `conduit_plan_id` write, re-parent on system/subsystem/feature delete, `/import`, search and agent-record joins, spawn-requirement from candidate, blueprint `requirementRef`/`requirement_ids` validation (`369`, deliberately on the transition surface per the comment at `244`), specification `link-requirements` candidate lookup (`5151`) | No change. Unqualified `requirements` resolves on `search_path=nebula`; `POST` returns the trigger-derived row via `RETURNING *`. The move route already avoids `FOR UPDATE` through the view (advisory lock at `1485`–`1491`). | Qualify `resolution.requirement`, add the current-segment predicate, supply `source_type` on INSERT, keep `work_request_dco`. |
| S2 | `typescript/nebula-srv/src/requirement-delete.ts` | `56`, `79` | scope id read + pre-V222 fallback delete | Guard probe (`to_regprocedure`) flips to installed; deletes go through `resolution.delete_requirements`; fallback SQL becomes dead but harmless. | Repoint the scope read; the guard calls are already canonical. |
| S3 | `adonisjs/nexus-control-edge` — `nebula_hierarchy_controller.ts` | `421`, `424`, `470`, `503`, `506`, `590`, `665`, `691`, `742`, `824`, `843`, `1016`, `1043`, `1086`, `1093`, `1100`, `1215`, `1247`, `1408` | list, count, get, children, dependency validation, create, batch status, patch, move, re-parent, compile read, plan-link write | No change; same connection and `search_path`. | Same as S1 (relation + predicate + `source_type`). |
| S4 | `adonisjs/nexus-control-edge` — `nebula_harvest_controller.ts` | `736`, `859`, `973`, `983`, `1168`, `1178` | candidate→requirement promotion INSERT (carries `candidate_id`, `conduit_plan_id`), candidate lookup, agent-record and snapshot joins | No change; the trigger derives `source_type='candidate'` from the supplied `candidate_id`. | Relation + predicate; supply `source_type` explicitly. |
| S5 | `adonisjs/nexus-control-edge` — `nebula_docs_controller.ts` | `809`, `811` | idempotent docs `INSERT ... WHERE NOT EXISTS (SELECT 1 FROM requirements WHERE id = ?)` | No change; the guard reads the projection, so a ported id is not re-inserted. | Relation + predicate + `source_type`. |
| S6 | `adonisjs/nexus-control-edge` — `nebula_meta_controller.ts` | `249`, `619`, `786`, `1258`, `1274`, `1287` | open-question entity title, global search (`result_type='requirement'`), requirement count, system/subsystem/feature rollup joins | No change (counts and search see exactly the current rows they saw before). | Relation + predicate. |
| S7 | `adonisjs/nexus-control-edge` — `app/services/requirement_delete_guard.ts` | `56`, `81` | scope id read + fallback delete | Same as S2. | Same as S2. |
| S8 | `moleculer/nebula/src/routes.ts` | `779`, `783`, `840`, `884`, `888`, `983`, `1050`, `1080`, `1121`, `1207`, `1223`, `1477`, `1626`, `1653`, `1692`, `1699`, `1706`, `2672`, `3497`–`3610`, `3780`–`3880`, `4499`, `4634` | byte-for-byte mirror of S1 | No change. | Mirror of S1. |
| S9 | `moleculer/nebula/src/requirement-delete.ts` | `56`, `79` | mirror of S2 | No change. | Mirror of S2. |
| S10 | `typescript/conduit-mcp/src/db.ts` | `1819`, `2116` (comment at `1758`) | read-only orphan audit: `NOT EXISTS (SELECT 1 FROM nebula.requirements WHERE conduit_plan_id = ...)` inside `vision.check_receipt_integrity()` | No change; `conduit_plan_id` is canonical and carried. | Relation only. |

`typescript/nebula-mcp` and `angular/*` reach these tables only through S1's
HTTP API — see §4.

---

## 2. Jobs and scripts

| # | Job | Sites | Operations | Transition at apply | Work required |
|---|---|---|---|---|---|
| J1 | `bin/architect_process_todo.py` | `112`, `129`, `142`, `154`, `156`, `286`, `329`, `482`, `894` | `ToDo` sweep, recursive descendant walk, parent lookup, status UPDATE, two INSERTs, open-question join | No change; INSERTs omit `id` and rely on the default. | Relation + predicate; supply `source_type`. |
| J2 | `bin/compiler_processInProgress.py` | `92`, `107`, `119`, `121`, `138`, `164`, `228`–`229` | `InProgress` sweep, hierarchy walk, count, status UPDATE, `work_request_dco` read and UPDATE | No change; `work_request_dco` is a real canonical column after V222, so the recompile gate (`work_request_dco IS NOT NULL`) keeps its meaning. | Relation only. |
| J3 | `python/tackle/intent_requirement_promote.py` | `176`, `212` | idempotency `SELECT` by `candidate_id`, promotion INSERT | No change. | Relation + predicate; INSERT must set `source_type` and `compilation_status`. |
| J4 | `python/tackle/resolve_requirements.py` | `31`, `57` | `UPDATE ... SET status='Done'`, status aggregate | No change; the update goes through the write-through trigger, which replaces the legacy history-row split. | Relation; make the bitemporal split explicit only if it is still required. |
| J5 | `python/tackle/req_compiler.py` | `131`, `151` | requirement + hierarchy read, cross-reference title subselect | No change. | Relation + predicate (two sites). |
| J6 | `python/tackle/planner_mcp_server.py` | `196`, `708`, `751`, `888` | `Done` sweep, similarity search, hierarchy lookup, planner reads (4 sites) | No change. | Relation + predicate. |
| J7 | `python/tackle/harness/analyst.py` | `272`, `273`, `560` | analyst requirement read, two cross-reference title subselects, sweep | No change. (`84` is prompt prose, not SQL.) | Relation + predicate. |
| J8 | `python/tackle/harness/architect.py` | `100` | `ToDo` sweep | No change. | Relation + predicate. |
| J9 | `python/cascade/evaluators/kg_impact.py` | `47` | `SELECT ... WHERE candidate_id = %s::uuid` | No change; the cross-domain candidate id is preserved by value. | Relation + predicate. |
| J10 | `python/nebula/wc_consumers.py` | `1705`–`1707` (registry metadata at `19`, `133`, `1032`, `1056`) | read-only census aggregates: total, candidate-linked, plan-linked | No change; counts are over current rows exactly as before. | Relation (keep the predicate). |
| J11 | `bin/transcript_rebuild.py` | `18`, `88`, `116`–`126` | **unconditional `DELETE FROM nebula.requirements`** in `wipe_pg()`, one table in a `BEGIN … COMMIT` allowlist | **Behaviour change.** Each row-delete now resolves through the `INSTEAD OF DELETE` branch into the canonical guard. If any requirement is named by a spec, plan, segment set, request link, work request, child or live blueprint, the statement raises `P4A01` and the whole wipe rolls back. Before V222 the same job always succeeded. The dry-run counts still work. | Either run it only after those references are retired, or repoint the requirements step at `resolution.delete_requirements(ids)` leaf-first and treat a refusal as a reported stop rather than a crash. |
| J12 | `legacy/bin/substance_segment_backfill.py` | `100`, `243` (+ INSERT into `requirement_segment_sets` at `235`–`249`) | requirement-by-candidate read, `valid_until > now()` filter, segment-set backfill | No change; the projection already hides closed segments, so the legacy filter is redundant but harmless (sentinel preserved). | Relation; drop the redundant filter. |
| J13 | `scripts/bash/restore-nebula.sh` | `85` | post-restore `SELECT count(*) FROM nebula.requirements` | No change as a verification query. **But** restoring a pre-V222 dump recreates the old view and silently un-ports the surface. | Re-apply V222 after any restore that predates the port; assert the trigger exists (`pg_trigger`), not just the row count. |

No scheduler references exist for J1–J13 in `systemd/`, `systemd-user/`,
`config/` or the `Makefile`; they are operator- or agent-invoked.

---

## 3. Database objects that name the relation

`resolution.requirement` is also the storage behind the executable `Requirement`
representation registered under V216/V217 (`argon-ten-turns.md:679`); V222 is what
makes that representation's table non-empty. The objects below are the ones that
still name the legacy relation.

| # | Object | Defined in | Operation | Transition | Work required |
|---|---|---|---|---|---|
| D1 | `nebula.assess_ripple(uuid)` and `nebula.assess_ripple_batch(uuid[])` | `typescript/nebula-srv/migrations/038-ripple-assessment.sql` | `STABLE` plpgsql: target row, direct children, recursive descendant walk, hierarchy join | No change; the projection preserves current-segment visibility, which is what the function assumes. | `CREATE OR REPLACE` against `resolution.requirement` with the predicate — bodies are invisible to `pg_depend`, so only `pg_proc.prosrc` finds this. |
| D2 | `nebula.v_requirements_with_questions` | migration `035-open-questions-and-agendas.sql` | view over the projection | Replacing `nebula.requirements` with `CREATE OR REPLACE VIEW` keeps dependent views valid (same 22 columns, same order). | Recreate over the base table before the drop. |
| D3 | `nebula.v_requirements_full_status` | migration `036-open-questions-rollup-and-status-flow.sql` (`331`–`334`) | view with child counts | Same as D2. | Same as D2. |
| D4 | `nebula.v_work_request_requirement` | `sql/V221__ac11_drop_source_requirement_shortcut.sql` (`80`) | normalized request→requirement projection with hierarchy | Same as D2. | Same as D2. |
| D5 | `nebula.requirements_insert_trigger` / `_update_trigger` / `_delete_trigger` | `migrations/scd-type4-bitemporal-upgrade.sql` (`493`, `797`, `1102`) | legacy history-table writers | Inert: the bootstrap records them as unattached residue with no trigger binding, and V222 installs its own `trg_requirements_compat_write` on the view. Do **not** re-bind them to the projection. | Retire with the history table's ownership review. |

The runbook §8 notes that on a fuller target these views plus the routine are
what the `pg_depend` / `pg_proc` enumeration prints; that enumeration was not
re-run here (§9).

---

## 4. UI and API clients (no SQL)

| # | Consumer | Path | Transition |
|---|---|---|---|
| U1 | `angular/nexus-console` — Nebula panel | `src/services/nebula.service.ts` (`84`, `129`, `155`–`158`, `211`–`212`) calls `GET /api/requirements?limit=500` and `PATCH /api/requirements/:id`; `components/nebula-panel/nebula-panel.component.ts:67` exposes the signal | None at apply. It follows S1; the response shape is unchanged because the API's `RETURNING *` and selects still see 22 columns. No UI change is needed before the drop either, as long as S1's JSON shape is preserved. |
| U2 | `typescript/nebula-mcp` — MCP tools | `src/api/nebulaClient.ts` (`108`–`142`): list, create, patch, delete, batch, move | None at apply. One behavioural note: `DELETE /api/requirements/:id` can now answer 409 with `blockers`; the MCP client surfaces the HTTP error, and tool callers should expect the new conflict body. |
| U3 | `angular/surface-ui` | only `NEBULA-API-SPEC.md` (`1840`, `1907`) | Documentation only; no code consumer exists in this worktree. |

---

## 5. Documentation and contracts to update with S1

Not consumers, but they assert the relation's identity and will be wrong after
the drop (not after the apply):

- `typescript/nebula-srv/API-SPEC.md` (`911`, `1775`, `1839`)
- `angular/surface-ui/NEBULA-API-SPEC.md` (`1840`, `1907`)
- `docs/open-questions-schema.md` (`163`)
- `docs/BITEMPORAL-API-CHANGES.md` (`180`)
- `docs/cpf-ui-hook.md` (`102`)
- `docs/events/events-inventory-v2-raw-observations.md` (`65`–`66`, history columns)
- `docs/promotion-flow/w-c-consumer-migration.md` and `w-c-consumer-inventory.json`
  (`candidate_id` / `conduit_plan_id` surfaces)
- `docs/T01-baseline-v2-2026-08-04.md` (`90`)
- `adonisjs/nexus-control-edge/contracts/openapi.yaml` (`4336`) and
  `typespec/v1/control-edge/typescript/operations.tsp` (`505`) — batch-update
  contract wording
- `docs/runbook-v222-requirements-port-live-apply.md` — §8 inventory

---

## 6. Not consumers (checked, so they are not re-investigated)

- `scripts/sql/nebula-backup-20260618-013558.sql`,
  `scripts/sql/nebula-pre-scd4-backup.sql` — dated backup artifacts.
- `sql/ci-bootstrap/nexus-ci-bootstrap.sql`, `sql/ci-bootstrap/refresh.sh`,
  `schemas/migrations/resolution/resolution.sql` — schema build inputs. The CI
  bootstrap still creates the legacy view and `requirements_history`; throwaway
  databases get V222 applied on top.
- Historical migrations `018`, `028`, `035`, `036`, `038`,
  `scd-type4-*` — applied history. Only the objects they create matter (D1–D5).
- `audit/`, `audit/PLANS/completed/0082-*` — completed-plan prose.
- `typescript/conduit-mcp/migrations/verify-receipt-integrity.js` (`11`) —
  comment describing the audit in S10.
- `docs/runbook…` §9 rollback scripts — operate on the port itself, not a
  consumer.

---

## 7. Hazards specific to this transition

1. **The rebuild job is the only unconditional delete.** `bin/transcript_rebuild.py`
   will roll back its entire wipe if a ported requirement is referenced. This is
   the one place where "works before, fails after" is a real regression rather
   than a stricter error the caller already handles.
2. **Scripts do not map the 409.** S1/S3/S8/S9 return
   `{ error, blockers }`; J1–J13 would surface `P4A01` as an exception. That is
   acceptable for one-shot jobs, but J11 must treat it as a stop, not a crash
   mid-corpus.
3. **Restore un-ports silently.** `scripts/bash/restore-nebula.sh` verifies only
   `count(*)`, which still returns the ported rows from a post-port dump and the
   legacy rows from a pre-port one. Assert
   `to_regclass('resolution.requirement_port_ledger')` and the compat trigger.
4. **Counts are view scans.** Every consumer that counts requirements now
   filters `resolution.requirement` by the two segment predicates. Indexes on
   `recorded_on_dt`/`recorded_until_dt` and `valid_from`/`valid_until` were not
   verified here; confirm before applying on a large target.
5. **`requirements_history` readers would go stale.** None exist in runtime
   code today, but migration `036`'s functions and `audit/` prose still name it.
   Treat any future history read as pre-port-only.
6. **Cross-domain links are preserved, not resolved.** `candidate_id` and
   `asset_id` keep their values; the candidate FK is dropped when
   `resolution.candidate` is empty, and the asset link must be satisfiable
   before the port or V222 refuses by name (`V222-PREFLIGHT-018`).
7. **The delete guard reads a column V221 removes.**
   `resolution.requirement_delete_blockers()` treats
   `resolution.work_request.source_requirement_id` as a blocker
   (`sql/V222__port_requirements_to_resolution.sql:712`–`:716`), but V221 drops
   that column (`sql/V221__ac11_drop_source_requirement_shortcut.sql:104`) and
   asserts its absence (`:247`). Applied in the plan-of-record order (V221 first,
   per `argon-ten-turns.md:449`–`:461`), every delete pre-check raises
   `column w.source_requirement_id does not exist` instead of returning blockers
   — a 500 on every delete and cascade, not a 409. The V222 fixture recreates the
   column on purpose (`bin/tests/test_v222_requirements_port.py:221`–`:226`), so
   the suite cannot catch it. A staged fix is prepared in
   `sql/patches/V222-v221-safe-delete-guard.patch` (NOT applied); the order test
   `test_delete_guard_is_v221_safe` is xfailed until it lands. Apply the patch —
   or an equivalent reading of `resolution.request_requirement_link`
   (`relation = 'source'`) — before any database where V221 has run.

---

## 8. Disposition before the projection can be dropped

The runbook §8 drop condition stands. Restated as a checklist over this
inventory:

- [ ] S1–S10 read `resolution.requirement` with the current-segment predicate.
- [ ] J1–J13 read/write the base table; J11 deletes through
      `resolution.delete_requirements` and reports refusals.
- [ ] D1–D4 are recreated over the base table; D5 stays unbound.
- [ ] U1/U2 verified against S1's unchanged JSON shape.
- [ ] §5 documentation updated in the same change.
- [ ] `pg_depend` returns no dependent views and `pg_proc.prosrc` returns no
      routine naming the projection.
- [ ] `rg` finds no runtime reference outside `sql/V222`, tests and docs.
- [ ] Runbook §7 post-apply verification still passes on the target.
- [ ] Follow-up migration drops `trg_requirements_compat_write` and the view in
      one transaction, keeping `requirements_history`, the port ledger and the
      delete guard.

---

## 9. Verification performed, and not performed

Performed: static enumeration and statement-by-statement classification of
every match in the worktree at `f78443b45`; file existence and line references
checked against the working tree; the V222 view/trigger definitions read in
full.

Not performed: no database connection, so the live `pg_depend` / `pg_proc`
enumeration, actual row counts, the dependent-view list, and index coverage
remain unverified. V222 was not applied to any database, live or throwaway, for
this inventory. Nothing was staged or committed.
