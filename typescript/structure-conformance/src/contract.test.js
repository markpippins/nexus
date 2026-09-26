/**
 * Second-runtime test suite for the Structure conformance package
 * (Structure S6). Node's built-in test runner — zero dependencies.
 *
 * Layers:
 *   1. unit: canonical JSON parity, digest-material guard, identity
 *      formulas, validator (ported assertions from test_contract.py)
 *   2. conformance: verify the committed golden vectors and pin the
 *      verdict_hash against the Python reference's expected value
 *   3. divergence: a deliberately corrupted vector MUST fail (the suite
 *      proves the verifier catches drift, not merely that it passes)
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { test } from "node:test";

import * as c from "./contract.js";
import { ConformanceError, verifyVectors } from "./verify.js";

const CONFORMANCE_SUITE_VERSION = c.CONFORMANCE_SUITE_VERSION;

const PKG_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const VECTORS_PATH = join(
  PKG_ROOT,
  "python",
  "structure",
  "fixtures",
  "conformance_vectors.json",
);

const vectors = JSON.parse(readFileSync(VECTORS_PATH, "utf8"));

// ---------------------------------------------------------------- unit

test("canonical JSON: unicode raw, code-point key order, compact", () => {
  const value = { table: "rolê_memória", z: 1, a: null, chêne: ["x", "y"], b: true };
  const got = c.canonicalJson(value);
  assert.ok(!got.includes("\\u"), `non-ASCII must be raw, got ${got}`);
  assert.equal(got, '{"a":null,"b":true,"chêne":["x","y"],"table":"rolê_memória","z":1}');
});

test("canonical JSON: digest material guard rejects floats", () => {
  assert.throws(() => c.canonicalJson({ price: 1.5 }), /floats/);
});

test("canonical JSON: digest material guard rejects >2^53-1 integers", () => {
  assert.throws(() => c.canonicalJson({ n: 2 ** 53 }), /2\^53/);
});

test("source_fact_id_v1 binds exactly the five identity inputs", () => {
  const base = {
    source_uri: "db/migrations/V178__role_memory.sql",
    revision: "v178-fixture",
    content_hash: "a".repeat(64),
    node_path: "statements[1].table.role_memory",
    fact_kind: "table",
  };
  const sfid = c.sourceFactIdV1(base);
  assert.match(sfid, /^[0-9a-f]{64}$/);
  for (const field of ["source_uri", "revision", "node_path"]) {
    assert.notEqual(
      c.sourceFactIdV1({ ...base, [field]: `${base[field]}~` }),
      sfid,
      `changing ${field} must change source_fact_id`,
    );
  }
  // fact_kind changes stay within the governed enum: an unknown kind is an
  // enum violation, not an identity change — test the binding on a real kind.
  assert.notEqual(
    c.sourceFactIdV1({ ...base, fact_kind: "column" }),
    sfid,
    "changing fact_kind must change source_fact_id",
  );
  assert.throws(() => c.sourceFactIdV1({ ...base, fact_kind: "invented" }), /fact_kind/);
  assert.throws(() => c.sourceFactIdV1({ ...base, content_hash: "zz" }), /sha256/);
});

test("read_set_fingerprint is order-independent", () => {
  const a = { source_uri: "s/a", revision: "r1", content_hash: "a".repeat(64), role: "" };
  const b = { source_uri: "s/b", revision: "r0", content_hash: "b".repeat(64), role: "" };
  assert.equal(c.readSetFingerprint([a, b]), c.readSetFingerprint([b, a]));
});

test("observation_id_v1 changes under grammar drift and is payload-bound", () => {
  const base = {
    source_fact_id: "a".repeat(64),
    parser_identity: "structure-sql-parser",
    parser_revision: "1",
    grammar_revision: "sql-ddl-v0.3.0",
    payload: { table: "t" },
    read_set_fingerprint: "b".repeat(64),
  };
  const oid = c.observationIdV1(base);
  assert.match(oid, /^[0-9a-f]{64}$/);
  assert.notEqual(c.observationIdV1({ ...base, grammar_revision: "sql-ddl-v0.4.0" }), oid);
  assert.notEqual(c.observationIdV1({ ...base, payload: { table: "other" } }), oid);
  assert.throws(() => c.observationIdV1({ ...base, source_fact_id: "short" }), /sha256/);
});

test("candidate_id_v1 requires a governed candidate status", () => {
  const base = {
    source_content_hash: "a".repeat(64),
    parser_identity: "p",
    parser_revision: "1",
    grammar_revision: "g",
    node_path: "statements[0]",
    reason: "ambiguous",
  };
  assert.match(c.candidateIdV1(base), /^[0-9a-f]{64}$/);
  assert.throws(() => c.candidateIdV1({ ...base, reason: "invented" }), /reason/);
});

test("validator: authority escalation is rejected", () => {
  const obs = {
    observation_id: "a".repeat(64),
    source_fact_id: "b".repeat(64),
    source: {
      source_uri: "u",
      revision: "r",
      content_hash: "c".repeat(64),
      language: "sql",
      source_kind: "sql_migration",
    },
    parser: { parser_identity: "p", parser_revision: "1", grammar_revision: "g" },
    anchor: { node_path: "statements[0]" },
    fact_kind: "table",
    payload: {},
    parse_status: "complete",
    read_set_fingerprint: "d".repeat(64),
    authority_status: "authoritative",
  };
  const errors = c.validateStructuralObservation(obs);
  assert.ok(errors.some((e) => e.includes("authority_status")));
});

test("validator: partial/failed parses must carry diagnostics", () => {
  const obs = {
    observation_id: "a".repeat(64),
    source_fact_id: "b".repeat(64),
    source: {
      source_uri: "u",
      revision: "r",
      content_hash: "c".repeat(64),
      language: "sql",
      source_kind: "sql_migration",
    },
    parser: { parser_identity: "p", parser_revision: "1", grammar_revision: "g" },
    anchor: { node_path: "statements[0]" },
    fact_kind: "operation",
    payload: {},
    parse_status: "partial",
    read_set_fingerprint: "d".repeat(64),
    authority_status: "non_authoritative",
  };
  assert.ok(
    c.validateStructuralObservation(obs).some((e) => e.includes("diagnostics")),
  );
});

// --------------------------------------------------------- conformance

test("committed vectors: schema and manifest provenance are intact", () => {
  assert.equal(vectors.schema_version, CONFORMANCE_SUITE_VERSION);
  assert.equal(vectors.contract_revision, c.STRUCTURE_CONTRACT_REVISION);
  assert.deepEqual(c.contractManifest(), vectors.contract_manifest);
  assert.equal(c.contractFingerprint(), vectors.contract_fingerprint);
});

test("cross-runtime conformance: all vectors pass in the second runtime", () => {
  const verdict = verifyVectors(vectors, { implementation: "node-typescript-test" });
  const failed = verdict.vector_results.filter((r) => !r.ok);
  assert.deepEqual(
    failed,
    [],
    `failing vectors: ${failed.map((f) => `${f.name}: ${f.detail}`).join("; ")}`,
  );
  assert.equal(verdict.vector_results.length, vectors.vectors.length);
});

test("cross-runtime conformance: verdict_hash equals the Python reference", () => {
  const verdict = verifyVectors(vectors, { implementation: "node-typescript-test" });
  assert.equal(
    verdict.verdict_hash,
    vectors.expected_verdict_hash,
    "second-runtime verdict_hash must byte-match the reference expectation",
  );
});

// ---------------------------------------------------------- divergence

test("divergence: a tampered machine vector must FAIL verification", () => {
  const tampered = JSON.parse(JSON.stringify(vectors));
  const machine = tampered.vectors.find((v) => v.kind === "machine");
  machine.inputs.text += " -- tampered\n";
  const verdict = verifyVectors(tampered, { implementation: "node-typescript-test" });
  const hit = verdict.vector_results.find((r) => r.name === machine.name);
  assert.equal(hit.ok, false);
  assert.match(hit.detail, /content_hash/);
});

test("divergence: a corrupted expected digest must FAIL verification", () => {
  const corrupted = JSON.parse(JSON.stringify(vectors));
  const sfid = corrupted.vectors.find((v) => v.kind === "source_fact_id");
  sfid.expected = "0".repeat(64);
  const verdict = verifyVectors(corrupted, { implementation: "node-typescript-test" });
  const hit = verdict.vector_results.find((r) => r.name === sfid.name);
  assert.equal(hit.ok, false);
  assert.match(hit.detail, /mismatch/);
});

test("divergence: verdict_hash differs when a vector fails", () => {
  const corrupted = JSON.parse(JSON.stringify(vectors));
  const sfid = corrupted.vectors.find((v) => v.kind === "source_fact_id");
  sfid.expected = "0".repeat(64);
  const verdict = verifyVectors(corrupted, { implementation: "node-typescript-test" });
  assert.notEqual(verdict.verdict_hash, vectors.expected_verdict_hash);
});

test("vector file schema violations are rejected", () => {
  assert.throws(() => verifyVectors({ ...vectors, schema_version: 99 }), /must be 1/);
  assert.throws(
    () => verifyVectors({ ...vectors, vectors: [] }),
    /non-empty/,
  );
});
