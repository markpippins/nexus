# V222 live-apply runbook — P4a requirement port (operator-gated)

> **Status:** procedure only. This document does **not** authorize an apply.
> Live application requires an explicit operator decision naming this runbook.
>
> **Scope:** `sql/V222__port_requirements_to_resolution.sql`, plus its staged
> prerequisite `sql/V220__work_request_requirement_and_artifact_links.sql`
> (V220 is applied first when it is not already present).
> Out of scope: dropping the compatibility projection (see §8), the
> `resolution.candidate` port, and every other migration.
>
> **Migration character:** transactional, idempotent, non-destructive to the
> legacy store. V222 copies the current `nebula.requirements` rows into
> `resolution.requirement` preserving primary keys by value, writes a per-row
> SHA-256 identity ledger, replaces `nebula.requirements` in place with a
> write-through compatibility projection, and installs the requirement delete
> guard. It performs **no** `DELETE`/`TRUNCATE`/`DROP TABLE`/column drop of the
> legacy surface, and leaves `nebula.requirements_history` untouched as the
> pre-port audit record.

## Provenance

| Item | Value |
|---|---|
| Migration | `sql/V222__port_requirements_to_resolution.sql` |
| Prerequisite | `sql/V220__work_request_requirement_and_artifact_links.sql` |
| Focused guard | `bin/tests/test_v222_requirements_port.py` (37 tests, throwaway PostgreSQL) |
| Cross-surface guard | `bin/tests/test_requirement_delete_guard_parity.py` (10 tests) |
| Expected ledger row | `resolution.migration_ledger`, `schema_name='sql'`, `migration_label='V222__port_requirements_to_resolution'` |
| Identity ledger | `resolution.requirement_port_ledger` (one row per ported requirement) |
| Rehearsal record | §10 of this document (executed on throwaway databases) |
| Staged PR | **#1035** (draft) — branch `req-port-p4a` |
| Staged commits | `54dad12d9e3f1ce751148ed6a0f597bccb5d8686` (P4a port + endpoint validation) + CI-fix follow-up(s) on branch `req-port-p4a` — authoritative list is the PR #1035 commit history at merge time |
| Migration blob | V222 `2cbe02e9200407849b8cdf11936d1f030fcf95a2`; V220 prerequisite `b5fcac09b0660d812d1df29e111a9c9293a4a00a` |
| Merge commit | _pending — PR #1035 is draft, unmerged; record here at merge time_ |
| DBA ratification | _pending_ |
| Architect ratification | _pending_ |
| Tester attestation | _pending (R8: merge needs tester `can_verify_work_requests` attestation with evidence, not author self-declaration)_ |

V222 is currently **staged in the worktree `req-port-p4a` and unmerged**
(PR #1035, draft). There is therefore no merge commit or architect/DBA/tester
record to check; the operator window is the only gate, and this runbook is the
procedure that window follows once the change set is merged. Fill the pending
rows at merge time — do not treat their absence today as an authorization of
the contrary.

---

## 0. Roles and stop conditions

| Role | Responsibility |
|---|---|
| **Operator** | Opens the window, approves the apply, owns the R9 replication decision |
| **DBA** | Verifies preflight, executes or directly supervises the apply, confirms the ledger row |
| **Engineer** | Produces evidence, runs the guards and post-apply verification, records the result |

**Stop and do not apply** if any of the following is true:

- `sql/V220__work_request_requirement_and_artifact_links.sql` cannot be applied
  in this window, or fails to apply — without it V222 refuses at
  `V222-PREFLIGHT-006` because `resolution.request_requirement_link` is
  missing;
- `nebula.requirements` is not exactly the 22-column shape in §2.3;
- `resolution.migration_ledger` already holds the V222 row, or
  `resolution.requirement_port_ledger` holds a partial set (rows present but
  fewer than the current source rows);
- a legacy `asset_id` has no `resolution.canonical_asset` row (§2.6) — V222
  now refuses at `V222-PREFLIGHT-018` rather than aborting on
  `requirement_asset_id_fkey`;
- the verified backup is missing or its restore check (§3) failed;
- the rehearsal in §5 has not passed on a clone of that backup;
- another migration is in flight, or a service is writing requirements in a
  way the DBA has not accounted for;
- any command exits non-zero.

An unexplained difference is a stop, not a judgement call to make alone.
The rehearsal in §5 exists precisely so that a data-level surprise is found
before the window rather than by an aborted apply.

---

## 1. Automated-apply trace (nothing applies V222 on its own)

Requirement: prove that no runner, CI job, bootstrap script or startup path
applies V222 without an operator. Traced 2026-11-20 on this worktree.

| Path | Mechanism | Finding |
|---|---|---|
| `typescript/nebula-srv/src/index.ts:72` → `src/migrate.ts` | Startup file runner: `FILE_RE = /^(\d{3})-.*\.sql$/` over `path.resolve(__dirname,'..','migrations')`, applying pending numbered files above `nebula.schema_version`, gated by `NEBULA_MIGRATE_TARGET` + `migrations/attestations.json` | **Cannot select V222.** Its directory holds 73 `NNN-*.sql` files and no `V*` file; V222 lives in `sql/` and does not match the three-digit prefix. The target-identity and content-attestation gates cover only its own numbered set. |
| `sql/ci-bootstrap/nexus-ci-bootstrap.sql` + `.github/workflows/*` | CI loads the committed `pg_dump --schema-only` artifact | **Pre-V222 by construction.** The snapshot has the 22-column legacy view, no `resolution.request_requirement_link`, no `requirement_port_ledger`, no V222 ledger row. CI reloads it; it never replays `sql/V*.sql` in a loop. |
| `.github/workflows/wr-conf-0*.yml` | Each applies one pinned migration to a throwaway database (V179, V184, V185, V189, V190, V191, V198) | No glob, no loop, no V222 reference. |
| `bin/apply-v184-package.py`, `bin/import-knowledge-graph.sh` | Single-file appliers hard-coded to V184 / V083 | Not generic runners. |
| `bin/start-nexus-services.sh`, `bin/start-nexus-uis.sh`, `bin/freebuff-boot.py`, `ansible/`, systemd units/timers | Service/UI/boot orchestration | Execute no SQL migration. |
| `adonisjs/nexus-control-edge`, `moleculer/nebula`, `typescript/conduit-mcp` | Own migration mechanisms (Lucid / none / `migrations/0NN_*.sql`) | None reads `sql/`. |
| `python/expression/source_links.py:118-124` | `_resolve_migration()` globs `sql/V{version}__*.sql` | **Resolves provenance paths only; never executes SQL.** |

Evidence (run from the repository root):

```bash
rg -n "MIGRATIONS_DIR|FILE_RE" typescript/nebula-srv/src/migrate.ts | head -4
ls typescript/nebula-srv/migrations | grep -E '^V[0-9]' || echo "no V* files in the startup runner directory"

echo '--- non-comment sql/V* invocations in CI ---'
rg -n "sql/V[0-9]{3}__[A-Za-z0-9_.-]+\.sql" .github/workflows -g '*.yml' \
  | grep -v ':[0-9]*:#' | sort -u || echo "no CI job invokes a sql/V* file"

echo '--- deployment paths naming the V222 file ---'
rg -n "V222__port_requirements_to_resolution" .github bin scripts ansible typescript python adonisjs moleculer \
  --glob '!*test*' --glob '!*__tests__*' --glob '!*.spec.*' \
  || echo "no deployment path names the V222 file"

echo '--- anything shaped like an execution of it, tests excluded ---'
rg -n "psql[^\n]*V222|-f ?\"?\$?V222" .github bin scripts ansible typescript python adonisjs moleculer \
  --glob '!*test*' \
  || echo "nothing outside tests executes V222"

echo '--- every path that names it, tests included ---'
rg -l "V222__port_requirements_to_resolution" .github bin python typescript adonisjs moleculer sql docs | sort

echo '--- generic loops over sql/V* ---'
rg -n "for .*sql/V|glob\(.*sql/V|sorted\(.*sql/V" bin scripts ansible -g '!*.md' || echo "no loop applies sql/V*"
```

Expected: the runner's `MIGRATIONS_DIR`/`FILE_RE` lines, then
`no V* files in the startup runner directory`; `no CI job invokes a sql/V*
file` (the workflow `sql/V...` mentions are comments; each WR-conf job runs a
per-migration pytest module pinned to one file, never a loop);
`no deployment path names the V222 file`; `nothing outside tests executes
V222`; `no loop applies sql/V*`; and finally the only five paths that name it —
`sql/V222__port_requirements_to_resolution.sql` (the file itself),
`docs/runbook-v222-requirements-port-live-apply.md` (this document), and three
tests that apply it only to throwaway databases:
`bin/tests/test_v222_requirements_port.py`,
`bin/tests/test_requirement_delete_guard_parity.py` and
`adonisjs/nexus-control-edge/tests/functional/requirement-delete-guard.spec.ts`.

Prose mentions of `V222` in code comments and in `API-SPEC.md` are expected and
are not execution paths; the checks ask for the file name and for an execution
shape, which is what a runner would need.

**Conclusion: V222 is inert until an operator runs §6.** No fix was required.
If a future change adds a runner over `sql/V*.sql`, this section must be
re-verified before the next apply.

---

## 2. Preflight

Run from a clean checkout at the approved commit.

### 2.1 Session environment

```bash
export DB="${DB:?set DB to the target database name}"
export REPO_ROOT="$(git rev-parse --show-toplevel)"
export V222="$REPO_ROOT/sql/V222__port_requirements_to_resolution.sql"
export V220="$REPO_ROOT/sql/V220__work_request_requirement_and_artifact_links.sql"
export WORKDIR="${WORKDIR:-$REPO_ROOT/tmp/p4a-v222-apply}"
mkdir -p "$WORKDIR"
# Authenticate as usual (PGPASSWORD, ~/.pgpass). PGHOST/PGPORT/PGUSER default below.
export PGHOST="${PGHOST:-localhost}" PGPORT="${PGPORT:-5432}" PGUSER="${PGUSER:-pguser}"
```

### 2.2 Exact artifact

```bash
test -f "$V222" && echo "V222 present"
sha256sum "$V222"
git status --short -- "$V222" "$V220" docs/runbook-v222-requirements-port-live-apply.md
```

Expected: `V222 present`, a hash to record in the window log, and `git status`
listing **only** the reviewed change set — nothing else. Before the change set
is merged that is exactly `M V222...` and the untracked runbook; on a checkout
at the approved commit it is empty. A modified migration file or any extra path
means the bytes are not the reviewed bytes: stop.

### 2.3 The prerequisite V220 is applied

```bash
V220_STATE=$(psql -w -X -At -d "$DB" -c "
SELECT CASE WHEN to_regclass('resolution.request_requirement_link') IS NULL
            THEN 'MISSING' ELSE 'present' END;")
printf 'v220=%s\n' "$V220_STATE"
case "$V220_STATE" in
  present)  echo "V220 already applied" ;;
  MISSING)  echo "V220 will be applied in section 6, immediately before V222" ;;
  *)        echo "STOP: could not determine the V220 state"; exit 1 ;;
esac
```

Expected: `v220=present` or `v220=MISSING` with the follow-up line. V220 is
additive and idempotent, so section 6 applies it unconditionally and V222 then
finds its prerequisite in place.

### 2.4 The source is the 22-column legacy shape

```bash
SHAPE=$(psql -w -X -At -d "$DB" -c "
SELECT count(*)::text || '|' || string_agg(column_name, ',' ORDER BY ordinal_position)
  FROM information_schema.columns
 WHERE table_schema = 'nebula' AND table_name = 'requirements';")
EXPECTED="22|id,system_id,subsystem_id,feature_id,title,description,status,priority,start_date,completion_date,created_at,recorded_on_dt,recorded_until_dt,valid_from,valid_until,parent_id,req_type,acceptance_criteria,candidate_id,conduit_plan_id,work_request_dco,asset_id"
printf 'observed: %s\n' "$SHAPE"
[ "$SHAPE" = "$EXPECTED" ] || { echo "STOP: nebula.requirements is not the expected shape"; exit 1; }
echo "source shape OK"
```

Expected: `source shape OK`. Both relations must also be base tables:

```bash
psql -w -X -F '|' -A -d "$DB" -c "
SELECT 'relkinds',
       (SELECT relkind::text FROM pg_class WHERE oid = 'nebula.requirements_history'::regclass) AS source,
       (SELECT relkind::text FROM pg_class WHERE oid = 'resolution.requirement'::regclass) AS target;"
```

Expected: `relkinds|r|r`.

### 2.5 No prior or partial attempt

```bash
SOURCE_ROWS=$(psql -w -X -At -d "$DB" -c "
SELECT count(*) FROM nebula.requirements_history h
 WHERE now() >= h.recorded_on_dt AND now() < h.recorded_until_dt
   AND now() >= h.valid_from AND now() < h.valid_until;")

LEDGER_TABLE=$(psql -w -X -At -d "$DB" -c "
SELECT coalesce(to_regclass('resolution.requirement_port_ledger')::text, '');")

PORT_ROWS=0
if [ -n "$LEDGER_TABLE" ]; then
  PORT_ROWS=$(psql -w -X -At -d "$DB" -c "SELECT count(*) FROM resolution.requirement_port_ledger;")
fi

MIG_ROW=$(psql -w -X -At -d "$DB" -c "
SELECT count(*) FROM resolution.migration_ledger
 WHERE schema_name = 'sql' AND migration_label = 'V222__port_requirements_to_resolution';")

printf 'source_rows=%s port_ledger_rows=%s v222_ledger_rows=%s\n' "$SOURCE_ROWS" "$PORT_ROWS" "$MIG_ROW"
if [ "$PORT_ROWS" != "0" ] && [ "$PORT_ROWS" != "$SOURCE_ROWS" ]; then
  echo "STOP: partial port — the ledger holds $PORT_ROWS of $SOURCE_ROWS source rows"; exit 1
fi
if [ "$MIG_ROW" != "0" ] && [ "$PORT_ROWS" != "$SOURCE_ROWS" ]; then
  echo "STOP: V222 ledger row present without a complete port"; exit 1
fi
echo "no partial prior attempt"
```

Expected: `no partial prior attempt`. A complete prior apply (all source rows
portable, ledger complete) is a legitimate re-apply and V222 is idempotent for
it; anything in between is a stop.

### 2.6 Representability of `asset_id`

`resolution.requirement.asset_id` carries `requirement_asset_id_fkey` to
`resolution.canonical_asset`, while the legacy link points at a different
store, `semantics.canonical_asset`. Any legacy id absent from the resolution
table is unrepresentable, and V222 refuses it by name at
`V222-PREFLIGHT-018` (`N of M legacy asset link(s) have no
resolution.canonical_asset row`) instead of aborting mid-insert on
`requirement_asset_id_fkey`; a missing `resolution.canonical_asset` relation
stops at `V222-PREFLIGHT-017`. The asset link is identity, so the port neither
NULLs it nor fabricates canonical asset rows.

This check is the same predicate the migration asserts, run in its own
statement so the window fails here — before the backup is spent — rather than
inside the apply transaction:

```bash
ASSET_MISSING=$(psql -w -X -At -d "$DB" -c "
SELECT count(*) FROM nebula.requirements r
 WHERE r.asset_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM resolution.canonical_asset a WHERE a.id = r.asset_id);")
printf 'asset_ids_missing_from_resolution=%s\n' "$ASSET_MISSING"
[ "$ASSET_MISSING" = "0" ] || { echo "STOP: port or reconcile canonical_asset rows first (V222-PREFLIGHT-018 would refuse)"; exit 1; }
echo "asset links representable"
```

Expected: `asset links representable`. A non-zero count is a stop: populate
`resolution.canonical_asset` for those ids (or clear the link deliberately) and
re-run §2.6 and §5 before opening the window.

---

## 3. Backup and restore point

```bash
export BACKUP_DIR="${BACKUP_DIR:-$WORKDIR}"
mkdir -p "$BACKUP_DIR"
export BACKUP_FILE="$BACKUP_DIR/${DB}-pre-v222.dump"
pg_dump -w -Fc -d "$DB" -f "$BACKUP_FILE"
pg_restore -l "$BACKUP_FILE" | wc -l
sha256sum "$BACKUP_FILE"
ls -l "$BACKUP_FILE"
```

Expected: a non-zero TOC entry count, a sha256 and size recorded in the window
log, and the file retained until the window closes. Restore it now — an
unrestored backup is not a restore point:

```bash
export RESTORE_DB="${DB}_restore_verify"
dropdb -w --if-exists "$RESTORE_DB"
createdb -w "$RESTORE_DB"
pg_restore -w -d "$RESTORE_DB" "$BACKUP_FILE" >/dev/null

for q in \
  "SELECT count(*) FROM nebula.requirements" \
  "SELECT count(*) FROM nebula.requirements_history" \
  "SELECT count(*) FROM resolution.requirement" \
  "SELECT count(*) FROM nebula.blueprints_history"; do
  a=$(psql -w -X -At -d "$DB" -c "$q")
  b=$(psql -w -X -At -d "$RESTORE_DB" -c "$q")
  printf '%s -> %s / restored %s\n' "$q" "$a" "$b"
  [ "$a" = "$b" ] || { echo "STOP: restored backup disagrees with the source"; exit 1; }
done

dropdb -w "$RESTORE_DB"
echo "backup restores"
```

Expected: four matching count pairs and `backup restores`.

---

## 4. Build the verification and rollback artifacts

The same two SQL files are used for the rehearsal, the target apply and any
rollback, so no step can drift from another.

```bash
mkdir -p "$WORKDIR"

cat > "$WORKDIR/v222-verify.sql" <<'SQL'
\echo 'V222 verification — counts must be equal, every mismatch must be 0'
SELECT 'v1_counts', source_current, ledger_rows, canonical_ported, projection_rows
  FROM (SELECT
    (SELECT count(*) FROM nebula.requirements_history h
      WHERE now() >= h.recorded_on_dt AND now() < h.recorded_until_dt
        AND now() >= h.valid_from AND now() < h.valid_until) AS source_current,
    (SELECT count(*) FROM resolution.requirement_port_ledger
      WHERE source_relation = 'nebula.requirements') AS ledger_rows,
    (SELECT count(*) FROM resolution.requirement r
      WHERE EXISTS (SELECT 1 FROM resolution.requirement_port_ledger l
                     WHERE l.target_id = r.id)) AS canonical_ported,
    (SELECT count(*) FROM nebula.requirements) AS projection_rows) q;

SELECT 'v2_ledger_identity',
  (SELECT count(*) FROM resolution.requirement_port_ledger
    WHERE source_id <> target_id OR source_digest <> target_digest) AS identity_mismatches,
  (SELECT count(*) - count(DISTINCT source_id)
     FROM resolution.requirement_port_ledger
    WHERE source_relation = 'nebula.requirements') AS duplicate_sources;

WITH src AS (
  SELECT h.* FROM nebula.requirements_history h
   WHERE now() >= h.recorded_on_dt AND now() < h.recorded_until_dt
     AND now() >= h.valid_from AND now() < h.valid_until)
SELECT 'v3_field_parity', count(*) AS parity_mismatches
  FROM src h
  JOIN resolution.requirement_port_ledger l ON l.source_id = h.id
  JOIN resolution.requirement r ON r.id = l.target_id
 WHERE r.title IS DISTINCT FROM h.title
    OR r.description IS DISTINCT FROM coalesce(h.description, '')
    OR r.status IS DISTINCT FROM h.status
    OR r.priority IS DISTINCT FROM h.priority
    OR r.req_type IS DISTINCT FROM h.req_type
    OR r.system_id IS DISTINCT FROM h.system_id
    OR r.subsystem_id IS DISTINCT FROM h.subsystem_id
    OR r.feature_id IS DISTINCT FROM h.feature_id
    OR r.parent_id IS DISTINCT FROM h.parent_id
    OR r.candidate_id IS DISTINCT FROM h.candidate_id
    OR r.conduit_plan_id IS DISTINCT FROM h.conduit_plan_id
    OR r.asset_id IS DISTINCT FROM h.asset_id
    OR r.acceptance_criteria IS DISTINCT FROM h.acceptance_criteria
    OR r.start_date IS DISTINCT FROM h.start_date
    OR r.completion_date IS DISTINCT FROM h.completion_date
    OR r.created_at IS DISTINCT FROM h.created_at
    OR r.valid_from IS DISTINCT FROM h.valid_from
    OR r.recorded_on_dt IS DISTINCT FROM h.recorded_on_dt
    OR r.work_request_dco IS DISTINCT FROM h.work_request_dco;

WITH src AS (
  SELECT h.* FROM nebula.requirements_history h
   WHERE now() >= h.recorded_on_dt AND now() < h.recorded_until_dt
     AND now() >= h.valid_from AND now() < h.valid_until),
src_dist AS (SELECT status, priority, count(*) AS n FROM src GROUP BY status, priority),
dst_dist AS (
  SELECT r.status, r.priority, count(*) AS n
    FROM resolution.requirement r
    JOIN resolution.requirement_port_ledger l ON l.target_id = r.id
   GROUP BY r.status, r.priority)
SELECT 'v4_lifecycle', count(*) AS distribution_mismatches
  FROM ((SELECT * FROM src_dist EXCEPT SELECT * FROM dst_dist)
        UNION ALL
        (SELECT * FROM dst_dist EXCEPT SELECT * FROM src_dist)) d;

WITH proj AS (
  SELECT id, title, status, priority, req_type, system_id, subsystem_id, feature_id,
         parent_id, candidate_id, conduit_plan_id, asset_id, start_date, completion_date,
         created_at, valid_from, valid_until, recorded_until_dt
    FROM nebula.requirements),
canon AS (
  SELECT id, title, status, priority, req_type, system_id, subsystem_id, feature_id,
         parent_id, candidate_id, conduit_plan_id, asset_id, start_date, completion_date,
         created_at, valid_from,
         CASE WHEN valid_until >= '9999-12-31'::timestamptz
              THEN '9999-12-31 23:59:59+00'::timestamptz ELSE valid_until END AS valid_until,
         CASE WHEN recorded_until_dt >= '9999-12-31'::timestamptz
              THEN '9999-12-31 23:59:59+00'::timestamptz ELSE recorded_until_dt END AS recorded_until_dt
    FROM resolution.requirement r
   WHERE now() >= r.recorded_on_dt AND now() < r.recorded_until_dt
     AND now() >= r.valid_from AND now() < r.valid_until)
SELECT 'v5_projection', count(*) AS projection_rows_differing
  FROM ((SELECT * FROM proj EXCEPT SELECT * FROM canon)
        UNION ALL
        (SELECT * FROM canon EXCEPT SELECT * FROM proj)) d;

SELECT 'v6_blueprints',
  count(*) FILTER (WHERE b.payload ->> 'requirement_ref'
                     ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$') AS live_refs,
  count(*) FILTER (WHERE b.payload ->> 'requirement_ref'
                     ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                   AND NOT EXISTS (SELECT 1 FROM resolution.requirement r
                                    WHERE r.id = (b.payload ->> 'requirement_ref')::uuid)) AS unresolved_refs
  FROM nebula.blueprints_history b
 WHERE now() >= b.recorded_on_dt AND now() < b.recorded_until_dt
   AND now() >= b.valid_from AND now() < b.valid_until;

SELECT 'v7_objects', ledger_row, guard_triggers, guard_functions, projection_columns, candidate_fk
  FROM (SELECT
    (SELECT count(*) FROM resolution.migration_ledger
      WHERE migration_label = 'V222__port_requirements_to_resolution') AS ledger_row,
    (SELECT count(*) FROM pg_trigger
      WHERE tgrelid = 'resolution.requirement'::regclass
        AND tgname = 'trg_requirement_delete_guard' AND NOT tgisinternal) AS guard_triggers,
    (SELECT count(*) FROM pg_proc WHERE pronamespace = 'resolution'::regnamespace
        AND proname IN ('requirement_delete_blockers','requirement_delete_guard','delete_requirements')) AS guard_functions,
    (SELECT count(*) FROM information_schema.columns
      WHERE table_schema = 'nebula' AND table_name = 'requirements') AS projection_columns,
    (SELECT count(*) FROM pg_constraint
      WHERE conrelid = 'resolution.requirement'::regclass
        AND conname = 'requirement_candidate_id_fkey') AS candidate_fk) q;

-- The gate: identical predicates, failing loudly instead of printing a number.
DO $$
DECLARE
  v_src bigint; v_led bigint; v_can bigint; v_proj bigint; v_n bigint;
BEGIN
  SELECT count(*) INTO v_src FROM nebula.requirements_history h
   WHERE now() >= h.recorded_on_dt AND now() < h.recorded_until_dt
     AND now() >= h.valid_from AND now() < h.valid_until;
  SELECT count(*) INTO v_led FROM resolution.requirement_port_ledger
   WHERE source_relation = 'nebula.requirements';
  SELECT count(*) INTO v_can FROM resolution.requirement r
   WHERE EXISTS (SELECT 1 FROM resolution.requirement_port_ledger l WHERE l.target_id = r.id);
  SELECT count(*) INTO v_proj FROM nebula.requirements;

  IF NOT (v_src = v_led AND v_led = v_can AND v_led = v_proj) THEN
    RAISE EXCEPTION 'V222-VERIFY-001: counts differ (source=%, ledger=%, canonical=%, projection=%)',
      v_src, v_led, v_can, v_proj;
  END IF;

  IF EXISTS (SELECT 1 FROM resolution.requirement_port_ledger
              WHERE source_id <> target_id OR source_digest <> target_digest) THEN
    RAISE EXCEPTION 'V222-VERIFY-002: an identity ledger row does not match its source';
  END IF;

  IF (SELECT count(*) - count(DISTINCT source_id) FROM resolution.requirement_port_ledger
       WHERE source_relation = 'nebula.requirements') <> 0 THEN
    RAISE EXCEPTION 'V222-VERIFY-003: duplicate source ids in the identity ledger';
  END IF;

  SELECT count(*) INTO v_n
    FROM nebula.requirements_history h
    JOIN resolution.requirement_port_ledger l ON l.source_id = h.id
    JOIN resolution.requirement r ON r.id = l.target_id
   WHERE now() >= h.recorded_on_dt AND now() < h.recorded_until_dt
     AND now() >= h.valid_from AND now() < h.valid_until
     AND (r.title IS DISTINCT FROM h.title
       OR r.description IS DISTINCT FROM coalesce(h.description, '')
       OR r.status IS DISTINCT FROM h.status
       OR r.priority IS DISTINCT FROM h.priority
       OR r.req_type IS DISTINCT FROM h.req_type
       OR r.system_id IS DISTINCT FROM h.system_id
       OR r.subsystem_id IS DISTINCT FROM h.subsystem_id
       OR r.feature_id IS DISTINCT FROM h.feature_id
       OR r.parent_id IS DISTINCT FROM h.parent_id
       OR r.candidate_id IS DISTINCT FROM h.candidate_id
       OR r.conduit_plan_id IS DISTINCT FROM h.conduit_plan_id
       OR r.asset_id IS DISTINCT FROM h.asset_id
       OR r.acceptance_criteria IS DISTINCT FROM h.acceptance_criteria
       OR r.start_date IS DISTINCT FROM h.start_date
       OR r.completion_date IS DISTINCT FROM h.completion_date
       OR r.created_at IS DISTINCT FROM h.created_at
       OR r.valid_from IS DISTINCT FROM h.valid_from
       OR r.recorded_on_dt IS DISTINCT FROM h.recorded_on_dt
       OR r.work_request_dco IS DISTINCT FROM h.work_request_dco);
  IF v_n <> 0 THEN
    RAISE EXCEPTION 'V222-VERIFY-004: % ported row(s) differ from their source', v_n;
  END IF;

  IF EXISTS (
      WITH src AS (
        SELECT h.* FROM nebula.requirements_history h
         WHERE now() >= h.recorded_on_dt AND now() < h.recorded_until_dt
           AND now() >= h.valid_from AND now() < h.valid_until),
      a AS (SELECT status, priority, count(*) AS n FROM src GROUP BY status, priority),
      b AS (SELECT r.status, r.priority, count(*) AS n FROM resolution.requirement r
              JOIN resolution.requirement_port_ledger l ON l.target_id = r.id
             GROUP BY r.status, r.priority)
      SELECT 1 FROM ((SELECT * FROM a EXCEPT SELECT * FROM b)
                     UNION ALL (SELECT * FROM b EXCEPT SELECT * FROM a)) d) THEN
    RAISE EXCEPTION 'V222-VERIFY-005: lifecycle distribution changed';
  END IF;

  IF EXISTS (
      WITH proj AS (
        SELECT id, title, status, priority, req_type, system_id, subsystem_id, feature_id,
               parent_id, candidate_id, conduit_plan_id, asset_id, start_date, completion_date,
               created_at, valid_from, valid_until, recorded_until_dt
          FROM nebula.requirements),
      canon AS (
        SELECT id, title, status, priority, req_type, system_id, subsystem_id, feature_id,
               parent_id, candidate_id, conduit_plan_id, asset_id, start_date, completion_date,
               created_at, valid_from,
               CASE WHEN valid_until >= '9999-12-31'::timestamptz
                    THEN '9999-12-31 23:59:59+00'::timestamptz ELSE valid_until END AS valid_until,
               CASE WHEN recorded_until_dt >= '9999-12-31'::timestamptz
                    THEN '9999-12-31 23:59:59+00'::timestamptz ELSE recorded_until_dt END AS recorded_until_dt
          FROM resolution.requirement r
         WHERE now() >= r.recorded_on_dt AND now() < r.recorded_until_dt
           AND now() >= r.valid_from AND now() < r.valid_until)
      SELECT 1 FROM ((SELECT * FROM proj EXCEPT SELECT * FROM canon)
                     UNION ALL (SELECT * FROM canon EXCEPT SELECT * FROM proj)) d) THEN
    RAISE EXCEPTION 'V222-VERIFY-006: the compatibility projection disagrees with the canonical table';
  END IF;

  IF EXISTS (
      SELECT 1 FROM nebula.blueprints_history b
       WHERE now() >= b.recorded_on_dt AND now() < b.recorded_until_dt
         AND now() >= b.valid_from AND now() < b.valid_until
         AND b.payload ->> 'requirement_ref'
             ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
         AND NOT EXISTS (SELECT 1 FROM resolution.requirement r
                          WHERE r.id = (b.payload ->> 'requirement_ref')::uuid)) THEN
    RAISE EXCEPTION 'V222-VERIFY-007: a live blueprint requirement_ref no longer resolves';
  END IF;

  IF (SELECT count(*) FROM resolution.migration_ledger
       WHERE migration_label = 'V222__port_requirements_to_resolution') <> 1 THEN
    RAISE EXCEPTION 'V222-VERIFY-008: the V222 migration ledger row is missing or duplicated';
  END IF;

  IF (SELECT count(*) FROM pg_trigger
       WHERE tgrelid = 'resolution.requirement'::regclass
         AND tgname = 'trg_requirement_delete_guard' AND NOT tgisinternal) <> 1 THEN
    RAISE EXCEPTION 'V222-VERIFY-009: the requirement delete guard trigger is missing';
  END IF;

  IF (SELECT count(*) FROM pg_proc WHERE pronamespace = 'resolution'::regnamespace
       AND proname IN ('requirement_delete_blockers','requirement_delete_guard','delete_requirements')) <> 3 THEN
    RAISE EXCEPTION 'V222-VERIFY-010: the delete guard functions are incomplete';
  END IF;

  IF (SELECT count(*) FROM information_schema.columns
       WHERE table_schema = 'nebula' AND table_name = 'requirements') <> 22 THEN
    RAISE EXCEPTION 'V222-VERIFY-011: the compatibility projection is not 22 columns';
  END IF;

  RAISE NOTICE 'V222 verification passed';
END $$;
SQL

cat > "$WORKDIR/v222-rollback-precheck.sql" <<'SQL'
\echo 'V222 rollback precheck — every value must be 0'
SELECT 'r0_precheck',
  (SELECT count(*) FROM resolution.requirement r
     WHERE NOT EXISTS (SELECT 1 FROM resolution.requirement_port_ledger l
                        WHERE l.target_id = r.id)) AS post_port_rows,
  (SELECT count(*) FROM resolution.specification s
     JOIN resolution.requirement_port_ledger l ON l.target_id = s.requirement_id) AS spec_refs,
  (SELECT count(*) FROM resolution.implementation_plan p
     JOIN resolution.requirement_port_ledger l ON l.target_id = p.requirement_id) AS plan_refs,
  (SELECT count(*) FROM resolution.requirement_segment_set ss
     JOIN resolution.requirement_port_ledger l ON l.target_id = ss.requirement_id) AS segment_refs,
  (SELECT count(*) FROM resolution.request_requirement_link rl
     JOIN resolution.requirement_port_ledger l ON l.target_id = rl.requirement_id) AS link_refs,
  (SELECT count(*) FROM resolution.work_request w
     JOIN resolution.requirement_port_ledger l ON l.target_id = w.source_requirement_id) AS work_request_refs;

DO $$
DECLARE v record;
BEGIN
  SELECT
    (SELECT count(*) FROM resolution.requirement req
       WHERE NOT EXISTS (SELECT 1 FROM resolution.requirement_port_ledger l
                          WHERE l.target_id = req.id)) AS post_port,
    (SELECT count(*) FROM resolution.specification s
       JOIN resolution.requirement_port_ledger l ON l.target_id = s.requirement_id)
    + (SELECT count(*) FROM resolution.implementation_plan p
       JOIN resolution.requirement_port_ledger l ON l.target_id = p.requirement_id)
    + (SELECT count(*) FROM resolution.requirement_segment_set ss
       JOIN resolution.requirement_port_ledger l ON l.target_id = ss.requirement_id)
    + (SELECT count(*) FROM resolution.request_requirement_link rl
       JOIN resolution.requirement_port_ledger l ON l.target_id = rl.requirement_id)
    + (SELECT count(*) FROM resolution.work_request w
       JOIN resolution.requirement_port_ledger l ON l.target_id = w.source_requirement_id) AS refs
  INTO v;

  IF v.post_port <> 0 THEN
    RAISE EXCEPTION 'V222-ROLLBACK-001: % requirement(s) were created or adopted after the port; a rollback would delete rows the port never copied', v.post_port;
  END IF;
  IF v.refs <> 0 THEN
    RAISE EXCEPTION 'V222-ROLLBACK-002: % reference(s) to ported requirements exist; retire or repoint them before rolling back', v.refs;
  END IF;
  RAISE NOTICE 'rollback precheck passed';
END $$;
SQL

cat > "$WORKDIR/v222-rollback.sql" <<'SQL'
BEGIN;
CREATE TEMP TABLE _v222_rb ON COMMIT DROP AS
SELECT source_id FROM resolution.requirement_port_ledger
 WHERE provenance = 'v222:port-p4a';

-- Restore the pre-V222 definition of the relation. CREATE OR REPLACE keeps
-- dependants that select from it; a DROP would need CASCADE.
CREATE OR REPLACE VIEW nebula.requirements AS
SELECT id, system_id, subsystem_id, feature_id, title, description, status,
       priority, start_date, completion_date, created_at, recorded_on_dt,
       recorded_until_dt, valid_from, valid_until, parent_id, req_type,
       acceptance_criteria, candidate_id, conduit_plan_id, work_request_dco,
       asset_id
  FROM nebula.requirements_history
 WHERE now() >= recorded_on_dt AND now() < recorded_until_dt
   AND now() >= valid_from AND now() < valid_until;

DROP TRIGGER IF EXISTS trg_requirements_compat_write ON nebula.requirements;
DROP TRIGGER IF EXISTS trg_requirement_delete_guard ON resolution.requirement;
DROP FUNCTION IF EXISTS resolution.delete_requirements(uuid[]);
DROP FUNCTION IF EXISTS resolution.requirement_delete_guard();
DROP FUNCTION IF EXISTS resolution.requirement_delete_blockers(uuid, uuid[]);
DROP TABLE IF EXISTS resolution.requirement_port_ledger;
DELETE FROM resolution.requirement
 WHERE id IN (SELECT source_id FROM _v222_rb);
ALTER TABLE resolution.requirement DROP COLUMN IF EXISTS work_request_dco;
DELETE FROM resolution.migration_ledger
 WHERE migration_label = 'V222__port_requirements_to_resolution';
COMMIT;
SQL

cat > "$WORKDIR/v222-rolled-back-check.sql" <<'SQL'
\echo 'post-rollback state — the pre-port surface must be back'
SELECT 'r1_restored', view_rows, canonical_rows, ledger_table, dco_column, port_triggers, ledger_row
  FROM (SELECT
    (SELECT count(*) FROM nebula.requirements) AS view_rows,
    (SELECT count(*) FROM resolution.requirement) AS canonical_rows,
    (SELECT count(*) FROM information_schema.tables
      WHERE table_schema = 'resolution' AND table_name = 'requirement_port_ledger') AS ledger_table,
    (SELECT count(*) FROM information_schema.columns
      WHERE table_schema = 'resolution' AND table_name = 'requirement'
        AND column_name = 'work_request_dco') AS dco_column,
    (SELECT count(*) FROM pg_trigger
      WHERE tgname IN ('trg_requirements_compat_write','trg_requirement_delete_guard')
        AND NOT tgisinternal) AS port_triggers,
    (SELECT count(*) FROM resolution.migration_ledger
      WHERE migration_label = 'V222__port_requirements_to_resolution') AS ledger_row) q;

DO $$
DECLARE r record;
BEGIN
  SELECT
    (SELECT count(*) FROM nebula.requirements) AS view_rows,
    (SELECT count(*) FROM resolution.requirement) AS canonical_rows,
    (SELECT count(*) FROM information_schema.tables
      WHERE table_schema = 'resolution' AND table_name = 'requirement_port_ledger') AS ledger_table,
    (SELECT count(*) FROM information_schema.columns
      WHERE table_schema = 'resolution' AND table_name = 'requirement'
        AND column_name = 'work_request_dco') AS dco_column,
    (SELECT count(*) FROM pg_trigger
      WHERE tgname IN ('trg_requirements_compat_write','trg_requirement_delete_guard')
        AND NOT tgisinternal) AS port_triggers,
    (SELECT count(*) FROM resolution.migration_ledger
      WHERE migration_label = 'V222__port_requirements_to_resolution') AS ledger_row
  INTO r;

  IF r.canonical_rows <> 0 OR r.ledger_table <> 0 OR r.dco_column <> 0
     OR r.port_triggers <> 0 OR r.ledger_row <> 0 THEN
    RAISE EXCEPTION 'V222-ROLLBACK-003: rollback left port machinery behind (canonical=%, ledger_table=%, dco_column=%, triggers=%, ledger_row=%)',
      r.canonical_rows, r.ledger_table, r.dco_column, r.port_triggers, r.ledger_row;
  END IF;
  IF r.view_rows <> (SELECT count(*) FROM nebula.requirements_history h
                      WHERE now() >= h.recorded_on_dt AND now() < h.recorded_until_dt
                        AND now() >= h.valid_from AND now() < h.valid_until) THEN
    RAISE EXCEPTION 'V222-ROLLBACK-004: the restored view does not expose the legacy current rows';
  END IF;
  RAISE NOTICE 'rollback restored the pre-port surface';
END $$;
SQL

ls -l "$WORKDIR"
```

Expected: four SQL files listed.

---

## 5. Rehearsal on a clone of the backup

Never rehearse against the target. This clone is the backup restored (§3), and
the rehearsal proves the backup, the apply, the verification, the rollback and
the re-apply in that order.

```bash
export CLONE="${DB}_v222_rehearsal"
dropdb -w --if-exists "$CLONE"
createdb -w "$CLONE"
pg_restore -w -d "$CLONE" "$BACKUP_FILE" >/dev/null
echo "clone restored from $BACKUP_FILE"

# V220 is a no-op when already applied (additive, idempotent).
psql -w -q -v ON_ERROR_STOP=1 -d "$CLONE" -f "$V220"
echo "V220 present on clone"

psql -w -q -v ON_ERROR_STOP=1 -d "$CLONE" -f "$V222"
echo "V222 applied to clone"

psql -w -X -F '|' -A -d "$CLONE" -v ON_ERROR_STOP=1 -f "$WORKDIR/v222-verify.sql"
echo "clone verify passed"

psql -w -X -F '|' -A -d "$CLONE" -v ON_ERROR_STOP=1 -f "$WORKDIR/v222-rollback-precheck.sql"
psql -w -q -v ON_ERROR_STOP=1 -d "$CLONE" -f "$WORKDIR/v222-rollback.sql"
echo "clone rollback executed"

psql -w -X -F '|' -A -d "$CLONE" -v ON_ERROR_STOP=1 -f "$WORKDIR/v222-rolled-back-check.sql"

psql -w -q -v ON_ERROR_STOP=1 -d "$CLONE" -f "$V222"
echo "V222 re-applied to clone"

psql -w -X -F '|' -A -d "$CLONE" -v ON_ERROR_STOP=1 -f "$WORKDIR/v222-verify.sql"
echo "clone re-apply verify passed"

dropdb -w "$CLONE"
echo "rehearsal complete; clone dropped"
```

Expected, in order: `clone restored from ...`; `V220 present on clone`;
`V222 applied to clone`; the seven verification rows with equal counts and zero
mismatches followed by `V222 verification passed`; the rollback precheck row of
zeros and `rollback precheck passed`; `clone rollback executed`; the restored
row (`canonical_rows=0`, `ledger_table=0`, `dco_column=0`, `port_triggers=0`,
`ledger_row=0`, `view_rows` equal to the legacy current-row count) followed by
`rollback restored the pre-port surface`; `V222 re-applied to clone`;
`clone re-apply verify passed`; `rehearsal complete; clone dropped`.

Any deviation is a stop: fix the cause and re-run the whole section on a fresh
clone.

---

## 6. Apply to the target

```bash
test "${V222_APPLY_APPROVED:-no}" = "yes" \
  || { echo "refusing: set V222_APPLY_APPROVED=yes only inside the approved window"; exit 1; }
test -s "${BACKUP_FILE:?backup file from §3}" \
  || { echo "refusing: no verified backup from §3"; exit 1; }

psql -w -q -v ON_ERROR_STOP=1 -d "$DB" -f "$V220"
echo "V220 ensured"
psql -w -q -v ON_ERROR_STOP=1 -d "$DB" -f "$V222"
echo "V222 applied to $DB"
```

Expected: `V220 ensured`, `V222 applied to <db>`. Two `NOTICE` lines are
expected and benign on a first apply — the cross-domain candidate link notice
(`V222: N legacy candidate link(s) are cross-domain ...`) and `does not exist,
skipping` for triggers being created for the first time. Any `ERROR` aborts the
transaction: nothing is written, and the pre-state is intact.

---

## 7. Post-apply verification (target)

**Ordering requirement.** Verify before serving writes. The apply replaces
`nebula.requirements` with the write-through projection, so from the moment §6
commits, every consumer write goes through the compatibility triggers into
`resolution.requirement`. Run this section to completion, with every check at
its expected value, **before** taking any consumer out of maintenance or
letting application traffic resume writing through the projection. A
write-first ordering would mix unverified rows into the surface these queries
compare, so a parity failure could no longer be attributed to the port — and a
real parity failure would then be written through, not observed.

```bash
psql -w -X -F '|' -A -d "$DB" -v ON_ERROR_STOP=1 -f "$WORKDIR/v222-verify.sql"
echo "TARGET VERIFICATION PASSED"
```

Expected:

| Check | Meaning | Expected |
|---|---|---|
| `v1_counts` | `source_current`, `ledger_rows`, `canonical_ported`, `projection_rows` | all four equal (27 on the live surface as measured 2026-11-20; re-measure, do not assume) |
| `v2_ledger_identity` | `source_id = target_id` and digest equality per row; duplicate sources | `0 | 0` |
| `v3_field_parity` | title, description, lifecycle, hierarchy, governance links, timestamps, bitemporal window and `work_request_dco` vs the frozen history | `0` |
| `v4_lifecycle` | status+priority distribution parity | `0` |
| `v5_projection` | `nebula.requirements` vs canonical, both directions, sentinels normalized | `0` |
| `v6_blueprints` | live blueprint `requirement_ref`s that resolve / that do not | `unresolved_refs = 0` |
| `v7_objects` | V222 ledger row, guard trigger, guard functions, projection columns, candidate FK | `1 | 1 | 3 | 22 | 0` on a live apply where `resolution.candidate` is empty (`1` when that store is populated) |

and `V222 verification passed` followed by `TARGET VERIFICATION PASSED`.

Only after that output is a target that consumers may write to again. Until
then the window stays closed (services in maintenance, or their requirement
write paths paused) — §6 has already committed, so a resumed writer would be
writing through the projection this section has not yet cleared.

---

## 8. Completing the transition: consumers and the drop condition

The prerequisite for everything in this section is §7: post-apply verification
must pass before consumers resume writing through the compatibility view. Until
it does, treat `nebula.requirements` as read-only infrastructure under the DBA's
control, not as a surface to hand back to the services.

> **Ordering hazard — V221 vs the delete guard (found 2026-11-21, fixed in V222
> the same day, before any live apply).** The guard originally read
> `resolution.work_request.source_requirement_id` as a delete blocker, but V221
> drops that column (`sql/V221__ac11_drop_source_requirement_shortcut.sql:104`)
> and asserts it is gone (`:247`). In the plan-of-record order the function is
> still created (plpgsql resolves column references at first execution), so the
> apply's postconditions pass and the failure appears on the first delete or
> cascade as `column w.source_requirement_id does not exist` — a 500, not the
> intended 409. **It is now fixed:** the guard reads
> `resolution.request_requirement_link` where `relation = 'source'` and keeps the
> legacy column visible through `to_jsonb`, so it is valid on both sides of V221.
> `test_delete_guard_is_v221_safe` drops the column before applying the port and
> proves both the `P4A01` refusal and a clean unreferenced delete. The V222 test
> fixture recreates the pre-V221 column, which is why the original suite could
> not catch it; the order test is the regression guard. §7's target probe should
> still delete and cascade once, to confirm the installed function behaves the
> same on the real schema.

The projection is a transition, not the destination. Every consumer below
resolves through `nebula.requirements` today and is served by the write-through
projection; each must move to `resolution.requirement` before the projection can
be dropped. Enumerate what this target actually has:

```bash
echo '--- views that select from the projection ---'
psql -w -X -At -d "$DB" -c "
SELECT DISTINCT v.relnamespace::regnamespace::text || '.' || v.relname
  FROM pg_depend d
  JOIN pg_rewrite r ON r.oid = d.objid
  JOIN pg_class v ON v.oid = r.ev_class
 WHERE d.refobjid = 'nebula.requirements'::regclass
   AND d.deptype = 'n'
   AND v.oid <> 'nebula.requirements'::regclass
 ORDER BY 1;"

echo '--- routines whose body names the projection ---'
psql -w -X -At -d "$DB" -c "
SELECT n.nspname || '.' || p.proname
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname IN ('nebula','resolution')
   AND p.prosrc ~* '(nebula\.)?requirements\y'
   AND p.proname NOT IN ('requirement_delete_blockers','requirement_delete_guard','delete_requirements')
 ORDER BY 1;"

echo '--- code paths still naming the projection ---'
rg -l "nebula\.requirements|\bFROM requirements\b|\bJOIN requirements\b|\bINTO requirements\b|\bUPDATE requirements\b|\bDELETE FROM requirements\b" \
   typescript python bin adonisjs moleculer \
   --glob '!*test*' --glob '!*__tests__*' --glob '!**/migrations/*' --glob '!node_modules' --glob '!dist' \
   | sort \
  || echo "no code path references the projection (drop condition 3 satisfied)"
```

The three V222 guard functions are excluded from the routine query on purpose:
their bodies operate on `resolution.requirement` and mention
`requirements`/`child_requirements` only as names, so they are not consumers.

Observed on the CI bootstrap snapshot (2026-11-20): **no dependent views**, one
routine (`nebula.assess_ripple`, defined by `typescript/nebula-srv/migrations/038-ripple-assessment.sql`),
and exactly the code paths in the table below. On a fuller target the first two
queries also list `nebula.v_requirements_with_questions` (035),
`nebula.v_requirements_full_status` (036) and `nebula.v_work_request_requirement`
(V221), which are created by the nebula-srv runner and by V221.

| # | Consumer | Current read/write path | Required change for the base table |
|---|---|---|---|
| 1 | `typescript/nebula-srv/src/routes.ts` | `requirements` on `search_path=nebula` and `nebula.requirements`: list, get, children, create, patch, batch status, move, dependencies, compile, search, counts, plan-ref validation | Qualify `resolution.requirement`; add the current-segment predicate `now() BETWEEN recorded_on_dt AND recorded_until_dt AND now() BETWEEN valid_from AND valid_until` to every read; on INSERT derive `source_type` (`candidate` when `candidate_id` is present, else `manual`) and write `recorded_until_dt`/`valid_until` as `'infinity'`; keep `work_request_dco` (now a canonical column); deletes already go through `resolution.delete_requirements` |
| 2 | `typescript/nebula-srv/src/requirement-delete.ts` | `SELECT id FROM nebula.requirements WHERE <scope> = $1` | Same relation + predicate; the blocker/delete calls are already canonical and unchanged |
| 3 | `moleculer/nebula/src/routes.ts` | The same API surface as nebula-srv | The same change as row 1 |
| 4 | `moleculer/nebula/src/requirement-delete.ts` | Same scope query as row 2 | Same relation + predicate |
| 5 | `adonisjs/nexus-control-edge/app/controllers/nebula_hierarchy_controller.ts` | `requirements` on the `nebula` connection: list, get, children, create, patch, move, demote (re-parent updates) | Same relation + predicate; INSERT must supply the derived `source_type`; the re-parent UPDATEs keep working because `system_id`/`subsystem_id`/`feature_id`/`parent_id` are canonical columns |
| 6 | `adonisjs/nexus-control-edge/app/controllers/nebula_harvest_controller.ts` | Promotion INSERT plus candidate/agent-record joins | `INSERT INTO resolution.requirement (...)` with derived `source_type`; joins move to `resolution.requirement` + predicate |
| 7 | `adonisjs/nexus-control-edge/app/controllers/nebula_docs_controller.ts` | Idempotent `INSERT INTO requirements` alongside a history-table guard | Same INSERT change; the guard stays on `requirements_history` as pre-port audit |
| 8 | `adonisjs/nexus-control-edge/app/controllers/nebula_meta_controller.ts` | Search (`result_type = 'requirement'`), open-question entity titles, requirement count | Relation + predicate |
| 9 | `adonisjs/nexus-control-edge/app/services/requirement_delete_guard.ts` | Same scope query as row 2 | Same relation + predicate |
| 10 | `python/tackle/intent_requirement_promote.py` | Idempotency `SELECT` by `candidate_id`, promotion `INSERT` | Relation + predicate on the read; the INSERT must set `source_type` and `compilation_status` (`resolution.requirement` has no defaults for those) |
| 11 | `python/tackle/resolve_requirements.py` | `UPDATE ... SET status = 'Done'` and a status aggregate | Relation only; the bitemporal `valid_until`/`recorded_until_dt` split that the legacy trigger performed must be explicit if it is still required |
| 12 | `python/tackle/req_compiler.py` | Requirement + hierarchy read; cross-reference title subselect | Relation + predicate (two sites) |
| 13 | `python/tackle/planner_mcp_server.py` | Three reads (`Done` sweeps, similarity search, hierarchy lookup) | Relation + predicate |
| 14 | `python/tackle/harness/analyst.py`, `python/tackle/harness/architect.py` | Analyst requirement read and cross-reference title subselects; Architect `ToDo` sweep | Relation + predicate |
| 15 | `python/cascade/evaluators/kg_impact.py` | `SELECT ... WHERE candidate_id = %s` | Relation + predicate |
| 16 | `python/nebula/wc_consumers.py` | Audit aggregates (`requirements_total`, candidate-linked, plan-linked) | Relation (counts are over current rows; keep the predicate) |
| 17 | `bin/architect_process_todo.py` | `SELECT`/`UPDATE`/`INSERT` on requirements (including a recursive descendant walk) | Relation + predicate; INSERT must supply `source_type` |
| 18 | `bin/compiler_processInProgress.py` | `InProgress` sweep, `work_request_dco` read and update | Relation only; `work_request_dco` is a canonical column after V222 |
| 19 | `bin/transcript_rebuild.py` | Names `nebula.requirements` in a table allowlist **and emits an unconditional `DELETE FROM nebula.requirements` per row** in `wipe_pg()` | Behaviour change: after the port each delete reaches the canonical guard, so the whole `BEGIN … COMMIT` wipe rolls back if any requirement is still referenced (spec, plan, segment set, request link, work request, child, live blueprint). Run it only after those are retired, or repoint the requirements step at `resolution.delete_requirements(ids)` and report a refusal instead of crashing |
| 20 | `typescript/conduit-mcp/src/db.ts` | Two `NOT EXISTS (SELECT 1 FROM nebula.requirements ... conduit_plan_id = ...)` audits | Relation (the plan-link column is canonical) |
| 21 | `nebula.assess_ripple(uuid)` (migration 038) | plpgsql body reads `nebula.requirements` (descendant walk, hierarchy, ripple) | `CREATE OR REPLACE FUNCTION` against `resolution.requirement` with the current-segment predicate (body references are not tracked by `pg_depend`, which is why §8 checks `pg_proc.prosrc`) |
| 22 | `nebula.v_requirements_with_questions` (035), `nebula.v_requirements_full_status` (036), `nebula.v_work_request_requirement` (V221) | Views selecting from the projection | Recreate each `CREATE OR REPLACE VIEW` over `resolution.requirement` (+ predicate) before the projection is dropped |
| 23 | `typescript/nebula-srv/API-SPEC.md` | Documents reads against the transition surface | Documentation update in the same change as row 1 |
| 24 | `scripts/bash/restore-nebula.sh` | Post-restore `count(*)` verification | No read-path change, but a restore from a pre-V222 dump recreates the legacy view and silently un-ports the surface: re-apply V222 and assert the compat trigger, not just the count |
| 25 | `bin/legacy/bin/substance_segment_backfill.py` | Requirement-by-candidate read, `valid_until > now()` filter, `requirement_segment_sets` backfill | Relation; the legacy filter is redundant (the projection already hides closed segments) but harmless because the sentinel is preserved |
| 26 | `typescript/nebula-mcp/src/api/nebulaClient.ts` | HTTP client of row 1 (list, create, patch, delete, batch, move) | No SQL; no change at apply. `DELETE` can now return the 409 blocker body, which tool callers must expect |
| 27 | `angular/nexus-console/src/services/nebula.service.ts` (+ `nebula-panel`) | HTTP client of row 1 (`GET /api/requirements`, `PATCH /api/requirements/:id`) | No SQL; no change at apply while row 1 keeps its JSON shape |

**Drop condition.** The projection may be dropped only when all of the
following hold, in one reviewed change:

1. every consumer in the table above (and anything the §8 enumeration prints)
   reads `resolution.requirement`, deployed and observed;
2. the §8 enumeration queries return **zero** views and **zero** routines — a
   non-empty result means an object still depends on the relation and the drop
   would fail or, worse, a routine would begin failing at call time;
3. `rg` finds no runtime reference outside `sql/V222`, tests and docs;
4. §7 still passes on the target (the canonical table is the only source of
   truth and every reference resolves);
5. a follow-up migration drops `trg_requirements_compat_write` and
   `nebula.requirements` in one transaction, keeping
   `nebula.requirements_history` (pre-port audit), the identity ledger and the
   delete guard. `resolution.requirement.work_request_dco` is retired
   separately when compiled output moves to `sol_ir_expression_id`.

Until then the projection is load-bearing by design: dropping it earlier breaks
every consumer that has not moved, and leaving it forever makes the transition
permanent by omission.

---

## 9. Rollback (target)

Rollback is safe only while nothing created or adopted a requirement after the
port and nothing references a ported requirement.

```bash
psql -w -X -F '|' -A -d "$DB" -v ON_ERROR_STOP=1 -f "$WORKDIR/v222-rollback-precheck.sql"
```

Non-zero in any precheck column is a stop: retire or repoint those references
deliberately; do not `CASCADE`.

```bash
psql -w -q -v ON_ERROR_STOP=1 -d "$DB" -f "$WORKDIR/v222-rollback.sql"
psql -w -X -F '|' -A -d "$DB" -v ON_ERROR_STOP=1 -f "$WORKDIR/v222-rolled-back-check.sql"
```

Expected: `rollback precheck passed`; then `r1_restored` with
`canonical_rows=0`, `ledger_table=0`, `dco_column=0`, `port_triggers=0`,
`ledger_row=0` and `view_rows` equal to the legacy current-row count; then
`rollback restored the pre-port surface`. The legacy history rows were never
modified, so the restored view serves exactly the pre-port surface.

Re-applying after a rollback is supported and is what §5 exercises:

```bash
psql -w -q -v ON_ERROR_STOP=1 -d "$DB" -f "$V222"
psql -w -X -F '|' -A -d "$DB" -v ON_ERROR_STOP=1 -f "$WORKDIR/v222-verify.sql"
echo "RE-APPLY VERIFIED"
```

Expected: `V222 verification passed` then `RE-APPLY VERIFIED`.

---

## 10. Rehearsal record (2026-11-20)

Executed from the `req-port-p4a` worktree against throwaway databases created
from `sql/ci-bootstrap/nexus-ci-bootstrap.sql` (the committed `pg_dump
--schema-only` of the live PG17 schema), seeded with three representative
requirements (system-level, child with a candidate link, feature-level with a
compiled DCO), two live blueprints naming requirements and one closed
blueprint. That pre-state reproduces the production shape: 22 columns, V220
absent until applied, `resolution.candidate` empty.

| Step | Observed |
|---|---|
| Preflight | `source shape OK`, `relkinds|r|r`, `no partial prior attempt`, `asset links representable` |
| Backup | dump written, TOC listed, sha256 recorded, restore counts equal, `backup restores` |
| Clone rehearsal | `V222 applied to clone`; verification `3|3|3|3`, `0|0`, `0`, `0`, `0`, `2|0`, `1|1|3|22|0`; `rollback precheck passed`; `r1_restored ... 3|0|0|0|0|0`; `rollback restored the pre-port surface`; `V222 re-applied to clone`; `clone re-apply verify passed` |
| Target sequence | `V222 applied`; `TARGET VERIFICATION PASSED`; rollback `r1_restored` clean; `rollback restored the pre-port surface`; re-apply `RE-APPLY VERIFIED` |

The focused suites were run in the same session:
`bin/tests/test_v222_requirements_port.py` (37 tests) and
`bin/tests/test_requirement_delete_guard_parity.py` (10 tests) — `47 passed in
12.25s`, exit 0. Neither suite nor this rehearsal touched a live or shared
database.

---

## 11. Cleanup

```bash
rm -rf "$WORKDIR"
```

The backup file survives only if `BACKUP_DIR` was set outside `$WORKDIR`; keep
it until the window closes and the R9 replication decision is recorded
(checklist in §12).

---

## 12. R9 — vanadium replication checklist (V222 objects)

R9 replicates the **result**, not the migration: vanadium receives the approved
PG backup / live-sync stream, so **V222 must not be re-run there** (the file is
STAGED; its preflight would refuse or double-port). The operator decides whether
this apply is replicated — never automatically — then verifies every object
below on vanadium after the sync completes and records the result against this
window.

| # | Object | Verification on vanadium | Expected |
|---|---|---|---|
| 1 | `resolution.migration_ledger` row | `SELECT count(*) … WHERE migration_label = 'V222__port_requirements_to_resolution'` | `1` |
| 2 | `resolution.requirement_port_ledger` | `to_regclass(...)`; `count(*)`; `count(DISTINCT source_id)` | table; `27`; `27` |
| 3 | ledger identity + digests | `SELECT count(*) … WHERE source_id <> target_id OR source_digest <> target_digest` | `0` |
| 4 | `resolution.requirement.work_request_dco` | `information_schema.columns` where `data_type = 'jsonb'` | `1` |
| 5 | `resolution.requirement_candidate_id_fkey` | `pg_constraint` by name | `0` (see note) |
| 6 | `resolution.requirement_delete_blockers(uuid,uuid[])` | `to_regprocedure(...) IS NOT NULL` | `t` |
| 7 | `resolution.requirement_delete_guard()` | `pg_proc` in schema `resolution` | `1` |
| 8 | `trg_requirement_delete_guard` | `pg_trigger` on `resolution.requirement`, `NOT tgisinternal` | `1` |
| 9 | `resolution.delete_requirements(uuid[])` | `pg_proc` in schema `resolution` | `1` |
| 10 | `nebula.requirements` projection | `pg_class.relkind`; `information_schema.columns` count | `v`; `22` |
| 11 | `nebula.requirements_compat_write()` + `trg_requirements_compat_write` | `pg_proc`; `pg_trigger` (INSTEAD OF) | `1`; `1` |
| 12 | ported rows present | `resolution.requirement r JOIN resolution.requirement_port_ledger l ON l.target_id = r.id` | `27` |
| 13 | column provenance comments | `col_description` for `requirement.candidate_id` and `requirement.work_request_dco` | both non-null and name P4a |

**Item 5 note.** V222 drops `requirement_candidate_id_fkey` only when
`resolution.candidate` is empty and legacy links are cross-domain
(`V222-PREFLIGHT-016`); if the candidate store is populated first, the port
refuses. `0` is therefore expected — record `1` with the reason if the apply
legitimately took the other branch.

Do not treat a restoring backup as verification: run the queries after the sync
completes. Record the date, the operator decision (replicate / decline), the
backup or sync run id, the per-item result, and the ledger count.

---

## Appendix A — disposable rehearsal fixture (not part of the apply)

The §10 rehearsal seeds a scratch database built from
`sql/ci-bootstrap/nexus-ci-bootstrap.sql` with three requirements covering the
three legacy shapes that matter (system-level `ToDo`, child `Backlog` with a
candidate link and a parent, feature-level `Done` with a compiled
`work_request_dco`), one `resolution.canonical_asset` / `semantics.canonical_asset`
pair, and three blueprint generations (two live, one closed). Run it only
against a scratch database.

```sql
INSERT INTO semantics.canonical_asset (id, canonical_asset_id, asset_kind)
VALUES ('aaaaaaaa-0000-4000-8000-000000000001','asset:nexus:requirement:0001','requirement')
ON CONFLICT (id) DO NOTHING;

INSERT INTO resolution.canonical_asset (id, canonical_asset_id, asset_kind)
VALUES ('aaaaaaaa-0000-4000-8000-000000000001','asset:nexus:requirement:0001','requirement')
ON CONFLICT (id) DO NOTHING;

INSERT INTO nebula.requirements_history
  (id, system_id, subsystem_id, feature_id, title, description, status, priority,
   req_type, start_date, completion_date, created_at, recorded_on_dt, valid_from,
   valid_until, candidate_id, conduit_plan_id, acceptance_criteria, asset_id,
   parent_id, work_request_dco)
VALUES
  ('11111111-0000-4000-8000-000000000001','99999999-0000-4000-8000-000000000001',
   '88888888-0000-4000-8000-000000000001', NULL,
   'system-level ported requirement','first','ToDo','High','Story','2026-09-01','2026-09-09',
   '2026-09-09 10:00:00+00','2026-09-09 10:00:00+00','2026-09-01 00:00:00+00',
   '9999-12-31 23:59:59+00', NULL,'8200001',
   '["emits on ingest","no duplicate on replay"]'::jsonb,
   'aaaaaaaa-0000-4000-8000-000000000001', NULL, NULL),
  ('11111111-0000-4000-8000-000000000002','99999999-0000-4000-8000-000000000001',
   NULL, NULL,
   'child requirement with candidate link','second','Backlog','Medium',NULL,NULL,NULL,
   '2026-09-08 12:00:00+00','2026-09-08 12:00:00+00','2026-09-08 12:00:00+00',
   '9999-12-31 23:59:59+00', 'bbbbbbbb-0000-4000-8000-000000000002', NULL,
   '[]'::jsonb, NULL, '11111111-0000-4000-8000-000000000001', NULL),
  ('11111111-0000-4000-8000-000000000003','99999999-0000-4000-8000-000000000001',
   '88888888-0000-4000-8000-000000000001','77777777-0000-4000-8000-000000000003',
   'feature-level requirement with compiled DCO','third','Done','High','Bug','2026-09-02','2026-09-10',
   '2026-09-10 08:00:00+00','2026-09-10 08:00:00+00','2026-09-02 00:00:00+00',
   '9999-12-31 23:59:59+00', NULL, NULL, '[]'::jsonb, NULL, NULL,
   '{"ops":[{"op":"write"}]}'::jsonb);

INSERT INTO nebula.blueprints_history
  (plan_number, title, payload, recorded_on_dt, recorded_until_dt, valid_from, valid_until)
VALUES
  ('8300001','live blueprint on requirement 1',
   '{"requirement_ref":"11111111-0000-4000-8000-000000000001"}'::jsonb,
   now() - interval '1 day','infinity', now() - interval '1 day','infinity'),
  ('8300002','live blueprint on requirement 3',
   '{"requirement_ref":"11111111-0000-4000-8000-000000000003"}'::jsonb,
   now() - interval '1 day','infinity', now() - interval '1 day','infinity'),
  ('8300003','closed blueprint on requirement 2',
   '{"requirement_ref":"11111111-0000-4000-8000-000000000002"}'::jsonb,
   now() - interval '2 day', now() - interval '1 day',
   now() - interval '2 day', now() - interval '1 day');
```

---

## Appendix B — plans-endpoint list test: the three run shapes (throwaway DBs only)

`typescript/nebula-srv/tests/plan-requirement-lists.test.ts` pins that
`POST /api/plans` carries and validates `requirement_ids` /
`specification_ids`. It refuses to run without `PG_DB_NAME` naming the
throwaway database the server is booted against, and refuses `nexus`. All
three shapes below use a scratch database built from
`sql/ci-bootstrap/nexus-ci-bootstrap.sql` plus `INSERT INTO
nebula.schema_version (version, description) VALUES (72, ...)`; none touches
a live or shared database.

| Shape | Boot state | Command tail | What it proves |
|---|---|---|---|
| 1. pre-port | V222 not applied | `PG_DB_NAME=<scratch> NEBULA_TEST_BASE=http://127.0.0.1:<port> npx tsx tests/plan-requirement-lists.test.ts` | Lists round-trip through both GETs; every rejection is a field-naming 400 and writes no row (count + title/goal probe); singular-only blueprints keep their old shape |
| 2. ported before boot | V220 + V222 applied, then server booted | apply both SQL files to the scratch DB first, then the same command | The endpoint validates and stores against the compat projection: fixtures seed through `nebula.requirements` in whichever shape it currently is, so no test change is needed |
| 3. ported mid-run | Boots pre-port, applies V220 + V222 in-test | same command with `P4A_APPLY_V222=1` | A plan created before the port still re-reads identically after it; `nebula.requirements` and `resolution.requirement` resolve the same ids; `requirement_port_ledger` shows 0 re-keyed rows; a post-port create round-trips too |

Notes: shape 3 is opt-in (`P4A_APPLY_V222=1`) so CI's shared throwaway DB is
never ported for later test files. Pre-port the fixture mirrors each legacy
row column-for-column into `resolution.requirement` (the spec FK needs the
canonical row first) so the port's identity digests agree — the same state
the port leaves behind, reached from the other side. The `rowsCarrying`
probe scans `title` and `payload::text`: the payload-only variant was proven
vacuous against a deliberate write (a title-only row is invisible in
`payload`), so both columns are required.
