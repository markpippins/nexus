#!/usr/bin/env python3
"""
req_compiler.py — Requirement → WorkRequest Compilation Pipeline

Two-stage compilation:

  Stage 1 (Semantic Normalization):
    Parse requirement → extract hierarchy context → normalize acceptance
    criteria → resolve cross-references. Produces a NormalizedIntent.

  Stage 2 (Engineering Compilation):
    Match intent to Op Mapping Registry → generate opcode sequence →
    resolve files_affected + dependencies → assign idempotency key.
    Produces a CompiledWorkRequest IR.

The compiled output feeds into Conduit plan creation (via conduit-mcp
create_plan) with cross-reference links back to the source Nebula
requirement.

Every compilation produces a journal entry in the audit substrate
(nebula.req_compilation_log) for traceability.

Usage:
    source /home/codex/dev/nexus/python/rover/.venv/bin/activate

    # Compile a single requirement
    python3 python/tackle/req_compiler.py --requirement <uuid>

    # Compile and create a conduit plan
    python3 python/tackle/req_compiler.py --requirement <uuid> --create-plan

    # Dry-run: show compiled IR without creating a plan
    python3 python/tackle/req_compiler.py --requirement <uuid> --dry-run

    # Stage 1 only (normalization without compilation)
    python3 python/tackle/req_compiler.py --requirement <uuid> --stage-1-only
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import uuid as uuidlib
from datetime import datetime
from typing import Any

# Ensure parent dir (python/) is on path so rover.* is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rover.event_emitter import emit_requirement_promoted_to_plan

log = logging.getLogger("req_compiler")

DOCKER_PSQL = ["docker", "exec", "-i", "pgvector_db", "psql", "-U", "pguser", "-d", "nexus"]
CONDUIT_MCP_URL = "http://localhost:3100/tools/call"
NEBULA_SRV_URL = "http://localhost:3101/api"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)


# ── DB helpers (follow rover conventions) ────────────────────────────────

def psql(sql: str, timeout: int = 30) -> tuple[int, str]:
    try:
        result = subprocess.run(
            DOCKER_PSQL + ["-t", "-A"],
            input=sql, capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return 1, "(timeout)"


def psql_json(sql: str, timeout: int = 30) -> list[dict] | None:
    """Execute SQL and return results as a list of dicts. Always returns a list (or None on error)."""
    rc, out = psql(sql, timeout)
    if rc != 0 or not out:
        return None
    try:
        results = []
        for line in out.splitlines():
            if line.strip():
                results.append(json.loads(line))
        return results
    except json.JSONDecodeError:
        return None


# ── Stage 1: Semantic Normalization ──────────────────────────────────────

def _validate_uuid(val: str) -> bool:
    """Basic UUID format validation to prevent SQL injection from CLI input."""
    return bool(re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', val, re.I))


def fetch_requirement(req_id: str) -> dict | None:
    """Fetch a requirement with hierarchy context."""
    if not _validate_uuid(req_id):
        log.error("Invalid requirement ID format: %s", req_id)
        return None
    sql = f"""
        SELECT row_to_json(r)::text FROM (
            SELECT
                req.id,
                req.title,
                req.description,
                req.status,
                req.priority,
                req.req_type,
                req.acceptance_criteria,
                req.candidate_id,
                req.system_id,
                req.subsystem_id,
                req.feature_id,
                req.parent_id,
                COALESCE(sys.name, '') AS system_name,
                COALESCE(sys.description, '') AS system_description,
                COALESCE(sub.name, '') AS subsystem_name,
                COALESCE(sub.description, '') AS subsystem_description,
                COALESCE(feat.name, '') AS feature_name,
                COALESCE(feat.description, '') AS feature_description
            FROM nebula.requirements req
            LEFT JOIN nebula.systems sys ON sys.id = req.system_id
            LEFT JOIN nebula.subsystems sub ON sub.id = req.subsystem_id
            LEFT JOIN nebula.features feat ON feat.id = req.feature_id
            WHERE req.id = '{req_id}'
        ) r;
    """
    rows = psql_json(sql)
    return rows[0] if rows else None


def resolve_cross_references(req_id: str) -> list[dict]:
    """Fetch cross-references for this requirement."""
    sql = f"""
        SELECT row_to_json(r)::text FROM (
            SELECT
                cr.rel_type,
                cr.target_type,
                cr.target_id,
                CASE WHEN cr.target_type = 'requirement' THEN
                    (SELECT title FROM nebula.requirements WHERE id = cr.target_id::uuid)
                ELSE cr.target_id::text
                END AS target_label
            FROM nebula.cross_references cr
            WHERE cr.source_type = 'requirement'
              AND cr.source_id = '{req_id}'
            ORDER BY cr.created_at
        ) r;
    """
    rows = psql_json(sql)
    return rows or []


def normalize_acceptance_criteria(raw: Any) -> list[str]:
    """Normalize acceptance criteria from JSONB to a clean string list.

    Handles:
    - JSON array of strings: ["criteria 1", "criteria 2"]
    - JSON array of objects: [{"condition": "...", "evaluator": "..."}]
    - Plain string
    - None/empty
    """
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return [raw] if raw.strip() else []
    if isinstance(raw, list):
        result = []
        for item in raw:
            if isinstance(item, str):
                result.append(item.strip())
            elif isinstance(item, dict):
                condition = item.get("condition") or item.get("title") or item.get("criterion") or ""
                if condition:
                    result.append(condition.strip())
            elif isinstance(item, (int, float)):
                result.append(str(item))
        return [c for c in result if c]
    if isinstance(raw, dict):
        # Single object with criteria
        condition = raw.get("condition") or raw.get("title") or ""
        return [condition.strip()] if condition else []
    return []


def stage1_normalize(requirement: dict, cross_refs: list[dict]) -> dict:
    """Stage 1: Semantic Normalization.

    Parses a raw requirement and produces a NormalizedIntent with:
    - hierarchy_context: system/subsystem/feature names and descriptions
    - normalized_criteria: clean list of acceptance criteria strings
    - cross_references: resolved links to other artifacts
    - intent_summary: synthesized one-paragraph intent description
    """
    log.info("Stage 1: Semantic Normalization for requirement %s", requirement["id"][:8])

    hierarchy_context = {
        "system": {
            "id": requirement.get("system_id"),
            "name": requirement.get("system_name", ""),
            "description": requirement.get("system_description", ""),
        },
        "subsystem": {
            "id": requirement.get("subsystem_id"),
            "name": requirement.get("subsystem_name", ""),
            "description": requirement.get("subsystem_description", ""),
        },
        "feature": {
            "id": requirement.get("feature_id"),
            "name": requirement.get("feature_name", ""),
            "description": requirement.get("feature_description", ""),
        },
    }

    normalized_criteria = normalize_acceptance_criteria(
        requirement.get("acceptance_criteria")
    )

    # Synthesize intent summary from title + description + hierarchy
    parts = [requirement.get("title", "")]
    desc = requirement.get("description", "")
    if desc:
        parts.append(desc)
    sub_name = hierarchy_context["subsystem"]["name"]
    if sub_name:
        parts.append(f"Subsystem: {sub_name}")
    feat_name = hierarchy_context["feature"]["name"]
    if feat_name:
        parts.append(f"Feature: {feat_name}")
    intent_summary = " — ".join(p for p in parts if p)

    normalized = {
        "requirement_id": requirement["id"],
        "title": requirement.get("title", ""),
        "description": requirement.get("description", ""),
        "status": requirement.get("status", "Backlog"),
        "priority": requirement.get("priority", "Medium"),
        "req_type": requirement.get("req_type"),
        "candidate_id": requirement.get("candidate_id"),
        "hierarchy_context": hierarchy_context,
        "normalized_criteria": normalized_criteria,
        "cross_references": cross_refs,
        "intent_summary": intent_summary,
        "normalized_at": datetime.utcnow().isoformat() + "Z",
    }

    log.info("  Hierarchy: %s/%s/%s",
             hierarchy_context["system"]["name"] or "(none)",
             hierarchy_context["subsystem"]["name"] or "(none)",
             hierarchy_context["feature"]["name"] or "(none)")
    log.info("  Criteria: %d items", len(normalized_criteria))
    log.info("  Cross-refs: %d links", len(cross_refs))
    log.info("  Intent: %s", intent_summary[:120])

    return normalized


# ── Stage 2: Engineering Compilation ─────────────────────────────────────

def fetch_op_registry_entries() -> list[dict]:
    """Fetch all active op_registry entries for pattern matching."""
    sql = """
        SELECT row_to_json(r)::text FROM (
            SELECT
                id,
                intent_id,
                version,
                label,
                match_patterns,
                opcode_template,
                required_params,
                optional_params,
                preconditions,
                postconditions,
                idempotency_key
            FROM nebula.op_registry
            WHERE status = 'active' AND deleted_at IS NULL
            ORDER BY created_at
        ) r;
    """
    rows = psql_json(sql)
    return rows or []


def match_intent_to_ops(normalized: dict, registry: list[dict]) -> dict | None:
    """Match the normalized intent against op_registry match_patterns.

    Uses regex patterns from match_patterns to find the best matching
    registry entry. Returns the matched entry or None.
    """
    intent_text = f"{normalized['title']} {normalized['intent_summary']}".lower()

    best_match = None
    best_score = 0

    for entry in registry:
        patterns = entry.get("match_patterns") or []
        if isinstance(patterns, str):
            try:
                patterns = json.loads(patterns)
            except json.JSONDecodeError:
                patterns = [patterns]

        for pattern in patterns:
            try:
                match = re.search(pattern, intent_text, re.IGNORECASE)
                if match:
                    score = len(match.group(0))
                    if score > best_score:
                        best_score = score
                        best_match = entry
            except re.error:
                # Skip invalid regex patterns
                continue

    if best_match:
        log.info("  Matched op_registry entry: %s (score=%d)",
                 best_match.get("id", "?"), best_score)
    else:
        log.info("  No op_registry match found — using default WRITE_FILE sequence")

    return best_match


def generate_opcode_sequence(matched_entry: dict | None, normalized: dict) -> list[dict]:
    """Generate the opcode sequence from the matched registry entry.

    If no match was found, produces a default sequence based on the
    requirement's acceptance criteria (WRITE_SOURCE_FILE + VALIDATE_SYNTAX).
    """
    if matched_entry:
        template = matched_entry.get("opcode_template")
        if isinstance(template, str):
            try:
                template = json.loads(template)
            except json.JSONDecodeError:
                template = []
        if isinstance(template, list) and template:
            # Deep copy the template and substitute placeholders
            steps = []
            for i, step_tpl in enumerate(template):
                step = {
                    "step": i + 1,
                    "op": step_tpl.get("op", "WRITE_FILE"),
                    "target": step_tpl.get("target", ""),
                    "args": step_tpl.get("params", {}),
                    "idempotency_key": f"{matched_entry.get('idempotency_key', '')}-{normalized['requirement_id'][:8]}",
                }
                steps.append(step)
            return steps

    # Default: generate from acceptance criteria
    steps = []
    req_short = normalized["requirement_id"][:8]
    for i, criterion in enumerate(normalized["normalized_criteria"][:5]):
        steps.append({
            "step": i + 1,
            "op": "WRITE_SOURCE_FILE",
            "target": f"src/{req_short}/step_{i+1}",
            "args": {
                "content_template": "acceptance-criterion",
                "criterion": criterion,
            },
            "idempotency_key": f"req-{req_short}-step-{i+1}",
        })

    # Add validation step
    steps.append({
        "step": len(steps) + 1,
        "op": "VALIDATE_SYNTAX",
        "target": f"src/{req_short}/",
        "args": {"language": "auto"},
        "idempotency_key": f"req-{req_short}-validate",
    })

    return steps


def resolve_files_affected(normalized: dict, op_sequence: list[dict]) -> list[str]:
    """Resolve files_affected from the opcode sequence and hierarchy context.

    Derives file paths from:
    1. Opcode targets (explicit paths in the sequence)
    2. Hierarchy context (system/subsystem/feature directory structure)
    """
    files = set()

    # From opcode targets
    for step in op_sequence:
        target = step.get("target", "")
        if target and not target.startswith("spec/") and not target.startswith("files/"):
            # Normalize target to a file path
            if not target.endswith((".py", ".ts", ".js", ".go", ".java", ".sql", ".md")):
                target = target.rstrip("/") + "/__init__.py"
            files.add(target)

    # From hierarchy context — derive likely module file paths
    sys_name = normalized["hierarchy_context"]["system"]["name"]
    sub_name = normalized["hierarchy_context"]["subsystem"]["name"]
    if sys_name:
        base = sys_name.lower().replace(" ", "-")
        if sub_name:
            base = f"{base}/{sub_name.lower().replace(' ', '-')}"
        files.add(f"{base}/__init__.py")

    return sorted(files)


def resolve_dependencies(normalized: dict) -> list[str]:
    """Extract dependency plan numbers from cross-references."""
    deps = []
    for xref in normalized.get("cross_references", []):
        if xref.get("rel_type") in ("req:depends_on", "req:blocks"):
            target = xref.get("target_label", "")
            if target:
                deps.append(target)
    return deps


def stage2_compile(normalized: dict, stage1_only: bool = False) -> dict:
    """Stage 2: Engineering Compilation.

    Matches the normalized intent to the Op Mapping Registry, generates
    an opcode sequence, resolves files_affected and dependencies, and
    assigns an idempotency key. Produces a CompiledWorkRequest IR.
    """
    if stage1_only:
        log.info("Stage 2: Skipped (--stage-1-only)")
        return {**normalized, "compiled": None}

    log.info("Stage 2: Engineering Compilation")

    registry = fetch_op_registry_entries()
    log.info("  Op registry entries loaded: %d", len(registry))

    matched_entry = match_intent_to_ops(normalized, registry)
    op_sequence = generate_opcode_sequence(matched_entry, normalized)
    files_affected = resolve_files_affected(normalized, op_sequence)
    dependencies = resolve_dependencies(normalized)

    # Build acceptance criteria for the plan
    acceptance_criteria = normalized["normalized_criteria"][:5]
    if not acceptance_criteria:
        acceptance_criteria = [f"Implement: {normalized['title']}"]

    # Assign idempotency key
    idem_key = matched_entry.get("idempotency_key", "") if matched_entry else ""
    if not idem_key:
        idem_key = f"req-{normalized['requirement_id'][:8]}"

    compiled = {
        "requirement_id": normalized["requirement_id"],
        "intent_id": matched_entry.get("intent_id", "") if matched_entry else f"REQ-{normalized['requirement_id'][:8]}",
        "registry_version": matched_entry.get("version", "v1") if matched_entry else "default",
        "op_sequence": op_sequence,
        "files_affected": files_affected,
        "dependencies": dependencies,
        "acceptance_criteria": acceptance_criteria,
        "idempotency_key": idem_key,
        "matched_op_registry_id": matched_entry.get("id") if matched_entry else None,
        "compiled_at": datetime.utcnow().isoformat() + "Z",
    }

    log.info("  Op sequence: %d steps", len(op_sequence))
    log.info("  Files affected: %d", len(files_affected))
    log.info("  Dependencies: %d", len(dependencies))
    log.info("  Idempotency key: %s", idem_key)

    return {**normalized, "compiled": compiled}


# ── Audit: Journal Entry ─────────────────────────────────────────────────

def write_journal_entry(normalized: dict, compiled_result: dict) -> str | None:
    """Write a compilation journal entry to the audit substrate.

    Creates a record in nebula.req_compilation_log for traceability.
    Uses an agent_record as the audit mechanism if the dedicated table
    doesn't exist.
    """
    req_id = normalized["requirement_id"]
    now = datetime.utcnow().isoformat() + "Z"
    compiled = compiled_result.get("compiled")

    # Try the dedicated table first
    log_data = json.dumps({
        "requirement_id": req_id,
        "stage1": {
            "normalized_criteria_count": len(normalized["normalized_criteria"]),
            "cross_references_count": len(normalized.get("cross_references", [])),
            "hierarchy": normalized["hierarchy_context"]["system"]["name"],
        },
        "stage2": {
            "matched": compiled is not None,
            "op_count": len(compiled["op_sequence"]) if compiled else 0,
            "files_count": len(compiled["files_affected"]) if compiled else 0,
            "idempotency_key": compiled["idempotency_key"] if compiled else None,
        } if compiled else None,
    }).replace("'", "''")

    # Insert into agent_records as an audit trail entry
    record_id = str(uuidlib.uuid4())
    sql = f"""
        INSERT INTO nebula.agent_records
            (id, record_type, role, title, content, tags, created_at, updated_at)
        VALUES
            ('{record_id}'::uuid, 'engineering_log', 'architect',
             'Requirement Compilation: {normalized["title"].replace(chr(39), chr(39)+chr(39))[:80]}',
             '{log_data}',
             '{{"req-compilation", "requirement:{req_id[:8]}", "audit"}}',
             '{now}', '{now}');
    """
    rc, out = psql(sql)
    if rc == 0:
        log.info("  Journal entry written: %s", record_id[:8])
        return record_id
    else:
        log.warning("  Journal entry write failed: %s", out[:100])
        return None


# ── WorkRequest Submission ────────────────────────────────────────────────

def call_conduit_submit_work_request(normalized: dict, compiled: dict) -> str | None:
    """Submit a WorkRequest via conduit-mcp runtime_submit_work_request.

    Replaces the old flow that created conduit plans directly. WorkRequests
    are the execution contract — the builder follows the opcode recipe.

    Returns the wrId on success, None on failure.
    """
    req_id = normalized["requirement_id"]
    title = normalized["title"]
    objective = normalized["intent_summary"]
    wr_id = f"WR-REQ-{req_id[:8].upper()}"

    # Build opTrace from compiled op_sequence
    resolved_ops = [step["op"] for step in compiled["op_sequence"]]
    ip_nodes = compiled["files_affected"][:10] if compiled["files_affected"] else [f"req-{req_id[:8]}"]
    registry_version = compiled.get("registry_version", "v1")

    payload = {
        "name": "runtime_submit_work_request",
        "arguments": {
            "wrId": wr_id,
            "intent": {
                "type": "implementation",
                "inputs": {
                    "requirement_id": req_id,
                    "acceptance_criteria": compiled["acceptance_criteria"],
                    "dependencies": compiled["dependencies"],
                },
                "objective": objective,
            },
            "constraints": {
                "deterministic": True,
                "maxRetries": 2,
                "timeoutPolicy": "medium",
                "resourceHints": ["local", "python"],
            },
            "opTrace": {
                "ipNodes": ip_nodes,
                "resolvedOps": resolved_ops,
                "registryVersion": registry_version,
            },
        },
    }

    data = json.dumps(payload).encode("utf-8")
    try:
        req = urllib.request.Request(
            CONDUIT_MCP_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())

        inner = result.get("result", result)
        if inner.get("ok") and inner.get("result", {}).get("state", {}).get("wrId"):
            wr_id_resp = inner["result"]["state"]["wrId"]
            log.info("  → WorkRequest submitted: %s", wr_id_resp)
            return wr_id_resp

        log.warning("  Conduit response: %s", str(result)[:200])
        return None

    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300] if e.fp else ""
        log.error("  Conduit HTTP %d: %s", e.code, body)
        return None
    except urllib.error.URLError as e:
        log.error("  Conduit unreachable: %s", e.reason)
        return None


def create_implementation_plan(normalized: dict, compiled: dict) -> str | None:
    """Create a record in nebula.blueprints_history for the compiled output.

    Implementation plans are the detailed context-heavy bridge between
    requirements/specs and WorkRequests. blueprints_history is the canonical
    surface (V171); the implementation_plans compat view is read-only over
    the folded payload, and the legacy mirror trigger retired in V176 — so
    writers must target blueprints_history directly with the payload fold
    (same mapping as conduit's upsertPlan, PR #302).
    """
    import uuid as uuidlib
    plan_id = str(uuidlib.uuid4())
    req_id = normalized["requirement_id"]

    title = normalized["title"].replace("'", "''")
    files = compiled["files_affected"]
    criteria = compiled["acceptance_criteria"]
    deps = compiled["dependencies"]

    payload_sql = json.dumps({
        "goal": normalized["intent_summary"],
        "files_affected": files,
        "acceptance_criteria": criteria,
        "dependencies": deps,
        "requirement_ref": req_id,
        "spec_ref": None,
        "compiled": True,
        "idempotency_key": compiled.get("idempotency_key", ""),
    }).replace("'", "''")

    sql = f"""
        INSERT INTO nebula.blueprints_history
            (id, plan_number, title, payload, blueprint_status)
        VALUES
            ('{plan_id}'::uuid, '{req_id[:8]}', '{title}',
             '{payload_sql}'::jsonb, 'work_requested')
        RETURNING id;
    """
    rc, out = psql(sql)
    if rc == 0 and out:
        impl_id = out.strip()
        log.info("  → Implementation plan created: %s", impl_id[:8])
        return impl_id

    log.error("  Failed to create implementation plan: %s", out[:200])
    return None


def create_cross_reference(req_id: str, target_id: str, rel_type: str = "compiles_to") -> bool:
    """Create a cross-reference linking the requirement to the target artifact."""
    now = datetime.utcnow().isoformat() + "Z"
    xref_id = str(uuidlib.uuid4())
    sql = f"""
        INSERT INTO nebula.cross_references_history
            (id, source_type, source_id, target_type, target_id, rel_type, metadata, created_at)
        SELECT '{xref_id}'::uuid, 'requirement', '{req_id}', 'implementation_plan', '{target_id}',
               '{rel_type}', '{{}}'::jsonb, '{now}'
        WHERE NOT EXISTS (
            SELECT 1 FROM nebula.cross_references_history
            WHERE source_type = 'requirement' AND source_id = '{req_id}'
              AND target_type = 'implementation_plan' AND target_id = '{target_id}'
              AND rel_type = '{rel_type}'
              AND valid_until = '9999-12-31 00:00:00+00'::timestamptz
        )
        ON CONFLICT (source_type, source_id, target_type, target_id, rel_type)
          WHERE valid_until = '9999-12-31 00:00:00+00'::timestamptz
        DO NOTHING;
    """
    rc, _ = psql(sql)
    if rc == 0:
        log.info("  Cross-reference created: requirement → implementation_plan %s", target_id[:8])
        return True
    return False


# ── Main Compilation Flow ────────────────────────────────────────────────

def compile_requirement(
    req_id: str,
    dry_run: bool = False,
    stage1_only: bool = False,
    submit_wr: bool = False,
) -> dict:
    """Full compilation pipeline for a single requirement."""
    result = {
        "requirement_id": req_id,
        "stage1": None,
        "stage2": None,
        "implementation_plan_id": None,
        "wr_id": None,
        "journal_entry_id": None,
        "success": False,
        "error": None,
    }

    # Fetch requirement
    requirement = fetch_requirement(req_id)
    if not requirement:
        result["error"] = "Requirement not found"
        log.error("Requirement not found: %s", req_id)
        return result

    log.info("═" * 60)
    log.info("Compiling: %s", requirement.get("title", ""))
    log.info("  ID: %s", req_id[:8])
    log.info("  Status: %s | Priority: %s", requirement.get("status"), requirement.get("priority"))
    log.info("═" * 60)

    # Stage 1
    cross_refs = resolve_cross_references(req_id)
    normalized = stage1_normalize(requirement, cross_refs)
    result["stage1"] = {
        "hierarchy": normalized["hierarchy_context"],
        "criteria_count": len(normalized["normalized_criteria"]),
        "cross_refs_count": len(cross_refs),
        "intent_summary": normalized["intent_summary"],
    }

    # Stage 2
    compiled_result = stage2_compile(normalized, stage1_only=stage1_only)
    compiled = compiled_result.get("compiled")
    if compiled:
        result["stage2"] = {
            "matched_op_registry": compiled.get("matched_op_registry_id"),
            "op_count": len(compiled["op_sequence"]),
            "files_affected": compiled["files_affected"],
            "idempotency_key": compiled["idempotency_key"],
        }

    if dry_run:
        log.info("  [DRY RUN] Would write journal entry and %ssubmit WorkRequest",
                 "" if submit_wr else "skip ")
        result["success"] = True
        result["compiled_ir"] = compiled_result
        return result

    # Journal entry (audit trail)
    journal_id = write_journal_entry(normalized, compiled_result)
    result["journal_entry_id"] = journal_id

    # WorkRequest submission (replaces old plan creation flow)
    if submit_wr and compiled:
        # 1. Create implementation_plan record first
        impl_plan_id = create_implementation_plan(normalized, compiled)
        if not impl_plan_id:
            result["error"] = "Implementation plan creation failed"
            result["success"] = True  # Compilation succeeded, plan creation failed
            return result
        result["implementation_plan_id"] = impl_plan_id

        # Cascade event: requirement.promoted_to_plan
        try:
            emit_requirement_promoted_to_plan(
                requirement_id=req_id,
                plan_id=impl_plan_id,
                source="rover.req_compiler",
            )
        except Exception as e:
            log.debug("  requirement.promoted_to_plan emission failed: %s", e)

        # 2. Cross-reference requirement → implementation_plan
        create_cross_reference(req_id, impl_plan_id, rel_type="compiles_to")

        # 3. Submit WorkRequest to conduit
        wr_id = call_conduit_submit_work_request(normalized, compiled)
        if wr_id:
            result["wr_id"] = wr_id
        else:
            log.warning("  WorkRequest submission failed (implementation plan created but not submitted)")

    result["success"] = True
    log.info("═" * 60)
    log.info("Compilation %s", "complete" if result["success"] else "failed")
    if result.get("implementation_plan_id"):
        log.info("  Implementation plan: %s", result["implementation_plan_id"][:8])
    if result.get("wr_id"):
        log.info("  WorkRequest: %s", result["wr_id"])
    log.info("═" * 60)
    return result


def main():
    parser = argparse.ArgumentParser(description="Requirement → WorkRequest Compiler")
    parser.add_argument("--requirement", type=str, required=True,
                        help="Requirement UUID to compile")
    parser.add_argument("--submit-wr", action="store_true",
                        help="Submit a WorkRequest from the compiled output (creates implementation_plan + WR)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show compiled IR without writing or submitting a WorkRequest")
    parser.add_argument("--stage-1-only", action="store_true",
                        help="Run Stage 1 (normalization) only, skip Stage 2")
    parser.add_argument("--json", action="store_true",
                        help="Output result as JSON")
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("Requirement → WorkRequest Compilation Pipeline")
    log.info("Time: %s", datetime.now().isoformat())
    log.info("Mode: %s", "DRY RUN" if args.dry_run else ("STAGE 1 ONLY" if args.stage_1_only else "FULL"))
    log.info("=" * 60)

    result = compile_requirement(
        args.requirement,
        dry_run=args.dry_run,
        stage1_only=args.stage_1_only,
        submit_wr=args.submit_wr,
    )

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    else:
        log.info("Result: %s", "✓ SUCCESS" if result["success"] else "✗ FAILED")
        if result.get("error"):
            log.error("Error: %s", result["error"])

    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
