"""
Migrate existing audit markdown files from nexus/audit/
into the nebula.agent_records table in PostgreSQL.

This is a one-time migration. After this, agents should write to the DB
directly via the nebula-mcp `nebula_create_agent_record` tool.
"""

import json
import os
import re
import sys
from datetime import date as datetime_date, datetime as datetime_dt
import yaml

try:
    import psycopg2
except ImportError:
    os.system("pip install psycopg2-binary pyyaml -q")
    import psycopg2
    import yaml

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "user": "pguser",
    "password": "pgpass",
    "database": "nexus",
    "options": "-c search_path=nebula",
}

AUDIT_ROOT = "/home/codex/dev/nexus/audit"

# Directories to skip (already migrated or not agent records)
SKIP_DIRS = {"ROVER", "IMPLEMENTATION_PLANS", ".obsidian", "chats", "STEWARD"}

# Map audit directory -> (record_type, role)
DIR_MAP = {
    "PROMPTS":        ("prompt",           "planner"),
    "RESPONSES":      ("response",         "planner"),
    "ANALYSIS":       ("analysis",         "analyst"),
    "ANALYSIS/reports":     ("report",     "analyst"),
    "ANALYSIS/specs":       ("analysis",   "analyst"),
    "ANALYSIS/reviewed":    ("analysis",   "analyst"),
    "ARCHITECTURE":         ("architecture_note", "architect"),
    "ARCHITECTURE/reports": ("report",     "architect"),
    "ARCHIVES":       ("analysis",         "architect"),
    "BUILDER":        ("engineering_log",  "builder"),
    "BUILDER/blockers":     ("engineering_log", "builder"),
    "CHANGES":        ("engineering_log",  "builder"),
    "CHANGES/committed":    ("engineering_log", "builder"),
    "CHANGES/flagged":      ("inspection",  "reviewer"),
    "CHANGES/reviewed":     ("assessment",  "reviewer"),
    "ENGINEERING":          ("engineering_log", "engineer"),
    "ENGINEERING/reports":  ("report",      "engineer"),
    "ENGINEERING/issues":   ("engineering_log", "engineer"),
    "ENGINEERING/blockers": ("engineering_log", "engineer"),
    "FINDINGS":       ("report",           "analyst"),
    "FINDINGS/resolutions": ("report",     "analyst"),
    "HISTORY":        ("report",           "architect"),
    "INSPECTIONS":          ("inspection",  "inspector"),
    "INSPECTIONS/errors":   ("inspection",  "inspector"),
    "INSPECTIONS/warnings": ("inspection",  "critic"),
    "INSPECTIONS/triage":   ("inspection",  "analyst"),
    "INSPECTIONS/processed": ("inspection", "inspector"),
    "INSPECTIONS/resolved":  ("inspection", "inspector"),
    "INSPECTIONS/unresolved":("inspection", "inspector"),
    "INSPECTIONS/todo":     ("inspection",  "inspector"),
    "INSPECTIONS/reports":  ("report",      "inspector"),
    "PLANS":          ("analysis",         "planner"),
    "PLANS/pending":  ("analysis",         "planner"),
    "PLANS/approved": ("analysis",         "planner"),
    "PLANS/rejected": ("analysis",         "planner"),
    "PLANS/candidate":("analysis",         "planner"),
    "PLANS/completed":("analysis",         "planner"),
    "REQUIREMENTS":   ("analysis",         "analyst"),
    "REVIEWS":        ("assessment",       "reviewer"),
    "SPECS":          ("architecture_note","architect"),
    "SPECS/implemented": ("architecture_note", "architect"),
}

# Additional subdirectories discovered during walk
SUBDIR_OVERRIDES: dict[str, tuple[str, str]] = {}


def find_audit_files(root: str) -> list[dict]:
    """Walk the audit directory and return metadata for all .md files."""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)

        # Skip the root itself (rel == '.') but process its children
        if rel != '.':
            parts = rel.split(os.sep)
            if parts[0] in SKIP_DIRS:
                dirnames.clear()
                continue
            if parts[0].startswith("."):
                dirnames.clear()
                continue

        for fname in filenames:
            if not fname.endswith(".md"):
                continue
            abs_path = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(abs_path, root)
            files.append({
                "abs_path": abs_path,
                "rel_path": rel_path,
                "dirname": rel,
                "filename": fname,
            })

    return files


def serialize_value(v):
    """Convert non-serializable values (dates, etc.) to strings."""
    if isinstance(v, (datetime_date, datetime_dt)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: serialize_value(v) for k, v in v.items()}
    if isinstance(v, list):
        return [serialize_value(i) for i in v]
    return v


def extract_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter and return (metadata, body)."""
    metadata = {}
    body = text

    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            front = text[3:end].strip()
            body = text[end + 3:].strip()
            try:
                metadata = yaml.safe_load(front) or {}
                # Serialize to ensure JSON compatibility
                metadata = serialize_value(metadata)
            except yaml.YAMLError:
                pass

    return metadata, body


def extract_title(text: str, filename: str) -> str:
    """Extract title from first heading or filename."""
    m = re.search(r'^#\s+(.+?)(?:\n|$)', text, re.MULTILINE)
    if m:
        return m.group(1).strip()
    # Fall back to filename without extension
    name = filename.replace(".md", "")
    # Convert kebab-case or underscores to spaces
    name = re.sub(r'[_-]', ' ', name)
    return name.strip().title()


def get_record_type_and_role(rel_path: str) -> tuple[str, str]:
    """Determine record_type and role from the relative path."""
    # Exact match first
    if rel_path in SUBDIR_OVERRIDES:
        return SUBDIR_OVERRIDES[rel_path]

    # Try parent directory
    parent = os.path.dirname(rel_path)
    if parent in DIR_MAP:
        return DIR_MAP[parent]

    # Try grandparent
    grandparent = os.path.dirname(parent)
    if grandparent in DIR_MAP:
        return DIR_MAP[grandparent]

    return ("report", "architect")


def migrate_files(dry_run: bool = False) -> int:
    """Migrate all audit files to the database."""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    # Get existing source paths to avoid duplicates
    cur.execute("SELECT source_path FROM nebula.agent_records WHERE source_path IS NOT NULL")
    existing = set(row[0] for row in cur.fetchall())
    print(f"Existing records in DB with source_path: {len(existing)}")

    files = find_audit_files(AUDIT_ROOT)
    print(f"Found {len(files)} audit markdown files")

    inserted = 0
    skipped = 0
    errors = 0

    for f in files:
        # Check if already migrated
        if f["rel_path"] in existing:
            skipped += 1
            continue

        try:
            with open(f["abs_path"], "r", encoding="utf-8") as fh:
                text = fh.read()

            metadata, body = extract_frontmatter(text)
            title = extract_title(text, f["filename"])
            record_type, role = get_record_type_and_role(f["rel_path"])

            # Add rel_path and file stats to metadata
            # Canonical owner-prefixed keys (metadata namespace ruling, item 6).
            metadata["migration.origin_path"] = f["rel_path"]
            metadata["migration.size_bytes"] = len(text.encode("utf-8"))
            metadata["migration.dirname"] = f["dirname"]

            if dry_run:
                print(f"  WOULD INSERT: [{record_type}/{role}] {title} ({f['rel_path']})")
                inserted += 1
                continue

            cur.execute(
                """INSERT INTO nebula.agent_records
                   (record_type, role, title, content, source_path, metadata, tags)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    record_type,
                    role,
                    title,
                    body,
                    f["rel_path"],
                    json.dumps(metadata),
                    [record_type, role, os.path.dirname(f["rel_path"]).split("/")[0]],
                )
            )
            conn.commit()
            inserted += 1

            if inserted % 50 == 0:
                print(f"  ... {inserted} migrated so far")

        except Exception as e:
            conn.rollback()
            print(f"  ERROR {f['rel_path']}: {e}")
            errors += 1

    cur.close()
    conn.close()
    return errors


def main():
    dry_run = "--dry-run" in sys.argv
    print(f"Audit migration (dry_run={dry_run})")
    errors = migrate_files(dry_run=dry_run)
    print(f"\nDone. Errors: {errors}")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
