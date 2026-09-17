"""Hermetic tests for bin/mark-operator-go-applied (apply-lock guard, ledger half).

No real services: a threaded local HTTP server stands in for nebula-srv :3101
and serves the observed record shapes, including the double-encoded tag
corruption seen in live agent_records (e22fe9c4 / a9474045). Pins the full
contract from R1 b88227f0:

  - fresh tagging appends status:applied via PATCH (full-array semantics)
  - idempotent re-run: exit 3, no second PATCH, no duplicate tag
  - double-encoded tag fragments are normalized, not made worse; re-runs converge
  - non-operator-go records refuse (exit 2) without any mutation
  - missing records refuse (exit 1); unreachable server refuses (exit 1)
  - title/content are never sent in the PATCH — tags-only annotation
  - mixed batch: exit code is the max (all-fresh = 0, any refusal dominates)
"""

import contextlib
import importlib.machinery
import importlib.util
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_SELF = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_SELF, "..", ".."))   # bin/tests/ -> repo root
TOOL_PATH = os.path.join(_REPO, "bin", "mark-operator-go-applied")


def load_tool():
    loader = importlib.machinery.SourceFileLoader("mark_operator_go_applied", TOOL_PATH)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# ── mock nebula-srv ─────────────────────────────────────────────────────

class MockNebula:
    """In-memory agent_records store with PATCH history."""

    def __init__(self):
        self.records = {}
        self.patches = []          # (record_id, body_dict)
        self.fail_get_500 = False

    def add(self, rid, tags, title="OPERATOR GO", content="body"):
        self.records[rid] = {"id": rid, "title": title, "content": content, "tags": tags}
        return rid


def make_handler(store):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):   # silence the test run
            pass

        def _send(self, code, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if store.fail_get_500:
                self._send(500, {"error": "boom"})
                return
            rid = self.path.rsplit("/", 1)[-1]
            rec = store.records.get(rid)
            self._send(404 if rec is None else 200, rec if rec else {"error": "not found"})

        def do_PATCH(self):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode())
            rid = self.path.rsplit("/", 1)[-1]
            rec = store.records.get(rid)
            if rec is None:
                self._send(404, {"error": "not found"})
                return
            if "tags" in body:
                rec["tags"] = body["tags"]
            store.patches.append((rid, body))
            self._send(200, rec)

    return Handler


@contextlib.contextmanager
def mock_server(store):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()
        srv.server_close()


# ── parse_tags unit tests ───────────────────────────────────────────────

class ParseTagsTests(unittest.TestCase):
    def setUp(self):
        self.m = load_tool()

    def test_proper_list_passes_through(self):
        tags = ["to:dba", "type:decision", "operator:go"]
        self.assertEqual(self.m.parse_tags(tags), tags)

    def test_json_string_shape_parses(self):
        self.assertEqual(self.m.parse_tags('["operator:go", "grant:wave1"]'),
                         ["operator:go", "grant:wave1"])

    def test_double_encoded_fragments_repair(self):
        corrupted = ['["to:dba"', '"to:tester"', '"type:decision"',
                     '"operator:go"', '"grant:wave1"', '"grant:wave2"]']
        self.assertEqual(
            self.m.parse_tags(corrupted),
            ["to:dba", "to:tester", "type:decision", "operator:go",
             "grant:wave1", "grant:wave2"])

    def test_none_and_garbage_are_safe(self):
        self.assertEqual(self.m.parse_tags(None), [])
        self.assertEqual(self.m.parse_tags("operator:go"), ["operator:go"])
        self.assertEqual(self.m.parse_tags(42), ["42"])

    def test_dedupe_preserves_order(self):
        self.assertEqual(self.m.parse_tags(["b", "a", "b", "a"]), ["b", "a"])

    def test_build_new_tags(self):
        new, already = self.m.build_new_tags(["operator:go"], "status:applied")
        self.assertFalse(already)
        self.assertEqual(new, ["operator:go", "status:applied"])
        new, already = self.m.build_new_tags(["operator:go", "status:applied"],
                                             "status:applied")
        self.assertTrue(already)
        self.assertEqual(new, ["operator:go", "status:applied"])


# ── live-path tests against the mock server ────────────────────────────

class MarkAppliedLiveTests(unittest.TestCase):
    def setUp(self):
        self.m = load_tool()
        self.store = MockNebula()
        # the two real shapes from the batch: clean + corrupted
        self.clean_id = self.store.add(
            "11111111-1111-1111-1111-111111111111",
            ["to:dba", "to:tester", "type:decision", "operator:go",
             "grant:wave1", "grant:wave2"])
        self.dirty_id = self.store.add(
            "22222222-2222-2222-2222-222222222222",
            ['["to:dba"', '"to:lead-engineer"', '"type:decision"',
              '"operator:go"', '"grant:wave3"]'])
        self.not_go_id = self.store.add(
            "33333333-3333-3333-3333-333333333333",
            ["to:dba", "type:status-update"])
        self._srv = mock_server(self.store)
        self.base = self._srv.__enter__()
        self.addCleanup(self._srv.__exit__, None, None, None)
        self.client = self.m.Client(self.base)

    def tags_of(self, rid):
        return self.store.records[rid]["tags"]

    def test_fresh_tagging_appends_and_preserves_content(self):
        code = self.m.mark_applied(self.client, self.clean_id)
        self.assertEqual(code, 0)
        self.assertEqual(self.tags_of(self.clean_id)[-1], "status:applied")
        self.assertEqual(len(self.store.patches), 1)
        rid, body = self.store.patches[0]
        self.assertEqual(rid, self.clean_id)
        self.assertEqual(set(body.keys()), {"tags"})   # tags-only annotation
        rec = self.store.records[self.clean_id]
        self.assertEqual(rec["title"], "OPERATOR GO")  # untouched
        self.assertEqual(rec["content"], "body")       # untouched

    def test_rerun_is_idempotent_exit3_no_second_patch(self):
        self.m.mark_applied(self.client, self.clean_id)
        patches_after_first = len(self.store.patches)
        code = self.m.mark_applied(self.client, self.clean_id)
        self.assertEqual(code, 3)
        self.assertEqual(len(self.store.patches), patches_after_first)
        self.assertEqual(self.tags_of(self.clean_id).count("status:applied"), 1)

    def test_corrupted_record_normalized_not_worse(self):
        code = self.m.mark_applied(self.client, self.dirty_id)
        self.assertEqual(code, 0)
        tags = self.tags_of(self.dirty_id)
        self.assertEqual(tags[-1], "status:applied")
        for t in tags:   # no bracket/quote fragments survive the write
            self.assertFalse(t.startswith("["), t)
            self.assertFalse(t.endswith("]"), t)
            self.assertFalse(t.startswith('"'), t)
        # convergence: a re-run is a no-op
        self.assertEqual(self.m.mark_applied(self.client, self.dirty_id), 3)
        self.assertEqual(len(self.store.patches), 1)

    def test_non_operator_go_refuses_without_mutation(self):
        code = self.m.mark_applied(self.client, self.not_go_id)
        self.assertEqual(code, 2)
        self.assertEqual(self.store.patches, [])
        self.assertNotIn("status:applied", self.tags_of(self.not_go_id))

    def test_missing_record_exit1(self):
        code = self.m.mark_applied(self.client, "44444444-4444-4444-4444-444444444444")
        self.assertEqual(code, 1)
        self.assertEqual(self.store.patches, [])

    def test_unreachable_server_exit1(self):
        client = self.m.Client("http://127.0.0.1:1")   # nothing listens there
        code = self.m.mark_applied(client, self.clean_id)
        self.assertEqual(code, 1)

    def test_main_mixed_batch_takes_max_exit_code(self):
        # pre-tag one record so the batch spans all three paths:
        # fresh (0) + already (3) + refusal (2) → max = 3
        self.assertEqual(self.m.mark_applied(self.client, self.dirty_id), 0)
        old = os.environ.get("NEBULA_URL")
        os.environ["NEBULA_URL"] = self.base
        try:
            rc = self.m.main(["prog", self.clean_id, self.dirty_id, self.not_go_id])
            self.assertEqual(rc, 3)
        finally:
            if old is None:
                del os.environ["NEBULA_URL"]
            else:
                os.environ["NEBULA_URL"] = old
        # both operator-gos are now tagged exactly once; the non-go record untouched
        self.assertEqual(self.tags_of(self.clean_id).count("status:applied"), 1)
        self.assertEqual(self.tags_of(self.dirty_id).count("status:applied"), 1)
        self.assertNotIn("status:applied", self.tags_of(self.not_go_id))

    def test_main_uses_nebula_url_env(self):
        old = os.environ.get("NEBULA_URL")
        os.environ["NEBULA_URL"] = self.base
        try:
            rc = self.m.main(["prog", self.clean_id])
            self.assertEqual(rc, 0)
        finally:
            if old is None:
                del os.environ["NEBULA_URL"]
            else:
                os.environ["NEBULA_URL"] = old

    def test_main_no_args_usage_exit64(self):
        self.assertEqual(self.m.main(["prog"]), 64)


if __name__ == "__main__":
    unittest.main()
