export type ReplayVerdict =
  | "replay_ok"
  | "fingerprint_mismatch"
  | "stale_doctrine"
  | "drift_confirmed"
  | "duplicate_retry";

export type DriftCategory = "contract" | "doctrine" | "input" | "evaluator" | "receipt_lineage" | "frame";

export interface RegistryRow { entity_key: string; effective_from: string; superseded_at?: string | null; version?: number; [key: string]: unknown; }
export interface ReplayEnvelope {
  contract: { contract_id: string; contract_version: number; contract_digest: string; [key: string]: unknown };
  law: { doctrine_ids?: string[]; proposition_ids?: string[]; posture_ids?: string[]; effective_at?: string; [key: string]: unknown };
  fingerprint: { evaluation_fingerprint: string; [key: string]: unknown };
  evaluation?: Record<string, unknown>;
  [key: string]: unknown;
}
export interface ContractRegistryRow { contract_id: string; version: number; digest: string; [key: string]: unknown; }
export interface ReceiptLineage { [key: string]: unknown; }
export interface PriorAdmission {
  envelopeId: string;
  evaluationFingerprint: string;
  pebTransactionId?: string;
  admissionReceiptId?: string;
  consumedAt?: string;
}
export interface ReplayFixture {
  envelope: ReplayEnvelope;
  expected: { evaluation_fingerprint: string; disposition?: unknown; receipt?: ReceiptLineage | null; [key: string]: unknown };
  law_registry?: Record<string, RegistryRow[]>;
  contract_registry?: ContractRegistryRow[];
  priorAdmission?: PriorAdmission | null;
}
export interface ReplayResult {
  verdict: ReplayVerdict;
  category?: DriftCategory;
  fingerprint?: string;
  claimed?: unknown;
  actual?: string | null;
  reason?: string;
  missing_id?: string;
  disposition_matches?: boolean;
  receipt_lineage_valid?: boolean;
  duplicate_of?: unknown;
}

const UUID_FIELDS = new Set(["envelope_id", "subject_id", "workflow_id", "node_id", "work_request_id", "lease_id", "grant_id", "attempt_id", "input_snapshot_id", "proposition_ids", "doctrine_ids", "posture_ids", "evidence_ids", "peb_transaction_id", "admission_receipt_id", "sanctioned_transition_id"]);
const TS_FIELDS = new Set(["created_at", "effective_at", "input_captured_at", "evaluated_at"]);
const IRI_FIELDS = new Set(["@context", "subject_ref"]);
const SET_ARRAY_FIELDS = new Set(["proposition_ids", "doctrine_ids", "posture_ids", "frame_values", "evidence_ids", "unknowns"]);
const ALLOWED_TOP_KEYS = new Set(["envelope_version", "envelope_id", "created_at", "contract", "semantic", "workflow", "law", "execution", "inputs", "evaluation", "evidence", "fingerprint", "authority"]);
const DRIFT_CATEGORIES: Record<string, DriftCategory> = {
  "contract.contract_digest": "contract", "law.proposition_ids": "doctrine", "law.posture_ids": "doctrine", "law.frame_values": "frame",
  "inputs.input_snapshot_id": "input", "inputs.input_fingerprint": "input", "evaluation.evaluated_at": "evaluator", "evaluation.disposition": "evaluator",
  "evaluation.assertion_results": "evaluator", "authority.peb_transaction_id": "receipt_lineage", "authority.admission_receipt_id": "receipt_lineage",
};

export class ReplayError extends Error {}
function isObject(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function normalizeString(value: string): string { return value.normalize("NFC").replace(/^\ufeff/, "").split("").filter((char) => !/\p{Cf}/u.test(char)).join(""); }
function normalizeUuid(value: string): string {
  const trimmed = value.trim();
  if (!/^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$/.test(trimmed)) return trimmed;
  const raw = trimmed.replaceAll("-", "");
  return `${raw.slice(0, 8)}-${raw.slice(8, 12)}-${raw.slice(12, 16)}-${raw.slice(16, 20)}-${raw.slice(20)}`.toLowerCase();
}
function normalizeTimestamp(value: string): string {
  if (!/(?:[zZ]|[+-]\d\d:\d\d)$/.test(value)) throw new ReplayError(`invalid timestamp: ${value}`);
  const match = value.trim().match(/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)$/i);
  if (!match) throw new ReplayError(`invalid timestamp: ${value}`);
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) throw new ReplayError(`invalid timestamp: ${value}`);
  const iso = date.toISOString();
  const fraction = (match[2] ?? "").padEnd(6, "0").slice(0, 6);
  const utcBase = iso.slice(0, 19);
  return `${utcBase}.${fraction}Z`;
}
function normalizeIri(value: string): string {
  const url = new URL(value);
  url.protocol = url.protocol.toLowerCase();
  url.hostname = url.hostname.toLowerCase();
  if ((url.protocol === "http:" && url.port === "80") || (url.protocol === "https:" && url.port === "443")) url.port = "";
  return url.toString().replace(/\/$/, url.pathname === "/" ? "/" : "");
}
function canonicalize(value: unknown, key?: string): unknown {
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "string") {
    const normalized = normalizeString(value);
    if (UUID_FIELDS.has(key ?? "")) return normalizeUuid(normalized);
    if (TS_FIELDS.has(key ?? "")) return normalizeTimestamp(normalized);
    if (IRI_FIELDS.has(key ?? "")) return normalizeIri(normalized);
    return normalized;
  }
  if (typeof value === "number") { if (!Number.isFinite(value)) throw new ReplayError("NaN/Infinity not allowed"); return Number.isInteger(value) ? Math.trunc(value) : value; }
  if (Array.isArray(value)) {
    const result = value.map((item) => canonicalize(item, key));
    return SET_ARRAY_FIELDS.has(key ?? "") ? result.sort((a, b) => canonicalJson(a).localeCompare(canonicalJson(b))) : result;
  }
  if (isObject(value)) return Object.fromEntries(Object.keys(value).sort().map((childKey) => [normalizeString(childKey), canonicalize(value[childKey], childKey)]));
  throw new ReplayError(`unsupported value: ${typeof value}`);
}
export function canonicalJson(value: unknown): string { return JSON.stringify(canonicalize(value)); }
async function sha256(value: string): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return `sha256:${Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}
function fingerprintCore(envelope: ReplayEnvelope): Record<string, unknown> { return Object.fromEntries(Object.entries(envelope).filter(([key]) => ALLOWED_TOP_KEYS.has(key) && key !== "fingerprint")); }
export async function envelopeFingerprint(envelope: ReplayEnvelope): Promise<string> { return sha256(canonicalJson(fingerprintCore(envelope))); }
export async function envelopeDigest(envelope: ReplayEnvelope): Promise<string> { return sha256(canonicalJson(envelope)); }

export function resolveLawAsOf(registry: Record<string, RegistryRow[]> = {}, kind: string, asOf: string): RegistryRow[] {
  const grouped = new Map<string, RegistryRow[]>();
  for (const row of registry[kind] ?? []) grouped.set(row.entity_key, [...(grouped.get(row.entity_key) ?? []), row]);
  return [...grouped.keys()].sort().flatMap((key) => {
    const valid = (grouped.get(key) ?? []).filter((row) => row.effective_from <= asOf && (row.superseded_at == null || row.superseded_at > asOf));
    return valid.length ? [valid.sort((a, b) => a.effective_from.localeCompare(b.effective_from) || (a.version ?? 0) - (b.version ?? 0)).at(-1)!] : [];
  });
}
function asOfFor(envelope: ReplayEnvelope): string { const value = envelope.law.effective_at ?? envelope.evaluation?.evaluated_at; if (typeof value !== "string") throw new ReplayError("fixture has no resolvable as-of timestamp"); return value.replace(/Z$/, ""); }
function hasId(rows: RegistryRow[], id: string): boolean { return rows.some((row) => row.entity_key === id); }
function validateReceiptLineage(envelope: ReplayEnvelope, expected: ReplayFixture["expected"]): boolean {
  if (!expected.receipt) return true;
  const authority = isObject(envelope.authority) ? envelope.authority : undefined;
  return authority !== undefined && Object.entries(expected.receipt).every(([key, value]) => authority[key] === value);
}
export function classifyDrift(path: string): DriftCategory { const category = DRIFT_CATEGORIES[path]; if (!category) throw new ReplayError(`unknown mutation path: ${path}`); return category; }
export function applyMutation(envelope: ReplayEnvelope, path: string, value: unknown): ReplayEnvelope {
  const result = structuredClone(envelope); const keys = path.split("."); let target = result as Record<string, unknown>;
  for (const key of keys.slice(0, -1)) { if (!isObject(target[key])) target[key] = {}; target = target[key] as Record<string, unknown>; }
  target[keys.at(-1)!] = value; return result;
}
export async function replayEnvelope(fixture: ReplayFixture): Promise<ReplayResult> {
  const { envelope, expected } = fixture; const claimed = envelope.fingerprint?.evaluation_fingerprint;
  if (typeof claimed !== "string") return { verdict: "fingerprint_mismatch", claimed };
  const actual = await envelopeFingerprint(envelope);
  if (actual !== claimed) return { verdict: "fingerprint_mismatch", claimed, actual };
  const contract = fixture.contract_registry?.find((row) => row.contract_id === envelope.contract.contract_id && row.version === envelope.contract.contract_version);
  if (contract && contract.digest !== envelope.contract.contract_digest) return { verdict: "drift_confirmed", category: "contract", reason: "contract digest mismatch" };
  const asOf = asOfFor(envelope);
  for (const [kind, ids] of [["doctrines", envelope.law.doctrine_ids], ["propositions", envelope.law.proposition_ids], ["postures", envelope.law.posture_ids]] as const) for (const id of ids ?? []) if (!hasId(resolveLawAsOf(fixture.law_registry, kind, asOf), id)) return { verdict: "stale_doctrine", category: "doctrine", missing_id: id };
  if (actual !== expected.evaluation_fingerprint) return { verdict: "drift_confirmed", category: "evaluator", reason: "expected fingerprint mismatch" };
  if (!fixture.priorAdmission && !validateReceiptLineage(envelope, expected) && isObject(envelope.authority)) return { verdict: "drift_confirmed", category: "receipt_lineage", reason: "receipt lineage mismatch", receipt_lineage_valid: false };
  if (fixture.priorAdmission) {
    const prior = fixture.priorAdmission;
    if (prior.envelopeId !== envelope.envelope_id || prior.evaluationFingerprint !== claimed) {
      return { verdict: "drift_confirmed", category: "receipt_lineage", reason: "prior admission identity mismatch", receipt_lineage_valid: false };
    }
    return { verdict: "duplicate_retry", category: "receipt_lineage", duplicate_of: prior.pebTransactionId, receipt_lineage_valid: true };
  }
  return { verdict: "replay_ok", fingerprint: actual, disposition_matches: expected.disposition === undefined || envelope.evaluation?.disposition === expected.disposition, receipt_lineage_valid: true };
}
export async function driftVerdict(fixture: ReplayFixture, mutationPath: string, value: unknown): Promise<ReplayResult & { mutation: string }> {
  const fingerprint = await envelopeFingerprint(applyMutation(fixture.envelope, mutationPath, value));
  return { mutation: mutationPath, verdict: fingerprint === fixture.expected.evaluation_fingerprint ? "replay_ok" : "drift_confirmed", category: classifyDrift(mutationPath), fingerprint };
}
