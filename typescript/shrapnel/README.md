# shrapnel-srv

REST API for the **shrapnel Relational Object Store / EAV system**, backed by
PostgreSQL.

The shrapnel system separates **Data Definitions** (metadata) from **Concrete
Object Instances** (values) using an Entity-Attribute-Value model:

| Table | Purpose |
|-------|---------|
| `shrapnel.field_type` | Type registry (1 Long, 2 String, 3 Double, 4 Boolean, 5 Timestamp, 6 JSONB, 7 UUID) |
| `shrapnel.field` | Attribute name + `property_name` (unique upsert key) + `field_type_code` |
| `shrapnel.object_instance` | A single concrete object/entity instance |
| `shrapnel.value` | Base entry for one concrete value, references `value_type_code` |
| `shrapnel.value_<type>` | 1:1 typed extension tables storing the physical value |
| `shrapnel.object_attribute_value` | Junction: `(object_id, field_id) -> value_id` |

## Quick start

```bash
cd ~/dev/nexus/typescript/shrapnel
npm install
npm run migrate                 # apply migrations/0001_init.sql against PG
SHRAPNEL_SRV_PORT=3110 npm run dev
```

Default DSN: `postgresql://pguser:pgpass@localhost:5432/postgres` — overridable
via `SHRAPNEL_PG_DSN`. Port is `SHRAPNEL_SRV_PORT` (default `3110`).

### Rate limiting

All `/api` routes pass through a global rate limiter (`src/lib/rate-limit.js`);
mutating endpoints additionally get a stricter write budget. Both respond
`429` with `{"error":{"message":"too_many_requests"}}` and emit standard
`RateLimit-*` headers (draft-7).

| Limiter | Env overrides | Default |
|---------|---------------|---------|
| Global (`apiLimiter`) | `SHRAPNEL_RATE_WINDOW_MS`, `SHRAPNEL_RATE_LIMIT` | 300 req / 60s / IP |
| Writes (`writeLimiter`) | `SHRAPNEL_WRITE_RATE_WINDOW_MS`, `SHRAPNEL_WRITE_RATE_LIMIT` | 60 req / 60s / IP |

Write-limited endpoints: `POST /api/objects`, `POST /api/encode`,
`POST /api/fields`, `DELETE /api/objects/:id`, `POST /api/objects/:id/classify`,
`POST /api/stereotypes/revisions`.

Counters are per-process memory — correct for the single-instance systemd
deployment this service runs under. If the service is ever put behind a
reverse proxy, set Express `trust proxy` appropriately so client IPs are
resolved from `X-Forwarded-For` (left **off** by default: direct LAN clients).

---

## REST Endpoints

---

### `GET /health`

Service and database health probe.

**Response** `200`

```json
{
  "status": "healthy",
  "counts": {
    "field_type_count": 7,
    "field_count": 42,
    "object_count": 105,
    "value_count": 315,
    "binding_count": 315
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | `string` | Always `"healthy"` when reachable |
| `counts.*` | `integer` | Row counts for each shrapnel table |

---

### `GET /api/field-types`

List the type registry (field type codes).

**Response** `200`

```json
{
  "field_types": [
    { "code": 1, "name": "Long",      "description": "64-bit integer",   "pg_type": "bigint" },
    { "code": 2, "name": "String",    "description": "Variable text",    "pg_type": "text" },
    { "code": 3, "name": "Double",    "description": "Double precision", "pg_type": "double precision" },
    { "code": 4, "name": "Boolean",   "description": "True/false",       "pg_type": "boolean" },
    { "code": 5, "name": "Timestamp", "description": "Date/time",        "pg_type": "timestamptz" },
    { "code": 6, "name": "JSONB",     "description": "JSON object/array","pg_type": "jsonb" },
    { "code": 7, "name": "UUID",      "description": "UUID v4",          "pg_type": "uuid" }
  ]
}
```

---

### `GET /api/field-types/:code`

Fetch a single field type by numeric code (1–7).

**Response** `200`

```json
{
  "field_type": {
    "code": 2,
    "name": "String",
    "description": "Variable text",
    "pg_type": "text"
  }
}
```

**Errors**

| Status | Body |
|--------|------|
| `400` | `{"error":{"message":"code must be an integer 1..7"}}` |
| `404` | `{"error":{"message":"not_found"}}` |

---

### `GET /api/fields`

List field metadata with pagination and optional type filtering.

**Query parameters**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | `integer` | `100` | Max 500 |
| `offset` | `integer` | `0` | Row offset |
| `type_code` | `integer` | — | Filter by field type code (1–7) |

**Response** `200`

```json
{
  "fields": [
    {
      "id": 1,
      "is_calculated": false,
      "field_index": 1,
      "label": "Full Name",
      "name": "Name",
      "property_name": "name",
      "field_type_code": 2,
      "created_at": "2026-07-29T12:00:00.000Z",
      "updated_at": "2026-07-29T12:00:00.000Z"
    }
  ]
}
```

---

### `GET /api/fields/:id`

Fetch a single field by ID.

**Response** `200`

```json
{
  "field": {
    "id": 1,
    "is_calculated": false,
    "field_index": 1,
    "label": "Full Name",
    "name": "Name",
    "property_name": "name",
    "field_type_code": 2,
    "created_at": "2026-07-29T12:00:00.000Z",
    "updated_at": "2026-07-29T12:00:00.000Z"
  }
}
```

**Errors**

| Status | Body |
|--------|------|
| `400` | `{"error":{"message":"id must be integer"}}` |
| `404` | `{"error":{"message":"not_found"}}` |

---

### `POST /api/fields`

Create or upsert a field. If `property_name` already exists, the existing row
is updated (`ON CONFLICT DO UPDATE`).

**Request body**

```json
{
  "is_calculated": false,
  "field_index": 1,
  "label": "Full Name",
  "name": "Name",
  "property_name": "name",
  "type": "String"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `property_name` | `string` | **yes** | Unique key for upsert |
| `type` | `string` | yes† | Type name: `Long`, `String`, `Double`, `Boolean`, `Timestamp`, `JSONB`, `UUID` |
| `field_type_code` | `integer` | yes† | Numeric type code (1–7); alternative to `type` |
| `is_calculated` | `boolean` | no | Default `false` |
| `field_index` | `integer` | no | Sort order; default `0` |
| `label` | `string` | no | Display label |
| `name` | `string` | no | Defaults to `property_name` |

†Either `type` or `field_type_code` must be provided.

**Response** `201`

```json
{
  "field": {
    "id": 1,
    "is_calculated": false,
    "field_index": 1,
    "label": "Full Name",
    "name": "Name",
    "property_name": "name",
    "field_type_code": 2,
    "created_at": "2026-07-29T12:00:00.000Z",
    "updated_at": "2026-07-29T12:00:00.000Z"
  }
}
```

**Errors**

| Status | Body |
|--------|------|
| `400` | `{"error":{"message":"field spec requires property_name"}}` |
| `400` | `{"error":{"message":"field spec 'x' missing type"}}` |
| `400` | `{"error":{"message":"unknown type 'Foo'. Valid: Long, String, Double, Boolean, Timestamp, JSONB, UUID"}}` |

---

### `GET /api/objects`

List object instances with optional decoded values.

**Query parameters**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | `integer` | `100` | Max 500 |
| `offset` | `integer` | `0` | Row offset |
| `decode` | `boolean` | `false` | If `true`, resolve each object's values |

**Response** `200` — without decode

```json
{
  "objects": [
    { "id": 1, "created_at": "2026-07-29T12:00:00.000Z" },
    { "id": 2, "created_at": "2026-07-29T12:01:00.000Z" }
  ]
}
```

**Response** `200` — with `?decode=true`

```json
{
  "objects": [
    {
      "id": 1,
      "created_at": "2026-07-29T12:00:00.000Z",
      "values": { "name": "Alice", "age": 30, "active": true }
    }
  ]
}
```

---

### `GET /api/objects/:id`

Decode a single object into its full JSON representation.

**Response** `200`

```json
{
  "object": {
    "id": 1,
    "created_at": "2026-07-29T12:00:00.000Z",
    "values": {
      "name": "Alice",
      "age": 30,
      "active": true,
      "metadata": { "role": "admin", "tags": ["a", "b"] },
      "registered_at": "2026-01-15T08:30:00.000Z"
    }
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `object.id` | `integer` | Object instance ID |
| `object.created_at` | `string` | ISO-8601 creation timestamp |
| `object.values` | `object` | Property-name → coerced JS value |

**Errors**

| Status | Body |
|--------|------|
| `400` | `{"error":{"message":"id must be integer"}}` |
| `404` | `{"error":{"message":"not_found"}}` |

---

### `POST /api/objects`

Create an object instance and encode values into the store. All writes happen
in a single transaction — partial encodings roll back on failure.

**Request body** — explicit fields

```json
{
  "fields": [
    { "property_name": "name", "label": "Full Name", "name": "Name", "type": "String" },
    { "property_name": "age",  "label": "User Age",  "name": "Age",  "type": "Long" }
  ],
  "values": { "name": "Alice", "age": 30 }
}
```

**Request body** — inferred fields (omit `fields` to auto-detect types from values)

```json
{
  "name": "Alice",
  "age": 30,
  "active": true,
  "score": 95.5,
  "registered_at": "2026-01-15T08:30:00.000Z",
  "metadata": { "role": "admin" }
}
```

When `fields` is omitted, the API infers a field spec per key using JS-type
heuristics:

| JS type | Inferred shrapnel type |
|---------|----------------------|
| `boolean` | Boolean |
| integer `number` | Long |
| float `number` | Double |
| `string` matching ISO-8601 | Timestamp |
| `string` matching UUID pattern | UUID |
| other `string` | String |
| `object` / array | JSONB |

**Response** `201`

```json
{
  "object_id": 42,
  "fields": [
    { "is_calculated": false, "field_index": 1, "label": "Full Name", "name": "Name", "property_name": "name", "field_type_code": 2 },
    { "is_calculated": false, "field_index": 2, "label": "User Age",  "name": "Age",  "property_name": "age",  "field_type_code": 1 }
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `object_id` | `integer` | The newly created object's ID |
| `fields` | `array` | Resolved field specs (useful when inferring types) |

**Errors**

| Status | Body |
|--------|------|
| `400` | `{"error":{"message":"body must be a JSON object"}}` |
| `400` | `{"error":{"message":"values must be a JSON object"}}` |
| `400` | `{"error":{"message":"cannot infer type from null/undefined"}}` |

---

### `DELETE /api/objects/:id`

Delete an object instance. Cascades to remove all associated values and
bindings (`value_<type>`, `value`, `object_attribute_value`).

**Response** `200`

```json
{
  "deleted": 42
}
```

**Errors**

| Status | Body |
|--------|------|
| `400` | `{"error":{"message":"id must be integer"}}` |
| `404` | `{"error":{"message":"not_found"}}` |

---

### `GET /api/objects/:id/values`

List raw `(field, value)` bindings for an object. Returns the junction table
rows with field metadata joined in — useful for inspecting the internal
structure without decoding to JSON.

**Response** `200`

```json
{
  "object_id": 1,
  "values": [
    {
      "field_id": 1,
      "property_name": "name",
      "label": "Full Name",
      "name": "Name",
      "field_type_code": 2,
      "value_id": 10,
      "bound_at": "2026-07-29T12:00:00.000Z"
    },
    {
      "field_id": 2,
      "property_name": "age",
      "label": "User Age",
      "name": "Age",
      "field_type_code": 1,
      "value_id": 11,
      "bound_at": "2026-07-29T12:00:00.000Z"
    }
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `object_id` | `integer` | The object queried |
| `values[].field_id` | `integer` | Field metadata ID |
| `values[].property_name` | `string` | Unique field key |
| `values[].field_type_code` | `integer` | Type code (1–7) |
| `values[].value_id` | `integer` | Concrete value ID in `shrapnel.value` |
| `values[].bound_at` | `string` | ISO-8601 timestamp of binding creation |

**Errors**

| Status | Body |
|--------|------|
| `400` | `{"error":{"message":"id must be integer"}}` |
| `404` | `{"error":{"message":"not_found"}}` |

---

### `GET /api/objects/:id/conformance`

StereoType conformance for an object, **evaluated on demand** via the
`shrapnel.object_conformance()` database function (migration 0005). Read-only
and distinct from the stored `stereotype_conformance` evidence fact, which is
the write gate for classification.

**Response** `200`

```json
{
  "object_id": 4105,
  "classified": true,
  "conformant": true,
  "stereotype": "concept_import_state",
  "revision_id": 1,
  "missing": []
}
```

**Errors** — `404` object not found.

---

### `POST /api/objects/:id/classify`

Classify an object into a stereotype revision — a single atomic transaction
over the `shrapnel.object_classify()` database function. Rejected with `409`
unless the object already carries every required member field (conformance is
evaluable data, never an implicit default) and, per the 0004 evidence gate,
carries-or-receives the `stereotype_conformance = 'conformant'` OAV fact.

**Request body**

```json
{ "revision_id": 1, "disposition": "seeded-by-import" }
```

`disposition` is optional; when present it is recorded as the
`stereotype_conformance_disposition` OAV fact for auditability.

**Response** `200`

```json
{
  "object_id": 4105,
  "stereotype": "concept_import_state",
  "revision_id": 1,
  "disposition": "seeded-by-import",
  "classified": true
}
```

**Errors** — `400` bad ids/body, `404` unknown object, `409` missing required
members, unevidenced classification, or unknown revision.

---

### `GET /api/stereotypes`

All stereotype identities with their head revision (highest version).

**Response** `200`

```json
{
  "stereotypes": [
    {
      "stereotype_id": 1,
      "name": "concept_import_state",
      "description": "Import-state contract over resolution-concept ingest metadata…",
      "head_revision_id": 1,
      "version": 1,
      "depth": 0,
      "contract_fingerprint": "sha256:…",
      "created_at": "2026-09-14T12:00:00.000Z"
    }
  ]
}
```

---

### `GET /api/stereotypes/:name`

Head revision detail for one stereotype. `404` when unknown.

---

### `GET /api/stereotypes/:name/chain`

Lineage of the head revision via `shrapnel.stereotype_chain()`: pinned parent
revisions from root to head, each with `name`, `version`, `hop`.

---

### `GET /api/stereotypes/:name/contract`

Compiled **effective contract** of the head revision via
`shrapnel.stereotype_effective_contract()`: parent ∪ child required-field set,
flattened, with per-field origin provenance
(`property_name, required, origin_revision, origin_stereotype, origin_version`).

Ordered `required DESC, property_name`.

---

### `POST /api/stereotypes/revisions`

Create a revision (root or child) through the
`shrapnel.stereotype_create_revision()` constructor — one atomic call: identity
get-or-create, `version = max+1`, server-computed contract fingerprint,
deferred superset/acyclicity/depth verification at COMMIT.

**Request body**

```json
{
  "name": "shape_child",
  "extends_revision": 1,
  "rationale": "adds color to the base shape contract",
  "required_fields": ["shape", "color"],
  "optional_fields": ["label"]
}
```

`extends_revision` omitted → root revision (`rationale` must be omitted too).
`rationale` is **mandatory** for child revisions (architect constraint).
`required_fields` must be a superset of the parent's required set (monotonic
upgrade — downgrades rejected at COMMIT).

**Response** `201`

```json
{
  "revision_id": 27,
  "name": "shape_child",
  "version": 1,
  "depth": 1,
  "contract_fingerprint": "sha256:…"
}
```

**Errors** — `400` validation, `409` fingerprint mismatch, non-superset child,
rationale-less extends, depth > 3, or append-only mutation attempts.

---

### `POST /api/encode`

Generic encode endpoint. Identical behaviour to `POST /api/objects` but also
returns the **decoded** snapshot used to verify the round-trip.

**Request body** — same shape as `POST /api/objects`

```json
{
  "fields": [
    { "property_name": "name", "type": "String" }
  ],
  "values": { "name": "Bob" }
}
```

**Response** `201`

```json
{
  "object_id": 43,
  "fields": [
    { "is_calculated": false, "field_index": 1, "label": null, "name": "name", "property_name": "name", "field_type_code": 2 }
  ],
  "decoded": { "name": "Bob" }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `object_id` | `integer` | Newly created object ID |
| `fields` | `array` | Resolved field specs |
| `decoded` | `object` | Round-trip verified values read back from the store |

**Errors** — same as `POST /api/objects`.

---

## Encoding contract

`POST /api/objects` and `POST /api/encode` accept a JSON body of the form:

```json
{
  "fields": [
    { "property_name": "name",  "label": "Full Name", "name": "Name", "type": "String" },
    { "property_name": "age",   "label": "User Age",  "name": "Age",  "type": "Long" }
  ],
  "values": { "name": "Alice", "age": 30 }
}
```

If `fields` is omitted, field metadata is inferred from the `values` payload
(basic JS type → shrapnel type code).

The API encodes strictly in the order defined by the spec:

1. Upsert every attribute in `shrapnel.field` (by `property_name`).
2. Insert a row in `shrapnel.object_instance`.
3. For each `(field, value)` pair:
   - insert a `shrapnel.value` row (`value_type_code`),
   - insert a row in the matching `shrapnel.value_<type>` extension,
   - link them via `shrapnel.object_attribute_value`.

All three sub-steps happen inside a single transaction so partial encodings
roll back on failure.

## Schema integrity guarantees

Every table in the shrapnel schema has an explicit primary key (the original
shrapnel review surfaced tables like `data_source.id` and `qbe_table.id` that
were `bigint NOT NULL` but lacked `PRIMARY KEY` — the local shrapnel schema
does not have this defect). All relationships are declared via `FOREIGN KEY`
constraints (e.g. `object_attribute_value.value_id -> value.id`,
`field.field_type_code -> field_type.code`,
`object_attribute_value.{object_id, field_id} -> object_instance.id / field.id`).
The junction table carries both a surrogate `id` PK and a `UNIQUE(object_id,
field_id)` composite to prevent duplicate bindings. The `field.property_name`
column has a unique constraint to support `ON CONFLICT (property_name)` upserts.

The polymorphic value↔value_<type> pair deserves special attention.

The 1:1 binding between `value.id` and exactly one `value_<type>.id` extension
row is enforced at TWO layers:

1. **DB layer** — migration `0002_value_extension_type_guard.sql` installs a
   `BEFORE INSERT OR UPDATE` trigger on every `value_<type>` table that
   raises an exception unless the parent `value.value_type_code` matches the
   type the extension represents. This means:
   - you cannot insert a `value_string` row for a parent `value` whose
     `value_type_code = 1` (Long) — the trigger raises;
   - you cannot insert the same `value.id` into TWO different extension
     tables because the second extension's trigger would assert the wrong
     type code;
   - you cannot insert an extension row for an `id` that doesn't exist in
     `value` at all (FK already catches this, the trigger re-states it).
2. **API layer** — `lib/encode.js`'s `encodePayload()` inserts the `value`
   base row AND the matching `value_<type>` row inside a single
   `withTransaction()` call. Partial encodings cannot escape: any failure in
   either row rolls the whole transaction back.

The one gap that is NOT closed purely inside the database schema is **existence**:
nothing structurally prevents a `value` row from having NO extension row at
all (a deferred constraint cannot know which extension table to expect). The
API invariant above makes this impossible in practice for writes going
through the shrapnel-srv; any other writer that talks to the shrapnel schema
directly MUST maintain the same invariant by inserting both rows in the same
transaction.

## Migrations

- `migrations/0001_init.sql` — full schema DDL (idempotent).
- `migrations/0002_value_extension_type_guard.sql` — DB-level guard trigger
  that rejects any `value_<type>` extension row whose parent `value` row's
  declared `value_type_code` does not match the type the extension represents.
- `smoke_example.sql` — the canonical DO-block example from the spec, plus a
  decode query to verify the round-trip. Not part of the migration flow; run
  manually with `psql -f smoke_example.sql` against the same DB.
- `negative_path_check.sql` — proves the type-guard trigger in `0002` fires
  on each failure mode (wrong extension, second extension, no parent).
  Run with `npm run dbcheck`.

> **Defect (pre-existing, not introduced here): `npm run migrate` cannot apply
> `0007_work_request_stereotypes.sql`.** That file uses the psql meta-command
> `\gset` three times to thread one `stereotype_create_revision` id into the
> next `CREATE`. `\gset` is a *client* directive, not SQL, so
> `src/scripts/migrate.js` — which pipes each file to `pool.query()` — dies
> with `syntax error at or near "\"`. The file has never been applied on this
> host: it is absent from `shrapnel._migration_ledger` and
> `shrapnel.stereotype_crud` does not exist. The ledger currently ends at
> `0006_reconcile_legacy_field.sql`.
>
> Two independent causes, either of which alone would block it: the `\gset`
> above, and `0003`'s top-level `SAVEPOINT`/`ROLLBACK TO SAVEPOINT`, which is
> likewise illegal through the driver. Until this is fixed, migrations must be
> applied with `psql -v ON_ERROR_STOP=1 -1 -f <file>` (which is what the
> hermetic sheet tests do). Reported to the architect; **no fix applied here**,
> as the SQL and the runner are both DBA/architect-owned.

## Sheets (phase 1, V166)

Manual sheets are **windows over the existing EAV store**, not a parallel
storage system. `src/routes/sheets.js` exposes the 9 functions and 3 views
defined by `sql/V166__sheet_phase1_manual_sheets.sql`.

> **V166 is a DBA draft marked "NOT APPLIED TO LIVE" and this service's
> `migrations/` chain deliberately does not include it.** Applying it is the
> roundtable's decision, not this PR's. Until then the sheet routes return
> **503** with `error.details.sheets_code = "SHEETS-SCHEMA"` — a client can tell
> "not deployed yet" from "broken". Every sheet request passes a
> `requireSheetSchema` guard, and the availability probe is cached per process.

### Doctrine the endpoints enforce

| | Rule | Enforced by |
|---|---|---|
| D1 | Dropping a sheet/row/column kills the *window*; objects, fields and OAV facts survive. No cascade into user data. | `DELETE /api/sheets/:id` (+ column/row removal) |
| D2 | `set_cell` **is** a direct OAV write — no shadow store, no override layer. | `PUT /api/sheets/:id/cells` |
| D3 | One value row per `(object, field)`; updating a cell rewrites in place, never duplicates. | `PUT /api/sheets/:id/cells` |
| D4 | Sparse by construction: an empty cell is an **absent OAV row**, never a stored NULL. Clearing reference-counts the value row away. | `DELETE /api/sheets/:id/cells/*` |
| D5 | Values go through the same 7-type encode path as `/api/encode`. | `POST /api/sheets/:id/encode` |

### Endpoints

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/sheets` | list with `column_count` / `row_count` |
| `GET` | `/api/sheets/:id` | |
| `DELETE` | `/api/sheets/:id` | D1 — window dies, data survives |
| `GET` | `/api/sheets/:id/columns` | projection, in display order |
| `POST` | `/api/sheets/:id/columns` | `field_id`, optional `column_index` / `display_label` |
| `DELETE` | `/api/sheets/:id/columns/:fieldId` | D1 |
| `GET` | `/api/sheets/:id/rows` | membership, in rank order |
| `POST` | `/api/sheets/:id/rows` | `object_id`, optional `row_index` |
| `DELETE` | `/api/sheets/:id/rows/:objectId` | D1 |
| `POST` | `/api/sheets/:id/rows/:objectId/move` | O(1) fractional rank |
| `GET` | `/api/sheets/:id/cells` | scoped to the sheet window |
| `PUT` | `/api/sheets/:id/rows/:objectId/fields/:fieldId` | `{ type_code, value }` |
| `DELETE` | `/api/sheets/:id/rows/:objectId/fields/:fieldId` | D4 — sparse absence |
| `POST` | `/api/sheets/encode` | D5 — standalone literal, no cell binding |
| `GET` | `/api/sheets/:id/grid` | sparse render; empty cells are `null` |

### Error codes

Domain violations are raised by the DDL as `SHEETS-0nn` and mapped to **409**:

| Code | Meaning |
|---|---|
| `SHEETS-001` | field is not a projected column of this sheet |
| `SHEETS-002` | object is not a row of this sheet |
| `SHEETS-003` | `type_code` disagrees with the field declaration |
| `SHEETS-005` | invalid literal, or a non-canonical boolean |
| `SHEETS-006` | sheet name must be a non-empty string |
| `SHEETS-007` | duplicate sheet name |
| `SHEETS-009` | unknown field |
| `SHEETS-010` | duplicate column projection |
| `SHEETS-011` | unknown object |
| `SHEETS-012` | duplicate row membership |
| `SHEETS-014` | cannot move a non-member |

A **malformed request** (missing/non-string name, no `row_index` on a move,
out-of-registry `type_code`) is a **400** and is rejected before the database
is touched. The two are deliberately distinct: a blank name is a well-formed
request that breaks a domain rule (`SHEETS-006` → 409), a missing name is not a
request this endpoint can act on (400).

### Tests

- `test/sheets.test.js` — pure unit tests for the router helpers.
- `test/sheets.hermetic.test.js` — 38 end-to-end tests that create a throwaway
  database, apply the service chain **plus the real V166** via `psql`, drive
  the HTTP layer, and drop the database. The live `nexus` database is never
  touched. This is the same hermetic pattern
  `python/shrapnel_sheet/tests/test_sheet_phase1.py` uses, and it is the only
  way to exercise this surface while V166 remains unapplied.

  It **skips** (38 skipped, 0 failed) when the admin DSN cannot create a
  database, so `npm test` stays useful on a host without that privilege:

  ```bash
  SHRAPNEL_TEST_ADMIN_DSN=postgresql://pguser:pgpass@localhost:5432/postgres npm test
  ```

  The harness uses `psql` rather than the pg driver for the reasons given under
  **Migrations** above.

