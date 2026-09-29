"""Hermetic tests for bin/post-change-log.sh (post + verify + self-repair).

No real services: a threaded local HTTP server stands in for assembly-srv
:3107 and serves the observed response shapes. The tool is driven as a
subprocess exactly as a role would invoke it, with --assembly-url pointed at
the mock.

The contract under test, and the reason this file exists:

  - A post is VERIFIED by re-reading the thread from the DETAIL endpoint.
  - Verification must never consult the forum LIST endpoint. It does not
    project `body`, so an empty `body` there says nothing about the write.
    Reading a projection and concluding the field was dropped is what made
    five healthy threads get hand-patched with duplicate "repair" comments
    (DBA record 2a51e900). Two tests below pin that: one asserts the list
    endpoint is never called, the other hands the tool a list that DOES carry
    the body while detail is empty, and requires it to repair anyway.
  - A genuinely empty detail body is repaired once, as the first comment, and
    the thread is re-verified; success is reported as `repaired`.
  - If body and comments are both empty after the repair, the tool exits
    non-zero rather than reporting a post nobody can read.
  - The submitted body round-trips byte-for-byte, including embedded triple
    double-quotes and backslashes — the payload now travels via the
    environment instead of being interpolated into Python source, which used
    to corrupt it.
  - Unverifiable is not the same as failed: if the detail read fails the tool
    warns and exits 0, because a non-zero exit invites a retry that duplicates
    the thread.
"""

import json
import os
import subprocess
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_SELF = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_SELF, "..", ".."))   # bin/tests/ -> repo root
TOOL = os.path.join(_REPO, "bin", "post-change-log.sh")

UID = "11111111-2222-3333-4444-555555555555"
THREAD_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


# ── mock assembly-srv ────────────────────────────────────────────────────

class MockAssembly:
    """In-memory forums with a switchable persistence mode.

    mode:
      "store"          body persists on the thread (the healthy path)
      "drop_body"      thread body comes back empty; comments persist
      "drop_everything" neither body nor comments ever persist
    """

    def __init__(self, mode="store", list_carries_body=False, detail_unreadable=False):
        self.mode = mode
        self.list_carries_body = list_carries_body
        self.detail_unreadable = detail_unreadable
        self.threads = {}
        self.comments = {}
        self.list_calls = 0
        self.posts = 0
        self.comment_posts = 0

    def create(self, title, body):
        self.posts += 1
        tid = THREAD_ID if len(self.threads) == 0 else f"extra-{len(self.threads)}"
        stored = "" if self.mode == "drop_body" else body
        if self.mode == "drop_everything":
            stored = ""
        self.threads[tid] = {"id": tid, "title": title, "body": stored}
        self.comments.setdefault(tid, [])
        return tid

    def add_comment(self, tid, body):
        self.comment_posts += 1
        if self.mode == "drop_everything":
            return
        self.comments.setdefault(tid, []).append({"id": f"c{self.comment_posts}", "body": body})


def make_handler(store):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):        # keep the test run quiet
            pass

        def _send(self, code, payload):
            raw = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n).decode()) if n else {}

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/api/users":
                return self._send(200, [{"id": UID, "name": "DBA"}])
            if path.startswith("/api/forums/") and path.endswith("/threads"):
                store.list_calls += 1
                items = []
                for tid, th in store.threads.items():
                    item = {"id": tid, "title": th["title"]}
                    # The live list endpoint does NOT project body; this flag
                    # lets a test make it lie in the tool's favour.
                    if store.list_carries_body:
                        item["body"] = th["body"]
                    items.append(item)
                return self._send(200, {"items": items, "total": len(items)})
            if path.startswith("/api/forums/threads/"):
                tid = path.rsplit("/", 1)[-1]
                if store.detail_unreadable or tid not in store.threads:
                    return self._send(500, {"error": "boom"})
                th = store.threads[tid]
                return self._send(200, {"thread": dict(th), "comments": store.comments.get(tid, [])})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            path = self.path.split("?")[0]
            if path.endswith("/threads"):
                p = self._body()
                tid = store.create(p.get("title", ""), p.get("body", ""))
                return self._send(201, {"id": tid, "title": p.get("title", "")})
            if path.startswith("/api/forums/threads/") and path.endswith("/comments"):
                tid = path.split("/api/forums/threads/")[1].split("/")[0]
                p = self._body()
                store.add_comment(tid, p.get("body", ""))
                return self._send(201, {"id": "c1", "role": p.get("role", "")})
            return self._send(404, {"error": "not found"})

    return Handler


class PostChangeLogTest(unittest.TestCase):
    def run_tool(self, store, *args, stdin_text=None):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            base = f"http://127.0.0.1:{srv.server_address[1]}"
            argv = [TOOL, "--assembly-url", base, "--role", "DBA", *args]
            if stdin_text is None:
                proc = subprocess.run(argv, capture_output=True, text=True,
                                      stdin=subprocess.DEVNULL, timeout=60)
            else:
                proc = subprocess.run(argv, capture_output=True, text=True,
                                      input=stdin_text, timeout=60)
        finally:
            srv.shutdown()
            srv.server_close()
        return proc

    # ── healthy path ────────────────────────────────────────────────
    def test_persisted_body_is_verified_against_detail(self):
        store = MockAssembly()
        p = self.run_tool(store, "--title", "T", "--body", "B" * 120)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("verified body=120 chars", p.stdout)
        self.assertEqual(store.comment_posts, 0, "must not repair a healthy post")

    def test_never_reads_the_forum_list_endpoint(self):
        """The list endpoint cannot prove anything about a write; the tool
        must not even consult it."""
        store = MockAssembly()
        self.run_tool(store, "--title", "T", "--body", "B" * 50)
        self.assertEqual(store.list_calls, 0,
                         "post-change-log.sh must verify via the detail endpoint only")

    def test_list_carrying_the_body_does_not_satisfy_verification(self):
        """Regression for DBA record 2a51e900: a list that DOES carry the
        body while the detail read is empty must still be treated as empty.
        The old hand-rolled check read the list and 'repaired' healthy posts."""
        store = MockAssembly(mode="drop_body", list_carries_body=True)
        p = self.run_tool(store, "--title", "T", "--body", "B" * 90)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("repaired", p.stdout)
        self.assertEqual(store.comment_posts, 1)

    # ── repair path ─────────────────────────────────────────────────
    def test_empty_detail_body_is_repaired_via_first_comment(self):
        store = MockAssembly(mode="drop_body")
        p = self.run_tool(store, "--title", "T", "--body", "important summary")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("repaired", p.stdout)
        self.assertIn("first comment", p.stdout)
        self.assertEqual(store.comment_posts, 1)
        self.assertEqual(store.comments[THREAD_ID][0]["body"], "important summary")

    def test_unrecoverable_body_fails_loudly(self):
        store = MockAssembly(mode="drop_everything")
        p = self.run_tool(store, "--title", "T", "--body", "important summary")
        self.assertEqual(p.returncode, 1)
        self.assertIn("unrecoverable", p.stderr)
        self.assertNotIn("verified", p.stdout)

    def test_unverifiable_detail_warns_but_does_not_fail(self):
        """A failed detail read must not exit non-zero: that would invite a
        retry and duplicate a thread that may be perfectly fine."""
        store = MockAssembly(detail_unreadable=True)
        p = self.run_tool(store, "--title", "T", "--body", "B" * 40)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("not verified", p.stderr)
        self.assertIn("OK 201", p.stdout)

    # ── payload fidelity ────────────────────────────────────────────
    def test_body_with_triple_quotes_and_backslashes_round_trips(self):
        """The payload travels via the environment now; before that it was
        interpolated into Python source, where an embedded triple double-quote
        broke the literal and backslashes were eaten as escapes."""
        tricky = 'has """ triple quotes, a backslash \\ and a tab\there plus éü'
        store = MockAssembly()
        p = self.run_tool(store, "--title", 'T with """ quotes', "--body", tricky)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(store.threads[THREAD_ID]["body"], tricky)
        self.assertEqual(store.threads[THREAD_ID]["title"], 'T with """ quotes')

    def test_newlines_and_markdown_survive(self):
        body = "# Heading\n\n- one\n- two\n\n```\ncode block\n```\n"
        store = MockAssembly()
        p = self.run_tool(store, "--title", "T", "--body", body)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(store.threads[THREAD_ID]["body"], body)

    # ── preserved behaviour ─────────────────────────────────────────
    def test_body_from_stdin(self):
        store = MockAssembly()
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            base = f"http://127.0.0.1:{srv.server_address[1]}"
            p = subprocess.run([TOOL, "--assembly-url", base, "--role", "DBA",
                                "--title", "From stdin"],
                               input="piped body", capture_output=True, text=True, timeout=60)
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(store.threads[THREAD_ID]["body"], "piped body")

    def test_missing_title_is_a_usage_error(self):
        store = MockAssembly()
        p = self.run_tool(store, "--body", "no title")
        self.assertEqual(p.returncode, 2)
        self.assertIn("--title is required", p.stderr)
        self.assertEqual(store.posts, 0)

    def test_missing_body_is_a_usage_error(self):
        store = MockAssembly()
        p = self.run_tool(store, "--title", "no body")
        self.assertEqual(p.returncode, 2)
        self.assertEqual(store.posts, 0)

    def test_unresolvable_role_exits_one(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(MockAssembly()))
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            base = f"http://127.0.0.1:{srv.server_address[1]}"
            p = subprocess.run([TOOL, "--assembly-url", base, "--title", "T",
                                "--body", "B", "--role", "nobody"],
                               capture_output=True, text=True,
                               stdin=subprocess.DEVNULL, timeout=60)
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(p.returncode, 1)
        self.assertIn("could not resolve", p.stderr)

    def test_help_exits_zero(self):
        p = subprocess.run([TOOL, "--help"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(p.returncode, 0)
        self.assertIn("Usage:", p.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
