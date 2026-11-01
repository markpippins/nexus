# Runbook — thallium host maintenance jobs

**Scope.** The scheduled PostgreSQL maintenance for the titanium nexus database
runs on **thallium** (192.168.1.82), not on titanium, and not out of this
repository. This runbook records what those jobs are, where they live, how they
reach titanium, and what state they were in when last inspected. It exists
because a host-level fix leaves no trace in git — the ANALYZE retarget of
2026-11-01 was found only by walking the units on the box.

**Why the jobs live on thallium.** thallium is the DR/observer host: it holds the
`postgres:17` client image, the dump spool, and the tunnel into titanium's
loopback services. It runs the jobs under its **user** systemd manager as
`codex`.

## Inventory

| Unit (`~/.config/systemd/user/`) | Cadence | Script | Log |
|---|---|---|---|
| `nexus-analyze.timer` → `.service` | hourly, `OnCalendar=hourly` | `~/bin/nexus-analyze.sh` | `~/tmp/nexus-maint/logs/analyze.log` |
| `nexus-barium-sync.timer` → `.service` | every 4h, `OnCalendar=*-*-* 00/4:00:00` | `~/bin/nexus-barium-sync.sh` | `~/tmp/nexus-barium-sync/logs/last-run.log` |
| `nexus-nightly-maint.timer` → `.service` | daily 04:30 | `~/bin/nexus-nightly-maint.sh` | `~/tmp/nexus-maint/logs/nightly.log` |
| `nexus-titanium-tunnel.service` | always-on | `ssh -N -L …` | — |

- **analyze** — `ANALYZE;` against titanium's `nexus` database, so the planner
  statistics behind conduit/nebula queries stay fresh. Guarded by
  `flock` on `~/tmp/nexus-maint/analyze.lock` (overlap exits 0).
- **barium-sync** — full snapshot DR sync of titanium's `nexus` into the
  replica: pre-image of the target, `pg_dump -Fc` of titanium, recreate +
  `pg_restore`, then a table-count parity check. Snapshot, not logical
  replication (57 tables lack PKs — DBA inspection `9f44710b` F3).
- **nightly-maint** — verifies WAL archiving is live, and only if it is takes a
  physical base backup (rotate 3). While `archive_mode` is not `on` it alerts
  once per day and exits.
- **titanium-tunnel** — forwards thallium-local 3101 / 3102 / 3107 / 3400 / 3600
  to titanium, which is what lets the alert curls below reach nebula and why a
  broken tunnel shows up as *maintenance alert delivery failing*, not as a
  maintenance failure.

All three jobs alert by POSTing a `dba` agent record to
nebula (titanium:3101) tagged `type:incident`; the nightly alert is
rate-limited to one per day via `~/tmp/nexus-maint/.alerted_<date>`.

## Reaching titanium: use the wired address

**mDNS on thallium resolves `titanium` to the laptop's Wi-Fi address
`192.168.1.71`, and the LAN path to that interface is blackholed from
thallium** (100% ICMP loss; 5432 and 3101 time out). The wired address
`192.168.1.120` serves normally. This is the failure that produced ~2 days of
`FATAL: ANALYZE failed` alerts ending 2026-11-01.

`nexus-analyze.sh` therefore targets the address directly, with an override:

```bash
TITANIUM_PG_HOST="${TITANIUM_PG_HOST:-192.168.1.120}"
```

If a maintenance job reports "not ready" or times out, test both before
touching anything:

```bash
# From thallium
getent hosts titanium                 # → 192.168.1.71 (Wi-Fi; expect blackhole)
docker run --rm postgres:17 pg_isready -h 192.168.1.120 -p 5432   # wired; want "accepting"
curl -s --max-time 3 http://192.168.1.120:3101/health || echo "NOTHING THERE"   # R15
```

## State as inspected 2026-11-01

Snapshot, not a standing claim — re-check before relying on it.

- **analyze: green.** Unattended timer runs succeed on the wired address:
  `16:00:03Z === ANALYZE start ===` → `16:00:38Z === ANALYZE ok ===`. The
  15:00Z run also succeeded (first green hourly run after the retarget).
- **barium-sync: failing every run.** `FATAL: titanium not ready` at 09:00Z and
  13:00Z on 2026-11-01, and `FATAL: barium not ready` at 04:55Z. Two causes:
  the script still uses the bare hostnames (`-h titanium`, `-h barium`), so it
  inherits the Wi-Fi resolution above; and per AGENTS.md R9 the replication
  target has been **vanadium** since 2026-09-05, while this unit is still named
  and aimed at barium. The script needs the same address treatment as
  `nexus-analyze.sh`, and a retarget decision (barium vs vanadium) that belongs
  to the DBA/architect, not to this runbook.
- **nightly-maint: no PITR base backups.** `archive_mode` has read `''`/`off`
  on every 04:30 run from 2026-10-21 through 2026-11-01, so the backup branch is
  never entered. The staged `postgresql.auto.conf` settings
  (`archive_mode=on`, `archive_command` → `/var/lib/postgresql/wal_archive`,
  `archive_timeout=300s`) have been waiting on a titanium postgres restart for
  that entire window; the daily alert fires until it happens.

## Diagnosing by hand

```bash
# Unit + timer state (user manager — NOT system)
ssh thallium 'systemctl --user list-timers --all | grep nexus'
ssh thallium 'systemctl --user status nexus-analyze.timer --no-pager'

# Logs
ssh thallium 'tail -20 ~/tmp/nexus-maint/logs/analyze.log'
ssh thallium 'tail -20 ~/tmp/nexus-maint/logs/nightly.log'
ssh thallium 'tail -20 ~/tmp/nexus-barium-sync/logs/last-run.log'

# Run one job now, in the foreground
ssh thallium 'bash ~/bin/nexus-analyze.sh; echo "exit=$?"'
```

Every job takes a `flock` first: if a manual run exits immediately with
`overlap, exiting`, a scheduled run is in progress — check the log rather than
forcing a second copy.

> **Note the local-time trap.** `journalctl --since "16:00"` is parsed in the
> host's local timezone (UTC-5 here), not UTC. Use `--since "-15min"` or an
> explicit UTC timestamp, or you will read "no entries" for a job that just ran.

## Keeping a fix visible

A change made only on thallium is invisible to everyone who reads the
repository — which is how the ANALYZE retarget was lost until it was traced by
hand. When you change one of these jobs:

1. Edit the script/unit on thallium, verify a real run, and confirm the log line.
2. Record it in a `dba` agent record (what changed, why, evidence) and post the
   change-log entry.
3. Update this runbook, including the dated state snapshot above.

The scripts are the source of truth; this document is the map. If you are
reading this because a maintenance alert fired, check the dated snapshot first —
it may already name the cause.
