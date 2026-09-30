"""Hermetic tests for bin/supersede-record.sh — Decision 24 (cfcc9d65).

Runs the real script against a stub nebula REST server (ThreadingHTTPServer on
loopback, random port). No live services are touched; no real records are
mutated. Covers the ratified sequence invariant: if the verbatim archive is
not created, not readable, or fails its sha256 round-trip, the pointer
mutation on the old record must never run.

Fault injection is per-server-INSTANCE (attributes on the httpd object), so
tests cannot pollute each other through the shared handler class.
"""

import json
import subprocess
import threading
import unittest
import uuid
import os
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "bin" / "supersede-record.sh"

UUIDS = [f"00000000-0000-4000-8000-0000000000{i:02d}" for i in range(10)]

OLD_CONTENT = "stale operational content v1\nline two\n"


def rec(rid, **kw):
    r = {
        "id": rid,
        "recordType": kw.pop("recordType", "analysis"),
        "role": kw.pop("role", "engineer-ii"),
        "title": kw.pop("title", "A stale record"),
        "content": kw.pop("content", OLD_CONTENT),
        "tags": kw.pop("tags", ["status:open", "to:devops", "area:ci"]),
        "metadata": kw.pop("metadata", {}),
    }
    r.update(kw)
    return r


def sha(text):
    return sha256(text.encode()).hexdigest()


class _Handler(BaseHTTPRequestHandler):
    server_version = "StubNebula/1.0"

    def log_message(self, *a):  # silence
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if getattr(self.server, "break_get", False):
            self._send(500, {"error": "injected"})
            return
        rec = self.server.records.get(self.path.rsplit("/", 1)[-1])
        self._send(200 if rec else 404,
                   rec or {"error": "Agent record not found"})

    def do_POST(self):
        if self.server.fail_on_post:
            self._send(500, {"error": "post failure injected"})
            return
        payload = self._read()
        rec = dict(payload)
        rec["id"] = str(uuid.uuid4())
        # SUT contract: the API persists what was sent. When corrupt_post is
        # set, the stored/read-back content differs from what the script
        # believes it posted — exercising the sha256 round-trip gate.
        if getattr(self.server, "corrupt_post", False):
            rec["content"] = "TAMPERED"
        self.server.records[rec["id"]] = rec
        self.server.posted.append(payload)
        self._send(201, rec)

    def do_PATCH(self):
        rid = self.path.rsplit("/", 1)[-1]
        rec = self.server.records.get(rid)
        if rec is None:
            self._send(404, {"error": "Agent record not found"})
            return
        rec.update(self._read())
        self.server.patched.append(rid)
        self._send(200, rec)


class StubNebula:
    """Per-test stub server; state lives ON the httpd instance."""

    def __init__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.records = {}
        self.httpd.posted = []
        self.httpd.patched = []
        self.httpd.fail_on_post = False
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.httpd.server_port}"

    @property
    def records(self):
        return self.httpd.records

    @property
    def posted(self):
        return self.httpd.posted

    @property
    def patched(self):
        return self.httpd.patched

    def set_fail_post(self, v):
        self.httpd.fail_on_post = v

    def set_corrupt_post(self, v):
        self.httpd.corrupt_post = v

    def set_break_get(self, v):
        self.httpd.break_get = v

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class SupersedeRecordTests(unittest.TestCase):
    def setUp(self):
        self.srv = StubNebula()
        self.old_id, self.new_id, self.other = UUIDS[0], UUIDS[1], UUIDS[2]
        self.srv.records[self.old_id] = rec(self.old_id)
        self.srv.records[self.new_id] = rec(self.new_id, title="Successor runbook")
        self.env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"

    def tearDown(self):
        self.srv.stop()

    def run_script(self, *args):
        # Mode comes first per the documented usage; global options may trail.
        return subprocess.run(
            ["bash", str(SCRIPT), *args,
             "--nebula-url", self.srv.url,
             "--role", "tester", "--model", "test/model"],
            capture_output=True, text=True, env=self.env, timeout=60)

    # ── supersede: happy path ────────────────────────────────────────

    def test_supersede_happy_path(self):
        r = self.run_script("supersede", "--old", self.old_id, "--new", self.new_id,
                            "--reason", "runbook reissued after #647")
        self.assertEqual(0, r.returncode, r.stderr)
        # archive record created with the ratified shape
        self.assertEqual(1, len(self.srv.posted))
        archive = self.srv.posted[0]
        self.assertEqual("report", archive["recordType"])
        self.assertIn("type:archive", archive["tags"])
        self.assertIn(f"supersedes-relation:{self.old_id[:8]}", archive["tags"])
        self.assertIn(f"superseded-by:{self.new_id[:8]}", archive["tags"])
        self.assertIn("captured pre-supersession", archive["title"])
        # verbatim segment is byte-identical to the original body
        verbatim = archive["content"].split("\n---\n\n", 1)[1]
        self.assertEqual(sha(verbatim), sha(OLD_CONTENT))
        self.assertIn("sha256(content) =", archive["content"])
        # exactly ONE mutation: [SUPERSEDED → ] title, pointer body, tags
        self.assertEqual([self.old_id], self.srv.patched)
        old = self.srv.records[self.old_id]
        self.assertEqual(f"[SUPERSEDED → {self.new_id[:8]}] A stale record",
                         old["title"])
        self.assertIn(self.new_id, old["content"])
        # the pointer must name the ARCHIVE's server-assigned id
        archive_id = next(rid for rid, r in self.srv.records.items()
                          if "type:archive" in (r.get("tags") or []))
        self.assertIn(archive_id, old["content"])
        self.assertIn("sha256 of original content", old["content"])
        self.assertIn("status:superseded", old["tags"])
        self.assertIn(f"superseded-by:{self.new_id[:8]}", old["tags"])

    def test_supersede_drops_lifecycle_tags_keeps_audience(self):
        self.run_script("supersede", "--old", self.old_id, "--new", self.new_id)
        tags = self.srv.records[self.old_id]["tags"]
        self.assertNotIn("status:open", tags)          # lifecycle dropped
        self.assertIn("to:devops", tags)               # to:* breadcrumb kept
        self.assertIn("area:ci", tags)                 # domain tags kept

    def test_supersede_dry_run_no_writes(self):
        r = self.run_script("supersede", "--old", self.old_id,
                            "--new", self.new_id, "--dry-run")
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertIn("no writes performed", r.stdout)
        self.assertEqual([], self.srv.posted)
        self.assertEqual([], self.srv.patched)

    # ── supersede: the ratified sequence invariant ───────────────────

    def test_supersede_archive_failure_blocks_pointer(self):
        self.srv.set_fail_post(True)
        r = self.run_script("supersede", "--old", self.old_id, "--new", self.new_id)
        self.assertEqual(1, r.returncode)
        self.assertEqual([], self.srv.patched)
        self.assertIn("NOT mutated", r.stderr)

    def test_supersede_roundtrip_mismatch_blocks_pointer(self):
        self.srv.set_corrupt_post(True)
        r = self.run_script("supersede", "--old", self.old_id, "--new", self.new_id)
        self.assertEqual(1, r.returncode)
        self.assertIn("round-trip MISMATCH", r.stderr)
        self.assertEqual([], self.srv.patched)
        # the old record still carries its original content and title
        self.assertEqual("A stale record", self.srv.records[self.old_id]["title"])
        self.assertEqual(OLD_CONTENT, self.srv.records[self.old_id]["content"])

    def test_supersede_archive_unreadable_blocks_pointer(self):
        self.srv.set_break_get(True)
        r = self.run_script("supersede", "--old", self.old_id, "--new", self.new_id)
        self.srv.set_break_get(False)
        self.assertEqual(1, r.returncode)
        self.assertEqual([], self.srv.patched)

    # ── supersede: policy refusals (nothing written, exit 2) ─────────

    def test_supersede_refuses_prompt_record(self):
        self.srv.records[self.old_id] = rec(self.old_id, recordType="prompt")
        r = self.run_script("supersede", "--old", self.old_id, "--new", self.new_id)
        self.assertEqual(2, r.returncode)
        self.assertIn("I4", r.stderr)
        self.assertEqual([], self.srv.patched)

    def test_supersede_refuses_response_record(self):
        self.srv.records[self.old_id] = rec(self.old_id, recordType="response")
        r = self.run_script("supersede", "--old", self.old_id, "--new", self.new_id)
        self.assertEqual(2, r.returncode)
        self.assertEqual([], self.srv.patched)

    def test_supersede_refuses_type_history_tag(self):
        self.srv.records[self.old_id] = rec(self.old_id, tags=["type:history"])
        r = self.run_script("supersede", "--old", self.old_id, "--new", self.new_id)
        self.assertEqual(2, r.returncode)
        self.assertEqual([], self.srv.patched)

    def test_supersede_refuses_already_superseded(self):
        self.srv.records[self.old_id] = rec(
            self.old_id, tags=["status:superseded", "superseded-by:deadbeef"])
        r = self.run_script("supersede", "--old", self.old_id, "--new", self.new_id)
        self.assertEqual(2, r.returncode)
        self.assertIn("already status:superseded", r.stderr)
        self.assertIn("deadbeef", r.stderr)
        self.assertEqual([], self.srv.patched)

    def test_supersede_refuses_already_retired(self):
        self.srv.records[self.old_id] = rec(self.old_id, tags=["status:retired"])
        r = self.run_script("supersede", "--old", self.old_id, "--new", self.new_id)
        self.assertEqual(2, r.returncode)
        self.assertEqual([], self.srv.patched)

    def test_supersede_refuses_missing_successor(self):
        r = self.run_script("supersede", "--old", self.old_id, "--new", self.other)
        self.assertEqual(1, r.returncode)
        self.assertIn("not found", r.stderr)
        self.assertEqual([], self.srv.posted)
        self.assertEqual([], self.srv.patched)

    def test_supersede_requires_new_flag(self):
        r = self.run_script("supersede", "--old", self.old_id)
        self.assertEqual(2, r.returncode)
        self.assertIn("--new", r.stderr)

    def test_unknown_option_is_usage_error(self):
        r = self.run_script("supersede", "--old", self.old_id,
                            "--new", self.new_id, "--bogus")
        self.assertEqual(2, r.returncode)

    # ── retire (Amendment 1) ─────────────────────────────────────────

    def test_retire_happy_path(self):
        r = self.run_script("retire", "--old", self.old_id,
                            "--note", "scenario closed by #647")
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertEqual([], self.srv.posted)          # no archive
        self.assertEqual([self.old_id], self.srv.patched)
        old = self.srv.records[self.old_id]
        self.assertTrue(old["title"].startswith("[RETIRED "))
        self.assertIn("A stale record", old["title"])
        self.assertIn("status:retired", old["tags"])
        self.assertNotIn("status:open", old["tags"])
        self.assertNotIn("superseded-by:", " ".join(old["tags"]))
        # body unchanged; closure note lives in metadata (not an emptied body)
        self.assertEqual(OLD_CONTENT, old["content"])
        self.assertEqual("scenario closed by #647",
                         old["metadata"]["retired"]["note"])
        self.assertIn("Decision 24", old["metadata"]["retired"]["convention"])

    def test_retire_dry_run_no_writes(self):
        r = self.run_script("retire", "--old", self.old_id,
                            "--note", "x", "--dry-run")
        self.assertEqual(0, r.returncode)
        self.assertIn("no writes performed", r.stdout)
        self.assertEqual([], self.srv.patched)
        self.assertEqual([], self.srv.posted)

    def test_retire_refuses_prompt_record(self):
        self.srv.records[self.old_id] = rec(self.old_id, recordType="prompt")
        r = self.run_script("retire", "--old", self.old_id, "--note", "x")
        self.assertEqual(2, r.returncode)
        self.assertEqual([], self.srv.patched)

    def test_retire_refuses_already_superseded(self):
        self.srv.records[self.old_id] = rec(
            self.old_id, tags=["status:superseded", "superseded-by:deadbeef"])
        r = self.run_script("retire", "--old", self.old_id, "--note", "x")
        self.assertEqual(2, r.returncode)
        self.assertEqual([], self.srv.patched)

    def test_retire_requires_note(self):
        r = self.run_script("retire", "--old", self.old_id)
        self.assertEqual(2, r.returncode)
        self.assertIn("--note", r.stderr)

    # ── script self-containment ──────────────────────────────────────

    def test_options_after_mode_are_accepted(self):
        """Global options may trail the mode and its arguments."""
        out = subprocess.run(
            ["bash", str(SCRIPT), "supersede",
             "--old", self.old_id, "--new", self.new_id, "--dry-run",
             "--nebula-url", self.srv.url],
            capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(0, out.returncode, out.stderr)
        self.assertIn("no writes performed", out.stdout)


if __name__ == "__main__":
    unittest.main()
