/**
 * SOLScript TypeScript core — Keychains event contracts.
 *
 * Ported from python/SOLScript/solscript/events.py. The contracts carry
 * identities and read-set metadata, never source content. `stableDigest`
 * MUST produce byte-identical digests to Python's `stable_digest`
 * (canonical JSON: sorted keys, separators (",", ":"), default=str).
 */

import { canonicalJson } from "./expression-compiler.js";

export const KEYCHAIN_EVENT_SCHEMA_VERSION = 1;
export const READ_SET_MANIFEST_SCHEMA_VERSION = 1;

export function stableDigest(value: unknown): string {
  return sha256Hex(canonicalJson(value));
}

// ── SHA-256 (dependency-free, Web Crypto async wrapped for parity) ───
// Python: hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

const encoder = new TextEncoder();

/** Synchronous SHA-256 implementation (no external deps; event digests are small). */
export function sha256Hex(input: string): string {
  const data = encoder.encode(input);
  const K = SHA256_K;
  const H = new Uint32Array(SHA256_H);
  const l = data.length;
  const withOne = new Uint8Array((((l + 8) >> 6) + 1) << 6);
  withOne.set(data);
  withOne[l] = 0x80;
  const bitLenHi = Math.floor((l / 0x100000000));
  const bitLenLo = (l * 8) >>> 0;
  const dv = new DataView(withOne.buffer);
  dv.setUint32(withOne.length - 8, bitLenHi);
  dv.setUint32(withOne.length - 4, bitLenLo);

  const w = new Uint32Array(64);
  const at = (arr: Uint32Array, i: number): number => arr[i] as number;
  for (let off = 0; off < withOne.length; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = dv.getUint32(off + i * 4);
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(at(w, i - 15), 7) ^ rotr(at(w, i - 15), 18) ^ (at(w, i - 15) >>> 3);
      const s1 = rotr(at(w, i - 2), 17) ^ rotr(at(w, i - 2), 19) ^ (at(w, i - 2) >>> 10);
      w[i] = (at(w, i - 16) + s0 + at(w, i - 7) + s1) >>> 0;
    }
    let a = at(H, 0), b = at(H, 1), c = at(H, 2), d = at(H, 3);
    let e = at(H, 4), f = at(H, 5), g = at(H, 6), h = at(H, 7);
    for (let i = 0; i < 64; i++) {
      const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const t1 = (h + S1 + ch + K[i]! + w[i]!) >>> 0;
      const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const t2 = (S0 + maj) >>> 0;
      h = g; g = f; f = e; e = (d + t1) >>> 0;
      d = c; c = b; b = a; a = (t1 + t2) >>> 0;
    }
    H[0] = (H[0]! + a) >>> 0; H[1] = (H[1]! + b) >>> 0; H[2] = (H[2]! + c) >>> 0; H[3] = (H[3]! + d) >>> 0;
    H[4] = (H[4]! + e) >>> 0; H[5] = (H[5]! + f) >>> 0; H[6] = (H[6]! + g) >>> 0; H[7] = (H[7]! + h) >>> 0;
  }
  return Array.from(H).map((x) => x.toString(16).padStart(8, "0")).join("");
}

function rotr(x: number, n: number): number {
  return ((x >>> n) | (x << (32 - n))) >>> 0;
}

const SHA256_K = [
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
];
const SHA256_H = [
  0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
];

// ── Read-set manifest ────────────────────────────────────────────────

export interface ReadSetManifestInput {
  sourceNamespace: string;
  evaluationId: string;
  evaluationKind: string;
  targetId: string;
  /** ISO string; capture-time fact, excluded from manifest_digest identity. */
  asOf: string;
  visibilityScope?: string;
  evaluatorId?: string;
  permissions?: Record<string, unknown>;
  context?: Record<string, unknown>;
  sourceRefs?: Record<string, unknown>[];
  manifestId?: string;
  recordedAt?: string;
  status?: string;
}

export interface ReadSetManifest {
  sourceNamespace: string;
  evaluationId: string;
  evaluationKind: string;
  targetId: string;
  asOf: string;
  visibilityScope: string;
  evaluatorId: string | null;
  permissions: Record<string, unknown>;
  context: Record<string, unknown>;
  sourceRefs: Record<string, unknown>[];
  manifestId: string;
  schemaVersion: number;
  recordedAt: string;
  manifestDigest: string;
  status: string;
  readonly idempotencyKey: string;
}

export function buildReadSetManifest(input: ReadSetManifestInput): ReadSetManifest {
  const manifest = {
    sourceNamespace: input.sourceNamespace,
    evaluationId: input.evaluationId,
    evaluationKind: input.evaluationKind,
    targetId: input.targetId,
    asOf: input.asOf,
    visibilityScope: input.visibilityScope ?? "all",
    evaluatorId: input.evaluatorId ?? null,
    permissions: input.permissions ?? {},
    context: input.context ?? {},
    sourceRefs: input.sourceRefs ?? [],
    manifestId: input.manifestId ?? crypto.randomUUID(),
    schemaVersion: READ_SET_MANIFEST_SCHEMA_VERSION,
    recordedAt: input.recordedAt ?? new Date().toISOString(),
    manifestDigest: "",
    status: input.status ?? "pending",
    idempotencyKey: "",
  };
  // Python: as_of retained but EXCLUDED from the digest descriptor.
  const descriptor = {
    schema_version: manifest.schemaVersion,
    source_namespace: manifest.sourceNamespace,
    evaluation_id: manifest.evaluationId,
    evaluation_kind: manifest.evaluationKind,
    target_id: manifest.targetId,
    visibility_scope: manifest.visibilityScope,
    evaluator_id: manifest.evaluatorId,
    permissions: manifest.permissions,
    context: manifest.context,
    source_refs: manifest.sourceRefs,
  };
  manifest.manifestDigest = stableDigest(descriptor);
  manifest.idempotencyKey = `${manifest.sourceNamespace}:evaluation:${manifest.evaluationKind}:${manifest.evaluationId}`;
  return manifest;
}

export function manifestToDict(m: ReadSetManifest): Record<string, unknown> {
  return { ...m, idempotencyKey: m.idempotencyKey };
}

// ── Keychain events ──────────────────────────────────────────────────

export interface KeychainEvent {
  sourceNamespace: string;
  sourceEventId: string;
  kind: string;
  outcome: string;
  aggregateId: string | null;
  schemaVersion: number;
  causationId: string | null;
  correlationId: string | null;
  actor: string | null;
  contractId: string | null;
  evaluatorId: string | null;
  lawId: string | null;
  effectiveAt: string | null;
  recordedAt: string;
  readSet: Record<string, unknown>;
  payload: Record<string, unknown>;
  checkpointStatus: string;
  /** Globally addressable identity: {source_namespace}:{source_event_id}. */
  readonly eventId: string;
  /** Stable source-scoped key used by outbox and Keychains consumers. */
  readonly idempotencyKey: string;
}

export function keychainEventId(e: Pick<KeychainEvent, "sourceNamespace" | "sourceEventId">): string {
  return `${e.sourceNamespace}:${e.sourceEventId}`;
}

export function keychainEventToDict(e: KeychainEvent): Record<string, unknown> {
  return {
    ...e,
    event_id: e.eventId,
    idempotency_key: e.idempotencyKey,
  };
}

function makeKeychainEvent(partial: Omit<KeychainEvent, "eventId" | "idempotencyKey" | "schemaVersion" | "recordedAt" | "checkpointStatus"> & Partial<Pick<KeychainEvent, "schemaVersion" | "recordedAt" | "checkpointStatus">>): KeychainEvent {
  const full: KeychainEvent = {
    schemaVersion: KEYCHAIN_EVENT_SCHEMA_VERSION,
    recordedAt: new Date().toISOString(),
    checkpointStatus: "pending",
    ...partial,
    eventId: keychainEventId(partial),
    idempotencyKey: keychainEventId(partial),
  } as KeychainEvent;
  return full;
}

// ── Event builders ───────────────────────────────────────────────────

export interface TransitionEventInput {
  sourceEventId: string;
  entityId: string;
  transitionId: string;
  outcome: string;
  results: unknown;
  conceptId?: string | null;
  stateBefore?: unknown;
  stateAfter?: unknown;
  effectiveAt?: string | null;
  correlationId?: string | null;
  actor?: string | null;
  sourceNamespace?: string;
}

const TRANSITION_KINDS: Record<string, string> = {
  committed: "resolution.transition.committed",
  refused: "resolution.transition.refused",
  rejected: "resolution.transition.rejected",
};

export function buildTransitionEvent(input: TransitionEventInput): KeychainEvent {
  return makeKeychainEvent({
    sourceNamespace: input.sourceNamespace ?? "sol-api",
    sourceEventId: input.sourceEventId,
    kind: TRANSITION_KINDS[input.outcome] ?? "resolution.transition.failed",
    outcome: input.outcome,
    aggregateId: input.entityId,
    causationId: input.sourceEventId,
    correlationId: input.correlationId ?? input.sourceEventId,
    actor: input.actor ?? "sol-api",
    contractId: null,
    evaluatorId: null,
    lawId: null,
    effectiveAt: input.effectiveAt ?? null,
    readSet: {
      entity_id: input.entityId,
      transition_id: input.transitionId,
      concept_id: input.conceptId ?? null,
      guard_results: input.results,
      state_before: input.stateBefore ?? null,
      state_after: input.stateAfter ?? null,
    },
    payload: { transition_id: input.transitionId },
  });
}

export interface EvaluationEventInput {
  sourceEventId: string;
  evaluationKind: string;
  targetId: string;
  manifest: ReadSetManifest;
  result: Record<string, unknown>;
  outcome?: string;
  evaluatorId?: string | null;
  actor?: string | null;
  sourceNamespace?: string;
}

export function buildEvaluationEvent(input: EvaluationEventInput): KeychainEvent {
  return makeKeychainEvent({
    sourceNamespace: input.sourceNamespace ?? "sol-api",
    sourceEventId: input.sourceEventId,
    kind: `resolution.evaluation.${input.evaluationKind}.completed`,
    outcome: input.outcome ?? "committed",
    aggregateId: input.targetId,
    causationId: input.manifest.manifestId,
    correlationId: input.manifest.evaluationId,
    actor: input.actor ?? "sol-api",
    contractId: null,
    evaluatorId: input.evaluatorId ?? "solscript",
    lawId: null,
    effectiveAt: input.manifest.asOf,
    readSet: {
      manifest_id: input.manifest.manifestId,
      manifest_digest: input.manifest.manifestDigest,
      evaluation_id: input.manifest.evaluationId,
      source_namespace: input.manifest.sourceNamespace,
    },
    payload: {
      evaluation_kind: input.evaluationKind,
      target_id: input.targetId,
      manifest_id: input.manifest.manifestId,
      result: input.result,
    },
  });
}
