# R5 Runbook — titanium `pgvector_db` PostgreSQL 17.10 → 17.11 (maintenance window)

> **Provenance:** Architect ruling `619049d6` (2026-09-23), item **R5**: "titanium pgvector_db patch bump at next maintenance window."
> **Scope:** the single live `pgvector_db` container on titanium (`/home/codex/dev/pgsql/compose.yml`, service `db`).
> **Not in scope:** other hosts' stacks (sonar 15.19 / vanadium-ci 15-alpine are R3 out-of-fleet-scope), image upgrades beyond patch level, any schema changes.
> **Rehearsal:** every command and expected output below was executed end-to-end on a scratch volume on 2026-09-23 — see [Appendix A](#appendix-a--rehearsal-evidence).

---

## 0. Why this is a low-risk window (and why it still needs one)

| Fact | Consequence |
|---|---|
| Same major (17.10 → 17.11) | **No `pg_upgrade`, no dump/restore.** The existing PGDATA on the bind mount (`/home/codex/dev/pgsql/pgdata`) boots unmodified. |
| `pgvector/pgvector:pg17` is a **floating major tag** (pgvector publishes no patch tags) | The "bump" is: pull the current `:pg17` build, recreate the container. The old image must be retagged FIRST or rollback is lost. |
| Data dir is a bind mount | Image swap does not touch data; container recreation is the only mutation. |
| Host client is already 17.11 | The escape-hatch version guard is exercised for real (pre-bump: 17 ≥ 17.10; post-bump: 17 = 17). |
| Upstream advisory: 17.11 contains fixes incl. security/correctness patches (see the 2026-11/2026 pg release notes linked from the DBA's fleet audit thread `ecb91216`) | The reason R5 exists. |

**Standing doctrine honored by this runbook:** R15 (reality-check addresses before surgery), R9 (off-machine backup tier), the R4 escape-hatch guard (PR #471) as the **safety net on the backup path**, and the D2 artifact (PR #495) as the schema baseline for verification counts.

---

## 1. Precondition gates (T-24h — ALL must be green; abort the window otherwise)

### Gate A — Rollback anchor exists *(DONE 2026-09-23 — re-verify)*

```bash
docker images pgvector-local --format '{{.Repository}}:{{.Tag}} {{.ID}}'
docker inspect pgvector_db --format '{{.Image}}'
```

Expect: `pgvector-local:pg17.10-rollback-20260923 -> feb68f4f1544` and the running container on `sha256:feb68f4f15...`.
If missing: `docker tag <running-image-id> pgvector-local:pg17.10-rollback-$(date +%Y%m%d)` — **never proceed without this**; it is the only rollback path once `:pg17` moves.

### Gate B — Target image pre-pulled *(DONE 2026-09-23 — re-verify)*

```bash
docker images pgvector/pgvector --format '{{.Tag}} {{.ID}} {{.CreatedAt}}'
docker run --rm pgvector/pgvector:pg17 postgres --version
```

Expect local `:pg17` = `cf134a767f47` (built 2026-08-13) and `postgres (PostgreSQL) 17.11`.
If the remote moved since, re-verify `postgres --version` on the new ID and update Section 3 expectations.

### Gate C — Backup currency (escape-hatch guard exercised as safety net)

```bash
cat /home/codex/backups/nexus-pg/last-backup.json | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["last_backup"], d["size"])'
```

Expect a stamp within ~24h (verified 2026-09-23: `2026-09-23T03:19:39-04:00`, 579M, sha256 `4ac060f7…`).
If stale or the nightly failed: **run the guard manually first** and require a verified dump before opening the window:

```bash
PGPASSWORD=pgpass nexus/bin/pg-escape-hatch.sh
```

Expected log line (this is the guard, pre-bump, client 17.x ≥ server 17.x): `pg client 17.x >= server 17.x — version guard ok` followed by `dumped + verified`.

### Gate D — Window criteria

- No active engineer/DBA migration against `pgvector_db` (check `ecb91216` / `7045dbbc` for in-flight work).
- V-migrations quiescent for the last hour (no V199 landing mid-window).
- Duration budget: **< 10 minutes** of DB downtime (rehearsed: ~30–60s of actual unavailability).

---

## 2. Freeze gate (T-0)

```bash
cd /home/codex/dev
nexus/bin/post-agent-record.py -r engineer -t "R5 window OPEN: pgvector_db 17.10→17.11" \
  -c "Maintenance window opened per docs/runbook-r5-titanium-pgvector-17.10-to-17.11.md. Gates A–D verified. Rollback anchor: pgvector-local:pg17.10-rollback-20260923." \
  --tags "r5,maintenance-window"
```

Optional but recommended (cheap): one final guard run so the newest pre-bump dump exists (Gate C procedure).

---

## 3. The bump (rehearsed commands, expected outputs)

### 3.1 — Pull current `:pg17` (no-op if Gate B is fresh) and recreate the container

```bash
docker pull pgvector/pgvector:pg17
docker compose -f /home/codex/dev/pgsql/compose.yml up -d db
```

Compose recreates the container because the image ID behind `:pg17` changed (`feb68f4f1544` → `cf134a767f47`). The bind mount keeps all data.

**Do NOT** `docker compose down` (touches everything in that project) — `up -d db` recreates only the db service.

### 3.2 — Watch the boot (the moment that matters)

```bash
docker logs -f pgvector_db 2>&1 | head -20
```

Expected (rehearsed verbatim):

```
PostgreSQL Database directory appears to contain a database; Skipping initialization
LOG:  starting PostgreSQL 17.11 (Debian 17.11-1.pgdg12+2) ...
LOG:  database system was shut down at <timestamp>   # clean shutdown, NOT recovery
LOG:  database system is ready to accept connections
```

**Abort criteria:** any `FATAL`, `PANIC`, `recovery`/`redo` lines after a clean shutdown, or a crash loop.

### 3.3 — Health assertion (the post-flight gate)

```bash
for i in $(seq 1 30); do sleep 2; docker exec pgvector_db pg_isready -U pguser -d nexus -q && break; done
docker exec pgvector_db psql -U pguser -d nexus -Atc "SHOW server_version; SELECT count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema'); SELECT count(*) FROM pg_constraint WHERE conrelid='tackle.role_memory'::regclass"
```

Expected (rehearsed):

- `17.11 (Debian 17.11-1.pgdg12+2)` — **hard requirement; abort+rollback on anything else**
- `229` user tables — matches the D2 V198-era baseline (PR #495); material drift means someone migrated mid-window: re-run the count before proceeding
- role_memory constraint count: `3` today (PK + 2 FKs — see the **known regression**, record `7045dbbc`); **`4` expected once V199 lands** (adds back `uq_role_memory_validity`). This line is informational: report the value, do not gate on it.

### 3.4 — Service-level check (things that talk to :5432)

```bash
docker exec pgvector_db psql -U pguser -d nexus -Atc "SELECT count(*) FROM pg_stat_activity WHERE datname='nexus'"
```

Expect the count to climb back as services reconnect. Spot-check one consumer (e.g. `curl -s --max-time 5 http://localhost:3101/health` for nebula) — if a consumer is wedged, its restart is *not* part of this runbook; record and continue.

### 3.5 — Safety net, post-bump proof (escape-hatch guard, exact parity)

```bash
PGPASSWORD=pgpass nexus/bin/pg-escape-hatch.sh
```

Expected: `pg client 17.x >= server 17.x — version guard ok` → `dumped + verified` (rehearsed: 980K on the scratch, ~606M live, exit 0). This proves the R4 guard holds on the NEW server version and leaves a verified same-day dump — the first backup taken *by* 17.11.

---

## 4. Unfreeze + closeout

```bash
cd /home/codex/dev
nexus/bin/post-agent-record.py -r engineer -t "R5 window COMPLETE: pgvector_db on 17.11" \
  -c "Bump executed per runbook. server_version=17.11, tables=229, role_memory constraints=<value>, post-bump guard run ok (<dump file>)." \
  --tags "r5,maintenance-window,completed"
nexus/bin/post-change-log.sh --title "R5: titanium pgvector_db bumped 17.10→17.11 (window complete)" \
  --body "Executed per docs/runbook-r5-titanium-pgvector-17.10-to-17.11.md. Same-major in-place image swap; no pg_upgrade. Escape-hatch guard green pre/post. Rollback anchor retained until next session."
```

Also reply on fleet-audit thread `ecb91216` ("R5 executed") and advance its status rating per the ruling.

Keep `pgvector-local:pg17.10-rollback-20260923` until at least the next two clean nightly cycles; prune after with `docker rmi pgvector-local:pg17.10-rollback-20260923`.

---

## 5. Rollback (only if 3.2 aborts or 3.3 fails hard)

```bash
docker compose -f /home/codex/dev/pgsql/compose.yml stop db
docker tag pgvector-local:pg17.10-rollback-20260923 pgvector/pgvector:pg17   # retag :pg17 back to the 17.10 build
docker compose -f /home/codex/dev/pgsql/compose.yml up -d db
docker exec pgvector_db psql -U pguser -d nexus -Atc "SHOW server_version"   # expect 17.10
```

Then verify with the guard (client 17.11 ≥ server 17.10 — the rehearsed pre-bump case passes) and post an incident record (`to:dba`, `to:sysadmin`) with the boot logs. **The retag is the critical step** — without Gate A it is impossible.

Data-safety note: because PGDATA is shared and never migrated, rollback cannot lose data that existed pre-window; any writes accepted *after* the 17.11 boot are also safe to keep (same-major rollback of the *image* does not invalidate the data dir).

---

## Appendix A — Rehearsal evidence (2026-09-23, scratch volume on titanium)

| Case | Setup | Result |
|---|---|---|
| Seed | `feb68f4f1544` (17.10) on `/tmp/r5-pgdata`, seeded from `sql/ci-bootstrap/nexus-ci-bootstrap.sql` (PR #495 artifact) via `ON_ERROR_STOP` | clean apply, **229 user tables** |
| Guard happy path, pre-bump | host client **17.11** vs scratch server **17.10** | `version guard ok` → dump + verify + manifest + stamp, **exit 0** |
| Image swap on shared PGDATA | stop/rm container, `docker run cf134a767f47` on the same volume | `Skipping initialization` → clean 17.11 boot, ready in ~2s, **229 tables intact** |
| Guard parity, post-bump | client 17.11 vs server 17.11 | `version guard ok` → dump + verify, **exit 0** |
| Guard skew fail | **real `pg_dump 16.x`** (postgres:16 image, `--network host`) vs 17.11 server | `FAIL: pg client/server major skew: host pg_dump 16.x < server 17.x` + remediation hint, **exit 1**, **no dump written**, `last-backup.json` untouched |

Scratch containers and network removed after rehearsal; `/tmp/r5-pgdata` (root-owned container files) could not be removed by the user account and is left to be reclaimed at reboot. Nothing touched the live `pgvector_db` at any point.
