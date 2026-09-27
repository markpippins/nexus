# Nebula Segments Service

FastAPI service for the "segment sets" scheme: reusable, possibly
non-contiguous collections of transcript chunks (`nebula.segments_history`
rows) that candidates and requirements point at instead of
copying source text forward.

## Setup

```bash
pip install -r requirements.txt

# 1. Run the migration against your nebula schema
psql "$NEBULA_PG_DSN" -f migrations/001_segment_sets.sql

# 2. Configure env vars (defaults shown)
export NEBULA_PG_DSN="postgresql://pguser:pgpass@localhost:5432/nexus"
export NEBULA_REDIS_URL="redis://localhost:6379/0"
export NEBULA_SEGSET_CACHE_TTL=3600   # safety-net TTL in seconds

# 3. Run
uvicorn app.main:app --reload
```

## Cache strategy

Every mutation (create members, exclude a member, update a set, link/unlink
a domain object) invalidates the relevant Redis key rather than writing
through — a resolved segment set can be referenced by multiple domain
objects at once, so deleting and letting the next `GET` lazily rebuild it
avoids two write paths racing to keep a cached blob in sync. The TTL is
purely a safety net in case an invalidation is ever missed.

Keys:
- `nexus:segset:{segment_set_id}` — resolved segment set JSON
- `nexus:{domain_type}:{domain_id}:segsets` — reserved for a reverse index if
  you want "all evidence for this candidate" without touching Postgres; the
  invalidation calls are already wired into the link/unlink routes, add the
  read-through list cache in `links.py` whenever you want it live.

## Endpoints

### Segment sets

| Method | Path | Notes |
|---|---|---|
| POST | `/segment-sets` | create, optionally with initial members |
| GET | `/segment-sets/{id}` | resolved view, Redis-cached |
| PATCH | `/segment-sets/{id}` | update name/description/status/metadata |
| POST | `/segment-sets/{id}/members` | upsert one or more segments (ordinal, note) |
| DELETE | `/segment-sets/{id}/members/{segment_id}` | soft-exclude (sets `included=false`, never deletes) |

### Domain links

`{domain_type}` is one of `candidates`, `requirements`. (Intent records were
removed as a domain concept — see 002_drop_intent_record_segment_sets.sql.)

| Method | Path | Notes |
|---|---|---|
| POST | `/{domain_type}/{domain_id}/segment-sets` | link a domain object to a segment set (`role`: `primary`/`supporting`) |
| GET | `/{domain_type}/{domain_id}/segment-sets` | list linked segment sets, resolved |
| DELETE | `/{domain_type}/{domain_id}/segment-sets/{segment_set_id}` | soft-unlink (sets `active=false`) |

## Adding another domain type

Add an entry to `_DOMAIN_TABLES` in `app/repository.py` and to the
`DomainType` literal in `app/schemas.py`, plus the matching join table
migration (same shape as `candidate_segment_sets`). Everything else — the
routes, caching, resolution — is generic and needs no changes.

## Example

```bash
# 1. Create a segment set covering chunks 5-8 and 12-18
curl -X POST localhost:8000/segment-sets -H 'content-type: application/json' -d '{
  "name": "auth-flow candidate evidence",
  "members": [
    {"segment_id": "<segments_history.id for chunk 5-8>", "ordinal": 1},
    {"segment_id": "<segments_history.id for chunk 12-18>", "ordinal": 2}
  ]
}'

# 2. Link it to a candidate
curl -X POST localhost:8000/candidates/<candidate_id>/segment-sets \
  -H 'content-type: application/json' \
  -d '{"segment_set_id": "<id from step 1>", "role": "primary"}'

# 3. Later, exclude a digression without touching the candidate row
curl -X DELETE localhost:8000/segment-sets/<id>/members/<segment_id>
```
