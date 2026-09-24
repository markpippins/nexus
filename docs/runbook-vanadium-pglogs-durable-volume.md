# Runbook — vanadium PostgreSQL durable DDL-attribution logging (pglogs volume)

**Companion to** `runbook-r5-titanium-pgvector-17.10-to-17.11.md` (the titanium
original of this procedure). Vanadium's PG leg is the pg-backup/live-sync
TARGET (R9) — its DDL is mostly incoming restores, but attribution there
matters for the same reason as titanium: anything that reshapes the backup
target's schema must be attributable on the box it happened.

**Topology (2026-09-24):** `pgvector_db` = pgvector/pgvector:pg17 (17.11,
aarch64), compose-managed from `/home/codex/db/pgsql/compose.yml`, port 5432,
data bind `./pgdata`, NEW log bind `./pglogs` → `/var/lib/postgresql/data/pglogs`.
No replication slots/subscriptions on this instance (file-level backups arrive
at `/home/codex/pg-backups/titanium/`, outside the container).

## Applied configuration (ALTER SYSTEM, in `postgresql.auto.conf`)
- `logging_collector=on`, `log_directory=pglogs` (inside the dedicated bind)
- `log_filename=postgresql-%Y-%m-%d.log`, `log_rotation_age=1d`,
  `log_truncate_on_rotation=on`, `log_file_mode=0644` (host-readable)
- `log_statement=ddl`
- `log_line_prefix='%m [%p] db=%d user=%u app=%a client=%h '`

## Procedure (as executed 2026-09-24)
1. **Pre-flight:** compose/inspect capture, auto.conf verbatim (empty here —
   replication overrides live on the titanium side), disk, clients (4 light).
2. **Log dir ownership:** vanadium host runs without elevated access for this
   account, so fix ownership through a throwaway container:
   `docker run --rm -v /home/codex/db/pgsql:/x alpine chown -R 999:999 /x/pglogs`
   (uid 999 = postgres in the image).
3. **Stage via ALTER SYSTEM** (8 settings above), verify auto.conf.
4. **Compose:** add `- ./pglogs:/var/lib/postgresql/data/pglogs`, then
   `docker compose up -d` from `/home/codex/db/pgsql` (one recreate, data bind
   untouched, labels preserved).
5. **Verify:** settings live; host-readable daily log exists; probe DDL
   (`CREATE/COMMENT/DROP`, plus a `PGAPPNAME=ddl-attribution-probe-*` tagged
   probe) greppable host-side with full `db= user= app= client=` attribution;
   `resolution` schema intact (74 tables), backup-arrival path untouched.

## Rollback
- Compose: `cp compose.yml.bak-pre-pglogs-<ts> compose.yml && docker compose up -d`
- Settings: `ALTER SYSTEM RESET <setting>` ×8 (or restore auto.conf) + restart
- No image change was made (already 17.11; recreate used the same `pg17` tag)

## Divergence notes vs titanium
- Ownership fix runs through a throwaway alpine container (the vanadium
  account does not perform elevated host operations); titanium did it
  in-container as root.
- Single compose project here owns only `pgvector_db`; the vd-ci-* stack is a
  separate compose project (`/home/codex/nexus/docker/vanadium-ci`).
- One-shot procedure: titanium's in-data detour (data dir is 0700) was skipped —
  this host went straight to the dedicated-volume topology.
