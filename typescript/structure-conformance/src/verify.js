/**
 * Vector verifier and machine-readable verdict (Structure S6).
 *
 * Consumes the runtime-neutral golden vectors with zero dependency on the
 * Python reference. Every digest is re-derived locally from the vector
 * inputs; the verdict re-derives per-vector results and a verdict_hash
 * that MUST equal the Python reference's for the same vectors — that
 * equality is the cross-runtime conformance proof.
 */

import { createHash } from "node:crypto";

import * as c from "./contract.js";

export class ConformanceError extends Error {}

/** Value equality with Python `==` semantics for JSON material: arrays and
 * plain objects compare structurally, everything else by identity. The
 * verifier MUST NOT use `===` on arrays — Python's list equality (used to
 * build the vectors) is value equality, and seed_value payloads carry lists. */
function deepEq(a, b) {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) {
    return a.length === b.length && a.every((x, i) => deepEq(x, b[i]));
  }
  if (a && b && typeof a === "object" && typeof b === "object") {
    const ka = Object.keys(a);
    const kb = Object.keys(b);
    return ka.length === kb.length && ka.every((k) => k in b && deepEq(a[k], b[k]));
  }
  return false;
}

function digestOf(value) {
  return c.digest(value);
}

function sha256Hex(text) {
  return createHash("sha256").update(text, "utf8").digest("hex");
}

export function checkVectorFile(vectors) {
  if (typeof vectors !== "object" || vectors === null || Array.isArray(vectors)) {
    throw new ConformanceError("vectors file must be a JSON object");
  }
  if (vectors.schema_version !== c.CONFORMANCE_SUITE_VERSION) {
    throw new ConformanceError(
      `schema_version must be ${c.CONFORMANCE_SUITE_VERSION}, got ${JSON.stringify(vectors.schema_version)}`,
    );
  }
  for (const field of ["contract_revision", "contract_fingerprint", "grammar_revision", "vectors"]) {
    if (!(field in vectors)) {
      throw new ConformanceError(`vectors file missing required field '${field}'`);
    }
  }
  if (!vectors.contract_revision || !vectors.grammar_revision) {
    throw new ConformanceError(
      "contract_revision and grammar_revision must be non-empty strings",
    );
  }
  if (!Array.isArray(vectors.vectors) || vectors.vectors.length === 0) {
    throw new ConformanceError("vectors must be a non-empty array");
  }
  const names = new Set();
  for (const v of vectors.vectors) {
    for (const field of ["name", "kind", "expectation", "inputs"]) {
      if (!(field in v)) {
        throw new ConformanceError(`vector missing required field '${field}': ${JSON.stringify(v).slice(0, 80)}`);
      }
    }
    if (names.has(v.name)) {
      throw new ConformanceError(`duplicate vector name '${v.name}'`);
    }
    names.add(v.name);
  }
}

export function verifyVectors(vectors, options = {}) {
  const implementation = options?.implementation;
  checkVectorFile(vectors);
  const results = [];
  for (const v of vectors.vectors) {
    let result;
    try {
      const [ok, detail] = verifyOne(vectors, v);
      result = {
        name: v.name,
        kind: v.kind,
        expectation: v.expectation,
        ok,
        ...(detail ? { detail } : {}),
      };
    } catch (err) {
      result = {
        name: v.name ?? "?",
        kind: v.kind ?? "?",
        expectation: v.expectation ?? "",
        ok: false,
        detail: `${err.constructor.name}: ${err.message}`,
      };
    }
    results.push(result);
  }
  const verdict = {
    suite_version: c.CONFORMANCE_SUITE_VERSION,
    vector_file: vectors.vector_file ?? "structure-conformance-vectors.json",
    implementation,
    contract: {
      contract_revision: vectors.contract_revision,
      contract_fingerprint: vectors.contract_fingerprint,
      grammar_revision: vectors.grammar_revision,
    },
    vector_results: results,
  };
  verdict.verdict_hash = digestOf({
    suite_version: c.CONFORMANCE_SUITE_VERSION,
    vector_results: results,
  });
  return verdict;
}

function verifyOne(vectors, v) {
  const kind = v.kind;
  const inputs = v.inputs ?? {};
  if (kind === "canonical_json") {
    const got = digestOf(inputs.value);
    return [got === v.expected, got === v.expected ? null : `canonical json digest mismatch: got ${got.slice(0, 16)}…`];
  }
  if (kind === "contract_fingerprint") {
    const got = c.contractFingerprint();
    return [got === v.expected, got === v.expected ? null : `contract fingerprint mismatch: got ${got.slice(0, 16)}…`];
  }
  if (kind === "source_fact_id") {
    const got = c.sourceFactIdV1({
      source_uri: inputs.source_uri,
      revision: inputs.revision,
      content_hash: inputs.content_hash,
      node_path: inputs.node_path,
      fact_kind: inputs.fact_kind,
    });
    return [got === v.expected, got === v.expected ? null : `source_fact_id mismatch: got ${got.slice(0, 16)}…`];
  }
  if (kind === "read_set_fingerprint") {
    const got = c.readSetFingerprint(inputs.read_set);
    return [got === v.expected, got === v.expected ? null : `read_set_fingerprint mismatch: got ${got.slice(0, 16)}…`];
  }
  if (kind === "observation_id") {
    const got = c.observationIdV1({
      source_fact_id: inputs.source_fact_id,
      parser_identity: inputs.parser.parser_identity,
      parser_revision: inputs.parser.parser_revision,
      grammar_revision: inputs.parser.grammar_revision,
      payload: inputs.payload,
      read_set_fingerprint: inputs.read_set_fingerprint,
    });
    return [got === v.expected, got === v.expected ? null : `observation_id mismatch: got ${got.slice(0, 16)}…`];
  }
  if (kind === "candidate_id") {
    const got = c.candidateIdV1({
      source_content_hash: inputs.source_content_hash,
      parser_identity: inputs.parser.parser_identity,
      parser_revision: inputs.parser.parser_revision,
      grammar_revision: inputs.parser.grammar_revision,
      node_path: inputs.node_path,
      reason: inputs.reason,
    });
    return [got === v.expected, got === v.expected ? null : `candidate_id mismatch: got ${got.slice(0, 16)}…`];
  }
  if (kind === "machine") {
    return verifyMachine(inputs, v.checks ?? {});
  }
  if (kind === "invalid_observation") {
    const errors = c.validateStructuralObservation(inputs.observation);
    if (errors.length === 0) {
      return [false, "validator accepted an observation it must reject"];
    }
    const needle = inputs.must_reject_with ?? "";
    if (needle && !errors.some((e) => e.includes(needle))) {
      return [false, `validator rejected but not for '${needle}': ${errors.slice(0, 2).join("; ")}`];
    }
    return [true, null];
  }
  throw new ConformanceError(`unknown vector kind '${kind}'`);
}

function verifyMachine(inputs, checks) {
  // 1. tamper-evident source
  const gotHash = sha256Hex(inputs.text ?? "");
  if (gotHash !== inputs.content_hash) {
    return [false, "source content_hash mismatch (tampered or stale source)"];
  }

  // 2. read-set fingerprint re-derives
  const rsf = c.readSetFingerprint([
    {
      source_uri: inputs.source_uri ?? "",
      revision: inputs.revision ?? "",
      content_hash: inputs.content_hash ?? "",
      role: inputs.role ?? "",
    },
  ]);
  if (rsf !== inputs.read_set_fingerprint) {
    return [false, "read_set_fingerprint mismatch (stale source revision?)"];
  }

  // 3. every observation validates and re-derives both identities
  const observations = inputs.observations ?? [];
  if (observations.length === 0) {
    return [false, "machine vector carries no observations"];
  }
  const pinnedParser = inputs.parser ?? {};
  const kindsSeen = new Set();
  for (const obs of observations) {
    const errors = c.validateStructuralObservation(obs);
    if (errors.length > 0) {
      return [false, `observation ${(obs.observation_id ?? "?").slice(0, 12)}… invalid: ${errors[0]}`];
    }
    const sfid = c.sourceFactIdV1({
      source_uri: obs.source.source_uri,
      revision: obs.source.revision,
      content_hash: obs.source.content_hash,
      node_path: obs.anchor.node_path,
      fact_kind: obs.fact_kind,
    });
    if (sfid !== obs.source_fact_id) {
      return [false, `observation ${obs.observation_id.slice(0, 12)}… source_fact_id does not re-derive from its own provenance`];
    }
    const p = obs.parser ?? {};
    const oid = c.observationIdV1({
      source_fact_id: obs.source_fact_id,
      parser_identity: p.parser_identity ?? "",
      parser_revision: p.parser_revision ?? "",
      grammar_revision: p.grammar_revision ?? "",
      payload: obs.payload,
      read_set_fingerprint: obs.read_set_fingerprint ?? "",
    });
    if (oid !== obs.observation_id) {
      return [false, `observation ${obs.observation_id.slice(0, 12)}… observation_id does not re-derive from its own provenance`];
    }
    if (p.grammar_revision !== inputs.grammar_revision) {
      return [false, `observation ${obs.observation_id.slice(0, 12)}… grammar_revision ${JSON.stringify(p.grammar_revision)} != pinned ${JSON.stringify(inputs.grammar_revision)} (parser drift)`];
    }
    if (p.parser_identity !== pinnedParser.parser_identity) {
      return [false, `observation ${obs.observation_id.slice(0, 12)}… parser_identity differs from the pinned implementation (parser drift)`];
    }
    kindsSeen.add(obs.fact_kind);
  }

  // 4. expected inventory
  const missingKinds = (checks.expected_fact_kinds ?? []).filter((k) => !kindsSeen.has(k)).sort();
  if (missingKinds.length > 0) {
    return [false, `expected fact kinds missing: ${JSON.stringify(missingKinds)}`];
  }

  const unsupportedPairs = [];
  for (const o of observations) {
    for (const d of o.diagnostics ?? []) {
      if (d.code === "unsupported_syntax") unsupportedPairs.push(d);
    }
  }
  for (const want of checks.expected_unsupported ?? []) {
    if (want.reason_code && !unsupportedPairs.some((d) => d.code === want.reason_code)) {
      return [false, `expected unsupported diagnostic '${want.reason_code}' not found`];
    }
  }

  for (const want of checks.expected_structural_facts ?? []) {
    const payload = want.payload ?? {};
    const found = observations.some(
      (o) =>
        o.fact_kind === want.fact_kind &&
        Object.entries(payload).every(([k, val]) =>
          deepEq((o.payload ?? {})[k], val),
        ),
    );
    if (!found) {
      return [false, `expected structural fact missing: ${JSON.stringify(want)}`];
    }
  }

  const minObs = checks.min_observations ?? 0;
  if (observations.length < minObs) {
    return [false, `expected at least ${minObs} observations, got ${observations.length}`];
  }
  return [true, null];
}
