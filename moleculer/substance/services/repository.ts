// ── repository.ts ────────────────────────────────────────────────────────────
// Port of python/substance/repository.py. Every statement is carried across
// unchanged; the SQL is the contract, not an implementation detail, because
// the ON CONFLICT predicates below are checked by Postgres against partial
// unique indexes and a mismatch is a hard error rather than a silent one.

import { invalidateSegsetBestEffort } from "./cache";
import { execute, query, queryOne, withTransaction } from "./db";
import {
  isDomainType,
  type DomainType,
  type ResolvedSegment,
  type SegmentMemberIn,
  type SegmentSetRow,
  type SegmentSetUpdateFields,
  SEGMENT_SET_UPDATE_COLUMNS,
} from "./schemas";

/**
 * Sentinel for "infinitely valid" — MUST match the NOT NULL DEFAULT on every
 * table and the WHERE clause of every partial unique index. If you change the
 * DB default, update this constant AND every ON CONFLICT WHERE clause below.
 * Postgres will reject the query if the ON CONFLICT predicate doesn't match
 * the index, so mismatches fail loudly rather than silently.
 */
export const FOREVER = "9999-12-31 00:00:00+00";

/** The segments_history sentinel for "this row has not been superseded". */
export const SEG_HISTORY_FOREVER = "9999-12-31 23:59:59+00";

export interface DomainTable {
  table: string;
  fkColumn: string;
}

/**
 * Maps the public `domain_type` path segment to (join_table, fk_column).
 * NOTE: "intent-records" was removed — intent records were eliminated as a
 * domain concept (nebula.intent_records no longer exists); its join table is
 * dropped by 002_drop_intent_record_segment_sets.sql.
 */
export const DOMAIN_TABLES: Readonly<Record<DomainType, DomainTable>> = {
  candidates: { table: "nebula.candidate_segment_sets", fkColumn: "candidate_id" },
  requirements: {
    table: "nebula.requirement_segment_sets",
    fkColumn: "requirement_id",
  },
};

export class UnknownDomainTypeError extends Error {
  readonly domainType: string;
  constructor(domainType: string) {
    super(`unknown domain_type '${domainType}'`);
    this.name = "UnknownDomainTypeError";
    this.domainType = domainType;
  }
}

/** Look up a domain's join table. Throws for anything not in the map. */
export function domainTable(domainType: string): DomainTable {
  if (!isDomainType(domainType)) {
    throw new UnknownDomainTypeError(domainType);
  }
  return DOMAIN_TABLES[domainType];
}

/** True when the string names a supported domain (used by the router's 404). */
export function isKnownDomainType(domainType: string): domainType is DomainType {
  return isDomainType(domainType);
}

const SEGMENT_SET_COLUMNS =
  "id, name, description, status, metadata, created_at, updated_at";

// ── Segment sets ─────────────────────────────────────────────────────────────

export async function createSegmentSet(
  name: string | null,
  description: string | null,
  metadata: Record<string, unknown>,
): Promise<SegmentSetRow> {
  const row = await queryOne<SegmentSetRow>(
    `
    insert into nebula.segment_sets (name, description, metadata)
    values ($1, $2, $3::jsonb)
    returning ${SEGMENT_SET_COLUMNS}
  `,
    [name, description, JSON.stringify(metadata ?? {})],
  );
  if (row === null) {
    throw new Error("createSegmentSet: INSERT ... RETURNING produced no row");
  }
  return row;
}

/** List all current-valid segment sets. */
export async function listSegmentSets(
  limit = 200,
  offset = 0,
): Promise<SegmentSetRow[]> {
  return query<SegmentSetRow>(
    `
    select ${SEGMENT_SET_COLUMNS}
    from nebula.segment_sets
    where valid_until > now()
    order by created_at desc
    limit $1 offset $2
  `,
    [limit, offset],
  );
}

export async function getSegmentSet(segmentSetId: string): Promise<SegmentSetRow | null> {
  return queryOne<SegmentSetRow>(
    `
    select ${SEGMENT_SET_COLUMNS}
    from nebula.segment_sets
    where id = $1 and valid_until > now()
  `,
    [segmentSetId],
  );
}

/**
 * The SET-clause half of updateSegmentSet, split out so it is testable without
 * a database. Column identifiers are drawn from a fixed allowlist — the Python
 * version interpolated the key straight into the statement and was only safe
 * because pydantic constrained the field set; here the constraint is explicit.
 */
export function buildUpdateSetClauses(
  fields: SegmentSetUpdateFields,
): { setClauses: string[]; values: unknown[] } {
  const setClauses: string[] = [];
  const values: unknown[] = [];
  for (const [col, val] of Object.entries(fields)) {
    if (!(SEGMENT_SET_UPDATE_COLUMNS as readonly string[]).includes(col)) {
      throw new Error(`updateSegmentSet: column '${col}' is not updatable`);
    }
    if (col === "metadata") {
      setClauses.push(`metadata = $${values.length + 1}::jsonb`);
      values.push(val === null ? null : JSON.stringify(val));
    } else {
      setClauses.push(`${col} = $${values.length + 1}`);
      values.push(val);
    }
  }
  // valid_from stays — it records first-valid time; updated_at tracks edits.
  setClauses.push("updated_at = now()");
  return { setClauses, values };
}

/**
 * Update in place — segment_sets has FK references so we cannot expire+INSERT
 * (PK collision). valid_from is bumped to now() to record when the current
 * version was established.
 */
export async function updateSegmentSet(
  segmentSetId: string,
  fields: SegmentSetUpdateFields,
): Promise<SegmentSetRow | null> {
  if (Object.keys(fields).length === 0) {
    return getSegmentSet(segmentSetId);
  }
  const { setClauses, values } = buildUpdateSetClauses(fields);
  values.push(segmentSetId);
  return queryOne<SegmentSetRow>(
    `
    update nebula.segment_sets
    set ${setClauses.join(", ")}
    where id = $${values.length} and valid_until > now()
    returning ${SEGMENT_SET_COLUMNS}
  `,
    values,
  );
}

/**
 * Upsert members. If a current-valid row exists (partial-unique-index match),
 * update it in place; otherwise a new current row is inserted.
 */
export async function addMembers(
  segmentSetId: string,
  members: readonly SegmentMemberIn[],
): Promise<void> {
  if (members.length === 0) {
    return;
  }
  await withTransaction(async (conn) => {
    for (const m of members) {
      // ON CONFLICT targets the partial unique index:
      //   uq_segment_set_members_current
      //     ON (segment_set_id, segment_id) WHERE valid_until = '9999-12-31'
      await conn.query(
        `
        insert into nebula.segment_set_members
            (segment_set_id, segment_id, ordinal, note, included)
        values ($1, $2, $3, $4, true)
        on conflict (segment_set_id, segment_id)
            where valid_until = '${FOREVER}'::timestamptz
        do update set ordinal = excluded.ordinal,
                      note = excluded.note,
                      included = true
      `,
        [segmentSetId, m.segment_id, m.ordinal, m.note],
      );
    }
    await conn.query(
      "update nebula.segment_sets set updated_at = now() where id = $1",
      [segmentSetId],
    );
  });
  // Invalidate cache after commit so next GET rebuilds from current DB state.
  await invalidateSegsetBestEffort(segmentSetId);
}

/** Soft-exclude: toggle included=false and close the validity window. */
export async function excludeMember(
  segmentSetId: string,
  segmentId: string,
): Promise<void> {
  await withTransaction(async (conn) => {
    await conn.query(
      `
      update nebula.segment_set_members
      set included = false, valid_until = now()
      where segment_set_id = $1 and segment_id = $2 and valid_until > now()
    `,
      [segmentSetId, segmentId],
    );
    await conn.query(
      "update nebula.segment_sets set updated_at = now() where id = $1",
      [segmentSetId],
    );
  });
  // Invalidate cache after commit so next GET rebuilds from current DB state.
  await invalidateSegsetBestEffort(segmentSetId);
}

/**
 * The members of a segment set, joined to their current-valid transcript
 * segments. The join is a LEFT JOIN on purpose: a member whose segment has been
 * superseded still appears, with null transcript metadata, so the ordinal
 * sequence a caller sees never silently shifts.
 */
export async function listResolvedSegments(
  segmentSetId: string,
): Promise<ResolvedSegment[]> {
  const rows = await query<ResolvedSegment>(
    `
    select
        ssm.segment_id,
        ssm.ordinal,
        ssm.note,
        sh.conversation_id,
        sh.start_block_index,
        sh.end_block_index,
        sh.segment_type,
        sh.title
    from nebula.segment_set_members ssm
    left join nebula.segments_history sh
        on sh.id = ssm.segment_id
       and sh.expiration_dt = '${SEG_HISTORY_FOREVER}'::timestamptz
    where ssm.segment_set_id = $1
      and ssm.included = true
      and ssm.valid_until > now()
    order by ssm.ordinal
  `,
    [segmentSetId],
  );
  return rows.map((r) => ({
    segment_id: r.segment_id,
    ordinal: r.ordinal,
    note: r.note ?? null,
    conversation_id: r.conversation_id ?? null,
    start_block_index: r.start_block_index ?? null,
    end_block_index: r.end_block_index ?? null,
    segment_type: r.segment_type ?? null,
    title: r.title ?? null,
  }));
}

// ── Domain links ─────────────────────────────────────────────────────────────

/**
 * Link a domain object to a segment set. If a current-valid link already
 * exists the partial unique index catches it and we update in place.
 */
export async function linkDomain(
  domainType: string,
  domainId: string,
  segmentSetId: string,
  role: string,
): Promise<void> {
  const { table, fkColumn } = domainTable(domainType);
  await execute(
    `
    insert into ${table} (${fkColumn}, segment_set_id, role, active)
    values ($1, $2, $3, true)
    on conflict (${fkColumn}, segment_set_id)
        where valid_until = '${FOREVER}'::timestamptz
    do update set role = excluded.role, active = true
  `,
    [domainId, segmentSetId, role],
  );
}

/** Soft-unlink: toggle active=false and close the validity window. */
export async function unlinkDomain(
  domainType: string,
  domainId: string,
  segmentSetId: string,
): Promise<void> {
  const { table, fkColumn } = domainTable(domainType);
  await execute(
    `
    update ${table} set active = false, valid_until = now()
    where ${fkColumn} = $1 and segment_set_id = $2 and valid_until > now()
  `,
    [domainId, segmentSetId],
  );
}

export interface DomainLinkRow {
  segment_set_id: string;
  role: string;
  active: boolean;
}

export async function listDomainLinks(
  domainType: string,
  domainId: string,
): Promise<DomainLinkRow[]> {
  const { table, fkColumn } = domainTable(domainType);
  return query<DomainLinkRow>(
    `
    select segment_set_id, role, active
    from ${table}
    where ${fkColumn} = $1 and active = true and valid_until > now()
  `,
    [domainId],
  );
}

// ── Transcript ingest ────────────────────────────────────────────────────────

export interface SegmentFromArcsRow {
  start_block_id: string;
  end_block_id: string;
  start_block_index: number;
  end_block_index: number;
  segment_type: string | null;
  title: string | null;
  notes_md: string | null;
}

/**
 * Create a segment set + segments_history rows + members, atomically.
 *
 * `segments` are pre-computed discourse arcs (from discourse_segmenter). All
 * writes happen in one transaction; failure rolls everything back
 * (strict-atomic per transcript).
 */
export async function createSegmentSetFromSegments(
  name: string | null,
  description: string | null,
  metadata: Record<string, unknown>,
  conversationId: string,
  snapshotId: string,
  segments: readonly SegmentFromArcsRow[],
): Promise<SegmentSetRow | null> {
  const segSetId = await withTransaction(async (conn) => {
    const segSet = await conn.query<{ id: string }>(
      `
      insert into nebula.segment_sets (name, description, metadata)
      values ($1, $2, $3::jsonb)
      returning id
    `,
      [name, description, JSON.stringify(metadata ?? {})],
    );
    const created = segSet.rows[0]?.id;
    if (created === undefined) {
      throw new Error("createSegmentSetFromSegments: INSERT ... RETURNING produced no row");
    }

    for (const [ordinal, seg] of segments.entries()) {
      // segments_history row
      const inserted = await conn.query<{ id: string }>(
        `
        insert into nebula.segments_history
            (id, conversation_id, snapshot_id,
             start_block_id, end_block_id,
             start_block_index, end_block_index,
             segment_type, state, source,
             title, notes_md, created_by,
             as_of_dt, expiration_dt)
        values (gen_random_uuid(), $1, $2, $3, $4, $5, $6, $7,
                'ACTIVE', 'HARVEST', $8, $9, 'SYSTEM', now(), $10::timestamptz)
        returning id
      `,
        [
          conversationId,
          snapshotId,
          seg.start_block_id,
          seg.end_block_id,
          seg.start_block_index,
          seg.end_block_index,
          seg.segment_type ?? "discussion",
          seg.title,
          seg.notes_md,
          SEG_HISTORY_FOREVER,
        ],
      );
      const segId = inserted.rows[0]?.id;
      if (segId === undefined) {
        throw new Error("createSegmentSetFromSegments: segment INSERT produced no id");
      }
      // segment_set_members link
      await conn.query(
        `
        insert into nebula.segment_set_members
            (segment_set_id, segment_id, ordinal, included)
        values ($1, $2, $3, true)
      `,
        [created, segId, ordinal],
      );
    }
    await conn.query(
      "update nebula.segment_sets set updated_at = now() where id = $1",
      [created],
    );
    return created;
  });
  await invalidateSegsetBestEffort(segSetId);
  return getSegmentSet(segSetId);
}
