#!/usr/bin/env python3
"""
architect_process_todo.py — Architect Cron: ToDo → InProgress

Processes requirements in ToDo status:
  1. Runs ripple assessment (nebula.assess_ripple)
  2. Simple (LOW risk): writes spec + implementation plan
  3. Complex (MEDIUM+ risk): decomposes into child Tasks, writes spec + plan per child
  4. Gate: moves to InProgress only when no blocking open questions

Usage:
    source /home/codex/dev/nexus/python/rover/.venv/bin/activate

    # Process all ToDo requirements
    python3 bin/architect_process_todo.py

    # Process a specific requirement
    python3 bin/architect_process_todo.py --requirement <uuid>

    # Dry run
    python3 bin/architect_process_todo.py --dry-run
"""

import argparse
import json
import logging
import subprocess
import sys
import os
from pathlib import Path

LOG_DIR = Path("/home/codex/dev/nexus/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Add parent directory to path so tackle.harness is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Add rover source dir so the deferred `from event_emitter import ...`
# calls inside this script resolve without PYTHONPATH (matches the pattern
# used by analyst_answer_questions.py and the other rover-bin scripts).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python", "rover"))

import uuid as uuidlib
from datetime import datetime, timezone

import psycopg2

from tackle.harness import ArchitectHarness
from tackle.harness.architect import build_requirement_context

log = logging.getLogger("architect")

DOCKER_PSQL = ["docker", "exec", "-i", "pgvector_db", "psql", "-U", "pguser", "-d", "nexus", "-q"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr),
        logging.FileHandler(LOG_DIR / "architect_process_todo.log"),
    ],
)


PG_DSN = "postgresql://pguser:pgpass@localhost:5432/nexus"


def get_pg_connection():
    """Create a psycopg2 connection for the harness with keepalive."""
    return psycopg2.connect(PG_DSN)


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

def fetch_todo_requirements(limit: int = 50) -> list[dict]:
    """Fetch requirements in ToDo status."""
    sql = f"""
        SELECT row_to_json(r)::text FROM (
            SELECT
                req.id, req.title, req.description, req.status,
                req.priority, req.req_type, req.parent_id,
                req.system_id, req.subsystem_id, req.feature_id,
                req.candidate_id, req.acceptance_criteria,
                (SELECT count(*) FROM nebula.open_questions oq
                 WHERE oq.requirement_id = req.id
                 AND oq.blocking = true AND oq.status = 'OPEN') as blocking_questions
            FROM nebula.requirements req
            WHERE req.status = 'ToDo'
            ORDER BY req.created_at ASC
            LIMIT {limit}
        ) r;
    """
    return psql_json(sql)


def fetch_requirement(req_id: str) -> dict | None:
    sql = f"""
        SELECT row_to_json(r)::text FROM (
            SELECT
                req.id, req.title, req.description, req.status,
                req.priority, req.req_type, req.parent_id,
                req.system_id, req.subsystem_id, req.feature_id,
                req.candidate_id, req.acceptance_criteria
            FROM nebula.requirements req
            WHERE req.id = '{req_id}'
        ) r;
    """
    rows = psql_json(sql)
    return rows[0] if rows else None


def get_children(req_id: str) -> list[dict]:
    """Get child requirements."""
    sql = f"""
        SELECT row_to_json(r)::text FROM (
            SELECT id, title, status, req_type
            FROM nebula.requirements
            WHERE parent_id = '{req_id}'
            ORDER BY created_at
        ) r;
    """
    return psql_json(sql)


def count_blocking_questions(req_id: str) -> int:
    """Count blocking open questions for a requirement (direct + inherited from children)."""
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


# ── Ripple Assessment ──────────────────────────────────────────────────

def assess_ripple(req_id: str) -> dict | None:
    """Run nebula.assess_ripple() on a requirement."""
    sql = f"SELECT nebula.assess_ripple('{req_id}'::uuid);"
    rc, out, _ = psql(sql)
    if rc != 0 or not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


# ── Write Spec ─────────────────────────────────────────────────────────

def write_spec(req_id: str, title: str, content: dict,
               work_request_id: str = None, dry_run: bool = False) -> str | None:
    """Write an architect spec to nebula.architect_specs."""
    spec_id = str(uuidlib.uuid4())
    escaped_title = title.replace("'", "''")
    content_json = json.dumps(content).replace("'", "''")
    wr_ref = f"'{work_request_id}'" if work_request_id else "NULL"

    sql = f"""
        INSERT INTO nebula.architect_specs
            (id, title, requirement_id, work_request_id, content, metadata)
        VALUES (
            '{spec_id}'::uuid,
            '{escaped_title}',
            '{req_id}'::uuid,
            {wr_ref}::uuid,
            '{content_json}'::jsonb,
            '{{"source": "architect_process_todo", "created_at": "{datetime.now(timezone.utc).isoformat()}"}}'::jsonb
        )
        RETURNING id;
    """

    if dry_run:
        log.info("    [DRY RUN] Would write spec: %s", title[:60])
        return spec_id

    rc, out, err = psql(sql)
    if rc != 0 or not out:
        log.error("    Failed to write spec: %s", (err or out)[:100])
        return None

    log.info("    Spec written: %s", spec_id[:8])
    return spec_id


# ── Write Implementation Plan ──────────────────────────────────────────

def write_implementation_plan(req_id: str, spec_id: str, title: str,
                               goal: str, acceptance_criteria: list[str],
                               files_affected: list[str] = None,
                               content: str = None,
                               dry_run: bool = False) -> str | None:
    """Write an implementation plan linked to requirement + spec.

    Targets canonical nebula.blueprints_history (V171) with the payload fold
    (same mapping as conduit's upsertPlan, PR #302): the implementation_plans
    view is read-only over the folded payload and the V171 mirror trigger
    retired in V176, so view-INSERTs fail (feature-not-supported) and legacy-
    table writes would silently vanish.
    """
    plan_id = str(uuidlib.uuid4())
    escaped_title = title.replace("'", "''")
    payload = json.dumps({
        "spec_ref": spec_id,
        "requirement_ref": req_id,
        "goal": goal,
        "content": content or "",
        "files_affected": files_affected or [],
        "acceptance_criteria": acceptance_criteria,
        "tags": ["architect-generated"],
        "source": "architect_process_todo",
    }).replace("'", "''")

    sql = f"""
        INSERT INTO nebula.blueprints_history
            (id, plan_number, title, payload, blueprint_status)
        VALUES (
            '{plan_id}'::uuid,
            NULL,
            '{escaped_title}',
            '{payload}'::jsonb,
            'pending'
        )
        RETURNING id;
    """

    if dry_run:
        log.info("    [DRY RUN] Would write plan: %s", title[:60])
        return plan_id

    rc, out, err = psql(sql)
    if rc != 0 or not out:
        log.error("    Failed to write plan: %s", (err or out)[:100])
        return None

    log.info("    Plan written: %s", plan_id[:8])
    return plan_id


# ── Update Status ──────────────────────────────────────────────────────

def update_status(req_id: str, new_status: str, dry_run: bool = False) -> bool:
    """Update requirement status in requirements_history."""
    if dry_run:
        log.info("  [DRY RUN] Would move to %s", new_status)
        return True

    sql = f"""
        UPDATE nebula.requirements_history
        SET valid_until = now()
        WHERE id = '{req_id}'::uuid
        AND valid_until > now();

        INSERT INTO nebula.requirements_history
        SELECT *, now(), '9999-12-31T23:59:59+00'
        FROM nebula.requirements_history
        WHERE id = '{req_id}'::uuid
        AND valid_until = now()
        ORDER BY valid_from DESC
        LIMIT 1;
    """
    # Simpler approach: just update the current row's status
    sql = f"""
        UPDATE nebula.requirements_history
        SET status = '{new_status}'
        WHERE id = '{req_id}'::uuid
        AND valid_until > now();
    """
    rc, out, err = psql(sql)
    if rc != 0:
        log.warning("  Could not update status: %s", (err or out)[:100])
        return False
    return True


# ── Decompose ──────────────────────────────────────────────────────────

def decompose_requirement(req: dict, dry_run: bool = False) -> list[dict]:
    """Decompose a complex requirement into child Tasks.

    Creates child requirements inheriting metadata from parent.
    Returns list of created child dicts.
    """
    parent_id = req["id"]
    parent_title = req.get("title", "")
    parent_desc = req.get("description") or ""
    parent_type = req.get("req_type") or "Story"

    # Children are Tasks if parent is Story, otherwise same type
    child_type = "Task" if parent_type == "Story" else parent_type

    # Generate decomposition from description
    # Split on paragraphs or logical sections
    sections = [s.strip() for s in parent_desc.split("\n\n") if s.strip()]

    # If description is too short for meaningful decomposition,
    # create a minimal set: one child for the core work
    if len(sections) <= 1:
        sections = [parent_desc] if parent_desc else [f"Implement: {parent_title}"]

    children = []
    for i, section in enumerate(sections[:10]):  # cap at 10 children
        child_title = f"{parent_title} — Part {i + 1}" if len(sections) > 1 else parent_title
        child_id = str(uuidlib.uuid4())

        sql = f"""
            INSERT INTO nebula.requirements_history
                (id, title, description, status, priority, req_type,
                 system_id, subsystem_id, feature_id, parent_id,
                 candidate_id, acceptance_criteria,
                 created_at, recorded_on_dt, valid_from, valid_until)
            VALUES (
                '{child_id}'::uuid,
                '{child_title.replace("'", "''")}',
                '{section.replace("'", "''")}',
                'ToDo',
                '{req.get("priority") or "Medium"}',
                '{child_type}',
                {f"'{req['system_id']}'" if req.get('system_id') else "NULL"}::uuid,
                {f"'{req['subsystem_id']}'" if req.get('subsystem_id') else "NULL"}::uuid,
                {f"'{req['feature_id']}'" if req.get('feature_id') else "NULL"}::uuid,
                '{parent_id}'::uuid,
                {f"'{req['candidate_id']}'" if req.get('candidate_id') else "NULL"}::uuid,
                '{json.dumps(req.get("acceptance_criteria") or [])}'::jsonb,
                now(), now(), now(), '9999-12-31T23:59:59+00'
            )
            RETURNING id;
        """

        if dry_run:
            log.info("    [DRY RUN] Would create child: %s", child_title[:60])
            children.append({"id": child_id, "title": child_title})
            continue

        rc, out, err = psql(sql)
        if rc == 0 and out:
            log.info("    Child created: %s — %s", child_id[:8], child_title[:50])
            children.append({"id": child_id, "title": child_title})
        else:
            log.error("    Failed to create child: %s", (err or out)[:100])

    return children


def _build_decomposition_prompt(req: dict, ripple: dict) -> str:
    """Build an LLM prompt asking it to decompose a complex requirement."""
    title = req.get("title", "")
    desc = req.get("description") or "No description"
    risk = ripple.get("risk_level", "UNKNOWN")
    blast = ripple.get("blast_radius", {})

    return f"""You are the Architect agent. Decompose this complex requirement into concrete child tasks.

## Requirement
- **Title**: {title}
- **Description**: {desc}
- **Risk Level**: {risk}
- **Blast Radius**: {blast.get('direct_children', 0)} direct children, depth {blast.get('max_depth', 0)}

## Instructions
Break this into 2-8 concrete, implementable child tasks. Each child should be:
- A single, focused unit of work
- Independently testable
- Small enough to implement in one session

## Output Format
Return a JSON array of objects:
```json
[
  {{
    "title": "Child task title",
    "description": "What this task does",
    "acceptance_criteria": ["Criterion 1", "Criterion 2"]
  }}
]
```

Return ONLY the JSON array, no other text."""


def _parse_decomposition(response: str, parent_req: dict) -> list[dict]:
    """Parse LLM decomposition response into child requirement dicts."""
    import re

    # Try to extract JSON array from response
    parsed = None

    # Try direct parse
    try:
        parsed = json.loads(response)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown fences
    if not parsed:
        match = re.search(r'```(?:json)?\s*\n(.*?)\n```', response, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

    # Try finding array in text
    if not parsed:
        bracket_start = response.find('[')
        bracket_end = response.rfind(']')
        if bracket_start >= 0 and bracket_end > bracket_start:
            try:
                parsed = json.loads(response[bracket_start:bracket_end + 1])
            except json.JSONDecodeError:
                pass

    if not parsed or not isinstance(parsed, list):
        log.warning("Could not parse decomposition response")
        return []

    # Create child requirement dicts (not yet inserted into DB)
    parent_type = parent_req.get("req_type") or "Story"
    child_type = "Task" if parent_type == "Story" else parent_type
    children = []

    for item in parsed[:10]:  # cap at 10
        child_id = str(uuidlib.uuid4())
        children.append({
            "id": child_id,
            "title": item.get("title", "Untitled"),
            "description": item.get("description", ""),
            "status": "ToDo",
            "priority": parent_req.get("priority") or "Medium",
            "req_type": child_type,
            "system_id": parent_req.get("system_id"),
            "subsystem_id": parent_req.get("subsystem_id"),
            "feature_id": parent_req.get("feature_id"),
            "parent_id": parent_req["id"],
            "candidate_id": parent_req.get("candidate_id"),
            "acceptance_criteria": item.get("acceptance_criteria", []),
        })

    return children


def _insert_children(children: list[dict], dry_run: bool = False) -> list[dict]:
    """Insert LLM-generated child requirements into the DB."""
    inserted = []
    for child in children:
        child_id = child["id"]
        title = child.get("title", "Untitled").replace("'", "''")
        desc = child.get("description", "").replace("'", "''")
        priority = child.get("priority") or "Medium"
        req_type = child.get("req_type") or "Task"
        ac_json = json.dumps(child.get("acceptance_criteria") or [])

        sys_id = f"'{child['system_id']}'" if child.get("system_id") else "NULL"
        sub_id = f"'{child['subsystem_id']}'" if child.get("subsystem_id") else "NULL"
        feat_id = f"'{child['feature_id']}'" if child.get("feature_id") else "NULL"
        parent_id = child.get("parent_id")
        cand_id = f"'{child['candidate_id']}'" if child.get("candidate_id") else "NULL"

        sql = f"""
            INSERT INTO nebula.requirements_history
                (id, title, description, status, priority, req_type,
                 system_id, subsystem_id, feature_id, parent_id,
                 candidate_id, acceptance_criteria,
                 created_at, recorded_on_dt, valid_from, valid_until)
            VALUES (
                '{child_id}'::uuid,
                '{title}',
                '{desc}',
                'ToDo',
                '{priority}',
                '{req_type}',
                {sys_id}::uuid,
                {sub_id}::uuid,
                {feat_id}::uuid,
                '{parent_id}'::uuid,
                {cand_id}::uuid,
                '{ac_json}'::jsonb,
                now(), now(), now(), '9999-12-31T23:59:59+00'
            )
            RETURNING id;
        """

        if dry_run:
            log.info("    [DRY RUN] Would create child: %s", title[:60])
            inserted.append(child)
            continue

        rc, out, err = psql(sql)
        if rc == 0 and out:
            log.info("    Child created: %s — %s", child_id[:8], title[:50])
            inserted.append(child)
        else:
            log.error("    Failed to create child: %s", (err or out)[:100])

    return inserted


# ── Process One Requirement ────────────────────────────────────────────

def process_requirement(req: dict, harness: ArchitectHarness, dry_run: bool = False) -> dict:
    """Process a single ToDo requirement through the Architect pipeline."""
    req_id = req["id"]
    title = req.get("title", "?")
    req_type = req.get("req_type") or "Story"

    result = {
        "requirement_id": req_id,
        "title": title,
        "action": None,
        "success": False,
    }

    log.info("─" * 50)
    log.info("Processing: %s (%s)", title[:55], req_type)

    # Gate: no blocking questions
    blocking = count_blocking_questions(req_id)
    if blocking > 0:
        result["action"] = f"skipped — {blocking} blocking question(s)"
        log.info("  ⊘ %s", result["action"])
        return result

    # Ripple assessment
    ripple = assess_ripple(req_id)
    if not ripple:
        result["action"] = "skipped — ripple assessment failed"
        log.warning("  ⊘ %s", result["action"])
        return result

    risk = ripple.get("risk_level", "UNKNOWN")
    blast = ripple.get("blast_radius", {})
    log.info("  Risk: %s | Children: %s | Depth: %s",
             risk, blast.get("direct_children", 0), blast.get("max_depth", 0))

    # Simple path (LOW risk)
    if risk == "LOW":
        return _process_simple(req, ripple, harness, dry_run)

    # Complex path (MEDIUM+ risk) — decompose
    return _process_complex(req, ripple, harness, dry_run)


def _process_simple(req: dict, ripple: dict, harness: ArchitectHarness, dry_run: bool) -> dict:
    """Simple requirement: LLM writes plan + questions, move to InProgress.

    Contract: only promote when the completion envelope proves inference succeeded.
    A failed model call, rate limit, or reboot during inference creates no
    completed evaluation — so the requirement stays ToDo and retries cleanly.
    """
    req_id = req["id"]
    title = req.get("title", "?")

    result = {"requirement_id": req_id, "title": title, "action": None, "success": False}

    # Emit: evaluation started
    from event_emitter import emit_event
    start_event_id = emit_event(
        event_type="evaluation.started",
        source="rover.architect_process_todo",
        aggregate_type="requirement",
        aggregate_id=req_id,
        payload={"title": title, "risk_level": ripple.get("risk_level", "UNKNOWN")},
        actor_type="agent",
    )

    # Build context for the harness
    ctx = build_requirement_context(harness._conn, req)
    full_context = {"requirements": [ctx]}

    if dry_run:
        prompt = harness.build_prompt(full_context)
        log.info("    [DRY RUN] Would invoke LLM (%d chars)", len(prompt))
        result["action"] = f"simple → dry-run (prompt={len(prompt)} chars)"
        result["success"] = True
        return result

    # Invoke LLM for real plan
    prompt = harness.build_prompt(full_context)
    response = harness.invoke_llm(prompt)

    if not response:
        # Emit: evaluation failed
        emit_event(
            event_type="evaluation.failed",
            source="rover.architect_process_todo",
            aggregate_type="requirement",
            aggregate_id=req_id,
            payload={"reason": "LLM invocation returned None", "start_event_id": start_event_id},
            actor_type="agent",
            causation_id=start_event_id,
            caused_by_event_type="evaluation.started",
        )
        result["action"] = "LLM invocation failed"
        log.warning("  ⊘ %s", result["action"])
        return result

    # Parse and persist plans + questions
    lr = harness.handle_response(response, full_context)

    # Gate: check completion envelope — only promote if inference succeeded
    plans_written = lr.get("plans_written", 0)
    envelope = lr.get("completion_envelope")

    if plans_written == 0 or not envelope:
        # Emit: evaluation failed (no completion evidence)
        emit_event(
            event_type="evaluation.failed",
            source="rover.architect_process_todo",
            aggregate_type="requirement",
            aggregate_id=req_id,
            payload={
                "reason": "No completion envelope",
                "plans_written": plans_written,
                "error": lr.get("error"),
                "start_event_id": start_event_id,
            },
            actor_type="agent",
            causation_id=start_event_id,
            caused_by_event_type="evaluation.started",
        )
        result["action"] = f"failed — no completion evidence (plans={plans_written})"
        log.warning("  ⊘ %s", result["action"])
        return result

    # Emit: evaluation completed
    emit_event(
        event_type="evaluation.completed",
        source="rover.architect_process_todo",
        aggregate_type="requirement",
        aggregate_id=req_id,
        payload={
            "plans_written": plans_written,
            "questions_created": lr.get("questions_created", 0),
            "envelope": envelope,
        },
        actor_type="agent",
        causation_id=start_event_id,
        caused_by_event_type="evaluation.started",
    )

    # Move to InProgress — we have completion evidence
    update_status(req_id, "InProgress", dry_run)

    questions = lr.get("questions_created", 0)
    result["action"] = f"simple → InProgress (plans={plans_written}, questions={questions})"
    result["success"] = True
    log.info("  ✓ %s", result["action"])
    return result


def _process_complex(req: dict, ripple: dict, harness: ArchitectHarness, dry_run: bool) -> dict:
    """Complex requirement: LLM decomposes + writes plans.

    Parent-child rollup: parent only advances when ALL children have
    completion envelopes (proof that inference succeeded for each child).
    """
    req_id = req["id"]
    title = req.get("title", "?")

    result = {"requirement_id": req_id, "title": title, "action": None, "success": False,
              "children": []}

    # Emit: decomposition started
    from event_emitter import emit_event
    start_event_id = emit_event(
        event_type="decomposition.started",
        source="rover.architect_process_todo",
        aggregate_type="requirement",
        aggregate_id=req_id,
        payload={"title": title, "risk_level": ripple.get("risk_level", "UNKNOWN")},
        actor_type="agent",
    )

    # Build context for decomposition prompt
    ctx = build_requirement_context(harness._conn, req)
    full_context = {"requirements": [ctx]}

    # Ask LLM to decompose this requirement into child tasks
    decomp_prompt = _build_decomposition_prompt(req, ripple)
    log.info("  Requesting LLM decomposition...")

    if dry_run:
        log.info("    [DRY RUN] Would invoke LLM for decomposition (%d chars)", len(decomp_prompt))
        result["action"] = "complex → dry-run"
        result["success"] = True
        return result

    decomp_response = harness.invoke_llm(decomp_prompt)
    if not decomp_response:
        emit_event(
            event_type="decomposition.failed",
            source="rover.architect_process_todo",
            aggregate_type="requirement",
            aggregate_id=req_id,
            payload={"reason": "LLM invocation returned None", "start_event_id": start_event_id},
            actor_type="agent",
            causation_id=start_event_id,
            caused_by_event_type="decomposition.started",
        )
        result["action"] = "decomposition LLM failed"
        log.warning("  ⊘ %s", result["action"])
        return result

    # Parse decomposition
    children = _parse_decomposition(decomp_response, req)
    if not children:
        emit_event(
            event_type="decomposition.failed",
            source="rover.architect_process_todo",
            aggregate_type="requirement",
            aggregate_id=req_id,
            payload={"reason": "No children parsed from LLM response", "start_event_id": start_event_id},
            actor_type="agent",
            causation_id=start_event_id,
            caused_by_event_type="decomposition.started",
        )
        result["action"] = "decomposition produced no children"
        log.warning("  ⊘ %s", result["action"])
        return result

    # Insert children into DB
    children = _insert_children(children, dry_run)
    if not children:
        result["action"] = "failed to insert children"
        log.warning("  ⊘ %s", result["action"])
        return result

    result["children"] = children

    emit_event(
        event_type="decomposition.completed",
        source="rover.architect_process_todo",
        aggregate_type="requirement",
        aggregate_id=req_id,
        payload={"child_count": len(children), "child_ids": [c["id"] for c in children]},
        actor_type="agent",
        causation_id=start_event_id,
        caused_by_event_type="decomposition.started",
    )

    # Write plan for each child using the harness — track completion envelopes
    all_plans = 0
    all_questions = 0
    children_with_envelopes = 0
    children_failed = 0

    for child in children:
        child_id = child["id"]
        child_title = child.get("title", "?")

        # Emit: child evaluation started
        child_start = emit_event(
            event_type="evaluation.started",
            source="rover.architect_process_todo",
            aggregate_type="requirement",
            aggregate_id=child_id,
            payload={"title": child_title, "parent_id": req_id},
            actor_type="agent",
        )

        child_ctx = build_requirement_context(harness._conn, child)
        child_full = {"requirements": [child_ctx]}

        prompt = harness.build_prompt(child_full)
        response = harness.invoke_llm(prompt)

        if not response:
            emit_event(
                event_type="evaluation.failed",
                source="rover.architect_process_todo",
                aggregate_type="requirement",
                aggregate_id=child_id,
                payload={"reason": "LLM invocation returned None", "parent_id": req_id, "start_event_id": child_start},
                actor_type="agent",
                causation_id=child_start,
                caused_by_event_type="evaluation.started",
            )
            children_failed += 1
            continue

        lr = harness.handle_response(response, child_full)
        plans = lr.get("plans_written", 0)
        questions = lr.get("questions_created", 0)
        envelope = lr.get("completion_envelope")

        all_plans += plans
        all_questions += questions

        if plans > 0 and envelope:
            children_with_envelopes += 1
            emit_event(
                event_type="evaluation.completed",
                source="rover.architect_process_todo",
                aggregate_type="requirement",
                aggregate_id=child_id,
                payload={
                    "plans_written": plans,
                    "questions_created": questions,
                    "envelope": envelope,
                    "parent_id": req_id,
                },
                actor_type="agent",
                causation_id=child_start,
                caused_by_event_type="evaluation.started",
            )
        else:
            children_failed += 1
            emit_event(
                event_type="evaluation.failed",
                source="rover.architect_process_todo",
                aggregate_type="requirement",
                aggregate_id=child_id,
                payload={
                    "reason": "No completion envelope",
                    "plans_written": plans,
                    "error": lr.get("error"),
                    "parent_id": req_id,
                    "start_event_id": child_start,
                },
                actor_type="agent",
                causation_id=child_start,
                caused_by_event_type="evaluation.started",
            )

    # Parent-child rollup: only advance when ALL children have completion envelopes
    if children_failed > 0:
        result["action"] = (
            f"complex → decomposed ({len(children)} children) but "
            f"{children_failed} child(ren) lack completion envelopes"
        )
        log.info("  ⊘ %s", result["action"])
        return result

    # Gate: check if all children have no blocking questions
    blocking = count_blocking_questions(req_id)
    if blocking > 0:
        result["action"] = (
            f"complex → decomposed ({len(children)} children) but "
            f"blocked by {blocking} question(s)"
        )
        log.info("  ⊘ %s", result["action"])
        return result

    # All children have completion envelopes + no blocking questions — advance
    update_status(req_id, "InProgress", dry_run)
    for child in children:
        update_status(child["id"], "InProgress", dry_run)

    result["action"] = (
        f"complex → InProgress ({len(children)} children, "
        f"{all_plans} plans, {all_questions} questions, "
        f"{children_with_envelopes}/{len(children)} envelopes)"
    )
    result["success"] = True
    log.info("  ✓ %s", result["action"])
    return result


# ── NEEDS_SPEC Escalations ─────────────────────────────────────────────

def fetch_needs_spec_questions(limit: int = 10) -> list[dict]:
    """Fetch OPEN questions with category=NEEDS_SPEC (Planner escalations)."""
    sql = f"""
        SELECT row_to_json(r)::text FROM (
            SELECT
                oq.id, oq.title, oq.description, oq.category,
                oq.requirement_id, oq.candidate_id, oq.created_by,
                req.title as req_title, req.description as req_description,
                req.status as req_status, req.priority as req_priority,
                req.acceptance_criteria
            FROM nebula.open_questions oq
            LEFT JOIN nebula.requirements req ON req.id = oq.requirement_id
            WHERE oq.status = 'OPEN'
              AND oq.category = 'NEEDS_SPEC'
            ORDER BY oq.blocking DESC, oq.created_at ASC
            LIMIT {limit}
        ) r;
    """
    return psql_json(sql)


def resolve_needs_spec_question(question_id: str, answer: str, dry_run: bool = False) -> bool:
    """Resolve a NEEDS_SPEC question after writing spec + plan."""
    if dry_run:
        log.info("  [DRY RUN] Would resolve NEEDS_SPEC question %s", question_id[:8])
        return True

    sql = f"""
        UPDATE nebula.open_questions
        SET status = 'RESOLVED',
            answered_by = 'architect',
            answered_at = now(),
            updated_at = now()
        WHERE id = '{question_id}'::uuid
          AND status = 'OPEN'
          AND category = 'NEEDS_SPEC'
        RETURNING id;
    """
    rc, out, err = psql(sql)
    if rc != 0 or not out:
        log.warning("  Could not resolve NEEDS_SPEC question: %s", (err or out)[:100])
        return False
    return True


def process_needs_spec(question: dict, harness: 'ArchitectHarness', dry_run: bool = False) -> dict:
    """Process a NEEDS_SPEC escalation: write spec + implementation plan."""
    q_id = question["id"]
    req_id = question.get("requirement_id")
    req_title = question.get("req_title", question.get("title", "Unknown"))
    req_description = question.get("req_description", question.get("description", ""))
    req_criteria = question.get("acceptance_criteria")

    result = {"success": False, "action": None}

    log.info("Processing NEEDS_SPEC: %s", question["title"][:60])

    if not req_id:
        result["action"] = "skipped — no linked requirement"
        log.info("  ⊘ %s", result["action"])
        return result

    # Build context for LLM
    context = {
        "requirement": {
            "id": req_id,
            "title": req_title,
            "description": req_description,
            "status": question.get("req_status", "unknown"),
            "priority": question.get("req_priority", "Medium"),
            "acceptance_criteria": req_criteria,
        },
        "escalation": {
            "question_id": q_id,
            "question_title": question["title"],
            "question_description": question.get("description", ""),
            "created_by": question.get("created_by", "planner"),
        },
    }

    # Build prompt
    prompt = harness.build_need_spec_prompt(context)

    # Invoke LLM
    log.info("  Invoking LLM for spec + plan...")
    response = harness.invoke(prompt)

    if not response:
        result["action"] = "failed — LLM returned no response"
        log.error("  ✗ %s", result["action"])
        return result

    # Parse response
    parsed = harness._extract_json(response)
    if not parsed:
        result["action"] = "failed — could not parse LLM response"
        log.error("  ✗ %s", result["action"])
        return result

    # Write spec
    spec_title = parsed.get("spec_title", f"Spec for {req_title[:50]}")
    spec_content = parsed.get("spec_content", "")
    spec_id = write_spec(req_id, spec_title, {"content": spec_content}, dry_run=dry_run)

    if not spec_id and not dry_run:
        result["action"] = "failed — could not write spec"
        log.error("  ✗ %s", result["action"])
        return result

    # Write implementation plan
    plan_title = parsed.get("plan_title", f"Plan for {req_title[:50]}")
    plan_goal = parsed.get("plan_goal", "")
    acceptance_criteria = parsed.get("acceptance_criteria", [])
    files_affected = parsed.get("files_affected", [])

    plan_id = write_implementation_plan(
        req_id, spec_id or "dry-run",
        plan_title, plan_goal, acceptance_criteria,
        files_affected, dry_run=dry_run,
    )

    if not plan_id and not dry_run:
        result["action"] = "failed — could not write plan"
        log.error("  ✗ %s", result["action"])
        return result

    # Resolve the NEEDS_SPEC question
    answer = f"Spec and plan written. Spec: {spec_id or 'dry-run'}, Plan: {plan_id or 'dry-run'}"
    resolve_needs_spec_question(q_id, answer, dry_run=dry_run)

    result["success"] = True
    result["action"] = (
        f"escalation resolved — spec: {spec_id[:8] if spec_id else 'dry-run'}, "
        f"plan: {plan_id[:8] if plan_id else 'dry-run'}"
    )
    log.info("  ✓ %s", result["action"])
    return result


# ── Main ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Architect cron: ToDo → InProgress + NEEDS_SPEC escalations"
    )
    parser.add_argument("--mode", type=str, default="both",
                        choices=["todo", "escalations", "both"],
                        help="Run mode: todo, escalations, or both (default: both)")
    parser.add_argument("--requirement", type=str, default=None,
                        help="Process a specific requirement UUID (todo mode only)")
    parser.add_argument("--limit", type=int, default=50,
                        help="Max requirements/questions to process")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without DB writes")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("Architect Cron: ToDo + Escalations (LLM-powered)")
    log.info("Time: %s", datetime.now().isoformat())
    log.info("Mode: %s (%s)", args.mode, "DRY RUN" if args.dry_run else "LIVE")
    log.info("=" * 60)

    # Connect harness for LLM calls
    pg_conn = get_pg_connection()
    harness = ArchitectHarness(pg_conn, dsn=PG_DSN)
    harness.load_model_info()

    if not harness.preferred_model:
        log.error("No model configured for architect — cannot run LLM")
        return 1

    log.info("Model: %s (%s)", harness.preferred_model.model_name,
             harness.preferred_model.model_identifier)

    stats = {"simple": 0, "complex": 0, "skipped": 0, "failed": 0, "escalations": 0}

    # Process ToDo requirements (if mode is todo or both)
    if args.mode in ("todo", "both"):
        if args.requirement:
            req = fetch_requirement(args.requirement)
            if not req:
                log.error("Requirement not found: %s", args.requirement)
                return 1
            reqs = [req]
        else:
            reqs = fetch_todo_requirements(args.limit)
            log.info("ToDo requirements: %d", len(reqs))

        for req in reqs:
            result = process_requirement(req, harness, args.dry_run)

            if result["success"]:
                if "complex" in (result.get("action") or ""):
                    stats["complex"] += 1
                else:
                    stats["simple"] += 1
            elif "skipped" in (result.get("action") or ""):
                stats["skipped"] += 1
            else:
                stats["failed"] += 1

    # Process NEEDS_SPEC escalations (if mode is escalations or both)
    if args.mode in ("escalations", "both"):
        escalation_limit = min(args.limit, 5)  # Cap escalations at 5
        escalations = fetch_needs_spec_questions(escalation_limit)
        log.info("NEEDS_SPEC escalations: %d", len(escalations))

        for question in escalations:
            result = process_needs_spec(question, harness, args.dry_run)
            if result["success"]:
                stats["escalations"] += 1
            else:
                stats["failed"] += 1

    log.info("=" * 60)
    log.info("Architect Summary:")
    log.info("  Simple → InProgress: %d", stats["simple"])
    log.info("  Complex → InProgress: %d", stats["complex"])
    log.info("  Escalations resolved: %d", stats["escalations"])
    log.info("  Skipped:             %d", stats["skipped"])
    log.info("  Failed:              %d", stats["failed"])
    log.info("=" * 60)

    pg_conn.close()
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
