# Runbook — enable WAL archiving and prove PITR (PostgreSQL 17, titanium)

**Scope:** turn on `archive_mode`, give it a durable archive target, anchor it with a base
backup, and prove recovery with a timed drill on a throwaway cluster.
**Window:** Sunday 2026-11-22, 04:30–06:00 EST (drill fallback Monday 2026-11-23, 04:30–05:15).
**Why then:** user timers run at 01:40, 02:47, 03:13, 03:30–03:41 and 07:55; 03:45–07:00 is clear.
**Background / sizing:** `pg-wal-pitr-restoration-scope.md` (workspace root).

> **Status when this runbook was written:** nothing below has been executed. `archive_mode=off`,
> `archive_command=(disabled)`, `pg_stat_archiver` never exercised, `pg-wal-guard.timer` disabled.
> Deadline (2026-10-17 decision, 30-day revisit) fell on **2026-11-17**.

---

## 0. Ground truth (re-measure before starting — do not trust this table blindly)

| Item | Value |
|---|---|
| Server | PostgreSQL **17.11**, container **`pgvector_db`** (`pgvector/pgvector:pg17`) |
| Data dir (container) | `/var/lib/postgresql/data` → host `pgsql/pgdata` (mode `700`, postgres uid) |
| Config | in-container `postgresql.conf`; use `ALTER SYSTEM` (writes `postgresql.auto.conf`), never a host edit |
| Superuser | `pguser` / `pgpass` (`rolsuper`, `rolreplication`) |
| `archive_timeout` | 300 s (already set — guarantees ≤5 min WAL lag once archiving is on) |
| WAL rate | ≈ **3.2 GB/day** (24 GB since 2026-11-14) |
| Disk | titanium 83 GB free · vanadium 160 GB free |
| Guard | `bin/check_pg_wal.py`, `pg-wal-guard.timer` — **must be enabled before step 3** |
| Existing backups | nightly logical dumps to vanadium 03:30 (not a PITR base) |

```bash
export PGHOST=127.0.0.1 PGUSER=pguser PGPASSWORD=pgpass
psql -d nexus -Atc "select name||'='||setting from pg_settings where name like 'archive%' or name='wal_level' order by 1"
psql -d nexus -Atc "select archived_count, failed_count, last_archived_wal from pg_stat_archiver"
df -h / | tail -1
```

---

## 1. Enable the guard FIRST (no downtime, ~10 min)

Archiving without a guard is how incident `e7a15424` happened (156 GB pinned WAL, disk to 17 GB).

```bash
cd /home/codex/dev/nexus
cp bin/pg-wal-guard.service bin/pg-wal-guard.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now pg-wal-guard.timer
systemctl --user start pg-wal-guard.service          # one foreground run, read the output
journalctl --user -u pg-wal-guard.service -n 20 --no-pager
```

Gate: the run must complete green **before** step 3. If it reports an existing problem (disk, `.ready`
backlog), stop and fix that first. Baseline thresholds it reads: `PG_WAL_SIZE_WARN_GIB`,
`PG_WAL_SIZE_CRIT_GIB`, `PG_WAL_READY_WARN/CRIT`, `PG_WAL_DISK_FREE_WARN/CRIT_GIB`.

## 2. Create the spool as a bind mount (~10 min, 10–20 s container recreate)

The spool must be a **bind mount**, not a path inside the container's writable layer — otherwise it
disappears if the container is ever recreated, taking the archive with it.

```bash
mkdir -p /home/codex/dev/pgsql/pg_archive
chmod 700 /home/codex/dev/pgsql/pg_archive        # postgres uid owns it inside the container
cd /home/codex/dev/pgsql
# add under services.db.volumes, next to ./pglogs:
#   - ./pg_archive:/var/lib/postgresql/data/pg_archive
docker compose up -d                              # recreate; ~10–20 s of downtime starts here
docker exec pgvector_db ls -ld /var/lib/postgresql/data/pg_archive
```

If the recreate is undesirable during the window, `docker restart pgvector_db` after adding the mount is
**not** enough — a volume change requires recreate. Accept the 20 s.

## 3. Turn archiving on (~10 min, 10–20 s restart)

`archive_mode` is a **postmaster-level** parameter: it needs a restart, not a reload. `archive_command`
is reloadable, but set both together.

```bash
export PGHOST=127.0.0.1 PGUSER=pguser PGPASSWORD=pgpass
psql -d nexus -v ON_ERROR_STOP=1 <<'SQL'
ALTER SYSTEM SET archive_mode = 'on';
ALTER SYSTEM SET archive_command =
  'test ! -f /var/lib/postgresql/data/pg_archive/%f && cp %p /var/lib/postgresql/data/pg_archive/%f';
SQL

docker restart pgvector_db
sleep 5
psql -d nexus -Atc "select name||'='||setting from pg_settings where name in ('archive_mode','archive_command')"
```

`test ! -f … && cp` is deliberate: it is idempotent (a retried segment succeeds), it never overwrites an
archived segment, and it cannot half-write (same-filesystem `cp`, then rename semantics of the archiver's
own retry). Do **not** use `mv`, `rsync --remove-source-files`, or a network command here.

## 4. Verify archiving progresses (~10 min)

```bash
psql -d nexus -Atc "select pg_switch_wal()"
sleep 3
ls -l /home/codex/dev/pgsql/pg_archive | tail -3
psql -d nexus -Atc "select archived_count, failed_count, last_archived_wal, last_archived_time from pg_stat_archiver"
```

Acceptance: `archived_count ≥ 1`, `failed_count = 0`, the newest file in the spool matches
`last_archived_wal`, and its size is a full 16 MB segment (or smaller if the switch was forced).

Then push it off-machine and check the pipeline end to end:

```bash
cd /home/codex/dev/nexus
bash bin/pg-wal-archive-push.sh --dry-run     # inspect the plan
bash bin/pg-wal-archive-push.sh               # push + verify + prune
ssh vanadium 'ls -l /srv/pg_archive/nexus | tail -3'
```

## 5. Install the push timer (~5 min)

```bash
cp bin/pg-wal-archive-push.service bin/pg-wal-archive-push.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now pg-wal-archive-push.timer
systemctl --user list-timers | grep pg-wal
```

## 6. Base backup anchor (~15 min, online)

`pg_basebackup` is the point from which WAL replay starts; without it archived WAL is unusable.

```bash
mkdir -p /home/codex/dev/pgsql/pg_base
ts=$(date +%Y%m%d_%H%M%S)
PGPASSWORD=pgpass pg_basebackup -h 127.0.0.1 -p 5432 -U pguser \
  -D "/home/codex/dev/pgsql/pg_base/$ts" -Ft -z -Xs -P -c fast
# then off-machine, next to the WAL archive:
rsync -a "/home/codex/dev/pgsql/pg_base/$ts" "vanadium:/srv/pg_archive/nexus-base/"
ls -l "/home/codex/dev/pgsql/pg_base/$ts"
```

Record the timestamp — the drill needs it.

## 7. PITR drill — the only step that proves recovery (~40 min, no downtime)

A backup that has never been restored is a hypothesis. Run the drill in a **separate container** on
**port 5433** so the live cluster is never touched.

```bash
BASE=/home/codex/dev/pgsql/pg_base/<timestamp-from-step-6>
DRILL=/home/codex/dev/pgsql/pitr-drill
rm -rf "$DRILL"; mkdir -p "$DRILL"

# 7.1 unpack the base backup (tar stream)
tar -xzf "$BASE"/base.tar.gz -C "$DRILL"

# 7.2 point the restore at the local spool and the drill port
cat >> "$DRILL/postgresql.auto.conf" <<EOF
restore_command = 'cp /var/lib/postgresql/data/pg_archive/%f %p'
recovery_target_time = '$(date -u -d '-10 min' '+%Y-%m-%d %H:%M:%S+00')'
recovery_target_action = 'promote'
port = 5432
EOF
touch "$DRILL/recovery.signal"

# 7.3 start the drill cluster (host port 5433 → container 5432)
docker run -d --name pg_pitr_drill \
  -e POSTGRES_PASSWORD=drill -p 5433:5432 \
  -v "$DRILL":/var/lib/postgresql/data \
  -v /home/codex/dev/pgsql/pg_archive:/var/lib/postgresql/data/pg_archive \
  pgvector/pgvector:pg17
docker logs -f pg_pitr_drill        # watch "database system is ready to accept connections"
```

```bash
# 7.4 acceptance: recovery stopped at the target and the data is coherent
PGPASSWORD=drill psql -h 127.0.0.1 -p 5433 -U postgres -d nexus -Atc \
  "select pg_is_in_recovery(), now(), (select count(*) from nebula.agent_records)"
PGPASSWORD=drill psql -h 127.0.0.1 -p 5433 -U postgres -d nexus -Atc \
  "select pg_last_wal_replay_lsn(), pg_last_xact_replay_timestamp()"
```

Acceptance: `pg_is_in_recovery() = f` (promoted), replay timestamp ≈ target − (a minute or two), row
counts plausible against the source. Then tear down:

```bash
docker rm -f pg_pitr_drill
rm -rf "$DRILL"
```

**If the drill fails**, do not widen scope in the window: capture
`docker logs pg_pitr_drill | tail -40`, leave archiving ON (it is harmless and now guarded), and write it
up as a finding. Archiving on + no verified restore is still strictly better than no archiving.

## 8. Rollback (any step, ~1 min)

```bash
psql -d nexus -c "ALTER SYSTEM SET archive_mode = 'off';"
docker restart pgvector_db
```

WAL stops being pinned immediately. The spool and vanadium copies stay in place; they are harmless and
are the evidence for the next attempt. Leave the guard timer running — it costs one SQL round-trip.

## 9. Retention (set in the push script, change deliberately)

| Window | Value | Footprint at 3.2 GB/day |
|---|---|---|
| Local spool | 72 h, floor 64 segments | ≈ 10 GB of 83 GB free |
| Vanadium | 14 days | ≈ 45 GB of 160 GB free |

Do **not** raise remote retention to 30 days (~96 GB): it shares vanadium with the backup GFS tiers.
Pruning is skipped automatically whenever a `.ready` file exists in the spool.

## 10. Post-window checklist

- [ ] `pg-wal-guard.timer` enabled and green
- [ ] `pg-wal-archive-push.timer` enabled, one successful real run
- [ ] `archived_count ≥ 1`, `failed_count = 0`, spool file matches `last_archived_wal`
- [ ] newest segment byte-identical on vanadium (script verifies automatically)
- [ ] base backup stored locally **and** on vanadium, timestamp recorded
- [ ] drill: promoted, replay timestamp ≈ target, row counts sane, container removed
- [ ] change-log entry + agent record with the drill's numbers
- [ ] follow-up decision: weekly base-backup timer (natural next increment)
