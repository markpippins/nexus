"""Hermetic tests for bin/substance-service.sh.

Background (incident 2026-09-27, records fd81b748 / 2d370d65): substance
runs as a systemd USER unit; agent shells lack XDG_RUNTIME_DIR, so manual
restarts bypassed systemd and orphaned the unit. The helper must guarantee
that every systemctl invocation reaches the user manager with
XDG_RUNTIME_DIR set, and never fall back to bare uvicorn processes.

Strategy: the script is driven with SYSTEMCTL/JOURNALCTL pointed at tiny
bash stubs that (a) record their argv + the XDG_RUNTIME_DIR they saw and
(b) emit canned output. No real systemd, no real network, no root.

Run:
  python3 -m pytest bin/tests/test_substance_service.py -v
"""
import os
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(REPO_ROOT, "bin", "substance-service.sh")


def _stub_systemctl(tmp, outcomes=None):
    """Write a systemctl stub that logs argv+env and replays outcomes.

    `outcomes` maps the first argument after `--user` (e.g. "status",
    "restart") to an exit code; default is 0 for everything.
    """
    outcomes = outcomes or {}
    path = os.path.join(tmp, "systemctl-stub")
    log = os.path.join(tmp, "systemctl.log")
    body = f"""#!/usr/bin/env bash
first="$1"; shift
echo "XDG_RUNTIME_DIR=${{XDG_RUNTIME_DIR:-UNSET}} argv=$first $*" >> "{log}"
code="${{OUTCOMES[$first]:-0}}"
exit "$code"
"""
    # associative-array OUTCOMES passed via env (comma/colon encoded) is
    # overkill — encode as simple KEY=CODE assignments prepended by caller.
    body = body.replace("${OUTCOMES[$first]:-0}", "${OUTCOME:-0}")
    with open(path, "w") as fh:
        fh.write(body)
    os.chmod(path, 0o755)
    return path, log


def _stub_journalctl(tmp):
    path = os.path.join(tmp, "journalctl-stub")
    log = os.path.join(tmp, "journalctl.log")
    with open(path, "w") as fh:
        fh.write(
            "#!/usr/bin/env bash\n"
            'echo "XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-UNSET} argv=$*" >> "%s"\n'
            'echo "sep 27 12:00:00 titanium python3[1]: stub log line"\n'
            "exit 0\n" % log
        )
    os.chmod(path, 0o755)
    return path, log


class SubstanceServiceHelperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="substance-svc-test-")
        self.systemctl, self.systemctl_log = _stub_systemctl(self.tmp)
        self.journalctl, self.journalctl_log = _stub_journalctl(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_script(self, *args, outcome="0", health_url="http://127.0.0.1:1/healthz", extra_env=None):
        env = dict(os.environ)
        env["SYSTEMCTL"] = self.systemctl
        env["JOURNALCTL"] = self.journalctl
        env["OUTCOME"] = outcome
        env["SUBSTANCE_HEALTH_URL"] = health_url
        # Start from a state WITHOUT XDG_RUNTIME_DIR — that is the exact
        # condition this script exists to survive.
        env.pop("XDG_RUNTIME_DIR", None)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", SCRIPT, *args], capture_output=True, text=True, env=env
        )

    def read_log(self, path):
        with open(path) as fh:
            return fh.read()

    # ── static checks ─────────────────────────────────────────────────

    def test_script_exists_and_is_bash(self):
        self.assertTrue(os.path.exists(SCRIPT), f"{SCRIPT} missing")
        with open(SCRIPT) as fh:
            first = fh.readline().strip()
        self.assertEqual(first, "#!/usr/bin/env bash")

    def test_sets_xdg_runtime_dir_default(self):
        """The script must default XDG_RUNTIME_DIR to /run/user/<uid>."""
        with open(SCRIPT) as fh:
            body = fh.read()
        self.assertIn('XDG_RUNTIME_DIR="/run/user/$(id -u)"', body)
        self.assertIn("export XDG_RUNTIME_DIR", body)

    # ── behaviour: every action reaches the USER manager ──────────────

    def test_status_sets_xdg_and_uses_user_bus(self):
        proc = self.run_script("status")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        line = self.read_log(self.systemctl_log).strip().splitlines()[-1]
        self.assertTrue(line.startswith("XDG_RUNTIME_DIR=/run/user/"), line)
        # The stub's argv includes the literal `--user` flag — proof the
        # call targeted the USER manager, not the system bus.
        self.assertIn("argv=--user status --no-pager -l substance.service", line)

    def test_restart_sets_xdg_and_uses_user_bus(self):
        proc = self.run_script("restart")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        line = self.read_log(self.systemctl_log).strip().splitlines()[-1]
        self.assertTrue(line.startswith("XDG_RUNTIME_DIR=/run/user/"), line)
        self.assertIn("argv=--user restart substance.service", line)

    def test_start_stop_use_user_bus(self):
        for action in ("start", "stop"):
            proc = self.run_script(action)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            line = self.read_log(self.systemctl_log).strip().splitlines()[-1]
            self.assertIn(f"argv=--user {action} substance.service", line)

    def test_enable_now_forwards_extra_flags(self):
        proc = self.run_script("enable", "--now")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        line = self.read_log(self.systemctl_log).strip().splitlines()[-1]
        self.assertIn("argv=--user enable substance.service --now", line)

    def test_failure_reports_error_and_rc(self):
        proc = self.run_script("restart", outcome="5")
        self.assertEqual(proc.returncode, 5, proc.stdout + proc.stderr)
        self.assertIn("ERROR: systemctl --user restart substance.service failed (rc=5)", proc.stderr)
        # The bus-unreachable hint must be present — it is the diagnosis
        # this incident actually needed.
        self.assertIn("Failed to connect to bus", proc.stderr)

    def test_logs_uses_journalctl_user_unit(self):
        proc = self.run_script("logs", "17")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("stub log line", proc.stdout)
        line = self.read_log(self.journalctl_log).strip().splitlines()[-1]
        self.assertTrue(line.startswith("XDG_RUNTIME_DIR=/run/user/"), line)
        self.assertIn("--user -u substance.service -n 17", line)

    def test_logs_default_lines(self):
        proc = self.run_script("logs")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        line = self.read_log(self.journalctl_log).strip().splitlines()[-1]
        self.assertIn("-n 50", line)

    # ── health subcommand (stubbed endpoint) ──────────────────────────

    def test_health_ok_200(self):
        # Serve a canned 200 without a listener: python http.server one-shot
        # is overkill; instead point curl at a file:// URL is not HTTP, so
        # run a throwaway python http server.
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            proc = self.run_script(
                "health", health_url=f"http://127.0.0.1:{port}/healthz"
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("OK 200", proc.stdout)
        finally:
            srv.shutdown()

    def test_health_non_200_fails(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b"nope")

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            proc = self.run_script(
                "health", health_url=f"http://127.0.0.1:{port}/healthz"
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn("UNHEALTHY 503", proc.stderr)
        finally:
            srv.shutdown()

    def test_health_unreachable_fails(self):
        # Port 1 on localhost is reliably closed; curl must fail fast.
        proc = self.run_script("health", health_url="http://127.0.0.1:1/healthz")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("cannot reach", proc.stderr)

    # ── usage / guardrails ────────────────────────────────────────────

    def test_unknown_action_is_usage_error(self):
        proc = self.run_script("bogus")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unknown action", proc.stderr)

    def test_help_exits_zero(self):
        proc = self.run_script("--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Usage:", proc.stdout)

    def test_default_action_is_status(self):
        proc = self.run_script()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("argv=--user status", self.read_log(self.systemctl_log))

    def test_env_override_unit_name(self):
        proc = self.run_script("status", extra_env={"SUBSTANCE_UNIT": "other.service"})
        line = self.read_log(self.systemctl_log).strip().splitlines()[-1]
        self.assertIn("other.service", line)

    def test_xdg_runtime_dir_passthrough_respected(self):
        """If the caller HAS XDG_RUNTIME_DIR set, the script must keep it."""
        env_extra = {"XDG_RUNTIME_DIR": "/run/user/424242"}
        proc = self.run_script("status", extra_env=env_extra)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        line = self.read_log(self.systemctl_log).strip().splitlines()[-1]
        self.assertIn("XDG_RUNTIME_DIR=/run/user/424242", line)


if __name__ == "__main__":
    unittest.main()
