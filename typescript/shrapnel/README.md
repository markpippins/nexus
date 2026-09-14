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
