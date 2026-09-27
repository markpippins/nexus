// ── schemas.ts ───────────────────────────────────────────────────────────────
// Port of python/substance/schemas.py.
//
// The Python service used pydantic v2 models for both request validation and
// response shaping. TypeScript has no equivalent in the standard library, and
// pulling zod in would add the first new runtime dependency to this service.
// So the validators are hand-rolled — but they reproduce pydantic's *observable*
// contract, which is what the HTTP surface actually promises:
//
//   * validation failures produce 422 with FastAPI's
//     `{ detail: [{ loc, msg, type }] }` envelope;
//   * absent optional fields are `null`, and defaulted fields are populated;
//   * a field explicitly sent as `null` is preserved as `null` (this is what
//     makes PATCH's "exclude_unset" semantics portable — see segmentSetUpdateFields).
//
// UUIDs are normalised to lowercase canonical form, matching what pydantic emits.

export const ROLES = ["primary", "supporting"] as const;
export type Role = (typeof ROLES)[number];

/**
 * Intent records were eliminated as a domain concept
 * (nebula.intent_records no longer exists); their join table is dropped by
 * python/substance/002_drop_intent_record_segment_sets.sql.
 */
export const DOMAIN_TYPES = ["candidates", "requirements"] as const;
export type DomainType = (typeof DOMAIN_TYPES)[number];

export const SEGMENT_SET_STATUSES = ["active", "archived"] as const;
export type SegmentSetStatus = (typeof SEGMENT_SET_STATUSES)[number];

// ── Validation result plumbing ───────────────────────────────────────────────

/** One entry of FastAPI's 422 `detail` list. */
export interface ValidationErrorItem {
  loc: (string | number)[];
  msg: string;
  type: string;
}

export type Valid<T> = { ok: true; value: T };
export type Invalid = { ok: false; errors: ValidationErrorItem[] };
export type ParseResult<T> = Valid<T> | Invalid;

/** A collector so a whole body is validated once and all errors reported. */
export class ErrorBag {
  readonly errors: ValidationErrorItem[] = [];

  add(loc: (string | number)[], msg: string, type: string): void {
    this.errors.push({ loc, msg, type });
  }

  get ok(): boolean {
    return this.errors.length === 0;
  }
}

// ── Primitives ───────────────────────────────────────────────────────────────

/** The grammar Python's `uuid.UUID(...)` accepts, minus the braces/urn sugar. */
const UUID_CANONICAL = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const UUID_HEX32 = /^[0-9a-f]{32}$/i;
const URN_PREFIX = /^urn:uuid:/i;

/**
 * Strip the urn:uuid: prefix and a surrounding pair of braces, leaving the hex.
 * The braces are peeled one character at a time on purpose: a regex like
 * /^\{.*\}$/ would match the whole braced literal and delete the UUID with it.
 */
function stripDecorations(value: string): string {
  const withoutUrn = value.replace(URN_PREFIX, "");
  if (withoutUrn.length >= 2 && withoutUrn.startsWith("{") && withoutUrn.endsWith("}")) {
    return withoutUrn.slice(1, -1);
  }
  return withoutUrn;
}

export function isUuid(value: unknown): value is string {
  if (typeof value !== "string") {
    return false;
  }
  const stripped = stripDecorations(value);
  return UUID_CANONICAL.test(stripped) || UUID_HEX32.test(stripped);
}

/** Normalise any accepted UUID spelling to lowercase canonical 8-4-4-4-12. */
export function normalizeUuid(value: string): string {
  const stripped = stripDecorations(value).toLowerCase();
  if (UUID_HEX32.test(stripped)) {
    return [
      stripped.slice(0, 8),
      stripped.slice(8, 12),
      stripped.slice(12, 16),
      stripped.slice(16, 20),
      stripped.slice(20, 32),
    ].join("-");
  }
  return stripped;
}

/** A plain JSON object — pydantic's `dict`. Arrays and null are not dicts. */
export function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * pydantic's lax int coercion accepts an integer-valued number and a numeric
 * string, and rejects bool (a bool is not a number here even though JS is
 * structurally happy to let it through) and any fractional value.
 */
export function coerceInt(value: unknown): number | undefined {
  if (typeof value === "number") {
    return Number.isInteger(value) ? value : undefined;
  }
  if (typeof value === "string" && /^-?\d+$/.test(value.trim())) {
    return Number(value.trim());
  }
  return undefined;
}

function requiredUuid(
  value: unknown,
  loc: (string | number)[],
  bag: ErrorBag,
): string | undefined {
  if (isUuid(value)) {
    return normalizeUuid(value);
  }
  bag.add(loc, "Input should be a valid UUID", "uuid_parsing");
  return undefined;
}

function optionalUuid(
  value: unknown,
  loc: (string | number)[],
  bag: ErrorBag,
): string | null {
  if (value === undefined || value === null) {
    return null;
  }
  if (isUuid(value)) {
    return normalizeUuid(value);
  }
  bag.add(loc, "Input should be a valid UUID", "uuid_parsing");
  return null;
}

function optionalStr(
  value: unknown,
  loc: (string | number)[],
  bag: ErrorBag,
): string | null {
  if (value === undefined || value === null) {
    return null;
  }
  if (typeof value === "string") {
    return value;
  }
  bag.add(loc, "Input should be a valid string", "string_type");
  return null;
}

function requiredInt(
  value: unknown,
  loc: (string | number)[],
  bag: ErrorBag,
): number | undefined {
  if (value === undefined || value === null) {
    bag.add(loc, "Field required", "missing");
    return undefined;
  }
  const coerced = coerceInt(value);
  if (coerced === undefined) {
    bag.add(loc, "Input should be a valid integer", "int_parsing");
    return undefined;
  }
  return coerced;
}

function optionalInt(
  value: unknown,
  loc: (string | number)[],
  bag: ErrorBag,
): number | null {
  if (value === undefined || value === null) {
    return null;
  }
  const coerced = coerceInt(value);
  if (coerced === undefined) {
    bag.add(loc, "Input should be a valid integer", "int_parsing");
    return null;
  }
  return coerced;
}

function optionalDict(
  value: unknown,
  loc: (string | number)[],
  bag: ErrorBag,
  dflt: Record<string, unknown> | null,
): Record<string, unknown> | null {
  if (value === undefined || value === null) {
    return dflt;
  }
  if (isPlainObject(value)) {
    return value;
  }
  bag.add(loc, "Input should be a valid dictionary", "dict_type");
  return dflt;
}

function literal<T extends string>(
  value: unknown,
  allowed: readonly T[],
  loc: (string | number)[],
  bag: ErrorBag,
  dflt: T | null,
): T | null {
  if (value === undefined || value === null) {
    return dflt;
  }
  if (typeof value === "string" && (allowed as readonly string[]).includes(value)) {
    return value as T;
  }
  bag.add(
    loc,
    `Input should be ${allowed.map((a) => `'${a}'`).join(" or ")}`,
    "literal_error",
  );
  return dflt;
}

// ── Request models ───────────────────────────────────────────────────────────

export interface SegmentMemberIn {
  segment_id: string;
  ordinal: number;
  note: string | null;
}

/** Parse one member. An empty object is a valid member with no valid fields. */
export function parseSegmentMemberIn(
  raw: unknown,
  loc: (string | number)[] = [],
  bag = new ErrorBag(),
): SegmentMemberIn | null {
  if (!isPlainObject(raw)) {
    bag.add(loc, "Input should be a valid dictionary", "dict_type");
    return null;
  }
  const segmentId = requiredUuid(raw.segment_id, [...loc, "segment_id"], bag);
  const ordinal = requiredInt(raw.ordinal, [...loc, "ordinal"], bag);
  const note = optionalStr(raw.note, [...loc, "note"], bag);
  if (segmentId === undefined || ordinal === undefined) {
    return null;
  }
  return { segment_id: segmentId, ordinal, note };
}

function parseMemberList(
  raw: unknown,
  loc: (string | number)[],
  bag: ErrorBag,
): SegmentMemberIn[] | null {
  if (!Array.isArray(raw)) {
    bag.add(loc, "Input should be a valid list", "list_type");
    return null;
  }
  const out: SegmentMemberIn[] = [];
  let failed = false;
  raw.forEach((item, index) => {
    const member = parseSegmentMemberIn(item, [...loc, index], bag);
    if (member === null) {
      failed = true;
    } else {
      out.push(member);
    }
  });
  return failed ? null : out;
}

export interface SegmentSetCreate {
  name: string | null;
  description: string | null;
  metadata: Record<string, unknown>;
  members: SegmentMemberIn[];
}

export function parseSegmentSetCreate(raw: unknown): ParseResult<SegmentSetCreate> {
  const bag = new ErrorBag();
  if (!isPlainObject(raw)) {
    return { ok: false, errors: [{ loc: ["body"], msg: "Input should be a valid dictionary", type: "dict_type" }] };
  }
  const name = optionalStr(raw.name, ["name"], bag);
  const description = optionalStr(raw.description, ["description"], bag);
  const metadata = optionalDict(raw.metadata, ["metadata"], bag, {});
  const members =
    raw.members === undefined || raw.members === null
      ? []
      : parseMemberList(raw.members, ["members"], bag);
  if (!bag.ok) {
    return { ok: false, errors: bag.errors };
  }
  return {
    ok: true,
    value: { name, description, metadata: metadata ?? {}, members: members ?? [] },
  };
}

/**
 * PATCH body. Only the keys actually present are returned — the TypeScript
 * equivalent of pydantic's `model_dump(exclude_unset=True)`, which the
 * repository's SET-clause builder depends on to tell "unset" from "set to null".
 */
/** Values a PATCH can set. `null` is a legal, meaningful value for every key. */
export type SegmentSetUpdateFields = {
  name?: string | null;
  description?: string | null;
  status?: SegmentSetStatus | null;
  metadata?: Record<string, unknown> | null;
};

/** The only columns a PATCH may write. Interpolated into SQL, so it is fixed. */
export const SEGMENT_SET_UPDATE_COLUMNS = [
  "name",
  "description",
  "status",
  "metadata",
] as const;

export function parseSegmentSetUpdate(raw: unknown): ParseResult<SegmentSetUpdateFields> {
  const bag = new ErrorBag();
  if (!isPlainObject(raw)) {
    return {
      ok: false,
      errors: [{ loc: ["body"], msg: "Input should be a valid dictionary", type: "dict_type" }],
    };
  }
  const fields: SegmentSetUpdateFields = {};
  // A key present with an explicit `null` is a real instruction ("clear this
  // column") and must survive into the SQL, so presence is checked with `in`
  // and null is preserved rather than dropped.
  if ("name" in raw) {
    fields.name = optionalStr(raw.name, ["name"], bag);
  }
  if ("description" in raw) {
    fields.description = optionalStr(raw.description, ["description"], bag);
  }
  if ("status" in raw) {
    fields.status = raw.status === null
      ? null
      : literal(raw.status, SEGMENT_SET_STATUSES, ["status"], bag, null);
  }
  if ("metadata" in raw) {
    fields.metadata =
      raw.metadata === null
        ? null
        : optionalDict(raw.metadata, ["metadata"], bag, null) ?? null;
  }
  if (!bag.ok) {
    return { ok: false, errors: bag.errors };
  }
  return { ok: true, value: fields };
}

export interface MembersAddIn {
  segments: SegmentMemberIn[];
}

export function parseMembersAddIn(raw: unknown): ParseResult<MembersAddIn> {
  const bag = new ErrorBag();
  if (!isPlainObject(raw)) {
    return { ok: false, errors: [{ loc: ["body"], msg: "Input should be a valid dictionary", type: "dict_type" }] };
  }
  if (raw.segments === undefined || raw.segments === null) {
    return {
      ok: false,
      errors: [{ loc: ["segments"], msg: "Field required", type: "missing" }],
    };
  }
  const segments = parseMemberList(raw.segments, ["segments"], bag);
  if (segments === null || !bag.ok) {
    return { ok: false, errors: bag.errors.length > 0 ? bag.errors : [{ loc: ["segments"], msg: "Input should be a valid list", type: "list_type" }] };
  }
  return { ok: true, value: { segments } };
}

export interface DomainLinkIn {
  segment_set_id: string;
  role: Role;
}

export function parseDomainLinkIn(raw: unknown): ParseResult<DomainLinkIn> {
  const bag = new ErrorBag();
  if (!isPlainObject(raw)) {
    return { ok: false, errors: [{ loc: ["body"], msg: "Input should be a valid dictionary", type: "dict_type" }] };
  }
  const segmentSetId = requiredUuid(raw.segment_set_id, ["segment_set_id"], bag);
  const role = literal(raw.role, ROLES, ["role"], bag, "primary") ?? "primary";
  if (segmentSetId === undefined) {
    return { ok: false, errors: bag.errors.length > 0 ? bag.errors : [{ loc: ["segment_set_id"], msg: "Field required", type: "missing" }] };
  }
  if (!bag.ok) {
    return { ok: false, errors: bag.errors };
  }
  return { ok: true, value: { segment_set_id: segmentSetId, role } };
}

// ── Transcript-ingest models ─────────────────────────────────────────────────

export interface SegmentFromArcs {
  start_block_id: string;
  end_block_id: string;
  start_block_index: number;
  end_block_index: number;
  segment_type: string | null;
  title: string | null;
  notes_md: string | null;
}

export function parseSegmentFromArcs(
  raw: unknown,
  loc: (string | number)[] = [],
  bag = new ErrorBag(),
): SegmentFromArcs | null {
  if (!isPlainObject(raw)) {
    bag.add(loc, "Input should be a valid dictionary", "dict_type");
    return null;
  }
  const startBlockId = requiredUuid(raw.start_block_id, [...loc, "start_block_id"], bag);
  const endBlockId = requiredUuid(raw.end_block_id, [...loc, "end_block_id"], bag);
  const startBlockIndex = requiredInt(raw.start_block_index, [...loc, "start_block_index"], bag);
  const endBlockIndex = requiredInt(raw.end_block_index, [...loc, "end_block_index"], bag);
  const segmentType = optionalStr(raw.segment_type, [...loc, "segment_type"], bag);
  const title = optionalStr(raw.title, [...loc, "title"], bag);
  const notesMd = optionalStr(raw.notes_md, [...loc, "notes_md"], bag);
  if (
    startBlockId === undefined ||
    endBlockId === undefined ||
    startBlockIndex === undefined ||
    endBlockIndex === undefined
  ) {
    return null;
  }
  return {
    start_block_id: startBlockId,
    end_block_id: endBlockId,
    start_block_index: startBlockIndex,
    end_block_index: endBlockIndex,
    segment_type: segmentType ?? "discussion",
    title,
    notes_md: notesMd,
  };
}

export interface SegmentSetFromSegmentsCreate {
  name: string | null;
  description: string | null;
  metadata: Record<string, unknown>;
  conversation_id: string;
  snapshot_id: string;
  segments: SegmentFromArcs[];
}

export function parseSegmentSetFromSegmentsCreate(
  raw: unknown,
): ParseResult<SegmentSetFromSegmentsCreate> {
  const bag = new ErrorBag();
  if (!isPlainObject(raw)) {
    return { ok: false, errors: [{ loc: ["body"], msg: "Input should be a valid dictionary", type: "dict_type" }] };
  }
  const name = optionalStr(raw.name, ["name"], bag);
  const description = optionalStr(raw.description, ["description"], bag);
  const metadata = optionalDict(raw.metadata, ["metadata"], bag, {});
  const conversationId = requiredUuid(raw.conversation_id, ["conversation_id"], bag);
  const snapshotId = requiredUuid(raw.snapshot_id, ["snapshot_id"], bag);

  let segments: SegmentFromArcs[] | null = null;
  if (raw.segments === undefined || raw.segments === null) {
    bag.add(["segments"], "Field required", "missing");
  } else if (!Array.isArray(raw.segments)) {
    bag.add(["segments"], "Input should be a valid list", "list_type");
  } else {
    segments = [];
    raw.segments.forEach((item, index) => {
      const seg = parseSegmentFromArcs(item, ["segments", index], bag);
      if (seg !== null && segments !== null) {
        segments.push(seg);
      }
    });
  }

  if (conversationId === undefined || snapshotId === undefined || !bag.ok) {
    return { ok: false, errors: bag.errors };
  }
  return {
    ok: true,
    value: {
      name,
      description,
      metadata: metadata ?? {},
      conversation_id: conversationId,
      snapshot_id: snapshotId,
      segments: segments ?? [],
    },
  };
}

export function isDomainType(value: unknown): value is DomainType {
  return typeof value === "string" && (DOMAIN_TYPES as readonly string[]).includes(value);
}

// ── Output models ────────────────────────────────────────────────────────────

/** A `nebula.segment_sets` row, exactly as selected by the repository. */
export interface SegmentSetRow {
  id: string;
  name: string | null;
  description: string | null;
  status: string;
  metadata: Record<string, unknown>;
  created_at: Date | string;
  updated_at: Date | string;
}

export interface ResolvedSegment {
  segment_id: string;
  ordinal: number;
  note: string | null;
  conversation_id: string | null;
  start_block_index: number | null;
  end_block_index: number | null;
  segment_type: string | null;
  title: string | null;
}

export interface SegmentSetOut {
  id: string;
  name: string | null;
  description: string | null;
  status: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  segments: ResolvedSegment[];
}

/** A SegmentSetOut as it round-trips through the Redis cache. */
export type CachedSegmentSet = SegmentSetOut;

/** ISO-8601 in UTC — the single timestamp spelling this service emits. */
export function toIso(value: Date | string | null | undefined): string {
  if (value === null || value === undefined) {
    return new Date(0).toISOString();
  }
  return value instanceof Date ? value.toISOString() : new Date(value).toISOString();
}

/** Shape a DB row plus its members into the resolved view. */
export function toSegmentSetOut(
  row: SegmentSetRow,
  members: readonly ResolvedSegment[] = [],
): SegmentSetOut {
  return {
    id: normalizeUuid(row.id),
    name: row.name,
    description: row.description,
    status: row.status,
    metadata: isPlainObject(row.metadata) ? row.metadata : {},
    created_at: toIso(row.created_at),
    updated_at: toIso(row.updated_at),
    segments: [...members],
  };
}

export interface DomainLinkOut {
  segment_set_id: string;
  role: Role;
  active: boolean;
  segment_set: SegmentSetOut | null;
}

export function toDomainLinkOut(
  segmentSetId: string,
  role: string,
  active: boolean,
  segmentSet: SegmentSetOut | null,
): DomainLinkOut {
  return {
    segment_set_id: normalizeUuid(segmentSetId),
    role: (ROLES as readonly string[]).includes(role) ? (role as Role) : "primary",
    active,
    segment_set: segmentSet,
  };
}
