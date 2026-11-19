"""Temporal Activity for executing AI harness subprocesses with heartbeats.

Port of executor_cloud.py's _run_harness_subprocess + run_opencode/run_codex,
adapted for Temporal with heartbeat-based progress reporting and stderr capture.
"""

import asyncio
import logging
import os
import select
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from temporalio import activity

_PARENT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PARENT))

from harness_launcher import HarnessLauncher, DEFAULT_BINARIES
from harness_enums import ExecutionMode, RoleMappingStrategy
from conduit.prompt_renderer import build_opencode_prompt

_log = logging.getLogger("conduit.temporal")


# Default opencode semantics (from executor_cloud.py)
_OPENCODE_SEMANTICS = {
    "binary": os.environ.get("OPENCODE_BIN", "/home/codex/.opencode/bin/opencode"),
    "capabilities": {"model": True, "agent": True, "working_directory": True, "system_prompt": False},
    "execution": {"mode": "interactive", "subcommand": "run"},
    "semantics": {
        "model": {"type": "flag", "flag": "--model"},
        "agent": {"type": "flag", "flag": "--agent"},
        "working_directory": {"type": "flag", "flag": "--dir"},
    },
    "role_mapping": {"strategy": "agent"},
}

OPENCODE_BIN = os.environ.get("OPENCODE_BIN", "/home/codex/.opencode/bin/opencode")
EXECUTOR_TIMEOUT = int(os.environ.get("PIPELINE_EXECUTOR_TIMEOUT", "1800"))


class HarnessError(Exception):
    """Non-retryable harness error (exit code != 0, not a rate limit)."""
    pass


class RateLimitError(Exception):
    """API rate limit detected — retryable."""
    pass


class LaunchError(Exception):
    """Failed to launch the harness binary — non-retryable."""
    pass


# ── Prompt builders (from executor_cloud.py) ──────────────────────

def _resolve_role(req: Dict[str, Any]) -> str:
    role = (req.get("metadata") or {}).get("role", "")
    return role if role in ("builder", "reviewer", "planner", "critic") else "builder"


def _build_opencode_prompt(req: Dict[str, Any], working_path: str) -> str:
    """Build a structured prompt from a WorkRequest DCO.  Ported from executor_cloud.py."""
    role = _resolve_role(req)
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

    blocks.append("## Intent")
    if intent.get("problem_statement"):
        blocks.append(_kv("Problem", intent["problem_statement"]))
    if intent.get("desired_outcome"):
        blocks.append(_kv("Outcome", intent["desired_outcome"]))
    blocks.append(_kv("Priority", intent.get("priority", "medium")))
    blocks.append(_kv("Abstraction", intent.get("abstraction_level", "task")))

    steps = decomposition.get("steps", [])
    blocks.append("\n## Decomposition")
    blocks.append(_kv("Strategy", decomposition.get("strategy", "")))
    for i, s in enumerate(steps, 1):
        blocks.append(f"  **Step {i}** [{s.get('type', 'execution')}]: {s.get('description', '')[:300]}")

    func = requirements.get("functional", [])
    if func:
        blocks.append("\n## Requirements (functional)")
        for ac in func:
            blocks.append(f"  - {ac}")

    safety = constraints.get("safety_constraints", [])
    if safety:
        blocks.append("\n## Constraints")
        for sc in safety:
            blocks.append(f"  - {sc}")

    conditions = success.get("completion_conditions", [])
    if conditions:
        blocks.append("\n## Success Criteria")
        for c in conditions:
            blocks.append(f"  - {c.get('condition', '')}")

    files = artifacts.get("produced_files", [])
    if files:
        blocks.append("\n## Target Files")
        for f in files:
            blocks.append(f"  - {f.get('path', '?')}")

    lines = ["\n".join(blocks), f"\n## Working directory\n{working_path}"]

    if role == "builder":
        lines.extend([
            "\n## Instructions",
            "Execute this WorkRequest. Implement the plan, modifying only "
            "the files listed in Target Files. Satisfy all acceptance criteria "
            "and completion conditions. Respect all safety constraints.",
        ])
    elif role == "reviewer":
        lines.extend([
            "\n## Instructions",
            "Review the implementation described in this WorkRequest. "
            "Compare the change report in CHANGES/committed/ against the plan. "
            "If changes match the acceptance criteria, issue a REVIEW_PASS receipt. "
            "If they don't match, issue a REVIEW_REJECT receipt with explanation.",
        ])
    elif role == "planner":
        lines.extend([
            "\n## Instructions",
            "Elucidate the proposed plan in this WorkRequest. "
            "Define acceptance criteria, identify files affected, and note dependencies. "
            "When the plan is fully defined, issue a PLAN_CREATE receipt.",
        ])

    lines.append("\nDo NOT issue receipts — the conduit manager handles the audit trail.")
    return "\n".join(lines)


# ── API limit detection (from main.py) ────────────────────────────

_API_LIMIT_PATTERNS = [
    "usage limit", "rate limit", "usage exceeded", "api usage",
    "insufficient_quota", "insufficient balance", "insufficient funds",
    "quota exceeded", "billing", "credit", "429", "402",
    "exceeded your current quota", "your account must be",
    "payment required", "freeusagelimiterror", "limit exceeded",
]


def _detect_api_limit(exit_code: int, output: str) -> bool:
    output_lower = output.lower()
    for pattern in _API_LIMIT_PATTERNS:
        if pattern in output_lower:
            return True
    return False


def _extract_tokens(output: str) -> int:
    """Scan output for token consumption data."""
    import re
    patterns = [
        r'"total"\s*:\s*(\d+)',
        r'"total_tokens"\s*:\s*(\d+)',
        r'Total tokens?\s*[:=]\s*([\d,]+)',
        r'total_tokens\s*=\s*(\d+)',
        r'tokens?_?used\s*[:=]\s*([\d,]+)',
        r'Tokens:\s*([\d,]+)',
    ]
    for pat in patterns:
        m = re.search(pat, output, re.IGNORECASE)
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except ValueError:
                continue
    return 0


# ── Main Activity ─────────────────────────────────────────────────

@activity.defn
async def execute_with_model(
    model_cfg: Dict[str, Any],
    dco: Dict[str, Any],
    wr_id: str,
    executor_cmd: str,
    ticket_id: str = "",
    working_path: str = "",
    session_id: str = "",
) -> Dict[str, Any]:
    """Execute an AI harness subprocess with the given model configuration.

    Sends Temporal heartbeats on every output line so the platform can
    detect stalls and enable graceful cancellation.

    Returns a dict with: exit_code, output, stderr, tokens_used, session_id,
    rate_limited (bool), error_summary.
    """
    harness = model_cfg.get("harness", "opencode")
    model = model_cfg.get("model", "")
    role = (dco.get("metadata") or {}).get("role", "builder")
    session_id = f"{role}-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"

    activity.logger.info(
        f"execute_with_model: harness={harness} model={model} "
        f"role={role} session={session_id}"
    )

    # Debug log for ollama prompt debugging
    if harness == "ollama":
        prompt = build_opencode_prompt(dco, working_path)
        activity.logger.info(
            f"ollama prompt length={len(prompt)} first_100={prompt[:100]!r}"
        )

    # Build the command
    if harness == "opencode" or "opencode" in harness.lower():
        provider_type = model_cfg.get("provider_type", "")
        cmd = _build_opencode_cmd(model, role, dco, working_path, provider_type=provider_type)
    elif harness == "codex" or "codex" in harness.lower():
        cmd = _build_codex_cmd(role, dco, working_path)
    elif harness == "ollama":
        # Build prompt inline and pipe via stdin (more reliable than positional arg)
        prompt = build_opencode_prompt(dco, working_path)
        cmd = _build_ollama_cmd(model)
    else:
        # Fallback: use executor_cloud.py directly with wr_id
        cmd = [sys.executable, executor_cmd, wr_id]

    # Launch subprocess in its own process group so we can kill the entire
    # tree (harness + child opencode) on timeout or cancellation.
    stdin_kw = {"stdin": asyncio.subprocess.PIPE} if harness == "ollama" else {}
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            **stdin_kw,
        )
    except FileNotFoundError:
        raise LaunchError(f"Harness binary not found: {cmd[0]}")
    except Exception as e:
        raise LaunchError(f"Failed to launch harness: {type(e).__name__}: {e}")

    # Pipe prompt to ollama via stdin, then close stdin
    if harness == "ollama":
        proc.stdin.write(prompt.encode())
        await proc.stdin.drain()
        proc.stdin.close()

    # Create session in DB (best-effort — DB ops are separate Activities)
    activity.logger.info(f"execute_with_model: pid={proc.pid} session={session_id}")

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    tokens_used = 0
    start_time = datetime.utcnow()

    # Persistent reader tasks — one per stream, no task leak
    # Uses chunk-based reading with a partial-line buffer instead of
    # readline() to avoid asyncio's default 64KB line-length limit.
    # The buffer correctly reassembles lines split across chunk boundaries.
    async def _read_stdout():
        buffer = ""
        while True:
            chunk = await proc.stdout.read(131072)
            if not chunk:
                if buffer:
                    stripped = buffer.rstrip("\r")
                    if stripped:
                        stdout_lines.append(stripped)
                break
            buffer += chunk.decode()
            lines = buffer.split("\n")
            for line in lines[:-1]:
                stripped = line.rstrip("\r")
                if stripped:
                    stdout_lines.append(stripped)
            buffer = lines[-1]

    async def _read_stderr():
        buffer = ""
        while True:
            chunk = await proc.stderr.read(131072)
            if not chunk:
                if buffer:
                    stripped = buffer.rstrip("\r")
                    if stripped:
                        stderr_lines.append(stripped)
                break
            buffer += chunk.decode()
            lines = buffer.split("\n")
            for line in lines[:-1]:
                stripped = line.rstrip("\r")
                if stripped:
                    stderr_lines.append(stripped)
            buffer = lines[-1]

    # Timeout / heartbeat coroutine — runs alongside the readers
    async def _timer_and_heartbeat():
        # Send an immediate heartbeat so Temporal doesn't time us out
        # before the first 5-second interval elapses.
        activity.heartbeat({
            "session_id": session_id,
            "pid": proc.pid,
            "lines": 0,
            "stderr_lines": 0,
            "tokens": 0,
        })
        last_heartbeat = datetime.utcnow()
        while proc.returncode is None:
            await asyncio.sleep(1.0)
            elapsed = (datetime.utcnow() - start_time).total_seconds()
            if elapsed > EXECUTOR_TIMEOUT:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
                await proc.wait()
                raise HarnessError(f"Harness timed out after {EXECUTOR_TIMEOUT}s")
            # Heartbeat every 5 seconds
            if (datetime.utcnow() - last_heartbeat).total_seconds() >= 5:
                activity.heartbeat({
                    "session_id": session_id,
                    "pid": proc.pid,
                    "lines": len(stdout_lines),
                    "stderr_lines": len(stderr_lines),
                    "tokens": tokens_used,
                })
                last_heartbeat = datetime.utcnow()

    try:
        stdout_task = asyncio.create_task(_read_stdout())
        stderr_task = asyncio.create_task(_read_stderr())
        timer_task = asyncio.create_task(_timer_and_heartbeat())

        # Wait for readers to finish naturally (pipes EOF on process exit).
        # The timer kills the process on timeout, which causes pipes to EOF
        # and readers to exit.  No premature cancellation — avoids data loss
        # from mid-readline() cancellation.
        await asyncio.gather(stdout_task, stderr_task)

        # Timer check — if it fired (killed the process), raise its error
        if timer_task.done():
            timer_exc = timer_task.exception()
            await proc.wait()
            if timer_exc:
                raise timer_exc
        else:
            timer_task.cancel()
            try:
                await timer_task
            except asyncio.CancelledError:
                pass
            # Ensure the process has fully exited before reading returncode
            await proc.wait()
    except asyncio.CancelledError:
        # Temporal cancellation — kill the entire process group
        activity.logger.warning(f"execute_with_model: cancelled session={session_id}")
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            await proc.wait()
        raise

    exit_code = proc.returncode

    output_text = "\n".join(stdout_lines)
    stderr_text = "\n".join(stderr_lines)
    tokens_used = _extract_tokens(output_text)

    # Classify the result
    is_rate_limit = _detect_api_limit(exit_code, output_text)
    if is_rate_limit and exit_code == 0:
        exit_code = 29  # Treat as rate-limit failure

    result = {
        "exit_code": exit_code,
        "output": output_text,
        "stderr": stderr_text,
        "tokens_used": tokens_used,
        "session_id": session_id,
        "rate_limited": is_rate_limit,
        "error_summary": "",
    }

    if is_rate_limit:
        error_summary = "API usage limit"
        for line in output_text.splitlines():
            ll = line.lower()
            if any(p in ll for p in ["limit", "quota", "usage", "credit", "429"]):
                error_summary = line.strip()[:200]
                break
        result["error_summary"] = error_summary
        raise RateLimitError(error_summary)

    if exit_code != 0:
        error_summary = f"Exit code {exit_code}"
        if stderr_text:
            stderr_trimmed = stderr_text[:500]
            error_summary += f" | stderr: {stderr_trimmed}"
            stderr_lower = stderr_text.lower()
            if "nameerror" in stderr_lower:
                error_summary += " (likely missing import or undefined variable)"
            elif "importerror" in stderr_lower or "modulenotfounderror" in stderr_lower:
                error_summary += " (missing Python module)"
            elif "syntaxerror" in stderr_lower:
                error_summary += " (syntax error in harness script)"
        # Also include last 200 chars of output for debug
        if output_text:
            error_summary += f" | output (last 200): {output_text[-200:]}"
        result["error_summary"] = error_summary
        raise HarnessError(error_summary)

    activity.logger.info(
        f"execute_with_model: success session={session_id} "
        f"tokens={tokens_used} lines={len(stdout_lines)}"
    )
    return result


# ── Command builders ───────────────────────────────────────────────

def _build_opencode_cmd(
    model: str,
    role: str,
    dco: Dict[str, Any],
    working_path: str,
    provider_type: str = "",
) -> list:
    """Build an opencode CLI command from the DCO.

    opencode expects ``--model <provider>/<model_identifier>`` format
    (e.g. ``ollama/qwen2.5-coder:latest``), so the model is prefixed
    with the provider type when available.
    """
    prompt = build_opencode_prompt(dco, working_path)

    # Prefix model with provider type for opencode (e.g., ollama/qwen2.5-coder:latest)
    qualified_model = model
    if provider_type and model and "/" not in model:
        qualified_model = f"{provider_type}/{model}"

    launcher = HarnessLauncher.from_harness_row({
        "name": "opencode",
        "invocation_semantics": _OPENCODE_SEMANTICS,
    })
    launcher.set_agent(role)
    if qualified_model:
        launcher.set_model(qualified_model)
    if working_path and os.path.isdir(working_path):
        launcher.set_working_directory(working_path)
    launcher.set_prompt(prompt)

    cmd = launcher.build()
    # Inject debug flags
    insert_pos = 2 if len(cmd) > 1 and cmd[1] == "run" else 1
    for flag in ["--print-logs", "--log-level", "DEBUG"]:
        cmd.insert(insert_pos, flag)
        insert_pos += 1
    return cmd


def _build_codex_cmd(
    role: str,
    dco: Dict[str, Any],
    working_path: str,
) -> list:
    """Build a Codex CLI command from the DCO."""
    prompt = build_opencode_prompt(dco, working_path)

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
    return launcher.build()


def _build_ollama_cmd(model: str) -> list:
    """Build an ollama CLI command.

    Invokes ``ollama run <model>`` with prompt piped via stdin.
    The model must already be pulled and the ollama server running.
    """
    return ["ollama", "run", model]
