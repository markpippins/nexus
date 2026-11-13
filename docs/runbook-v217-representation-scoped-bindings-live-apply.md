# V217 live-apply runbook — executable representations (operator-gated)

> **Status:** procedure only. Live application requires the operator's explicit
> authorization for this window.
>
> **Scope:** Phase 3 only — `sql/V217__representation_scoped_bindings.sql`.
> V218, Phase 4 and Phase 5 are out of scope and must not be applied here.
>
> **Migration character:** transactional DDL plus a deterministic backfill.
> V217 adds the representation selector, representation-scoped binding keys and
> uniqueness, replaces the binding lookup functions, and constrains
> `expression.function_name` to the existing `resolution.function_binding`
> allow-list. It does **not** create domain tables, move data, add Phase 4
> columns, or make a representation executable unless an existing binding
> matches it uniquely.

## Provenance

| Item | Value |
|---|---|
| Design note | `docs/nebula-sol-absorption.md` §6.3 |
| Migration | `sql/V217__representation_scoped_bindings.sql` |
| Merged PR | **#982**, merge commit `7914983a65157f48d8aff0889b6a9e0fa65c6a15` |
| Migration blob | `6f6b453007cb10633c7d25c70ebd7884b75ce49a` |
| DBA ratification | `93d3f092-5e6f-476d-8ec7-41cf587360f9` — APPROVED_WITH_AMENDMENTS |
| Architect ratification | `9b532211-da40-4b52-9729-b273523ff3ab` — RATIFIED_WITH_AMENDMENTS |
| Focused guard | `bin/tests/test_v217_representation_bindings.py` — 13 tests |
| Expected ledger | `resolution.migration_ledger`, `schema_name='sql'`, `migration_label='V217__representation_scoped_bindings'` |

## 0. Stop conditions

Do not apply if any of these is true:

- the V217 blob is not `6f6b453007cb10633c7d25c70ebd7884b75ce49a`;
- V217 is already in `resolution.migration_ledger`;
- `representation.evaluation_role` or either binding `representation_id`
  column already exists;
- the V217 preflight finds any `expression.function_call` whose
  `function_name` is absent from `resolution.function_binding`;
- the verified full backup or the affected-table snapshot is missing or cannot
  be listed;
- the focused guard, Python scope tests, or scratch rehearsal fail;
- a different migration is in flight, or a service is writing the registry in
  a way the DBA has not accounted for;
- any `psql` command exits non-zero.

An unexplained difference is a stop. Do not force the migration.

## 1. Preflight

Use a clean checkout at the approved commit. Do not apply from a dirty tree.

### 1.1 Exact artifact

```bash
cd /home/codex/dev/nexus-worktrees/v217-apply

git merge-base --is-ancestor \
  7914983a65157f48d8aff0889b6a9e0fa65c6a15 HEAD \
  && echo "V217 merge commit is in HEAD"

test "$(git hash-object sql/V217__representation_scoped_bindings.sql)" \
     = "6f6b453007cb10633c7d25c70ebd7884b75ce49a" \
  && echo "V217 blob matches the approved merge"

git status --short -- sql docs python/SOLScript bin/tests bin/tests-ci-manifest.json
```

Expected: both success lines; no relevant working-tree changes.

### 1.2 Ratification and authorization

The DBA and Architect records above must both be present and must name V217.
Read both records; a title match alone is not ratification. The live apply is
authorized only by the operator's explicit current-window authorization; the
ratification records themselves do not grant it.

### 1.3 Live pre-state

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -v ON_ERROR_STOP=1 -At -F '|' -c "
SELECT 'concept', count(*) FROM resolution.concept
UNION ALL SELECT 'representation', count(*) FROM resolution.representation
UNION ALL SELECT 'concept_attribute_binding', count(*) FROM resolution.concept_attribute_binding
UNION ALL SELECT 'concept_relationship_binding', count(*) FROM resolution.concept_relationship_binding
UNION ALL SELECT 'evaluation_role_columns', count(*) FROM information_schema.columns
 WHERE table_schema='resolution' AND table_name='representation'
   AND column_name IN ('evaluation_role','read_adapter')
UNION ALL SELECT 'cab_key_columns', count(*) FROM information_schema.columns
 WHERE table_schema='resolution' AND table_name='concept_attribute_binding'
   AND column_name IN ('id','representation_id')
UNION ALL SELECT 'crb_key_columns', count(*) FROM information_schema.columns
 WHERE table_schema='resolution' AND table_name='concept_relationship_binding'
   AND column_name IN ('id','representation_id')
UNION ALL SELECT 'v217_ledger', count(*) FROM resolution.migration_ledger
 WHERE schema_name='sql' AND migration_label='V217__representation_scoped_bindings';
"
```

Expected on the 2026-11-13 baseline: `concept=44`, `representation=31`,
`concept_attribute_binding=7`, `concept_relationship_binding=6`, both key
column counts `0`, and `v217_ledger=0`.

Confirm the V217 preflight condition is satisfied:

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -c "
SELECT count(*)
FROM resolution.expression e
WHERE e.kind='function_call'
  AND e.function_name IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM resolution.function_binding f
      WHERE f.function_name = e.function_name
  );
"
```

Expected: `0`. If non-zero, stop: register the function with a real template or
remove the call; do not apply V217 over an unregistered function.

Capture the live binding topology for the post-apply comparison:

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -F '|' -c "
SELECT c.name, a.name, b.schema_name, b.table_name, b.column_name
FROM resolution.concept_attribute_binding b
JOIN resolution.concept_attribute a ON a.id=b.attribute_id
JOIN resolution.concept c ON c.id=a.concept_id
ORDER BY 1,2;
" | tee "$OUT/pre-attribute-bindings.txt"

PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -F '|' -c "
SELECT f.name, t.name, b.from_schema, b.from_table, b.to_schema, b.to_table
FROM resolution.concept_relationship_binding b
JOIN resolution.concept_relationship cr ON cr.id=b.concept_relationship_id
JOIN resolution.concept f ON f.id=cr.from_concept_id
JOIN resolution.concept t ON t.id=cr.to_concept_id
ORDER BY 1,2;
" | tee "$OUT/pre-relationship-bindings.txt"
```

### 1.4 Backup

Use the project's verified full backup and preserve a window copy. Also take an
affected-table snapshot so a scratch rehearsal can be restored without the
whole database.

```bash
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT=/home/codex/backups/nexus-pg/v217-$STAMP
TMP_LOG=/tmp/v217-full-backup-$STAMP.log

df -h /home/codex/backups/nexus-pg
PGPASSWORD=pgpass /home/codex/dev/nexus/bin/pg-escape-hatch.sh 2>&1 | tee "$TMP_LOG"

install -d "$OUT"
mv "$TMP_LOG" "$OUT/full-backup.log"
python3 -c '
import json
d=json.load(open("/home/codex/backups/nexus-pg/last-backup.json"))
print("last_backup=", d["last_backup"], "file=", d["file"], "size=", d["size"], "sha256=", d["sha256"])
'

FULL_DUMP=$(python3 -c '
import json
print("/home/codex/backups/nexus-pg/" + json.load(open("/home/codex/backups/nexus-pg/last-backup.json"))["file"])
')
test -f "$FULL_DUMP"
cp --reflink=auto "$FULL_DUMP" "$OUT/full-pre.dump"
sha256sum "$OUT/full-pre.dump" | tee "$OUT/full-pre.sha256"
pg_restore -l "$OUT/full-pre.dump" > "$OUT/full-pre.toc"
test -s "$OUT/full-pre.toc"

PGPASSWORD=pgpass pg_dump -h localhost -U pguser -d nexus -Fc \
  -t resolution.concept \
  -t resolution.concept_attribute \
  -t resolution.concept_attribute_binding \
  -t resolution.concept_relationship \
  -t resolution.concept_relationship_binding \
  -t resolution.representation \
  -t resolution.representation_identity \
  -t resolution.expression \
  -t resolution.expression_operand \
  -t resolution.function_binding \
  -f "$OUT/v217-pre.dump"
pg_restore -l "$OUT/v217-pre.dump" > "$OUT/v217-pre.toc"
test -s "$OUT/v217-pre.toc"
sha256sum "$OUT/v217-pre.dump" | tee "$OUT/v217-pre.sha256"
```

Expected: the full-backup log ends with the verified-dump success line; the
stamp is fresh; both TOCs list all ten affected tables. Preserve `$OUT`.

### 1.5 Guard and scratch rehearsal

```bash
cd /home/codex/dev/nexus-worktrees/v217-apply
PGPASSWORD=pgpass python3 -m pytest bin/tests/test_v217_representation_bindings.py -q \
  2>&1 | tee "$OUT/v217-guard.log"
test "${PIPESTATUS[0]}" -eq 0

cd python/SOLScript
python3 -m pytest tests -q 2>&1 | tee "$OUT/sol-tests.log"
test "${PIPESTATUS[0]}" -eq 0
```

Expected: V217 guard `13 passed`; the SOL suite passes (298 tests on the
2026-11-13 baseline).

Rehearse against the affected-table snapshot:

```bash
SCRATCH="v217_rehearsal_$STAMP"
createdb -h localhost -U pguser "$SCRATCH"
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d "$SCRATCH" \
  -v ON_ERROR_STOP=1 -c 'CREATE SCHEMA IF NOT EXISTS resolution;'
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d "$SCRATCH" \
  -v ON_ERROR_STOP=1 -c 'CREATE SCHEMA IF NOT EXISTS nebula;'
PGPASSWORD=pgpass pg_restore -h localhost -U pguser -d "$SCRATCH" \
  --no-owner --no-privileges "$OUT/v217-pre.dump"

PGPASSWORD=pgpass python3 bin/rehearse-migration.py \
  sql/V217__representation_scoped_bindings.sql \
  --dsn "postgresql://pguser@localhost:5432/$SCRATCH" \
  --allow-self-transaction \
  2>&1 | tee "$OUT/v217-rehearsal.log"
test "${PIPESTATUS[0]}" -eq 0

PGPASSWORD=pgpass psql -X -h localhost -U pguser -d "$SCRATCH" -At -F '|' -c "
SELECT 'concept',count(*) FROM resolution.concept
UNION ALL SELECT 'representation',count(*) FROM resolution.representation
UNION ALL SELECT 'cab',count(*) FROM resolution.concept_attribute_binding
UNION ALL SELECT 'crb',count(*) FROM resolution.concept_relationship_binding
UNION ALL SELECT 'selector_columns',count(*) FROM information_schema.columns
 WHERE table_schema='resolution' AND table_name='representation'
   AND column_name IN ('evaluation_role','read_adapter');
"
PGPASSWORD=pgpass dropdb -h localhost -U pguser "$SCRATCH"
```

Expected: the helper reports `BEGIN; <migration> ROLLBACK;`; post-rollback
counts equal the pre-state; the selector-column count is `0`. Do not apply if
the helper refuses or any count changed.

### 1.6 Go/no-go

Record: operator authorization and window start; DBA/Architect record ids;
exact V217 blob; pre-state and binding topology; full-backup path/size/sha256;
affected snapshot path/sha256; guard and SOL test results; rehearsal result;
on-duty roles; and the rollback owner.

## 2. Apply

V217 manages its own transaction. Run it once with `ON_ERROR_STOP=1`; do not
wrap it in another transaction.

```bash
cd /home/codex/dev/nexus-worktrees/v217-apply
set -o pipefail
export PGPASSWORD=pgpass

PGAPPNAME=v217-live-apply \
PGOPTIONS='-c lock_timeout=5s -c statement_timeout=60s' \
  psql -X -h localhost -U pguser -d nexus -v ON_ERROR_STOP=1 \
  -f sql/V217__representation_scoped_bindings.sql \
  2>&1 | tee "$OUT/apply.log"

rc=${PIPESTATUS[0]}
echo "psql_exit=$rc" | tee -a "$OUT/apply.log"
test "$rc" -eq 0
```

Expected: `ALTER TABLE`, backfill `UPDATE` counts, `CREATE INDEX`, function
`CREATE`/`REPLACE` statements, and `COMMIT`. Do not re-run on the live database.
If `psql` exits non-zero, the transaction did not commit; re-check §1.3 and do
not write the ledger.

## 3. Post-apply verification

Run every check before the ledger. Any mismatch is a rollback trigger.

### 3.1 Schema and keys

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -F '|' -c "
SELECT 'representation_selector', count(*) FROM information_schema.columns
 WHERE table_schema='resolution' AND table_name='representation'
   AND column_name IN ('evaluation_role','read_adapter')
UNION ALL SELECT 'cab_key_columns', count(*) FROM information_schema.columns
 WHERE table_schema='resolution' AND table_name='concept_attribute_binding'
   AND column_name IN ('id','representation_id')
UNION ALL SELECT 'crb_key_columns', count(*) FROM information_schema.columns
 WHERE table_schema='resolution' AND table_name='concept_relationship_binding'
   AND column_name IN ('id','representation_id')
UNION ALL SELECT 'unique_indexes', count(*) FROM pg_indexes
 WHERE schemaname='resolution'
   AND indexname IN ('cab_rep_attr_uq','cab_attr_legacy_uq','crb_rep_rel_uq','crb_rel_legacy_uq')
UNION ALL SELECT 'resolver_functions', count(*) FROM pg_proc p
 JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='resolution'
   AND p.proname IN ('resolve_binding_representation','attribute_binding_for','relationship_binding_for');
"
```

Expected: selector columns `2`, each key-column count `2`, unique indexes `4`,
resolver functions `3`.

### 3.2 Backfill and executable marking

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -F '|' -c "
SELECT c.name, count(*) FILTER (WHERE b.representation_id IS NOT NULL) AS scoped,
       count(*) FILTER (WHERE b.representation_id IS NULL) AS legacy
FROM resolution.concept_attribute_binding b
JOIN resolution.concept_attribute a ON a.id=b.attribute_id
JOIN resolution.concept c ON c.id=a.concept_id
GROUP BY c.name ORDER BY c.name;

SELECT c.name, count(*) FILTER (WHERE r.evaluation_role='executable') AS executable,
       count(*) FILTER (WHERE r.evaluation_role='descriptive') AS descriptive
FROM resolution.representation r
JOIN resolution.concept c ON c.id=r.concept_id
GROUP BY c.name ORDER BY c.name;
"
```

Compare with `pre-attribute-bindings.txt` and
`pre-relationship-bindings.txt`. Expected: only a binding whose
`(schema_name, table_name)` matches exactly one representation is scoped; no
binding is rewritten to a different table/column. `Proposition` and the
`..asset_id` bridge keep the NULL leg.

### 3.3 No ambiguous default

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -F '|' -c "
SELECT concept_id, count(*) AS executable
FROM resolution.representation
WHERE evaluation_role='executable' AND expired_at IS NULL
GROUP BY concept_id HAVING count(*) > 1;
"
```

Expected: no rows. A concept with an executable representation must have exactly
one; zero means it is descriptive-only and evaluation fails closed. Do not
promote a representation merely to make this query empty.

### 3.4 Representation equivalence and negative proof

```bash
PGPASSWORD=pgpass python3 -m pytest bin/tests/test_v217_representation_bindings.py -q \
  2>&1 | tee "$OUT/post-v217-guard.log"
PGPASSWORD=pgpass python3 -m pytest python/SOLScript/tests -q \
  2>&1 | tee "$OUT/post-sol-tests.log"
```

Expected: V217 guard `13 passed`, including the two-representation
`nebula.assessments` / `resolution.assessment` equivalence, the unbound and
zero-executable failures, and no concept with multiple executables; SOL suite
passes with no regression.

### 3.5 No schema allow-list remains

```bash
cd /home/codex/dev/nexus-worktrees/v217-apply
if rg -n 'schema not in \\(.resolution.|schema_name = .resolution.|ELSE r.schema_name = .resolution.' \
  python/SOLScript/solscript/database_loader.py \
  python/SOLScript/solscript/adapters/sol_native.py; then
  echo "STOP: schema allow-list remains" >&2
  exit 1
fi
```

Expected: no output, exit 0.

### 3.6 Services

```bash
systemctl --user is-active \
  nebula-srv.service semantics-srv.service resolution-srv.service
curl -fsS http://localhost:3101/health
/home/codex/dev/nexus/bin/start-nexus-services.sh status | tail -10
```

Expected: active/active/active; health ok/db true; no new failure attributable
to V217.

## 4. Ledger and closeout

Only after §3 is green, the DBA writes:

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -v ON_ERROR_STOP=1 -c "
INSERT INTO resolution.migration_ledger
  (schema_name, migration_label, description, applied_by, applied_at)
SELECT
  'sql',
  'V217__representation_scoped_bindings',
  'V217 (docs/nebula-sol-absorption.md Phase 3; PR #982 merge 7914983a65157f48d8aff0889b6a9e0fa65c6a15; blob 6f6b453007cb10633c7d25c70ebd7884b75ce49a; DBA 93d3f092-5e6f-476d-8ec7-41cf587360f9; Architect 9b532211-da40-4b52-9729-b273523ff3ab): representation selector, representation-scoped binding keys and uniqueness, fail-closed resolver, and function allow-list. Applied live in window <WINDOW>; pre/post counts recorded; V218/Phases 4-5 remain unapplied.',
  'dba',
  now()
WHERE NOT EXISTS (
  SELECT 1 FROM resolution.migration_ledger
  WHERE schema_name='sql'
    AND migration_label='V217__representation_scoped_bindings'
);
"
```

Verify exactly one row. Then post the DBA closeout record and the change-log
entry using the existing `bin/post-change-log.sh` convention. Record the window
evidence (backup, test output, post counts, ledger, roles) in this runbook and
in the agent record.

## 5. Rollback

If the apply transaction fails, it rolls back automatically; re-check §1.3 and
do not write the ledger.

If a committed V217 must be withdrawn, restore `$OUT/full-pre.dump` to a
replacement database under DBA supervision and repoint services using the DBA
cutover procedure. Do not drop live columns or functions ad hoc. The V217
guard's double-apply/rollback test proves the transaction shape, not a
production rollback path; the verified full backup is the guaranteed fallback.

## 6. What this does not do

- It does not apply V218 or create Phase 4 assessment-family columns/tables.
- It does not add Phase 5 `peb.decisions.agent_record_id`.
- It does not move a table, backfill domain rows, or change a writer.
- It does not make every registered representation executable; only a
  representation with a uniquely matched existing binding is promoted.
- It does not replicate to vanadium.
