#!/usr/bin/env python3
"""agent_chat.py — Async agent chat server for the message box.

Reuses executor_cloud.py's opencode invocation logic.
Accepts @agent messages, dispatches to opencode in background threads,
and streams responses via SSE.

Usage:
    python3 agent_chat.py              # listen on port 3017
    AGENT_CHAT_PORT=3103 python3 ...   # custom port

Endpoints:
    POST /chat              —  { role, message } → { session_id }
    GET  /chat/stream/<id>  —  SSE stream of opencode stdout
    GET  /chat/sessions     —  list active sessions
    GET  /chat/agents       —  list available agents
    GET  /chat/health       —  liveness check
"""

import json
import os
import queue
import re
import secrets
import select
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# ── Ensure the 'tackle' package is importable ────────────────────
# This lets you run `python3 agent_chat.py` from any working directory
# without needing PYTHONPATH set in the environment.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_TACKLE_PARENT = os.path.dirname(_SCRIPT_DIR)  # nexus/python/
if _TACKLE_PARENT not in sys.path:
    sys.path.insert(0, _TACKLE_PARENT)

# ── Shared env loader ────────────────────────────────────────────
from tackle.env_config import load_env  # noqa: F401 — fires at import time

# ── Tackle modules (standalone — no conduit dependency) ───────────
from tackle.executor import (
    OPENCODE_BIN,
    OPENCODE_TIMEOUT_SECONDS,
    _build_opencode_prompt,
)
from tackle.db import get_role_config as _get_role_config

PROJECT_ROOT = os.environ.get(
    "PIPELINE_ROOT",
    "/home/codex/dev",
)


# Shared token for agent-chat auth. When set, all agent-launch endpoints
# (POST /chat, POST /chat/kill, GET /chat/stream) require a Bearer token.
AGENT_CHAT_TOKEN = os.environ.get("AGENT_CHAT_TOKEN", "")

# Concurrency limits (configurable via env vars)
AGENT_CHAT_MAX_PER_ROLE = int(os.environ.get("AGENT_CHAT_MAX_PER_ROLE", "1"))
AGENT_CHAT_MAX_GLOBAL = int(os.environ.get("AGENT_CHAT_MAX_GLOBAL", "4"))

VALID_ROLES = ["planner", "builder", "reviewer", "critic", "analyst", "architect", "inspector", "engineer", "engineer-ii", "engineer-iii", "devops", "topologist", "rover"]

# ── AI config resolution (delegates to tackle.db / tackle-mcp) ──


def _resolve_role_config(role: str) -> dict | None:
    """Look up the AI config for a role from tackle-mcp (``tackle.*`` schema).

    Returns a dict with keys: model_identifier, provider_type, harness_name,
    invocation_semantics (parsed JSON dict), api_key, endpoint_url,
    fallback_models (list), or None if not configured.
    Results are cached inside ``tackle.db`` with a 60-second TTL.
    """
    try:
        return _get_role_config(role)
    except Exception as e:
        print(f"[ai-config] tackle-mcp lookup failed: {e}", file=sys.stderr)
        return None


def _build_harness_cmd(role: str, prompt: str, log_level: str = 'INFO') -> tuple[list[str], dict]:
    """Build the CLI command for a harness, resolving model + harness config
    from the DB via the semantic HarnessLauncher.

    Falls back to PIPELINE_MODEL env var if no DB config exists.

    log_level controls the --log-level flag passed to the opencode binary.
    Set to 'NONE' to omit --print-logs entirely.

    Returns (cmd_list, env_overrides) where env_overrides is a dict of
    environment variables the caller should inject into the subprocess.
    """
    from tackle.harness_launcher import HarnessLauncher

    cfg = _resolve_role_config(role)
    env_overrides: dict = {}

    if cfg:
        # Build launcher from the DB harness row (has invocation_semantics with
        # capabilities/semantics/execution/role_mapping)
        launcher = HarnessLauncher.from_harness_row({
            "name": cfg["harness_name"],
            "invocation_semantics": cfg["invocation_semantics"],
        })
        launcher.set_model(cfg["model_identifier"])
        launcher.set_agent(role)
        launcher.set_working_directory(PROJECT_ROOT)
        launcher.set_prompt(prompt)

        # For PROMPT_FILE strategy (e.g., Codex CLI), write the role prompt
        # to a temp file that gets passed on the command line instead of inline text.
        launcher.prepare_role_prompt_file()

        print(f"[ai-config] role={role} → model={cfg['model_identifier']} "
              f"provider={cfg['provider_type']} harness={cfg['harness_name']} "
              f"launcher={launcher}", flush=True)

        # Pass the DB-stored API key to the subprocess
        api_key = cfg.get("api_key") or ""
        provider_type = cfg.get("provider_type") or ""
        if api_key:
            env_name = _PROVIDER_ENV_MAP.get(provider_type, f"{provider_type.upper()}_API_KEY")
            env_overrides[env_name] = api_key
            print(f"[ai-config] Injecting {env_name} for provider type '{provider_type}'", flush=True)
    else:
        # No DB config — build a default opencode launcher as fallback
        fallback_model = os.environ.get("PIPELINE_MODEL", "")
        launcher = HarnessLauncher(
            binary=OPENCODE_BIN,
            capabilities={"model": True, "agent": True, "working_directory": True},
            semantics={
                "model": {"type": "flag", "flag": "--model"},
                "agent": {"type": "flag", "flag": "--agent"},
                "working_directory": {"type": "flag", "flag": "--dir"},
            },
            execution_data={"mode": "interactive", "subcommand": "run"},
            # role_mapping_strategy omitted — defaults to RoleMappingStrategy.AGENT
        )
        launcher.set_agent(role)
        launcher.set_working_directory(PROJECT_ROOT)
        launcher.set_prompt(prompt)
        if fallback_model:
            launcher.set_model(fallback_model)
            print(f"[ai-config] role={role} → fallback model={fallback_model} (env)", flush=True)
        else:
            print(f"[ai-config] role={role} → no model configured (opencode default)", flush=True)

    cmd = launcher.build()
    # Inject --print-logs --log-level that opencode needs but is
    # harness-internal, not part of the semantic model.
    # log_level='NONE' means omit --print-logs entirely.
    if launcher.binary == OPENCODE_BIN or "opencode" in launcher.binary:
        if log_level != 'NONE':
            insert_pos = 2 if len(cmd) > 1 and cmd[1] == "run" else 1
            for flag in ["--print-logs", "--log-level", log_level.upper()]:
                cmd.insert(insert_pos, flag)
                insert_pos += 1

    return cmd, env_overrides


# Map provider types to their standard API key environment variable names
_PROVIDER_ENV_MAP = {
    "opencode": "OPENCODE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "ollama": "",
    "codex": "CODEX_API_KEY",
    "custom": "CUSTOM_API_KEY",
}

# ── In-memory session state ───────────────────────────────────────

_sessions: dict = {}        # session_id → { role, status, pid, queue, started_at, exit_code }
_sessions_lock = threading.Lock()


def _generate_id() -> str:
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    return f"chat-{ts}-{uuid.uuid4().hex[:6]}"


MCP_TOOLS_URL = "http://localhost:3100/tools/call"


def _save_audit_trail(session_id: str, role: str, message: str, output: str) -> None:
    """Save the prompt and agent response to the MCP audit trail.

    Calls save_prompt to create a prompt record, then save_response to
    attach the agent's output. Best-effort — failures are logged but
    never crash the worker.
    """
    if not output.strip():
        return  # nothing worth saving

    try:
        # Extract a title from the user's actual message
        title = ""
        outcome_match = re.search(r'-\s*\*\*Outcome:\*\*\s*(.+)', message)
        if outcome_match:
            title = outcome_match.group(1).strip()[:120]
        if not title:
            title = message.strip().split('\n')[0][:80] if message.strip() else f"{role} response"

        # Step 1: save the prompt
        save_prompt_body = json.dumps({
            "name": "save_prompt",
            "arguments": {
                "title": title,
                "content": message,
                "project": "pipeline",
                "session": session_id,
            }
        }).encode('utf-8')
        req = urllib.request.Request(
            MCP_TOOLS_URL,
            data=save_prompt_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        prompt_number = result.get("result", {}).get("promptNumber", "")

        # Step 2: save the response
        if prompt_number:
            save_resp_body = json.dumps({
                "name": "save_response",
                "arguments": {
                    "promptNumber": prompt_number,
                    "response": output,
                }
            }).encode('utf-8')
            req2 = urllib.request.Request(
                MCP_TOOLS_URL,
                data=save_resp_body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req2, timeout=10)
            print(f"[audit] Saved prompt {prompt_number} + response for session {session_id} ({role})", flush=True)
    except Exception as e:
        print(f"[audit] Failed to save audit trail for session {session_id}: {e}", file=sys.stderr)


def _run_chat_worker(session_id: str, role: str, message: str, log_level: str = 'INFO') -> None:
    """Spawn opencode in a background thread.  Write stdout lines to the session queue.

    Model/harness resolution now goes through the ai_role_config DB table;
    falls back to PIPELINE_MODEL env var if no DB config exists.

    stderr is captured separately and emitted as structured error events
    so that harness crashes (NameError, ImportError, etc.) are never silently lost.
    """
    req = {
        "intent": {
            "desired_outcome": message,
            "priority": "medium",
            "abstraction_level": "task",
        },
        "metadata": {
            "role": role,
            "harness": "opencode",
            "session_id": session_id,
        },
    }

    prompt = _build_opencode_prompt(req, PROJECT_ROOT)
    cmd, env_overrides = _build_harness_cmd(role, prompt, log_level=log_level)

    q = _sessions[session_id]["queue"]
    exit_code = -1
    output_lines: list[str] = []
    stderr_lines: list[str] = []

    # Merge DB-stored API keys into the subprocess environment
    proc_env = None
    if env_overrides:
        proc_env = os.environ.copy()
        for k, v in env_overrides.items():
            if v:
                proc_env[k] = v

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=proc_env,
        )
        _sessions[session_id]["pid"] = proc.pid
        _sessions[session_id]["status"] = "running"

        start_time = datetime.utcnow()
        fds = [proc.stdout, proc.stderr]

        while True:
            ready, _, _ = select.select(fds, [], [], 1.0)
            if not ready:
                elapsed = (datetime.utcnow() - start_time).total_seconds()
                if elapsed > OPENCODE_TIMEOUT_SECONDS:
                    proc.kill()
                    proc.wait()
                    q.put({"type": "error", "text": f"Agent timed out after {OPENCODE_TIMEOUT_SECONDS}s"})
                    break
                if proc.poll() is not None:
                    break
                continue

            got_data = False
            for stream in ready:
                line = stream.readline()
                if not line:
                    continue
                got_data = True

                stripped = line.rstrip("\n\r")
                if not stripped:
                    continue

                if stream is proc.stdout:
                    output_lines.append(stripped)
                    q.put({"type": "line", "text": stripped})
                elif stream is proc.stderr:
                    # Emit stderr as structured error events so the UI
                    # can highlight them and they're never silently lost.
                    stderr_lines.append(stripped)
                    q.put({"type": "stderr", "text": stripped})

            # Break when process is dead AND pipes are at EOF (no data)
            if not got_data and proc.poll() is not None:
                break

        proc.wait(timeout=10)
        exit_code = proc.returncode

        # Read any remaining stderr after exit
        try:
            remaining_stderr = proc.stderr.read()
            if remaining_stderr:
                for line in remaining_stderr.splitlines():
                    stripped = line.rstrip("\n\r")
                    if stripped:
                        stderr_lines.append(stripped)
                        q.put({"type": "stderr", "text": stripped})
        except Exception:
            pass

        # If the process exited non-zero, emit a structured crash diagnostic
        if exit_code != 0 and stderr_lines:
            crash_hint = _detect_crash_type(stderr_lines)
            q.put({"type": "error", "text": f"Agent exited with code {exit_code}{crash_hint}"})

        with _sessions_lock:
            if session_id in _sessions:
                _sessions[session_id]["status"] = "done"
    except FileNotFoundError:
        err_msg = f"Agent binary not found: {cmd[0] if cmd else '(empty cmd)'}"
        q.put({"type": "error", "text": err_msg})
        with _sessions_lock:
            if session_id in _sessions:
                _sessions[session_id]["status"] = "error"
    except Exception as e:
        error_msg = str(e)
        q.put({"type": "error", "text": f"Agent error: {error_msg}"})
        with _sessions_lock:
            if session_id in _sessions:
                _sessions[session_id]["status"] = "timeout" if "timed out" in error_msg else "error"
    finally:
        with _sessions_lock:
            if session_id in _sessions:
                _sessions[session_id]["exit_code"] = exit_code
        q.put({"type": "done", "exit_code": exit_code})
        q.put({"type": "close"})
        # Save prompt + response to audit trail (best-effort, after stream close)
        _save_audit_trail(session_id, role, prompt, "\n".join(output_lines))

        # Emit LOSM ExecutionReceipt for completed agent task.
        # Sends SUCCESS/FAILED to vision.receipts → governance_events via trigger.
        # Best-effort — import failures or server errors are logged, not fatal.
        try:
            from tackle.vision_bridge import issue_receipt
            from losm_ir.execution_receipt import ExecutionReceipt
            receipt = ExecutionReceipt(
                work_request_id=session_id,
                executor_id=role,
                timestamp=datetime.utcnow().isoformat() + "Z",
                result="SUCCESS" if exit_code == 0 else "FAILED",
                lineage_parent=message[:200],
            )
            issue_receipt(receipt, plan_id=session_id, session_id=session_id)
        except ImportError:
            pass  # losm_ir or vision_bridge not available — skip
        except Exception as e:
            print(f"[vision-bridge] Failed to emit receipt for {session_id}: {e}", file=sys.stderr)


def _detect_crash_type(stderr_lines: list[str]) -> str:
    """Scan stderr lines for common Python crash patterns and return a hint."""
    stderr_text = "\n".join(stderr_lines).lower()
    if "nameerror" in stderr_text:
        return " (likely missing import or undefined variable — check the harness script)"
    if "importerror" in stderr_text or "modulenotfounderror" in stderr_text:
        return " (missing Python module — install the dependency)"
    if "syntaxerror" in stderr_text:
        return " (syntax error in harness script — check for recent edits)"
    if "filenotfounderror" in stderr_text:
        return " (binary or file not found — check PATH and installation)"
    if "keyboardinterrupt" in stderr_text:
        return " (interrupted by signal)"
    if "memoryerror" in stderr_text:
        return " (out of memory)"
    return ""


def _ensure_dirs() -> None:
    # .conduit-data was deleted 2026-08-09 and mirrored to audit/CONDUIT_DATA
    d = os.path.join(PROJECT_ROOT, "nexus", "audit", "CONDUIT_DATA", "sessions")
    os.makedirs(d, exist_ok=True)


_AGENT_DESCRIPTIONS = {
    "planner": "Creates and refines implementation plans. Defines acceptance criteria, identifies affected files.",
    "builder": "Implements plans. Modifies code, satisfies acceptance criteria and completion conditions.",
    "reviewer": "Reviews implementations against plans. Issues REVIEW_PASS or REVIEW_REJECT.",
    "critic":  "Critiques plans for gaps and improvements. Issues CRITIQUE_PASS or CRITIQUE_REJECT.",
    "analyst": "Analyzes inspection reports and triages issues. Writes analysis findings to INSPECTIONS/triage/ and ANALYSIS/.",
    "architect": "Designs architecture and writes specifications. Reviews plans and IMPLEMENTATION_PLANS for structural soundness.",
    "inspector": "Inspects codebase for errors and issues. Writes todo items and error reports to INSPECTIONS/.",
    "engineer": "Reports on the Nebula backlog by querying requirements, systems, and subsystems. Identifies priority work and stale items.",
    "engineer-ii": "Reports on the Nebula backlog by querying requirements, systems, and subsystems. Identifies priority work and stale items.",
    "engineer-iii": "Third engineer instance — same scope as Engineer II: full implementation agent. Reports on the Nebula backlog, writes code, runs commands.",
    "devops": "Infrastructure operations and systems administration — system scripts, container setup/maintenance, migrations, and sysadmin tasks. Expansion of engineer with sysadmin concerns.",
    "topologist": "Interactive representative of the terrain subsystem — verifies local docs match actual service configuration; validates specs/plans/work requests against live capabilities; offers running alternatives for unavailable services.",
    "rover": "Processes chat transcripts through the harvesting pipeline. Extracts specifications, code blocks, and agenda items to ROVER/ audit folder.",
}


# ── Auth helper ───────────────────────────────────────────────────

def _check_token(handler) -> bool:
    """Return True if the request is authorized.

    When AGENT_CHAT_TOKEN is set, requires an Authorization: Bearer <token>
    header matching the shared token. Uses constant-time comparison to
    prevent timing side-channel attacks.
    When unset, all requests pass.
    """
    if not AGENT_CHAT_TOKEN:
        return True
    auth = handler.headers.get("Authorization", "").strip()
    expected = f"Bearer {AGENT_CHAT_TOKEN}"
    return secrets.compare_digest(auth, expected)


# ── HTTP handler ───────────────────────────────────────────────────

class ChatHandler(BaseHTTPRequestHandler):
    """Lightweight HTTP handler — no framework dependency."""

    def log_message(self, fmt, *args):
        pass  # quiet

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        try:
            self._do_post()
        except Exception as e:
            self._send_json({"error": "Internal server error", "detail": str(e)}, 500)

    def _do_post(self):
        path = urlparse(self.path).path

        # Auth gate for agent-launch endpoints
        if path in ("/chat", "/chat/kill") and not _check_token(self):
            self._send_json({"error": "unauthorized"}, 401)
            return

        if path == "/chat":
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length)) if content_length > 0 else {}

            role = (body.get("role") or "").lower()
            message = (body.get("message") or "").strip()
            log_level = (body.get("log_level") or "INFO").upper().strip()

            if log_level not in ("NONE", "ERROR", "INFO", "DEBUG"):
                log_level = "INFO"

            if role not in VALID_ROLES:
                self._send_json(
                    {"error": f"Invalid role '{role}'. Use: {', '.join(VALID_ROLES)}"}, 400,
                )
                return

            if not message:
                self._send_json({"error": "message is required"}, 400)
                return

            # Concurrency gates + session creation (atomic to avoid TOCTOU)
            session_id = _generate_id()
            q = queue.Queue()

            with _sessions_lock:
                active_count = sum(
                    1 for s in _sessions.values()
                    if s.get("status") in ("starting", "running")
                )
                role_count = sum(
                    1 for s in _sessions.values()
                    if s.get("role") == role and s.get("status") in ("starting", "running")
                )

                if active_count >= AGENT_CHAT_MAX_GLOBAL:
                    self._send_json({
                        "error": f"Global session limit reached ({active_count}/{AGENT_CHAT_MAX_GLOBAL}). Wait for a session to finish."
                    }, 429)
                    return

                if role_count >= AGENT_CHAT_MAX_PER_ROLE:
                    self._send_json({
                        "error": f"Role '{role}' limit reached ({role_count}/{AGENT_CHAT_MAX_PER_ROLE}). Wait for the current {role} to finish."
                    }, 429)
                    return

                _sessions[session_id] = {
                    "role": role,
                    "status": "starting",
                    "pid": None,
                    "queue": q,
                    "started_at": datetime.utcnow().isoformat() + "Z",
                    "exit_code": None,
                }

            t = threading.Thread(
                target=_run_chat_worker, args=(session_id, role, message, log_level), daemon=True,
            )
            t.start()

            self._send_json({
                "session_id": session_id,
                "role": role,
                "status": "started",
            })
        elif path == "/chat/kill":
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length)) if content_length > 0 else {}
            session_id = body.get("session_id", "")
            if not session_id:
                self._send_json({"error": "session_id is required"}, 400)
                return
            with _sessions_lock:
                session = _sessions.get(session_id)
            if not session:
                self._send_json({"error": "session not found"}, 404)
                return
            pid = session.get("pid")
            if pid:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            with _sessions_lock:
                if session_id in _sessions:
                    _sessions[session_id]["status"] = "killed"
            session["queue"].put({"type": "error", "text": "Session killed by user"})
            session["queue"].put({"type": "done", "exit_code": 137})
            session["queue"].put({"type": "close"})
            self._send_json({"killed": True, "session_id": session_id})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_GET(self):
        try:
            self._do_get()
        except Exception as e:
            self._send_json({"error": "Internal server error", "detail": str(e)}, 500)

    def _do_get(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/chat/stream/") or path == "/chat/sessions":
            if not _check_token(self):
                self._send_json({"error": "unauthorized"}, 401)
                return

        if path == "/chat/agents":
            self._send_json({
                "agents": [
                    {"role": r, "label": r.capitalize(), "description": d}
                    for r, d in _AGENT_DESCRIPTIONS.items()
                ],
            })
            return

        if path == "/chat/sessions":
            with _sessions_lock:
                result = []
                for sid, s in list(_sessions.items()):
                    result.append({
                        "session_id": sid,
                        "role": s["role"],
                        "status": s["status"],
                        "pid": s.get("pid"),
                        "exit_code": s.get("exit_code"),
                        "started_at": s["started_at"],
                    })
            self._send_json({"sessions": result})
            return

        if path.startswith("/chat/stream/"):
            session_id = path.split("/chat/stream/", 1)[1]

            with _sessions_lock:
                session = _sessions.get(session_id)

            if not session:
                self._send_json({"error": "session not found"}, 404)
                return

            q = session["queue"]

            # SSE response
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            try:
                while True:
                    try:
                        event = q.get(timeout=30)
                    except queue.Empty:
                        # keepalive
                        try:
                            self.wfile.write(b": ping\n\n")
                            self.wfile.flush()
                        except (BrokenPipeError, ConnectionResetError):
                            break
                        continue

                    if event["type"] == "close":
                        break

                    payload = json.dumps(event, ensure_ascii=False)
                    try:
                        self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        break

                    # status already set by worker; no extra bookkeeping needed
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        if path == "/chat/health":
            self._send_json({"status": "ok", "port": int(os.environ.get("AGENT_CHAT_PORT", "3017"))})
            return

        self._send_json({"error": "not found"}, 404)


# ── Entry point ────────────────────────────────────────────────────

def _cleanup_old_sessions() -> None:
    """Remove terminal sessions older than 1 hour."""
    cutoff = datetime.utcnow().timestamp() - 3600
    with _sessions_lock:
        stale = [
            sid for sid, s in _sessions.items()
            if s.get("status") in ("done", "error", "timeout", "killed")
            and datetime.fromisoformat(s["started_at"].replace("Z", "")).timestamp() < cutoff
        ]
        for sid in stale:
            del _sessions[sid]
        if stale:
            print(f"[cleanup] Pruned {len(stale)} old chat session(s)")

def _cleanup_loop() -> None:
    """Background thread: periodically prune old sessions."""
    while True:
        time.sleep(600)  # every 10 minutes
        _cleanup_old_sessions()

def main():
    _ensure_dirs()
    threading.Thread(target=_cleanup_loop, daemon=True).start()
    port = int(os.environ.get("AGENT_CHAT_PORT", "3017"))
    bind = os.environ.get("AGENT_CHAT_BIND", "127.0.0.1")
    server = ThreadingHTTPServer((bind, port), ChatHandler)
    print(f"Agent chat server → http://{bind}:{port}")
    if AGENT_CHAT_TOKEN:
        print(f"  Auth: shared token active (AGENT_CHAT_TOKEN set)")
    else:
        print(f"  Auth: disabled (set AGENT_CHAT_TOKEN to enable)")
    print(f"  POST /chat               send message to agent")
    print(f"  GET  /chat/stream/<id>    SSE stream")
    print(f"  GET  /chat/sessions       active sessions")
    print(f"  GET  /chat/agents         available agents")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
