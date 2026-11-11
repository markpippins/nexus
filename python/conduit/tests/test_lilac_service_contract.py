"""Contract guards for the Lilac service boundary (plan 8261669).

Hermetic by design: no database, no network, no running service. Every claim
below is asserted against source text, the OpenAPI document, or a client driven
through an injected opener — so the guards cannot pass by borrowing a
developer's environment.

What is pinned here, and why it is a guard rather than a test:

  AC1  the documented surface and the client agree, in both directions.
  AC2  an idempotency key is DERIVED, so a retry dedupes instead of duplicating.
  AC3  the envelope is complete and route-bound before any request is built.
  AC5  the proxy holds no SQL and delegates every mutation to LilacClient.
  AC6  the surface is staged-inert: nothing is mounted or advertised by default.
  AC7  no admission or workflow-definition authority leaks into the surface.

Run:
    python3 -m pytest python/conduit/tests/test_lilac_service_contract.py -q
"""
from __future__ import annotations

import ast
import io
import json
import os
import re
import sys
import unittest
from typing import Any, Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import lilac_client  # noqa: E402
import lilac_mcp_adapter  # noqa: E402
from lilac_client import (  # noqa: E402
    CONTRACT_VERSION,
    HTTP_PATHS,
    ROUTES,
    LilacClient,
    LilacServiceError,
    derive_idempotency_key,
)

CONDUIT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPO_ROOT = os.path.abspath(os.path.join(CONDUIT_ROOT, "..", ".."))
ROUTES_LILAC = os.path.join(CONDUIT_ROOT, "app", "api", "routes_lilac.py")
LILAC_OPENAPI = os.path.join(REPO_ROOT, "typescript", "lilac-srv", "openapi.yaml")
CONDUIT_MAIN = os.path.join(CONDUIT_ROOT, "app", "main.py")


def read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


class _FakeResponse:
    def __init__(self, status: int, payload: Dict[str, Any]) -> None:
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class _Recorder:
    """Records requests and answers with a canned (status, payload)."""

    def __init__(self, status: int = 201, payload: Dict[str, Any] | None = None) -> None:
        self.status = status
        self.payload = payload or {"outcome": "accepted", "target_id": "t-1", "surface": "receipt"}
        self.requests: List[Any] = []

    def __call__(self, req: Any, timeout: float = 10.0) -> _FakeResponse:
        self.requests.append(req)
        return _FakeResponse(self.status, self.payload)

    def body(self, index: int = 0) -> Dict[str, Any]:
        return json.loads(self.requests[index].data.decode("utf-8"))


class OpenApiParityTest(unittest.TestCase):
    """AC1 — the client and the documented surface cannot drift apart."""

    def _documented(self) -> Dict[str, str]:
        paths: Dict[str, str] = {}
        in_paths = False
        current = None
        for line in read(LILAC_OPENAPI).splitlines():
            if line.startswith("paths:"):
                in_paths = True
                continue
            if in_paths and line and not line.startswith(" "):
                break
            match = re.match(r"^  (/[A-Za-z0-9/{}_-]+):\s*$", line)
            if match:
                current = match.group(1)
                continue
            method_match = re.match(r"^    (get|post):\s*$", line)
            if match or method_match:
                if method_match and current:
                    paths[f"{method_match.group(1).upper()} {current}"] = current
        return paths

    def test_every_client_path_is_documented(self) -> None:
        documented_paths = {p.split(" ", 1)[1] for p in self._documented()}
        missing = sorted(set(HTTP_PATHS.values()) - documented_paths)
        self.assertEqual(missing, [], f"client calls undocumented paths: {missing}")

    def test_every_mutation_route_in_the_client_is_documented(self) -> None:
        documented = self._documented()
        for surface in ("receipt", "ticket", "ticket_transition", "fanout_transition"):
            self.assertIn(
                ROUTES[surface],
                documented,
                f"mutation route for {surface} is not in openapi.yaml",
            )

    def test_documented_mutations_all_appear_in_the_client(self) -> None:
        # The reverse direction: a documented route nobody can call is a lie.
        documented_mutations = {
            route for route in self._documented() if route.startswith("POST ")
        }
        client_mutations = set(lilac_client.mutation_routes().values())
        self.assertEqual(documented_mutations, client_mutations)


class IdempotencyKeyTest(unittest.TestCase):
    """AC2 — retries must dedupe, not duplicate."""

    def test_key_is_deterministic_for_the_same_identity(self) -> None:
        identity = {"workflow_ref": "wr-8261669", "role": "engineer", "position": 1}
        first = derive_idempotency_key("ticket", "conduit", identity)
        second = derive_idempotency_key("ticket", "conduit", dict(reversed(list(identity.items()))))
        self.assertEqual(first, second, "key order must not change the key")

    def test_key_changes_with_the_identity_or_the_surface(self) -> None:
        base = {"workflow_ref": "wr-8261669", "role": "engineer", "position": 1}
        self.assertNotEqual(
            derive_idempotency_key("ticket", "conduit", base),
            derive_idempotency_key("ticket", "conduit", {**base, "position": 2}),
        )
        self.assertNotEqual(
            derive_idempotency_key("ticket", "conduit", base),
            derive_idempotency_key("receipt", "conduit", base),
        )

    def test_a_retry_presents_the_same_key(self) -> None:
        recorder = _Recorder()
        client = LilacClient("http://lilac.test", opener=recorder)
        kwargs = dict(
            producer_id="conduit",
            kind="implementation",
            source_system="conduit",
            source_receipt_id="src-1",
            payload={"plan": "8261669"},
        )
        client.issue_receipt(**kwargs)
        client.issue_receipt(**kwargs)
        first = recorder.body(0)["provenance"]["idempotency_key"]
        second = recorder.body(1)["provenance"]["idempotency_key"]
        self.assertEqual(first, second)


class EnvelopeTest(unittest.TestCase):
    """AC3 — the envelope is complete and route-bound before a request exists."""

    def setUp(self) -> None:
        self.recorder = _Recorder()
        self.client = LilacClient("http://lilac.test", opener=self.recorder)

    def test_envelope_has_exactly_the_five_required_fields(self) -> None:
        envelope = self.client.envelope("POST /v1/receipts", "idem-12345678")
        self.assertEqual(
            sorted(envelope),
            [
                "contract_version",
                "idempotency_key",
                "runtime_route",
                "source_identity",
                "source_implementation",
            ],
        )
        for key, value in envelope.items():
            self.assertNotEqual(value, "", f"{key} must not be empty")
        self.assertEqual(envelope["contract_version"], CONTRACT_VERSION)

    def test_envelope_refuses_an_empty_key(self) -> None:
        with self.assertRaises(ValueError):
            self.client.envelope("POST /v1/receipts", "")

    def test_each_mutation_declares_the_route_it_arrived_on(self) -> None:
        self.client.issue_receipt(
            producer_id="conduit",
            kind="implementation",
            source_system="conduit",
            source_receipt_id="s1",
            payload={},
        )
        self.assertEqual(
            self.recorder.body(0)["provenance"]["runtime_route"], ROUTES["receipt"]
        )

        self.client.issue_ticket(workflow_ref="wr-1", role="engineer", position=1)
        self.assertEqual(
            self.recorder.body(1)["provenance"]["runtime_route"], ROUTES["ticket"]
        )

        self.client.record_transition(
            ticket_id="t1",
            input_receipt_id="r1",
            from_status="pending",
            to_status="claimed",
        )
        self.assertEqual(
            self.recorder.body(2)["provenance"]["runtime_route"],
            ROUTES["ticket_transition"],
        )

        self.client.record_fanout(input_receipt_id="r1", kind="implementation", produced=[])
        self.assertEqual(
            self.recorder.body(3)["provenance"]["runtime_route"],
            ROUTES["fanout_transition"],
        )

    def test_a_refusal_is_typed_and_keeps_both_fingerprints(self) -> None:
        conflict = _Recorder(
            409,
            {
                "outcome": "refused",
                "code": "CONFLICT",
                "message": "conflict on receipt",
                "detail": {
                    "existing_fingerprint": "aaaa",
                    "incoming_fingerprint": "bbbb",
                },
            },
        )
        client = LilacClient("http://lilac.test", opener=conflict)
        with self.assertRaises(LilacServiceError) as ctx:
            client.issue_receipt(
                producer_id="conduit",
                kind="implementation",
                source_system="conduit",
                source_receipt_id="s1",
                payload={},
            )
        self.assertEqual(ctx.exception.code, "CONFLICT")
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.fingerprints(), ("aaaa", "bbbb"))

    def test_a_success_status_with_an_unknown_outcome_fails_closed(self) -> None:
        weird = _Recorder(200, {"outcome": "maybe", "target_id": "x"})
        client = LilacClient("http://lilac.test", opener=weird)
        with self.assertRaises(LilacServiceError) as ctx:
            client.issue_ticket(workflow_ref="w", role="engineer", position=1)
        self.assertEqual(ctx.exception.code, "PROTOCOL")


class ProxyHasNoSQLTest(unittest.TestCase):
    """AC5 — the proxy translates; it does not implement."""

    SQL_TOKENS = re.compile(
        r"\b(?:SELECT\s+[^\n]*\bFROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM"
        r"|CREATE\s+TABLE|ALTER\s+TABLE|DROP\s+TABLE)\b",
        re.IGNORECASE,
    )

    def test_routes_module_contains_no_sql(self) -> None:
        source = read(ROUTES_LILAC)
        hits = self.SQL_TOKENS.findall(source)
        self.assertEqual(hits, [], f"routes_lilac.py contains SQL: {hits}")

    def test_routes_module_imports_no_database_access(self) -> None:
        source = read(ROUTES_LILAC)
        for forbidden in (
            "db_adapter",
            "psycopg",
            "LilacAdapter",
            "import lilac\n",
            "from lilac import",
        ):
            self.assertNotIn(forbidden, source, f"routes_lilac.py reaches the store via {forbidden}")

    def test_every_post_handler_delegates_to_the_client(self) -> None:
        tree = ast.parse(read(ROUTES_LILAC))
        checked = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            is_post = False
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "post"
                ):
                    is_post = True
            if not is_post:
                continue
            checked += 1
            delegated = [
                call.func.attr
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "client"
            ]
            self.assertTrue(delegated, f"{node.name} is a POST route that calls no client method")
        self.assertGreaterEqual(checked, 4, "expected the four mutation routes to be inspected")

    def test_the_fanout_route_records_a_ledger_row_it_does_not_mint_tickets(self) -> None:
        """Fan-out is a ledger write. A proxy that spawns tickets on the way
        through would be a second fan-out engine, and the receipt-to-ticket
        spawn would exist in two places with two chances to disagree.

        Scoped to the fanout handler on purpose: the ticket route calling
        issue_ticket is its job, and a guard that forbade it would be wrong
        (this assertion was written too broadly first and caught itself).
        """
        tree = ast.parse(read(ROUTES_LILAC))
        inspected = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            paths = [
                arg.value
                for decorator in node.decorator_list
                if isinstance(decorator, ast.Call)
                for arg in decorator.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            ]
            if not any("fanouts" in path for path in paths):
                continue
            inspected += 1
            called = {
                call.func.attr
                for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "client"
            }
            self.assertNotIn(
                "issue_ticket",
                called,
                f"{node.name} mints tickets; fan-out must be recorded, not performed",
            )
            self.assertIn("record_fanout", called)
        self.assertEqual(inspected, 1, "expected exactly one fanout route")


class StagedInertTest(unittest.TestCase):
    """AC6 — with no configuration, nothing is mounted and nothing is advertised."""

    def test_disabled_without_a_base_url(self) -> None:
        from app.api.routes_lilac import lilac_enabled_from_env

        self.assertFalse(lilac_enabled_from_env({}))
        self.assertFalse(lilac_enabled_from_env({"CONDUIT_LILAC_URL": "  "}))
        self.assertTrue(lilac_enabled_from_env({"CONDUIT_LILAC_URL": "http://localhost:3172"}))

    def test_client_is_none_without_configuration(self) -> None:
        self.assertIsNone(LilacClient.from_env({}))
        self.assertIsNotNone(
            LilacClient.from_env({"CONDUIT_LILAC_URL": "http://localhost:3172"})
        )

    def test_no_tools_are_advertised_without_a_client(self) -> None:
        self.assertEqual(lilac_mcp_adapter.tools_for_client(None), [])

    def test_main_mounts_the_surface_only_behind_the_flag(self) -> None:
        source = read(CONDUIT_MAIN)
        self.assertIn("lilac_enabled_from_env()", source)
        # The include must be inside the conditional, never at module level.
        pattern = re.compile(
            r"if lilac_enabled_from_env\(\):(?P<body>(?:\n(?:\s{4,}.*)?)+)"
        )
        match = pattern.search(source)
        self.assertIsNotNone(match, "lilac router include is not guarded by the staging flag")
        self.assertIn("include_router(lilac_router", match.group("body"))


class AuthorityBoundaryTest(unittest.TestCase):
    """AC7 — no admission or workflow-definition authority on this surface."""

    def test_no_admission_or_workflow_definition_route(self) -> None:
        source = read(ROUTES_LILAC)
        lowered = source.lower()
        self.assertNotIn("@router.post(\"/admission", lowered)
        self.assertNotIn("workflow-definition", lowered)
        self.assertNotIn("workflow_definition", lowered)

    def test_client_exposes_no_admission_or_workflow_definition(self) -> None:
        for name in dir(LilacClient):
            if name.startswith("_"):
                continue
            self.assertNotIn("admission", name.lower())
            self.assertNotIn("workflow_definition", name.lower())
        for path in HTTP_PATHS.values():
            self.assertNotIn("admission", path.lower())


class McpAdapterTest(unittest.TestCase):
    """The adapter delegates and refuses unknowns; it owns no write path."""

    class _FakeClient:
        def __init__(self) -> None:
            self.calls: List[tuple] = []

        def issue_receipt(self, **kwargs: Any) -> str:
            self.calls.append(("issue_receipt", kwargs))
            return "ok"

        def issue_ticket(self, **kwargs: Any) -> str:
            self.calls.append(("issue_ticket", kwargs))
            return "ok"

        def record_transition(self, **kwargs: Any) -> str:
            self.calls.append(("record_transition", kwargs))
            return "ok"

        def record_fanout(self, **kwargs: Any) -> str:
            self.calls.append(("record_fanout", kwargs))
            return "ok"

        def ticket_timeline(self, **kwargs: Any) -> str:
            self.calls.append(("ticket_timeline", kwargs))
            return "ok"

        def parity_diagnostics(self, **kwargs: Any) -> str:
            self.calls.append(("parity_diagnostics", kwargs))
            return "ok"

    def test_dispatch_delegates_to_the_client(self) -> None:
        client = self._FakeClient()
        lilac_mcp_adapter.dispatch(
            client,
            "lilac_issue_ticket",
            {"workflow_ref": "wr-1", "role": "engineer", "position": 2},
        )
        self.assertEqual(client.calls[0][0], "issue_ticket")
        self.assertEqual(client.calls[0][1]["position"], 2)

    def test_missing_arguments_are_refused_before_dispatch(self) -> None:
        client = self._FakeClient()
        with self.assertRaises(lilac_mcp_adapter.LilacToolError):
            lilac_mcp_adapter.dispatch(client, "lilac_issue_ticket", {"workflow_ref": "wr-1"})
        self.assertEqual(client.calls, [])

    def test_unknown_tool_is_refused(self) -> None:
        with self.assertRaises(lilac_mcp_adapter.LilacToolError):
            lilac_mcp_adapter.dispatch(self._FakeClient(), "lilac_drop_everything", {})

    def test_every_advertised_tool_has_a_client_method(self) -> None:
        client = self._FakeClient()
        tools = lilac_mcp_adapter.tools_for_client(client)
        self.assertTrue(tools)
        for tool in tools:
            spec = lilac_mcp_adapter.TOOL_SPECS[tool["name"]]
            self.assertTrue(callable(getattr(client, spec["method"], None)))


if __name__ == "__main__":
    unittest.main()
