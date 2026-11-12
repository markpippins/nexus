"""`status` must report what a browser gets, not whether a socket is open.

The fleet failure this guards is the quiet one: a dev server binds 0.0.0.0 and
answers `curl 127.0.0.1:<port>` with 200, but rejects the machine's hostname
with `403 Blocked request. This host (...) is not allowed.` The old check only
looked for a listening socket, so it printed UP for UIs nobody could open and
exited 0 -- the condition was invisible to both operators and CI.
"""
from __future__ import annotations

import http.server
import pathlib
import socket
import subprocess
import threading
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "bin" / "start-nexus-uis.sh"
BLOCK_PAGE = (
    'Blocked request. This host ("titanium") is not allowed.\n'
    "To allow this host, add it to `server.allowedHosts` in vite.config.js.\n"
)


def _serve(status: int, body: str):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib naming
            payload = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _health(port: int) -> str:
    out = subprocess.run(
        ["bash", "-c", 'source "$1"; ui_health "$2"', "_", str(SCRIPT), str(port)],
        capture_output=True, text=True, timeout=60,
    )
    return out.stdout.strip()


def _status_rc(port: int) -> int:
    out = subprocess.run(
        ["bash", "-c",
         'source "$1"; UI_NAMES=(probe-ui); UI_PORTS[probe-ui]="$2"; '
         'cmd_status_all >/dev/null 2>&1 || echo "rc=$?"',
         "_", str(SCRIPT), str(port)],
        capture_output=True, text=True, timeout=60,
    )
    return int(out.stdout.strip().removeprefix("rc=")) if out.stdout.strip() else 0


def _closed_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class UiStatusHealthTests(unittest.TestCase):
    def setUp(self):
        self._servers = []

    def tearDown(self):
        for server in self._servers:
            server.shutdown()
            server.server_close()

    def _serve(self, status, body):
        server = _serve(status, body)
        self._servers.append(server)
        return server.server_address[1]

    def test_healthy_ui_is_ok_and_status_passes(self):
        port = self._serve(200, "<!doctype html><title>ok</title>")
        self.assertEqual(_health(port), "up")
        self.assertEqual(_status_rc(port), 0)

    def test_vite_host_block_is_reported_blocked_and_fails_status(self):
        port = self._serve(403, BLOCK_PAGE)
        self.assertEqual(_health(port), "blocked")
        self.assertEqual(_status_rc(port), 1,
                         "status must exit 1 when a UI rejects the host header")

    def test_app_level_403_is_not_mistaken_for_a_host_block(self):
        port = self._serve(403, "<h1>Forbidden: you need an admin role</h1>")
        self.assertEqual(_health(port), "up")

    def test_nothing_listening_is_down_and_fails_status(self):
        port = _closed_port()
        self.assertEqual(_health(port), "down")
        self.assertEqual(_status_rc(port), 1)

    def test_server_error_is_reported_as_error(self):
        port = self._serve(503, "boom")
        self.assertEqual(_health(port), "error")
        self.assertEqual(_status_rc(port), 1)


if __name__ == "__main__":
    unittest.main()
