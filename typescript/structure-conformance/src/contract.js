/**
 * Contract-level primitives for the Structure contract (Structure S6).
 *
 * Second-runtime implementation of the S1 contract: digests, vocabulary,
 * manifest assembly, identity derivation, and the conformance validator.
 * This file is an INDEPENDENT implementation written from the contract
 * itself — it consumes no Python code. Cross-runtime conformance means
 * this runtime and the CPython reference agree on the same golden vectors
 * and produce the same verdict_hash for them.
 *
 * Canonical JSON (must match contract.py _canonical byte for byte):
 *   - object keys sorted by unicode code point (JS default sort is code
 *     point order for strings — NOT locale collation)
 *   - compact separators, no insignificant whitespace
 *   - non-ASCII emitted RAW (no \u escapes), UTF-8 encoded before hashing
 *   - no float or >2^53-1 integer material (rejected — cross-runtime rule)
 * Digest: sha256 hex over the canonical JSON bytes.
 */

import { createHash } from "node:crypto";

export const STRUCTURE_CONTRACT_REVISION = "structure-v0.1";
export const CONFORMANCE_SUITE_VERSION = 1;
export const AUTHORITY_STATUS = "non_authoritative";

export const SOURCE_KINDS = [
  "jvm_source",
  "python_source",
  "seed_sql",
  "solscript",
  "sql_ddl",
  "sql_migration",
  "typespec",
  "unknown",
];

export const FACT_KINDS = [
  "column",
  "enum_type",
  "foreign_key",
  "index",
  "named_constraint",
  "operation",
  "seed_value",
  "table",
  "unsupported_syntax",
];

export const PARSE_STATUSES = ["complete", "failed", "partial", "unsupported"];
export const CANDIDATE_STATUSES = ["ambiguous", "resolved", "unmapped", "unsupported"];
export const RELATION_MAPPING_STATUSES = ["mapped", "unmapped"];

export const FINDING_CODES = [
  "authority_escalation_denied",
  "grammar_drift",
  "identity_collision",
  "missing_anchor",
  "parser_drift",
  "stale_source_revision",
  "unsupported_syntax",
];

const HEX64 = /^[0-9a-f]{64}$/;

export function canonicalJson(value) {
  return JSON.stringify(canonicalValue(value, "$"));
}

function canonicalValue(value, path) {
  checkDigestMaterial(value, path);
  if (
    value === null ||
    typeof value === "boolean" ||
    typeof value === "string" ||
    typeof value === "number"
  ) {
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((v, i) => canonicalValue(v, `${path}[${i}]`));
  }
  const out = {};
  const keys = Object.keys(value).sort();
  for (const k of keys) {
    out[k] = canonicalValue(value[k], `${path}.${k}`);
  }
  return out;
}

function checkDigestMaterial(value, path) {
  if (typeof value === "number" && !Number.isInteger(value)) {
    throw new Error(
      `canonical JSON digest material must not contain floats (${path}); ` +
        "use a string representation"
    );
  }
  if (
    typeof value === "number" &&
    Number.isInteger(value) &&
    (value > 9007199254740991 || value < -9007199254740991)
  ) {
    throw new Error(
      `canonical JSON digest material must not contain integers beyond 2^53-1 (${path})`
    );
  }
  if (value !== null && typeof value === "object" && typeof value !== "string") {
    for (const [k, v] of Object.entries(value)) {
      checkDigestMaterial(v, `${path}.${k}`);
    }
  }
}

export function digest(value) {
  return createHash("sha256").update(canonicalJson(value), "utf8").digest("hex");
}

// ---------------------------------------------------------------- manifest

const EXPRESSION_ENVELOPE = {
  shared_fields: ["revision", "content_hash", "source_uri"],
  field_semantics: {
    content_hash: "sha256 hex digest of the exact bytes parsed",
    revision: "source revision identifier (git rev, migration stamp)",
    source_uri: "canonical URI of the parsed artifact",
  },
  implicit_taxonomy_growth: false,
  bridging_fields: [],
};

const AUTHORITY_BOUNDARY = {
  structure_is: "reader",
  grants_governed_identity: false,
  self_authorizes_relations: false,
  governed_identity_owner: "aspects",
  relation_authority_owner: "resolution-aspects",
  authority_status: AUTHORITY_STATUS,
  escalation_finding_code: "authority_escalation_denied",
  invents_predicates: false,
  invented_predicates: [],
  unresolved_shapes_become: "explicit_unresolved_candidate",
  absence_never_asserts_nonexistence: true,
  state_boundary: [
    "structural_fact",
    "relation_candidate",
    "evaluated_relation",
    "admitted_relation",
  ],
};

export function contractManifest() {
  return {
    contract_revision: STRUCTURE_CONTRACT_REVISION,
    fingerprint_version: 1,
    owner: "structure",
    authority_status: AUTHORITY_STATUS,
    source_kinds: SOURCE_KINDS,
    fact_kinds: FACT_KINDS,
    parse_statuses: PARSE_STATUSES,
    relation_mapping_statuses: RELATION_MAPPING_STATUSES,
    candidate_statuses: CANDIDATE_STATUSES,
    finding_codes: FINDING_CODES,
    source_fact_id_version: 1,
    observation_id_version: 1,
    read_set_fingerprint_algorithm: "sha256-canonical-json-v1",
    expression_envelope: EXPRESSION_ENVELOPE,
    authority_boundary: AUTHORITY_BOUNDARY,
  };
}

export function contractFingerprint() {
  return digest(contractManifest());
}

// --------------------------------------------------------------- identities

export function readSetFingerprint(readSet) {
  const normalized = [...readSet]
    .map((e) => ({
      content_hash: e.content_hash ?? "",
      revision: e.revision ?? "",
      role: e.role ?? "",
      source_uri: e.source_uri ?? "",
    }))
    .sort((a, b) =>
      a.source_uri < b.source_uri
        ? -1
        : a.source_uri > b.source_uri
          ? 1
          : a.revision < b.revision
            ? -1
            : a.revision > b.revision
              ? 1
              : 0,
    );
  return digest(normalized);
}

export function sourceFactIdV1({ source_uri, revision, content_hash, node_path, fact_kind }) {
  for (const [name, value] of Object.entries({
    source_uri,
    revision,
    content_hash,
    node_path,
    fact_kind,
  })) {
    if (!value) throw new Error(`source_fact_id_v1: ${name} must be non-empty`);
  }
  if (!HEX64.test(content_hash)) {
    throw new Error("source_fact_id_v1: content_hash must be sha256 hex");
  }
  if (!FACT_KINDS.includes(fact_kind)) {
    throw new Error(`source_fact_id_v1: unknown fact_kind ${JSON.stringify(fact_kind)}`);
  }
  return digest({
    source_fact_id_version: 1,
    source_uri,
    revision,
    content_hash,
    node_path,
    fact_kind,
  });
}

export function observationIdV1({
  source_fact_id,
  parser_identity,
  parser_revision,
  grammar_revision,
  payload,
  read_set_fingerprint,
}) {
  for (const [name, value] of Object.entries({
    source_fact_id,
    parser_identity,
    parser_revision,
    grammar_revision,
    read_set_fingerprint,
  })) {
    if (!value) throw new Error(`observation_id_v1: ${name} must be non-empty`);
  }
  if (!HEX64.test(source_fact_id)) {
    throw new Error("observation_id_v1: source_fact_id must be sha256 hex");
  }
  if (!HEX64.test(read_set_fingerprint)) {
    throw new Error("observation_id_v1: read_set_fingerprint must be sha256 hex");
  }
  if (payload === null || payload === undefined) {
    throw new Error("observation_id_v1: payload must not be null");
  }
  return digest({
    observation_id_version: 1,
    source_fact_id,
    parser_identity,
    parser_revision,
    grammar_revision,
    canonical_payload: canonicalJson(payload),
    read_set_fingerprint,
  });
}

export function candidateIdV1({
  source_content_hash,
  parser_identity,
  parser_revision,
  grammar_revision,
  node_path,
  reason,
}) {
  for (const [name, value] of Object.entries({
    source_content_hash,
    parser_identity,
    parser_revision,
    grammar_revision,
    node_path,
    reason,
  })) {
    if (!value) throw new Error(`candidate_id_v1: ${name} must be non-empty`);
  }
  if (!HEX64.test(source_content_hash)) {
    throw new Error("candidate_id_v1: source_content_hash must be sha256 hex");
  }
  if (!CANDIDATE_STATUSES.includes(reason)) {
    throw new Error(`candidate_id_v1: unknown reason ${JSON.stringify(reason)}`);
  }
  return digest({
    candidate_id_version: 1,
    source_content_hash,
    parser_identity,
    parser_revision,
    grammar_revision,
    node_path,
    reason,
  });
}

// --------------------------------------------------------------- validator

export function validateStructuralObservation(observation) {
  const errors = [];
  for (const field of [
    "observation_id",
    "source_fact_id",
    "source",
    "parser",
    "anchor",
    "fact_kind",
    "payload",
    "parse_status",
    "read_set_fingerprint",
    "authority_status",
  ]) {
    const v = observation?.[field];
    if (!(field in (observation ?? {})) || v === null || v === "" || v === undefined) {
      errors.push(`structural observation missing required field '${field}'`);
    }
  }
  if (errors.length > 0) return errors;

  if (!FACT_KINDS.includes(observation.fact_kind)) {
    errors.push(`unknown fact_kind ${JSON.stringify(observation.fact_kind)}`);
  }

  const authority = observation.authority_status;
  if (authority !== AUTHORITY_STATUS) {
    errors.push(
      `authority_status must be '${AUTHORITY_STATUS}', got ${JSON.stringify(authority)}; ` +
        "Structure never grants governed identity",
    );
  }

  if (!HEX64.test(observation.observation_id)) {
    errors.push("observation_id must be sha256 hex");
  }
  if (!HEX64.test(observation.source_fact_id)) {
    errors.push("source_fact_id must be sha256 hex");
  }
  if (!HEX64.test(observation.read_set_fingerprint)) {
    errors.push("read_set_fingerprint must be sha256 hex");
  }

  const source = observation.source ?? {};
  for (const field of ["source_uri", "revision", "content_hash", "language", "source_kind"]) {
    if (!source[field]) {
      errors.push(`structural observation source missing required field '${field}'`);
    }
  }
  if (!SOURCE_KINDS.includes(source.source_kind)) {
    errors.push(`unknown source_kind ${JSON.stringify(source.source_kind)}`);
  }
  if (source.content_hash && !HEX64.test(source.content_hash)) {
    errors.push("source content_hash must be sha256 hex");
  }

  const parser = observation.parser ?? {};
  for (const field of ["parser_identity", "parser_revision", "grammar_revision"]) {
    if (!parser[field]) {
      errors.push(`structural observation parser missing required field '${field}'`);
    }
  }

  const anchor = observation.anchor ?? {};
  if (!anchor.node_path) {
    errors.push("structural observation anchor missing node_path");
  }

  const parseStatus = observation.parse_status;
  if (!PARSE_STATUSES.includes(parseStatus)) {
    errors.push(`unknown parse_status ${JSON.stringify(parseStatus)}`);
  }
  const diagnostics = observation.diagnostics ?? [];
  if (["partial", "unsupported", "failed"].includes(parseStatus) && diagnostics.length === 0) {
    errors.push(
      `parse_status '${parseStatus}' requires diagnostics; a partial or ` +
        "failed parse must never silently assert the construct does not exist",
    );
  }

  const mapping = observation.relation_mapping;
  if (mapping !== null && mapping !== undefined) {
    const status = mapping.status;
    if (!RELATION_MAPPING_STATUSES.includes(status)) {
      errors.push(`unknown relation_mapping status ${JSON.stringify(status)}`);
    } else if (status === "mapped") {
      if (!mapping.governed_relation_id) {
        errors.push("relation_mapping status 'mapped' requires governed_relation_id");
      }
      if (!mapping.evidence_refs) {
        errors.push("relation_mapping status 'mapped' requires evidence_refs");
      }
    }
  }
  return errors;
}
