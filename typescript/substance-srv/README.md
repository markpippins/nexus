# substance-srv

TypeScript port of `python/substance` — the **segment sets** scheme: reusable,
possibly non-contiguous collections of transcript chunks
(`nebula.segments_history` rows) that candidates and requirements point at
instead of copying source text forward.

Port 3115. Service-registry id 117. Reads are cached in Redis; writes invalidate
rather than write through.

> **Status:** the port is complete and green (146 tests, `tsc --noEmit` clean),
> but it is **not deployed**. The Python service still owns port 3115. See
> [Cutover](#cutover) below — swapping the service is a separate, deliberate step.

## Setup

```bash
npm install
npm run build        # tsc → dist/
npm start            # node dist/index.js
npm test             # vitest run
npm run typecheck    # tsc --noEmit
```

Env vars (defaults shown):

```bash
export NEBULA_PG_DSN="postgresql://pguser:pgpass@localhost:5432/nexus"
export NEBULA_REDIS_URL="redis://localhost:6379/0"
export NEBULA_SEGSET_CACHE_TTL=3600   # safety-net TTL in seconds
export SUBSTANCE_PORT=3115
export REGISTRY_URL="http://localhost:8085"
```

The schema migrations live with the Python service and are **not** duplicated
here — the tables are the same ones:

- `python/substance/001_segment_sets.sql`
- `python/substance/002_drop_intent_record_segment_sets.sql`

## Cache strategy

Unchanged from the Python service, and the reasoning still holds: every mutation
invalidates the relevant Redis key rather than writing through. A resolved
segment set can be referenced by multiple domain objects at once, so deleting
and letting the next `GET` lazily rebuild it avoids two write paths racing to
keep a cached blob in sync. The TTL is purely a safety net in case an
invalidation is ever missed.

Keys:

- `nexus:segset:{segment_set_id}` — resolved segment set JSON
- `nexus:{domain_type}:{domain_id}:segsets` — reserved for a reverse index; the
  invalidation calls are already wired into the link/unlink routes, so a
  read-through list cache can be added in `routes/links.ts` whenever wanted

A Redis outage is survivable by design: the client is `lazyConnect` with an
`error` listener, a corrupt or foreign cache blob is treated as a miss,
post-commit invalidations are best-effort, and Postgres is the source of truth.

## Endpoints

### Segment sets

| Method | Path | Notes |
|---|---|---|
| POST | `/segment-sets` | create, optionally with initial members |
| GET | `/segment-sets` | list current-valid sets (`limit`, `offset`) |
| GET | `/segment-sets/{id}` | resolved view, Redis-cached |
| PATCH | `/segment-sets/{id}` | update name/description/status/metadata |
| POST | `/segment-sets/{id}/members` | upsert one or more segments (ordinal, note) |
| DELETE | `/segment-sets/{id}/members/{segment_id}` | soft-exclude (`included=false`, never deletes) |
| POST | `/segment-sets/from-segments` | strict-atomic transcript ingest |

### Domain links

`{domain_type}` is one of `candidates`, `requirements`. (Intent records were
removed as a domain concept — see `002_drop_intent_record_segment_sets.sql`.)

| Method | Path | Notes |
|---|---|---|
| POST | `/{domain_type}/{domain_id}/segment-sets` | link a domain object (`role`: `primary`/`supporting`) |
| GET | `/{domain_type}/{domain_id}/segment-sets` | list linked segment sets, resolved |
| DELETE | `/{domain_type}/{domain_id}/segment-sets/{segment_set_id}` | soft-unlink (`active=false`) |

### Other

| Method | Path | Notes |
|---|---|---|
| GET | `/healthz` | liveness |

## Adding another domain type

Add an entry to `DOMAIN_TABLES` in `src/repository.ts` and to the `DOMAIN_TYPES`
literal in `src/schemas.ts`, plus the matching join table migration (same shape
as `candidate_segment_sets`). Everything else — the routes, caching, resolution
— is generic and needs no changes.

## Parity notes

Semantics carried across unchanged, because they are load-bearing:

- `FOREVER = "9999-12-31 00:00:00+00"` must match the NOT NULL DEFAULT on every
  table **and** the WHERE clause of every partial unique index. The ON CONFLICT
  predicates are checked by Postgres against those indexes, so drift is a hard
  query error rather than silent corruption.
- Members are upserted via the partial unique index, not delete-then-insert.
- Exclude and unlink are **soft**: they close the validity window and flip a
  flag. Nothing is ever deleted.
- `POST /segment-sets/from-segments` is strict-atomic — segment set,
  `segments_history` rows, and members in one transaction; failure rolls
  everything back.
- Cache invalidation happens **after commit**, never inside the transaction.

Two deliberate divergences, both improvements:

1. **No pydantic.** The request validators are hand-rolled (no new runtime
   dependency), but they reproduce pydantic's observable contract: 422 with
   FastAPI's `{detail: [{loc, msg, type}]}` envelope, `null` for absent optional
   fields, and — critically for PATCH — the ability to tell "field absent" from
   "field explicitly set to null", which is what `exclude_unset` gave us in
   Python.
2. **The `updateSegmentSet` column allowlist.** Column identifiers are
   interpolated into SQL. The Python version was only safe because pydantic
   constrained the field set; here `SEGMENT_SET_UPDATE_COLUMNS` makes the
   constraint explicit and a bad key throws instead of reaching the statement.

And one behavioural fix, because the Python behaviour was a latent bug:

3. **Post-commit invalidation is best-effort.** Every invalidation call runs
   *after* the transaction has committed, so the write it accompanies is already
   durable. The Python service let a Redis failure there propagate, which turned
   a successful write into a 500 — and a client that retries on a 500 duplicates
   the row. `invalidateSegsetBestEffort` / `invalidateDomainIndexBestEffort`
   log and swallow; the TTL is the safety net for exactly this case. The strict
   forms remain exported for callers that need the deletion to have happened.

### Wire format is unchanged

Responses stay **snake_case**, and 404s stay 404s. That is a live contract, not
an accident: `typescript/nebula-srv/src/substance-proxy.ts` proxies read-only to
`:3115` and normalises via `substanceToCamel`, so a camelCase or status-changing
response would silently break the segment-set evidence reads in nebula-srv and
Assembly (merged in #599). `src/http.test.ts` asserts this.

Timestamps are emitted as ISO-8601 UTC (`...Z`) on both the cached and the
uncached path, so the two never disagree.

## Conformance to #599

`#599` (`c207bdf8`) wired substance's reads into nebula-srv and assembly-srv and
then, in the amend commit, eliminated the intent-record domain. The audit
against that merged state:

**Consumers, and what they call** — all four read paths are served, and none of
the callers reference a path this service lacks:

| Consumer | Calls on :3115 |
|---|---|
| `nebula-srv/src/routes.ts:293` | `GET /segment-sets?limit&offset` |
| `nebula-srv/src/routes.ts:311` | `GET /segment-sets/:id` |
| `nebula-srv/src/routes.ts:322` | `GET /candidates/:id/segment-sets` |
| `nebula-srv/src/routes.ts:332` | `GET /requirements/:id/segment-sets` |
| `assembly-srv/src/routes/segment-sets.js` | the same four |
| `bin/transcript_ingest.py:342` | `POST /segment-sets/from-segments` (write) |
| `bin/transcript_ingest.py:234` | `GET /segment-sets` (write-path lookup) |

The amend removed `GET /api/intent-records/:id/segment-sets` from **both**
consumers, so the `DOMAIN_TYPES` reduction to `candidates | requirements` is
exactly right — nothing calls `/intent-records/...` any more, and
`nebula.intent_record_segment_sets` is dropped by
`002_drop_intent_record_segment_sets.sql`.

**Response shape.** `SegmentSetOut`, `ResolvedSegment` and `DomainLinkOut` are
key-for-key identical to the pydantic models, so `substanceToCamel` produces the
same camelCase names the consumers already read. The list route returns a bare
JSON array, which both list handlers assume (`total: Array.isArray(data) ? …`).
`404` bodies keep FastAPI's `{"detail": "segment set not found"}`, so
nebula-srv's `/substance 404/` → 404 mapping still fires. All of this is pinned
in `src/contract.test.ts`.

**Divergences, all unobservable and none of them regressions:**

| | Python | Here | Why it does not matter |
|---|---|---|---|
| unknown `domain_type` | 422 (pydantic `Literal`) | 404 `unknown domain_type` | No consumer calls a retired domain; 404 keeps assembly-srv's `NotFoundError` mapping sane |
| `limit` | unbounded | clamped to `[0, 1000]` | Both consumers already clamp before calling; Python's behaviour on a negative limit is a Postgres error, i.e. a 500 |
| timestamps | `…T12:00:00+00:00` | `…T12:00:00.000Z` | No consumer reads them, and `new Date()` parses both. The `Z` form also matches nebula-srv's own `toISOString()` house style |
| non-object `metadata` | 500 on response validation | coerced to `{}` | the column is `jsonb`; this only differs on data that should not exist |

**One pre-existing defect found, in `bin/transcript_ingest.py` — not introduced
here.** Line 238 calls `DELETE /segment-sets/{id}`, and that route has never
existed on *either* runtime: `python/substance/routers/segment_sets.py` declares
no `DELETE /{segment_set_id}`, and neither does this port. The call is doubly
swallowed — `_delete_url` catches every exception and returns `False`, and the
caller sits in a bare `try/except: pass` — so re-ingesting a transcript silently
leaks the previous segment set instead of removing it.

The fix belongs on the ingest side, not here: the scheme is soft-delete
throughout (exclude and unlink close a validity window; nothing is ever
removed), and `PATCH /segment-sets/{id}` with `status: "archived"` already
exists and is the correct operation. Adding a hard-delete route to make the dead
call succeed would contradict the design, so `src/contract.test.ts` pins its
absence instead.

### TypeSpec drift — not fixed here

`typespec/v1/substance/python/` is a loose reverse-engineering that has drifted
from the running API: `SegmentSetOut` declares `segmentSetId` + `members: string[]`
instead of `id` / `segments: ResolvedSegment[]`, the link models use camelCase
`segmentSetId`, the request bodies are typed `unknown`, and the list and
`from-segments` routes are missing entirely. `typespec/**` is architect-owned, so
this port implements the **runtime** contract (`python/substance`) and the drift
is filed for the architect rather than papered over here.

## Cutover

Not done by this PR, deliberately:

1. Run `npm run build` on the target host.
2. Stop `substance.service` (the Python unit).
3. Install and start `substance-srv.service`.
4. Verify `GET /healthz`, then a read through
   `GET /api/segment-sets/:id` on nebula-srv (:3101) — that path is the one the
   proxy exercises, so it is the real smoke test.
5. Watch the `[listener]` log lines: a segment expiration should produce
   `invalidated N/M segset(s)`.

`python/substance` is left in place. Removing it is a separate decision once the
TypeScript service has carried production traffic.

### Two things to settle before deploying

1. **System unit vs user unit.** The `substance-srv.service` shipped here is a
   *system* unit (`WantedBy=default.target`), mirroring
   `python/substance/substance.service`. But #601 (`bin/substance-service.sh`)
   states that substance actually runs as a **systemd user** unit
   (`~/.config/systemd/user/substance.service`). If that is the live shape, this
   unit needs a user-unit variant — the `User=`/`Group=` lines are dropped,
   `ProtectHome=read-only` has to be relaxed enough to reach the nvm Node
   install, and `[Install]` becomes `WantedBy=default.target` under
   `systemctl --user`. Whoever cuts over should confirm which shape the host
   actually uses rather than assuming the Python file was authoritative.
2. **The helper is already env-overridable.** #601 reads `SUBSTANCE_UNIT` and
   `SUBSTANCE_HEALTH_URL`, so the same tooling drives both implementations:
   `SUBSTANCE_UNIT=substance-srv.service bin/substance-service.sh restart`. No
   edit to the helper is needed — which is fortunate, since #601 is still open.

## Layout

```
src/
  index.ts             Express app, CORS, /healthz, registry heartbeat, lifecycle
  config.ts            env-driven settings (memoized singleton + pure factory)
  db.ts                pg Pool, query helpers, withTransaction
  cache.ts             Redis client, key helpers, JSON encoding, invalidation
  schemas.ts           types + hand-rolled validators + output shaping
  repository.ts        all SQL, the domain table map, the FOREVER sentinel
  listener.ts          LISTEN segment_expired → invalidate affected sets
  http.ts              error envelopes, async wrapper, path/query coercion
  routes/
    segment-sets.ts    /segment-sets (prefixed)
    links.ts           /{domain_type}/{domain_id}/segment-sets (root-mounted)
```

Tests are colocated as `src/*.test.ts` and run under vitest.
