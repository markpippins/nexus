#!/usr/bin/env python3
"""Conduit-native execution worker — the missing READY consumer.

Architect decision 096bab33 (2026-08-09) retired legacy DCO-file dispatch;
``vision.work_requests`` + ``execution.requests`` + cascade admission are
the ONLY dispatch authority. The cascade-admission subscriber mirrors
WorkRequests into ``execution.requests`` with status READY — but until now
no live executor consumed those rows. This worker closes that gap:

    reconcile eligible plans → READY execution requests
        (get_eligible_plans + get_or_create_execution_request)
    for each READY request:
        acquire lease (mutual exclusion)
        claim builder ticket (Invariant 1)
        create + start attempt
        walk the tackle model chain (primary NVIDIA glm-5.2 → fallbacks
            → Ollama last; advance on rate-limit/harness failure)
        run real opencode builder via executor_cloud.run_opencode
        complete attempt + issue execution receipt
        release lease, mark request COMPLETED / FAILED
        advance conduit plan lifecycle:
            IMPLEMENTATION receipt (success) / BLOCK (failure),
            close ticket, advance cursor, create next tickets (Invariant 5)

Execution Authority protocol (ADR-006) is reused wholesale from
``db_adapter.py``; harness launch is reused from ``executor_cloud.py``
(HarnessLauncher + model chain via tackle → GLM 5.2).

Usage:
    python3 execution_worker.py --status             # show READY backlog
    python3 execution_worker.py --dry-run            # reconcile + report, launch nothing
    python3 execution_worker.py --once               # single pass
    python3 execution_worker.py --loop               # daemon (default)
    python3 execution_worker.py --interval 60        # poll seconds
    python3 execution_worker.py --executor-id conduit-worker
    python3 execution_worker.py --include-legacy     # also consume non-plan READY rows
"""

import argparse
import hashlib
import json
import logging
import os
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db_adapter import (  # noqa: E402
    DBAdapter,
    fallback_provider_prefix_slug,
    provider_prefix_slug,
    qualify_opencode_model_id,
)
from env_config import load_env  # noqa: E402 — shared .env loader (fires at import)
import executor_cloud  # noqa: E402
from executor_registry import ModelConfig  # noqa: E402
from work_request_factory import WorkRequestFactory  # noqa: E402

_log = logging.getLogger("execution_worker")
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"))
_log.addHandler(_handler)
_log.setLevel(logging.INFO)

PROJECT_ROOT = os.environ.get("PIPELINE_ROOT", "/home/codex/dev")
ROLE = "builder"
SUCCESS_RECEIPT = "IMPLEMENTATION"
FAIL_RECEIPT = "BLOCK"
LEASE_TTL = 600  # 10 min — renewed in the background during a run; crash recovery ≤10 min
RENEW_INTERVAL = 240  # renew every 4 min (well inside the 10-min TTL)
OUTCOME_EXCERPT_CHARS = 2000
EVIDENCE_FAILURE_OUTCOME = "evidence_failed"
WORK_EVIDENCE_MARKER = "NEXUS_WORK_EVIDENCE:"
PERMISSION_FAILURE_MARKERS = (
    "permission denied", "operation not permitted", "not permitted",
    "opencode permission", "approval required", "tool permission",
)
TOOL_FAILURE_MARKERS = (
    "tool failed", "command failed", "command not found", "traceback (most recent call last)",
)
REVIEW_SETTLEMENT_CRITERIA = (
    "Before issuing REVIEW_PASS, inspect the linked execution attempt and session "
    "outcomes. Explicitly record a disposition for any attempt/session exit-code "
    "mismatch; treat the attempt and session as distinct observations and do not "
    "infer one exit code from the other."
)


class ReviewSettlementCriteriaError(RuntimeError):
    """The reviewer ticket could not be safely made to require mismatch disposition."""

    def __init__(self, message: str):
        super().__init__(message)
        self.review_criterion = {"status": "not_enforced", "reason": message}


class WorkEvidenceError(RuntimeError):
    """A zero-exit builder process did not produce reviewable work evidence."""

    def __init__(self, reasons: list[str], evidence: dict | None = None):
        self.reasons = reasons
        self.evidence = evidence or {}
        super().__init__("; ".join(reasons))


def _parse_work_evidence(*outputs: str) -> dict:
    """Parse the machine-readable evidence line from stdout or stderr."""
    for output in outputs:
        for line in (output or "").splitlines():
            if line.startswith(WORK_EVIDENCE_MARKER):
                try:
                    value = json.loads(line[len(WORK_EVIDENCE_MARKER):].strip())
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    return value
    return {}


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as artifact_file:
        for chunk in iter(lambda: artifact_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def capture_artifact_baseline(
    req: dict,
    *,
    project_root: str = PROJECT_ROOT,
) -> dict[str, str | None]:
    """Capture content identity before the builder can change artifacts."""
    contract = ((req.get("metadata") or {}).get("evidence_contract") or {})
    expected = contract.get("expected_files")
    if expected is None:
        expected = [item.get("path") for item in ((req.get("artifacts") or {}).get("produced_files") or [])
                    if isinstance(item, dict) and item.get("path")]
    root = os.path.realpath(contract.get("allowed_root") or project_root)
    baseline: dict[str, str | None] = {}
    for raw_path in expected or []:
        path = str(raw_path)
        candidate = os.path.realpath(os.path.join(root, path))
        if not (candidate == root or candidate.startswith(root + os.sep)):
            baseline[path] = None
        elif os.path.isfile(candidate):
            baseline[path] = _sha256_file(candidate)
        elif os.path.isdir(candidate):
            for current_root, _dirs, filenames in os.walk(candidate):
                for filename in filenames:
                    file_path = os.path.join(current_root, filename)
                    relative = os.path.relpath(file_path, root)
                    baseline[relative] = _sha256_file(file_path)
            baseline.setdefault(path, None)
        else:
            baseline[path] = None
    return baseline


def validate_work_evidence(
    db: DBAdapter,
    req: dict,
    output: str,
    plan_id: str | None,
    *,
    project_root: str = PROJECT_ROOT,
    diagnostics: str = "",
    work_request_id: str | None = None,
    attempt_id: str | None = None,
    session_id: str | None = None,
    attempt_started_at: float | None = None,
    artifact_baseline: dict[str, str | None] | None = None,
) -> dict:
    """Validate positive builder evidence independently from process success.

    Paths are checked on disk under the contract root. Durable references are
    checked against the DB where possible; a marker in process output alone is
    never accepted as proof that a file or record exists. Content identity is
    compared with the pre-attempt baseline; mtime is not freshness evidence.
    """
    text = output or ""
    diagnostic_text = diagnostics or ""
    lower = f"{text}\n{diagnostic_text}".lower()
    reasons: list[str] = []
    for marker in PERMISSION_FAILURE_MARKERS + TOOL_FAILURE_MARKERS:
        if marker in lower:
            reasons.append(f"failure marker: {marker}")
    contract = ((req.get("metadata") or {}).get("evidence_contract") or {})
    parsed = _parse_work_evidence(text, diagnostic_text)
    if not parsed:
        reasons.append("missing machine-readable work evidence marker")
    expected_correlation = {
        "work_request_id": work_request_id,
        "attempt_id": attempt_id,
        "session_id": session_id,
    }
    for field, expected_value in expected_correlation.items():
        if not expected_value:
            reasons.append(f"missing current {field} validator context")
        elif parsed.get(field) != expected_value:
            reasons.append(f"evidence {field} is missing or does not match current attempt")
    expected = contract.get("expected_files")
    if expected is None:
        expected = [item.get("path") for item in ((req.get("artifacts") or {}).get("produced_files") or [])
                    if isinstance(item, dict) and item.get("path")]
    root = os.path.realpath(contract.get("allowed_root") or project_root)
    artifact_refs = []
    target_dirs = []
    reported = parsed.get("artifacts") if isinstance(parsed.get("artifacts"), list) else []
    for raw_path in expected or []:
        path = str(raw_path)
        candidate = os.path.realpath(os.path.join(root, path))
        if not (candidate == root or candidate.startswith(root + os.sep)):
            reasons.append(f"artifact outside allowed target tree: {path}")
        elif os.path.isdir(candidate):
            target_dirs.append((path, candidate))
        elif not os.path.isfile(candidate):
            reasons.append(f"missing artifact: {path}")
        else:
            current_hash = _sha256_file(candidate)
            baseline_hash = (artifact_baseline or {}).get(path)
            if baseline_hash is not None and current_hash == baseline_hash:
                reasons.append(f"artifact content was not changed during current attempt: {path}")
            artifact_refs.append(path)

    for raw_path in reported:
        artifact_hash = None
        if isinstance(raw_path, dict):
            path = str(raw_path.get("path") or "")
            artifact_hash = raw_path.get("sha256")
        else:
            path = str(raw_path)
        candidate = os.path.realpath(os.path.join(root, path))
        if not (candidate == root or candidate.startswith(root + os.sep)):
            reasons.append(f"reported artifact outside allowed target tree: {path}")
        elif not os.path.isfile(candidate):
            reasons.append(f"reported artifact is not a file: {path}")
        else:
            current_hash = _sha256_file(candidate)
            baseline_hash = (artifact_baseline or {}).get(path)
            changed = baseline_hash is None or current_hash != baseline_hash
            content_matches = bool(artifact_hash) and current_hash == str(artifact_hash).lower()
            if artifact_hash and not content_matches:
                reasons.append(f"artifact content hash mismatch: {path}")
            if not changed:
                reasons.append(f"artifact content was not changed during current attempt: {path}")
            elif content_matches:
                artifact_refs.append(path)
    for target_path, target in target_dirs:
        if not any(
            os.path.realpath(os.path.join(root, ref)).startswith(target + os.sep)
            and ((artifact_baseline or {}).get(ref) is None
                 or _sha256_file(os.path.realpath(os.path.join(root, ref))) != (artifact_baseline or {}).get(ref))
            for ref in artifact_refs
        ):
            reasons.append(f"missing reviewable artifact under target tree: {target_path}")

    durable_ref = parsed.get("agent_record") or parsed.get("pr") or parsed.get("docket")
    durable = False
    if plan_id and hasattr(db, "has_builder_work_evidence"):
        try:
            durable = bool(db.has_builder_work_evidence(
                plan_id,
                work_request_id=work_request_id,
                attempt_id=attempt_id,
                session_id=session_id,
                evidence_ref=durable_ref,
            ))
        except Exception as exc:  # noqa: BLE001
            _log.warning("builder evidence lookup failed plan=%s: %s", plan_id, exc)
    # W-C requires both reviewable target files and a durable trail. Other
    # plans may use the same DB-backed builder record when available.
    if plan_id and (str(plan_id) == "8261655" or contract.get("durable_reference_required", False)) and not durable:
        # A stdout reference is only a locator. The DB-backed record is the
        # evidence; prose or a claimed PR/docket cannot substitute for it.
        reasons.append("missing durable builder evidence record")
    if plan_id and (str(plan_id) == "8261655" or contract.get("durable_reference_required", False)) and not durable_ref:
        reasons.append("missing durable evidence reference in current marker")
    if reasons:
        raise WorkEvidenceError(reasons, {
            "artifact_refs": artifact_refs,
            "durable_reference": durable_ref,
        })
    return {
        "status": "valid",
        "correlation": {
            "work_request_id": work_request_id,
            "attempt_id": attempt_id,
            "session_id": session_id,
        },
        "artifact_refs": artifact_refs,
        "durable_reference": durable_ref,
        "artifact_baseline": artifact_baseline or {},
    }


def _persist_evidence_failure(
    db: DBAdapter,
    request_id: str,
    plan_id: str | None,
    session_id: str,
    ticket_id: str | None,
    attempt_id: str,
    wr_id: str,
    model: str,
    result: dict,
    error: WorkEvidenceError,
) -> dict:
    """Record evidence failure without rewriting the process outcome.

    Every write is best-effort, but request status is closed in a final step.
    A persistence error is carried in the result so callers never report a
    retryable READY row as if evidence handling succeeded.
    """
    updated = dict(result)
    updated["work_outcome"] = EVIDENCE_FAILURE_OUTCOME
    updated["evidence_failure"] = {
        "status": "pending",
        "reasons": error.reasons,
        "evidence": error.evidence,
        "retry_requires": "explicit REQUEUED or review rework authorization",
    }
    updated["governance_finalization"] = {
        "status": "pending",
        "error": str(error),
        "attempt_id": attempt_id,
        "request_id": str(request_id),
        "plan_id": plan_id,
    }
    failures = []
    try:
        if not db.update_attempt_result(attempt_id, updated):
            failures.append(f"attempt result update affected no row (attempt={attempt_id})")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"attempt result update failed: {exc}")
    if plan_id:
        receipt_id = f"rec-execution-worker-{attempt_id}-evidence-failure"
        try:
            if not db.has_plan_receipt(plan_id, receipt_id, FAIL_RECEIPT):
                db.insert_receipt(
                    plan_id=plan_id, receipt_type=FAIL_RECEIPT, agent_role=ROLE,
                    session_id=session_id, ticket_id=ticket_id or "",
                    summary=f"builder evidence failed via {wr_id} (model={model})",
                    metadata={"work_request_id": wr_id, "attempt_id": attempt_id,
                              "evidence_failure": updated["evidence_failure"]},
                    receipt_id=receipt_id,
                )
        except Exception as exc:  # noqa: BLE001
            failures.append(f"evidence failure receipt failed: {exc}")
        # Keep the plan blocked even if receipt visibility/checking fails.  Do
        # not let one lifecycle write prevent the ticket from leaving the
        # eligible pool after a failed request-row update.
        if ticket_id:
            try:
                db.close_ticket(plan_id, ROLE, session_id, "failed")
            except Exception as exc:  # noqa: BLE001
                failures.append(f"plan ticket close failed: {exc}")
        try:
            db.advance_cursor(ROLE, plan_id, wr_id)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"plan cursor advance failed: {exc}")
    try:
        with db._get_connection() as conn:
            conn.execute(
                "UPDATE execution.requests SET status = 'FAILED', updated_at = NOW() "
                "WHERE id = %s AND status = 'READY'",
                (request_id,),
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        failures.append(f"request fail-closed transition failed: {exc}")
    if failures:
        updated["evidence_failure"]["persistence_errors"] = failures
        updated["non_runnable"] = True
        _log.error("evidence failure persistence incomplete request=%s: %s", request_id, failures)
    return updated


def _bounded_text(value: str, limit: int = OUTCOME_EXCERPT_CHARS) -> dict:
    text = value or ""
    return {
        "excerpt": text[:limit],
        "length_chars": len(text),
        "truncated": len(text) > limit,
    }


def _text_value(value) -> str:
    if isinstance(value, dict) and "excerpt" in value:
        return str(value.get("excerpt") or "")
    return str(value or "")


def _review_criteria_for_result(result: dict) -> str:
    reconciliation = (result or {}).get("outcome_reconciliation") or {}
    if reconciliation.get("kind") != "attempt_session_exit_mismatch":
        return REVIEW_SETTLEMENT_CRITERIA
    return (
        f"{REVIEW_SETTLEMENT_CRITERIA}\n\n"
        "Observed linked mismatch requiring explicit disposition: "
        f"attempt {reconciliation.get('attempt_id')} exit code "
        f"{reconciliation.get('attempt_exit_code')}; session "
        f"{reconciliation.get('session_id')} exit code "
        f"{reconciliation.get('session_exit_code')}."
    )

# ── API usage limit detection (ported from conduit/main.py, v075) ───
_API_LIMIT_PATTERNS = [
    "usage limit",
    "rate limit",
    "usage exceeded",
    "api usage",
    "insufficient_quota",
    "quota exceeded",
    "billing",
    "credit",
    "429",
    "402",
    "exceeded your current quota",
    "your account must be",
    "payment required",
    "freeusagelimiterror",
]


def _detect_api_limit_error(exit_code: int, output: str) -> bool:
    output_lower = output.lower()
    if exit_code == 0:
        return False
    for pattern in _API_LIMIT_PATTERNS:
        if pattern in output_lower:
            return True
    return False


def _resolve_model_chain(db: DBAdapter, role: str) -> list:
    """Build the primary + fallback model chain via tackle.

    Mirrors conduit/main.py::_resolve_model_chain (v120): returns a list
    of dicts, primary first, each entry's ``model`` being the
    fully-qualified opencode ID for that model's OWN provider.  This is
    what lets the worker "move NVIDIA up and fall back to Ollama on rate
    limits" — the tackle role config already orders NVIDIA first, and the
    chain walk in _process_one advances on API-limit failures.
    """
    chain: list = []
    primary = None
    try:
        primary = db.get_role_model_config(role)
    except Exception as exc:  # noqa: BLE001
        _log.warning("_resolve_model_chain: primary lookup failed role=%s: %s", role, exc)

    primary_slug = ""
    provider_map: dict = {}
    if primary:
        primary_slug = provider_prefix_slug(
            primary.get("provider_name", ""),
            primary.get("provider_type", ""),
            primary.get("provider_id", ""),
        )
        pid = primary.get("provider_id", "")
        if pid and primary_slug:
            provider_map[pid] = primary_slug

    if primary and primary.get("harness") and primary.get("model"):
        chain.append({
            "harness": primary["harness"],
            "model": qualify_opencode_model_id(primary["model"], primary_slug),
            "priority": -1,
        })

    try:
        fallbacks = db.get_fallback_models(role)
    except Exception as exc:  # noqa: BLE001
        _log.warning("_resolve_model_chain: fallback lookup failed role=%s: %s", role, exc)
        fallbacks = []

    primary_model = (primary or {}).get("model", "")
    for fb in fallbacks:
        semantics = fb.get("invocation_semantics") or {}
        binary = semantics.get("binary", "")
        if not binary:
            _log.warning(
                "_resolve_model_chain: skipping fallback role=%s model=%s harness '%s' has no binary",
                role, fb.get("model_identifier", "?"), fb.get("harness_name", "?"),
            )
            continue
        model_id = fb.get("model_identifier", "")
        if model_id and model_id == primary_model:
            continue
        fbid = fb.get("provider_id", "")
        slug = provider_map.get(fbid, "") or fallback_provider_prefix_slug(
            fb.get("provider_name", ""), fb.get("provider_type", ""),
            fbid, primary_slug,
        )
        chain.append({
            "harness": binary,
            "model": qualify_opencode_model_id(model_id, slug),
            "priority": fb.get("priority", 0),
        })

    if not chain:
        env_model = os.environ.get("PIPELINE_MODEL", "")
        if env_model:
            _log.info("_resolve_model_chain: PIPELINE_MODEL fallback role=%s model=%s", role, env_model)
            chain.append({"harness": "opencode", "model": env_model, "priority": -1})

    return chain


def _log_ok(msg: str):
    print(f"  ✓ {msg}")


def _log_warn(msg: str):
    print(f"  ! {msg}")


def _list_ready(db: DBAdapter, include_legacy: bool, eligible_plan_ids: set) -> list[dict]:
    """Fetch READY execution requests.

    Default: only requests whose source_plan_id is genuinely eligible
    (open builder ticket + correct derived status — i.e., the plans
    get_eligible_plans returned). This prevents consuming stale
    plan-backed rows that have no live ticket (187 legacy-p* rows from
    the June/July era). With --include-legacy, all READY rows are
    returned (use with care).
    """
    if include_legacy:
        rows = db.get_requests_by_status("READY", limit=100)
    elif eligible_plan_ids:
        placeholders = ", ".join(["%s"] * len(eligible_plan_ids))
        with db._get_connection() as conn:
            cursor = conn.execute(
                # Eligibility is scoped by source_plan_id IN (eligible plans
                # with live open tickets). No intent_type predicate: tier-2
                # bridge rows created by resolve_request_for_receipt() for REAL
                # plans carry intent_type='legacy' (business_key legacy-plan-*),
                # so excluding them would skip live plans (W-A 8261653,
                # planner record cd206980). Stale June/July rows are excluded
                # by the IN clause — their plans have no live ticket.
                f"""SELECT * FROM execution.requests
                    WHERE status = 'READY' AND source_plan_id IN ({placeholders})
                    ORDER BY created_at ASC
                    LIMIT 100""",
                tuple(eligible_plan_ids),
            )
            rows = cursor.dict_fetchall()
    else:
        rows = []
    return rows


def _reconcile_plans(db: DBAdapter) -> list[dict]:
    """Ensure every eligible builder plan has a READY execution request.

    Returns the READY requests created (or found) for those plans.
    """
    plans = db.get_eligible_plans(ROLE)
    created = []
    for plan in plans:
        plan_id = plan["id"]
        req = db.get_or_create_execution_request(
            plan_id=plan_id,
            title=plan.get("title", ""),
            objective=plan.get("goal", ""),
        )
        request_status = req.get("status")
        may_set_ready = request_status in {"DRAFT", "COMPILED", "VALIDATED", "ADMITTED"}
        if request_status == "FAILED":
            may_set_ready = db.has_plan_requeued_after_last_attempt(plan_id, req["id"])
        elif request_status == "CANCELLED":
            may_set_ready = db.has_plan_requeued_after_cancellation(
                plan_id, req["id"]
            )
        elif request_status == "COMPLETED":
            may_set_ready = db.has_plan_rework_after_last_success(
                plan_id, req["id"]
            )
        # CANCELLED/COMPLETED remain terminal without their explicit, newer
        # requeue/rework receipt; plan eligibility alone is not authorization.
        if may_set_ready:
            where = "r.id = %s AND r.status = %s"
            params = [req["id"], request_status]
            if request_status == "CANCELLED":
                where += (
                    " AND r.source_plan_id = %s AND r.updated_at = %s"
                    " AND EXISTS (SELECT 1 FROM nebula.receipts_unified rr"
                    " WHERE rr.plan_id = r.source_plan_id AND rr.type = 'REQUEUED'"
                    " AND rr.created_at > r.updated_at)"
                )
                params.extend((plan_id, req.get("updated_at")))
            elif request_status == "COMPLETED":
                where += (
                    " AND r.source_plan_id = %s"
                    " AND EXISTS (SELECT 1 FROM execution.attempts a"
                    " WHERE a.request_id = r.id AND a.status = 'SUCCEEDED')"
                    " AND EXISTS (SELECT 1 FROM nebula.receipts_unified rr"
                    " WHERE rr.plan_id = r.source_plan_id"
                    " AND rr.type IN ('REVIEW_REJECT', 'CRITIQUE_PASS')"
                    " AND rr.created_at > (SELECT MAX(a.completed_at)"
                    " FROM execution.attempts a WHERE a.request_id = r.id"
                    " AND a.status = 'SUCCEEDED'))"
                )
                params.append(plan_id)
            elif request_status == "FAILED":
                where += (
                    " AND EXISTS (SELECT 1 FROM nebula.receipts_unified rr"
                    " WHERE rr.plan_id = r.source_plan_id AND rr.type = 'REQUEUED'"
                    " AND rr.created_at > COALESCE((SELECT MAX(a.completed_at)"
                    " FROM execution.attempts a WHERE a.request_id = r.id),"
                    " '-infinity'::timestamptz))"
                )
            with db._get_connection() as conn:
                cursor = conn.execute(
                    f"UPDATE execution.requests r SET status = 'READY', updated_at = NOW() WHERE {where}",
                    tuple(params),
                )
                conn.commit()
            if cursor.rowcount > 0:
                _log.info("reconcile: plan %s request %s (%s) → READY",
                          plan_id, req["id"], request_status)
                req["status"] = "READY"
            else:
                with db._get_connection() as conn:
                    latest = conn.execute(
                        "SELECT status FROM execution.requests WHERE id = %s",
                        (req["id"],),
                    ).fetchone()
                actual_status = latest[0] if latest else None
                if actual_status is not None:
                    req["status"] = actual_status
                _log.warning(
                    "reconcile: READY transition lost race/authorization plan=%s "
                    "request=%s expected_status=%s current_status=%s",
                    plan_id, req["id"], request_status, actual_status,
                )
        created.append(req)
    return created


def _build_req_from_request(db: DBAdapter, row: dict, model: str = "") -> dict | None:
    """Build a DCO-shaped req dict for executor_cloud from an execution request.

    Plan-backed → full DCO via WorkRequestFactory.create_from_plan.
    Non-plan → minimal req from the request row (legacy mode only).
    Returns None if the plan cannot be resolved.
    """
    plan_id = row.get("source_plan_id")
    if plan_id:
        plan = db.get_plan_by_id(plan_id)
        if not plan:
            _log.warning("request %s → plan %s not found; skipping", row["id"], plan_id)
            return None
        model_cfg = ModelConfig(harness="opencode", model=model)
        dco = WorkRequestFactory.create_from_plan(
            plan,
            role=ROLE,
            model_cfg=model_cfg,
            working_path=PROJECT_ROOT,
            session_id=row.get("_session_id", ""),
        )
        req = dco.model_dump(by_alias=True)
        req["_plan_id"] = plan_id
        return req

    # Legacy / non-plan request: minimal DCO-shaped dict
    req = {
        "id": row.get("business_key") or str(row["id"]),
        "path": PROJECT_ROOT,
        "objective": row.get("objective") or row.get("title") or "",
        "metadata": {
            "role": ROLE,
            "harness": "opencode",
            "model": model,
            "session_id": row.get("_session_id", ""),
            "tags": [],
        },
        "intent": {
            "problem_statement": row.get("objective") or "",
            "desired_outcome": row.get("title") or "",
        },
        "decomposition": {"strategy": "Direct execution", "steps": []},
        "requirements": {"functional": [], "non_functional": [], "system_requirements": [], "tool_requirements": []},
        "constraints": {"safety_constraints": [], "forbidden_actions": [], "resource_limits": None, "architectural_constraints": []},
        "success_criteria": {"completion_conditions": [], "validation_rules": [], "acceptance_tests": [], "failure_modes": []},
        "artifacts": {"produced_files": [], "intermediate_outputs": []},
        "lineage": {"derived_from": [], "supersedes": None, "branches": [], "merge_history": []},
    }
    return req


def _claim_conduit_ticket(db: DBAdapter, plan_id: str, session_id: str) -> str | None:
    """Claim the builder ticket on the plan (Invariant 1). Returns ticket_id or None."""
    try:
        ticket_id = db.claim_ticket(plan_id, ROLE, session_id)
    except Exception as e:  # noqa: BLE001
        _log.warning("claim_ticket failed plan=%s error=%s", plan_id, e)
        return None
    if ticket_id:
        _log_ok(f"ticket {ticket_id} claimed (plan {plan_id})")
    else:
        _log_warn(f"no open builder ticket for plan {plan_id} — continuing without ticket claim")
    return ticket_id


def _complete_conduit_lifecycle(
    db: DBAdapter,
    plan_id: str,
    session_id: str,
    ticket_id: str | None,
    wr_id: str,
    model: str,
    success: bool,
    attempt_id: str = "",
    review_criteria: str = REVIEW_SETTLEMENT_CRITERIA,
) -> dict | None:
    """Replicate _dispatch_one's success/failure tail: receipt, close ticket,
    advance cursor, create next tickets (Invariant 5).

    GOVERNANCE COVERAGE (do NOT add vision_bridge.issue_receipt here):
    db.insert_receipt owns the plan-level IMPLEMENTATION/BLOCK write across
    request-backed and synthetic receipt channels. Adding issue_receipt() on
    top would duplicate lifecycle evidence and can map success directly to
    REVIEW_PASS, which create_next_tickets' terminal-state guard treats as
    plan-terminal and would suppress the reviewer ticket. Keep the
    IMPLEMENTATION/BLOCK lifecycle receipt here.
    """
    if success:
        receipt_id = f"rec-execution-worker-{attempt_id or wr_id}-implementation"
        db.insert_receipt(
            plan_id=plan_id,
            receipt_type=SUCCESS_RECEIPT,
            agent_role=ROLE,
            session_id=session_id,
            ticket_id=ticket_id or "",
            summary=f"builder completed via {wr_id} (model={model})",
            metadata={"work_request_id": wr_id, "role": ROLE, "harness": "opencode",
                      "model": model, "executor": "execution-worker",
                      "attempt_id": attempt_id},
            receipt_id=receipt_id,
        )
        if not db.has_plan_receipt(plan_id, receipt_id, SUCCESS_RECEIPT):
            raise RuntimeError(
                f"plan-level {SUCCESS_RECEIPT} receipt is not visible "
                f"(plan={plan_id}, receipt={receipt_id})"
            )
        if ticket_id:
            db.close_ticket(plan_id, ROLE, session_id, "completed")
        db.advance_cursor(ROLE, plan_id, wr_id)
        db.create_next_tickets(
            plan_id, ROLE, "completed",
            parent_ticket_id=ticket_id or "",
            objective=wr_id,
            completion_criteria=review_criteria,
            owner=ROLE,
            idempotency_key=f"execution-worker:{attempt_id or wr_id}:reviewer",
        )
        if not db.ensure_reviewer_ticket_completion_criteria(
            plan_id,
            ticket_id,
            wr_id,
            review_criteria,
        ):
            raise ReviewSettlementCriteriaError(
                "no matching open reviewer child could be safely updated, or a "
                "claimed reviewer ticket has a different completion criterion"
            )
        _log_ok(f"IMPLEMENTATION receipt + next tickets created (plan {plan_id})")
        return {"status": "enforced", "criteria": review_criteria}
    else:
        db.insert_receipt(
            plan_id=plan_id,
            receipt_type=FAIL_RECEIPT,
            agent_role=ROLE,
            session_id=session_id,
            ticket_id=ticket_id or "",
            summary=f"builder failed via {wr_id} (model={model})",
            metadata={"work_request_id": wr_id, "role": ROLE, "harness": "opencode",
                      "model": model, "executor": "execution-worker"},
        )
        if ticket_id:
            db.close_ticket(plan_id, ROLE, session_id, "failed")
        db.advance_cursor(ROLE, plan_id, wr_id)
        _log_warn(f"BLOCK receipt recorded (plan {plan_id})")
    return None


def _set_governance_finalization(
    db: DBAdapter,
    attempt_id: str,
    result: dict,
    status: str,
    error: str | None = None,
    review_criterion: dict | None = None,
) -> dict:
    """Update plan-governance state without changing process/work outcome."""
    updated = dict(result or {})
    governance = dict(updated.get("governance_finalization") or {})
    governance["status"] = status
    governance["error"] = error
    governance["updated_at"] = datetime.now(timezone.utc).isoformat()
    if review_criterion is not None:
        governance["review_settlement_criterion"] = dict(review_criterion)
    updated["governance_finalization"] = governance
    if not db.update_attempt_result(attempt_id, updated):
        raise RuntimeError(f"attempt result update affected no row (attempt={attempt_id})")
    return updated


def _ensure_success_execution_evidence(
    db: DBAdapter,
    attempt_id: str,
    request_id: str,
    result: dict,
    executor_id: str,
) -> None:
    """Ensure correlated work evidence and an attempt receipt precede settlement."""
    if (str(result.get("attempt_id") or "") != str(attempt_id)
            or str(result.get("request_id") or "") != str(request_id)
            or not result.get("session_id")
            or not result.get("work_request_id")):
        raise RuntimeError(
            f"successful outcome identity does not match attempt/request/session/WorkRequest "
            f"(attempt={attempt_id}, request={request_id})"
        )
    work_evidence = result.get("work_evidence") or {}
    correlation = work_evidence.get("correlation") or {}
    if (work_evidence.get("status") != "valid"
            or str(correlation.get("attempt_id") or "") != str(attempt_id)
            or str(correlation.get("work_request_id") or "") != str(result["work_request_id"])
            or str(correlation.get("session_id") or "") != str(result["session_id"])):
        raise RuntimeError(
            f"successful outcome lacks evidence correlated to current attempt/session/WorkRequest "
            f"(attempt={attempt_id}, request={request_id})"
        )
    if not db.has_attempt_execution_receipt(attempt_id, request_id):
        db.issue_execution_receipt(
            attempt_id=attempt_id,
            request_id=request_id,
            receipt_type=SUCCESS_RECEIPT,
            agent_role=executor_id,
            summary=f"builder completed via {result.get('work_request_id')} "
                    f"(model={result.get('model')})",
            metadata={
                "work_request_id": result.get("work_request_id"),
                "executor": executor_id,
                "model": result.get("model"),
                "session_id": result.get("session_id"),
                "attempt_exit_code": result.get("process_outcome", {}).get("returncode"),
            },
        )
    if not db.has_attempt_execution_receipt(attempt_id, request_id):
        raise RuntimeError(
            f"attempt-linked success receipt is not visible (attempt={attempt_id}, request={request_id})"
        )
    if not db.mark_request_completed_after_attempt(attempt_id, request_id):
        raise RuntimeError(
            f"request completion requires a SUCCEEDED exit_code=0 attempt and its receipt "
            f"(attempt={attempt_id}, request={request_id})"
        )


def _finalize_governance_row(db: DBAdapter, row: dict) -> dict:
    """Retry plan fan-out for an already successful execution attempt."""
    attempt_id = str(row["attempt_id"])
    result = row.get("result") or {}
    if isinstance(result, str):
        result = json.loads(result)
    if result.get("work_outcome") == EVIDENCE_FAILURE_OUTCOME:
        raise RuntimeError(
            f"evidence-failed attempt cannot be governance-settled (attempt={attempt_id})"
        )
    plan_id = row.get("source_plan_id") or result.get("plan_id")
    request_id = str(row.get("request_id") or result.get("request_id") or "")
    if row.get("attempt_status") != "SUCCEEDED" or row.get("exit_code") not in (0, "0"):
        raise RuntimeError(
            f"attempt evidence is not a successful zero-exit outcome "
            f"(attempt={attempt_id}, status={row.get('attempt_status')}, exit_code={row.get('exit_code')})"
        )
    _ensure_success_execution_evidence(
        db, attempt_id, request_id, result, result.get("executor_id", "conduit-worker")
    )
    if not plan_id:
        return _set_governance_finalization(db, attempt_id, result, "not_applicable")
    review_criterion = _complete_conduit_lifecycle(
        db,
        plan_id,
        result.get("session_id", ""),
        result.get("ticket_id"),
        result.get("work_request_id", ""),
        result.get("model", ""),
        success=True,
        attempt_id=attempt_id,
        review_criteria=_review_criteria_for_result(result),
    )
    return _set_governance_finalization(
        db, attempt_id, result, "complete",
        review_criterion=review_criterion or {"status": "not_required"},
    )


def _retry_pending_governance_finalizations(db: DBAdapter, limit: int = 100) -> tuple[int, int]:
    """Reconcile successful attempts without reacquiring a lease or relaunching."""
    try:
        pending = db.get_pending_governance_finalizations(limit=limit)
    except Exception as exc:  # noqa: BLE001
        _log.warning("pending governance lookup failed: %s", exc)
        return 0, 0
    finalized = 0
    still_pending = 0
    for row in pending:
        result = row.get("result") or {}
        if isinstance(result, str):
            result = json.loads(result)
        try:
            _finalize_governance_row(db, row)
            finalized += 1
        except Exception as exc:  # noqa: BLE001
            still_pending += 1
            _log.error(
                "governance finalization remains pending request=%s plan=%s session=%s attempt=%s: %s",
                row.get("request_id"), row.get("source_plan_id"),
                result.get("session_id"), row.get("attempt_id"), exc,
            )
            try:
                _set_governance_finalization(
                    db, str(row["attempt_id"]), result, "pending", str(exc),
                    review_criterion=getattr(exc, "review_criterion", None),
                )
            except Exception as record_exc:  # noqa: BLE001
                _log.error("could not update pending governance error attempt=%s: %s",
                           row.get("attempt_id"), record_exc)
    return finalized, still_pending


def _surface_session_exit_observations(db: DBAdapter) -> int:
    """Persist and surface only mismatched attempt/session exit observations."""
    try:
        observations = db.get_worker_session_outcomes(limit=100)
    except Exception as exc:  # noqa: BLE001
        _log.warning("worker session outcome lookup failed: %s", exc)
        return 0
    mismatch_count = 0
    for observation in observations:
        attempt_code = observation.get("attempt_exit_code")
        session_code = observation.get("session_exit_code")
        if attempt_code is None or session_code is None or attempt_code == session_code:
            continue

        mismatch_count += 1
        result = observation.get("attempt_result") or {}
        if isinstance(result, str):
            result = json.loads(result)
        prior = result.get("outcome_reconciliation") or {}
        same_observation = (
            prior.get("kind") == "attempt_session_exit_mismatch"
            and prior.get("attempt_exit_code") == attempt_code
            and prior.get("session_exit_code") == session_code
            and prior.get("session_id") == observation.get("session_id")
        )
        prior_criterion = prior.get("review_ticket_criterion") or {}
        if same_observation and prior_criterion.get("status") == "enforced":
            continue

        observed_at = (
            prior.get("observed_at") if same_observation
            else datetime.now(timezone.utc).isoformat()
        )
        result["session_outcome"] = {
            "session_id": observation.get("session_id"),
            "exit_code": session_code,
            "observed_at": observed_at,
        }
        reviewer_criteria = _review_criteria_for_result({
            **result,
            "outcome_reconciliation": {
                "kind": "attempt_session_exit_mismatch",
                "attempt_id": observation.get("attempt_id"),
                "attempt_exit_code": attempt_code,
                "session_id": observation.get("session_id"),
                "session_exit_code": session_code,
            },
        })
        try:
            criterion_enforced = bool(observation.get("plan_id")) and db.ensure_reviewer_ticket_completion_criteria(
                observation.get("plan_id"),
                result.get("ticket_id"),
                result.get("work_request_id", ""),
                reviewer_criteria,
            )
            criterion_record = (
                {"status": "enforced", "criteria": reviewer_criteria}
                if criterion_enforced else
                {"status": "not_enforced", "reason": "no safely updatable matching reviewer ticket"}
            )
        except Exception as exc:  # noqa: BLE001
            criterion_enforced = False
            criterion_record = {"status": "not_enforced", "reason": str(exc)[:500]}
        result["outcome_reconciliation"] = {
            "kind": "attempt_session_exit_mismatch",
            "review_required": True,
            "attempt_id": observation.get("attempt_id"),
            "request_id": observation.get("request_id"),
            "plan_id": observation.get("plan_id"),
            "session_id": observation.get("session_id"),
            "attempt_exit_code": attempt_code,
            "session_exit_code": session_code,
            "disposition": prior.get("disposition") if same_observation else None,
            "review_ticket_criterion": criterion_record,
            "observed_at": observed_at,
        }
        governance = dict(result.get("governance_finalization") or {})
        governance["review_settlement_criterion"] = criterion_record
        if not criterion_enforced:
            governance["status"] = "pending"
            governance["error"] = criterion_record.get("reason")
        result["governance_finalization"] = governance
        try:
            if not db.update_attempt_result(observation["attempt_id"], result):
                raise RuntimeError("attempt result update affected no row")
        except Exception as exc:  # noqa: BLE001
            _log.error(
                "attempt/session exit mismatch could not be persisted attempt=%s "
                "session=%s attempt_exit_code=%s session_exit_code=%s: %s",
                observation.get("attempt_id"), observation.get("session_id"),
                attempt_code, session_code, exc,
            )
            continue
        if not same_observation or prior_criterion != criterion_record:
            _log.warning(
                "review required: independent attempt/session exit mismatch attempt=%s "
                "request=%s plan=%s session=%s attempt_exit_code=%s session_exit_code=%s "
                "review_ticket_criterion=%s",
                observation.get("attempt_id"), observation.get("request_id"),
                observation.get("plan_id"), observation.get("session_id"),
                attempt_code, session_code, criterion_record.get("status"),
            )
    return mismatch_count


def _release_lease_safely(db: DBAdapter, lease_id: str) -> None:
    """Release an active or already released lease without masking outcomes."""
    try:
        db.release_lease(lease_id)
    except Exception as exc:  # noqa: BLE001
        _log.warning("lease release failed lease=%s: %s", lease_id, exc)


def _close_worker_session_safely(db: DBAdapter, session_id: str, exit_code: int | None) -> None:
    """Close this worker's session without replacing another writer's outcome."""
    try:
        if not db.close_worker_session(session_id, exit_code):
            _log.info("worker session already closed; preserving stored outcome session=%s",
                      session_id)
    except Exception as exc:  # noqa: BLE001
        _log.error("worker session close failed session=%s exit_code=%s: %s",
                   session_id, exit_code, exc)


def _release_claimed_ticket_safely(
    db: DBAdapter, plan_id: str, session_id: str
) -> None:
    """Return this worker's claimed ticket to the pool without failing it."""
    try:
        if not db.release_ticket(plan_id, ROLE, session_id):
            _log.info(
                "builder ticket was no longer claimed by this worker; preserving ticket state "
                "plan=%s session=%s",
                plan_id, session_id,
            )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "builder ticket release failed after admission refusal plan=%s session=%s: %s",
            plan_id, session_id, exc,
        )


def _process_one(db: DBAdapter, row: dict, executor_id: str, dry_run: bool, model: str = "") -> str:
    """Claim + execute one READY request. Returns outcome string."""
    request_id = row["id"]
    plan_id = row.get("source_plan_id")
    title = row.get("title", "")[:60]

    if dry_run:
        _log.info("DRY-RUN: would execute request %s (plan=%s title=%r)", request_id, plan_id, title)
        return "dry-run"

    # ── Lease (mutual exclusion, ADR-006) ──────────────────────────
    lease = db.acquire_lease(request_id=request_id, executor_id=executor_id, ttl_seconds=LEASE_TTL)
    if not lease:
        _log_warn(f"request {request_id} already leased — skipping")
        return "leased"

    lease_id = lease["id"]
    session_id = f"worker-{executor_id}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{os.urandom(4).hex()}"
    ticket_id = None
    attempt_id = None
    attempt_started_at = None
    session_created = False
    worker_session_exit_code = 1
    attempt_outcome_saved = False
    outcome_result: dict = {}
    model_attempts: list[dict] = []
    last_process_outcome: dict = {}
    session_log_path = None
    renewer_stop = threading.Event()

    def _renew_lease_loop():
        while not renewer_stop.wait(RENEW_INTERVAL):
            try:
                db.renew_lease(lease_id, LEASE_TTL)
            except Exception as e:  # noqa: BLE001
                _log.warning("lease renew failed: %s", e)

    renewer = threading.Thread(target=_renew_lease_loop, daemon=True)
    renewer.start()

    try:
        # ── Ticket (Invariant 1) — claim if plan-backed ─────────────
        if plan_id:
            session_created = True
            db.create_session(session_id, ROLE, [plan_id])
            ticket_id = _claim_conduit_ticket(db, plan_id, session_id)

        # ── Attempt (ADR-006) ───────────────────────────────────────
        attempt = db.create_attempt(
            lease_id=lease_id,
            request_id=request_id,
            executor_id=executor_id,
            work_role=ROLE,
            session_id=session_id,
            result={"session_id": session_id, "plan_id": plan_id},
        )
        if attempt is None:
            _log.info(
                "builder attempt refused by admission recheck request=%s plan=%s",
                request_id, plan_id,
            )
            _release_lease_safely(db, lease_id)
            if plan_id and ticket_id:
                _release_claimed_ticket_safely(db, plan_id, session_id)
            # No process was launched; preserve the session's null outcome
            # rather than reporting a build failure.
            worker_session_exit_code = None
            return "admission-race"
        attempt_id = attempt["id"]
        db.start_attempt(attempt_id)
        attempt_started_at = time.time()
        _log.info("attempt %s started (request %s, plan %s)", attempt_id, request_id, plan_id)

        # ── Build req + launch real agent ───────────────────────────
        row["_session_id"] = session_id
        req = _build_req_from_request(db, row, model=model)
        if req is None:
            raise RuntimeError(f"cannot build req for request {request_id}")
        req.setdefault("metadata", {}).update({
            "work_request_id": req.get("id"),
            "attempt_id": attempt_id,
            "session_id": session_id,
        })
        artifact_baseline = capture_artifact_baseline(req, project_root=PROJECT_ROOT)
        req["metadata"]["artifact_baseline"] = artifact_baseline

        # ── Record the WR in vision.work_requests (idempotent) ──────
        # Mirrors _dispatch_one's add_work_request: gives plan 1284's
        # conformance trace a real WR row in the vision store.
        if plan_id:
            try:
                db.add_work_request(
                    req.get("id", ""), plan_id,
                    json.dumps(req, default=str),
                    title=title,
                )
            except Exception as e:  # noqa: BLE001
                _log.warning("add_work_request failed plan=%s error=%s", plan_id, e)

        session_log_path = os.path.join(
            os.environ.get("CONDUIT_DATA_DIR", os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "audit", "CONDUIT_DATA")),
            "session_logs", f"{session_id}.log",
        )

        # ── Model chain walk (NVIDIA first, fall back on rate limits) ──
        # v121: walk the tackle model chain exactly like legacy
        # _dispatch_one — primary (NVIDIA glm-5.2) first, then fallbacks,
        # advancing on API-limit / harness failure. The old single-shot
        # launch used whatever PIPELINE_MODEL forced (systemd env had
        # ollama/qwen2.5-coder-ctx32k, bypassing tackle entirely, so
        # Ollama always won). User directive 2026-08-10: NVIDIA up top,
        # rate limits push us back to Ollama.
        if model:
            # Explicit --model override (legacy manual mode): single entry.
            chain = [{"harness": "opencode", "model": model}]
        else:
            chain = _resolve_model_chain(db, ROLE)
        if not chain:
            raise RuntimeError(f"no model chain resolved for role={ROLE} (tackle role config empty)")
        _log.info("model chain: %s", [e["model"] for e in chain])

        work_result = ""
        last_error = ""
        used_model = ""
        for idx, entry in enumerate(chain):
            entry_model = entry.get("model", "")
            req["metadata"]["model"] = entry_model
            req["metadata"]["harness"] = entry.get("harness", "opencode")
            label = "primary" if idx == 0 else f"fallback#{idx}"
            try:
                _log.info("launching opencode builder via run_opencode [%s] model=%s …", label, entry_model)
                outcome = executor_cloud.run_opencode_outcome(
                    req, PROJECT_ROOT, session_log_path=session_log_path
                )
                if not isinstance(outcome, executor_cloud.HarnessExecutionResult):
                    raise TypeError(
                        "run_opencode_outcome must return HarnessExecutionResult; "
                        f"got {type(outcome).__name__}"
                    )
                if outcome.returncode != 0:
                    raise executor_cloud.HarnessExecutionError(
                        f"harness exited with code {outcome.returncode}", outcome
                    )
                model_attempts.append({
                    "model": entry_model,
                    "harness": entry.get("harness", "opencode"),
                    "returncode": outcome.returncode,
                    "stdout": _bounded_text(outcome.stdout),
                    "stderr": _bounded_text(outcome.stderr),
                    "error": None,
                })
                last_process_outcome = model_attempts[-1]
                work_result = outcome.stdout
                used_model = entry_model
                _log.info("builder finished via [%s] model=%s (chars=%d)", label, entry_model, len(work_result or ""))
                break
            except Exception as e:  # noqa: BLE001
                last_error = str(e)
                process_outcome = getattr(e, "outcome", None)
                failed_model_attempt = {
                    "model": entry_model,
                    "harness": entry.get("harness", "opencode"),
                    "returncode": getattr(process_outcome, "returncode", None),
                    "stdout": _bounded_text(getattr(process_outcome, "stdout", "")),
                    "stderr": _bounded_text(getattr(process_outcome, "stderr", "")),
                    "error": _bounded_text(last_error),
                }
                model_attempts.append(failed_model_attempt)
                last_process_outcome = failed_model_attempt
                is_limit = _detect_api_limit_error(
                    failed_model_attempt["returncode"] if failed_model_attempt["returncode"] is not None else -1,
                    "\n".join((last_error,
                               getattr(process_outcome, "stdout", ""),
                               getattr(process_outcome, "stderr", ""))),
                )
                _log.warning("[%s] model=%s failed rate_limit=%s: %s",
                             label, entry_model, is_limit, last_error[:300])
                continue
        if not used_model:
            raise RuntimeError(
                f"all {len(chain)} model(s) in chain failed for request {request_id}: {last_error[:500]}")
        _log.info("builder succeeded via model=%s", used_model)

        # ── Process outcome + WorkRequest result ────────────────────
        outcome_result = {
            "work_request_id": req.get("id"),
            "work_result": _bounded_text(work_result),
            "process_outcome": {
                "returncode": last_process_outcome.get("returncode", 0),
                "stdout": last_process_outcome["stdout"],
                "stderr": last_process_outcome["stderr"],
                "model": used_model,
                "session_log_path": session_log_path,
            },
            "model_attempts": model_attempts,
            "session_log_path": session_log_path,
            "session_id": session_id,
            "attempt_id": attempt_id,
            "plan_id": plan_id,
            "ticket_id": ticket_id,
            "request_id": str(request_id),
            "executor_id": executor_id,
            "model": used_model,
            "artifact_baseline": artifact_baseline,
            "governance_finalization": {
                "status": "pending",
                "error": None,
                "attempt_id": attempt_id,
                "request_id": str(request_id),
                "plan_id": plan_id,
                "session_id": session_id,
                "review_settlement_criterion": {
                    "status": "pending" if plan_id else "not_required",
                    "required": bool(plan_id),
                },
            },
        }
        try:
            outcome_result["work_evidence"] = validate_work_evidence(
                db,
                req,
                work_result,
                plan_id,
                project_root=PROJECT_ROOT,
                diagnostics=_text_value(last_process_outcome.get("stderr", "")),
                work_request_id=req.get("id"),
                attempt_id=attempt_id,
                session_id=session_id,
                attempt_started_at=attempt_started_at,
                artifact_baseline=artifact_baseline,
            )
        except WorkEvidenceError as evidence_error:
            # The subprocess really did exit zero. Preserve that exact fact in
            # the attempt/session rows, but do not let process success settle
            # work that has no reviewable evidence.
            outcome_result["work_outcome"] = EVIDENCE_FAILURE_OUTCOME
            outcome_result["evidence_failure"] = {
                "status": "pending",
                "reasons": evidence_error.reasons,
                "evidence": evidence_error.evidence,
            }
            attempt_saved = db.complete_attempt(
                attempt_id, "SUCCEEDED", exit_code=0, result=outcome_result
            )
            if not attempt_saved:
                raise RuntimeError(
                    f"attempt completion rejected because it is already terminal (attempt={attempt_id})"
                )
            attempt_outcome_saved = True
            try:
                outcome_result = _persist_evidence_failure(
                    db, request_id, plan_id, session_id, ticket_id, attempt_id,
                    req.get("id", ""), used_model, outcome_result, evidence_error,
                )
            except Exception as persist_error:  # noqa: BLE001
                _log.error(
                    "evidence failure persisted only on attempt; request remains unsettled "
                    "request=%s attempt=%s: %s", request_id, attempt_id, persist_error,
                )
            if outcome_result.get("non_runnable"):
                _release_lease_safely(db, lease_id)
                worker_session_exit_code = 0
                return "evidence-failed-persistence"
            _release_lease_safely(db, lease_id)
            worker_session_exit_code = 0
            _log_warn(f"request {request_id} evidence_failed (explicit requeue required)")
            return "evidence-failed"
        attempt_saved = db.complete_attempt(
            attempt_id, "SUCCEEDED", exit_code=0, result=outcome_result
        )
        if not attempt_saved:
            raise RuntimeError(
                f"attempt completion rejected because it is already terminal (attempt={attempt_id})"
            )
        attempt_outcome_saved = True
        _ensure_success_execution_evidence(
            db, attempt_id, request_id, outcome_result, executor_id
        )
        _release_lease_safely(db, lease_id)
        _log_ok(f"request {request_id} COMPLETED")

        if plan_id:
            try:
                outcome_result = _finalize_governance_row(db, {
                    "attempt_id": attempt_id,
                    "request_id": request_id,
                    "source_plan_id": plan_id,
                    "result": outcome_result,
                    "attempt_status": "SUCCEEDED",
                    "exit_code": 0,
                })
            except Exception as e:  # noqa: BLE001
                _log.error(
                    "execution completed; plan governance finalization pending "
                    "request=%s plan=%s session=%s attempt=%s: %s",
                    request_id, plan_id, session_id, attempt_id, e,
                )
                try:
                    outcome_result = _set_governance_finalization(
                        db, attempt_id, outcome_result, "pending", str(e),
                        review_criterion=getattr(e, "review_criterion", None),
                    )
                except Exception as persist_error:  # noqa: BLE001
                    # The initial successful attempt result already carries
                    # status=pending, so a future pass can still reconcile it.
                    _log.error("could not persist governance error attempt=%s: %s",
                               attempt_id, persist_error)
            # ── consumed_units tracking (RoleLeases plan 1286) ──────
            if outcome_result.get("governance_finalization", {}).get("status") == "complete":
                # Unified accounting: POST /api/role-leases/consume (canonical endpoint)
                try:
                    _body = json.dumps({"role": ROLE}).encode()
                    _req = urllib.request.Request(
                        "http://localhost:3101/api/role-leases/consume",
                        data=_body,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(_req, timeout=5):
                        pass
                except Exception as e:  # noqa: BLE001
                    _log.warning("consumed_units update failed: %s", e)
        else:
            outcome_result = _set_governance_finalization(
                db, attempt_id, outcome_result, "not_applicable"
            )
        renewer_stop.set()
        if outcome_result.get("governance_finalization", {}).get("status") == "pending":
            return "finalization-pending"
        worker_session_exit_code = 0
        return "completed"

    except Exception as e:  # noqa: BLE001
        _log.error("execution failed request=%s error=%s", request_id, e)
        renewer_stop.set()
        if attempt_outcome_saved:
            # Preserve the builder/process success. Any persistence error
            # after that point must not rewrite it as a failed attempt or
            # create a contradictory plan BLOCK.
            _release_lease_safely(db, lease_id)
            try:
                _ensure_success_execution_evidence(
                    db, attempt_id, request_id, outcome_result, executor_id
                )
                if plan_id:
                    try:
                        outcome_result = _set_governance_finalization(
                            db, attempt_id, outcome_result, "pending", str(e),
                            review_criterion=getattr(e, "review_criterion", None),
                        )
                    except Exception as persist_error:  # noqa: BLE001
                        _log.error("could not persist pending governance attempt=%s: %s",
                                   attempt_id, persist_error)
                else:
                    try:
                        outcome_result = _set_governance_finalization(
                            db, attempt_id, outcome_result, "not_applicable"
                        )
                    except Exception:  # noqa: BLE001
                        pass
                _log.error("successful attempt retained; governance remains pending request=%s", request_id)
                return (
                    "finalization-pending"
                    if outcome_result.get("governance_finalization", {}).get("status") == "pending"
                    else "completed"
                )
            except Exception as completion_error:  # noqa: BLE001
                _log.error("could not restore completed request status request=%s: %s",
                           request_id, completion_error)
                try:
                    _set_governance_finalization(
                        db, attempt_id, outcome_result, "pending", str(completion_error)
                    )
                except Exception as persist_error:  # noqa: BLE001
                    _log.error("could not persist recovery pending state attempt=%s: %s",
                               attempt_id, persist_error)
                return "finalization-pending"

        process_outcome = getattr(e, "outcome", None)
        if process_outcome is None and last_process_outcome:
            process_outcome = executor_cloud.HarnessExecutionResult(
                last_process_outcome.get("returncode"),
                _text_value(last_process_outcome.get("stdout", "")),
                _text_value(last_process_outcome.get("stderr", "")),
            )
        exact_exit_code = getattr(process_outcome, "returncode", None)
        if exact_exit_code not in (None, 0):
            worker_session_exit_code = exact_exit_code
        if attempt_id:
            failure_result = {
                "work_request_id": None,
                "process_outcome": {
                    "returncode": exact_exit_code,
                    "stdout": _bounded_text(getattr(process_outcome, "stdout", "")),
                    "stderr": _bounded_text(getattr(process_outcome, "stderr", "")),
                    "session_log_path": session_log_path,
                },
                "model_attempts": model_attempts,
                "session_log_path": session_log_path,
                "session_id": session_id,
                "plan_id": plan_id,
                "ticket_id": ticket_id,
                "request_id": str(request_id),
                "governance_finalization": {"status": "not_applicable", "error": None},
            }
            attempt_saved = db.complete_attempt(
                attempt_id, "FAILED", exit_code=exact_exit_code,
                error=str(e), result=failure_result,
            )
            if not attempt_saved:
                _log.error(
                    "attempt is already terminal; preserving stored status/exit_code "
                    "and skipping BLOCK request=%s attempt=%s",
                    request_id, attempt_id,
                )
                _release_lease_safely(db, lease_id)
                return "attempt-terminal"
        if plan_id:
            db.insert_receipt(
                plan_id=plan_id, receipt_type="BLOCK", agent_role=ROLE,
                session_id=session_id, ticket_id=ticket_id or "",
                summary=f"execution_worker failure: {e}",
                metadata={"request_id": request_id, "executor": executor_id},
            )
            db.close_ticket(plan_id, ROLE, session_id, "failed")
            db.advance_cursor(ROLE, plan_id, str(request_id))
        try:
            db.release_lease(lease_id)
        except Exception:  # noqa: BLE001
            pass
        with db._get_connection() as conn:
            conn.execute("UPDATE execution.requests SET status = 'FAILED', updated_at = NOW() WHERE id = %s", (request_id,))
            conn.commit()
        _log_warn(f"request {request_id} FAILED")
        return "failed"
    finally:
        renewer_stop.set()
        if session_created:
            _close_worker_session_safely(
                db, session_id, worker_session_exit_code
            )


def _recover_orphans(db: DBAdapter) -> int:
    """Self-heal after a crashed run.

    - Expire stale ACTIVE leases (past TTL).
    - Release 'claimed' builder tickets whose session no longer has an
      active lease on the request (i.e., the worker died mid-run).

    Returns number of released tickets.
    """
    try:
        expired = db.expire_stale_leases()
        if expired:
            _log.info("recovered: expired %d stale lease(s)", expired)
    except Exception as e:  # noqa: BLE001
        _log.warning("expire_stale_leases failed: %s", e)

    released = 0
    try:
        with db._get_connection() as conn:
            # Claimed tickets where the claiming session has no ACTIVE lease
            # on the same request (orphaned by a killed worker).
            cursor = conn.execute(
                """SELECT t.id AS ticket_id, t.plan_id, t.session_id
                   FROM vision.tickets t
                   WHERE t.role = 'builder' AND t.status = 'claimed'
                     AND t.session_id IS NOT NULL
                     AND NOT EXISTS (
                         SELECT 1 FROM execution.leases l
                         JOIN execution.attempts a ON a.lease_id = l.id
                         WHERE a.executor_id = 'conduit-worker'
                           AND l.status = 'ACTIVE'
                           AND l.request_id IN (
                               SELECT id FROM execution.requests
                               WHERE source_plan_id = t.plan_id
                           )
                     )
                   LIMIT 20"""
            )
            rows = cursor.dict_fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE vision.tickets SET status='open', session_id=NULL, "
                    "claimed_at=NULL, last_activity=NOW() WHERE id = %s AND status='claimed'",
                    (row["ticket_id"],),
                )
                released += 1
            conn.commit()
        if released:
            _log.info("recovered: released %d orphaned builder ticket(s)", released)
    except Exception as e:  # noqa: BLE001
        _log.warning("orphaned-ticket recovery failed: %s", e)
    return released


def _role_lease_blocked(role: str) -> str | None:
    """Return a denial reason (LEASE_EXPIRED/LEASE_EXHAUSTED) if the role's
    ACTIVE lease is expired/exhausted, else None (allowed or no lease).

    T20 two-tier governance: lease quota gates the WORKER POOL (this path).
    Scheduled/system-triggered runs are governed by admission (config
    validity) in harness-srv, not by lease expiry here.
    """
    try:
        # D-008 R2: lease-derived REVOKED blocks the worker pool; NEVER_LEASED
        # is advisory (warn-and-proceed).
        try:
            status_url = f"http://localhost:3101/api/role-leases/{role}/status"
            status_req = urllib.request.Request(status_url, method="GET")
            with urllib.request.urlopen(status_req, timeout=5) as resp:
                status = json.loads(resp.read())
            state = status.get("state")
            if state == "REVOKED":
                return "ROLE_REVOKED"
            if state == "NEVER_LEASED":
                _log.info("role %s is NEVER_LEASED — advisory (warn-and-proceed)", role)
        except Exception as e:  # noqa: BLE001
            _log.warning("role-lease status check failed (proceeding): %s", e)

        url = f"http://localhost:3101/api/role-leases?role={role}"
        req = urllib.request.Request(url, headers={"Content-Type": "application/json"}, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        now = datetime.now(timezone.utc)
        for item in data.get("items", []):
            if item.get("status") != "ACTIVE":
                continue
            # D-007 R4: interactive-channel leases are consumed by the
            # interactive turn subscriber, not the worker pool. The worker
            # gate honors {opencode, ollama, unknown} only.
            if item.get("channel") == "interactive":
                continue
            window_end = item.get("window_end")
            budget = item.get("budget_units")
            consumed = item.get("consumed_units", 0)
            if window_end:
                try:
                    we = datetime.fromisoformat(str(window_end).replace("Z", "+00:00"))
                    if we < now:
                        return "LEASE_EXPIRED"
                except ValueError:
                    pass
            if budget is not None and consumed >= budget:
                return "LEASE_EXHAUSTED"
            break
        return None
    except Exception as e:  # noqa: BLE001
        _log.warning("role-lease check failed (proceeding): %s", e)
        return None


def _run_pass(db: DBAdapter, executor_id: str, dry_run: bool, include_legacy: bool, model: str = "") -> dict:
    """One full pass: recover + reconcile + consume. Returns summary."""
    summary = {"recovered": 0, "governance_finalized": 0,
               "finalization_pending": 0, "session_mismatches": 0,
               "reconciled": 0, "consumed": 0, "evidence_failed": 0,
               "skipped_leased": 0, "skipped_admission_race": 0, "failed": 0,
               "dry_run": 0, "lease_blocked": None}

    # 0. Self-heal after a crashed run
    summary["recovered"] = _recover_orphans(db)

    # 0.25 Surface session mismatches first so any unsafe reviewer-ticket
    # criterion update is persisted as pending before governance retry runs.
    if not dry_run:
        summary["session_mismatches"] = _surface_session_exit_observations(db)
        # Reconcile completed attempts without reacquiring a lease or launching.
        (summary["governance_finalized"],
         summary["finalization_pending"]) = _retry_pending_governance_finalizations(db)

    # 0.75 Role-lease gate (T20 B3): an expired/exhausted role lease blocks
    #     worker-pool consumption — skip reconcile + consume this pass.
    blocked = _role_lease_blocked(ROLE)
    if blocked:
        summary["lease_blocked"] = blocked
        _log.warning("role lease %s — skipping reconcile + READY consumption (role=%s)", blocked, ROLE)
        return summary

    # 1. Reconcile eligible plans → READY requests
    try:
        reconciled = _reconcile_plans(db)
        summary["reconciled"] = len(reconciled)
        if reconciled:
            _log.info("reconciled %d plan(s) into READY execution requests", len(reconciled))
    except Exception as e:  # noqa: BLE001
        _log.error("reconcile failed: %s", e)

    # 2. Eligible plan ids = plans with open builder ticket + correct derived status
    eligible_plan_ids = set()
    try:
        eligible = db.get_eligible_plans(ROLE)
        eligible_plan_ids = {p["id"] for p in eligible}
    except Exception as e:  # noqa: BLE001
        _log.error("eligible-plans query failed: %s", e)

    # 3. Consume READY requests
    rows = _list_ready(db, include_legacy, eligible_plan_ids)
    if rows:
        _log.info("found %d READY request(s)", len(rows))
    for row in rows:
        outcome = _process_one(db, row, executor_id, dry_run, model=model)
        if outcome == "completed":
            summary["consumed"] += 1
        elif outcome == "finalization-pending":
            summary["finalization_pending"] += 1
        elif outcome == "failed":
            summary["failed"] += 1
        elif outcome == "evidence-failed":
            summary["evidence_failed"] += 1
        elif outcome == "leased":
            summary["skipped_leased"] += 1
        elif outcome == "admission-race":
            summary["skipped_admission_race"] += 1
        elif outcome == "dry-run":
            summary["dry_run"] += 1

    return summary


def _show_status(db: DBAdapter) -> None:
    print("=== execution.requests status ===\n")
    for status in ("READY", "COMPLETED", "FAILED", "VALIDATED", "ADMITTED", "DRAFT"):
        try:
            rows = db.get_requests_by_status(status, limit=500)
        except Exception:  # noqa: BLE001
            continue
        plan_backed = sum(1 for r in rows if r.get("source_plan_id"))
        print(f"{status:<10} {len(rows):>5}  (plan-backed: {plan_backed})")
    print()
    print("=== eligible builder plans (reconcile would pick these up) ===")
    try:
        plans = db.get_eligible_plans(ROLE)
        if plans:
            for p in plans:
                print(f"  plan {p['id']}: {p.get('title', '')[:70]}")
        else:
            print("  (none)")
    except Exception as e:  # noqa: BLE001
        print(f"  error: {e}")
    print()
    try:
        chain = _resolve_model_chain(db, ROLE)
        print("model chain (tackle):")
        for e in chain:
            marker = "  primary" if e["priority"] == -1 else f"  fallback#{e['priority']}"
            print(f"  {marker} {e['harness']:>9}  {e['model']}")
    except Exception as e:  # noqa: BLE001
        print(f"  model chain error: {e}")


def main():
    parser = argparse.ArgumentParser(description="Conduit execution worker — consumes execution.requests READY")
    parser.add_argument("--status", action="store_true", help="Show READY backlog + eligible plans")
    parser.add_argument("--dry-run", action="store_true", help="Reconcile + report, do not launch agents")
    parser.add_argument("--once", action="store_true", help="Run a single pass")
    parser.add_argument("--loop", action="store_true", help="Run as daemon (default)")
    parser.add_argument("--interval", type=int, default=60, help="Poll interval seconds (default 60)")
    parser.add_argument("--executor-id", default="conduit-worker", help="Executor identity (default: conduit-worker)")
    parser.add_argument("--include-legacy", action="store_true", help="Also consume non-plan READY rows (default: plan-backed only)")
    parser.add_argument("--model", default=os.environ.get("PIPELINE_MODEL", ""),
                        help="Hard model override (e.g. ollama/qwen2.5-coder-ctx32k) — "
                             "used as a single-entry chain, bypassing the tackle "
                             "fallbacks. Omit to walk the tackle chain "
                             "(primary NVIDIA glm-5.2 → fallbacks → Ollama). "
                             "Defaults to $PIPELINE_MODEL if set.")
    args = parser.parse_args()

    db = DBAdapter("")

    if args.status:
        _show_status(db)
        return

    if args.dry_run:
        summary = _run_pass(db, args.executor_id, dry_run=True, include_legacy=args.include_legacy, model=args.model)
        print(f"\nDRY-RUN summary: {json.dumps(summary)}")
        return

    if args.once:
        summary = _run_pass(db, args.executor_id, dry_run=False, include_legacy=args.include_legacy, model=args.model)
        print(f"\nPass summary: {json.dumps(summary)}")
        return

    # Default: loop
    _log.info("execution worker starting (executor=%s, interval=%ds, include_legacy=%s, model=%s)",
              args.executor_id, args.interval, args.include_legacy, args.model or "(tackle config)")
    while True:
        try:
            summary = _run_pass(db, args.executor_id, dry_run=False, include_legacy=args.include_legacy, model=args.model)
            _log.info("pass summary: %s", json.dumps(summary))
        except Exception as e:  # noqa: BLE001
            _log.error("pass error: %s", e)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
