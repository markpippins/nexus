"""C-2: validateReceipt routing in revise_plan / unblock_plan.

Verifies that:
1. tools.ts calls validateReceipt before every insertReceipt call
2. receipts.ts ALLOWED map includes PLANNING from empty state (for revise_plan)
3. receipts.ts ALLOWED map includes PLAN_CREATE from PLAN_CREATE (for unblock_plan)
"""
import os
import re
import pytest

NEXUS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _read(relpath):
    with open(os.path.join(NEXUS_ROOT, relpath)) as f:
        return f.read()


def _handler_body(src, name):
    """Return the body of the `name: async (...) => {` handler in tools.ts.

    The pattern must anchor on the *handler declaration* (`\n    unblock_plan:
    async `), not on the bare tool name. A plain `unblock_plan:.*?` match starts
    at the first occurrence anywhere in the file — for unblock_plan that is a
    line inside a comment at tools.ts:1251, so the assertion was checking prose
    rather than code and reported a false failure ("does not call
    validateReceipt") against a handler that does.
    """
    m = re.search(
        r"\n    " + re.escape(name) + r": async .*?(?=\n    \w+: async)",
        src,
        re.DOTALL,
    )
    assert m, f"{name} handler not found in tools.ts"
    return m.group(0)


class TestToolsTsRouting:
    """Verify validateReceipt is called before every insertReceipt in tools.ts."""

    def test_validate_receipt_count_ge_insert_count(self):
        src = _read("typescript/conduit-mcp/src/tools.ts")
        insert_count = src.count("api.insertReceipt")
        validate_count = src.count("validateReceipt(")
        assert validate_count >= insert_count, (
            f"tools.ts has {insert_count} insertReceipt calls but only "
            f"{validate_count} validateReceipt calls — bypass detected"
        )

    def test_revise_plan_has_validation(self):
        src = _read("typescript/conduit-mcp/src/tools.ts")
        body = _handler_body(src, "revise_plan")
        assert "validateReceipt(" in body, (
            "revise_plan does not call validateReceipt before insertReceipt"
        )
        val_pos = body.index("validateReceipt(")
        ins_pos = body.index("api.insertReceipt")
        assert val_pos < ins_pos, (
            f"validateReceipt (pos {val_pos}) appears after insertReceipt (pos {ins_pos})"
        )

    def test_unblock_plan_has_validation(self):
        src = _read("typescript/conduit-mcp/src/tools.ts")
        body = _handler_body(src, "unblock_plan")
        assert "validateReceipt(" in body, (
            "unblock_plan does not call validateReceipt before insertReceipt"
        )
        val_pos = body.index("validateReceipt(")
        ins_pos = body.index("api.insertReceipt")
        assert val_pos < ins_pos, (
            f"validateReceipt (pos {val_pos}) appears after insertReceipt (pos {ins_pos})"
        )


class TestReceiptsAllowedMap:
    """Verify receipts.ts ALLOWED map includes new transitions for C-2."""

    def _parse_allowed(self):
        """Parse the ALLOWED map from receipts.ts into a Python dict."""
        src = _read("typescript/conduit-mcp/src/receipts.ts")
        # Extract the block between "const ALLOWED" and the closing "};"
        m = re.search(r'const ALLOWED.*?=\s*\{(.*?)\};', src, re.DOTALL)
        assert m, "ALLOWED map not found in receipts.ts"
        block = m.group(1)
        allowed = {}
        for line in block.split("\n"):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            # Match the key (may be "" or unquoted) and the values array
            km = re.match(r'["\']?([^"\':\s]+)["\']?\s*:\s*\[(.*?)\]', line)
            if not km:
                # Handle the "" (empty string) key: "": [...]
                km2 = re.match(r'["\']{2}\s*:\s*\[(.*?)\]', line)
                if km2:
                    vals_raw = km2.group(1)
                    vals = [v.strip().strip('"') for v in vals_raw.split(",") if v.strip().strip('"')]
                    allowed[""] = vals
                continue
            key = km.group(1)
            vals_raw = km.group(2)
            vals = [v.strip().strip('"') for v in vals_raw.split(",") if v.strip().strip('"')]
            allowed[key] = vals
        return allowed

    def test_planning_allowed_from_empty(self):
        """revise_plan issues PLANNING on a fresh plan (empty receipt chain)."""
        allowed = self._parse_allowed()
        assert "" in allowed, "Empty string key not in ALLOWED map"
        assert "PLANNING" in allowed[""], (
            f"PLANNING not allowed from empty state. Allowed: {allowed['']}"
        )

    def test_plan_create_allowed_from_plan_create(self):
        """unblock_plan issues PLAN_CREATE after deleting block receipts;
        if plan had PLAN_CREATE -> BLOCK, latest remaining is PLAN_CREATE."""
        allowed = self._parse_allowed()
        assert "PLAN_CREATE" in allowed, "PLAN_CREATE key not in ALLOWED map"
        assert "PLAN_CREATE" in allowed["PLAN_CREATE"], (
            f"PLAN_CREATE not allowed from PLAN_CREATE state. "
            f"Allowed: {allowed['PLAN_CREATE']}"
        )

    def test_existing_transitions_preserved(self):
        """Ensure new additions didn't break existing transitions."""
        allowed = self._parse_allowed()
        # PLAN_CREATE should still allow IMPLEMENTATION, BLOCK, CRITIQUE, HOLD
        for t in ["IMPLEMENTATION", "BLOCK", "CRITIQUE", "HOLD"]:
            assert t in allowed.get("PLAN_CREATE", []), (
                f"Existing transition PLAN_CREATE -> {t} missing after C-2 fix"
            )
        # Empty state should still allow PLAN_CREATE and BLOCK
        for t in ["PLAN_CREATE", "BLOCK"]:
            assert t in allowed.get("", []), (
                f"Existing transition '' -> {t} missing after C-2 fix"
            )
        # PLANNING should still allow PLAN_CREATE, PLAN_BLOCK, HOLD
        for t in ["PLAN_CREATE", "PLAN_BLOCK", "HOLD"]:
            assert t in allowed.get("PLANNING", []), (
                f"Existing transition PLANNING -> {t} missing after C-2 fix"
            )
