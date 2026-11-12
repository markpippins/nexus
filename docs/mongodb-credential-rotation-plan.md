# MongoDB root credential rotation — plan (NOT EXECUTED)

Status: **plan only.** Nothing in this document has been run against the live server.
Execution requires operator approval (`7b0d775d` §3.5 / Phase 4 step 3) because it changes a
credential shared by every Mongo consumer.

## Why

`mongoUser` / `somePassword` is placeholder-grade and has been live since the container was first
initialized. The server has been on `0.0.0.0:27017` throughout, so the value should be treated as
known. Rotation is the remaining security item from the architect's integration pass; bind
narrowing is its sibling and is now supported by the start script (`MONGO_BIND_ADDR` accepts a
list, see below).

## The mechanism constraint that decides the whole plan

`MONGO_INITDB_ROOT_USERNAME` / `MONGO_INITDB_ROOT_PASSWORD` apply **only at first initialization of
an empty data directory**. They do nothing on an existing volume.

So rotation is two operations that must both happen:

1. **In the database:** `db.changeUserPassword(...)` on `admin`.
2. **In configuration:** every consumer's copy of the password/URI.

A fresh volume would "rotate" by re-initialization, but that drops the data — not acceptable.

Also note there is **no dual-password window**: a MongoDB user has one password. Existing open
connections stay authenticated, but every *new* connection after the change needs the new value.
That is the outage window this plan is designed to keep short.

## Consumers that carry the credential today

| # | Consumer | Where the value lives | Rotation action |
|---|---|---|---|
| 1 | `broker-gateway` (Spring, host) | `nexus/.env` → `SPRING_DATA_MONGODB_URI` via `EnvironmentFile` | update `.env`, restart unit |
| 2 | `moleculer-search` | `nexus/.env` `MONGO_URL` else `SPRING_DATA_MONGODB_URI` (`EnvironmentFile`) | update `.env`, restart unit |
| 3 | `moleculer-nexus-broker` (keychain snapshot) | `MONGO_URL` in its own env | update, restart |
| 4 | `bin/mongo-sh` (agents) | `MONGO_URI` → `SPRING_DATA_MONGODB_URI` | none beyond `.env` |
| 5 | `bin/mongo-backup.sh` (nightly) | `MONGO_URI` → `SPRING_DATA_MONGODB_URI` | none beyond `.env` |
| 6 | `bin/mongodb-health-monitor.sh` | `MONGO_ROOT_USER` / `MONGO_ROOT_PASS` | none beyond `.env` |
| 7 | `bin/start-mongodb-docker.sh` | same two keys (only used at first init) | none beyond `.env` |
| 8 | `bin/docklang_store.py` | `MONGO_URI` env or `mongoPass` | inherits env; no file change |
| 9 | ansible `deploy-candidate` / `deploy-tier` | controller-side untracked env, key `SPRING_DATA_MONGODB_URI` | update controller env |
| 10 | `docker/vanadium/docker-compose.yml` | `${SPRING_DATA_MONGODB_URI:-…}` | update the deployed `.env` on that host if it sets one |
| 11 | candidate stack (`docker/candidate-stack/*`) | `MONGO_URL` in a gitignored `.env` next to the compose file | update that file if present |

Known stale/hardcoded copies (not live consumers, but they will mislead the next person):
`scripts/bash/start-mongodb.sh`, `python/fs/fs-crawler/start-mongodb.sh`,
`python/fs/fs-crawler/docker-compose-core.yml` (`changeme`), two `service-broker` READMEs.

## Proposed sequence (single operator-approved window)

1. **Announce + freeze** the window; record start time in the queue thread.
2. **Take a verified backup** and confirm the gates pass (`bin/mongo-backup.sh` already does
   `gzip -t`, size floor, `mongorestore --dryRun`, and a coverage check). Do not start without a
   green run.
3. **Pre-stage** the edited `.env` (new password in `SPRING_DATA_MONGODB_URI`, `MONGO_URI`,
   `MONGO_ROOT_PASS`) in a temp file — do not swap it in yet, and never print it.
4. **Change the password in the DB** with `mongosh` over the *admin* database using the current
   credential:
   `db.getSiblingDB('admin').changeUserPassword('mongoUser', <new>)`.
5. **Swap the `.env` immediately** (atomic `mv`), then `systemctl --user daemon-reload` if needed.
6. **Restart consumers** that hold long-lived connections: `broker-gateway`, `moleculer-search`,
   `moleculer-nexus-broker`.
7. **Verify each consumer** (below). Only then close the window.
8. **Remove the fallbacks**: delete `somePassword` defaults from `bin/start-mongodb-docker.sh` and
   `bin/mongodb-health-monitor.sh` in the same change, so a missing key fails loudly instead of
   silently authenticating with the retired password.
9. **Record** the rotation in the queue thread and the change log — never the value.

## Verification (must all pass before closing)

```bash
# new credential works, old one does not
bin/mongo-sh --quiet --eval 'db.runCommand({ping:1}).ok'                    # 1
MONGO_ROOT_PASS=<old> bin/mongo-sh --quiet --eval 'db.runCommand({ping:1}).ok'  # auth failure

# consumers
curl -s localhost:8081/actuator/health | jq '.components.mongo.status'      # UP
systemctl --user is-active moleculer-search moleculer-nexus-broker          # active
bin/mongodb-health-monitor.sh                                               # "MongoDB is healthy"

# backup path still authenticated (dry run is enough to prove the URI resolves)
bin/mongo-backup.sh --dry-run
```

## Rollback

Change the password back to the previous value with `changeUserPassword` and restore the staged
`.env`. This requires the old password to be held securely for the duration of the window — there is
no second valid password to fall back on. Rollback is only viable while the window is open; after
the old value is discarded, the only recovery is the backup plus a fresh initialization.

## Risks, stated plainly

- **Short auth gap** between step 4 and step 5 (new connections fail with old credentials). Keep the
  staged file ready so the gap is seconds.
- **A consumer missed** shows up as `Command find requires authentication` or `Authentication
  failed`, not as a silent fallback — that is the intended failure mode.
- **Backup timer at 04:05** must not fall inside the window.
- **The password is shared** by services on this host; rotating it from one place without the others
  is worse than not rotating.

## Related work already landed

- `7b0d775d` items 3 and 5: honest readiness probe (PR #966) and consumer inventory.
- `d6db2b90` / PR #968: `MONGO_URI` is the agent-discoverable key; `bin/mongo-sh` and
  `bin/mongo-backup.sh` resolve it before the Spring key, so rotation has one place to update.
- Bind narrowing support: `MONGO_BIND_ADDR="127.0.0.1 172.17.0.1"` publishes on both loopback and
  the docker bridge, which is the only pair that keeps host and container consumers working while
  removing LAN exposure. Verified on a throwaway instance on port 27018: loopback `1`, bridge `1`,
  LAN `ECONNREFUSED`.
