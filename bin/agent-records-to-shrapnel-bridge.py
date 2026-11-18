#!/usr/bin/env python3
"""agent-records-to-shrapnel-bridge.py

Bridges new records from nebula.agent_records (nexus DB) into the shrapnel
EAV store (postgres DB, shrapnel schema).

Polls the nebula.agent_records view for records created after the last seen
cursor and encodes each as a shrapnel EAV object. Detects new record types
automatically (maintains the record_type_enum field registry) and upserts
shrapnel field metadata on demand.

This complements the one-shot bulk loader
(/tmp/opencode/load_agent_records_to_shrapnel.py): this bridge keeps shrapnel
current with every new agent record.

Usage:
    python3 agent-records-to-shrapnel-bridge.py [--interval 5] [--once]
"""

import argparse
import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

log = logging.getLogger("agent-records-to-shrapnel-bridge")

# ── Databases ──────────────────────────────────────────────────────────────
# Source: nexus.nebula.agent_records (view over agent_records_history)
#
# Connection details come from the environment so the test harness can point
# this at its own per-run database (tests/helpers/isolated-pg.js) — the defaults
# are the historic titanium values, so a bare invocation behaves as before.
# Before this, the database name was hardcoded to "nexus", which meant the
# bridge could only ever be tested against the deployment database.
NEXUS_DB = {
    "host": os.environ.get("PG_HOST", "localhost"),
    "port": int(os.environ.get("PG_PORT", "5432")),
    "database": os.environ.get("PG_DB_NAME", "nexus"),
    "user": os.environ.get("PG_USER", "pguser"),
    "password": os.environ.get("PG_PASSWORD", "pgpass"),
}
# Target: shrapnel EAV schema in the `postgres` database
SHRAPNEL_DB = {
    "host": os.environ.get("SHRAPNEL_PG_HOST", os.environ.get("PG_HOST", "localhost")),
    "port": int(os.environ.get("SHRAPNEL_PG_PORT", os.environ.get("PG_PORT", "5432"))),
    "database": os.environ.get("SHRAPNEL_PG_DB", "postgres"),
    "user": os.environ.get("SHRAPNEL_PG_USER", os.environ.get("PG_USER", "pguser")),
    "password": os.environ.get("SHRAPNEL_PG_PASSWORD", os.environ.get("PG_PASSWORD", "pgpass")),
}

# ── Field schema (property_name, field_type_code) ──────────────────────────
# type registry: 1 Long, 2 String, 3 Double, 4 Boolean, 5 Timestamp,
#                6 JSONB, 7 UUID
FIELDS = [
    ("record_id", 7),
    ("record_type", 2),
    ("record_type_enum", 1),
    ("role", 2),
    ("title", 2),
    ("content", 2),
    ("source_path", 2),
    ("metadata", 6),
    ("tags", 6),
    ("system_id", 7),
    ("subsystem_id", 7),
    ("feature_id", 7),
    ("plan_ref", 2),
    ("candidate_id", 7),
    ("requirement_id", 7),
    ("created_at", 5),
    ("level", 1),
    ("visibility_scope", 2),
    ("model", 2),
]

# Canonical record_type ordering (drives record_type_enum). Unknown/new
# record types are appended dynamically.
KNOWN_RECORD_TYPES = [
    "report", "engineering_log", "response", "architecture_note",
    "decision", "analysis", "assessment", "prompt", "inspection",
]

EXT = {1: "value_long", 2: "value_string", 3: "value_double",
       4: "value_boolean", 5: "value_timestamp", 6: "value_jsonb",
       7: "value_uuid"}


# ── Helpers ────────────────────────────────────────────────────────────────
def coerce(type_code, value):
    """Coerce a Python value for storage in the typed extension table."""
    if value is None:
        return None
    if type_code == 1:
        return int(value)
    if type_code == 2:
        return str(value)
    if type_code == 3:
        return float(value)
    if type_code == 4:
        return bool(value)
    if type_code == 5:
        return value  # datetime/timestamptz
    if type_code == 6:
        # JSONB — pass through JSON string (psycopg2 wraps)
        return json.dumps(value) if not isinstance(value, str) else value
    if type_code == 7:
        return str(value)
    return value


def get_field_ids(conn):
    """Return {property_name: field_id} for all known fields, upserting as needed."""
    field_ids = {}
    with conn.cursor() as cur:
        for idx, (prop, tcode) in enumerate(FIELDS, start=1):
            cur.execute(
                """
                INSERT INTO shrapnel.field
                  (is_calculated, field_index, label, name, property_name, field_type_code)
                VALUES (false, %s, %s, %s, %s, %s)
                ON CONFLICT (property_name) DO UPDATE SET
                  name = EXCLUDED.name, field_type_code = EXCLUDED.field_type_code
                RETURNING id
                """,
                (idx, prop, prop, prop, tcode),
            )
            field_ids[prop] = cur.fetchone()[0]
    conn.commit()
    return field_ids


def load_seen_record_ids(conn):
    """Return the set of record_ids already present in shrapnel (dedupe)."""
    seen = set()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT vu.value::text
            FROM shrapnel.object_attribute_value oav
            JOIN shrapnel.field f ON f.id = oav.field_id
            JOIN shrapnel.value v ON v.id = oav.value_id
            JOIN shrapnel.value_uuid vu ON vu.id = v.id
            WHERE f.property_name = 'record_id'
            """
        )
        for row in cur.fetchall():
            seen.add(row[0])
    return seen


def get_cursor(conn):
    """Return the last-record cursor (created_at) recorded in the cursor table."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT cursor_val FROM shrapnel.bridge_cursor
            WHERE tool = 'agent-records-to-shrapnel-bridge'
            """
        )
        row = cur.fetchone()
    if row and row[0]:
        return row[0]
    return "1970-01-01T00:00:00+00:00"


def set_cursor(conn, cursor_iso):
    """Record the last-processed cursor into the shrapnel cursor table."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO shrapnel.bridge_cursor (tool, cursor_val)
            VALUES ('agent-records-to-shrapnel-bridge', %s)
            ON CONFLICT (tool) DO UPDATE SET
              cursor_val = EXCLUDED.cursor_val, updated_at = now()
            """,
            (cursor_iso,),
        )
    conn.commit()


def fetch_new_records(src_conn, cursor_iso, limit=200):
    """Fetch agent records created after the cursor, oldest first."""
    with src_conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, record_type, role, title, content, source_path, metadata,
                   tags, system_id, subsystem_id, feature_id, plan_ref,
                   candidate_id, requirement_id, created_at, level,
                   visibility_scope, model
            FROM nebula.agent_records
            WHERE created_at > %s
            ORDER BY created_at ASC
            LIMIT %s
            """,
            (cursor_iso, limit),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def encode_record(shrapnel_conn, record, field_ids, record_type_enum):
    """Encode a single agent record as a shrapnel EAV object."""
    with shrapnel_conn.cursor() as cur:
        cur.execute("INSERT INTO shrapnel.object_instance DEFAULT VALUES RETURNING id")
        object_id = cur.fetchone()[0]

        values = {
            "record_id": str(record["id"]),
            "record_type": record["record_type"],
            "record_type_enum": record_type_enum.get(record["record_type"]),
        }
        for col in ("role", "title", "content", "source_path", "metadata",
                    "tags", "system_id", "subsystem_id", "feature_id",
                    "plan_ref", "candidate_id", "requirement_id",
                    "created_at", "level", "visibility_scope", "model"):
            values[col] = record.get(col)

        for prop, tcode in FIELDS:
            raw = values.get(prop)
            if raw is None:
                continue
            if isinstance(raw, list) and len(raw) == 0:
                continue
            if isinstance(raw, dict) and len(raw) == 0:
                continue
            try:
                val = coerce(tcode, raw)
            except Exception:
                continue
            if val is None:
                continue

            cur.execute(
                "INSERT INTO shrapnel.value (value_type_code) VALUES (%s) RETURNING id",
                (tcode,),
            )
            value_id = cur.fetchone()[0]
            ext = EXT[tcode]
            cur.execute(f"INSERT INTO shrapnel.{ext} (id, value) VALUES (%s, %s)",
                        (value_id, val))
            cur.execute(
                """
                INSERT INTO shrapnel.object_attribute_value (object_id, field_id, value_id)
                VALUES (%s, %s, %s)
                """,
                (object_id, field_ids[prop], value_id),
            )
    return object_id


def ensure_record_type_enum(shrapnel_conn, record_type):
    """Add a new record_type to the enum registry if it is unknown."""
    if record_type in KNOWN_RECORD_TYPES:
        return
    # A new type appeared: append it (never mutate the canonical order).
    KNOWN_RECORD_TYPES.append(record_type)
    log.info("Detected new record_type=%s (enum index %d)", record_type,
             len(KNOWN_RECORD_TYPES))


def main():
    parser = argparse.ArgumentParser(description="Agent-records → shrapnel bridge")
    parser.add_argument("--interval", type=int, default=5,
                        help="Poll interval in seconds")
    parser.add_argument("--once", action="store_true",
                        help="Run one pass and exit (for cron/manual)")
    args = parser.parse_args()

    LOG_DIR = Path("/home/codex/dev/nexus/logs")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(LOG_DIR / "agent-records-to-shrapnel-bridge.log"),
        ],
    )

    stop = False

    def handle_signal(sig, frame):
        nonlocal stop
        log.info("Received signal %s, shutting down...", sig)
        stop = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    log.info("Agent-records → shrapnel bridge started (interval=%ds)", args.interval)

    src = psycopg2.connect(**NEXUS_DB)
    dst = psycopg2.connect(**SHRAPNEL_DB)
    src.autocommit = True

    field_ids = get_field_ids(dst)
    seen = load_seen_record_ids(dst)
    cursor_iso = get_cursor(dst)
    log.info("Starting: %d records already in shrapnel, cursor=%s", len(seen), cursor_iso)

    while not stop:
        try:
            records = fetch_new_records(src, cursor_iso)
            if not records:
                # nothing new this pass
                pass
            else:
                new_count = 0
                last_created = cursor_iso
                for rec in records:
                    rid = str(rec["id"])
                    if rec["created_at"]:
                        last_created = rec["created_at"].isoformat()
                    if rid in seen:
                        continue
                    ensure_record_type_enum(dst, rec["record_type"])
                    encode_record(dst, rec, field_ids, _enum_map())
                    seen.add(rid)
                    new_count += 1
                dst.commit()
                # Always advance the cursor to the newest record examined, so
                # subsequent passes only scan records newer than the last one.
                if records and last_created != cursor_iso:
                    set_cursor(dst, last_created)
                    log.info("Encoded %d new record(s), cursor→%s (scanned %d)",
                             new_count, last_created, len(records))
            if args.once:
                break
        except Exception as e:
            log.error("Poll error: %s", e)
            try:
                dst.rollback()
            except Exception:
                pass
            # reconnect on error
            try:
                dst.close()
            except Exception:
                pass
            try:
                dst = psycopg2.connect(**SHRAPNEL_DB)
                field_ids = get_field_ids(dst)
                seen = load_seen_record_ids(dst)
            except Exception as e2:
                log.error("Reconnect failed: %s", e2)

        time.sleep(args.interval)

    src.close()
    dst.close()
    log.info("Agent-records → shrapnel bridge stopped")


def _enum_map():
    return {rt: i + 1 for i, rt in enumerate(KNOWN_RECORD_TYPES)}


if __name__ == "__main__":
    main()