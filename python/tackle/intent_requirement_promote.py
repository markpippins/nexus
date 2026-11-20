#!/usr/bin/env python3
"""
intent_requirement_promote.py — Candidate → Requirement Promotion Gate

Takes promoted harvest candidates and promotes them to requirements in the
backlog, where they await human-in-the-loop triage.

V115 removed nebula.intent_records. This script now reads candidates
directly from harvest_candidates instead of intent_records.

Promotion criteria (all must pass):
  1. CPF >= threshold (default 0.7) OR candidate has code snippets
  2. No blocking open_questions for the candidate
  3. Candidate status is 'promoted' (not yet promoted to requirement)

Non-blocking: questions with blocking=false are ignored.

Usage:
    source /home/codex/dev/nexus/python/rover/.venv/bin/activate

    # Promote all eligible candidates
    python3 python/tackle/intent_requirement_promote.py

    # Promote a specific candidate
    python3 python/tackle/intent_requirement_promote.py --candidate <uuid>

    # Dry run
    python3 python/tackle/intent_requirement_promote.py --dry-run

    # Custom threshold
    python3 python/tackle/intent_requirement_promote.py --threshold 0.8
"""

import argparse
import json
import logging
import subprocess
import sys
import uuid as uuidlib
from datetime import datetime, timezone

log = logging.getLogger("ir_promote")

DOCKER_PSQL = ["docker", "exec", "-i", "pgvector_db", "psql", "-U", "pguser", "-d", "nexus", "-q"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)


def psql(
    sql: str,
    timeout: int = 30,
    variables: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run SQL through psql.

    `variables` are passed with psql's own `-v` and referenced as `:'name'`,
    which psql quotes as a literal. That keeps caller-supplied values out of the
    SQL text (used by the promotion INSERT), instead of interpolating them.
    """
    try:
        args = DOCKER_PSQL + ["-t", "-A"]
        for key, value in (variables or {}).items():
            args += ["-v", f"{key}={value}"]
        result = subprocess.run(
            args,
            input=sql, capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return 1, "(timeout)", ""


def fetch_promoted_candidates(limit: int = 100) -> list[dict]:
    """Fetch promoted candidates ready for requirement creation."""
    sql = f"""
        SELECT row_to_json(r)::text FROM (
            SELECT
                hc.id,
                hc.title,
                hc.intent_description,
                hc.compilation_readiness AS cpf,
                hc.code_snippets,
                hc.system_id,
                hc.subsystem_id,
                hc.feature_id,
                hc.tags AS candidate_tags,
                hc.status
            FROM nebula.harvest_candidates hc
            WHERE hc.status = 'promoted'
            ORDER BY hc.compilation_readiness DESC NULLS LAST
            LIMIT {limit}
        ) r;
    """
    rc, out, err = psql(sql)
    if rc != 0 or not out:
        return []

    candidates = []
    for line in out.splitlines():
        if not line:
            continue
        try:
            candidates.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return candidates


def fetch_single_candidate(candidate_id: str) -> dict | None:
    """Fetch a single promoted candidate."""
    sql = f"""
        SELECT row_to_json(r)::text FROM (
            SELECT
                hc.id, hc.title, hc.intent_description,
                hc.compilation_readiness AS cpf,
                hc.code_snippets,
                hc.system_id, hc.subsystem_id, hc.feature_id,
                hc.tags AS candidate_tags,
                hc.status
            FROM nebula.harvest_candidates hc
            WHERE hc.id = '{candidate_id}'
        ) r;
    """
    rc, out, err = psql(sql)
    if rc != 0 or not out:
        return None
    try:
        return json.loads(out.splitlines()[0])
    except (json.JSONDecodeError, IndexError):
        return None


def has_blocking_questions(candidate_id: str) -> tuple[bool, list[dict]]:
    """Check if candidate has blocking open_questions.

    Returns (is_blocked, questions).
    """
    if not candidate_id:
        return False, []

    sql = f"""
        SELECT row_to_json(r)::text FROM (
            SELECT id, title, category, status
            FROM nebula.open_questions
            WHERE candidate_id = '{candidate_id}'::uuid
              AND blocking = true
              AND status = 'OPEN'
        ) r;
    """
    rc, out, err = psql(sql)
    if rc != 0 or not out:
        return False, []

    questions = []
    for line in out.splitlines():
        if not line:
            continue
        try:
            questions.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    return len(questions) > 0, questions


def check_existing_requirement(candidate_id: str) -> str | None:
    """Check if a requirement already exists for this candidate."""
    if not candidate_id:
        return None

    sql = f"""
        SELECT id FROM nebula.requirements
        WHERE candidate_id = '{candidate_id}'::uuid
        LIMIT 1;
    """
    rc, out, err = psql(sql)
    if rc == 0 and out:
        return out.strip()
    return None


def create_requirement(candidate: dict, dry_run: bool = False) -> str | None:
    """Create a requirement in backlog status from a candidate.

    Returns the requirement UUID or None on failure.
    """
    req_id = str(uuidlib.uuid4())
    title = candidate["title"]
    description = candidate.get("intent_description") or ""
    candidate_id = candidate["id"]
    system_id = candidate.get("system_id")
    subsystem_id = candidate.get("subsystem_id")
    feature_id = candidate.get("feature_id")

    # Build metadata from CPF
    cpf = candidate.get("cpf")
    acceptance_criteria = json.dumps({"cpf": cpf, "source": "candidate_promotion"}) if cpf else None

    # Tags from candidate
    tags = candidate.get("candidate_tags") or []
    all_tags = list(set(tags + ["promoted-from-candidate"]))

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # Values go in as psql variables, not as SQL text: the statement is static so
    # a candidate title or description cannot change its shape.
    sql = """
        INSERT INTO nebula.requirements
            (id, title, description, status, priority, req_type,
             system_id, subsystem_id, feature_id, candidate_id,
             acceptance_criteria, created_at, valid_from, valid_until)
        VALUES (
            :'id'::uuid,
            :'title',
            :'description',
            'Backlog',
            'Medium',
            'Story',
            NULLIF(:'system_id', '')::uuid,
            NULLIF(:'subsystem_id', '')::uuid,
            NULLIF(:'feature_id', '')::uuid,
            NULLIF(:'candidate_id', '')::uuid,
            NULLIF(:'acceptance_criteria', '')::jsonb,
            :'now',
            :'now',
            '9999-12-31T00:00:00Z'
        )
        RETURNING id;
    """
    variables = {
        "id": req_id,
        "title": candidate["title"],
        "description": description,
        "system_id": system_id or "",
        "subsystem_id": subsystem_id or "",
        "feature_id": feature_id or "",
        "candidate_id": candidate_id or "",
        "acceptance_criteria": acceptance_criteria or "",
        "now": now,
    }

    if dry_run:
        log.info("  [DRY RUN] Would create requirement: %s", title[:60])
        return req_id

    rc, out, err = psql(sql, variables=variables)
    if rc != 0 or not out:
        details = err[:200] if err else out[:200]
        log.error("  Failed to create requirement: %s", details)
        return None

    return req_id


def update_candidate_status(candidate_id: str, new_status: str, dry_run: bool = False) -> bool:
    """Update candidate status after promotion to requirement."""
    if dry_run:
        log.info("  [DRY RUN] Would update candidate %s -> %s", candidate_id[:8], new_status)
        return True

    sql = f"""
        UPDATE nebula.harvest_candidates
        SET status = '{new_status}',
            updated_at = now()
        WHERE id = '{candidate_id}'::uuid;
    """
    rc, out, err = psql(sql)
    if rc != 0:
        log.warning("  Could not update candidate status: %s", (err or out)[:100])
        return False
    return True


def promote_candidate(candidate: dict, threshold: float, dry_run: bool = False) -> dict:
    """Evaluate and promote a single candidate to requirement.

    Returns a result dict with outcome and reasoning.
    """
    cid = candidate["id"]
    title = candidate.get("title", "?")
    cpf = candidate.get("cpf")
    code_snippets = candidate.get("code_snippets") or []
    has_code = len(code_snippets) > 0

    result = {
        "candidate_id": cid,
        "title": title,
        "requirement_id": None,
        "success": False,
        "reason": None,
        "cpf": cpf,
        "has_code": has_code,
    }

    # Gate 1: CPF threshold OR has code
    meets_cpf = cpf is not None and cpf >= threshold
    if not meets_cpf and not has_code:
        result["reason"] = f"CPF={cpf} < {threshold} and no code snippets"
        log.info("  %s -- %s", title[:50], result["reason"])
        return result

    # Gate 2: No blocking questions
    blocked, questions = has_blocking_questions(cid)
    if blocked:
        q_summary = "; ".join(q["title"][:40] for q in questions[:3])
        result["reason"] = f"Blocked by {len(questions)} open question(s): {q_summary}"
        log.info("  %s -- %s", title[:50], result["reason"])
        return result

    # Gate 3: No existing requirement for this candidate
    existing = check_existing_requirement(cid)
    if existing:
        result["reason"] = f"Requirement {existing[:8]} already exists for this candidate"
        log.info("  %s -- %s", title[:50], result["reason"])
        return result

    # All gates passed -- promote
    signal = f"CPF={cpf}" if meets_cpf else f"code={len(code_snippets)} snippets"
    log.info("  Promoting (%s): %s", signal, title[:55])

    req_id = create_requirement(candidate, dry_run)
    if not req_id:
        result["reason"] = "Requirement creation failed"
        return result

    result["requirement_id"] = req_id
    result["success"] = True

    update_candidate_status(cid, "decomposed", dry_run)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Candidate -> Requirement promotion gate"
    )
    parser.add_argument("--candidate", type=str, default=None,
                        help="Promote a specific candidate UUID")
    parser.add_argument("--threshold", type=float, default=0.7,
                        help="CPF threshold (default 0.7)")
    parser.add_argument("--limit", type=int, default=50,
                        help="Max candidates to process")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without DB writes")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("Candidate -> Requirement Promotion Gate")
    log.info("Time: %s", datetime.now().isoformat())
    log.info("Mode: %s", "DRY RUN" if args.dry_run else "LIVE")
    log.info("Threshold: CPF >= %.2f OR has code snippets", args.threshold)
    log.info("=" * 60)

    # Fetch candidates
    if args.candidate:
        candidate = fetch_single_candidate(args.candidate)
        if not candidate:
            log.error("Candidate not found: %s", args.candidate)
            return 1
        candidates = [candidate]
    else:
        candidates = fetch_promoted_candidates(args.limit)
        log.info("Promoted candidates: %d", len(candidates))

    if not candidates:
        log.info("Nothing to promote.")
        return 0

    stats = {"promoted": 0, "skipped_cpf": 0, "skipped_blocked": 0,
             "skipped_existing": 0, "failed": 0}

    for candidate in candidates:
        log.info("-" * 50)
        log.info("Processing: %s", candidate.get("title", "?")[:60])

        result = promote_candidate(candidate, args.threshold, args.dry_run)

        if result["success"]:
            stats["promoted"] += 1
        elif result["reason"] and "CPF=" in result["reason"] and "code" in result["reason"]:
            stats["skipped_cpf"] += 1
        elif result["reason"] and "question" in result["reason"].lower():
            stats["skipped_blocked"] += 1
        elif result["reason"] and "already exists" in result["reason"].lower():
            stats["skipped_existing"] += 1
        else:
            stats["failed"] += 1

    # Summary
    log.info("=" * 60)
    log.info("Promotion Summary:")
    log.info("  Promoted:        %d", stats["promoted"])
    log.info("  Skipped (CPF):   %d", stats["skipped_cpf"])
    log.info("  Skipped (blocked): %d", stats["skipped_blocked"])
    log.info("  Skipped (exists):  %d", stats["skipped_existing"])
    log.info("  Failed:          %d", stats["failed"])
    log.info("=" * 60)

    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
