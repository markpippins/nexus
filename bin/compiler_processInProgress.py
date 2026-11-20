#!/usr/bin/env python3
"""
compiler_processInProgress.py — Compiler Cron: InProgress → Active

Processes InProgress requirements that have an implementation plan
and no blocking open questions:

  1. Checks no blocking questions remain
  2. Runs req_compiler.py (two-stage compilation)
  3. Attaches the compiled WorkRequest DCO to the requirements surface
  4. Moves requirement to Active

Usage:
    source /home/codex/dev/nexus/python/rover/.venv/bin/activate

    # Process all InProgress requirements
    python3 bin/compiler_processInProgress.py

    # Process a specific requirement
    python3 bin/compiler_processInProgress.py --requirement <uuid>

    # Dry run
    python3 bin/compiler_processInProgress.py --dry-run
"""

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

LOG_DIR = Path("/home/codex/dev/nexus/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
from datetime import datetime, timezone

log = logging.getLogger("compiler")

DOCKER_PSQL = ["docker", "exec", "-i", "pgvector_db", "psql", "-U", "pguser", "-d", "nexus", "-q"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(LOG_DIR / "compiler_processInProgress.log"),
    ],
)


def psql(sql: str, timeout: int = 30) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            DOCKER_PSQL + ["-t", "-A"],
            input=sql, capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return 1, "(timeout)", ""


def psql_json(sql: str, timeout: int = 30) -> list[dict]:
    rc, out, err = psql(sql, timeout)
    if rc != 0 or not out:
        return []
    results = []
    for line in out.splitlines():
        if line.strip():
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return results


# ── Fetch ──────────────────────────────────────────────────────────────

def fetch_inprogress_requirements(limit: int = 50) -> list[dict]:
    """Fetch InProgress requirements with plans and no blocking questions."""
    sql = f"""
        SELECT row_to_json(r)::text FROM (
            SELECT
                req.id, req.title, req.description, req.status,
                req.priority, req.req_type, req.parent_id,
                req.system_id, req.subsystem_id, req.feature_id,
                (SELECT count(*) FROM nebula.open_questions oq
                 WHERE oq.requirement_id = req.id
                 AND oq.blocking = true AND oq.status = 'OPEN') as blocking_questions,
                (SELECT count(*) FROM nebula.implementation_plans ip
                 WHERE ip.requirement_id = req.id) as plan_count,
                (req.work_request_dco IS NOT NULL) as has_dco
            FROM nebula.requirements req
            WHERE req.status = 'InProgress'
            ORDER BY req.created_at ASC
            LIMIT {limit}
        ) r;
    """
    return psql_json(sql)


def fetch_requirement(req_id: str) -> dict | None:
    sql = f"""
        SELECT row_to_json(r)::text FROM (
            SELECT
                id, title, description, status, priority, req_type,
                parent_id, system_id, subsystem_id, feature_id
            FROM nebula.requirements
            WHERE id = '{req_id}'
        ) r;
    """
    rows = psql_json(sql)
    return rows[0] if rows else None


def count_blocking_questions(req_id: str) -> int:
    """Count blocking open questions (direct + children)."""
    sql = f"""
        WITH RECURSIVE descendants AS (
            SELECT id FROM nebula.requirements WHERE parent_id = '{req_id}'
            UNION ALL
            SELECT r.id FROM nebula.requirements r
            JOIN descendants d ON r.parent_id = d.id
        )
        SELECT count(*) FROM nebula.open_questions oq
        WHERE (
            oq.requirement_id = '{req_id}'
            OR oq.requirement_id IN (SELECT id FROM descendants)
        )
        AND oq.blocking = true AND oq.status = 'OPEN';
    """
    rc, out, _ = psql(sql)
    return int(out) if rc == 0 and out else 0


def has_compiled_wr(req_id: str) -> bool:
    """Check if a WorkRequest DCO already exists for this requirement."""
    sql = f"""
        SELECT count(*) FROM nebula.requirements
        WHERE id = '{req_id}'::uuid
        AND work_request_dco IS NOT NULL
        AND valid_until > now();
    """
    rc, out, _ = psql(sql)
    return int(out) > 0 if rc == 0 and out else False


def has_plan(req_id: str) -> bool:
    """Check if requirement has at least one implementation plan."""
    sql = f"""
        SELECT count(*) FROM nebula.implementation_plans
        WHERE requirement_id = '{req_id}'::uuid;
    """
    rc, out, _ = psql(sql)
    return int(out) > 0 if rc == 0 and out else False


# ── Status Update ──────────────────────────────────────────────────────

def update_status(req_id: str, new_status: str, dry_run: bool = False) -> bool:
    if dry_run:
        log.info("  [DRY RUN] Would move to %s", new_status)
        return True
    sql = f"""
        UPDATE nebula.requirements
        SET status = '{new_status}'
        WHERE id = '{req_id}'::uuid
        AND valid_until > now();
    """
    rc, out, err = psql(sql)
    if rc != 0:
        log.warning("  Could not update status: %s", (err or out)[:100])
        return False
    return True


# ── Compile via req_compiler ───────────────────────────────────────────

def compile_requirement(req_id: str, dry_run: bool = False) -> dict:
    """Invoke req_compiler.py on a requirement. Returns compiled IR.

    Uses --dry-run to get the compiled IR without submitting to conduit.
    The DCO writing and status update are handled by the caller.
    """
    cmd = ["python3", "req_compiler.py",
           "--requirement", req_id,
           "--dry-run",
           "--json"]
    script = subprocess.run(
        cmd,
        capture_output=True, text=True, timeout=120,
        cwd="/home/codex/dev/nexus/python/tackle",
    )

    if script.returncode != 0:
        log.error("  req_compiler failed: %s", script.stderr[:200])
        return {"success": False, "error": script.stderr[:200]}

    # Parse stdout JSON (--json flag makes req_compiler output pretty JSON)
    stdout = script.stdout.strip()
    if stdout:
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            # Try line-by-line fallback
            for line in reversed(stdout.splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        return json.loads(line)
                    except json.JSONDecodeError:
                        continue

    return {"success": False, "error": "No JSON in compiler output"}


# ── Write WorkRequest DCO ──────────────────────────────────────────────

def write_work_request_dco(req_id: str, compiled_ir: dict,
                            dry_run: bool = False) -> bool:
    """Persist the compiled WorkRequest DCO through the requirements surface."""
    if dry_run:
        log.info("  [DRY RUN] Would write WorkRequest DCO")
        return True

    ir_json = json.dumps(compiled_ir).replace("'", "''")

    sql = f"""
        UPDATE nebula.requirements
        SET work_request_dco = '{ir_json}'::jsonb
        WHERE id = '{req_id}'::uuid
        AND valid_until > now();
    """
    rc, out, err = psql(sql)
    if rc != 0:
        log.error("  Failed to write DCO: %s", (err or out)[:100])
        return False

    log.info("  DCO written to requirements.work_request_dco")
    return True


# ── Process One Requirement ────────────────────────────────────────────

def process_requirement(req: dict, dry_run: bool = False) -> dict:
    """Process a single InProgress requirement through the Compiler pipeline."""
    req_id = req["id"]
    title = req.get("title", "?")

    result = {
        "requirement_id": req_id,
        "title": title,
        "action": None,
        "success": False,
    }

    log.info("─" * 50)
    log.info("Compiling: %s", title[:55])

    # Gate 1: no blocking questions
    blocking = count_blocking_questions(req_id)
    if blocking > 0:
        result["action"] = f"skipped — {blocking} blocking question(s)"
        log.info("  ⊘ %s", result["action"])
        return result

    # Gate 2: must have a plan
    if not has_plan(req_id):
        result["action"] = "skipped — no implementation plan"
        log.info("  ⊘ %s", result["action"])
        return result

    # Skip if already compiled
    if has_compiled_wr(req_id):
        result["action"] = "skipped — WorkRequest DCO already exists"
        log.info("  ⊘ %s", result["action"])
        return result

    # Run req_compiler
    log.info("  Running req_compiler...")
    compiled = compile_requirement(req_id, dry_run)

    if not compiled.get("success") and not dry_run:
        result["action"] = f"compilation failed: {compiled.get('error', 'unknown')[:80]}"
        log.error("  ✗ %s", result["action"])
        return result

    # Write DCO
    compiled_ir = compiled.get("compiled_ir", compiled)
    dco_ok = write_work_request_dco(req_id, compiled_ir, dry_run)

    # Move to Active
    update_status(req_id, "Active", dry_run)

    wr_id = compiled_ir.get("wr_id", f"WR-REQ-{req_id[:8].upper()}")
    result["action"] = f"Active (wr={wr_id})"
    result["success"] = True
    log.info("  ✓ %s", result["action"])
    return result


# ── Main ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Compiler cron: InProgress → Active"
    )
    parser.add_argument("--requirement", type=str, default=None,
                        help="Process a specific requirement UUID")
    parser.add_argument("--limit", type=int, default=50,
                        help="Max requirements to process")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without DB writes")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("Compiler Cron: InProgress → Active")
    log.info("Time: %s", datetime.now().isoformat())
    log.info("Mode: %s", "DRY RUN" if args.dry_run else "LIVE")
    log.info("=" * 60)

    if args.requirement:
        req = fetch_requirement(args.requirement)
        if not req:
            log.error("Requirement not found: %s", args.requirement)
            return 1
        reqs = [req]
    else:
        reqs = fetch_inprogress_requirements(args.limit)
        log.info("InProgress requirements: %d", len(reqs))

    if not reqs:
        log.info("Nothing to compile.")
        return 0

    stats = {"compiled": 0, "skipped": 0, "failed": 0}

    for req in reqs:
        result = process_requirement(req, args.dry_run)
        if result["success"]:
            stats["compiled"] += 1
        elif "skipped" in (result.get("action") or ""):
            stats["skipped"] += 1
        else:
            stats["failed"] += 1

    log.info("=" * 60)
    log.info("Compiler Summary:")
    log.info("  Compiled → Active: %d", stats["compiled"])
    log.info("  Skipped:           %d", stats["skipped"])
    log.info("  Failed:            %d", stats["failed"])
    log.info("=" * 60)

    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
