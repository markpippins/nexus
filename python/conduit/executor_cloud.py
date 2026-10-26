import json
import logging
import os
import re
import select
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict

try:
    from urllib.request import Request, urlopen
    from urllib.error import URLError
except ImportError:
    from urllib2 import Request, urlopen, URLError  # type: ignore[no-redef]

from env_config import load_env  # shared .env loader; load_env() fires at import time

from token_estimator import estimate_tokens
from work_request import WorkRequestDCO  # Pydantic model for DCO validation


# ── Module-level logger ─────────────────────────────────────────────
_log = logging.getLogger("conduit.executor_cloud")

# ── Env vars loaded above ───────────────────────────────────────────

OPENCODE_BIN = os.environ.get("OPENCODE_BIN", "/home/codex/.opencode/bin/opencode")
# Timeout for external harness invocations (30 min default).
# This is a last-resort safety valve — the conduit watchdog
# handles the primary timeout, but this ensures executor_cloud.py itself
# doesn't hang forever even when invoked outside the conduit.
OPENCODE_TIMEOUT_SECONDS = int(os.environ.get("PIPELINE_EXECUTOR_TIMEOUT", "1800"))

# Default harness semantic definitions (used when DB is not available).
# These match the seed defaults in nexus/typescript/conduit-mcp/src/db.ts.
_OPencode_SEMANTICS = {
    "binary": OPENCODE_BIN,  # full path from env or fallback (not bare "opencode" which may not be in PATH)
    "capabilities": {"model": True, "agent": True, "working_directory": True, "system_prompt": False},
    "execution": {"mode": "interactive", "subcommand": "run"},
    "semantics": {
        "model": {"type": "flag", "flag": "--model"},
        "agent": {"type": "flag", "flag": "--agent"},
        "working_directory": {"type": "flag", "flag": "--dir"},
    },
    "role_mapping": {"strategy": "agent"},
}

try:
    import ollama
except ImportError:
    ollama = None  # optional — only needed when harness is ollama


MCP_BASE_URL = os.environ.get("MCP_BASE_URL", "http://localhost:3100")
HEARTBEAT_INTERVAL_SECONDS = int(os.environ.get("PIPELINE_HEARTBEAT_INTERVAL", "30"))


@dataclass(frozen=True)
class HarnessExecutionResult:
    """Outcome observed directly from one harness subprocess."""

    returncode: int | None
    stdout: str = ""
    stderr: str = ""


class HarnessExecutionError(RuntimeError):
    """Harness failure carrying the subprocess outcome without lossy coercion."""

    def __init__(self, message: str, outcome: HarnessExecutionResult):
        super().__init__(message)
        self.outcome = outcome
        self.returncode = outcome.returncode
        self.stdout = outcome.stdout
        self.stderr = outcome.stderr


def _capture_session_cost(session_id: str, opencode_bin: str) -> None:
    """Run `opencode stats --days 1` and POST the total cost to the MCP server."""
    if not session_id:
        return

    _log.debug("_capture_session_cost: entry session=%s", session_id)
    try:
        result = subprocess.run(
            [opencode_bin, "stats", "--days", "1"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = result.stdout or ""
        stderr_output = result.stderr or ""

        if result.returncode != 0:
            _log.warning("_capture_session_cost: non-zero exit=%d session=%s stderr=%s",
                         result.returncode, session_id, stderr_output[:200])
            return

        # Parse "Total Cost                                       $13.48"
        match = re.search(r"Total Cost\s+\$?([\d.]+)", output)
        if not match:
            if stderr_output:
                _log.debug("_capture_session_cost: no cost match, stderr=%s", stderr_output[:200])
            else:
                _log.debug("_capture_session_cost: no cost match in stats output")
            return
        cost_usd = float(match.group(1))

        payload = json.dumps({"cost_usd": cost_usd}).encode("utf-8")
        req = Request(
            f"{MCP_BASE_URL}/sessions/{session_id}/cost",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urlopen(req, timeout=5)
        print(f"[cost] session={session_id} cost_usd={cost_usd}")
        _log.info("_capture_session_cost: session=%s cost_usd=%.2f", session_id, cost_usd)
    except Exception as e:
        print(f"[cost] Failed to capture cost for {session_id}: {type(e).__name__}: {e}", file=sys.stderr)
        _log.warning("_capture_session_cost: failed session=%s error=%s", session_id, e)


def _send_heartbeat(session_id: str, role: str, pid: int | None) -> None:
    """POST a liveness heartbeat to the conduit MCP server."""
    if not session_id:
        return
    try:
        payload = json.dumps({
            "role": role,
            "state": "working",
            "pid": pid,
        }).encode("utf-8")
        req = Request(
            f"{MCP_BASE_URL}/sessions/{session_id}/heartbeat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urlopen(req, timeout=5)
    except Exception as e:
        # Heartbeat failures are non-fatal — log and continue
        _log.debug("_send_heartbeat: failed session=%s error=%s", session_id, e)


def _resolve_harness(req: Dict[str, Any]) -> str:
    """Extract the harness from DCO metadata (defaults to 'opencode').

    Supports both logical harness names (``opencode``, ``ollama``, ``codex``)
    and binary paths (e.g. ``/home/codex/.opencode/bin/opencode``), extracting
    the base name in the latter case.
    """
    harness = (req.get("metadata") or {}).get("harness", "opencode")
    # If harness is a binary path, extract basename (e.g. "/path/to/opencode" → "opencode")
    if "/" in harness:
        harness = os.path.basename(harness)
    resolved = harness if harness in ("opencode", "ollama", "codex") else "opencode"
    _log.debug("_resolve_harness: raw=%s resolved=%s", harness, resolved)
    return resolved


def _ensure_provider_prefix(model_id: str, role: str) -> str:
    """Return a provider-prefixed model ID suitable for opencode.

    opencode expects ``provider/model`` (e.g. ``ollama/qwen2.5-coder``).
    Identifiers that already carry a slash (fully-qualified IDs shipped
    by the pipeline's ``_resolve_model_chain``) pass through unchanged.
    For bare identifiers, resolve the role's provider slug via tackle and
    prepend it (delegates to ``provider_prefix_slug`` so the priority
    matches the pipeline: provider_name → provider_type → provider_id).

    Using the wrong prefix produces ``ProviderModelNotFoundError``.
    """
    if not model_id or "/" in model_id:
        return model_id
    try:
        from tackle.db import get_role_config
        from db_adapter import provider_prefix_slug, qualify_opencode_model_id
        cfg = get_role_config(role)
        slug = provider_prefix_slug(
            (cfg or {}).get("provider_name", ""),
            (cfg or {}).get("provider_type", ""),
            (cfg or {}).get("provider_id", ""),
        )
        return qualify_opencode_model_id(model_id, slug)
    except Exception as e:
        _log.debug("_ensure_provider_prefix: tackle lookup failed role=%s error=%s", role, e)
    return model_id


def _resolve_model_name(req: Dict[str, Any]) -> str:
    """Extract the model name from DCO metadata.

    Falls back to DB ai_role_config lookup, then PIPELINE_MODEL env var.
    v110: Ensures opencode-compatible model IDs include the provider prefix
    (e.g. ``ollama/qwen2.5-coder``) when the raw identifier lacks one.
    """
    role = (req.get("metadata") or {}).get("role", "")

    explicit = (req.get("metadata") or {}).get("model", "")
    if explicit:
        model_id = _ensure_provider_prefix(explicit, role)
        _log.debug("_resolve_model_name: explicit model=%s resolved=%s", explicit, model_id)
        return model_id

    # Fallback 1: DB-backed role config via tackle (authoritative AI config)
    if role:
        try:
            from tackle.db import get_role_config
            cfg = get_role_config(role)
            if cfg:
                from db_adapter import provider_prefix_slug, qualify_opencode_model_id
                model_id = cfg.get("model_identifier", "")
                slug = provider_prefix_slug(
                    cfg.get("provider_name", ""),
                    cfg.get("provider_type", ""),
                    cfg.get("provider_id", ""),
                )
                model_id = qualify_opencode_model_id(model_id, slug)
                if model_id:
                    _log.debug("_resolve_model_name: tackle lookup role=%s model=%s", role, model_id)
                    return model_id
        except Exception as e:
            print(f"[ai-config] tackle role config lookup failed: {e}", file=sys.stderr)
            _log.warning("_resolve_model_name: tackle lookup failed role=%s error=%s", role, e)

        # Legacy fallback via DBAdapter if tackle is unreachable
        try:
            from db_adapter import DBAdapter
            db = DBAdapter()
            cfg = db.get_role_model_config(role)
            if cfg and cfg.get("model"):
                model_id = _ensure_provider_prefix(cfg["model"], role)
                _log.debug("_resolve_model_name: DB lookup role=%s model=%s", role, model_id)
                return model_id
        except Exception as e:
            print(f"[ai-config] model lookup failed: {e}", file=sys.stderr)
            _log.warning("_resolve_model_name: DB lookup failed role=%s error=%s", role, e)

    # Fallback 2: env var
    env_model = os.environ.get("PIPELINE_MODEL", "")
    _log.debug("_resolve_model_name: env fallback model=%s", env_model)
    return _ensure_provider_prefix(env_model, role)


def _resolve_fallback_models(role: str) -> list[dict]:
    """Return fallback models with per-model provider/harness from ai_role_models.

    v098: Each entry includes provider_type, api_key, endpoint_url,
    harness_name, and invocation_semantics resolved from the model's
    per-model provider_id/harness_id (or falling back to the model's
    native provider/harness from ai_models).

    Used by the conduit's _dispatch_one for rate-limit / failure retries.
    Returns empty list if no fallbacks configured or DB unavailable.
    """
    _log.debug("_resolve_fallback_models: entry role=%s", role)
    try:
        from db_adapter import DBAdapter
        db = DBAdapter()
        models = db.get_fallback_models(role)
        _log.info("_resolve_fallback_models: role=%s count=%d", role, len(models))
        return models
    except Exception as e:
        print(f"[ai-config] fallback model lookup failed: {e}", file=sys.stderr)
        _log.warning("_resolve_fallback_models: failed role=%s error=%s", role, e)
        return []


def _build_default_opencode_launcher(role: str, model: str) -> 'HarnessLauncher':
    """Build a HarnessLauncher for opencode with default semantics.

    Used by executor_cloud when the DB is not available or when
    running outside the context of a role-config lookup.
    Callers should call ``launcher.set_working_directory(path)``
    and ``launcher.set_prompt(prompt)`` after construction.
    """
    from nexus_core.harness.launcher import HarnessLauncher
    launcher = HarnessLauncher.from_harness_row({
        "name": "opencode",
        "invocation_semantics": _OPencode_SEMANTICS,
    })
    launcher.set_agent(role)
    if model:
        launcher.set_model(model)
    _log.debug("_build_default_opencode_launcher: role=%s model=%s", role, model or "(none)")
    return launcher


def run_ollama(req, system_base, prompt_body, session_log_path=None):
    if ollama is None:
        _log.error("run_ollama: ollama package not installed")
        raise RuntimeError("ollama package is not installed — cannot use ollama harness")
    # The ollama harness talks to the local ollama server directly, which
    # expects bare model names (e.g. "qwen2.5-coder:latest"). Prefer the DCO's
    # explicit metadata.model verbatim (stripping an opencode-style
    # "ollama/" provider prefix if present) — role-config resolution returns
    # opencode-qualified IDs (e.g. "nvidia/z-ai/glm-5.2") that local ollama
    # cannot serve. Fall back to that resolution only when no explicit model
    # is set (finding 8b1a8623, option D).
    explicit = (req.get("metadata") or {}).get("model", "")
    if explicit:
        model = explicit[len("ollama/") :] if explicit.startswith("ollama/") else explicit
        _log.info("run_ollama: explicit model=%s → ollama model=%s", explicit, model)
    else:
        model = _resolve_model_name(req)
    _log.info("run_ollama: entry model=%s prompt_len=%d", model, len(prompt_body) if prompt_body else 0)

    # Retry loop: local models sometimes produce empty output on first attempt.
    # Retry up to 2 times with slightly different parameters.
    for attempt in range(1, 3):
        _write_session_log(session_log_path, f"[ollama] model={model} attempt={attempt} generating...\n")
        _log.debug("run_ollama: attempt=%d model=%s", attempt, model)
        options = {"num_predict": 2000}
        if attempt > 1:
            # Second attempt: increase output token limit
            options = {"num_predict": 4000}

        response = ollama.generate(
            model=model,
            system=system_base,
            prompt=prompt_body,
            options=options,
        )
        # ollama SDK >= 0.5.0 returns a GenerateResponse dataclass, not a dict
        if isinstance(response, dict):
            result = response.get("response")
        else:
            result = getattr(response, "response", None)

        if result and result.strip():
            truncated = result[:200] + ("..." if len(result) > 200 else "")
            _write_session_log(session_log_path, f"[ollama] output ({len(result)} chars)\n{truncated}\n")
            _log.info("run_ollama: success model=%s chars=%d attempt=%d", model, len(result), attempt)
            return result

        _write_session_log(session_log_path, f"[ollama] attempt {attempt}: no output\n")
        _log.warning("run_ollama: empty output model=%s attempt=%d", model, attempt)

    _write_session_log(session_log_path, "[ollama] all attempts exhausted, no output produced\n")
    _log.warning("run_ollama: all attempts exhausted model=%s", model)
    return None


def _write_session_log(session_log_path, text):
    """Append a line to the session log if a path is provided."""
    if not session_log_path:
        return
    try:
        os.makedirs(os.path.dirname(session_log_path), exist_ok=True)
        with open(session_log_path, "a", encoding="utf-8") as f:
            f.write(text)
    except Exception as exc:
        _log.warning("_write_session_log: write failed path=%s error=%s", session_log_path, exc)


def _serialize_dco_for_prompt(req: Dict[str, Any]) -> str:
    """Render the full WorkRequest DCO as structured text for the agent."""
    intent = req.get("intent", {})
    decomposition = req.get("decomposition", {})
    requirements = req.get("requirements", {})
    constraints = req.get("constraints", {})
    success = req.get("success_criteria", {})
    artifacts = req.get("artifacts", {})
    lineage = req.get("lineage", {})
    meta = req.get("metadata", {})

    def _kv(k, v):
        return f"  - **{k}:** {v}"

    blocks: list[str] = []

    # ── Intent ──
    blocks.append("## Intent")
    if intent.get("problem_statement"):
        blocks.append(_kv("Problem", intent["problem_statement"]))
    if intent.get("desired_outcome"):
        blocks.append(_kv("Outcome", intent["desired_outcome"]))
    blocks.append(_kv("Priority", intent.get("priority", "medium")))
    blocks.append(_kv("Abstraction", intent.get("abstraction_level", "task")))
    if intent.get("user_intent_trace"):
        blocks.append(_kv("Prompt ref", intent["user_intent_trace"]))

    # ── Decomposition ──
    steps = decomposition.get("steps", [])
    blocks.append("\n## Decomposition")
    blocks.append(_kv("Strategy", decomposition.get("strategy", "")))
    for i, s in enumerate(steps, 1):
        blocks.append(f"  **Step {i}** [{s.get('type', 'execution')}]: {s.get('description', '')[:300]}")

    # ── Requirements ──
    func = requirements.get("functional", [])
    if func:
        blocks.append("\n## Requirements (functional)")
        for ac in func:
            blocks.append(f"  - {ac}")

    # ── Constraints ──
    safety = constraints.get("safety_constraints", [])
    if safety:
        blocks.append("\n## Constraints")
        for sc in safety:
            blocks.append(f"  - {sc}")

    # ── Success Criteria ──
    conditions = success.get("completion_conditions", [])
    if conditions:
        blocks.append("\n## Success Criteria")
        for c in conditions:
            blocks.append(f"  - {c.get('condition', '')}")

    # ── Artifacts (target files) ──
    files = artifacts.get("produced_files", [])
    if files:
        blocks.append("\n## Target Files")
        for f in files:
            blocks.append(f"  - {f.get('path', '?')}")

    # ── Lineage ──
    derived = lineage.get("derived_from", [])
    if derived:
        blocks.append(f"\n## Lineage: derived from {', '.join(derived)}")

    # ── Metadata ──
    tags = meta.get("tags", [])
    if tags:
        blocks.append(f"\n## Metadata: tags={', '.join(tags)}")

    return "\n".join(blocks)


def _resolve_role(req: Dict[str, Any]) -> str:
    """Extract the agent role from DCO metadata (defaults to 'builder')."""
    role = (req.get("metadata") or {}).get("role", "")
    resolved = role if role in ("builder", "reviewer", "planner", "critic") else "builder"
    _log.debug("_resolve_role: raw=%s resolved=%s", role, resolved)
    return resolved


def _build_role_instructions(role: str) -> str:
    """Return the role-specific instruction block for agent prompts."""
    if role == "builder":
        return (
            "Execute this WorkRequest. Implement the plan, modifying only "
            "the files listed in Target Files. Satisfy all acceptance criteria "
            "and completion conditions. Respect all safety constraints."
        )
    elif role == "reviewer":
        return (
            "Review the implementation described in this WorkRequest. "
            "Compare the change report in CHANGES/committed/ against the plan. "
            "Apply every reviewer ticket completion criterion included in the "
            "WorkRequest acceptance criteria. If a linked attempt/session exit-code "
            "mismatch is marked review_required, do not issue REVIEW_PASS without "
            "an explicit disposition and evidence reference for each mismatch. "
            "Include them in receipt metadata as "
            "outcome_reconciliation.dispositions: [{attempt_id, session_id, "
            "disposition, evidence_ref}]. "
            "If changes match the acceptance criteria, issue a REVIEW_PASS receipt. "
            "If they don't match, issue a REVIEW_REJECT receipt with explanation."
        )
    elif role == "planner":
        return (
            "Elucidate the proposed plan in this WorkRequest. "
            "Define acceptance criteria, identify files affected, and note dependencies. "
            "When the plan is fully defined, issue a PLAN_CREATE receipt."
        )
    elif role == "critic":
        return (
            "Critique the plan in this WorkRequest. "
            "Evaluate the acceptance criteria, identify gaps, suggest improvements. "
            "Issue a CRITIQUE_PASS or CRITIQUE_REJECT receipt."
        )
    return ""


def _build_system_prompt(req: Dict[str, Any]) -> str:
    """Build a system-level prompt for harnesses that support system/instruction separation."""
    role = _resolve_role(req)
    parts = [_build_role_instructions(role)]
    parts.append("Do NOT issue receipts — the conduit manager handles the audit trail.")
    return "\n\n".join(parts)


def _build_opencode_prompt(
    req: Dict[str, Any],
    working_path: str,
    artifacts_dir: str | None = None,
) -> str:
    """Build a structured prompt for opencode from the full WorkRequest DCO."""
    role = _resolve_role(req)
    dco_text = _serialize_dco_for_prompt(req)
    lines = [dco_text, f"\n## Working directory\n{working_path}"]

    if artifacts_dir:
        lines.append(
            f"\n## Full DCO\nThe complete WorkRequest DCO is on disk at "
            f"{artifacts_dir}/request.json. Read it for full detail."
        )

    instructions = _build_role_instructions(role)
    if instructions:
        lines.extend(["\n## Instructions", instructions])

    lines.append("\nDo NOT issue receipts — the conduit manager handles the audit trail.")
    prompt = "\n".join(lines)
    model = _resolve_model_name(req)
    est = estimate_tokens(prompt, model)
    _log.info("_build_opencode_prompt: estimated %d tokens for model=%s role=%s", est, model, role)
    return prompt


def _run_harness_subprocess(
    cmd: list[str],
    session_log_path: str | None,
    timeout: int,
    tool_name: str,
    *,
    session_id: str = "",
    role: str = "",
) -> HarnessExecutionResult:
    """Run a harness CLI subprocess and return its complete process outcome.

    Shared helper for ``run_opencode``, ``run_codex``, and future harness
    functions.  Handles the full subprocess lifecycle:

    - Opens a session log file if ``session_log_path`` is provided
    - Launches ``cmd`` via ``subprocess.Popen`` with **separate stderr**
    - Reads stdout with a ``select.select()`` polling loop (1s tick)
    - Enforces ``timeout`` as a last-resort safety valve
    - Periodically POSTs to ``/sessions/:sessionId/heartbeat`` for agent liveness
    - Writes every output line to the log file in real time
    - On non-zero exit, writes stderr to the log file with ``[ERROR]`` prefix
    - Raises ``RuntimeError`` on launch failure, timeout, or non-zero exit
      with structured error info that includes stderr for diagnostics

    Args:
        cmd: The full CLI command list (binary + args).
        session_log_path: Optional path to append stdout to a log file.
        timeout: Maximum wall-clock seconds before the process is killed.
        tool_name: Human-readable name used in error messages (e.g., "opencode", "Codex").
        session_id: Session ID for heartbeat liveness reporting.
        role: Agent role for heartbeat liveness reporting.

    Returns:
        The return code, stdout, and stderr observed from the child process.
    """
    _log.info("_run_harness_subprocess: entry tool=%s cmd=%s timeout=%ds session=%s",
              tool_name, ' '.join(cmd), timeout, session_id or "(none)")
    log_fh = None
    if session_log_path:
        os.makedirs(os.path.dirname(session_log_path), exist_ok=True)
        log_fh = open(session_log_path, "a", encoding="utf-8")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        _log.debug("_run_harness_subprocess: launched tool=%s pid=%d", tool_name, proc.pid)
    except FileNotFoundError as e:
        err_msg = f"{tool_name} binary not found: {cmd[0] if cmd else '(empty cmd)'}"
        _log.error("_run_harness_subprocess: %s", err_msg)
        if log_fh:
            log_fh.write(f"[ERROR] {err_msg}\n")
            log_fh.flush()
            log_fh.close()
        raise RuntimeError(err_msg)
    except Exception as e:
        err_msg = f"Failed to launch {tool_name}: {type(e).__name__}: {e}"
        _log.error("_run_harness_subprocess: %s", err_msg)
        if log_fh:
            log_fh.write(f"[ERROR] {err_msg}\n")
            log_fh.flush()
            log_fh.close()
        raise RuntimeError(err_msg)

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    start_time = datetime.utcnow()
    heartbeat_ticks = 0

    try:
        # Read stdout + stderr in parallel via select to avoid deadlocks
        fds = [proc.stdout, proc.stderr]
        while True:
            ready, _, _ = select.select(fds, [], [], 1.0)
            if not ready:
                elapsed = (datetime.utcnow() - start_time).total_seconds()
                if elapsed > timeout:
                    _log.warning("_run_harness_subprocess: timeout tool=%s elapsed=%ds", tool_name, timeout)
                    proc.kill()
                    proc.wait()
                    raise subprocess.TimeoutExpired(cmd, timeout)
                if proc.poll() is not None:
                    break

                # ── Agent heartbeat (every HEARTBEAT_INTERVAL_SECONDS ticks) ──
                heartbeat_ticks += 1
                if session_id and heartbeat_ticks >= HEARTBEAT_INTERVAL_SECONDS:
                    heartbeat_ticks = 0
                    _send_heartbeat(session_id, role, proc.pid)

                continue

            got_data = False
            for stream in ready:
                line = stream.readline()
                if not line:
                    continue
                got_data = True

                if stream is proc.stdout:
                    stdout_lines.append(line)
                    if log_fh:
                        log_fh.write(line)
                        log_fh.flush()
                elif stream is proc.stderr:
                    stderr_lines.append(line)
                    # Write stderr to session log with [stderr] prefix so
                    # it's visible in the UI and never silently lost.
                    if log_fh:
                        log_fh.write(f"[stderr] {line}")
                        log_fh.flush()

            # Break when process is dead AND pipes are at EOF (no data)
            if not got_data and proc.poll() is not None:
                break

        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        _log.error("_run_harness_subprocess: timed out tool=%s timeout=%ds", tool_name, timeout)
        raise HarnessExecutionError(
            f"{tool_name} timed out after {timeout}s. "
            f"The conduit watchdog will clean up orphaned processes.",
            HarnessExecutionResult(
                returncode=proc.returncode,
                stdout="".join(stdout_lines),
                stderr="".join(stderr_lines),
            )
        )
    finally:
        if log_fh:
            log_fh.close()

    # Read any remaining stderr after process exit
    stderr_text = "".join(stderr_lines)
    try:
        stderr_text += proc.stderr.read()
    except Exception:
        pass

    if proc.returncode != 0:
        stdout_text = "".join(stdout_lines)
        output = stdout_text.strip()
        error_detail = output or f"exit code {proc.returncode}"
        if stderr_text:
            error_detail += f"\n[stderr]: {stderr_text.strip()}"

        # Detect common crash patterns in stderr for better diagnostics
        crash_hint = ""
        if stderr_text:
            stderr_lower = stderr_text.lower()
            if "nameerror" in stderr_lower:
                crash_hint = " (likely missing import or undefined variable)"
            elif "importerror" in stderr_lower or "modulenotfounderror" in stderr_lower:
                crash_hint = " (missing Python module)"
            elif "syntaxerror" in stderr_lower:
                crash_hint = " (syntax error in harness script)"
            elif "filenotfounderror" in stderr_lower:
                crash_hint = " (binary or file not found)"

        _log.warning("_run_harness_subprocess: non-zero exit tool=%s exit_code=%d%s stderr_len=%d",
                     tool_name, proc.returncode, crash_hint, len(stderr_text))
        raise HarnessExecutionError(
            f"{tool_name} invocation failed: {error_detail[:500]}{crash_hint}",
            HarnessExecutionResult(
                returncode=proc.returncode,
                stdout=stdout_text,
                stderr=stderr_text,
            ),
        )

    _log.info("_run_harness_subprocess: success tool=%s pid=%d lines=%d",
              tool_name, proc.pid, len(stdout_lines))
    return HarnessExecutionResult(
        returncode=proc.returncode,
        stdout="".join(stdout_lines),
        stderr=stderr_text,
    )


def run_opencode_outcome(req, working_path, artifacts_dir=None, session_log_path=None):
    """Run OpenCode and return its typed process outcome."""
    role = _resolve_role(req)
    prompt = _build_opencode_prompt(req, working_path, artifacts_dir)
    model = _resolve_model_name(req)
    _log.info("run_opencode: entry role=%s model=%s working_path=%s", role, model or "(none)", working_path)

    # Build command via HarnessLauncher — no hardcoded flags
    launcher = _build_default_opencode_launcher(role, model)
    launcher.set_working_directory(working_path)
    launcher.set_prompt(prompt)

    # For PROMPT_FILE strategy (e.g., Codex), write the role prompt to a file
    launcher.prepare_role_prompt_file()

    cmd = launcher.build()

    # Inject opencode-specific debug flags (harness-internal, not semantic)
    insert_pos = 2 if len(cmd) > 1 and cmd[1] == "run" else 1
    debug_flags = ["--print-logs", "--log-level", "DEBUG"]
    for f in debug_flags:
        cmd.insert(insert_pos, f)
        insert_pos += 1

    _log.debug("run_opencode: cmd=%s", ' '.join(cmd))
    session_id = (req.get("metadata") or {}).get("session_id", "")
    result = _run_harness_subprocess(
        cmd, session_log_path, OPENCODE_TIMEOUT_SECONDS, "opencode",
        session_id=session_id, role=role,
    )
    _log.info("run_opencode: exit role=%s chars=%d", role, len(result.stdout))
    return result


def run_opencode(req, working_path, artifacts_dir=None, session_log_path=None):
    """Compatibility wrapper returning stdout for legacy callers."""
    return run_opencode_outcome(req, working_path, artifacts_dir, session_log_path).stdout


def run_codex(req, working_path, artifacts_dir=None, session_log_path=None):
    """Run Codex CLI with prompt_file role injection strategy.

    Builds a HarnessLauncher with the codex semantic schema, writes the
    role prompt to a temp file, and invokes ``codex exec --cd PATH FILE``.
    """
    from nexus_core.harness.launcher import DEFAULT_BINARIES, HarnessLauncher
    from nexus_core.harness.enums import ExecutionMode, RoleMappingStrategy

    role = _resolve_role(req)
    prompt = _build_opencode_prompt(req, working_path, artifacts_dir)
    model = _resolve_model_name(req)
    _log.info("run_codex: entry role=%s model=%s working_path=%s", role, model or "(none)", working_path)

    # Codex semantics: oneshot, prompt_file, no model/agent CLI flags
    launcher = HarnessLauncher(
        binary=DEFAULT_BINARIES.get("codex", "codex"),
        capabilities={"model": False, "agent": False,
                       "working_directory": True, "system_prompt": True},
        execution_mode=ExecutionMode.ONESHOT,
        semantics={
            "working_directory": {"type": "flag", "flag": "--cd"},
        },
        role_mapping_strategy=RoleMappingStrategy.PROMPT_FILE,
        execution_data={"mode": "oneshot", "subcommand": "exec"},
    )
    launcher.set_agent(role)
    launcher.set_working_directory(working_path)
    launcher.set_prompt(prompt)
    launcher.prepare_role_prompt_file()

    cmd = launcher.build()
    _log.debug("run_codex: cmd=%s", ' '.join(cmd))
    session_id = (req.get("metadata") or {}).get("session_id", "")
    result = _run_harness_subprocess(
        cmd, session_log_path, OPENCODE_TIMEOUT_SECONDS, "Codex",
        session_id=session_id, role=role,
    )
    _log.info("run_codex: exit role=%s chars=%d", role, len(result.stdout))
    return result.stdout


def _run_from_path(dco_path: str) -> int:
    """CLI entry point: load a WorkRequest DCO from disk and execute it.

    ``main.py`` spawns ``executor_cloud.py <dco_path>`` for each plan.
    This function reads the JSON DCO, dispatches to the correct harness,
    and returns a process exit code that ``main.py`` maps to success or
    failure receipts.
    """
    try:
        with open(dco_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        _log.error("_run_from_path: failed to read DCO path=%s error=%s", dco_path, e)
        print(f"[executor] Failed to read DCO: {e}", file=sys.stderr)
        return 2

    try:
        dco = WorkRequestDCO.model_validate(raw)
    except Exception as e:
        _log.error("_run_from_path: DCO validation failed path=%s error=%s", dco_path, e)
        print(f"[executor] DCO validation failed: {e}", file=sys.stderr)
        return 2

    req = raw  # keep raw dict for backward-compat in helpers that use .get()
    wr_id = dco.id or os.path.splitext(os.path.basename(dco_path))[0]
    role = dco.metadata.role or _resolve_role(req)
    # Always normalize harness through _resolve_harness to handle both
    # logical names ("opencode") and binary paths ("/path/to/opencode")
    harness = _resolve_harness(req) or dco.metadata.harness or "opencode"
    working_path = os.path.abspath(dco.path)
    session_id = dco.metadata.session_id

    _log.info("_run_from_path: wr_id=%s role=%s harness=%s working_path=%s",
              wr_id, role, harness, working_path)

    session_log_path = None
    if session_id:
        session_log_path = os.path.join(
            os.environ.get("CONDUIT_DATA_DIR", os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "audit", "CONDUIT_DATA")),
            "session_logs",
            f"{session_id}.log",
        )

    # ── CCNF bridge (optional, feature-flagged) ─────────────────────
    _ccnf_cer_json: dict | None = None
    _ccnf_hash: str | None = None
    _ccnf_started_at: int | None = None
    _ccnf_artifacts_dir: str | None = None

    if os.environ.get("CONDUIT_USE_CCNF", "").lower() in ("1", "true"):
        from ccnf_bridge import (
            CCNFAdapter,
            CCNFBridgeError,
            call_ccnf_conformance,
        )

        # T21 (D-T21-1): emission is delegated to nexus_core.wrp.compile — the
        # bridge is a caller, not an emitter. No Go binary, no
        # CCNF_CONFORMANCE_BIN.
        try:
            _ccnf_input = CCNFAdapter.from_work_request(req)
            _ccnf_result = call_ccnf_conformance(_ccnf_input)
            _ccnf_cer_json = _ccnf_result.cer
            _ccnf_hash = _ccnf_result.hash
            _ccnf_started_at = int(time.time())
            _ccnf_artifacts_dir = os.path.join(working_path, ".artifacts")
            _log.info("[ccnf] anchored wr=%s hash=%s", wr_id, _ccnf_hash)
        except CCNFBridgeError as _e:
            _log.warning("[ccnf] bridge failed, continuing without CCNF: %s", _e)
            _ccnf_cer_json = None
            _ccnf_hash = None

    exit_code = 3
    try:
        if harness == "opencode":
            run_opencode(req, working_path, artifacts_dir=None, session_log_path=session_log_path)
        elif harness == "codex":
            run_codex(req, working_path, artifacts_dir=None, session_log_path=session_log_path)
        elif harness == "ollama":
            prompt_body = _build_opencode_prompt(req, working_path, artifacts_dir=None)
            system_prompt = _build_system_prompt(req)
            result = run_ollama(req, system_prompt, prompt_body, session_log_path=session_log_path)
            if result is None:
                raise RuntimeError("ollama produced no output")
        else:
            _log.error("_run_from_path: unsupported harness=%s", harness)
            print(f"[executor] Unsupported harness: {harness}", file=sys.stderr)
            return 3

        _log.info("_run_from_path: success wr_id=%s", wr_id)
        exit_code = 0
    except Exception as e:
        _log.error("_run_from_path: failed wr_id=%s error=%s", wr_id, e)
        print(f"[executor] Execution failed: {e}", file=sys.stderr)
        exit_code = 3
    finally:
        if session_id:
            try:
                _capture_session_cost(session_id, OPENCODE_BIN)
            except Exception as e:
                _log.warning("_run_from_path: cost capture failed session=%s error=%s", session_id, e)

    # ── Publish CCNF execution receipt (if bridge ran successfully) ──
    # Primary path: POST CCNF_EXECUTION receipt to kernel API.
    # Fallback: write to filesystem if kernel is unreachable.
    if _ccnf_cer_json is not None and _ccnf_started_at is not None and _ccnf_artifacts_dir is not None:
        from ccnf_bridge import CERBinder
        try:
            _completed_at = int(time.time())
            _receipt = CERBinder.attach_execution(
                cer_json=_ccnf_cer_json,
                session_id=session_id,
                plan_id="",
                wr_id=wr_id,
                status="SUCCESS" if exit_code == 0 else "FAILURE",
                started_at=_ccnf_started_at,
                completed_at=_completed_at,
            )

            # Build KernelDelta with CCNF_EXECUTION receipt
            _delta_payload = {
                "delta_id": f"ccnf-{wr_id}-{int(time.time())}",
                "batch_id": f"ccnf-bridge-{int(time.time())}",
                "receipts": [{
                    "id": f"rec-ccnf-{wr_id}-{int(time.time())}",
                    "type": "CCNF_EXECUTION",
                    "agent_role": "builder",
                    "plan_id": wr_id,
                    "summary": f"CCNF conformance: {_receipt.get('status', 'UNKNOWN')}",
                    "ccnf_hash": _receipt.get("ccnf_hash", ""),
                    "created_at": datetime.utcnow().isoformat() + "Z",
                    "metadata": _receipt,
                }],
                "affected_plans": [wr_id] if wr_id else [],
                "invalidated_plans": [],
            }

            _kernel_url = os.environ.get("KERNEL_API_URL", "http://localhost:3103")
            _post_url = f"{_kernel_url}/delta/"
            _post_data = json.dumps(_delta_payload).encode("utf-8")
            _post_req = Request(_post_url, data=_post_data, method="POST")
            _post_req.add_header("Content-Type", "application/json")

            try:
                with urlopen(_post_req, timeout=15) as _resp:
                    _body = json.loads(_resp.read().decode("utf-8"))
                    if _body.get("success"):
                        _log.info("[ccnf] receipt posted to kernel: delta=%s version=%d",
                                  _delta_payload["delta_id"], _body.get("version", 0))
                    else:
                        _log.warning("[ccnf] kernel rejected receipt: %s", _body.get("error", "unknown"))
            except URLError as _e:
                _log.warning("[ccnf] kernel unreachable (%s), falling back to filesystem artifact", _e)
                # Fallback: write to filesystem
                os.makedirs(_ccnf_artifacts_dir, exist_ok=True)
                _receipt_path = os.path.join(_ccnf_artifacts_dir, "execution_receipt.json")
                with open(_receipt_path, "w", encoding="utf-8") as _rf:
                    json.dump(_receipt, _rf, indent=2)
                _log.info("[ccnf] receipt written: %s", _receipt_path)

        except Exception as _e:
            _log.warning("[ccnf] bridge post failed: %s", _e)

    return exit_code


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: executor_cloud.py <dco_path>")
        sys.exit(1)
    sys.exit(_run_from_path(sys.argv[1]))
