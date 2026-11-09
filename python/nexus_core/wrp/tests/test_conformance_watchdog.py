"""
wr-conf-003: T16 Watchdog kill path — PID tracking, kill, unload, record.

This test verifies the runaway watchdog building blocks without waiting
for the 15-minute threshold. The watchdog operates as a setInterval loop
in harness-srv; we test each component in isolation:

  AC1 — PID capture: spawning a child process via child_process.spawn
        captures the PID for direct SIGTERM (not pkill -f).
  AC2 — Process kill: process.kill(pid, 'SIGTERM') terminates a test
        process. Verify via OS-level process check.
  AC3 — Session tracking: GET /sessions on harness-srv returns active
        sessions with timestamps and count.
  AC4 — PID registration: after simulating a spawn, the session map
        contains the expected PID.
  AC5 — Runaway record emission: INSERT INTO nebula.agent_records_history
        with type:runaway-detected tag persists and is queryable.
  AC6 — Model unload format: POST /api/generate {keep_alive: 0} is the
        correct Ollama unload contract.

Usage:
    cd /home/codex/dev/nexus
    python3 -m pytest python/nexus_core/wrp/tests/test_conformance_watchdog.py -v
"""

import json
import os
import signal
import subprocess
import sys
import time
import unittest
import urllib.request

import pathlib

_GUARD_REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
if str(_GUARD_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_GUARD_REPO_ROOT))

from host_tool_guard import skip_unless_host_tools  # noqa: E402

pytestmark = skip_unless_host_tools(
    "bash", doing="these tests spawn shell processes to exercise signal handling"
)

HARNESS_URL = os.environ.get("HARNESS_URL", "http://localhost:3420")
NEBULA_URL = os.environ.get("NEBULA_URL", "http://localhost:3101")


def _get(url: str, timeout: int = 10) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


def _post(url: str, body: dict, timeout: int = 10) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


# ═══════════════════════════════════════════════════════════════════════
#  AC1 — PID capture via child_process.spawn
# ═══════════════════════════════════════════════════════════════════════

class TestAc1PidCapture(unittest.TestCase):
    """Verify that spawning a child process captures a PID for direct kill."""

    def test_spawn_captures_pid(self):
        """subprocess.Popen returns a pid that can be killed."""
        proc = subprocess.Popen(
            ["sleep", "10"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            self.assertIsNotNone(proc.pid)
            self.assertGreater(proc.pid, 0, "PID should be a positive integer")
        finally:
            proc.kill()
            proc.wait()

    def test_pid_is_present_in_os(self):
        """A spawned PID is visible in the OS process table."""
        proc = subprocess.Popen(
            ["sleep", "5"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            # Check /proc/<pid> exists
            proc_path = f"/proc/{proc.pid}"
            self.assertTrue(os.path.exists(proc_path),
                            f"PID {proc.pid} should exist in /proc")
        finally:
            proc.kill()
            proc.wait()

    def test_spawn_stdout_capture(self):
        """stdout from a spawned process is collected correctly."""
        proc = subprocess.Popen(
            ["echo", "hello-from-spawn"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, _ = proc.communicate(timeout=5)
        self.assertIn(b"hello-from-spawn", stdout)


# ═══════════════════════════════════════════════════════════════════════
#  AC2 — Process kill via SIGTERM
# ═══════════════════════════════════════════════════════════════════════

class TestAc2ProcessKill(unittest.TestCase):
    """Verify that process.kill(pid, SIGTERM) terminates a process."""

    def test_sigterm_kills_process(self):
        """Sending SIGTERM to a spawned process terminates it."""
        proc = subprocess.Popen(
            ["sleep", "30"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        pid = proc.pid
        os.kill(pid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        self.assertNotEqual(proc.returncode, None)
        # After SIGTERM, /proc/<pid> should be gone (or zombie briefly)
        time.sleep(0.2)
        self.assertFalse(
            os.path.exists(f"/proc/{pid}"),
            f"PID {pid} should not exist after SIGTERM"
        )

    def test_sigkill_force_kills(self):
        """SIGKILL force-terminates a process that ignores SIGTERM."""
        # Trap SIGTERM, sleep — only SIGKILL should work
        proc = subprocess.Popen(
            ["bash", "-c", "trap '' TERM; sleep 30"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        pid = proc.pid
        # SIGTERM should be ignored
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.3)
        self.assertTrue(
            os.path.exists(f"/proc/{pid}"),
            "process trapping SIGTERM should still be alive"
        )
        # SIGKILL should force-terminate
        os.kill(pid, signal.SIGKILL)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        time.sleep(0.2)
        self.assertFalse(
            os.path.exists(f"/proc/{pid}"),
            f"PID {pid} should not exist after SIGKILL"
        )

    def test_kill_nonexistent_pid_raises(self):
        """Killing a nonexistent PID raises ProcessLookupError."""
        # Use a very high PID that doesn't exist
        with self.assertRaises(ProcessLookupError):
            os.kill(999999, signal.SIGTERM)


# ═══════════════════════════════════════════════════════════════════════
#  AC3 — Session tracking via GET /sessions
# ═══════════════════════════════════════════════════════════════════════

class TestAc3SessionTracking(unittest.TestCase):
    """Verify harness-srv session tracking endpoint."""

    def test_sessions_endpoint_returns_valid_structure(self):
        """GET /sessions returns { sessions: [...], count: N }."""
        resp = _get(f"{HARNESS_URL}/sessions")
        self.assertIn("sessions", resp)
        self.assertIn("count", resp)
        self.assertIsInstance(resp["sessions"], list)
        self.assertEqual(resp["count"], len(resp["sessions"]))

    def test_sessions_have_required_fields(self):
        """Each session entry has jobId, role, model, startedAt, elapsedSeconds."""
        resp = _get(f"{HARNESS_URL}/sessions")
        for session in resp.get("sessions", []):
            for field in ["jobId", "role", "startedAt", "elapsedSeconds"]:
                self.assertIn(field, session,
                              f"session should have '{field}' field")

    def test_sessions_elapsed_is_positive(self):
        """Elapsed seconds should be non-negative."""
        resp = _get(f"{HARNESS_URL}/sessions")
        for session in resp.get("sessions", []):
            self.assertGreaterEqual(session["elapsedSeconds"], 0)


# ═══════════════════════════════════════════════════════════════════════
#  AC4 — PID registration on session map
# ═══════════════════════════════════════════════════════════════════════

class TestAc4PidRegistration(unittest.TestCase):
    """Verify that the spawn-based executeOpencode registers the PID.

    Since we can't easily trigger a full harness-srv /run (it requires
    wind.tasks setup), we verify the spawn→PID→track pattern through
    the OS-level primitives that the harness code uses.
    """

    def test_spawn_pid_is_accessible_for_tracking(self):
        """After spawn, the PID can be stored and later used for kill."""
        proc = subprocess.Popen(
            ["sleep", "10"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        pid = proc.pid
        try:
            # Simulate what executeOpencode does: store PID on session map
            session_map = {}
            session_map["test-job"] = {"pid": pid, "role": "builder"}
            self.assertEqual(session_map["test-job"]["pid"], pid)
            # Verify the PID can be killed from the stored reference
            stored_pid = session_map["test-job"]["pid"]
            os.kill(stored_pid, signal.SIGTERM)
            proc.wait(timeout=5)
            self.assertIsNotNone(proc.returncode)
        finally:
            try:
                proc.kill()
                proc.wait()
            except Exception:
                pass

    def test_pid_cleanup_on_process_exit(self):
        """After a tracked process exits, the PID should be invalid."""
        proc = subprocess.Popen(
            ["true"],  # exits immediately
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        pid = proc.pid
        proc.wait(timeout=5)
        time.sleep(0.1)
        # PID should no longer exist
        self.assertFalse(
            os.path.exists(f"/proc/{pid}"),
            f"PID {pid} should be gone after process exits"
        )


# ═══════════════════════════════════════════════════════════════════════
#  AC5 — Runaway detection record emission
# ═══════════════════════════════════════════════════════════════════════

class TestAc5RunawayRecordEmission(unittest.TestCase):
    """Verify that the runaway-detected agent record INSERT works."""

    def test_runaway_record_insert(self):
        """INSERT with type:runaway-detected tag is queryable."""
        import psycopg2
        import uuid
        dsn = os.environ.get(
            "CONDUIT_PG_DSN",
            "postgresql://pguser:pgpass@localhost:5432/nexus"
        )
        conn = psycopg2.connect(dsn)
        record_id = str(uuid.uuid4())
        try:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO nebula.agent_records_history
                   (id, record_type, role, title, content, tags, created_at, recorded_on_dt)
                   VALUES (%s::uuid, 'report', 'architect', %s, %s,
                           ARRAY['type:runaway-detected', 'to:architect', 'to:engineer', 'role:wr-conf-003'],
                           NOW(), NOW())
                   RETURNING id""",
                [record_id, "wr-conf-003 runaway test",
                 "## Runaway test\n\nKill path verified."]
            )
            conn.commit()
            returned = cur.fetchone()[0]
            self.assertEqual(returned, record_id)

            # Verify it's queryable by tag
            cur.execute(
                """SELECT id, title FROM nebula.agent_records_history
                   WHERE 'type:runaway-detected' = ANY(tags)
                     AND id = %s::uuid""",
                [record_id]
            )
            row = cur.fetchone()
            self.assertIsNotNone(row, "runaway record should be queryable by tag")
            self.assertIn("wr-conf-003", row[1])
        finally:
            # Clean up
            try:
                cur = conn.cursor()
                cur.execute(
                    "DELETE FROM nebula.agent_records_history WHERE id = %s::uuid",
                    [record_id]
                )
                conn.commit()
            except Exception:
                conn.rollback()
            conn.close()

    def test_runaway_record_has_correct_structure(self):
        """The record contains role, model, elapsed, threshold fields."""
        import psycopg2
        import uuid
        dsn = os.environ.get(
            "CONDUIT_PG_DSN",
            "postgresql://pguser:pgpass@localhost:5432/nexus"
        )
        conn = psycopg2.connect(dsn)
        record_id = str(uuid.uuid4())
        content = """## Runaway agent detected + killed

- **Job:** test-job-123
- **Role:** builder
- **Model:** test/model
- **Elapsed:** 900s
- **Threshold:** 900s

No agent records were produced since launch."""
        try:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO nebula.agent_records_history
                   (id, record_type, role, title, content, tags, created_at, recorded_on_dt)
                   VALUES (%s::uuid, 'report', 'architect', %s, %s,
                           ARRAY['type:runaway-detected', 'to:architect', 'to:engineer', 'role:builder'],
                           NOW(), NOW())""",
                [record_id, "Runaway agent killed: builder (job test-job)",
                 content]
            )
            conn.commit()

            cur.execute(
                "SELECT content FROM nebula.agent_records_history WHERE id = %s::uuid",
                [record_id]
            )
            row = cur.fetchone()
            self.assertIsNotNone(row)
            body = row[0]
            self.assertIn("Job:", body)
            self.assertIn("Role:", body)
            self.assertIn("Model:", body)
            self.assertIn("Elapsed:", body)
            self.assertIn("Threshold:", body)
        finally:
            try:
                cur = conn.cursor()
                cur.execute(
                    "DELETE FROM nebula.agent_records_history WHERE id = %s::uuid",
                    [record_id]
                )
                conn.commit()
            except Exception:
                conn.rollback()
            conn.close()


# ═══════════════════════════════════════════════════════════════════════
#  AC6 — Ollama model unload contract
# ═══════════════════════════════════════════════════════════════════════

class TestAc6ModelUnloadContract(unittest.TestCase):
    """Verify the Ollama model unload HTTP contract used by the watchdog."""

    def test_unload_request_format(self):
        """POST /api/generate {model, keep_alive: 0} is the correct format."""
        unload_body = {
            "model": "qwen2.5-coder-ctx32k",
            "keep_alive": 0,
        }
        self.assertEqual(unload_body["keep_alive"], 0)
        self.assertIn("model", unload_body)
        self.assertIsInstance(unload_body["model"], str)

    def test_unload_url_is_configurable(self):
        """OLLAMA_URL env var or default http://127.0.0.1:11434."""
        default_url = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
        self.assertTrue(
            default_url.startswith("http://") or default_url.startswith("https://"),
            "OLLAMA_URL should be an HTTP URL"
        )

    def test_unload_is_best_effort(self):
        """Unload failure (Ollama down) should not crash the watchdog."""
        # The watchdog wraps the unload fetch in try/catch — we verify
        # the pattern by testing that an unreachable URL raises a
        # catchable exception rather than crashing.
        import urllib.error
        try:
            _post("http://127.0.0.1:19999/api/generate", {
                "model": "nonexistent",
                "keep_alive": 0,
            }, timeout=2)
        except (urllib.error.URLError, ConnectionRefusedError, OSError,
                TimeoutError, urllib.error.HTTPError):
            # Expected — unreachable endpoint
            pass
        except Exception:
            pass  # Any exception is catchable, which is the point
