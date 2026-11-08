# Handoff: MongoDB 4.4.18 → 8.0 (devops)

> **Status: HANDOFF — prepared by architect, nothing changed.** All numbers below
> were measured on titanium on 2026-10-31 against the running `atomic-mongodb`
> container. Commands are written for devops to execute; none were run as part of
> preparing this document beyond read-only inspection.
>
> Companion design: `docs/architecture-reports-keychain-index.md` (§8) gates the
> keychain-index work on this upgrade.

## 1. Why this is now, and not later

| Version | Released | End of life |
|---|---|---|
| 4.4 (running) | Jul 2020 | **Feb 2024** — 2.5 years past EOL |
| 5.0 | Jul 2021 | Oct 2024 |
| 6.0 | Jul 2022 | 31 Jul 2025 |
| 7.0 | Aug 2023 | 31 Aug 2027 |
| **8.0 (target)** | Oct 2024 | **31 Oct 2029** |

Two independent reasons:

1. **No security patches for 2.5 years** on a server that holds 3.4M keychain
   checkpoint records and all work-request history.
2. **The repo's drivers are ahead of the server.** `typescript/tsp-eav-emitter`
   and `moleculer/search` declare `mongodb@^7.6.0`; `moleculer/nexus-broker`
   declares `^6.8.0`. They currently work by tolerating an older server, but the
   version gap is growing in the unsupported direction, and an unannounced
   breaking change in a driver patch release would surface as a runtime failure
   in the broker rather than as a dependency error.

**Target: 8.0.x.** 6.0 is already EOL and 7.0 is inside its last year, so landing
on 6.0 only relocates the problem. Upgrading one major at a time is mandatory, so
this is a four-hop mechanical sequence, not a decision.

## 2. Measured state (2026-10-31)

Server: `mongo:4.4.18`, WiredTiger, FCV **4.4**, auth enabled (root user in
`admin`), uptime 19.6 h, data volume `mongodb_data` → `/data/db` (4.4 GB on
disk), published `0.0.0.0:27017`.

| Database | Collections | Documents | Data size | On disk |
|---|---|---|---|---|
| `keychains` | 12 | 3,402,875 | 9,689 MB | 3,853 MB |
| `nexus` | 6 | 3,667 | 240 MB | 93 MB |
| `legacy-audit` | 2 | 1,944 | 12 MB | 2 MB |

Largest collections:

| Collection | Docs | Data | Index |
|---|---|---|---|
| `keychains.checkpoint_entries` | 3,112,745 | 8,462 MB | 177 MB |
| `keychains.ar_snapshots` | 288 | 1,078 MB | — |
| `keychains.ar_drift_findings` | 274,017 | 102 MB | 3 MB |
| `keychains.entries` | 15,461 | 43 MB | — |
| `nexus.docklang` | 1,287 | 196 MB | — |
| `nexus.conduit_session_logs` | 467 | 34 MB | — |
| `nexus.conduit_work_requests` | 1,890 | 10 MB | — |

`ar_snapshots` averages 3.7 MB per document — array-heavy aggregate documents,
not blobs. The largest single document measured anywhere in the survey is
**4.96 MB** (`keychains.ar_snapshots`); next are `nexus.docklang` 2.06 MB,
`checkpoint_heads` 1.03 MB. Nothing approaches the 16 MB cap, so no document has
to be re-shaped for the upgrade.

Host: 12 vCPU, 30 GB RAM with **5 GB available**, root filesystem 64% used
(153 GB free). CPU has `avx2`, which satisfies MongoDB 5.0+'s AVX requirement —
this upgrade is *possible* on this hardware, not just desirable.

## 3. Four findings that matter more than the version number

These are the reason this is a handoff and not a one-line image bump.

### 3.1 There is no backup of `keychains`

`bin/` contains `mongodb-health-monitor.sh` and `start-mongodb-docker.sh` but no
`mongodump`/`mongorestore` path, and `systemctl list-timers` shows no Mongo
backup timer — only `pg-backup` covers Postgres. **3.4M keychain records exist
with zero recoverable copy.** Any upgrade procedure that starts by stopping this
container is currently a one-way operation. This has to be closed *before* hop 1,
and it stays valuable afterwards.

### 3.2 Nothing supervises the container

`docker inspect atomic-mongodb` → restart policy `no`; `systemctl is-enabled
mongodb` → `not-found`. `bin/start-mongodb-docker.sh` documents "Called by
systemd unit: mongodb.service", but that unit is not installed on titanium. A
host reboot leaves MongoDB down and every Mongo-backed consumer failing at
startup — and the `mongodb-health-monitor` that was designed to compensate is
also not installed as a timer.

### 3.3 The start script can silently roll the data files backward

`bin/start-mongodb-docker.sh` tries `mongo:4.4.18`, then `4.2.18`, then
`4.0.28`, taking the first image that starts. After the volume is upgraded, the
first entry in that list is *older than the data files*, so mongod will refuse to
open the volume, the loop will fall through to 4.2/4.0 for the same reason, and
the script will report "Failed to start MongoDB container with all version
attempts." Worse, if someone "fixes" it by removing the volume, the fallback path
becomes data loss. **The version list must be updated in the same change that
performs the upgrade**, and the post-upgrade default must never point below the
volume's version.

### 3.4 There is no memory for a side-by-side upgrade with default settings

`mongod` runs with an unlimited container and a WiredTiger cache of **14,945 MB**,
currently holding 10,563 MB. Host headroom is 5 GB. A blue/green or rolling
upgrade needs two `mongod` processes on this host; with default cache sizing the
second one cannot start without pushing the host into swap or OOM.

Either cap the cache on both instances (`--wiredTigerCacheSizeGB 4`) for the
duration, or accept an in-place window. **Recommendation: cap the cache
permanently at 4–6 GB anyway** — a 19 GB dataset served from a 15 GB cache on a
host that also runs Postgres, Redis and the TypeScript service fleet is over-
provisioned, and today's 5 GB headroom is a risk independent of the upgrade.

### 3.5 (minor) Credentials are the historical defaults

The container was initialized with `mongoUser` plus the dev default password, and
the start script still carries that default as a fallback. Because
`MONGO_INITDB_*` only applies to a **fresh, empty volume**, changing them later
does nothing — rotation requires `db.changeUserPassword()` (or a new volume with
a restore). The upgrade is the natural moment to rotate, since it already
involves a restore rehearsal.

## 4. Procedure

Four hops, each: *swap binaries → start → set FCV → verify → proceed.* Do not
combine hops. Do not skip one.

### Phase 0 — pre-flight (no downtime)

```bash
# 0.1 Capture a full dump from 4.4 (this is also the backup from §3.1)
docker exec atomic-mongodb mongodump \
  --uri 'mongodb://mongoUser:<pass>@localhost:27017/?authSource=admin' \
  --gzip --archive=/tmp/mongo-pre-upgrade-$(date +%F).gz

# 0.2 Pull it out of the container and verify it is non-trivial + restorable
docker cp atomic-mongodb:/tmp/mongo-pre-upgrade-$(date +%F).gz ./
ls -lh mongo-pre-upgrade-*.gz
docker exec atomic-mongodb mongorestore --gzip --archive=/tmp/... --dryRun

# 0.3 Inventory indexes (5.0 rejects geoHaystack; confirm none exist)
mongosh ... --eval 'db.getSiblingDB("keychains").getCollectionNames()
  .forEach(c => printjson(db.getSiblingDB("keychains")[c].getIndexes()))'

# 0.4 Confirm FCV is exactly 4.4 and no upgrade is mid-flight
db.adminCommand({getParameter:1, featureCompatibilityVersion:1})
```

Freeze writes for the cutover: stop `moleculer/nexus-broker`, `moleculer/search`
and `tsp-eav-emitter`, plus the Spring services with `MongoRepository`
dependencies (`bin/mongodb-health-monitor.sh` lists them).

### Phase 1 — 4.4 → 5.0

```bash
docker stop atomic-mongodb && docker rm atomic-mongodb
# start 5.0.x against the SAME volume, cache capped, restart policy set
docker run -d --name atomic-mongodb --restart unless-stopped \
  -p 172.17.0.1:27017:27017 \
  -e MONGO_INITDB_ROOT_USERNAME=... -e MONGO_INITDB_ROOT_PASSWORD=... \
  -v mongodb_data:/data/db mongo:5.0 \
  --wiredTigerCacheSizeGB 4
# verify it opened the volume and is healthy, then:
mongosh ... --eval 'db.adminCommand({setFeatureCompatibilityVersion:"5.0"})'
```

5.0 still ships the legacy `mongo` shell. **From 6.0 onward it does not** — use
`mongosh` for every command from Phase 2.

### Phase 2 → 3 → 4 — 5.0 → 6.0 → 7.0 → 8.0

Identical shape per hop, with the matching image tag and FCV string. Two notes
specific to later hops:

- **6.0** removes several long-deprecated operators and options; anything still
  using `$where`, `mapReduce`, or the removed index types fails here rather than
  earlier. Grep the consumers before Phase 2 rather than discovering it after.
- **8.0** drops the legacy shell and old wire-protocol support; containers must
  be running a driver that speaks the current protocol. The repo's `^7.6.0`
  driver is fine; any Python service pinned to `pymongo<4` is not — check before
  hop 4, not during it.

### Phase 4 — cutover back to service

1. Update `MONGO_VERSIONS` in `bin/start-mongodb-docker.sh` to the new tag
   (and only the new tag — the fallback ladder is the §3.3 hazard).
2. Install/enable the `mongodb.service` unit so the container survives reboot;
   install the health-monitor timer at the same time.
3. Rotate the root password (`db.changeUserPassword`) and set `MONGO_ROOT_PASS`
   in `nexus/.env`; remove the `somePassword` fallback from the start script.
4. Narrow the bind from `0.0.0.0` to the bridge address (`172.17.0.1`) — the
   script's own comment already documents that containerized consumers reach
   mongod via `host.docker.internal`, not loopback, so this is the correct
   non-breaking narrowing.
5. Restart consumers in dependency order; verify each reconnects.
6. Schedule the recurring `mongodump` and replicate it off-machine (R9 target:
   vanadium), so `keychains` stops being a single copy.

## 5. Verification and acceptance

The upgrade is done when all of the following are true, checked and recorded —
not when the container starts:

| Check | Expected |
|---|---|
| `db.version()` | 8.0.x |
| `getParameter(featureCompatibilityVersion)` | `8.0` |
| Collection counts per database | **identical to §2** (3,402,875 / 3,667 / 1,944) |
| `keychains.checkpoint_entries` count | 3,112,745 (exact; a mismatch is a restore bug) |
| Every consumer reconnected | no `MongoNetworkError` / `MongoServerSelectionError` in service logs for 10 min |
| `nexus.conduit_work_requests` readable by the broker | a known request id round-trips |
| Dump + restore cycle | rehearsed once into a scratch volume before hop 1 |

Counts matter more than "it started": `checkpoint_entries` is an append-only
chain surface, and a silently short restore would present as a healthy server.

## 6. Rollback

Rolling back a *binaries* swap is safe only **before** the FCV is raised, and only
to the previous major. Once FCV is at 5.0 the volume is not readable by 4.4;
the rollback path is `mongorestore` from the Phase 0 dump into a fresh volume.
That makes Phase 0 non-optional and makes each hop a separate, verifiable
checkpoint.

## 7. Open questions for devops / operator

1. **Window**: in-place with capped cache (short downtime, no duplicate host), or
   side-by-side on a second volume (lower risk, needs the cache cap from §3.4 and
   ~5 GB of additional disk for the dump plus scratch)?
2. **Target**: 8.0 confirmed, or 9.0 (released Sep 2026, EOL 2031) if you would
   rather have the current major? 9.0 is one more hop and a newer driver story;
   8.0 is the conservative landing.
3. **Backup destination**: `keychains` is a governance surface — does the
   recurring dump go to vanadium alongside the Postgres backup, or elsewhere?
4. **`nexus.docklang` (196 MB) and `nexus.conduit_session_logs` (34 MB)**: session
   logs look like they want retention. Out of scope here, flagged so they don't
   silently ride along as permanent state.

## 8. What happens after this lands

Nothing in the keychain-index design (§8 of the companion doc) starts until the
server is on a supported release. Once it is:

1. Create `architecture_reports` + the quarantine collection and indexes.
2. Backfill the 2,801 architect reports idempotently.
3. Stand up the indexer on `resolution.keychain_event_outbox` and verify the
   chain end-to-end with a synthetic insert → anchor → verify cycle.
4. Switch `architecture-reports` to render a manifest, not 2,801 files.

The read-set question in that design (§6, §9.1) is still open with the operator
and is unaffected by the upgrade.
