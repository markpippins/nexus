"""
wr-conf-019: T12 role lifecycle — cascade.events emission (R3), runtime-vs-
governance-vs-execution drift check (R5), and bitemporal soft-delete (R7).

Implements the remaining T12 legs from D-2026-08-16-009:

  - R3 lifecycle events on `cascade.events` — role.granted / role.revoked /
    role.expired / capability.changed, emitted from the role CRUD routes
    (nebula.roles) and the role-lease routes (tackle.role_leases).
  - R5 drift check — GET /api/roles/drift compares the three planes
    (governance = nebula.roles / roles_history, runtime = tackle.roles,
    execution = tackle.role_leases ACTIVE) and surfaces findings without
    silently reconciling.
  - R7 bitemporal history — deleting a role soft-expires it (valid_until
    = now()) so the record survives in nebula.roles_history for lineage.

Every test uses a synthetic role/lease with a per-run UUID suffix and cleans
up in tearDown, so the suite is idempotent and non-destructive against the
live localhost topology.

Usage:
    cd /home/codex/dev/nexus
    python3 -m pytest python/nexus_core/wrp/tests/test_conformance_role_lifecycle.py -v
"""

import json
import os
import random
import string
import subprocess
import unittest
import urllib.error
import urllib.request
import shutil
import pytest

# Skip (naming what is missing) rather than crashing with FileNotFoundError on a
# host that lacks the external tool this suite drives.
_REQUIRED_TOOLS = ("psql",)
_MISSING_TOOLS = [tool for tool in _REQUIRED_TOOLS if shutil.which(tool) is None]
pytestmark = pytest.mark.skipif(
    bool(_MISSING_TOOLS),
    reason="missing host tools for this suite: "
    + ", ".join(_MISSING_TOOLS)
    + " (these tests query PostgreSQL through psql)",
)

NEBULA_URL = os.environ.get("NEBULA_URL", "http://localhost:3101")
PSQL = "postgres://pguser:pgpass@localhost:5432/nexus"
# role names must match ^[a-z_]+$ (no digits), so the per-run suffix is letters-only
_RUN = "".join(random.choices(string.ascii_lowercase, k=10))
TEST_ROLE = f"wr_conf_{_RUN}"
MISSING_ROLE = f"wr_conf_missing_{_RUN}"


def _request(method: str, path: str, body: dict | None = None, timeout: int = 10) -> tuple:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{NEBULA_URL}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def _post(path: str, body: dict, timeout: int = 10) -> tuple:
    return _request("POST", path, body, timeout)


def _patch(path: str, body: dict, timeout: int = 10) -> tuple:
    return _request("PATCH", path, body, timeout)


def _delete(path: str, timeout: int = 10) -> tuple:
    return _request("DELETE", path, None, timeout)


def _get(path: str, timeout: int = 10):
    with urllib.request.urlopen(f"{NEBULA_URL}{path}", timeout=timeout) as resp:
        return json.loads(resp.read() or b"null")


def _pg(sql: str) -> str:
    """Run a read/write SQL statement via psql, return stdout."""
    return subprocess.run(
        ["psql", PSQL, "-t", "-A", "-c", sql],
        capture_output=True, text=True,
    ).stdout


def _events(aggregate_id: str, event_type: str) -> list[str]:
    """Return matching cascade.events rows for an aggregate + event type."""
    out = _pg(
        "SELECT event_type || '|' || aggregate_type || '|' || aggregate_id || '|' || payload::text "
        f"FROM cascade.events WHERE aggregate_id = '{aggregate_id}' "
        f"AND event_type = '{event_type}' ORDER BY sequence_number DESC;"
    )
    return [line for line in out.splitlines() if line.strip()]


def _cleanup() -> None:
    _pg("DELETE FROM tackle.role_leases WHERE role LIKE 'wr_conf_%';")
    _pg("DELETE FROM nebula.roles_history WHERE name LIKE 'wr_conf_%';")


class TestRoleLifecycleEventsOnCascadeEvents(unittest.TestCase):
    """D-009 R3: role transitions are emitted onto cascade.events."""

    def setUp(self):
        _cleanup()

    def tearDown(self):
        _cleanup()

    def test_01_role_grant_capability_change_revoke_emit_events(self):
        # role.granted on role create
        status, role = _post("/api/roles", {"name": TEST_ROLE, "displayName": "WR Conf 019"})
        self.assertEqual(status, 201, f"create role: {role}")
        granted = _events(str(role["id"]), "role.granted")
        self.assertTrue(granted, "expected a role.granted event for the created role")
        self.assertIn("role|", granted[0])

        # capability.changed on PATCH
        status, patched = _patch(f"/api/roles/{role['id']}", {"canGreenlight": True})
        self.assertEqual(status, 200, f"patch role: {patched}")
        changed = _events(str(role["id"]), "capability.changed")
        self.assertTrue(changed, "expected a capability.changed event for the PATCH")

        # role.revoked on DELETE (soft-expire keeps history)
        status, deleted = _delete(f"/api/roles/{role['id']}")
        self.assertEqual(status, 200, f"delete role: {deleted}")
        revoked = _events(str(role["id"]), "role.revoked")
        self.assertTrue(revoked, "expected a role.revoked event for the delete")

    def test_02_lease_grant_revoke_emit_events(self):
        # role.granted (role_lease) on lease issue
        status, lease = _post("/api/role-leases/issue", {
            "role": TEST_ROLE, "channel": "opencode", "ttlSeconds": 120,
        })
        self.assertEqual(status, 201, f"issue lease: {lease}")
        granted = _events(str(lease["id"]), "role.granted")
        self.assertTrue(granted, "expected a role.granted event for the issued lease")
        self.assertIn("role_lease|", granted[0])

        # role.revoked (role_lease) on explicit revoke
        status, _ = _post(f"/api/role-leases/{lease['id']}/revoke", {})
        self.assertEqual(status, 200)
        revoked = _events(str(lease["id"]), "role.revoked")
        self.assertTrue(revoked, "expected a role.revoked event for the revoked lease")

    def test_03_lease_sweep_emits_role_expired(self):
        # Issue a lease then force it past its window, then sweep.
        status, lease = _post("/api/role-leases/issue", {
            "role": TEST_ROLE, "channel": "opencode", "ttlSeconds": 120,
        })
        self.assertEqual(status, 201, f"issue lease: {lease}")
        _pg(
            "UPDATE tackle.role_leases SET expires_at = NOW() - interval '1 second' "
            f"WHERE id = '{lease['id']}';"
        )
        status, swept = _post("/api/role-leases/sweep", {})
        self.assertEqual(status, 200, f"sweep: {swept}")
        expired = _events(str(lease["id"]), "role.expired")
        self.assertTrue(expired, "expected a role.expired event for the swept lease")


class TestRoleDriftCheck(unittest.TestCase):
    """D-009 R5: drift check surfaces findings, never silently reconciles."""

    def setUp(self):
        _cleanup()

    def tearDown(self):
        _cleanup()

    def test_04_drift_endpoint_shape(self):
        drift = _get("/api/roles/drift")
        self.assertIn("summary", drift)
        self.assertIn("findings", drift)
        summary = drift["summary"]
        for key in ("governanceRoles", "runtimePersonas", "activeLeases", "findings"):
            self.assertIn(key, summary)
        self.assertIsInstance(drift["findings"], list)
        for f in drift["findings"]:
            self.assertIn("severity", f)
            self.assertIn("type", f)
            self.assertIn("role", f)

    def test_05_execution_lease_unknown_role_flagged_high(self):
        # An ACTIVE lease for a role with no canonical key (neither governance
        # nor runtime persona) must surface as execution_missing_role (high).
        status, lease = _post("/api/role-leases/issue", {
            "role": MISSING_ROLE, "channel": "opencode", "ttlSeconds": 120,
        })
        self.assertEqual(status, 201, f"issue lease: {lease}")

        drift = _get("/api/roles/drift")
        types = {f["type"]: f for f in drift["findings"]}
        self.assertIn("execution_missing_role", types, f"expected execution_missing_role in {types}")
        finding = types["execution_missing_role"]
        self.assertEqual(finding["severity"], "high")
        self.assertEqual(finding["role"], MISSING_ROLE)


class TestRoleBitemporalSoftDelete(unittest.TestCase):
    """D-009 R7: deleting a role soft-expires it (history preserved)."""

    def setUp(self):
        _cleanup()

    def tearDown(self):
        _cleanup()

    def test_06_delete_soft_expires_role_history(self):
        status, role = _post("/api/roles", {"name": TEST_ROLE, "displayName": "WR Conf 019"})
        self.assertEqual(status, 201, f"create role: {role}")
        role_id = str(role["id"])

        # Role is current before delete.
        current = _pg(f"SELECT name FROM nebula.roles WHERE id = '{role_id}';").strip()
        self.assertEqual(current, TEST_ROLE)

        status, _ = _delete(f"/api/roles/{role_id}")
        self.assertEqual(status, 200)

        # Gone from the current view...
        current_after = _pg(f"SELECT name FROM nebula.roles WHERE id = '{role_id}';").strip()
        self.assertEqual(current_after, "")

        # ...but preserved in history with a past valid_until (soft-expire).
        history = _pg(
            f"SELECT valid_until < '9999-12-01 00:00:00+00' FROM nebula.roles_history "
            f"WHERE id = '{role_id}';"
        ).strip()
        self.assertEqual(history, "t", "expected the role to be soft-expired in roles_history")


if __name__ == "__main__":
    unittest.main()
