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
