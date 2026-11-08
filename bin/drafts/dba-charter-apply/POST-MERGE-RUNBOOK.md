# Post-merge runbook — DBA charter grant apply + persona seed

**Status: DRAFT. NOT AUTHORIZED TO RUN.** Every step below was rehearsed against a
throwaway copy of the live data; **nothing was applied to live `nexus`**.

| | |
|---|---|
| PR | #580 (`dba-charter`), head `76861e29` |
| Grant file | `sql/grants/dba-grant-v0.1.sql` (142 lines, `BEGIN;` L38 / `COMMIT;` L142) |
| Ratification UUID | **`b31b61bb-6501-4660-80e8-9f0ece1ab0ef`** (architect `b31b61bb`; reaffirmed `ff7f596d`, `ab48d1f7`) |
| Seed script | `bin/seed_dba_persona.py` (`ROLE="DBA"`, `SLUG="opencode-persona"`) |
| Target | `postgresql://…@localhost:5432/nexus` |

---

## 0. Preconditions — ALL must hold, or STOP

| # | Precondition | Verified how |
|---|---|---|
| P1 | #580 merged; architect ratification `b31b61bb` stands | `ff7f596d`, `ab48d1f7` |
| P2 | **Tester disposition on hold `cf3fe896` ground 2** | Requested 2026-09-29; unanswered |
| P3 | **Persona-name mismatch fixed** (see §BLOCKER 1) | **FAILS today** |
| P4 | Stale `KNOWN_CASE_DRIFT` entry removed (see §BLOCKER 2) | **FAILS today** |
| P5 | `can_verify_work_requests` stays `false` in the grant | Verified in rehearsal |
| P6 | `DBA`/`dba` spelling settled or split explicitly accepted | Supervisor's lane, **open** |

**P3 and P4 are currently false.** Running this runbook today would apply a
working grant and seed a persona that `dba.md` cannot resolve.

---

## BLOCKER 1 — the persona name in `dba.md` will not resolve (must fix first)

Proven against a database, not inferred:

| | name | rows |
|---|---|---|
| `dba.md:11` requests | `dba/opencode-persona` (lowercase) | **0** |
| `seed_dba_persona.py` creates | `DBA/opencode-persona` | **1** |

Exact-case lookup, so these are different rows. Live bridge today:
`GET :3400/prompts/get?name=dba/opencode-persona` → **HTTP 404**.

The persona is seeded successfully and then **never fetched**. Both the charter
and the seed script must agree on one spelling. Per architect `ff7f596d` §5 the
canonical spelling is Supervisor's lane and is **unruled**; do not pick one
unilaterally.

## BLOCKER 2 — `test_persona_bridge_parity.py` will fail

    AssertionError: stale KNOWN_CASE_DRIFT entries — this drift no longer exists:
      dba.md: dba vs DBA

The allowlist entry is stale *because* the charter changed the request. Removing
it fixes the red — but only **after** BLOCKER 1, or the mismatch hides as a
silent 404. **Do not re-add the entry to silence the failure.**

---

## 1. Preflight (read-only)

```bash
cd /home/codex/dev/nexus
export PGPASSWORD=$(grep -m1 '^GATEWAY_PG_PASSWORD=' .env | cut -d= -f2-)
PSQL="psql -X -h localhost -p 5432 -U pguser -d nexus"
RAT=b31b61bb-6501-4660-80e8-9f0ece1ab0ef
```

**1a — confirm the target.** `psql -c 'SELECT current_database()'` must print
`nexus`. This is the M2 control from `ab48d1f7`: *name and confirm the DSN
before writing.* The 2026-09-25 incident was an unverified wrapper hitting
production.

**1b — precondition shape.** The grant refuses unless exactly one open `dba`
snapshot exists and it is the generic placeholder:

```bash
$PSQL -tAF'|' -c "
SELECT count(*) FILTER (WHERE valid_until='9999-12-31') AS open_rows, count(*) AS total
FROM nebula.roles_history WHERE name='dba';"
```

Expected **exactly `1|2`**. (Observed live 2026-09-29.) `0` or `>1` → **STOP**;
inspect before granting. This is the grant's own `multiple open snapshots`
refusal and it is correct — do not work around it.

**1c — placeholder marker.** The grant matches
`description LIKE 'Ratified role vocabulary%capabilities unassigned%'`. The live
description **wraps onto a second line**, so a truncated read looks like a
mismatch. Check the full string, not `left(description, 45)`.

**1d — backup before any write:**

```bash
pg_dump -Fc -h localhost -p 5432 -U pguser -d nexus \
  -t 'nebula.roles' -t 'nebula.roles_history' -t 'tackle.prompts' \
  -f /tmp/dba-charter-pre-apply-$(date -u +%Y%m%dT%H%M%SZ).dump
```

A **failed** dump refuses the apply.

---

## 2. Rehearse in a throwaway database (MANDATORY)

Never rehearse by wrapping the grant in `ROLLBACK`. **`dba-grant-v0.1.sql` ends
with its own `COMMIT;` (L142)** — an outer `ROLLBACK` is a no-op on the file's
work. That is precisely how `057-backfill-harvest-references.sql` committed
28,428 rows to live on 2026-09-25.

Correct method: `sed` the file's own `BEGIN;`/`COMMIT;` out into a temp copy,
or apply into a separate database:

```bash
sed -e '/^BEGIN;$/d' -e '/^COMMIT;$/d' \
    sql/grants/dba-grant-v0.1.sql > /tmp/grant-no-txn.sql
# then run the stripped copy inside an explicit BEGIN … ROLLBACK
```

Rehearsal DB construction (verified working; `TEMPLATE nexus` **fails** — 80
sessions attached):

```bash
pg_dump … --schema=nebula -t 'nebula.roles' -t 'nebula.roles_history' -f /tmp/roles.sql
{ echo 'CREATE SCHEMA IF NOT EXISTS nebula;'; cat /tmp/roles.sql; } > /tmp/roles_full.sql
psql -d postgres -c 'CREATE DATABASE dba_580_rehearse'
psql -v ON_ERROR_STOP=1 -d dba_580_rehearse -f /tmp/roles_full.sql
```

The dump omits `CREATE SCHEMA` — prepend it. `tackle.prompts` additionally
needs `CREATE EXTENSION pgcrypto` for `digest()`.

**Rehearsal result (2026-09-29), for comparison:**

```
NOTICE:  DBA GRANT: closing generic placeholder snapshot for dba.
NOTICE:  DBA GRANT: chartered capability row inserted for dba.
after: 1 open / 3 total; owns_domains={database_integrity,backup_replication,migration_ledger}
       can_greenlight=f  can_create_questions=t  can_create_agendas=f
       can_resolve_questions=t  can_verify_work_requests=f  max_open_questions=5
re-apply: NOTICE: chartered row already open — no-op.   (1 open / 3 total, unchanged)
```

**The `sed`-strip method was rehearsed end-to-end and is proven safe.** Applying
the stripped copy inside an explicit `BEGIN … ROLLBACK` produced the two
NOTICEs, then restored the state exactly:

```
BEGIN / SET / NOTICE x2 / DO / ROLLBACK
after ROLLBACK: 1 open / 2 total   (unchanged)
```

This is the containment note's prescribed method, and the direct contrast to
2026-09-25, where the same wrap-around committed 28,428 rows to live. The `sed`
removes exactly the 2 outer transaction lines (142 -> 140) and leaves the
PL/pgSQL `BEGIN`/`END` blocks intact.

**Persona seed, rehearsed (2026-09-29):** dry-run defaulted to writing nothing
and planned `insert version 1 (no prior persona)`; `--apply` wrote
`DBA/opencode-persona v1`; re-running reported
`no-op: latest version already carries this exact body`. That is also where
BLOCKER 1 was proven — an exact-case lookup for `dba/opencode-persona` returned
**0 rows** while `DBA/opencode-persona` returned **1**.

---

## 3. Apply the grant to live

```bash
psql -X -v ON_ERROR_STOP=1 -h localhost -p 5432 -U pguser -d nexus \
  -c "SET app.dba_grant_ratified = '$RAT'" \
  -f sql/grants/dba-grant-v0.1.sql
```

- `-v ON_ERROR_STOP=1` is **mandatory**. Verified: *without* it a refused grant
  still **exits 0** (the RAISE is reported, then the session ends 0). With it,
  a refusal exits 3. A runbook that omits it cannot tell success from failure.
- `-c` then `-f` **is a single session** — verified, the `SET` carries through.
  (I initially assumed otherwise and was wrong; it was tested instead of asserted.)
- Expect: `closing generic placeholder snapshot` → `chartered capability row
  inserted` → `COMMIT`.
- **Idempotent.** Re-running yields `chartered row already open — no-op` and
  leaves the row count unchanged. If it does *not* say no-op, something changed
  — investigate, do not re-run.

### Refusal modes (both verified live, both non-destructive)

| Condition | Message | Exit (with ON_ERROR_STOP) |
|---|---|---|
| var unset | `DBA GRANT: NOT RATIFIED — …` | 3 |
| var not a UUID | `must be the ratification decision UUID (got …)` | 3 |
| >1 open snapshot | `multiple open snapshots (open=N)` | 3 |
| open row is neither shape | `open row is neither the known placeholder nor the chartered shape` | 3 |

All refusals leave the database unchanged.

### Post-apply verification

```bash
$PSQL -tAF'|' -c "
SELECT owns_domains::text, can_greenlight, can_create_questions, can_create_agendas,
       can_resolve_questions, can_verify_work_requests, max_open_questions
FROM nebula.roles_history WHERE name='dba' AND valid_until='9999-12-31';"
```

Must match the ratified shape, and `can_verify_work_requests` **must be `f`** —
DBA never attests, including its own work (G1, charter v0.1).

---

## 4. Seed the persona

**Only after BLOCKER 1 is fixed**, or this writes a row nothing reads.

```bash
# dry run first — this is the DEFAULT with no --apply
python3 bin/seed_dba_persona.py --dsn "postgresql://pguser:$PGPASSWORD@localhost:5432/nexus"

# then, on review of the plan:
python3 bin/seed_dba_persona.py --dsn "postgresql://pguser:$PGPASSWORD@localhost:5432/nexus" --apply
```

- Dry-run is the default and writes nothing. It prints the body sha256 and the
  planned action — read them before applying.
- The script resolves the persona body via `Path(__file__).resolve().parents[1]`,
  so it must be at `bin/seed_dba_persona.py` in the repo. **Running a copy from
  `/tmp` fails with `FileNotFoundError: '/config/harnesses/…'`** regardless of
  cwd — it follows the script's own location.
- Idempotent by body hash: a re-run reports
  `no-op: latest version already carries this exact body`.
- Versions are append-only (`max+1`); a changed body inserts a new version.

### Verify the persona actually resolves

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  "http://localhost:3400/prompts/get?name=<role>/opencode-persona"
```

Must be **200**. If it is 404, the name in `dba.md` and the seeded name still
disagree — that is BLOCKER 1, and it fails *silently* at runtime if skipped.

---

## 5. Close out

- Record the apply in the DBA change-log with the ratification UUID.
- Supervisor checklist items **3–4** (post-ratification live write; guard
  refusals / clean session) are the remaining apply-time steps per `ff7f596d` §4.
- `v_role := 'dba'` at `dba-grant-v0.1.sql:42` grants capability to the
  lowercase row. 611 `DBA`-attributed records resolve to no authority row.
  Applying the charter does **not** resolve this.

## Rollback

The grant is **append-only and bitemporal** — it closes the placeholder
(`valid_until`/`recorded_until_dt` set to now) and inserts the chartered row. It
does not mutate or delete history. Reversal means superseding with a new
close-then-insert, not an `UPDATE` or `DELETE`. The §1d dump is the recovery
point. Persona seed is versioned, not destructive.

## Not covered

M1/M2 from `ab48d1f7` (rehearsal refusing self-transactioning files; DSN
confirmation) are **not built** — `bin/rehearse-migration.sh` exists on neither
`origin/main` nor #580's head. This runbook implements M2 procedurally (§1a) and
M1 by the `sed` method in §2. M3 (non-prod DSN default) remains standing debt.
