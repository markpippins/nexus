"""Receipts DELETE hardening (architect ruling fcec95a2 #2).

Locks the constraint that DELETE /api/receipts/{plan_id} is NOT a general-
purpose receipt deleter. The sole legitimate caller is conduit-mcp's
unblock_plan tool, which purges the unblock receipt family. Any type outside
{BLOCK, PLAN_BLOCK, CANCELLED, ABANDONED} must be rejected.

Source-contract style (matches test_c1/test_c2 in this suite) — no DB
required; asserts bind the Python route to the ruling.

Usage:
  cd /home/codex/dev/nexus/python/conduit
  python -m pytest tests/test_receipts_delete_hardening.py -q
"""
import os

NEXUS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _route_src():
    with open(os.path.join(NEXUS_ROOT, "python/conduit/app/api/routes_receipts.py")) as f:
        return f.read()


# The unblock family (sole legitimate caller: conduit-mcp unblock_plan).
UNBLOCK_TYPES = {"BLOCK", "PLAN_BLOCK", "CANCELLED", "ABANDONED"}


class TestReceiptsDeleteAllowlist:
    def test_allowlist_constant_defines_unblock_family(self):
        src = _route_src()
        assert "UNBLOCK_RECEIPT_TYPES" in src
        for t in UNBLOCK_TYPES:
            assert f'"{t}"' in src, f"unblock type {t} missing from allowlist"

    def test_route_rejects_types_outside_family(self):
        src = _route_src()
        # The route must have a guard that raises on disallowed types
        assert "disallowed" in src
        assert "UNBLOCK_RECEIPT_TYPES" in src
        assert "status_code=400" in src

    def test_allowlist_is_the_only_valid_type_set(self):
        # No arbitrary type list may bypass the allowlist in the DELETE handler.
        src = _route_src()
        delete_block = src[src.index('@router.delete'):]
        # The DELETE handler must reference the allowlist, not accept raw types.
        assert "UNBLOCK_RECEIPT_TYPES" in delete_block
        # It must reject anything outside, never delete with an unchecked list.
        assert "not in UNBLOCK_RECEIPT_TYPES" in delete_block