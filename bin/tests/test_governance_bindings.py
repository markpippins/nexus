"""Hermetic tests for the governance binding approval surface (bin/governance_bindings.py).

All DB access goes through a fake port — no asyncpg, no database. Dual-runnable:
pytest-compatible functions plus a `python3 test_governance_bindings.py` runner
(bin/tests/test_merge_pr.py convention).

Covers (intent 7923c595):
  - list rendering (default proposed scope, --json, --all)
  - approve/reject REQUIRE --actor; attribution (decided_by + note) reaches the port
  - unknown/ambiguous binding refs refuse cleanly (fail closed)
  - port ValueErrors (illegal transitions) become REFUSED exit 1
  - the proposer's bound_by is never overwritten by decisions
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import sys
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
TOOL_PATH = REPO / "bin" / "governance_bindings.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("governance_bindings", TOOL_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gb = _load_tool()


# ── fixtures ─────────────────────────────────────────────────────────────

def make_binding(
    bid: Optional[uuid.UUID] = None,
    status: str = "proposed",
    bound_by: Optional[str] = "engineer:e2e-exercise",
    decided_by: Optional[str] = None,
    decision_note: Optional[str] = None,
) -> Any:
    """A duck-typed TagBinding: only the fields the tool reads."""
    return type(
        "B", (), {
            "id": bid or uuid.uuid4(),
            "governed_tag_id": uuid.uuid4(),
            "governed_tag_name": "Production",
            "governed_normalized_name": "production",
            "member_kind": "value",
            "applies_to": "service",
            "source_identity": "agent_record:abc",
            "source_revision": "rev-1",
            "namespace": "agent-record",
            "tag_key": "environment",
            "normalized_value": "production",
            "expression_observation_id": "tagobs:1",
            "status": status,
            "bound_by": bound_by,
            "created_at": datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc),
            "expired_at": None,
            "decided_by": decided_by,
            "decided_at": None,
            "decision_note": decision_note,
        },
    )()


class FakePort:
    """Records calls; returns scripted bindings / raises scripted errors."""

    def __init__(self, bindings: Optional[List[Any]] = None, error: Optional[Exception] = None):
        self.bindings = bindings or []
        self.error = error
        self.calls: List[Dict[str, Any]] = []

    async def list_bindings(self, **kw):
        self.calls.append({"op": "list_bindings", **kw})
        if self.error:
            raise self.error
        return self.bindings

    async def update_binding_status(self, binding_id, status, decided_by=None, decision_note=None):
        self.calls.append({
            "op": "update_binding_status", "binding_id": binding_id,
            "status": status, "decided_by": decided_by, "decision_note": decision_note,
        })
        if self.error:
            raise self.error
        b = next((x for x in self.bindings if x.id == binding_id), None)
        if b is None:
            return None
        b.status = status
        b.decided_by = decided_by
        b.decision_note = decision_note
        if status in ("approved", "rejected"):
            b.decided_at = datetime.now(timezone.utc)
        return b


def run_tool(argv, port):
    """Run the CLI with a fake port, capturing stdout/stderr.

    The coroutine is awaited INSIDE the capture context so all tool
    output is captured, not leaked to the test runner's terminal.
    """
    async def factory(dsn):
        return port

    out, err = io.StringIO(), io.StringIO()

    async def _invoke():
        return await gb.run(
            ["--dsn", "postgresql://x", *argv],
            port_factory=factory,
            driver_check=lambda: None,
        )

    with redirect_stdout(out), redirect_stderr(err):
        rc = asyncio.run(_invoke())
    return rc, out.getvalue(), err.getvalue()


# ── list ─────────────────────────────────────────────────────────────────

def test_list_defaults_to_proposed_and_renders_rows():
    b = make_binding()
    port = FakePort([b])
    rc, out, err = run_tool(["list"], port)
    assert rc == 0 and err == ""
    assert port.calls[0]["status"] == "proposed"
    # Long tag cells truncate with an ellipsis by design; assert on the
    # stable identity fields instead of the full value.
    assert "proposed" in out and str(b.id)[:8] in out
    assert "agent-record" in out and "engineer:e2e-exer" in out


def test_list_json_is_machine_readable():
    port = FakePort([make_binding(status="approved", decided_by="governance:architect",
                                  decision_note="fits the axis")])
    rc, out, _ = run_tool(["list", "--json"], port)
    assert rc == 0
    data = json.loads(out)
    assert data[0]["status"] == "approved"
    assert data[0]["decided_by"] == "governance:architect"


def test_list_all_clears_status_filter():
    port = FakePort([make_binding(status="expired")])
    rc, out, _ = run_tool(["list", "--all"], port)
    assert rc == 0
    assert port.calls[0]["status"] is None
    assert port.calls[0]["active_only"] is False


# ── decide: attribution ──────────────────────────────────────────────────

def test_approve_requires_actor():
    port = FakePort([make_binding()])
    rc, out, err = run_tool(["approve", "deadbeef"], port)
    assert rc == 1
    assert "requires --actor" in err
    assert port.calls == []  # refused before any port interaction


def test_reject_requires_actor():
    port = FakePort([make_binding()])
    rc, _, err = run_tool(["reject", "deadbeef"], port)
    assert rc == 1 and "requires --actor" in err


def test_approve_passes_actor_and_note_to_port():
    bid = uuid.uuid4()
    port = FakePort([make_binding(bid=bid)])
    rc, out, err = run_tool(
        ["approve", str(bid)[:8], "--actor", "governance:architect", "--note", "matches fleet"], port)
    assert rc == 0, err
    call = port.calls[-1]
    assert call["op"] == "update_binding_status"
    assert call["status"] == "approved"
    assert call["decided_by"] == "governance:architect"
    assert call["decision_note"] == "matches fleet"
    assert "governance:architect" in out


def test_decision_preserves_proposer_identity():
    bid = uuid.uuid4()
    b = make_binding(bid=bid, bound_by="engineer:e2e-exercise")
    port = FakePort([b])
    rc, out, _ = run_tool(["approve", str(bid)[:8], "--actor", "governance:architect"], port)
    assert rc == 0
    assert b.bound_by == "engineer:e2e-exercise"  # never clobbered
    assert "proposer engineer:e2e-exercise preserved" in out


# ── decide: fail-closed paths ────────────────────────────────────────────

def test_unknown_binding_refuses_cleanly():
    port = FakePort([])  # resolves nothing
    rc, _, err = run_tool(["approve", "00000000", "--actor", "x"], port)
    assert rc == 1
    assert "no active binding matches" in err


def test_ambiguous_prefix_refuses():
    # Deterministic ids sharing the leading hex block — random uuid4s
    # essentially never share even a 4-char prefix.
    b1 = make_binding(bid=uuid.UUID("abcdef12-0000-0000-0000-000000000001"))
    b2 = make_binding(bid=uuid.UUID("abcdef12-0000-0000-0000-000000000002"))
    ref = "abcdef12"
    port = FakePort([b1, b2])
    rc, _, err = run_tool(["approve", ref, "--actor", "x"], port)
    assert rc == 1 and "ambiguous" in err


def test_port_value_error_becomes_refusal():
    bid = uuid.uuid4()
    port = FakePort([make_binding(bid=bid)],
                    error=ValueError("illegal binding transition: 'approved' -> 'approved'"))
    rc, _, err = run_tool(["approve", str(bid)[:8], "--actor", "x"], port)
    assert rc == 1 and "REFUSED" in err


def test_infrastructure_error_fails_closed_without_traceback():
    bid = uuid.uuid4()
    port = FakePort([make_binding(bid=bid)], error=RuntimeError("connection refused"))
    rc, _, err = run_tool(["approve", str(bid)[:8], "--actor", "x"], port)
    assert rc == 2 and "fail closed" in err and "Traceback" not in err


# ── driver guard ─────────────────────────────────────────────────────────

def test_missing_driver_message_is_actionable():
    # Simulate the driverless environment through the injected check; the
    # port must never be built and the message must name the fix.
    out, err2 = io.StringIO(), io.StringIO()
    async def factory(dsn):
        raise AssertionError("port must not be built without a driver")

    async def _invoke():
        return await gb.run(
            ["--dsn", "postgresql://x", "list"],
            port_factory=factory,
            driver_check=lambda: "asyncpg is not installed; Fix: pip install -r python/aspects/requirements.txt",
        )

    with redirect_stdout(out), redirect_stderr(err2):
        rc = asyncio.run(_invoke())
    assert rc == 2
    assert "pip install -r python/aspects/requirements.txt" in err2.getvalue()


def test_check_driver_ok_when_present():
    # This environment may or may not have asyncpg; only assert message shape.
    msg = gb.check_driver()
    assert msg is None or "requirements.txt" in msg


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok {name}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  ERROR {name}: {exc!r}")
    print(f"{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
