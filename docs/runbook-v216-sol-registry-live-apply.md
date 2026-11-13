# V216 live-apply runbook — Nebula → SOL concept registry (operator-gated)

> **Status:** procedure only. This document does **not** authorize an apply.
> Live application requires an explicit operator decision naming this runbook.
>
> **Scope:** Phase 2 only — `sql/V216__nebula_sol_concept_registry.sql`.
> V217, V218, Phase 3, Phase 4 and Phase 5 are out of scope and must not be
> applied in this window.
>
> **Migration character:** data-only registry inserts. V216 adds concepts,
> identity strategies, representations, representation identities and governed
> values. It adds **no bindings**, moves no table, backfills no data row and
> changes no writer. Applying it makes the concepts **describable**, not
> evaluable.

## Provenance

| Item | Value |
|---|---|
| Design note | `docs/nebula-sol-absorption.md` §6, Phase 2 |
| Migration | `sql/V216__nebula_sol_concept_registry.sql` |
| Merged PR | **#980**, merged `2026-11-12T22:18:41Z` |
| Merge commit | `3b99da08a2d7ede964104f0ae4b48b390c4ca92b` |
| Migration blob | `b62b3087d6a5c1b247f851d69648674773642090` |
| DBA approval | record `07c9fdf3-59c4-4d04-869d-35cdd5c53275`, verdict **APPROVED**, no blockers |
| Focused guard | `bin/tests/test_nebula_sol_concept_registry.py` — 9 tests |
| Expected ledger row | `resolution.migration_ledger`, `schema_name='sql'`, `migration_label='V216__nebula_sol_concept_registry'` |

The DBA review is a retrospective approval of the exact merged migration. It
confers **no** authorization to apply. The operator's approval is a separate
gate.

---

## 0. Authority, roles and stop conditions

| Role | Responsibility |
|---|---|
| **Operator** | Opens the window, approves the apply, owns the R9 replication decision |
| **DBA** | Verifies preflight, executes or directly supervises the apply, writes the ledger row |
| **Engineer** | Produces the evidence, runs the guard and post-apply checks, records the result |

**Stop immediately and do not apply** if any of the following is true:

- the migration blob does not equal `b62b3087d6a5c1b247f851d69648674773642090`;
- the merge commit is not an ancestor of the checkout;
- the DBA record is absent, is not `APPROVED`, or does not name this migration
  and merge commit;
- any pre-state query differs from §1.3 **and** the difference is not explained
  and accepted by the DBA before the window;
- V216 is already present in `resolution.migration_ledger`;
- any Phase 3/4 marker in §1.4 is present;
- the verified backup or the targeted registry snapshot is missing;
- another migration is in flight, or a service is actively writing the registry
  in a way the DBA has not accounted for;
- any `psql` command exits non-zero.

An unexplained difference is a stop, not a judgement call to make alone.

---

## 1. Preflight

Run every command from a clean checkout of the approved commit. Do not apply
from a dirty `main` working tree.

Recommended clean checkout:

```bash
cd /home/codex/dev/nexus
git fetch origin
git worktree add --detach /home/codex/dev/nexus-worktrees/v216-apply main
cd /home/codex/dev/nexus-worktrees/v216-apply
```

If the worktree already exists, verify it is at the approved commit rather than
creating a second copy.

### 1.1 Exact artifact

```bash
cd /home/codex/dev/nexus-worktrees/v216-apply

git merge-base --is-ancestor \
  3b99da08a2d7ede964104f0ae4b48b390c4ca92b HEAD \
  && echo "merge commit is in HEAD"

test "$(git hash-object sql/V216__nebula_sol_concept_registry.sql)" \
     = "b62b3087d6a5c1b247f851d69648674773642090" \
  && echo "V216 blob matches the approved merge"

git status --short -- sql docs bin/tests bin/tests-ci-manifest.json typespec
```

Expected: both checks print their success line; `git status` prints nothing for
the relevant paths.

### 1.2 DBA approval is present and exact

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -F '|' -c "
SELECT id, role, record_type,
       position('APPROVED' in upper(content)) > 0 AS has_approved,
       position('3b99da08a2d7ede964104f0ae4b48b390c4ca92b' in content) > 0 AS has_merge
FROM nebula.agent_records
WHERE id = '07c9fdf3-59c4-4d04-869d-35cdd5c53275';
"
```

Expected: one row, `role='dba'`, `record_type='assessment'`, both boolean
columns `t`. Read the full record; a substring match alone is not approval.
The record must also say it does **not** authorize live application.

### 1.3 Live pre-state

These are the expected pre-apply values observed on **2026-11-13**. Re-capture
them at apply time; do not treat the baseline as an invariant.

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -v ON_ERROR_STOP=1 -At -F '|' -c "
WITH target(name) AS (
  VALUES ('Ticket'),('Receipt'),('AgentRecord'),('Decision'),
         ('Agenda'),('AgendaItem'),('Assessment')
)
SELECT 'target_concepts', count(*)::text
  FROM resolution.concept
 WHERE name IN ('Ticket','Receipt','AgentRecord','Decision','Agenda','AgendaItem')
UNION ALL
SELECT 'target_identity_strategies', count(*)::text
  FROM resolution.identity_strategy s
  JOIN resolution.concept c ON c.id = s.concept_id
 WHERE c.name IN (SELECT name FROM target)
UNION ALL
SELECT 'target_representations', count(*)::text
  FROM resolution.representation r
  JOIN resolution.concept c ON c.id = r.concept_id
 WHERE c.name IN (SELECT name FROM target)
UNION ALL
SELECT 'target_representation_identities', count(*)::text
  FROM resolution.representation_identity ri
  JOIN resolution.representation r ON r.id = ri.representation_id
  JOIN resolution.concept c ON c.id = r.concept_id
 WHERE c.name IN (SELECT name FROM target)
UNION ALL
SELECT 'target_attributes', count(*)::text
  FROM resolution.concept_attribute a
  JOIN resolution.concept c ON c.id = a.concept_id
 WHERE c.name IN (SELECT name FROM target)
   AND a.name IN ('record_type','assessment_kind')
UNION ALL
SELECT 'target_values', count(*)::text
  FROM resolution.concept_attribute_value v
  JOIN resolution.concept_attribute a ON a.id = v.attribute_id
  JOIN resolution.concept c ON c.id = a.concept_id
 WHERE c.name IN (SELECT name FROM target)
   AND a.name IN ('record_type','assessment_kind')
UNION ALL
SELECT 'target_bindings', count(*)::text
  FROM resolution.concept_attribute_binding b
  JOIN resolution.concept_attribute a ON a.id = b.attribute_id
  JOIN resolution.concept c ON c.id = a.concept_id
 WHERE c.name IN (SELECT name FROM target)
UNION ALL
SELECT 'assessment_history_rows', count(*)::text FROM nebula.assessments_history
UNION ALL
SELECT 'peb_decision_rows', count(*)::text FROM peb.decisions;
"
```

Expected pre-state:

| Check | Expected |
|---|---:|
| `target_concepts` | 0 |
| `target_identity_strategies` | 0 |
| `target_representations` | 1 (the pre-existing `Assessment` → `resolution.assessment`) |
| `target_representation_identities` | 0 |
| `target_attributes` | 0 |
| `target_values` | 0 |
| `target_bindings` | 0 |
| `assessment_history_rows` | 0 |
| `peb_decision_rows` | 16 |

Also confirm that **all seven physical surfaces exist**:

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -F '|' -c "
SELECT surface, to_regclass(surface) IS NOT NULL AS present
FROM unnest(ARRAY[
  'resolution.ticket','resolution.receipt','nebula.agent_records',
  'peb.decisions','nebula.agendas','nebula.agenda_items','nebula.assessments'
]) AS surface
ORDER BY surface;
"
```

Expected: seven rows, all `present = t`.

Confirm the registry constraints V216 relies on:

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -c "
SELECT conname
FROM pg_constraint
WHERE conname IN (
  'concept_attribute_concept_id_name_key',
  'concept_attribute_value_attribute_id_value_key',
  'identity_strategy_concept_id_key',
  'representation_identity_representation_id_key'
)
ORDER BY conname;
"
```

Expected: all four names. Also confirm the nine-value `record_type` CHECK:

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -c "
SELECT pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'nebula.agent_records_history'::regclass
  AND conname = 'agent_records_record_type_check';
"
```

Expected: the definition lists `report`, `analysis`, `assessment`, `inspection`,
`prompt`, `response`, `engineering_log`, `architecture_note`, `decision`.

### 1.4 Gated work must be absent

V216 must not run on top of V217/V218 or Phase 4 DDL. Every query below must
return `0` columns/records:

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -F '|' -c "
SELECT 'representation.evaluation_role',
       count(*) FROM information_schema.columns
 WHERE table_schema='resolution' AND table_name='representation'
   AND column_name='evaluation_role'
UNION ALL
SELECT 'representation.read_adapter',
       count(*) FROM information_schema.columns
 WHERE table_schema='resolution' AND table_name='representation'
   AND column_name='read_adapter'
UNION ALL
SELECT 'binding.representation_id',
       count(*) FROM information_schema.columns
 WHERE table_schema='resolution' AND table_name='concept_attribute_binding'
   AND column_name='representation_id'
UNION ALL
SELECT 'assessments_history.assessment_kind',
       count(*) FROM information_schema.columns
 WHERE table_schema='nebula' AND table_name='assessments_history'
   AND column_name='assessment_kind'
UNION ALL
SELECT 'assessments_history.agent_record_id',
       count(*) FROM information_schema.columns
 WHERE table_schema='nebula' AND table_name='assessments_history'
   AND column_name='agent_record_id'
UNION ALL
SELECT 'peb.decisions.agent_record_id',
       count(*) FROM information_schema.columns
 WHERE table_schema='peb' AND table_name='decisions'
   AND column_name='agent_record_id';
"
```

Expected: every count `0`. A non-zero value means a gated migration is already
applied; stop and reconcile with the DBA.

Confirm V216 is not already ledgered:

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -c "
SELECT count(*)
FROM resolution.migration_ledger
WHERE schema_name = 'sql'
  AND migration_label = 'V216__nebula_sol_concept_registry';
"
```

Expected: `0`. If `1`, do not apply; the live state and ledger must be
reconciled first.

### 1.5 Backup

Two artifacts are required: the normal verified full backup, and a targeted
snapshot of the six registry tables plus `resolution.owning_subsystem`. The
lookup table is required because `resolution.representation` has a foreign key
to it; without it the registry-only archive cannot be restored into an empty
database. V216 does not write `owning_subsystem`.

```bash
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT=/home/codex/backups/nexus-pg/v216-$STAMP
TMP_LOG=/tmp/v216-full-backup-$STAMP.log

# Do not pre-create the backup directory: the backup tier's drive guard must
# decide whether the destination exists and is usable. A local mkdir could
# create a directory on the wrong filesystem when the drive is absent.
df -h /home/codex/backups/nexus-pg

# 1) The project's verified full backup (R9 anchor).
PGPASSWORD=pgpass /home/codex/dev/nexus/bin/pg-escape-hatch.sh \
  2>&1 | tee "$TMP_LOG"

# Only after the drive guard and dump succeed, create the window directory.
install -d "$OUT"
mv "$TMP_LOG" "$OUT/full-backup.log"

# Prove the stamp is from this window, not a stale file left by a skipped run.
python3 -c '
import json
p="/home/codex/backups/nexus-pg/last-backup.json"
d=json.load(open(p))
print("last_backup=", d["last_backup"], "file=", d["file"], "size=", d["size"])
'

# Preserve the verified dump in the window directory; the nightly tier prunes
# dumps older than 7 days, which is shorter than a delayed rollback window.
FULL_DUMP=$(python3 -c '
import json
print("/home/codex/backups/nexus-pg/" + json.load(open("/home/codex/backups/nexus-pg/last-backup.json"))["file"])
')
test -f "$FULL_DUMP"
cp --reflink=auto "$FULL_DUMP" "$OUT/full-pre.dump"
sha256sum "$OUT/full-pre.dump" | tee "$OUT/full-pre.sha256"
pg_restore -l "$OUT/full-pre.dump" > "$OUT/full-pre.toc"

# 2) Targeted, restorable snapshot: six registry tables + the FK lookup.
PGPASSWORD=pgpass pg_dump \
  -h localhost -U pguser -d nexus -Fc \
  -t resolution.owning_subsystem \
  -t resolution.concept \
  -t resolution.identity_strategy \
  -t resolution.representation \
  -t resolution.representation_identity \
  -t resolution.concept_attribute \
  -t resolution.concept_attribute_value \
  -f "$OUT/registry-pre.dump"

pg_restore -l "$OUT/registry-pre.dump" > "$OUT/registry-pre.toc"
sha256sum "$OUT/registry-pre.dump" | tee "$OUT/registry-pre.sha256"

# 3) Pre-apply totals for the post-apply delta check.
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -F '|' -c "
SELECT 'concept', count(*) FROM resolution.concept
UNION ALL SELECT 'identity_strategy', count(*) FROM resolution.identity_strategy
UNION ALL SELECT 'representation', count(*) FROM resolution.representation
UNION ALL SELECT 'representation_identity', count(*) FROM resolution.representation_identity
UNION ALL SELECT 'concept_attribute', count(*) FROM resolution.concept_attribute
UNION ALL SELECT 'concept_attribute_value', count(*) FROM resolution.concept_attribute_value
UNION ALL SELECT 'concept_attribute_binding', count(*) FROM resolution.concept_attribute_binding
UNION ALL SELECT 'concept_relationship', count(*) FROM resolution.concept_relationship
UNION ALL SELECT 'concept_relationship_binding', count(*) FROM resolution.concept_relationship_binding
UNION ALL SELECT 'owning_subsystem', count(*) FROM resolution.owning_subsystem;
" | tee "$OUT/pre-counts.txt"
```

Expected full-backup log ends with the project's verified-dump success line.
`registry-pre.toc` must be non-empty and list all seven tables (`concept`,
`identity_strategy`, `representation`, `representation_identity`,
`concept_attribute`, `concept_attribute_value`, `owning_subsystem`). The
pre-counts baseline observed on 2026-11-13 was:

| Table | Pre-apply |
|---|---:|
| `concept` | 38 |
| `identity_strategy` | 13 |
| `representation` | 23 |
| `representation_identity` | 13 |
| `concept_attribute` | 70 |
| `concept_attribute_value` | 45 |
| `concept_attribute_binding` | 7 |
| `concept_relationship` | 15 |
| `concept_relationship_binding` | 6 |
| `owning_subsystem` | 6 |

Do not proceed if the full backup is stale, unverified, or the targeted dump
cannot be listed.

### 1.6 Rehearsal

At minimum, rerun the focused guard. It creates a throwaway database, applies
V216 twice, rolls back, and asserts the data-only/idempotency contract.

```bash
cd /home/codex/dev/nexus-worktrees/v216-apply
PGPASSWORD=pgpass python3 -m pytest \
  bin/tests/test_nebula_sol_concept_registry.py -q \
  2>&1 | tee "$OUT/guard.log"
```

Expected: `9 passed`.

Recommended stronger rehearsal against the captured live registry shape:

```bash
SCRATCH="v216_rehearsal_$STAMP"
createdb -h localhost -U pguser "$SCRATCH"
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d "$SCRATCH" \
  -v ON_ERROR_STOP=1 -c 'CREATE SCHEMA IF NOT EXISTS resolution;'

PGPASSWORD=pgpass pg_restore -h localhost -U pguser -d "$SCRATCH" \
  --no-owner --no-privileges "$OUT/registry-pre.dump"

PGPASSWORD=pgpass python3 bin/rehearse-migration.py \
  sql/V216__nebula_sol_concept_registry.sql \
  --dsn "postgresql://pguser@localhost:5432/$SCRATCH" \
  --allow-self-transaction \
  2>&1 | tee "$OUT/rehearsal.log"

# The helper rolls back; the scratch counts must equal the pre-apply counts.
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d "$SCRATCH" -At -F '|' -c "
SELECT 'concept', count(*) FROM resolution.concept
UNION ALL SELECT 'identity_strategy', count(*) FROM resolution.identity_strategy
UNION ALL SELECT 'representation', count(*) FROM resolution.representation
UNION ALL SELECT 'representation_identity', count(*) FROM resolution.representation_identity
UNION ALL SELECT 'concept_attribute', count(*) FROM resolution.concept_attribute
UNION ALL SELECT 'concept_attribute_value', count(*) FROM resolution.concept_attribute_value
UNION ALL SELECT 'owning_subsystem', count(*) FROM resolution.owning_subsystem;
"

PGPASSWORD=pgpass dropdb -h localhost -U pguser "$SCRATCH"
```

Expected: the rehearsal helper reports `BEGIN; <migration> ROLLBACK;` and exits
0; scratch counts equal the pre-apply counts. Do not apply if the helper
refuses or if any count changed.

The scratch restore contains only the registry tables plus
`resolution.owning_subsystem`; it proves the inserts and rollback, not the live
physical-surface resolution or the binding tables. §3.3 and §3.4 are live
checks, not scratch checks.

### 1.7 Go/no-go

Record in the window record:

- operator approval and window start;
- DBA record id and the exact blob hash;
- pre-counts and target-surface check;
- full-backup path, size, sha256, and `pg_restore -l` result;
- guard result and rehearsal result;
- the names of the operator, DBA and engineer on duty.

Only then proceed to §2.

---

## 2. Apply

The file manages its own transaction (`BEGIN; ... COMMIT;`). Run it with
`ON_ERROR_STOP=1`; do not wrap it in another `BEGIN/COMMIT`, and do not use the
rehearsal helper (which deliberately rolls back).

```bash
cd /home/codex/dev/nexus-worktrees/v216-apply
set -o pipefail
export PGPASSWORD=pgpass

PGAPPNAME=v216-live-apply \
PGOPTIONS='-c lock_timeout=5s -c statement_timeout=60s' \
  psql -X -h localhost -U pguser -d nexus -v ON_ERROR_STOP=1 \
  -f sql/V216__nebula_sol_concept_registry.sql \
  2>&1 | tee "$OUT/apply.log"

rc=${PIPESTATUS[0]}
echo "psql_exit=$rc" | tee -a "$OUT/apply.log"
test "$rc" -eq 0
```

Expected: six `INSERT 0 N` lines (6, 7, 8, 9, 2, 11) and `COMMIT`. If `psql`
exits non-zero, stop: the transaction did not commit. Run §1.3 again and confirm
the pre-state; do **not** insert a ledger row.

Do not re-run the migration on the live database. Idempotency is proven in the
guard and rehearsal; a second live run is not part of this procedure.

---

## 3. Post-apply verification

Run every query before writing the ledger. Any mismatch is a rollback trigger
(§5).

### 3.1 Target summary

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -F '|' -c "
WITH target(name) AS (
  VALUES ('Ticket'),('Receipt'),('AgentRecord'),('Decision'),
         ('Agenda'),('AgendaItem'),('Assessment')
)
SELECT c.name,
       count(DISTINCT r.id)  AS representations,
       count(DISTINCT s.id)  AS identity_strategies,
       count(DISTINCT ri.id) AS representation_identities
FROM resolution.concept c
LEFT JOIN resolution.representation r ON r.concept_id = c.id
LEFT JOIN resolution.identity_strategy s ON s.concept_id = c.id
LEFT JOIN resolution.representation_identity ri ON ri.representation_id = r.id
WHERE c.name IN (SELECT name FROM target)
GROUP BY c.name
ORDER BY c.name;
"
```

Expected exactly:

| Concept | Representations | Identity strategies | Representation identities |
|---|---:|---:|---:|
| `Agenda` | 1 | 1 | 1 |
| `AgendaItem` | 1 | 1 | 1 |
| `AgentRecord` | 1 | 1 | 1 |
| `Assessment` | 2 | 1 | 2 |
| `Decision` | 2 | 1 | 2 |
| `Receipt` | 1 | 1 | 1 |
| `Ticket` | 1 | 1 | 1 |

### 3.2 Governed values

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -F '|' -c "
WITH target(name) AS (
  VALUES ('Ticket'),('Receipt'),('AgentRecord'),('Decision'),
         ('Agenda'),('AgendaItem'),('Assessment')
)
SELECT c.name, a.name, count(v.id) AS values
FROM resolution.concept c
JOIN resolution.concept_attribute a ON a.concept_id = c.id
LEFT JOIN resolution.concept_attribute_value v ON v.attribute_id = a.id
WHERE c.name IN (SELECT name FROM target)
  AND a.name IN ('record_type','assessment_kind')
GROUP BY c.name, a.name
ORDER BY c.name, a.name;
"
```

Expected exactly two rows: `AgentRecord|record_type|9` and
`Assessment|assessment_kind|2`.

### 3.3 Every representation resolves

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -F '|' -c "
WITH target(name) AS (
  VALUES ('Ticket'),('Receipt'),('AgentRecord'),('Decision'),
         ('Agenda'),('AgendaItem'),('Assessment')
)
SELECT c.name, r.label, r.schema_name || '.' || r.table_name AS surface,
       to_regclass(format('%I.%I', r.schema_name, r.table_name)) IS NOT NULL AS resolves
FROM resolution.representation r
JOIN resolution.concept c ON c.id = r.concept_id
WHERE c.name IN (SELECT name FROM target)
ORDER BY c.name, r.label;
"
```

Expected: nine rows, every `resolves = t`. The nine are the eight new
representations plus the preserved `Assessment|assessment table|resolution.assessment`.

### 3.4 No binding was added and Assessment was preserved

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -F '|' -c "
WITH target(name) AS (
  VALUES ('Ticket'),('Receipt'),('AgentRecord'),('Decision'),
         ('Agenda'),('AgendaItem'),('Assessment')
)
SELECT 'attribute_bindings', count(*)::text
FROM resolution.concept_attribute_binding b
JOIN resolution.concept_attribute a ON a.id = b.attribute_id
JOIN resolution.concept c ON c.id = a.concept_id
WHERE c.name IN (SELECT name FROM target)
UNION ALL
SELECT 'relationship_bindings', count(*)::text
FROM resolution.concept_relationship_binding rb
JOIN resolution.concept_relationship cr ON cr.id = rb.concept_relationship_id
WHERE cr.from_concept_id IN (SELECT id FROM resolution.concept WHERE name IN (SELECT name FROM target))
   OR cr.to_concept_id   IN (SELECT id FROM resolution.concept WHERE name IN (SELECT name FROM target))
UNION ALL
SELECT 'assessment_existing_representation', count(*)::text
FROM resolution.representation r
JOIN resolution.concept c ON c.id = r.concept_id
WHERE c.name = 'Assessment'
  AND r.label = 'assessment table'
  AND r.schema_name = 'resolution'
  AND r.table_name = 'assessment';
"
```

Expected: `attribute_bindings|0`, `relationship_bindings|0`,
`assessment_existing_representation|1`.

### 3.5 Totals match the captured baseline plus the V216 delta

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -F '|' -c "
SELECT 'concept', count(*) FROM resolution.concept
UNION ALL SELECT 'identity_strategy', count(*) FROM resolution.identity_strategy
UNION ALL SELECT 'representation', count(*) FROM resolution.representation
UNION ALL SELECT 'representation_identity', count(*) FROM resolution.representation_identity
UNION ALL SELECT 'concept_attribute', count(*) FROM resolution.concept_attribute
UNION ALL SELECT 'concept_attribute_value', count(*) FROM resolution.concept_attribute_value
UNION ALL SELECT 'concept_attribute_binding', count(*) FROM resolution.concept_attribute_binding
UNION ALL SELECT 'concept_relationship', count(*) FROM resolution.concept_relationship
UNION ALL SELECT 'concept_relationship_binding', count(*) FROM resolution.concept_relationship_binding
UNION ALL SELECT 'owning_subsystem', count(*) FROM resolution.owning_subsystem;
" | tee "$OUT/post-counts.txt"
```

Expected deltas from `pre-counts.txt`:

| Table | Delta |
|---|---:|
| `concept` | +6 |
| `identity_strategy` | +7 |
| `representation` | +8 |
| `representation_identity` | +9 |
| `concept_attribute` | +2 |
| `concept_attribute_value` | +11 |
| `concept_attribute_binding` | 0 |
| `concept_relationship` | 0 |
| `concept_relationship_binding` | 0 |
| `owning_subsystem` | 0 |

If the live baselines above still hold, the expected absolute totals are:
`concept=44`, `identity_strategy=20`, `representation=31`,
`representation_identity=22`, `concept_attribute=72`,
`concept_attribute_value=56`, `concept_attribute_binding=7`,
`concept_relationship=15`, `concept_relationship_binding=6`,
`owning_subsystem=6`.

Also confirm the source surfaces did not change:

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -F '|' -c "
SELECT 'resolution.receipt', count(*) FROM resolution.receipt
UNION ALL SELECT 'nebula.agent_records', count(*) FROM nebula.agent_records
UNION ALL SELECT 'peb.decisions', count(*) FROM peb.decisions
UNION ALL SELECT 'nebula.agendas', count(*) FROM nebula.agendas
UNION ALL SELECT 'nebula.agenda_items', count(*) FROM nebula.agenda_items
UNION ALL SELECT 'nebula.assessments', count(*) FROM nebula.assessments
UNION ALL SELECT 'nebula.assessments_history', count(*) FROM nebula.assessments_history;
"
```

Expected: `resolution.receipt=1997`, `nebula.agent_records=16673`,
`peb.decisions=16`, `nebula.agendas=3`, `nebula.agenda_items=1`,
`nebula.assessments=0`, `nebula.assessments_history=0` on the 2026-11-13
baseline. Capture your pre-window values and compare; V216 performs no data
movement, so any change is a stop.

### 3.6 Service health

No service restart is expected: V216 changes registry rows, not code or
bindings. Spot-check the registry-reading services and the backend fleet:

```bash
systemctl is-active nebula-srv semantics-srv resolution-srv
curl -fsS http://localhost:3101/health
/home/codex/dev/nexus/bin/start-nexus-services.sh status | tail -10
```

Expected: all three units `active`, the health request succeeds, and the status
table has no new failure attributable to the apply. If a service caches the
registry, restart only the service that owns the cache, per its owner's
procedure; do not restart the fleet as part of this runbook.

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
  'V216__nebula_sol_concept_registry',
  'V216 (docs/nebula-sol-absorption.md Phase 2; PR #980 merge 3b99da08a2d7ede964104f0ae4b48b390c4ca92b; blob b62b3087d6a5c1b247f851d69648674773642090; DBA approval 07c9fdf3-59c4-4d04-869d-35cdd5c53275): data-only registry completion for Ticket, Receipt, AgentRecord, Decision, Agenda, AgendaItem and missing Assessment links. Applied by operator-directed DBA apply in window <WINDOW>; pre/post counts recorded; zero bindings added; V217/V218 remain unapplied.',
  'dba',
  now()
WHERE NOT EXISTS (
  SELECT 1 FROM resolution.migration_ledger
  WHERE schema_name = 'sql'
    AND migration_label = 'V216__nebula_sol_concept_registry'
);
"
```

Verify exactly one row:

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus -At -F '|' -c "
SELECT schema_name, migration_label, applied_by, applied_at
FROM resolution.migration_ledger
WHERE schema_name = 'sql'
  AND migration_label = 'V216__nebula_sol_concept_registry';
"
```

Then write the window records:

```bash
# DBA-authored closeout (adjust the role/model/tags to the on-duty sessions).
python3 bin/post-agent-record.py \
  -r dba \
  --record-type engineering_log \
  --level 2 --visibility all \
  --tags 'to:architect,to:engineer,to:operator,type:milestone,planRef:nebula-sol-absorption,phase:2,v216,live-apply' \
  -t "V216 live apply COMPLETE: registry rows present, zero bindings, ledgered" \
  -c "Window <WINDOW>. DBA approval 07c9fdf3-59c4-4d04-869d-35cdd5c53275. Apply log <OUT>/apply.log exit 0. Post-apply: 6 concepts, 7 identity strategies, 9 representations, 9 representation identities, 2 attributes, 11 values, 0 bindings. Assessment existing representation preserved. Ledger row written. V217/V218 remain unapplied."

bash bin/post-change-log.sh \
  --role dba --model "$NEXUS_AGENT_MODEL" \
  -t "V216 applied: Nebula → SOL registry completion (staged-inert → live registry)" \
  -b "Applied sql/V216__nebula_sol_concept_registry.sql per docs/runbook-v216-sol-registry-live-apply.md. PR #980 merge 3b99da08a; blob b62b3087…; DBA approval 07c9fdf3. Data-only: 6 concepts, 7 identity strategies, 9 representations, 9 representation identities, 2 governed attributes, 11 values. Zero bindings; V217/V218 and Phases 3–5 remain operator-gated. Ledger row written; backup <OUT> retained."
```

Finally, the operator makes the **R9 replication decision**: ask whether the
registry change should be replicated to the canonical off-machine target
(currently **vanadium**). Do not replicate automatically. If approved, use the
existing PG backup/sync path, verify the target row counts and ledger, and
record the result against this window.

Retain `$OUT` and the full backup until at least two clean backup cycles have
passed and the DBA has signed off.

---

## 5. Rollback

### 5.1 When to roll back

Roll back if any post-apply invariant fails, if a downstream service shows a
regression attributable to V216, or if the operator and DBA agree before the
window closes. Do not roll back merely because V217 or V218 is still unapplied;
that is the intended staged state.

### 5.2 Preferred rollback (before the ledger row)

Because V216 is data-only, the rollback is the exact inverse set of deletes.
The block in Appendix A is transactional and fail-closed:

- it refuses if the expected 6/2/11 row shapes are not present;
- it refuses if `aegis` rows reference the V216 concepts/attributes/values
  (those FKs are `SET NULL`/`CASCADE` and would otherwise mutate silently);
- it deletes values → attributes → representation identities → the eight new
  representations → identity strategies → the six new concepts;
- it **never** deletes the pre-existing `Assessment` concept or its
  `resolution.assessment` representation;
- it asserts the restored pre-state before `COMMIT`.

Run it only if §1.3 recorded zero pre-existing Assessment identity/representation
identity/attributes and zero target concepts. If those conditions do not hold,
do not use the delete block; use §5.3.

```bash
PGPASSWORD=pgpass psql -X -h localhost -U pguser -d nexus \
  -v ON_ERROR_STOP=1 -f "$OUT/rollback.sql" \
  2>&1 | tee "$OUT/rollback.log"
```

The expected `DELETE` counts are `11, 2, 9, 8, 7, 6`. Then rerun §1.3; the
pre-state counts must return exactly. If the transaction aborts, nothing was
deleted; investigate the reported dependency. Do not force deletes.

If the ledger row was already written, **do not silently delete it**. Treat the
rollback as an incident: record the reason, have the DBA correct or annotate the
ledger per the ledger policy, and note that the applied-state claim is
superseded. The ledger is append-only by convention even where no trigger
enforces it.

### 5.3 Fallback restore

If the guarded delete cannot run, or a dependency must be preserved, restore the
verified pre-apply backup under DBA supervision. This is the guaranteed path and
is heavier than the delete block:

1. Stop registry-writing services or put the window into maintenance.
2. Restore `$OUT/full-pre.dump` (the verified full backup preserved in the
   window directory; its path and hash are also in `$OUT/full-backup.log` and
   `last-backup.json`) to a replacement database; do not overwrite the live
   database in place.
3. Verify the six registry tables plus `owning_subsystem` and the pre-state
   counts in the replacement.
4. Repoint the services to the replacement under the DBA's cutover procedure.
5. Record the restore, the replacement database, and the verification in the
   incident/window record.

Do not use `DROP`/`TRUNCATE` on the live registry as a shortcut.

---

## 6. What this does not do

- It does **not** apply V217 (`representation.evaluation_role`, binding
  `representation_id`, fail-closed representation resolution).
- It does **not** apply V218 or add `assessment_kind`, subject/evidence FKs or
  `agent_record_id` to `nebula.assessments_history`.
- It does **not** add `peb.decisions.agent_record_id`.
- It does **not** add a binding, so no concept becomes evaluable merely by
  applying V216.
- It does **not** move a table, backfill a data row, change a writer, or create
  a new `resolution` domain table.
- It does **not** apply `vision.canonical_work_requests` (V170); that file is
  withdrawn and its table is absent.

---

## Appendix A — guarded rollback block

Save as `$OUT/rollback.sql` and run only under §5.2. Expected delete counts are
shown inline. The block runs in one transaction and asserts the pre-state before
committing.

```sql
BEGIN;
SET LOCAL lock_timeout = '5s';

DO $$
DECLARE
    v_concepts uuid[];
    v_attrs    uuid[];
    v_values   uuid[];
    v_exists   boolean;
BEGIN
    SELECT array_agg(id) INTO v_concepts
      FROM resolution.concept
     WHERE name IN ('Ticket','Receipt','AgentRecord','Decision','Agenda','AgendaItem');

    SELECT array_agg(a.id) INTO v_attrs
      FROM resolution.concept_attribute a
      JOIN resolution.concept c ON c.id = a.concept_id
     WHERE c.name IN ('AgentRecord','Assessment')
       AND a.name IN ('record_type','assessment_kind');

    SELECT array_agg(v.id) INTO v_values
      FROM resolution.concept_attribute_value v
     WHERE v.attribute_id = ANY(v_attrs);

    IF coalesce(array_length(v_concepts,1),0) <> 6 THEN
        RAISE EXCEPTION 'rollback refused: expected 6 V216 concepts, found %',
            coalesce(array_length(v_concepts,1),0);
    END IF;
    IF coalesce(array_length(v_attrs,1),0) <> 2 THEN
        RAISE EXCEPTION 'rollback refused: expected 2 V216 attributes, found %',
            coalesce(array_length(v_attrs,1),0);
    END IF;
    IF coalesce(array_length(v_values,1),0) <> 11 THEN
        RAISE EXCEPTION 'rollback refused: expected 11 V216 values, found %',
            coalesce(array_length(v_values,1),0);
    END IF;

    -- These FKs are SET NULL/CASCADE; refuse rather than mutate silently.
    -- Dynamic SQL keeps the block runnable in a reduced restore where the
    -- aegis schema is absent; PL/pgSQL would otherwise parse the relation name
    -- even when the first branch is false.
    IF to_regclass('aegis.registry') IS NOT NULL THEN
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM aegis.registry WHERE main_concept_id = ANY($1))'
          INTO v_exists USING v_concepts;
        IF v_exists THEN
            RAISE EXCEPTION 'rollback refused: aegis.registry references V216 concepts';
        END IF;
    END IF;
    IF to_regclass('aegis.state') IS NOT NULL THEN
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM aegis.state WHERE concept_id = ANY($1) OR attribute_value_id = ANY($2))'
          INTO v_exists USING v_concepts, v_values;
        IF v_exists THEN
            RAISE EXCEPTION 'rollback refused: aegis.state references V216 objects';
        END IF;
    END IF;
    IF to_regclass('aegis.variable') IS NOT NULL THEN
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM aegis.variable WHERE attribute_id = ANY($1))'
          INTO v_exists USING v_attrs;
        IF v_exists THEN
            RAISE EXCEPTION 'rollback refused: aegis.variable references V216 attributes';
        END IF;
    END IF;
    IF to_regclass('aegis.concept_mapping') IS NOT NULL THEN
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM aegis.concept_mapping WHERE concept_id = ANY($1))'
          INTO v_exists USING v_concepts;
        IF v_exists THEN
            RAISE EXCEPTION 'rollback refused: aegis.concept_mapping references V216 concepts';
        END IF;
    END IF;
    IF to_regclass('aegis.attribute_mapping') IS NOT NULL THEN
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM aegis.attribute_mapping WHERE attribute_id = ANY($1))'
          INTO v_exists USING v_attrs;
        IF v_exists THEN
            RAISE EXCEPTION 'rollback refused: aegis.attribute_mapping references V216 attributes';
        END IF;
    END IF;
END $$;

-- 11 values
DELETE FROM resolution.concept_attribute_value v
USING resolution.concept_attribute a, resolution.concept c
WHERE v.attribute_id = a.id
  AND a.concept_id = c.id
  AND c.name IN ('AgentRecord','Assessment')
  AND a.name IN ('record_type','assessment_kind');

-- 2 attributes
DELETE FROM resolution.concept_attribute a
USING resolution.concept c
WHERE a.concept_id = c.id
  AND c.name IN ('AgentRecord','Assessment')
  AND a.name IN ('record_type','assessment_kind');

-- 9 representation identities (8 new + the existing Assessment representation)
DELETE FROM resolution.representation_identity ri
USING resolution.representation r, resolution.concept c
WHERE ri.representation_id = r.id
  AND r.concept_id = c.id
  AND (c.name, r.label) IN (
    ('Ticket','canonical ticket table'),
    ('Receipt','canonical receipt table'),
    ('AgentRecord','bitemporal narrative record'),
    ('Decision','ADR authority ledger'),
    ('Decision','narrative decision record'),
    ('Agenda','deliberation agenda'),
    ('AgendaItem','deliberation agenda item'),
    ('Assessment','assessment table'),
    ('Assessment','operational assessment table')
  );

-- 8 new representations; the existing resolution.assessment representation stays
DELETE FROM resolution.representation r
USING resolution.concept c
WHERE r.concept_id = c.id
  AND (c.name, r.label) IN (
    ('Ticket','canonical ticket table'),
    ('Receipt','canonical receipt table'),
    ('AgentRecord','bitemporal narrative record'),
    ('Decision','ADR authority ledger'),
    ('Decision','narrative decision record'),
    ('Agenda','deliberation agenda'),
    ('AgendaItem','deliberation agenda item'),
    ('Assessment','operational assessment table')
  );

-- 7 identity strategies (including the one added for Assessment)
DELETE FROM resolution.identity_strategy s
USING resolution.concept c
WHERE s.concept_id = c.id
  AND c.name IN ('Ticket','Receipt','AgentRecord','Decision','Agenda','AgendaItem','Assessment');

-- 6 new concepts; Assessment is preserved
DELETE FROM resolution.concept c
WHERE c.name IN ('Ticket','Receipt','AgentRecord','Decision','Agenda','AgendaItem');

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM resolution.concept
         WHERE name IN ('Ticket','Receipt','AgentRecord','Decision','Agenda','AgendaItem')
    ) THEN
        RAISE EXCEPTION 'rollback failed: V216 concepts remain';
    END IF;
    IF (SELECT count(*) FROM resolution.concept WHERE name = 'Assessment') <> 1 THEN
        RAISE EXCEPTION 'rollback failed: Assessment concept missing or duplicated';
    END IF;
    IF (SELECT count(*) FROM resolution.representation r
          JOIN resolution.concept c ON c.id = r.concept_id
         WHERE c.name = 'Assessment'
           AND r.label = 'assessment table'
           AND r.schema_name = 'resolution'
           AND r.table_name = 'assessment') <> 1 THEN
        RAISE EXCEPTION 'rollback failed: existing Assessment representation missing or duplicated';
    END IF;
    IF EXISTS (
        SELECT 1 FROM resolution.identity_strategy s
          JOIN resolution.concept c ON c.id = s.concept_id
         WHERE c.name = 'Assessment'
    ) THEN
        RAISE EXCEPTION 'rollback failed: Assessment identity strategy remains';
    END IF;
    IF EXISTS (
        SELECT 1 FROM resolution.representation_identity ri
          JOIN resolution.representation r ON r.id = ri.representation_id
          JOIN resolution.concept c ON c.id = r.concept_id
         WHERE c.name = 'Assessment'
    ) THEN
        RAISE EXCEPTION 'rollback failed: Assessment representation identity remains';
    END IF;
    IF EXISTS (
        SELECT 1 FROM resolution.concept_attribute a
          JOIN resolution.concept c ON c.id = a.concept_id
         WHERE c.name = 'Assessment'
           AND a.name IN ('record_type','assessment_kind')
    ) THEN
        RAISE EXCEPTION 'rollback failed: V216 Assessment attribute remains';
    END IF;
END $$;

COMMIT;
```

After the rollback, rerun §1.3. Expected: the 2026-11-13 pre-state table, zero
target concepts/identities/representation identities/attributes/values/bindings,
one existing Assessment representation, and `nebula.assessments_history = 0`.

---

## Appendix B — observed pre-apply baseline (2026-11-13)

This is a snapshot for orientation, not a substitute for the live preflight.

| Check | Value |
|---|---:|
| `resolution.concept` | 38 |
| `resolution.identity_strategy` | 13 |
| `resolution.representation` | 23 |
| `resolution.representation_identity` | 13 |
| `resolution.concept_attribute` | 70 |
| `resolution.concept_attribute_value` | 45 |
| `resolution.concept_attribute_binding` | 7 |
| `resolution.concept_relationship` | 15 |
| `resolution.concept_relationship_binding` | 6 |
| `resolution.owning_subsystem` | 6 |
| Target concepts (`Ticket`…`AgendaItem`) | 0 |
| Target identity strategies | 0 |
| Target representations | 1 (existing Assessment) |
| Target representation identities | 0 |
| Target attributes / values | 0 / 0 |
| Target bindings | 0 |
| `nebula.assessments_history` | 0 |
| `peb.decisions` | 16 |
| `resolution.receipt` | 1997 |
| `nebula.agent_records` | 16,673 |
| `nebula.agendas` | 3 |
| `nebula.agenda_items` | 1 |
| `nebula.assessments` | 0 |
| `resolution.ticket` | 0 |
| V216 ledger rows | 0 |
| `representation.evaluation_role` column | absent |
| `assessments_history.assessment_kind` column | absent |
| `vision.canonical_work_requests` | absent |

The focused guard result on the same date was **9 passed**.
