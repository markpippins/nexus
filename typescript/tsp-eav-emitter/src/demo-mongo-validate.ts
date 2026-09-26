/**
 * End-to-end demo: compile-once contract, validate-many documents.
 *
 *   PG (scrap, :55433)   <- contract authority (stereotype_resolve +
 *                           stereotype_effective_contract + field types)
 *   Mongo (scrap, :28019) <- document destination, gated by the loader
 *
 * Runs one valid insert and five rejection cases against
 * TesterAttestationRequest — which now EXTENDS WorkRequest, so the
 * contract mixes inherited fields (id/title/intent/context/constraints/
 * created_by/created_at/updated_at) with the attestation-specific own
 * fields. The rejection cases deliberately hit BOTH halves: a missing
 * INHERITED required field and a type violation on an INHERITED field
 * prove the materialized contract is enforced like any other.
 *
 * Usage:
 *   node dist/src/demo-mongo-validate.js   (after npm run build)
 * Env:
 *   PG_DSN    default postgresql://pguser:pgpass@localhost:55433/scrap
 *   MONGO_URL default mongodb://localhost:28019
 *   MONGO_DB  default scrap_dispatch
 */
import pg from "pg";
import { MongoClient } from "mongodb";
import { loadContract, hydrateFingerprint, validateDocument } from "./contract-loader.js";

const PG_DSN = process.env.PG_DSN ?? "postgresql://pguser:pgpass@localhost:55433/scrap";
const MONGO_URL = process.env.MONGO_URL ?? "mongodb://localhost:28019";
const MONGO_DB = process.env.MONGO_DB ?? "scrap_dispatch";
const STEREOTYPE = "TesterAttestationRequest";
const COLLECTION = "tester_attestation_requests"; // the dispatcher design's collection name

const reqId = () => crypto.randomUUID();

function baseDoc(fingerprint: string, version: number): Record<string, unknown> {
  const id = reqId();
  return {
    _id: id,
    stereotype: STEREOTYPE,
    schema_version: version,
    schema_fingerprint: fingerprint,
    // ── Inherited from WorkRequest (identity/attribution/lifecycle) ──
    id,
    title: "Attest head abc123 for PR #123",
    description: "Tester attestation of the merged head per the WR verification flow.",
    intent: "verify_work",
    context: [{ pr: 123, branch: "fix/codeql-hardening" }],
    constraints: [{ required_contexts: 6 }],
    created_by: "engineer-ii",
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    // ── Own attestation fields ────────────────────────────────────────
    request_key: "pr:123:head:abc123",
    work_ref: "pr:123",
    head_sha: "abc123def4567890abcdef1234567890abcdef12",
    evidence: [{ ci_run: "36258612824", suite: "harness-srv", result: "pass" }],
    state: "pending",
    attempts: 0,
  };
}

async function main() {
  const pool = new pg.Pool({ connectionString: PG_DSN });
  const mongo = new MongoClient(MONGO_URL);
  await mongo.connect();
  const coll = mongo.db(MONGO_DB).collection(COLLECTION);

  // ── Compile-once: load the contract from shrapnel ────────────────────
  let contract = await loadContract(pool, STEREOTYPE);
  contract = await hydrateFingerprint(pool, contract);
  const inherited = contract.fields.filter((f) =>
    ["id", "title", "description", "intent", "context", "constraints", "created_by", "created_at", "updated_at"].includes(
      f.property_name
    )
  ).length;
  console.log(
    `[loader] contract loaded: ${contract.stereotype} v${contract.schema_version} ` +
      `rev=${contract.head_revision_id} fields=${contract.fields.length} ` +
      `(inherited-from-WorkRequest: ${inherited}) ` +
      `fp=${contract.schema_fingerprint.slice(0, 16)}... ` +
      `registry=${contract.registered_storage ?? "unregistered"}`
  );

  const fingerprint = contract.schema_fingerprint;
  const version = contract.schema_version;
  let accepted = 0;
  let rejected = 0;

  const attempt = (label: string, doc: Record<string, unknown>) => {
    const result = validateDocument(doc, contract);
    if (result.valid) {
      accepted++;
      console.log(`[${label}] VALID -> insert allowed`);
    } else {
      rejected++;
      console.log(`[${label}] REJECTED (${result.issues.length} issue(s)):`);
      for (const i of result.issues) {
        console.log(`    [${i.gate}]${i.field ? " " + i.field + ":" : ""} ${i.message}`);
      }
    }
    return result;
  };

  // ── 1. Valid document (inherited + own fields) ───────────────────────
  const okDoc = baseDoc(fingerprint, version);
  if (attempt("1-valid", okDoc).valid) {
    await coll.insertOne(okDoc);
    console.log("    inserted into " + COLLECTION);
  }

  // ── 2. Missing required INHERITED field ──────────────────────────────
  const missing = baseDoc(fingerprint, version);
  delete (missing as any).intent; // inherited from WorkRequest — still required
  attempt("2-missing-inherited-required", missing);

  // ── 3. Type violation on an OWN field (attempts must be Long) ────────
  const badType = baseDoc(fingerprint, version);
  (badType as any).attempts = "0";
  attempt("3-type-violation-own", badType);

  // ── 4. Type violation on an INHERITED field (id must be UUID) ────────
  const badUuid = baseDoc(fingerprint, version);
  (badUuid as any).id = "not-a-uuid";
  attempt("4-bad-uuid-inherited", badUuid);

  // ── 5. Stale fingerprint (old revision) ──────────────────────────────
  const stale = baseDoc("sha256:" + "0".repeat(64), version);
  attempt("5-stale-fingerprint", stale);

  // ── 6. Wrong stereotype name in metadata ─────────────────────────────
  const wrongName = baseDoc(fingerprint, version);
  (wrongName as any).stereotype = "CredentialRecord";
  attempt("6-wrong-stereotype", wrongName);

  console.log(`\n[demo] accepted=${accepted} rejected=${rejected} (expected 1/5)`);
  const count = await coll.countDocuments({});
  console.log(`[demo] mongo ${MONGO_DB}.${COLLECTION} now holds ${count} document(s)`);

  await pool.end();
  await mongo.close();
  process.exit(accepted === 1 && rejected === 5 && count === 1 ? 0 : 1);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
