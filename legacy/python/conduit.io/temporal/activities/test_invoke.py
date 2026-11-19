"""Temporal Activities for model test-invocation.

Two activities supporting TestInvokeWorkflow:

1. resolve_test_model_activity — looks up a model by ID and returns config
2. execute_test_invoke_activity — runs the model harness with a test prompt,
   writing output to a session log file with heartbeats
"""

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from temporalio import activity

_PARENT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PARENT))

from db_adapter import DBAdapter
from temporal.activities.execute_model import HarnessError, LaunchError

_log = activity.logger


# ── Activity 1: Resolve model config ─────────────────────────────

@activity.defn
async def resolve_test_model_activity(model_id: str) -> Optional[Dict[str, Any]]:
    """Look up a model by ID in the AI config DB and return harness + config.

    Returns dict with keys: id, name, harness, model_identifier, provider, provider_type.
    Returns None if not found.
    """
    try:
        db = DBAdapter()
        row = db.get_model_config(model_id)
        if not row:
            _log.warning(f"resolve_test_model_activity: model {model_id} not found")
            return None

        harness_semantics = {}
        if row.get("invocation_semantics"):
            try:
                harness_semantics = json.loads(row["invocation_semantics"])
            except (json.JSONDecodeError, TypeError):
                pass

        return {
            "id": row["id"],
            "name": row["name"],
            "model_identifier": row["model_identifier"],
            "harness": row.get("harness_name") or "opencode",
            "harness_semantics": harness_semantics,
            "provider": row.get("provider_name") or "",
            "provider_type": row.get("provider_type") or "",
            "endpoint_url": row.get("endpoint_url") or "",
        }
    except Exception as e:
        _log.error(f"resolve_test_model_activity: error: {e}")
        return None


# ── Activity 2: Execute model with test prompt ───────────────────

@activity.defn
async def execute_test_invoke_activity(
    model_cfg: Dict[str, Any],
    test_prompt: str,
    session_id: str,
    session_log_path: str = "",
) -> Dict[str, Any]:
    """Run a model harness with the given test prompt and write output to a session log.

    Sends Temporal heartbeats on every output line so the platform can detect stalls.
    Returns dict with: exit_code, output, stderr, tokens_used, session_id.
    """
    harness_type = (model_cfg.get("harness") or "opencode").lower()
    model_identifier = model_cfg.get("model_identifier", "")

    if not session_log_path:
        conduit_data = os.environ.get(
            "PIPELINE_DIR",
            os.path.expanduser("~/dev/nexus/.conduit-data"),
        )
        sessions_dir = os.path.join(conduit_data, "sessions")
        os.makedirs(sessions_dir, exist_ok=True)
        session_log_path = os.path.join(sessions_dir, f"{session_id}.log")

    # Build the command based on harness type
    cmd = _build_test_cmd(harness_type, model_identifier, test_prompt)
    if not cmd:
        raise HarnessError(f"Unsupported harness type: {harness_type}")

    _log.info(
        f"execute_test_invoke: harness={harness_type} model={model_identifier} "
        f"session={session_id} cmd={' '.join(cmd[:3])}..."
    )

    # Write header to session log
    _append_log(session_log_path, [
        f"[test-invoke] Model: {model_cfg.get('name', model_identifier)} ({model_identifier})",
        f"[test-invoke] Harness: {harness_type}",
        f"[test-invoke] Command: {' '.join(cmd)}",
        f"[test-invoke] Started at: {datetime.utcnow().isoformat()}Z",
        "─" * 60,
    ])

    # Launch subprocess
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        _append_log(session_log_path, [
            f"[test-invoke] [stderr] Binary not found: {cmd[0] if cmd else '(empty cmd)'}",
            f"[test-invoke] Failed to launch harness",
        ])
        raise LaunchError(f"Harness binary not found: {cmd[0]}")
    except Exception as e:
        _append_log(session_log_path, [
            f"[test-invoke] [stderr] Failed to launch: {type(e).__name__}: {e}",
        ])
        raise LaunchError(f"Failed to launch harness: {type(e).__name__}: {e}")

    # Stream stdout/stderr with heartbeats
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    start_time = datetime.utcnow()
    timeout = int(os.environ.get("PIPELINE_EXECUTOR_TIMEOUT", "1800"))

    async def _read_stream(stream, lines: list[str], is_stderr: bool = False):
        while True:
            line = await stream.readline()
            if not line:
                break
            decoded = line.decode().rstrip("\n\r")
            lines.append(decoded)
            prefix = "[stderr] " if is_stderr else ""
            _append_log(session_log_path, [f"{prefix}{decoded}"])

    async def _heartbeat_and_timeout():
        last_heartbeat = datetime.utcnow()
        while proc.returncode is None:
            await asyncio.sleep(1.0)
            elapsed = (datetime.utcnow() - start_time).total_seconds()
            if elapsed > timeout:
                proc.kill()
                await proc.wait()
                raise HarnessError(f"Test invoke timed out after {timeout}s")
            if (datetime.utcnow() - last_heartbeat).total_seconds() >= 5:
                activity.heartbeat({
                    "session_id": session_id,
                    "lines": len(stdout_lines),
                    "stderr_lines": len(stderr_lines),
                })
                last_heartbeat = datetime.utcnow()

    try:
        stdout_task = asyncio.create_task(_read_stream(proc.stdout, stdout_lines))
        stderr_task = asyncio.create_task(_read_stream(proc.stderr, stderr_lines, is_stderr=True))
        timer_task = asyncio.create_task(_heartbeat_and_timeout())

        await asyncio.gather(stdout_task, stderr_task)
        await proc.wait()

        if timer_task.done():
            exc = timer_task.exception()
            if exc:
                raise exc
        else:
            timer_task.cancel()
            try:
                await timer_task
            except asyncio.CancelledError:
                pass
    except asyncio.CancelledError:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        raise

    exit_code = proc.returncode or 0
    output_text = "\n".join(stdout_lines)
    stderr_text = "\n".join(stderr_lines)
    tokens_used = _extract_tokens(output_text)

    elapsed = (datetime.utcnow() - start_time).total_seconds()
    _append_log(session_log_path, [
        "─" * 60,
        f"[test-invoke] Exit code: {exit_code} | Elapsed: {elapsed:.1f}s",
        f"[test-invoke] Ended at: {datetime.utcnow().isoformat()}Z",
    ])

    if exit_code != 0:
        raise HarnessError(
            f"Exit code {exit_code} | stderr: {stderr_text[:500] if stderr_text else 'none'}"
        )

    return {
        "exit_code": exit_code,
        "output": output_text,
        "stderr": stderr_text,
        "tokens_used": tokens_used,
        "session_id": session_id,
    }


# ── Helpers ──────────────────────────────────────────────────────

def _build_test_cmd(
    harness: str,
    model_identifier: str,
    test_prompt: str,
) -> list[str]:
    """Build a CLI command for the given harness type."""
    if harness == "opencode":
        opencode_bin = os.environ.get(
            "OPENCODE_BIN", "/home/codex/.opencode/bin/opencode"
        )
        working_dir = os.environ.get("PIPELINE_ROOT", os.path.expanduser("~/dev"))
        return [
            opencode_bin, "run",
            "--model", model_identifier,
            "--agent", "build",
            "--dir", working_dir,
            "--print-logs", "--log-level", "DEBUG",
            test_prompt,
        ]
    elif harness == "codex":
        codex_bin = os.environ.get("CODEX_BIN", "codex")
        return [codex_bin, "exec", test_prompt]
    elif harness == "ollama":
        return ["ollama", "run", model_identifier]
    else:
        raise ValueError(f"Unsupported harness: {harness}")


def _append_log(path: str, lines: list[str]):
    """Append lines to a log file (thread-safe append)."""
    try:
        with open(path, "a", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
            f.flush()
    except OSError:
        pass


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
