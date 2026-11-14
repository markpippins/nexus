# V218 live-apply runbook — assessment family and decision identity link (operator-gated)

> **Status:** procedure only. This document does **not** authorize an apply.
> Live application requires an explicit operator decision naming this runbook.
>
> **Scope:** Phases 4–5 only — `sql/V218__assessment_family_and_decision_link.sql`.
> V216, V217 and Phase 2/3 work are out of scope and must not be re-applied here.
>
> **Migration character:** transactional DDL plus integrity objects. V218 adds
> assessment-family columns, per-kind checks, one partial unique index, one
> agent-record link trigger, a rebuilt 19-column `nebula.assessments` view,
> and one nullable `peb.decisions.agent_record_id` column. It performs **no
> backfill**, moves no table, changes no writer, creates no link table, and
> does not touch V170.

## Provenance

| Item | Value |
|---|---|
| Design note | `docs/nebula-sol-absorption.md` §1, §6 Phase 4/5 rows, §8.1–8.2 |
| Migration | `sql/V218__assessment_family_and_decision_link.sql` |
| Merged PR | **#986**, merge commit `8991553dd09899d43eeb6c5fc1756d1665179173` |
| Migration blob | `0964d20cd1d764b4af877074c79682b7bfb83695` |
| Architect ratification | `e3f39f9a-1a5e-4168-8481-74a80235e41b` — RATIFIED_WITH_AMENDMENTS |
| DBA ratification | `f170e343-2464-4b1a-9811-ae6bc6628def` — APPROVED_WITH_AMENDMENTS |
| Reconciliation ruling | `f72817f5-3ff9-4634-8625-a97476678d90` |
| Focused guard | `bin/tests/test_v218_assessment_family.py` — 14 tests |
| Base commit for this procedure | `f41004fa5e0f5d7eaf2258732c874c441b945a45` |
| Expected ledger row | `resolution.migration_ledger`, `schema_name='sql'`, `migration_label='V218__assessment_family_and_decision_link'` |

The ratifications authorize the staged artifact. They do **not** authorize live
application. The operator authorization is a separate gate.

---

## 0. Authority, roles and stop conditions

| Role | Responsibility |
|---|---|
| **Operator** | Opens the window, approves the apply, owns the R9 replication decision |
| **DBA** | Verifies preflight, executes or directly supervises the apply, writes the ledger row |
| **Engineer** | Produces evidence, runs the guard and post-apply checks, records the result |

**Stop immediately and do not apply** if any of the following is true:

- the migration blob is not `0964d20cd1d764b4af877074c79682b7bfb83695`;
- the V218 merge commit is not an ancestor of the checkout;
- any ratification record is absent, does not name V218, or is not an approval/ruling;
- any pre-state query in §1.3 differs from the expected shape and the DBA has not accepted that difference before the window;
- V218 is already present in `resolution.migration_ledger`;
- any Phase 4/5 column, constraint, index, trigger, function or view change is already present;
- `nebula.assessments_history` is populated;
- `nebula.assessments` has drifted from the expected 15-column shape;
- `nebula.agent_records_history` is missing;
- the verified backup or targeted affected-table snapshot is missing;
- another migration is in flight, or a service is writing the affected tables in a way the DBA has not accounted for;
- any `psql` command exits non-zero.

An unexplained difference is a stop, not a judgement call to make alone.

---

## 1. Preflight

Run from a clean checkout at the approved commit. Do not apply from a dirty tree.

Recommended clean checkout:

```bash
cd /home/codex/dev/nexus
git fetch origin
git worktree add --detach /home/codex/dev/nexus-worktrees/v218-apply main
cd /home/codex/dev/nexus-worktrees/v218-apply
```

If the worktree already exists, verify it is at the approved commit.

### 1.1 Exact artifact

```bash
cd /home/codex/dev/nexus-worktrees/v218-apply

git merge-base --is-ancestor \
  8991553dd09899d43eeb6c5fc1756d1665179173 HEAD \
  && echo "V218 merge commit is in HEAD"

test "$(git hash-object sql/V218__assessment_family_and_decision_link.sql)" \
     = "0964d20cd1d764b4af877074c79682b7bfb83695" \
  && echo "V218 blob matches the approved merge"

git status --short -- sql docs bin/tests bin/tests-ci-manifest.json typespec
```

Expected: both success lines; no relevant working-tree changes.

### 1.2 Ratification is present and exact

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -F '|' -c "
SELECT id, role, record_type
FROM nebula.agent_records
WHERE id IN (
  'e3f39f9a-1a5e-4168-8481-74a80235e41b',
  'f170e343-2464-4b1a-9811-ae6bc6628def',
  'f72817f5-3ff9-4634-8625-a97476678d90'
)
ORDER BY id;
"
```

Expected: three rows with roles `architect`, `dba`, and `architect`.
Read all three records; an ID match alone is not ratification. The records
must support the staged V218 artifact, including D1 as authored and D2 as
index-only.

### 1.3 Live pre-state

These are the expected pre-apply values observed on **2026-11-14**. Re-capture
them at apply time; do not treat the baseline as an invariant.

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -v ON_ERROR_STOP=1 -At -F '|' -c "
SELECT 'v218_ledger', count(*)
FROM resolution.migration_ledger
WHERE schema_name = 'sql'
  AND migration_label = 'V218__assessment_family_and_decision_link'
UNION ALL
SELECT 'assess_history_rows', count(*) FROM nebula.assessments_history
UNION ALL
SELECT 'peb_decision_rows', count(*) FROM peb.decisions
UNION ALL
SELECT 'new_cols', count(*)
FROM information_schema.columns
WHERE (
        table_schema = 'nebula' AND table_name = 'assessments_history'
        OR table_schema = 'peb' AND table_name = 'decisions'
      )
  AND column_name IN ('assessment_kind', 'subject', 'evidence', 'agent_record_id')
UNION ALL
SELECT 'assess_history_cols', count(*)
FROM information_schema.columns
WHERE table_schema = 'nebula' AND table_name = 'assessments_history'
UNION ALL
SELECT 'decisions_cols', count(*)
FROM information_schema.columns
WHERE table_schema = 'peb' AND table_name = 'decisions';
"
```

Expected pre-state:

| Check | Expected |
|---|---:|
| `v218_ledger` | 0 |
| `assess_history_rows` | 0 |
| `peb_decision_rows` | 16 |
| `new_cols` | 0 |
| `assess_history_cols` | 15 |
| `decisions_cols` | 14 |

Confirm the view shape is exactly the ratified 15-column shape:

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -c "
SELECT string_agg(column_name, ',' ORDER BY ordinal_position)
FROM information_schema.columns
WHERE table_schema = 'nebula'
  AND table_name = 'assessments';
"
```

Expected:

```text
id,observation_id,outcome,confidence,impact_scope,open_questions,agenda_id,auto_resolve_plan_id,analysis_detail,created_at,forum_post_id,valid_from,valid_until,recorded_on_dt,recorded_until_dt
```

Confirm the unaffected evaluation contract is still intact:

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -F '|' -c "
SELECT 'executable_representations', count(*)
FROM resolution.representation
WHERE evaluation_role = 'executable'
UNION ALL
SELECT 'attribute_bindings', count(*) FROM resolution.concept_attribute_binding
UNION ALL
SELECT 'relationship_bindings', count(*) FROM resolution.concept_relationship_binding;
"
```

Expected on the 2026-11-14 baseline: `6`, `7`, `6`. Any drift must be
explained and accepted before the window; V218 must not be used to repair or
re-mark unrelated registry state.

### 1.4 Backup

Two artifacts are required: the normal verified full backup, and a targeted
snapshot of the affected tables plus the ledger row table.

```bash
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT=/home/codex/backups/nexus-pg/v218-$STAMP
TMP_LOG=/tmp/v218-full-backup-$STAMP.log

# Do not pre-create the backup directory: the backup tier's drive guard must
# decide whether the destination exists and is usable.
df -h /home/codex/backups/nexus-pg

# 1) The project's verified full backup.
PGPASSWORD=pgpass /home/codex/dev/nexus/bin/pg-escape-hatch.sh \
  2>&1 | tee "$TMP_LOG"

# Only after the drive guard and dump succeed, create the window directory.
install -d "$OUT"
mv "$TMP_LOG" "$OUT/full-backup.log"

# Preserve the verified dump in the window directory.
FULL_DUMP=$(python3 -c '
import json
print("/home/codex/backups/nexus-pg/" + json.load(open("/home/codex/backups/nexus-pg/last-backup.json"))["file"])
')
test -f "$FULL_DUMP"
cp --reflink=auto "$FULL_DUMP" "$OUT/full-pre.dump"
sha256sum "$OUT/full-pre.dump" | tee "$OUT/full-pre.sha256"
pg_restore -l "$OUT/full-pre.dump" > "$OUT/full-pre.toc"
test -s "$OUT/full-pre.toc"

# 2) Targeted, restorable snapshot of the affected tables and ledger table.
PGPASSWORD=pgpass pg_dump \
  -h localhost -U pguser -d nexus -Fc \
  -t nebula.assessments_history \
  -t nebula.assessments \
  -t nebula.agent_records_history \
  -t peb.decisions \
  -t resolution.migration_ledger \
  -f "$OUT/v218-pre.dump"

pg_restore -l "$OUT/v218-pre.dump" > "$OUT/v218-pre.toc"
test -s "$OUT/v218-pre.toc"
sha256sum "$OUT/v218-pre.dump" | tee "$OUT/v218-pre.sha256"
```

Expected: the full-backup log ends with the project's verified-dump success
line, and the targeted TOC lists all five relations. Preserve `$OUT` until at
least two clean backup cycles have passed and the DBA has signed off.

### 1.5 Guard and scratch rehearsal

At minimum, run the focused guard. It creates throwaway databases, applies
V218, proves refusal on re-apply and populated tables, and asserts the
AC1–AC6 behavior contract.

```bash
cd /home/codex/dev/nexus-worktrees/v218-apply
PGPASSWORD=pgpass python3 -m pytest \
  bin/tests/test_v218_assessment_family.py -q \
  2>&1 | tee "$OUT/v218-guard.log"
test "${PIPESTATUS[0]}" -eq 0
```

Expected: `14 passed`.

Stronger rehearsal against the live affected-table shape:

```bash
SCRATCH="v218_rehearsal_$STAMP"
createdb -h localhost -U pguser "$SCRATCH"

PGPASSWORD=pgpass psql -X -h localhost -U pguser -d "$SCRATCH" \
  -v ON_ERROR_STOP=1 -q \
  -c 'CREATE EXTENSION IF NOT EXISTS pgcrypto;' \
  -c 'CREATE SCHEMA IF NOT EXISTS nebula;' \
  -c 'CREATE SCHEMA IF NOT EXISTS peb;' \
  -c 'CREATE SCHEMA IF NOT EXISTS resolution;'

PGPASSWORD=pgpass pg_dump -h localhost -U pguser -d nexus -Fc -s \
  -t nebula.assessments_history \
  -t nebula.assessments \
  -t nebula.agent_records_history \
  -t peb.decisions \
  -f "$OUT/v218-shape.dump"

# The schema-only restore must omit live audit TRIGGER TOC entries: they call
# tackle audit functions outside the affected-table snapshot, and V218 does
# not depend on them.
pg_restore -l "$OUT/v218-shape.dump" | grep -v ' TRIGGER ' > "$OUT/v218-shape.list"
PGPASSWORD=pgpass pg_restore -h localhost -U pguser -d "$SCRATCH" \
  --no-owner --no-privileges -L "$OUT/v218-shape.list" "$OUT/v218-shape.dump"

PGPASSWORD=pgpass python3 bin/rehearse-migration.py \
  sql/V218__assessment_family_and_decision_link.sql \
  --dsn "postgresql://pguser@localhost:5432/$SCRATCH" \
  --allow-self-transaction \
  2>&1 | tee "$OUT/v218-rehearsal.log"
test "${PIPESTATUS[0]}" -eq 0

# The helper rolls back; prove there is no residue.
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d "$SCRATCH" -At -F '|' -c "
SELECT 'new_cols', count(*)
FROM information_schema.columns
WHERE (
        table_schema = 'nebula' AND table_name = 'assessments_history'
        OR table_schema = 'peb' AND table_name = 'decisions'
      )
  AND column_name IN ('assessment_kind', 'subject', 'evidence', 'agent_record_id');
"

PGPASSWORD=pgpass dropdb -h localhost -U pguser "$SCRATCH"
```

Expected: the helper reports that it stripped the migration's two transaction
statements, runs `BEGIN; <migration> ROLLBACK;`, shows the DDL sequence, ends
with `ROLLBACK`, and the post-rollback `new_cols` count is `0`.

### 1.6 Go/no-go

Record in the window record:

- operator approval and window start;
- Architect/DBA/reconciliation record IDs and the exact blob hash;
- pre-state, view shape, and evaluation-contract counts;
- full-backup path, size, sha256, and TOC result;
- targeted snapshot path, TOC result, and sha256;
- guard and rehearsal results;
- the names of the operator, DBA and engineer on duty.

Only then proceed to §2.

---

## 2. Apply

The file manages its own transaction (`BEGIN; ... COMMIT;`). Run it once with
`ON_ERROR_STOP=1`; do not wrap it in another `BEGIN/COMMIT`, and do not use
the rehearsal helper for the live apply.

```bash
cd /home/codex/dev/nexus-worktrees/v218-apply
set -o pipefail
export PGPASSWORD=pgpass

PGAPPNAME=v218-live-apply \
PGOPTIONS='-c lock_timeout=5s -c statement_timeout=60s' \
  psql -X -h localhost -U pguser -d nexus -v ON_ERROR_STOP=1 \
  -f sql/V218__assessment_family_and_decision_link.sql \
  2>&1 | tee "$OUT/apply.log"

rc=${PIPESTATUS[0]}
echo "psql_exit=$rc" | tee -a "$OUT/apply.log"
test "$rc" -eq 0
```

Expected output sequence:

```text
DO
ALTER TABLE
ALTER TABLE
ALTER TABLE
CREATE INDEX
CREATE FUNCTION
CREATE TRIGGER
CREATE VIEW
ALTER TABLE
COMMIT
```

If `psql` exits non-zero, stop: the transaction did not commit. Re-run §1.3
and confirm the pre-state; do **not** insert a ledger row. Do not re-run the
migration on live merely because a retry feels convenient; idempotency is
proven in the guard and rehearsal, not by repeated live attempts.

---

## 3. Post-apply verification

Run every query before writing the ledger. Any mismatch is a rollback trigger
(§5).

### 3.1 New columns and view shape

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -F '|' -c "
SELECT 'history_cols', count(*)
FROM information_schema.columns
WHERE table_schema = 'nebula' AND table_name = 'assessments_history'
UNION ALL
SELECT 'decisions_cols', count(*)
FROM information_schema.columns
WHERE table_schema = 'peb' AND table_name = 'decisions'
UNION ALL
SELECT 'new_cols', count(*)
FROM information_schema.columns
WHERE (
        table_schema = 'nebula' AND table_name = 'assessments_history'
        OR table_schema = 'peb' AND table_name = 'decisions'
      )
  AND column_name IN ('assessment_kind', 'subject', 'evidence', 'agent_record_id');
"
```

Expected: `history_cols=19`, `decisions_cols=15`, `new_cols=5`.

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -c "
SELECT string_agg(column_name, ',' ORDER BY ordinal_position)
FROM information_schema.columns
WHERE table_schema = 'nebula'
  AND table_name = 'assessments';
"
```

Expected: the original 15 columns in their original order, followed by
`assessment_kind,subject,evidence,agent_record_id`.

### 3.2 Integrity objects

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -F '|' -c "
SELECT 'kind_checks', count(*)
FROM pg_constraint
WHERE conrelid = 'nebula.assessments_history'::regclass
  AND conname IN (
    'assessments_assessment_kind_check',
    'assessments_kind_shape_check'
  )
UNION ALL
SELECT 'single_writer_index', count(*)
FROM pg_indexes
WHERE schemaname = 'nebula'
  AND tablename = 'assessments_history'
  AND indexname = 'assessments_single_writer_idx'
UNION ALL
SELECT 'link_trigger', count(*)
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'nebula'
  AND c.relname = 'assessments_history'
  AND t.tgname = 'trg_assessments_agent_record_link'
UNION ALL
SELECT 'writer_role_trigger', count(*)
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'nebula'
  AND c.relname = 'assessments_history'
  AND t.tgname = 'trg_assessments_single_writer';
"
```

Expected: `kind_checks=2`, `single_writer_index=1`, `link_trigger=1`,
`writer_role_trigger=0`.

### 3.3 Empty link/backfill proof

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -F '|' -c "
SELECT 'history_rows', count(*) FROM nebula.assessments_history
UNION ALL
SELECT 'history_non_null_links', count(*)
FROM nebula.assessments_history
WHERE agent_record_id IS NOT NULL
UNION ALL
SELECT 'decisions_rows', count(*) FROM peb.decisions
UNION ALL
SELECT 'decisions_non_null_links', count(*)
FROM peb.decisions
WHERE agent_record_id IS NOT NULL;
"
```

Expected: `history_rows=0`, `history_non_null_links=0`, `decisions_rows=16`,
`decisions_non_null_links=0`.

### 3.4 Behavior proof

Re-run the focused guard after apply; it uses throwaway databases and does not
re-apply live.

```bash
cd /home/codex/dev/nexus-worktrees/v218-apply
PGPASSWORD=pgpass python3 -m pytest \
  bin/tests/test_v218_assessment_family.py -q \
  2>&1 | tee "$OUT/post-v218-guard.log"
test "${PIPESTATUS[0]}" -eq 0
```

Expected: `14 passed`.

### 3.5 Services

V218 changes DDL and integrity objects, not service code. Spot-check the
services that read the affected tables and the backend fleet:

```bash
systemctl --user is-active \
  nebula-srv.service semantics-srv.service resolution-srv.service peb-srv.service
curl -fsS http://localhost:3101/health
/home/codex/dev/nexus/bin/start-nexus-services.sh status | tail -10
```

Expected: all listed user-manager units `active`, the health request succeeds,
and the status table has no new failure attributable to the apply. Restart a
service only if it owns a stale prepared statement, cached plan, or schema
cache; do not restart the fleet as part of this runbook.

---

## 4. Ledger and closeout

Only after §3 is fully green, the DBA writes the ledger. The ledger row is the
closeout commit point; do not write it before verification.

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -v ON_ERROR_STOP=1 -c "
INSERT INTO resolution.migration_ledger
  (schema_name, migration_label, description, applied_by, applied_at)
SELECT
  'sql',
  'V218__assessment_family_and_decision_link',
  'V218 (docs/nebula-sol-absorption.md Phases 4-5; PR #986 merge 8991553dd09899d43eeb6c5fc1756d1665179173; blob 0964d20cd1d764b4af877074c79682b7bfb83695; Architect e3f39f9a-1a5e-4168-8481-74a80235e41b; DBA f170e343-2464-4b1a-9811-ae6bc6628def; reconciliation f72817f5-3ff9-4634-8625-a97476678d90): assessment-family DDL plus nullable peb.decisions.agent_record_id. No backfill, no link table, no writer change. Applied by operator-directed DBA apply in window <WINDOW>; pre/post counts recorded; V218 residue verified absent from rehearsal scratch.',
  'dba',
  now()
WHERE NOT EXISTS (
  SELECT 1 FROM resolution.migration_ledger
  WHERE schema_name = 'sql'
    AND migration_label = 'V218__assessment_family_and_decision_link'
);
"
```

Verify exactly one row:

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -F '|' -c "
SELECT schema_name, migration_label, applied_by, applied_at
FROM resolution.migration_ledger
WHERE schema_name = 'sql'
  AND migration_label = 'V218__assessment_family_and_decision_link';
"
```

Then write the window records:

```bash
python3 bin/post-agent-record.py \
  -r dba \
  --record-type engineering_log \
  --level 2 --visibility all \
  --tags 'to:architect,to:engineer,to:operator,type:milestone,planRef:nebula-sol-absorption,phase:4-5,v218,live-apply' \
  -t "V218 live apply COMPLETE: assessment-family DDL and empty decision link live" \
  -c "Window <WINDOW>. Architect e3f39f9a-1a5e-4168-8481-74a80235e41b; DBA f170e343-2464-4b1a-9811-ae6bc6628def; reconciliation f72817f5-3ff9-4634-8625-a97476678d90. Apply log <OUT>/apply.log exit 0. Post-apply: history 19 columns, decisions 15 columns, 2 kind checks, 1 partial unique index, link trigger present, writer-role trigger absent, 0 populated history rows, 0 non-null decision links. Ledger row written; V170 remains withdrawn."

bash bin/post-change-log.sh \
  --role dba --model "$NEXUS_AGENT_MODEL" \
  -t 'V218 applied: assessment family and decision identity link (staged-inert → live)' \
  -b 'Applied sql/V218__assessment_family_and_decision_link.sql per docs/runbook-v218-assessment-family-live-apply.md. PR #986 merge 8991553dd; blob 0964d20c…; Architect e3f39f9a; DBA f170e343; reconciliation f72817f5. DDL only: assessment_kind/subject/evidence/agent_record_id, per-kind checks, single-writer partial unique index, agent-record link trigger, rebuilt 19-column assessments view, nullable peb.decisions.agent_record_id. No backfill, no link table, no writer change. Ledger row written; backup <OUT> retained.'
```

Finally, the operator makes the **R9 replication decision**: ask whether the
schema change should be replicated to the canonical off-machine target
(currently **vanadium**). Do not replicate automatically. If approved, use the
existing PG backup/sync path, verify the target columns/ledger, and record the
result against this window.

Retain `$OUT` and the verified full backup until at least two clean backup
cycles have passed and the DBA has signed off.

---

## 5. Rollback

If the apply transaction fails, PostgreSQL rolls it back automatically.
Re-check §1.3 and do not write the ledger.

If a committed V218 must be withdrawn, do not drop columns, constraints,
indexes, triggers, functions, or views ad hoc: the rebuilt view and link
trigger have live readers and bitemporal audit dependencies. Restore
`$OUT/full-pre.dump` to a replacement database under DBA supervision and
repoint services using the DBA cutover procedure. Record the restore, the
replacement database, and the verification in the incident/window record.

Do not use `DROP`/`TRUNCATE` on the live registry or assessment tables as a
shortcut. Do not roll back merely because a later migration remains unapplied;
that is an intended staged state.

---

## 6. What this does not do

- It does **not** apply or re-apply V216/V217.
- It does **not** backfill `nebula.assessments_history` or populate any `agent_record_id`.
- It does **not** write any of the 14 title-matched ADR/agent-record pairs; title equality is not identity proof.
- It does **not** create a link table, move `peb.decisions`, change decision authority, or change a writer/role.
- It does **not** make an assessment representation executable; executable marking remains Phase 3 state.
- It does **not** apply `vision.canonical_work_requests` (V170).
- It does **not** replicate to vanadium.

---

## 7. Preparation dry-run — 2026-11-14

This runbook was prepared without touching live state.

- Focused guard: `14 passed in 3.20s` on `bin/tests/test_v218_assessment_family.py`.
  The guard creates fresh throwaway databases, applies V218 once, refuses a
  second apply and a populated-table apply, and checks the 19-column view,
  kind shape, single-writer index, writer-trigger absence, and D1 link rules.
- Scratch rehearsal: the migration's two transaction statements were stripped
  (`BEGIN` line 61, `COMMIT` line 238); `bin/rehearse-migration.py` ran
  `BEGIN; <migration> ROLLBACK;` against a fresh disposable database seeded
  with the live schema-only affected-table shape. Output ended in `ROLLBACK`,
  and the post-rollback new-column count was `0`. The scratch database was
  dropped.
- Live pre-state observed while preparing this runbook: V218 ledger `0`,
  `nebula.assessments_history` rows `0`, `peb.decisions` rows `16`, new Phase
  4/5 columns `0`, history columns `15`, decisions columns `14`, executable
  representations `6`, attribute bindings `7`, relationship bindings `6`.

No live apply, ledger write, service restart, or replication was performed.
