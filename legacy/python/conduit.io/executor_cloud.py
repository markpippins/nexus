import json
import logging
import os
import re
import select
import subprocess
import sys
from datetime import datetime
from typing import Any, Dict

try:
    from urllib.request import Request, urlopen
    from urllib.error import URLError
except ImportError:
    from urllib2 import Request, urlopen, URLError  # type: ignore[no-redef]

from .prompt_renderer import build_opencode_prompt


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


def _resolve_harness(req: Dict[str, Any]) -> str:
    """Extract the harness from DCO metadata (defaults to 'opencode')."""
    harness = (req.get("metadata") or {}).get("harness", "opencode")
    resolved = harness if harness in ("opencode", "ollama", "codex") else "opencode"
    _log.debug("_resolve_harness: raw=%s resolved=%s", harness, resolved)
    return resolved


def _resolve_model_name(req: Dict[str, Any]) -> str:
    """Extract the model name from DCO metadata.

    Falls back to DB ai_role_config lookup, then PIPELINE_MODEL env var.
    """
    explicit = (req.get("metadata") or {}).get("model", "")
    if explicit:
        _log.debug("_resolve_model_name: explicit model=%s", explicit)
        return explicit

    # Fallback 1: DB-backed role config
    role = (req.get("metadata") or {}).get("role", "")
    if role:
        try:
            from db_adapter import DBAdapter
            db = DBAdapter()
            cfg = db.get_role_model_config(role)
            if cfg and cfg.get("model"):
                _log.debug("_resolve_model_name: DB lookup role=%s model=%s", role, cfg["model"])
                return cfg["model"]
        except Exception as e:
            print(f"[ai-config] model lookup failed: {e}", file=sys.stderr)
            _log.warning("_resolve_model_name: DB lookup failed role=%s error=%s", role, e)

    # Fallback 2: env var
    env_model = os.environ.get("PIPELINE_MODEL", "")
    _log.debug("_resolve_model_name: env fallback model=%s", env_model)
    return env_model


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
    from harness_launcher import HarnessLauncher
    launcher = HarnessLauncher.from_harness_row({
        "name": "opencode",
        "invocation_semantics": _OPencode_SEMANTICS,
    })
    launcher.set_agent(role)
    if model:
        launcher.set_model(model)
    _log.debug("_build_default_opencode_launcher: role=%s model=%s", role, model or "(none)")
    return launcher


def run_ollama(req, system_base, prompt_body, session_id=None):
    if ollama is None:
        _log.error("run_ollama: ollama package not installed")
        raise RuntimeError("ollama package is not installed — cannot use ollama harness")
    model = _resolve_model_name(req)
    _log.info("run_ollama: entry model=%s prompt_len=%d", model, len(prompt_body) if prompt_body else 0)

    # Retry loop: local models sometimes produce empty output on first attempt.
    # Retry up to 2 times with slightly different parameters.
    for attempt in range(1, 3):
        _write_session_log(session_id, f"[ollama] model={model} attempt={attempt} generating...\n")
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
            _write_session_log(session_id, f"[ollama] output ({len(result)} chars)\n{truncated}\n")
            _log.info("run_ollama: success model=%s chars=%d attempt=%d", model, len(result), attempt)
            return result

        _write_session_log(session_id, f"[ollama] attempt {attempt}: no output\n")
        _log.warning("run_ollama: empty output model=%s attempt=%d", model, attempt)

    _write_session_log(session_id, "[ollama] all attempts exhausted, no output produced\n")
    _log.warning("run_ollama: all attempts exhausted model=%s", model)
    return None


_session_db = None

def _get_session_db():
    global _session_db
    if _session_db is None:
        try:
            from db_adapter import DBAdapter
            _session_db = DBAdapter()
        except Exception as exc:
            _log.warning("_get_session_db: failed to init DB adapter: %s", exc)
    return _session_db


def _write_session_log(session_id, text, level="INFO"):
    """Append a line to the session_logs DB table if a session_id is provided."""
    if not session_id:
        return
    db = _get_session_db()
    if not db:
        return
    try:
        db.append_session_log(session_id, text, level)
    except Exception as exc:
        _log.warning("_write_session_log: DB write failed session=%s error=%s", session_id, exc)


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
    ALL_ROLES = {"builder", "reviewer", "planner", "critic", "analyst", "architect", "inspector", "engineer", "rover"}
    resolved = role if role in ALL_ROLES else "builder"
    _log.debug("_resolve_role: raw=%s resolved=%s", role, resolved)
    return resolved




def _run_harness_subprocess(
    cmd: list[str],
    session_id: str | None,
    timeout: int,
    tool_name: str,
) -> str:
    """Run a harness CLI subprocess and return stdout.

    Shared helper for ``run_opencode``, ``run_codex``, and future harness
    functions.  Handles the full subprocess lifecycle:

    - Batches log output and flushes to the ``session_logs`` DB table
    - Launches ``cmd`` via ``subprocess.Popen`` with **separate stderr**
    - Reads stdout with a ``select.select()`` polling loop (1s tick)
    - Enforces ``timeout`` as a last-resort safety valve
    - Writes every output line to the log table in near-real-time batches
    - On non-zero exit, writes stderr to the log with ``WARN`` level
    - Raises ``RuntimeError`` on launch failure, timeout, or non-zero exit
      with structured error info that includes stderr for diagnostics

    Args:
        cmd: The full CLI command list (binary + args).
        session_id: Optional session ID for DB log streaming.
        timeout: Maximum wall-clock seconds before the process is killed.
        tool_name: Human-readable name used in error messages (e.g., "opencode", "Codex").

    Returns:
        Combined stdout as a single string.
    """
    _log.info("_run_harness_subprocess: entry tool=%s cmd=%s timeout=%ds",
              tool_name, ' '.join(cmd), timeout)

    log_buffer: list[tuple[str, str]] = []

    def _flush_log():
        if not session_id or not log_buffer:
            return
        try:
            from db_adapter import DBAdapter
            db = DBAdapter()
            db.append_session_logs(session_id, log_buffer)
        except Exception as exc:
            _log.warning("_run_harness_subprocess: log flush failed session=%s error=%s", session_id, exc)
        log_buffer.clear()

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
        log_buffer.append(("ERROR", err_msg))
        _flush_log()
        raise RuntimeError(err_msg)
    except Exception as e:
        err_msg = f"Failed to launch {tool_name}: {type(e).__name__}: {e}"
        _log.error("_run_harness_subprocess: %s", err_msg)
        log_buffer.append(("ERROR", err_msg))
        _flush_log()
        raise RuntimeError(err_msg)

    stdout_lines: list[str] = []
    start_time = datetime.utcnow()
    lines_since_flush = 0

    try:
        fds = [proc.stdout, proc.stderr]
        while True:
            ready, _, _ = select.select(fds, [], [], 1.0)
            if not ready:
                elapsed = (datetime.utcnow() - start_time).total_seconds()
                if elapsed > timeout:
                    _log.warning("_run_harness_subprocess: timeout tool=%s elapsed=%ds", tool_name, timeout)
                    _flush_log()
                    proc.kill()
                    proc.wait()
                    raise subprocess.TimeoutExpired(cmd, timeout)
                if proc.poll() is not None:
                    break
                continue

            got_data = False
            for stream in ready:
                line = stream.readline()
                if not line:
                    continue
                got_data = True

                if stream is proc.stdout:
                    stdout_lines.append(line)
                    log_buffer.append(("INFO", line))
                    lines_since_flush += 1
                elif stream is proc.stderr:
                    log_buffer.append(("WARN", f"[stderr] {line}"))
                    lines_since_flush += 1

            if lines_since_flush >= 50:
                _flush_log()
                lines_since_flush = 0

            if not got_data and proc.poll() is not None:
                break

        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _flush_log()
        proc.kill()
        proc.wait()
        _log.error("_run_harness_subprocess: timed out tool=%s timeout=%ds", tool_name, timeout)
        raise RuntimeError(
            f"{tool_name} timed out after {timeout}s. "
            f"The conduit watchdog will clean up orphaned processes."
        )
    finally:
        _flush_log()

    stderr_text = ""
    try:
        stderr_text = proc.stderr.read()
    except Exception:
        pass

    if proc.returncode != 0:
        output = "".join(stdout_lines).strip()
        error_detail = output or f"exit code {proc.returncode}"
        if stderr_text:
            error_detail += f"\n[stderr]: {stderr_text.strip()}"

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
        raise RuntimeError(
            f"{tool_name} invocation failed: {error_detail[:500]}{crash_hint}"
        )

    _log.info("_run_harness_subprocess: success tool=%s pid=%d lines=%d",
              tool_name, proc.pid, len(stdout_lines))
    return "".join(stdout_lines)


def run_opencode(req, working_path, session_id=None):
    role = _resolve_role(req)
    prompt = build_opencode_prompt(req, working_path)
    model = _resolve_model_name(req)
    _log.info("run_opencode: entry role=%s model=%s working_path=%s", role, model or "(none)", working_path)

    launcher = _build_default_opencode_launcher(role, model)
    launcher.set_working_directory(working_path)
    launcher.set_prompt(prompt)

    launcher.prepare_role_prompt_file()

    cmd = launcher.build()

    insert_pos = 2 if len(cmd) > 1 and cmd[1] == "run" else 1
    debug_flags = ["--print-logs", "--log-level", "DEBUG"]
    for f in debug_flags:
        cmd.insert(insert_pos, f)
        insert_pos += 1

    _log.debug("run_opencode: cmd=%s", ' '.join(cmd))
    result = _run_harness_subprocess(cmd, session_id, OPENCODE_TIMEOUT_SECONDS, "opencode")
    _log.info("run_opencode: exit role=%s chars=%d", role, len(result))
    return result


def run_codex(req, working_path, session_id=None):
    """Run Codex CLI with prompt_file role injection strategy.

    Builds a HarnessLauncher with the codex semantic schema, writes the
    role prompt to a temp file, and invokes ``codex exec --cd PATH FILE``.
    """
    from harness_launcher import DEFAULT_BINARIES, HarnessLauncher
    from harness_enums import ExecutionMode, RoleMappingStrategy

    role = _resolve_role(req)
    prompt = build_opencode_prompt(req, working_path)
    model = _resolve_model_name(req)
    _log.info("run_codex: entry role=%s model=%s working_path=%s", role, model or "(none)", working_path)

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
    result = _run_harness_subprocess(cmd, session_id, OPENCODE_TIMEOUT_SECONDS, "Codex")
    _log.info("run_codex: exit role=%s chars=%d", role, len(result))
    return result


def run_model(req, working_path, system_base, prompt_body, session_id=None):
    harness = _resolve_harness(req)
    _log.info("run_model: entry harness=%s working_path=%s", harness, working_path)
    if harness == "opencode":
        return run_opencode(req, working_path, session_id)
    if harness == "ollama":
        return run_ollama(req, system_base, prompt_body, session_id)
    if harness == "codex":
        return run_codex(req, working_path, session_id)
    _log.error("run_model: unsupported harness=%s", harness)
    raise RuntimeError(f"Unsupported harness: {harness}")


def execute_step(step, req, working_path, wr_id, step_outputs):
    """Executes a single step using the selected backend and stores its output."""
    step_id = step['step_id']
    harness = _resolve_harness(req)
    _log.info("execute_step: entry step=%s type=%s harness=%s", step_id, step.get('type', '?'), harness)
    print(f"Executing step: {step_id}")

    resources = req.get("resources", []) or []
    context_contents = []
    for res in resources:
        res_path = os.path.join(working_path, res)
        resolved = os.path.realpath(res_path)
        root = os.path.realpath(working_path)
        if os.path.commonpath([resolved, root]) != root:
            continue
        if os.path.isfile(res_path):
            try:
                with open(res_path, "r", encoding="utf-8") as rf:
                    content = rf.read()
                context_contents.append(f"[RESOURCE: {res}]\n{content}")
            except Exception:
                pass

    for dep in step.get("dependencies", []):
        dep_output = step_outputs.get(dep, {})
        dep_text = dep_output.get("output") or dep_output.get("raw", "")
        if dep_text:
            context_contents.append(f"[DEP OUTPUT: {dep}]\n{dep_text}")

    context_joined = "\n---\n".join(context_contents)

    intent_desc = req.get("intent", {}).get("desired_outcome", "Solve task")
    step_desc = step.get("description", "")

    system_base = "SYSTEM:\nYou are a deterministic cognitive compiler node executing a graph step.\n"

    # ── ollama: use a simpler prompt that local models handle better ──
    if harness == "ollama":
        prompt_body = (
            system_base
            + f"\nTASK:\n{intent_desc}\n"
            + f"\nSTEP:\n{step_desc}"
            + "\n\nWORKING DIRECTORY:\n" + working_path
        )
        if context_joined:
            prompt_body += "\n\nCONTEXT:\n" + context_joined
        prompt_body += (
            "\n\nINSTRUCTIONS:\n"
            + "Describe what you would do to complete this step. "
            + "If the step asks you to run a shell command, output the exact command on a line by itself starting with `$ `. "
            + "If the step asks you to write a file, use the format:\n\n---START_FILE: relative/path---\n<file content>\n---END_FILE---\n\n"
            + "Be concise and direct. Only use the START_FILE/END_FILE format for files you are creating or modifying."
        )
    else:
        prompt_body = (
            system_base
            + f"\nGLOBAL INTENT:\n{intent_desc}\n"
            + f"\nCURRENT STEP [{step.get('type')}]:\n{step_desc}\n"
            + "\n\nWORKING DIRECTORY:\n"
            + working_path
            + "\n\nCONTEXT FILES & PRIOR OUTPUTS:\n"
            + context_joined
            + "\n\nOUTPUT FORMAT RULES:\n"
            + "You must output only structured file blocks.\n\nFormat:\n\n---START_FILE: relative/path---\n<content>\n---END_FILE---"
            + "\nNo explanations. No markdown outside file blocks."
        )

    try:
        dag_session_id = (req.get("metadata") or {}).get("session_id", "")
        raw_text = run_model(req, working_path, system_base, prompt_body, dag_session_id)
    except Exception as e:
        _log.warning("execute_step: run_model failed step=%s error=%s", step_id, e)
        return False, str(e), []

    if raw_text:
        step_outputs[step_id] = {"raw": raw_text}

    if not raw_text:
        _log.warning("execute_step: no model output step=%s", step_id)
        return False, "No model output produced", []

    # ── Parse file blocks FIRST so files exist for shell commands ──
    file_blocks = re.findall(r"---START_FILE: (.*?)---(.*?)---END_FILE---", raw_text, re.DOTALL)

    # ── Analysis/validation: write raw output, no shell commands expected ──
    if not file_blocks and step.get("type") in ["analysis", "validation"]:
        _log.info("execute_step: analysis/validation step=%s completed", step_id)
        step_outputs[step_id] = {"raw": raw_text.strip()}
        return True, "", []

    # ── Write file blocks to disk FIRST (files exist for $ commands below) ──
    files_written: list[str] = []
    if file_blocks:
        for rel_path, content in file_blocks:
            rel = rel_path.strip()
            if rel.startswith("..") or os.path.isabs(rel):
                _log.warning("execute_step: invalid path step=%s path=%s", step_id, rel)
                return False, "Invalid relative path in output block", files_written
            dest_path = os.path.abspath(os.path.join(working_path, rel))
            if not dest_path.startswith(os.path.abspath(working_path)):
                _log.warning("execute_step: path escapes working dir step=%s path=%s", step_id, rel)
                return False, "Block path escapes working directory", files_written
            try:
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                with open(dest_path, "w", encoding="utf-8") as wf:
                    wf.write(content)
                files_written.append(rel)
                step_outputs[step_id] = {
                    "output": f"File updated: {rel}\n\n{content}",
                    "files_written": files_written,
                }
            except Exception as e:
                _log.warning("execute_step: file write failed step=%s path=%s error=%s", step_id, rel, e)
                return False, f"Filesystem write failed: {e}", files_written

    # ── Execute $ shell commands AFTER file writes (files now exist if referenced) ──
    shell_outputs: list[str] = []
    shell_any_failed = False
    shell_any_succeeded = False
    if harness == "ollama":
        cmd_pattern = re.compile(r'^\$\s+(.+)$', re.MULTILINE)
        commands = cmd_pattern.findall(raw_text)
        for cmd in commands:
            cmd = cmd.strip()
            if not cmd:
                continue
            # Safety filter: reject obviously destructive commands
            cmd_lower = cmd.lower()
            dangerous = ["sudo ", "mkfs.", "dd if=", "| sh", "| bash", " |sh", " |bash",
                         "> /dev/sd", "chmod 777 /", ":(){ :|:& };:"]
            if any(d in cmd_lower for d in dangerous):
                shell_outputs.append(f"$ {cmd}\nREJECTED: unsafe command")
                _write_session_log(dag_session_id, f"[exec] REJECTED (unsafe): {cmd[:100]}\n", level="WARN")
                _log.warning("execute_step: rejected unsafe command step=%s cmd=%s", step_id, cmd[:100])
                continue
            print(f"[execute_step] ollama shell command: {cmd[:120]}")
            _log.debug("execute_step: shell command step=%s cmd=%s", step_id, cmd[:120])
            _write_session_log(dag_session_id, f"[exec] $ {cmd}\n")
            try:
                proc = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=working_path,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                out = proc.stdout
                if proc.stderr:
                    out += "\n[stderr]\n" + proc.stderr
                shell_outputs.append(f"$ {cmd}\nexit={proc.returncode}\n{out}")
                if proc.returncode == 0:
                    shell_any_succeeded = True
                else:
                    shell_any_failed = True
                _write_session_log(dag_session_id,
                    f"[exec] exit={proc.returncode} stdout={len(proc.stdout)}B stderr={len(proc.stderr)}B\n")
                _log.debug("execute_step: shell result step=%s exit=%d", step_id, proc.returncode)
            except subprocess.TimeoutExpired:
                shell_outputs.append(f"$ {cmd}\nTIMEOUT after 120s")
                shell_any_failed = True
                _write_session_log(dag_session_id, f"[exec] TIMEOUT\n", level="WARN")
                _log.warning("execute_step: shell timeout step=%s cmd=%s", step_id, cmd[:80])
            except Exception as e:
                shell_outputs.append(f"$ {cmd}\nERROR: {e}")
                shell_any_failed = True
                _write_session_log(dag_session_id, f"[exec] ERROR: {e}\n", level="ERROR")
                _log.warning("execute_step: shell error step=%s error=%s", step_id, e)
        if shell_outputs:
            raw_text += "\n\n--- SHELL EXECUTION RESULTS ---\n" + "\n---\n".join(shell_outputs)
        # If shell commands ran but none succeeded, treat the step as failed
        if shell_any_failed and not shell_any_succeeded:
            _log.warning("execute_step: all shell commands failed step=%s", step_id)
            return False, "All shell commands failed", files_written

    # ── Return success ──
    if file_blocks:
        _log.info("execute_step: success step=%s files=%d", step_id, len(files_written))
        return True, "", files_written

    # No file blocks — supported harnesses treat this as success
    if harness in ("opencode", "codex", "ollama"):
        _log.info("execute_step: no-file-block success step=%s harness=%s", step_id, harness)
        print(f"[execute_step] Harness={harness} completed but produced no file-block output. "
              f"This is expected — treating as success.")
        return True, "", []

    _log.warning("execute_step: no valid file blocks step=%s harness=%s", step_id, harness)
    return False, "No valid file blocks generated", []


def run_worker(wr_id: str):
    _log.info("run_worker: entry wr_id=%s", wr_id)

    # Read DCO from DB
    try:
        from db_adapter import DBAdapter
        db = DBAdapter()
        wr = db.get_work_request(wr_id)
    except Exception as e:
        _log.error("run_worker: DB lookup failed wr_id=%s error=%s", wr_id, e)
        exit(2)

    if not wr:
        _log.error("run_worker: work_request %s not found", wr_id)
        exit(2)

    req = wr["dco_json"]
    if not isinstance(req, dict):
        _log.error("run_worker: invalid dco_json for wr_id=%s", wr_id)
        exit(2)

    result = {
        "workRequestId": wr_id,
        "status": "failure",
        "files_written": [],
        "outputs": [],
        "artifacts": [],
        "error": "",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "executor_id": "executor-cloud",
        "harness": _resolve_harness(req),
        "model": _resolve_model_name(req),
    }

    if "decomposition" not in req or "steps" not in req["decomposition"]:
        _log.error("run_worker: missing decomposition wr_id=%s", wr_id)
        result["error"] = "Not a valid DCO. Missing decomposition block."
        exit(2)

    working_path = os.path.abspath(req.get("path", "."))

    # ── Non-builder fast-path: skip DAG, just call opencode ──
    role = _resolve_role(req)
    session_id = (req.get("metadata") or {}).get("session_id", "")

    _log.info("run_worker: wr_id=%s role=%s working_path=%s", wr_id, role, working_path)

    if role != "builder":
        print(f"Non-builder role '{role}' — fast-path: dispatch via run_model().")
        _log.info("run_worker: non-builder fast-path role=%s wr_id=%s", role, wr_id)
        try:
            system_base = (
                "SYSTEM:\nYou are a deterministic cognitive compiler node executing a graph step.\n"
            )
            prompt_body = (
                system_base
                + f"\nGLOBAL INTENT:\n{req.get('intent', {}).get('desired_outcome', 'Solve task')}\n"
                + "\n\nWORKING DIRECTORY:\n" + working_path
                + "\n\nExecute this task."
            )
            raw_text = run_model(req, working_path, system_base, prompt_body, session_id)
            result["status"] = "success"
            result["timestamp"] = datetime.utcnow().isoformat() + "Z"
        except Exception as e:
            result["status"] = "failure"
            result["error"] = str(e)
            _log.error("run_worker: fast-path failure role=%s wr_id=%s error=%s", role, wr_id, e)

        _capture_session_cost(session_id, OPENCODE_BIN)
        _store_result_in_db(db, wr_id, result, {})
        _log.info("run_worker: fast-path exit wr_id=%s status=%s", wr_id, result["status"])
        exit(0 if result["status"] == "success" else 3)

    steps = req["decomposition"]["steps"]
    step_status = {s["step_id"]: "pending" for s in steps}
    all_files_written = []
    step_outputs: Dict[str, Any] = {}

    while True:
        ready_set = []
        for s in steps:
            if step_status[s["step_id"]] != "pending":
                continue

            deps_met = True
            for dep in s.get("dependencies", []):
                if step_status.get(dep) != "completed":
                    deps_met = False
                    break

            if deps_met:
                ready_set.append(s)

        if not ready_set:
            if all(v == "completed" for v in step_status.values()):
                break
            result["status"] = "failure"
            result["error"] = "DAG deadlock detected. Unmet dependencies."
            _log.error("run_worker: DAG deadlock wr_id=%s steps=%s", wr_id, step_status)
            _capture_session_cost(session_id, OPENCODE_BIN)
            _store_result_in_db(db, wr_id, result, step_outputs)
            exit(3)

        for step in ready_set:
            step_id = step["step_id"]
            step_status[step_id] = "in_progress"
            _log.debug("run_worker: executing step=%s wr_id=%s", step_id, wr_id)
            success, err, written = execute_step(step, req, working_path, wr_id, step_outputs)

            if success:
                step_status[step_id] = "completed"
                all_files_written.extend(written)
                _log.debug("run_worker: step=%s completed files=%d", step_id, len(written))
            else:
                step_status[step_id] = "failed"
                result["status"] = "failure"
                result["error"] = f"Step {step_id} failed: {err}"
                _log.error("run_worker: step=%s failed wr_id=%s error=%s", step_id, wr_id, err)
                _capture_session_cost(session_id, OPENCODE_BIN)
                _store_result_in_db(db, wr_id, result, step_outputs)
                exit(3)

    result["status"] = "success"
    result["files_written"] = all_files_written
    result["timestamp"] = datetime.utcnow().isoformat() + "Z"
    _log.info("run_worker: success wr_id=%s files=%d", wr_id, len(all_files_written))
    _capture_session_cost(session_id, OPENCODE_BIN)
    _store_result_in_db(db, wr_id, result, step_outputs)
    exit(0)


def _store_result_in_db(
    db: Any, wr_id: str, result: Dict[str, Any], step_outputs: Dict[str, Any]
) -> None:
    """Persist step outputs and update work request status."""
    _log.debug("_store_result_in_db: wr_id=%s status=%s outputs=%d",
               wr_id, result["status"], len(step_outputs))
    try:
        if step_outputs:
            db.update_work_request_step_outputs(wr_id, step_outputs)
        db.update_work_request_status(wr_id, result["status"])
    except Exception as exc:
        _log.warning("_store_result_in_db: failed wr_id=%s error=%s", wr_id, exc)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: executor_cloud.py <work_request_id>")
        sys.exit(1)
    run_worker(sys.argv[1])
