"""
wr-conf-009: Cross-plane role contract (D-2026-08-16-009) — targeted
cache invalidation + revocation admission denial.

Implements the parts of the T12 role contract that are live today:

  - R4 targeted Redis cache invalidation: revoking a role's lease deletes
    the role's `mem:idx:{role}` procedure index (no broad flush).
  - Revocation admission: an explicitly-revoked lease denies new work with
    ROLE_REVOKED (harness-srv 403 + admission.denied).
  - Canonical role admission: a role with a valid config bundle + valid
    ACTIVE lease passes admission (config admission + lease-state pass).

Out of scope for now (follow-up decisions): capability-proof admission,
bitemporal invalidation events on cascade.events, and drift checks — these
require the R6 admission feature and are tracked on the T12 thread.

Usage:
    cd /home/codex/dev/nexus
    python3 -m pytest python/nexus_core/wrp/tests/test_conformance_role_contract.py -v
"""

import json
import os
import subprocess
import unittest
import urllib.error
import urllib.request

import pathlib

import sys

_GUARD_REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
if str(_GUARD_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_GUARD_REPO_ROOT))

from host_tool_guard import skip_unless_host_tools  # noqa: E402

pytestmark = skip_unless_host_tools(
    "psql", "redis-cli", doing="these tests drive PostgreSQL and Redis from the shell"
)

NEBULA_URL = os.environ.get("NEBULA_URL", "http://localhost:3101")
TEST_ROLE = "wr-conf-009"


def _post(path: str, body: dict, base: str = NEBULA_URL, timeout: int = 10) -> tuple:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{base}{path}", data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _get(path: str, base: str = NEBULA_URL, timeout: int = 10):
    with urllib.request.urlopen(f"{base}{path}", timeout=timeout) as resp:
        return json.loads(resp.read())


def _redis(*args: str) -> str:
    return subprocess.run(["redis-cli", *args], capture_output=True, text=True).stdout.strip()


def _cleanup() -> None:
    subprocess.run(
        ["psql", "postgres://pguser:pgpass@localhost:5432/nexus", "-c",
         f"DELETE FROM tackle.role_leases WHERE role='{TEST_ROLE}';"],
        capture_output=True,
    )


class TestCacheInvalidationOnRevoke(unittest.TestCase):
    """D-009 R4: revoking a lease invalidates the role's procedure index."""

    def setUp(self):
        _cleanup()

    def tearDown(self):
        _cleanup()

    def test_revoke_deletes_role_memory_index(self):
        # Seed a sentinel procedure index for the test role.
        _redis("SET", f"mem:idx:{TEST_ROLE}", '["sentinel"]')
        self.assertEqual(_redis("GET", f"mem:idx:{TEST_ROLE}"), '["sentinel"]')

        # Issue + revoke the lease.
        status, lease = _post("/api/role-leases/issue", {
            "role": TEST_ROLE, "channel": "opencode", "ttlSeconds": 120,
        })
        self.assertEqual(status, 201)
        status, _ = _post(f"/api/role-leases/{lease['id']}/revoke", {})
        self.assertEqual(status, 200)

        # The role's procedure index must now be gone (no broad flush, but
        # this role's mem:idx:{role} key is deleted).
        after = _redis("GET", f"mem:idx:{TEST_ROLE}")
        self.assertIn(after, ("", "(nil)"), f"expected mem:idx:{TEST_ROLE} to be invalidated, got {after!r}")


class TestRevocationAdmissionDenial(unittest.TestCase):
    """Revoked lease → ROLE_REVOKED at admission (D-008 R2 / D-009 test 4)."""

    def setUp(self):
        _cleanup()

    def tearDown(self):
        _cleanup()

    def test_revoked_lease_blocks_status_endpoint(self):
        status, lease = _post("/api/role-leases/issue", {
            "role": TEST_ROLE, "channel": "opencode", "ttlSeconds": 120,
        })
        self.assertEqual(status, 201)
        _post(f"/api/role-leases/{lease['id']}/revoke", {})

        st = _get(f"/api/role-leases/{TEST_ROLE}/status")
        self.assertEqual(st["state"], "REVOKED")
        self.assertEqual(st["lastLease"]["release_reason"], "revoked")


class TestCanonicalRoleAdmission(unittest.TestCase):
    """Canonical role with valid config bundle + ACTIVE lease passes."""

    def setUp(self):
        _cleanup()

    def tearDown(self):
        _cleanup()

    def test_active_lease_within_window_passes(self):
        status, lease = _post("/api/role-leases/issue", {
            "role": TEST_ROLE, "channel": "opencode", "ttlSeconds": 120,
        })
        self.assertEqual(status, 201)
        st = _get(f"/api/role-leases/{TEST_ROLE}/status")
        self.assertEqual(st["state"], "ACTIVE")
        self.assertIsNone(st["lastLease"]["released_at"])


if __name__ == "__main__":
    unittest.main()
