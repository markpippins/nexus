#!/usr/bin/env python3
"""operator.server — HTTP + SSE server for the Operator service.

The Operator is the host personality for the Nexus UI set.
It handles chat messages from the messagebox, runs inference
via tackle's provider APIs, and proxies Nexus API calls.

Usage:
    python3 server.py                   # listen on port 3018
    OPERATOR_PORT=3018 python3 ...      # custom port

Endpoints:
    POST /chat                  —  { message, role?, session_id? } → { session_id, response }
    GET  /chat/stream/<id>      —  SSE stream of operator response
    GET  /chat/sessions         —  list recent sessions
    GET  /chat/health           —  liveness check
    POST /api/proxy/<service>   —  proxy to conduit/nebula/terrain
"""

import json
import os
import queue
import sys
import threading
import time
import uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# ── Ensure parent dir is importable ──────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_SCRIPT_DIR)  # nexus/python/
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from operator_svc.operator import respond
from operator_svc.api_proxy import proxy_request
from operator_svc.chat_store import get_recent_sessions
from operator_svc.lease_check import check_adoption

# ── Configuration ─────────────────────────────────────────────────
PORT = int(os.environ.get("OPERATOR_PORT", "3018"))
BIND = os.environ.get("OPERATOR_BIND", "127.0.0.1")

# ── Session state ─────────────────────────────────────────────────
_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()


def _get_or_create_session(session_id: str | None) -> str:
    """Get existing session or create a new one."""
    with _sessions_lock:
        if session_id and session_id in _sessions:
            return session_id
        new_id = session_id or str(uuid.uuid4())
        _sessions[new_id] = {
            "id": new_id,
            "created_at": time.time(),
            "status": "active",
            "queue": queue.Queue(),
        }
        return new_id


def _drain_queue(sess: dict) -> None:
    """Drop stale events left over from a previous request on this session.

    The worker thread always enqueues a trailing ``{"type": "done"}``
    sentinel (consumed only by the SSE stream path, which reads until
    ``done``). POST-only chat requests consume exactly one item, so the
    sentinel — plus any unconsumed response/error items from an
    interrupted turn — stays in the queue and is what the NEXT request's
    single ``get()`` pops, producing an empty (or wrong) reply.

    Draining before each new request keeps the queue aligned so the
    handler's ``get()`` reads THIS request's response.
    """
    with _sessions_lock:
        while True:
            try:
                sess["queue"].get_nowait()
            except queue.Empty:
                break


# ── HTTP Handler ──────────────────────────────────────────────────

class OperatorHandler(BaseHTTPRequestHandler):
    """Handle HTTP requests for the Operator service."""

    def log_message(self, format, *args):
        """Suppress default logging — we use our own."""
        pass

    def _send_json(self, status: int, data: dict):
        """Send a JSON response."""
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        """Read and parse the request body."""
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    # ── Routes ───────────────────────────────────────────────────

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/chat/health":
            self._handle_health()
        elif path == "/chat/sessions":
            self._handle_sessions()
        elif path.startswith("/chat/stream/"):
            session_id = path.split("/")[-1]
            self._handle_stream(session_id)
        else:
            self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/chat":
            self._handle_chat()
        elif path.startswith("/api/proxy/"):
            service = path.split("/")[-1]
            self._handle_proxy(service)
        else:
            self._send_json(404, {"error": "Not found"})

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ── Chat ─────────────────────────────────────────────────────

    def _handle_chat(self):
        """Handle POST /chat — accept message, run inference, return response."""
        body = self._read_body()
        user_message = body.get("message", "")
        role = body.get("role", "operator")
        session_id = body.get("session_id")
        log_level = body.get("log_level", "ERROR")

        if not user_message:
            self._send_json(400, {"error": "message is required"})
            return

        # ── Lease liveness check (continuity prerequisite, 65fe85a8) ──────
        # Resolve the requested role against tackle.role_leases BEFORE any
        # role-scoped context is assembled (model config, procedure cards,
        # future continuity digests). Modes: off / warn (default) / enforce.
        adoption = check_adoption(role)
        if not adoption["allowed"]:
            self._send_json(403, {
                "error": "role adoption refused (lease check)",
                "role": role,
                "mode": adoption["mode"],
                "reason": adoption["reason"],
            })
            return

        session_id = _get_or_create_session(session_id)

        # Run inference in a background thread, stream via queue
        sess = _sessions[session_id]
        sess["status"] = "processing"

        # A previous request on this session leaves a trailing "done"
        # sentinel (and possibly unconsumed response/error items) in the
        # queue. Drain it so this request's single get() reads THIS
        # request's response instead of stale leftovers.
        _drain_queue(sess)

        def _run():
            try:
                result = respond(
                    user_message=user_message,
                    session_id=session_id,
                    role=role,
                    log_level=log_level,
                )
                sess["queue"].put({"type": "response", "data": result})
            except Exception as e:
                sess["queue"].put({"type": "error", "error": str(e)})
            finally:
                sess["queue"].put({"type": "done"})
                sess["status"] = "complete"

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

        # Wait for the response and return it directly.
        # The frontend uses the POST response body for the text.
        # SSE stream is available as a secondary path for future streaming use.
        try:
            result = sess["queue"].get(timeout=120)
            if result["type"] == "response":
                self._send_json(200, {
                    "session_id": session_id,
                    "role": role,
                    "status": "complete",
                    "response": result["data"]["response"],
                    "model_identifier": result["data"].get("model_identifier", ""),
                    "latency_ms": result["data"].get("latency_ms", 0),
                    "lease_check": {
                        "mode": adoption["mode"],
                        "adopted": adoption["adopted"],
                        "lease_ref": (adoption["lease"] or {}).get("id"),
                        "reason": adoption["reason"],
                    },
                })
            elif result["type"] == "error":
                self._send_json(500, {
                    "session_id": session_id,
                    "error": result["error"],
                })
            else:
                self._send_json(200, {
                    "session_id": session_id,
                    "status": "complete",
                    "response": "",
                })
        except Exception:
            self._send_json(504, {
                "session_id": session_id,
                "error": "Response timed out",
            })

    # ── SSE Stream ───────────────────────────────────────────────

    def _handle_stream(self, session_id: str):
        """Handle GET /chat/stream/<id> — SSE stream of response."""
        sess = _sessions.get(session_id)
        if not sess:
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        try:
            # Send initial connection event so the client knows we're alive
            init_data = json.dumps({"type": "connected", "session_id": session_id})
            self.wfile.write(f"data: {init_data}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    event = sess["queue"].get(timeout=30)
                except queue.Empty:
                    # Send keepalive ping
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue

                if event["type"] == "response":
                    # Send the full response as a single SSE event
                    data = json.dumps({"type": "line", "text": event["data"]["response"]})
                    self.wfile.write(f"data: {data}\n\n".encode())
                    self.wfile.flush()
                elif event["type"] == "error":
                    data = json.dumps({"type": "error", "text": event["error"]})
                    self.wfile.write(f"data: {data}\n\n".encode())
                    self.wfile.flush()
                elif event["type"] == "done":
                    data = json.dumps({"type": "done", "exit_code": 0})
                    self.wfile.write(f"data: {data}\n\n".encode())
                    self.wfile.flush()
                    break
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ── Sessions ─────────────────────────────────────────────────

    def _handle_sessions(self):
        """Handle GET /chat/sessions — list recent sessions."""
        sessions = get_recent_sessions(limit=20)
        self._send_json(200, {"sessions": sessions})

    # ── Health ───────────────────────────────────────────────────

    def _handle_health(self):
        """Handle GET /chat/health — liveness check."""
        self._send_json(200, {
            "status": "ok",
            "service": "operator",
            "version": "0.1.0",
        })

    # ── API Proxy ────────────────────────────────────────────────

    def _handle_proxy(self, service: str):
        """Handle POST /api/proxy/<service> — proxy to Nexus service."""
        body = self._read_body()
        path = body.get("path", "/")
        method = body.get("method", "GET")
        payload = body.get("body")

        result = proxy_request(
            service=service,
            path=path,
            method=method,
            body=payload,
        )

        self._send_json(result["status"], {
            "data": result["data"],
            "error": result["error"],
        })


# ── Main ──────────────────────────────────────────────────────────

def main():
    """Start the Operator HTTP server."""
    server = ThreadingHTTPServer((BIND, PORT), OperatorHandler)
    print(f"Operator service listening on {BIND}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down Operator service.")
        server.shutdown()


if __name__ == "__main__":
    main()
