# tsp-eav-emitter

TypeSpec → shrapnel EAV catalog compiler (prototype, validated on a scrap
database 2026-09-26). Based on the operator-approved DeepSeek approach
("TypeSpec EAV emitter start"): this is not a code emitter — it is a compiler
backend whose target is a relational catalog.

## The approach

`$onEmit` walks the TypeSpec program with `navigateProgram` and emits an
ordered SQL migration of `shrapnel.stereotype_create_revision(...)` calls
(the 0005 database API). Invariant ownership is deliberate:

| Invariant | Enforced by |
|---|---|
| contract fingerprint v2 | **server-side**, inside `stereotype_create_revision` (client-side PG jsonb canonicalization is a rabbit hole and the DB is the source of truth) |
| superset v2 (child ⊇ parent required) | DB deferred trigger at COMMIT |
| depth ≤ 3 (C1), acyclicity | DB trigger (immediate) |
| field freeze post-commit | DB trigger |
| extends requires a rationale (C2) | **emitter**, at compile time (`@doc` on the extending model — the DB's `ck_sterev_parent_rationale` would reject an empty one, so the emitter refuses earlier with a precise location) |
| scalar → field_type_code | emitter (0001 registry: 1=Long, 2=String, 3=Double, 4=Boolean, 5=Timestamp, 6=JSONB, 7=UUID) |

## Key semantic decision: materialized inheritance

The DB's superset-v2 check works on `stereotype_field` **rows** — a child
revision must *declare* every field its parent requires. TypeSpec `extends`
keeps base properties *implicit*, so DeepSeek's original skeleton failed its
own database (caught live on the scrap DB: `required fields {11,12} missing
relative to parent revision`). This emitter **materializes** inherited
required fields transitively into the child's contract. Optional parent
fields stay implicit; redeclaring an inherited required field as optional in
TypeSpec is already a compile error (`override-property-mismatch`) — the two
type systems' monotonicity rules coincide.

## Usage

```bash
npm install && npm run build
npx tsp compile ./sample          # emits shrapnel-catalog.sql
```

tspconfig options: `output-file`, `namespace` (walk filter).

Apply order on a fresh database (scrap or otherwise):

```bash
cd ../shrapnel && SHRAPNEL_PG_DSN=<dsn> npm run migrate   # 0001–0006
psql <dsn> -v ON_ERROR_STOP=1 -f shrapnel-catalog.sql
```

## Validation on the scrap database (2026-09-26)

Scrap: disposable `postgres:17-alpine` container (`scrap-tsp-eav`, port
55433), migrations 0001–0006 applied via the stock runner. Three legs:

1. **Clean apply** — `Person` (3 fields, depth 0), `VerifiedPerson`
   (depth 1, 4 declared + 2 materialized inherited), `Credential` (4 fields,
   JSONB for `Record<unknown>`): all committed, server-computed
   `sha256:` fingerprints, verification echo shows `inherited_fields = 2`.
2. **Superset oracle** — first-generation output (pre-materialization) was
   rejected by the DB at COMMIT: `required fields {11,12} missing relative
   to parent revision 2`; the whole transaction rolled back. This is the
   emitter's own first bug being caught by the intended oracle.
3. **Depth oracle** — `sample/violation-depth.tsp` (5-level extends chain,
   legal in TypeSpec) emits a migration the DB rejects:
   `depth 4 exceeds maximum 3 (C1 shallow-hierarchy doctrine)` at
   `check_stereotype_acyclic()`, 0 rows persisted after rollback.

## Known limits (prototype scope)

- Re-running the emitted migration creates **v2 revisions** (append-only
  model, `uq_sterev_identity_version` arbitrates) — idempotence is a
  catalog-reconciliation question, out of prototype scope.
- Unions/enums of string literals map to String (code 2); a `text[]` value
  extension would be the honest target later.
- `@doc` on the extending model doubles as the extends rationale; a
  dedicated `@extendsRationale` decorator is the natural next step once
  decorator plumbing exists.
- Anonymous models, cross-model `property_name` type conflicts, and
  unmappable types are compile-time diagnostics (`shrapnel-*` codes).
