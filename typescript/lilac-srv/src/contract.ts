/**
 * Lilac service contract — the provenance envelope and the outcome vocabulary.
 *
 * Everything a caller can do to the Resolution store passes through here
 * first. The envelope is MANDATORY: a mutation without a complete, unambiguous
 * envelope is refused before a single statement runs, so an unattributable
 * write is unrepresentable through this surface rather than merely discouraged.
 *
 * Outcome vocabulary is the documented C3 set (planned record 1394292c R4):
 *   accepted · duplicate-equivalent · conflict · refused
 *
 * `conflict` is refused, not merged: a replay that reuses an identity but
 * carries a different payload must never be silently overwritten or silently
 * ignored — see `conflictRefusal`, which records BOTH fingerprints.
 */
import { createHash } from "crypto";

/** Contract version implemented by this service. */
export const SUPPORTED_CONTRACT_VERSION = 1;

/** The four mutation surfaces. Mirrors the CHECK on
 *  resolution.mutation_provenance.surface — a fifth surface is a migration,
 *  not a code change. */
export const SURFACES = [
  "receipt",
  "ticket",
  "ticket_transition",
  "fanout_transition",
] as const;
export type Surface = (typeof SURFACES)[number];

/** Tables this service is permitted to touch. Every entry is canonical
 *  resolution.* state; no legacy vision/execution/wind/kernel table appears
 *  here and a test enforces that (AC4). */
export const CANONICAL_TABLES = [
  "receipt",
  "ticket",
  "ticket_transition",
  "fanout_transition",
  "mutation_provenance",
  "parity_evidence",
  "executable_topology",
] as const;

export type OutcomeClass = "accepted" | "duplicate-equivalent" | "conflict" | "refused";

export interface Envelope {
  source_implementation: string;
  runtime_route: string;
  contract_version: number;
  source_identity: string;
  idempotency_key: string;
}

export type RefusalCode =
  | "PROVENANCE_MISSING"
  | "PROVENANCE_INCOMPLETE"
  | "PROVENANCE_AMBIGUOUS"
  | "CONTRACT_VERSION_UNSUPPORTED"
  | "IDEMPOTENCY_KEY_REUSED"
  | "PROVENANCE_ORPHAN"
  | "REQUEST_INVALID"
  | "CONFLICT";

export const ENVELOPE_FIELDS = [
  "source_implementation",
  "runtime_route",
  "contract_version",
  "source_identity",
  "idempotency_key",
] as const;

/**
 * A typed refusal. Always carries a machine-readable `code` and a `detail`
 * object, so a caller can branch without parsing prose, and so the evidence
 * trail (which fingerprints, which route) survives in a structured field.
 */
export class ContractRefusal extends Error {
  readonly code: RefusalCode;
  readonly status: number;
  readonly detail: Record<string, unknown>;

  constructor(
    code: RefusalCode,
    status: number,
    message: string,
    detail: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ContractRefusal";
    this.code = code;
    this.status = status;
    this.detail = detail;
  }

  toBody(): Record<string, unknown> {
    return {
      outcome: "refused" satisfies OutcomeClass,
      code: this.code,
      message: this.message,
      detail: this.detail,
    };
  }
}

/** Recursive key-sorted serialisation. Deterministic across processes and
 *  Node versions, which is what makes a fingerprint comparable to the one a
 *  different writer computed earlier. */
export function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  const obj = value as Record<string, unknown>;
  const keys = Object.keys(obj).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalJson(obj[k])}`).join(",")}}`;
}

/** sha256 over the canonical form — the same shape resolution.receipt stores
 *  in payload_fingerprint, so a service fingerprint and a stored one are
 *  directly comparable. */
export function fingerprint(value: unknown): string {
  return createHash("sha256").update(canonicalJson(value)).digest("hex");
}

const IDENTITY_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._/-]*$/;

/**
 * Validate an envelope against the route it arrived on.
 *
 * `expectedRoute` is a `${METHOD} ${path-template}` string (for example
 * "POST /v1/receipts"). A declared runtime_route that disagrees with the
 * route actually handled is AMBIGUOUS, not tolerated: two different writers
 * claiming the same mutation is exactly the condition the envelope exists to
 * make impossible.
 */
export function parseEnvelope(
  raw: unknown,
  expectedRoute: string,
  supportedVersion: number = SUPPORTED_CONTRACT_VERSION,
): Envelope {
  if (raw === undefined || raw === null) {
    throw new ContractRefusal(
      "PROVENANCE_MISSING",
      422,
      `mutation refused: no provenance envelope supplied for ${expectedRoute}`,
      { expected_route: expectedRoute, required: ENVELOPE_FIELDS },
    );
  }
  if (typeof raw !== "object" || Array.isArray(raw)) {
    throw new ContractRefusal(
      "PROVENANCE_MISSING",
      422,
      "mutation refused: provenance envelope must be an object",
      { expected_route: expectedRoute, required: ENVELOPE_FIELDS },
    );
  }

  const obj = raw as Record<string, unknown>;
  const missing: string[] = [];
  for (const field of ENVELOPE_FIELDS) {
    const value = obj[field];
    if (field === "contract_version") {
      if (value === undefined || value === null) missing.push(field);
      continue;
    }
    if (typeof value !== "string" || value.trim() === "") missing.push(field);
  }
  if (missing.length > 0) {
    throw new ContractRefusal(
      "PROVENANCE_INCOMPLETE",
      422,
      `mutation refused: envelope is missing ${missing.join(", ")}`,
      { expected_route: expectedRoute, missing },
    );
  }

  const contractVersion = obj.contract_version;
  if (typeof contractVersion !== "number" || !Number.isInteger(contractVersion)) {
    throw new ContractRefusal(
      "PROVENANCE_INCOMPLETE",
      422,
      "mutation refused: contract_version must be an integer",
      { expected_route: expectedRoute, received: contractVersion },
    );
  }
  if (contractVersion !== supportedVersion) {
    throw new ContractRefusal(
      "CONTRACT_VERSION_UNSUPPORTED",
      422,
      `mutation refused: contract_version ${contractVersion} is not supported (this service speaks ${supportedVersion})`,
      { expected_route: expectedRoute, received: contractVersion, supported: supportedVersion },
    );
  }

  const envelope: Envelope = {
    source_implementation: String(obj.source_implementation).trim(),
    runtime_route: String(obj.runtime_route).trim(),
    contract_version: contractVersion,
    source_identity: String(obj.source_identity).trim(),
    idempotency_key: String(obj.idempotency_key).trim(),
  };

  if (envelope.runtime_route !== expectedRoute) {
    throw new ContractRefusal(
      "PROVENANCE_AMBIGUOUS",
      422,
      `mutation refused: envelope claims route "${envelope.runtime_route}" but arrived on ${expectedRoute}`,
      { expected_route: expectedRoute, claimed_route: envelope.runtime_route },
    );
  }
  if (!IDENTITY_PATTERN.test(envelope.source_implementation)) {
    throw new ContractRefusal(
      "PROVENANCE_AMBIGUOUS",
      422,
      "mutation refused: source_implementation is not a resolvable identifier",
      { source_implementation: envelope.source_implementation },
    );
  }
  if (envelope.idempotency_key === envelope.source_identity) {
    throw new ContractRefusal(
      "PROVENANCE_AMBIGUOUS",
      422,
      "mutation refused: idempotency_key must not be the source identity (a reused identity cannot be replayed distinctly)",
      { source_identity: envelope.source_identity },
    );
  }
  if (envelope.idempotency_key.length < 8 || envelope.idempotency_key.length > 200) {
    throw new ContractRefusal(
      "PROVENANCE_INCOMPLETE",
      422,
      "mutation refused: idempotency_key must be 8..200 characters",
      { length: envelope.idempotency_key.length },
    );
  }

  return envelope;
}

/**
 * Conflict refusal for a replay that reuses an identity with a different
 * payload. Both fingerprints travel with the refusal so an operator can see
 * exactly which two writes are in tension — the same evidence discipline the
 * Python adapter applies (lilac.py, R4).
 */
export function conflictRefusal(
  surface: Surface,
  existingFingerprint: string,
  incomingFingerprint: string,
  extra: Record<string, unknown> = {},
): ContractRefusal {
  return new ContractRefusal(
    "CONFLICT",
    409,
    `conflict on ${surface}: identity already recorded with a different payload — existing_fingerprint=${existingFingerprint} incoming_fingerprint=${incomingFingerprint}`,
    {
      surface,
      existing_fingerprint: existingFingerprint,
      incoming_fingerprint: incomingFingerprint,
      ...extra,
    },
  );
}

/** Classify a replay against the fingerprint already stored for that identity. */
export function classifyReplay(
  surface: Surface,
  existingFingerprint: string | null | undefined,
  incomingFingerprint: string,
  extra: Record<string, unknown> = {},
): "duplicate-equivalent" {
  if (existingFingerprint && existingFingerprint === incomingFingerprint) {
    return "duplicate-equivalent";
  }
  throw conflictRefusal(
    surface,
    String(existingFingerprint ?? "(none)"),
    incomingFingerprint,
    extra,
  );
}
