"""
wr-conf-010: Receipt read-path regression (V110/V111) — a conduit-lineage
receipt in execution.receipts must be visible to every plan receipt read.

The D-T19-2(b) split moved the canonical conduit receipt write to
execution.receipts (request-scoped, lineage_source='conduit'), leaving
vision.receipts frozen (D-T19-2d). The read paths initially never followed:
get_plan_receipts and query_conduit_state still read vision.receipts only,
orphaning any plan whose receipts landed in execution.receipts (plan 1290
was invisible until the V110/V111 unified surface + repoint landed).

This test seeds a plan whose ONLY receipt is a conduit-lineage row in
execution.receipts (no vision.receipts row — the orphan condition) and
asserts:

  1. nebula.receipts_unified surfaces the receipt (and vision.receipts
     does NOT — proving placement is genuinely execution-only).
  2. nebula.plan_status derives PLAN_CREATE from the unified surface
     (the query_conduit_state backend).
  3. Live query_conduit_state (conduit-mcp :3100) buckets the plan in
     `pending` with derivedStatus PLAN_CREATE.
  4. Live get_plan_receipts (conduit-mcp :3100 → conduit-kernel :3103)
     returns the execution.receipts row (id = lineage_original_id).

Usage:
    cd /home/codex/dev/nexus
    python3 -m pytest python/nexus_core/wrp/tests/test_conformance_receipt_read_path.py -v
"""

import json
import os
import subprocess
import unittest
import urllib.error
import urllib.request
import uuid

DSN = os.environ.get("CONDUIT_PG_DSN", "postgres://pguser:pgpass@localhost:5432/nexus")
CONDUIT_MCP_URL = os.environ.get("CONDUIT_MCP_URL", "http://localhost:3100")


def _psql(sql: str, one: bool = False):
    """Run SQL via psql. Returns rows as list[list[str]] (one → single scalar)."""
    proc = subprocess.run(
        ["psql", DSN, "-At", "-F", "\t", "-c", sql],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr.strip()}")
    rows = [line.split("\t") for line in proc.stdout.strip().splitlines() if line]
    if one:
        return rows[0][0] if rows else None
    return rows


def _mcp_call(name: str, arguments: dict) -> dict:
    """Call a conduit-mcp tool via Streamable-HTTP JSON-RPC and unwrap text payload."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}
    req = urllib.request.Request(
        CONDUIT_MCP_URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"mcp {name} failed: HTTP {e.code}: {e.read().decode()[:300]}")
    if data.get("error"):
        raise RuntimeError(f"mcp {name} error: {data['error']}")
    result = data.get("result", {})
    if "content" in result:
        for c in result["content"]:
            text = c.get("text")
            if text:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    pass
    return result


class TestReceiptReadPathsTest(unittest.TestCase):
    """A conduit-only receipt in execution.receipts is visible on every read path."""

    # Unique-per-run identities so runs never collide with real data.
    PLAN_ID = f"wrconf010-{uuid.uuid4().hex[:8]}"
    REQUEST_UUID = str(uuid.uuid4())
    LINEAGE_ID = f"lineage-{uuid.uuid4().hex[:12]}"

    @classmethod
    def setUpClass(cls):
        cls._seed()

    @classmethod
    def tearDownClass(cls):
        cls._cleanup()

    # ── Seed / cleanup ────────────────────────────────────────────────

    @classmethod
    def _seed(cls) -> None:
        # Seed canonical blueprints_history (V171 surface) with the payload
        # fold — the implementation_plans view is read-only over the folded
        # payload and the V171 mirror trigger retired in V176, so writing
        # through the view would fail (feature-not-supported) and writing the
        # legacy table would silently vanish.
        _psql(
            f"""
            INSERT INTO nebula.blueprints_history
              (plan_number, title, payload, blueprint_status)
            VALUES
              ('{cls.PLAN_ID}', 'wr-conf-010 receipt read-path regression',
               jsonb_build_object(
                 'goal', 'conduit-lineage receipt must be visible to get_plan_receipts + query_conduit_state',
                 'content', ''),
               'pending');

            INSERT INTO execution.requests
              (id, business_key, source_plan_id, status, created_at, updated_at)
            VALUES
              ('{cls.REQUEST_UUID}', 'legacy-plan-{cls.PLAN_ID}', '{cls.PLAN_ID}',
               'DRAFT', NOW(), NOW());

            INSERT INTO execution.receipts
              (attempt_id, request_id, type, agent_role, summary, metadata,
               lineage_source, lineage_original_id, issued_at)
            VALUES
              (NULL, '{cls.REQUEST_UUID}', 'PLAN_CREATE', 'planner',
               'test_read_path_execution_only',
               '{{"session_id": "", "ticket_id": null, "tokens_used": 0}}'::jsonb,
               'conduit', '{cls.LINEAGE_ID}', NOW());
            """
        )

    @classmethod
    def _cleanup(cls) -> None:
        _psql(
            f"""
            ALTER TABLE execution.receipts DISABLE TRIGGER trg_receipts_immutable;
            DELETE FROM execution.receipts
            WHERE lineage_source = 'conduit' AND lineage_original_id = '{cls.LINEAGE_ID}';
            ALTER TABLE execution.receipts ENABLE TRIGGER trg_receipts_immutable;

            DELETE FROM peb.governance_events
            WHERE plan_id = '{cls.PLAN_ID}'
               OR receipt_id IN ('{cls.LINEAGE_ID}');
            DELETE FROM vision.tickets WHERE plan_id = '{cls.PLAN_ID}';
            DELETE FROM execution.requests WHERE id = '{cls.REQUEST_UUID}';
            DELETE FROM nebula.blueprints_history WHERE plan_number = '{cls.PLAN_ID}';
            """
        )

    # ── Tests ─────────────────────────────────────────────────────────

    def test_unified_view_surfaces_receipt_but_vision_does_not(self):
        unified = _psql(
            "SELECT count(*) FROM nebula.receipts_unified WHERE plan_id = "
            f"'{self.PLAN_ID}';", one=True)
        vision = _psql(
            "SELECT count(*) FROM vision.receipts WHERE plan_id = "
            f"'{self.PLAN_ID}';", one=True)
        self.assertEqual(int(unified), 1,
                         "receipts_unified must surface the execution-only receipt")
        self.assertEqual(int(vision), 0,
                         "test plan must have NO vision.receipts row (the orphan condition)")

        row = _psql(
            "SELECT id, type, recorded_on_dt IS NOT NULL, recorded_until_dt IS NULL "
            f"FROM nebula.receipts_unified WHERE plan_id = '{self.PLAN_ID}' LIMIT 1;")[0]
        receipt_id, typ, has_dt, until_null = row
        self.assertEqual(receipt_id, self.LINEAGE_ID,
                         "unified id must be lineage_original_id from execution.receipts")
        self.assertEqual(typ, "PLAN_CREATE")
        self.assertEqual(has_dt, "t", "execution branch must project recorded_on_dt (issued_at)")
        self.assertEqual(until_null, "t")

    def test_plan_status_derives_from_unified_surface(self):
        row = _psql(
            "SELECT derived_status, deleted FROM nebula.plan_status WHERE id = "
            f"'{self.PLAN_ID}';")
        self.assertEqual(len(row), 1, "plan_status must include the test plan")
        derived, deleted = row[0]
        self.assertEqual(deleted, "0")
        self.assertEqual(derived, "PLAN_CREATE",
                         "nebula.plan_status must derive PLAN_CREATE from execution.receipts")

    def test_query_conduit_state_buckets_plan_as_pending(self):
        state = _mcp_call("query_conduit_state", {})
        pending = state.get("plans", {}).get("pending", [])
        cards = [p for p in pending
                 if p.get("planNumber") == self.PLAN_ID or p.get("plan_id") == self.PLAN_ID]
        self.assertEqual(len(cards), 1,
                         f"query_conduit_state must show {self.PLAN_ID} in pending; "
                         f"got {[p.get('planNumber') for p in pending]}")
        self.assertEqual(cards[0].get("derivedStatus"), "PLAN_CREATE")

    def test_get_plan_receipts_returns_execution_receipt(self):
        result = _mcp_call("get_plan_receipts", {"plan_id": self.PLAN_ID})
        self.assertEqual(result.get("count"), 1,
                         f"get_plan_receipts must return the conduit receipt; got {result.get('receipts')}")
        receipts = result.get("receipts", [])
        self.assertEqual(receipts[0]["id"], self.LINEAGE_ID)
        self.assertEqual(receipts[0]["type"], "PLAN_CREATE")
        self.assertEqual(receipts[0].get("agent_role"), "planner")


if __name__ == "__main__":
    unittest.main()