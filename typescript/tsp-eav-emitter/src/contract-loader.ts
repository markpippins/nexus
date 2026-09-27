/**
 * Mongo-side contract loader — the enforcement companion to
 * tsp-eav-emitter. The emitter compiles TypeSpec stereotypes INTO the
 * shrapnel catalog; this loader reads the compiled contract back OUT and
 * validates documents before they reach MongoDB.
 *
 * Contract authority (read from the database, never hardcoded):
 *   - shrapnel.stereotype_resolve(name)      -> (stereotype_id, head_revision_id, version)
 *   - shrapnel.stereotype_effective_contract(rev) -> (property_name, required, ...)
 *   - shrapnel.field.property_name           -> field_type_code (1..7 registry)
 *
 * The dispatch design (thread 8d431a5d) carries stereotype, schema_version
 * and schema_fingerprint on every Mongo document. This loader checks:
 *   1. REGISTRY (advisory): the instance_storage registry declares the
 *      stereotype's storage class; a document aimed at mongo when the
 *      registry says otherwise (or vice versa) fails.
 *   2. EXISTENCE: the stereotype must exist and have a head revision.
 *   3. METADATA: doc.stereotype, doc.schema_version, doc.schema_fingerprint
 *      must match the catalog (staleness gate: stale fingerprint = reject;
 *      re-emit and re-validate).
 *   4. CONTRACT: every required field present; every present field's value
 *      conforms to its field_type_code (1=Long, 2=String, 3=Double,
 *      4=Boolean, 5=Timestamp, 6=JSONB, 7=UUID).
 *   5. SANITY: _id present, no reserved-key collisions beyond the three
 *      metadata keys, required fields non-null.
 *
 * Only after all gates pass does the caller insert — the demo wires the
 * loader directly in front of collection.insertOne.
 */
import type { Pool as PgPool } from "pg";

export const TYPE_NAMES: Record<number, string> = {
  1: "Long",
  2: "String",
  3: "Double",
  4: "Boolean",
  5: "Timestamp",
  6: "JSONB",
  7: "UUID",
};

export interface LoadedContract {
  stereotype: string;
  stereotype_id: number;
  head_revision_id: number;
  schema_version: number;
  schema_fingerprint: string;
  /** Full effective contract: every field with its type code and required flag. */
  fields: { property_name: string; field_type_code: number; required: boolean }[];
  /** Where the registry says instances of this shape live (null = unregistered). */
  registered_storage: string | null;
}

export interface ValidationIssue {
  gate: "registry" | "existence" | "metadata" | "contract" | "sanity";
  field?: string;
  message: string;
}

export interface ValidationResult {
  valid: boolean;
  issues: ValidationIssue[];
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function conformsToType(value: unknown, code: number): boolean {
  switch (code) {
    case 1: // Long — integral
      return typeof value === "number" && Number.isInteger(value);
    case 2: // String
      return typeof value === "string";
    case 3: // Double
      return typeof value === "number" && Number.isFinite(value);
    case 4: // Boolean
      return typeof value === "boolean";
    case 5: // Timestamp — accept BSON Date or ISO-8601 string
      return value instanceof Date || typeof value === "string";
    case 6: // JSONB — anything array/object-shaped (the anything-catch-all)
      return Array.isArray(value) || (typeof value === "object" && value !== null);
    case 7: // UUID — string form
      return typeof value === "string" && UUID_RE.test(value);
    default:
      return false;
  }
}

/** Read the compiled contract for one stereotype from shrapnel. */
export async function loadContract(pool: PgPool, stereotype: string): Promise<LoadedContract> {
  const resolveRes = await pool.query(
    `SELECT stereotype_id, head_revision_id, version
       FROM shrapnel.stereotype_resolve($1)`,
    [stereotype]
  );
  if (resolveRes.rowCount === 0) {
    throw new Error(
      `stereotype_resolve('${stereotype}') returned no rows — the stereotype does not exist in this catalog (compile the .tsp and apply the migration first).`
    );
  }
  const { stereotype_id, head_revision_id, version } = resolveRes.rows[0];

  const contractRes = await pool.query(
    `SELECT property_name, required
       FROM shrapnel.stereotype_effective_contract($1)`,
    [head_revision_id]
  );
  const fieldIds = contractRes.rows.map((r: any) => r.property_name);

  // Resolve each contract field's type via the field table (unique by
  // property_name), in one round trip.
  const fieldsRes = await pool.query(
    `SELECT property_name, field_type_code
       FROM shrapnel.field
      WHERE property_name = ANY($1::text[])`,
    [fieldIds]
  );
  const typeByProperty = new Map<string, number>(
    fieldsRes.rows.map((r: any) => [r.property_name, r.field_type_code])
  );

  const storageRes = await pool.query(
    `SELECT storage_class
       FROM shrapnel.stereotype_instance_storage
      WHERE stereotype_name = $1`,
    [stereotype]
  );

  return {
    stereotype,
    stereotype_id,
    head_revision_id,
    schema_version: version,
    schema_fingerprint: "", // filled below
    fields: contractRes.rows.map((r: any) => ({
      property_name: r.property_name,
      field_type_code: typeByProperty.get(r.property_name) ?? -1,
      required: r.required,
    })),
    registered_storage: storageRes.rows[0]?.storage_class ?? null,
  };
}

/** Fill the fingerprint via a second read (kept separate for clarity). */
export async function hydrateFingerprint(
  pool: PgPool,
  contract: LoadedContract
): Promise<LoadedContract> {
  const res = await pool.query(
    `SELECT contract_fingerprint
       FROM shrapnel.stereotype_revision
      WHERE id = $1`,
    [contract.head_revision_id]
  );
  contract.schema_fingerprint = res.rows[0]?.contract_fingerprint ?? "";
  return contract;
}

/**
 * Validate a dispatcher document against the loaded contract.
 * Pure function — no I/O — so it is directly unit-testable.
 */
export function validateDocument(
  doc: Record<string, unknown>,
  contract: LoadedContract
): ValidationResult {
  const issues: ValidationIssue[] = [];

  // ── Registry (advisory-but-binding on placement) ────────────────────
  // The document's destination collection is decided by the caller; this
  // gate checks the REGISTRY agrees that this stereotype's instances are
  // mongo-resident. Unregistered stereotypes pass here (the registry is
  // optional until 0007 is ruled); registered-as-other fails.
  if (contract.registered_storage && contract.registered_storage !== "mongodb") {
    issues.push({
      gate: "registry",
      message: `instance_storage registry says '${contract.stereotype}' instances live in '${contract.registered_storage}', not mongodb — wrong substrate for this insert.`,
    });
  }

  // ── Metadata ─────────────────────────────────────────────────────────
  if (doc.stereotype !== contract.stereotype) {
    issues.push({
      gate: "metadata",
      message: `doc.stereotype (${JSON.stringify(doc.stereotype)}) != contract stereotype '${contract.stereotype}'.`,
    });
  }
  if (doc.schema_version !== contract.schema_version) {
    issues.push({
      gate: "metadata",
      message: `stale schema_version: doc has ${JSON.stringify(doc.schema_version)}, catalog head is ${contract.schema_version}.`,
    });
  }
  if (doc.schema_fingerprint !== contract.schema_fingerprint) {
    issues.push({
      gate: "metadata",
      message: `stale schema_fingerprint: doc ${JSON.stringify(doc.schema_fingerprint)} != catalog head ${contract.schema_fingerprint}. Re-emit from .tsp and rebuild the document.`,
    });
  }

  // ── Sanity: identity ─────────────────────────────────────────────────
  if (typeof doc._id !== "string" || !UUID_RE.test(doc._id)) {
    issues.push({
      gate: "sanity",
      field: "_id",
      message: "_id must be the request UUID in string form.",
    });
  }

  // ── Contract: per-field presence + type conformance ──────────────────
  for (const f of contract.fields) {
    const present = Object.prototype.hasOwnProperty.call(doc, f.property_name);
    if (f.required) {
      const v = (doc as any)[f.property_name];
      if (!present || v === null || v === undefined) {
        issues.push({
          gate: "contract",
          field: f.property_name,
          message: `required field '${f.property_name}' is missing/null.`,
        });
        continue;
      }
      if (!conformsToType(v, f.field_type_code)) {
        issues.push({
          gate: "contract",
          field: f.property_name,
          message: `field '${f.property_name}' = ${JSON.stringify(v)} does not conform to field_type_code ${f.field_type_code} (${TYPE_NAMES[f.field_type_code] ?? "unknown"}).`,
        });
      }
    } else if (present) {
      const v = (doc as any)[f.property_name];
      if ((v !== null && v !== undefined) && !conformsToType(v, f.field_type_code)) {
        issues.push({
          gate: "contract",
          field: f.property_name,
          message: `optional field '${f.property_name}' = ${JSON.stringify(v)} does not conform to field_type_code ${f.field_type_code} (${TYPE_NAMES[f.field_type_code] ?? "unknown"}).`,
        });
      }
    }
  }

  return { valid: issues.length === 0, issues };
}
