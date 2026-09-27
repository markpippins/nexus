import datetime
import uuid
from typing import Optional

import asyncpg

from .cache import invalidate_segset
from .db import get_pool

# Sentinel for "infinitely valid" — MUST match the NOT NULL DEFAULT on every table
# and the WHERE clause of every partial unique index.  If you change the DB
# default, update this constant AND every ON CONFLICT WHERE clause below.
# Postgres will reject the query if the ON CONFLICT predicate doesn't match
# the index, so mismatches fail loudly rather than silently.
_FOREVER = "9999-12-31 00:00:00+00"

# maps the public "domain_type" path segment to (join_table, fk_column)
# NOTE: "intent-records" was removed — intent records were eliminated as a
# domain concept (nebula.intent_records no longer exists); its join table is
# dropped by 002_drop_intent_record_segment_sets.sql.
_DOMAIN_TABLES: dict[str, tuple[str, str]] = {
    "candidates": ("nebula.candidate_segment_sets", "candidate_id"),
    "requirements": ("nebula.requirement_segment_sets", "requirement_id"),
}


def domain_table(domain_type: str) -> tuple[str, str]:
    try:
        return _DOMAIN_TABLES[domain_type]
    except KeyError:
        raise ValueError(f"unknown domain_type '{domain_type}'")


async def create_segment_set(
    name: Optional[str], description: Optional[str], metadata: dict
) -> asyncpg.Record:
    pool = get_pool()
    return await pool.fetchrow(
        """
        insert into nebula.segment_sets (name, description, metadata)
        values ($1, $2, $3::jsonb)
        returning id, name, description, status, metadata, created_at, updated_at
        """,
        name,
        description,
        metadata,
    )


async def list_segment_sets(limit: int = 200, offset: int = 0) -> list[asyncpg.Record]:
    """List all current-valid segment sets."""
    pool = get_pool()
    return await pool.fetch(
        """
        select id, name, description, status, metadata, created_at, updated_at
        from nebula.segment_sets
        where valid_until > now()
        order by created_at desc
        limit $1 offset $2
        """,
        limit,
        offset,
    )


async def get_segment_set(segment_set_id: uuid.UUID) -> Optional[asyncpg.Record]:
    pool = get_pool()
    return await pool.fetchrow(
        """
        select id, name, description, status, metadata, created_at, updated_at
        from nebula.segment_sets
        where id = $1 and valid_until > now()
        """,
        segment_set_id,
    )


async def update_segment_set(
    segment_set_id: uuid.UUID, fields: dict
) -> Optional[asyncpg.Record]:
    """Update in place — segment_sets has FK references so we cannot
    expire+INSERT (PK collision).  valid_from is bumped to now() to
    record when the current version was established."""
    if not fields:
        return await get_segment_set(segment_set_id)
    pool = get_pool()
    set_clauses: list[str] = []  # valid_from stays — it records first-valid time, updated_at tracks edits
    values: list = []
    for i, (col, val) in enumerate(fields.items(), start=1):
        if col == "metadata":
            set_clauses.append(f"metadata = ${i}::jsonb")
        else:
            set_clauses.append(f"{col} = ${i}")
        values.append(val)
    set_clauses.append("updated_at = now()")
    values.append(segment_set_id)
    query = f"""
        update nebula.segment_sets
        set {', '.join(set_clauses)}
        where id = ${len(values)} and valid_until > now()
        returning id, name, description, status, metadata, created_at, updated_at
    """
    return await pool.fetchrow(query, *values)


async def add_members(segment_set_id: uuid.UUID, members: list[dict]) -> None:
    """Upsert members.  If a current-valid row exists (partial-unique-index
    match), update it in place; otherwise a new current row is inserted."""
    if not members:
        return
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for m in members:
                # ON CONFLICT targets the partial unique index:
                #   uq_segment_set_members_current
                #     ON (segment_set_id, segment_id) WHERE valid_until = '9999-12-31'
                await conn.execute(
                    f"""
                    insert into nebula.segment_set_members
                        (segment_set_id, segment_id, ordinal, note, included)
                    values ($1, $2, $3, $4, true)
                    on conflict (segment_set_id, segment_id)
                        where valid_until = '{_FOREVER}'::timestamptz
                    do update set ordinal = excluded.ordinal,
                                  note = excluded.note,
                                  included = true
                    """,
                    segment_set_id,
                    m["segment_id"],
                    m["ordinal"],
                    m.get("note"),
                )
            await conn.execute(
                "update nebula.segment_sets set updated_at = now() where id = $1",
                segment_set_id,
            )
        # Invalidate cache after commit so next GET rebuilds from current DB state
        await invalidate_segset(segment_set_id)


async def exclude_member(segment_set_id: uuid.UUID, segment_id: uuid.UUID) -> None:
    """Soft-exclude: toggle included=false and close the validity window."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                update nebula.segment_set_members
                set included = false, valid_until = now()
                where segment_set_id = $1 and segment_id = $2 and valid_until > now()
                """,
                segment_set_id,
                segment_id,
            )
            await conn.execute(
                "update nebula.segment_sets set updated_at = now() where id = $1",
                segment_set_id,
            )
        # Invalidate cache after commit so next GET rebuilds from current DB state
        await invalidate_segset(segment_set_id)


async def list_resolved_segments(segment_set_id: uuid.UUID) -> list[asyncpg.Record]:
    pool = get_pool()
    return await pool.fetch(
        """
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
           and sh.expiration_dt = '9999-12-31 23:59:59+00'::timestamptz
        where ssm.segment_set_id = $1
          and ssm.included = true
          and ssm.valid_until > now()
        order by ssm.ordinal
        """,
        segment_set_id,
    )


async def link_domain(
    domain_type: str, domain_id: uuid.UUID, segment_set_id: uuid.UUID, role: str
) -> None:
    """Link a domain object to a segment set.  If a current-valid link already
    exists the partial unique index catches it and we update in place."""
    table, fk_col = domain_table(domain_type)
    pool = get_pool()
    await pool.execute(
        f"""
        insert into {table} ({fk_col}, segment_set_id, role, active)
        values ($1, $2, $3, true)
        on conflict ({fk_col}, segment_set_id)
            where valid_until = '{_FOREVER}'::timestamptz
        do update set role = excluded.role, active = true
        """,
        domain_id,
        segment_set_id,
        role,
    )


async def unlink_domain(
    domain_type: str, domain_id: uuid.UUID, segment_set_id: uuid.UUID
) -> None:
    """Soft-unlink: toggle active=false and close the validity window."""
    table, fk_col = domain_table(domain_type)
    pool = get_pool()
    await pool.execute(
        f"update {table} set active = false, valid_until = now() "
        f"where {fk_col} = $1 and segment_set_id = $2 and valid_until > now()",
        domain_id,
        segment_set_id,
    )


async def list_domain_links(
    domain_type: str, domain_id: uuid.UUID
) -> list[asyncpg.Record]:
    table, fk_col = domain_table(domain_type)
    pool = get_pool()
    return await pool.fetch(
        f"""
        select segment_set_id, role, active
        from {table}
        where {fk_col} = $1 and active = true and valid_until > now()
        """,
        domain_id,
    )


# ── Transcript-ingest support ────────────────────────────────────────

_SEG_HISTORY_FOREVER = datetime.datetime(9999, 12, 31, 23, 59, 59, tzinfo=datetime.timezone.utc)  # segments_history sentinel


async def create_segment_set_from_segments(
    name: Optional[str],
    description: Optional[str],
    metadata: dict,
    conversation_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    segments: list[dict],
) -> asyncpg.Record:
    """Create a segment set + segments_history rows + members, atomically.

    ``segments`` is a list of dicts with keys:
        start_block_id (uuid), end_block_id (uuid),
        start_block_index (int), end_block_index (int),
        segment_type (str), title (str|None), notes_md (str|None)

    Returns the segment_sets row.  All writes are in one transaction;
    failure rolls everything back (strict-atomic per transcript).
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # 1) segment_sets row
            seg_set = await conn.fetchrow(
                """
                insert into nebula.segment_sets (name, description, metadata)
                values ($1, $2, $3::jsonb)
                returning id
                """,
                name,
                description,
                metadata,
            )
            seg_set_id = seg_set["id"]

            for ordinal, seg in enumerate(segments):
                # 2) segments_history row
                seg_id = await conn.fetchval(
                    """
                    insert into nebula.segments_history
                        (id, conversation_id, snapshot_id,
                         start_block_id, end_block_id,
                         start_block_index, end_block_index,
                         segment_type, state, source,
                         title, notes_md, created_by,
                         as_of_dt, expiration_dt)
                    values (gen_random_uuid(), $1, $2, $3, $4, $5, $6, $7,
                            'ACTIVE', 'HARVEST', $8, $9, 'SYSTEM', now(), $10)
                    returning id
                    """,
                    conversation_id,
                    snapshot_id,
                    seg["start_block_id"],
                    seg["end_block_id"],
                    seg["start_block_index"],
                    seg["end_block_index"],
                    seg.get("segment_type", "discussion"),
                    seg.get("title"),
                    seg.get("notes_md"),
                    _SEG_HISTORY_FOREVER,
                )
                # 3) segment_set_members link
                await conn.execute(
                    """
                    insert into nebula.segment_set_members
                        (segment_set_id, segment_id, ordinal, included)
                    values ($1, $2, $3, true)
                    """,
                    seg_set_id,
                    seg_id,
                    ordinal,
                )
            await conn.execute(
                "update nebula.segment_sets set updated_at = now() where id = $1",
                seg_set_id,
            )
        await invalidate_segset(seg_set_id)
    return await get_segment_set(seg_set_id)
