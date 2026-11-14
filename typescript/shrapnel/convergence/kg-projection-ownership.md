# Track D — Shrapnel ownership in the KG projection (plan 8261645)

**Track:** D — Resolution / Keychains / KG projection
**Package:** D3 (supporting statement; the freeze itself is
`typespec/convergence/kg-projection-contract-freeze.v1.json`)
**Owner:** architect (freeze) · **State:** statement published. **No schema
change, no row written, no value promoted.**

> Shrapnel is a **relational object store** (EAV): Data Definitions
> (`shrapnel.field`, `shrapnel.field_type`) are separated from Concrete Object
> Instances (`shrapnel.object_instance`, `shrapnel.value`, typed
> `shrapnel.value_<type>` extensions, joined by
> `shrapnel.object_attribute_value`). It stores values; it does not adjudicate
> them.

---

## 1. What Shrapnel owns

| Surface | Owner | Notes |
|---|---|---|
| `shrapnel.field_type` (1 Long, 2 String, 3 Double, 4 Boolean, 5 Timestamp, 6 JSONB, 7 UUID) | Shrapnel | type registry |
| `shrapnel.field` (`property_name` is the unique upsert key) | Shrapnel | attribute definitions |
| `shrapnel.object_instance` / `shrapnel.value*` / `shrapnel.object_attribute_value` | Shrapnel | instance + value + junction |
| `shrapnel.*` migrations (`0001`–`0009`) | Shrapnel | append-only; `0009` (stereotype field freeze subtransaction) is carried by plan **8261661** — this decomposition does not touch it |
| Port `3110` (`typespec/v1/shrapnel`) | Shrapnel | contract of record for the REST surface |

## 2. What Shrapnel does not own

- Resolution identity (`resolution.canonical_asset.canonical_key`) — Shrapnel may
  *reference* an identity, never mint one.
- Aegis machine semantics, Semantics asset identity, Keychains checkpoints, PEB
  activation.
- The knowledge-graph projection (`graph_entities` / `graph_edges`, owned by
  `typescript/knowledge-srv`). Shrapnel contributes *entities*, under the freeze's
  provenance rules.

## 3. JSONB storage rule (field_type 6)

1. A JSONB value is a **value**, not a governed key. Shrapnel-side JSONB defaults
   to `payload_only` under
   `schemas/convergence/resolution-jsonb-classification.v1.json`.
2. Entity identity is expressed by the EAV columns — `object_instance.id`,
   `field.property_name`, `object_attribute_value` — **never** by a key buried
   inside a JSONB value. If a key must be joinable, it needs a typed field, not a
   JSONB member.
3. Promoting a Shrapnel value to a higher class (`queryable_identity`, `lineage`,
   `authority`) requires an architect ruling and an explicit classification
   entry, exactly as for `resolution.*` columns.
4. Temporal values (TIMESTAMP, field_type 5) stay typed; a timestamp stored as a
   JSONB string is a defect, because it erases the temporal semantics the freeze
   depends on.

## 4. Relation to the freeze's seed gate

Shrapnel participates in the KG rebuild as the source of `entities` rows. Its
contribution is valid only when:

- each entity row carries `source_authority = "shrapnel"` and a
  `source_record_id` pointing at the `object_instance` id;
- the edge that connects it to a Resolution asset uses `references` (or a
  stronger *evidenced* backbone term) and never `produces` by default;
- the JSONB classification entry for the contributing field exists.

## 5. Evidence slots

| Slot | Value |
|---|---|
| `observed` | static read of `typescript/shrapnel/README.md` (EAV table map), `migrations/0001_init.sql`–`0009_stereotype_field_freeze_subtransaction.sql` (listing only), `typespec/v1/shrapnel` |
| `artifact` | `typescript/shrapnel/convergence/kg-projection-ownership.md` |
| `timestamp` | 2026-11-10 |
| `verification_owner` | architect |
| `unfilled` | a shrapnel-srv bring-up and catalog read (port 3110 is not running on this host per Track A's port scan) |

## 6. Stop condition

Stop after publishing the ownership statement. Any catalog change (new field
type, new typed table, JSONB key promotion) is a separate bounded plan against
`typescript/shrapnel/migrations` and `typespec/v1/shrapnel`.

Related: `typespec/convergence/kg-projection-contract-freeze.v1.json`,
`schemas/convergence/README.md` §4,
`schemas/convergence/work-packages.v1.json` (D3).
