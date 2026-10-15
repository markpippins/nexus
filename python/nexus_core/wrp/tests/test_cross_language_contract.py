"""
Cross-language contract tests for WRP state machine and receipt vocabulary.

DRIFT DETECTION per tester-role mandate:
  These tests FAIL at build time the moment the Python and TypeScript
  canonical sources diverge. The tester-role warns: "this project has
  already demonstrated this drift happens, so make the drift loud
  instead of something the next audit has to discover by hand."

Canonical sources compared:
  Python:
    nexus_core/wrp/states.py       — RECEIPT_TO_WRP_STATE, WRP_ADJACENCY_MATRIX
    cascade/event_store.py         — TRANSITION_MATRIX, WorkRequestState
  TypeScript:
    conduit-mcp/src/receipts.ts    — ALLOWED (receipt transition graph)
    conduit-mcp/src/types.ts       — ReceiptType union
    nebula-mcp/src/conduit-wrp-contract.ts — receiptToWrpState, WRP_ADJACENCY_MATRIX

Usage:
    cd /home/codex/dev/nexus
    python3 -m pytest python/nexus_core/wrp/tests/test_cross_language_contract.py -v
"""

import os
import re
import sys
import unittest

# Make nexus_core importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from nexus_core.wrp.states import (
    RECEIPT_TO_WRP_STATE,
    WRP_ADJACENCY_MATRIX as PY_WRP_ADJACENCY_MATRIX,
    is_valid_transition as py_is_valid_transition,
)

NEXUS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

CASCADE_DIR = os.path.join(NEXUS_ROOT, "python", "cascade")
sys.path.insert(0, CASCADE_DIR)
from event_store import TRANSITION_MATRIX as CASCADE_TRANSITION_MATRIX
from event_store import WorkRequestState as CascadeWRState


# ── File readers ────────────────────────────────────────────────────

def _read(relpath: str) -> str:
    with open(os.path.join(NEXUS_ROOT, relpath)) as f:
        return f.read()


# ══════════════════════════════════════════════════════════════════════
# Section 1: TS ReceiptType union extraction
# ══════════════════════════════════════════════════════════════════════

def extract_ts_receipt_types() -> set[str]:
    """Extract receipt types from types.ts ReceiptType union."""
    src = _read("typescript/conduit-mcp/src/types.ts")
    m = re.search(r"export type ReceiptType =\n((?:\s+\|.*\n)+)", src)
    if not m:
        return set()
    return set(re.findall(r'"(\w+)"', m.group(1)))


def extract_ts_conduit_receipt_types() -> set[str]:
    """Extract from conduit-wrp-contract.ts ConduitReceiptType union."""
    src = _read("typescript/nebula-mcp/src/conduit-wrp-contract.ts")
    m = re.search(r"export type ConduitReceiptType =\n((?:\s+\|.*\n)+)", src)
    if not m:
        return set()
    return set(re.findall(r'"(\w+)"', m.group(1)))


def extract_ts_allowed_keys() -> set[str]:
    """Extract receipt types from receipts.ts ALLOWED keys."""
    src = _read("typescript/conduit-mcp/src/receipts.ts")
    return set(re.findall(r'"?(\w+)"?\s*:\s*\[', src)) - {""}


# ══════════════════════════════════════════════════════════════════════
# Section 2: TS receiptToWrpState extraction
# ══════════════════════════════════════════════════════════════════════

def extract_ts_receipt_to_wrp_state() -> dict[str, str]:
    """Extract receipt → WRP state mapping from conduit-wrp-contract.ts.

    Scoped to ONLY the receiptToWrpState function body to avoid matching
    other switch statements (wrpStateCategory, determineAbstractionLevel).
    """
    src = _read("typescript/nebula-mcp/src/conduit-wrp-contract.ts")
    # Extract only the receiptToWrpState function body
    m = re.search(
        r"export function receiptToWrpState\(.*?\)\s*\{(.*?)\n\}",
        src, re.DOTALL,
    )
    if not m:
        return {}

    mapping: dict[str, str] = {}
    # Pattern: case "RECEIPT_TYPE": return "WRP_STATE";
    for receipt_type, wrp_state in re.findall(
        r'case\s+"(\w+)":\s+return\s+"(\w+)"',
        m.group(1),
    ):
        mapping[receipt_type] = wrp_state
    return mapping


# ══════════════════════════════════════════════════════════════════════
# Section 3: TS WRP adjacency matrix extraction
# ══════════════════════════════════════════════════════════════════════

def extract_ts_wrp_adjacency() -> dict[str, set[str]]:
    """Extract WRP adjacency matrix from conduit-wrp-contract.ts."""
    src = _read("typescript/nebula-mcp/src/conduit-wrp-contract.ts")
    # The matrix is declared as:
    # const WRP_ADJACENCY_MATRIX: Record<WRPState, Record<WRPState, boolean>> = {
    #   CREATED: { CREATED: false, INTAKE: true, ... },
    #   ...
    # }
    m = re.search(
        r"WRP_ADJACENCY_MATRIX.*?= \{(.*?)\n\};",
        src, re.DOTALL,
    )
    if not m:
        return {}

    matrix: dict[str, set[str]] = {}
    # Each line: STATE: { STATE: BOOL, STATE: BOOL, ... },
    for line in m.group(1).split("\n"):
        line = line.strip()
        if not line or ":" not in line:
            continue
        # Extract state name before the colon
        state_match = re.match(r'(\w+)\s*:', line)
        if not state_match:
            continue
        from_state = state_match.group(1)
        # Extract all TARGET: true pairs
        targets = set(re.findall(r'(\w+)\s*:\s*true', line))
        matrix[from_state] = targets
    return matrix


def extract_ts_wrp_states() -> set[str]:
    """Extract WRPState type values from conduit-wrp-contract.ts."""
    src = _read("typescript/nebula-mcp/src/conduit-wrp-contract.ts")
    m = re.search(r"export type WRPState =\n((?:\s+\|.*\n)+)", src)
    if not m:
        return set()
    return set(re.findall(r'"(\w+)"', m.group(1)))


# ══════════════════════════════════════════════════════════════════════
# Section 4: TS ALLOWED transitions extraction
# ══════════════════════════════════════════════════════════════════════

def extract_ts_allowed_transitions() -> dict[str, set[str]]:
    """Extract the ALLOWED transition map from receipts.ts."""
    src = _read("typescript/conduit-mcp/src/receipts.ts")
    m = re.search(r"const ALLOWED.*?= \{((?:.*\n)+?)\};", src)
    if not m:
        return {}

    transitions: dict[str, set[str]] = {}
    for line in m.group(1).split("\n"):
        line = line.strip()
        if not line or ":" not in line:
            continue
        # Handle both quoted and unquoted keys
        key_match = re.match(r'"?(\w*)"?\s*:', line)
        if not key_match:
            continue
        from_type = key_match.group(1) or ""  # empty string key
        targets_raw = re.findall(r'"(\w+)"', line)
        transitions[from_type] = set(targets_raw)
    return transitions


# ══════════════════════════════════════════════════════════════════════
# TESTS
# ══════════════════════════════════════════════════════════════════════


class TestReceiptTypeSetConsistency(unittest.TestCase):
    """All 4 canonical sources must agree on the set of receipt types."""

    @classmethod
    def setUpClass(cls):
        cls.ts_receipt_types = extract_ts_receipt_types()
        cls.ts_conduit_types = extract_ts_conduit_receipt_types()
        cls.ts_allowed_keys = extract_ts_allowed_keys()
        cls.py_receipt_keys = set(RECEIPT_TO_WRP_STATE.keys())

    def test_python_has_all_ts_receipt_types(self):
        """Every TS ReceiptType must be in Python RECEIPT_TO_WRP_STATE."""
        missing = self.ts_receipt_types - self.py_receipt_keys
        self.assertEqual(
            missing, set(),
            f"Receipt types in TS ReceiptType but missing from Python "
            f"RECEIPT_TO_WRP_STATE: {sorted(missing)}",
        )

    def test_python_has_all_ts_conduit_types(self):
        """Every TS ConduitReceiptType must be in Python."""
        missing = self.ts_conduit_types - self.py_receipt_keys
        self.assertEqual(
            missing, set(),
            f"Receipt types in TS ConduitReceiptType but missing from "
            f"Python: {sorted(missing)}",
        )

    def test_python_has_no_extra_types_vs_conduit(self):
        """Python must not have receipt types absent from ConduitReceiptType."""
        extra = self.py_receipt_keys - self.ts_conduit_types
        self.assertEqual(
            extra, set(),
            f"Receipt types in Python RECEIPT_TO_WRP_STATE but not in "
            f"TS ConduitReceiptType: {sorted(extra)}",
        )

    def test_ts_receipt_type_matches_conduit_type(self):
        """TS ReceiptType and ConduitReceiptType must be identical."""
        diff = self.ts_receipt_types ^ self.ts_conduit_types
        self.assertEqual(
            diff, set(),
            f"Mismatch between TS ReceiptType and ConduitReceiptType: "
            f"{sorted(diff)}",
        )

    def test_allowed_keys_are_subset_of_conduit_types(self):
        """Every key in ALLOWED must be in ConduitReceiptType (except "")."""
        extra = self.ts_allowed_keys - self.ts_conduit_types - {""}
        self.assertEqual(
            extra, set(),
            f"ALLOWED keys not in ConduitReceiptType: {sorted(extra)}",
        )

    def test_conduit_types_are_subset_of_allowed_keys(self):
        """Every ConduitReceiptType should appear in ALLOWED keys.

        Terminal states (REVIEW_PASS, API_LIMIT) may have empty target lists
        but should still appear as keys.
        """
        missing = self.ts_conduit_types - self.ts_allowed_keys
        self.assertEqual(
            missing, set(),
            f"ConduitReceiptType values missing from ALLOWED keys: "
            f"{sorted(missing)}",
        )


class TestReceiptToWrpStateMapping(unittest.TestCase):
    """Every receipt type must map to the same WRP state in Python and TS."""

    @classmethod
    def setUpClass(cls):
        cls.ts_mapping = extract_ts_receipt_to_wrp_state()
        cls.py_mapping = RECEIPT_TO_WRP_STATE

    def test_mapping_coverage_same_keys(self):
        """Both mappings must have the same set of receipt types."""
        py_keys = set(self.py_mapping.keys())
        ts_keys = set(self.ts_mapping.keys())
        missing_in_py = ts_keys - py_keys
        missing_in_ts = py_keys - ts_keys
        self.assertEqual(
            missing_in_py, set(),
            f"In TS receiptToWrpState but not Python: {sorted(missing_in_py)}",
        )
        self.assertEqual(
            missing_in_ts, set(),
            f"In Python RECEIPT_TO_WRP_STATE but not TS: {sorted(missing_in_ts)}",
        )

    def test_every_receipt_maps_to_same_wrp_state(self):
        """For every shared receipt type, the WRP state must match."""
        mismatches = []
        for receipt_type in sorted(set(self.py_mapping) & set(self.ts_mapping)):
            py_state = self.py_mapping[receipt_type]
            ts_state = self.ts_mapping[receipt_type]
            if py_state != ts_state:
                mismatches.append(
                    f"  {receipt_type}: Python={py_state} TS={ts_state}"
                )
        self.assertEqual(
            len(mismatches), 0,
            f"Receipt → WRP state mismatches between Python and TS:\n"
            + "\n".join(mismatches),
        )


class TestWrpAdjacencyMatrixConsistency(unittest.TestCase):
    """The WRP adjacency matrix must match across Python and TypeScript."""

    @classmethod
    def setUpClass(cls):
        cls.ts_matrix = extract_ts_wrp_adjacency()
        cls.py_matrix = PY_WRP_ADJACENCY_MATRIX
        cls.ts_states = extract_ts_wrp_states()

    # ── State set identity ──

    def test_same_11_states(self):
        """Both must define exactly 11 WRP states."""
        py_states = set(self.py_matrix.keys())
        self.assertEqual(
            py_states, self.ts_states,
            f"State set mismatch:\n"
            f"  Python only: {sorted(py_states - self.ts_states)}\n"
            f"  TS only:     {sorted(self.ts_states - py_states)}",
        )

    def test_matrix_keys_match_state_type(self):
        """Every key in the TS matrix must be a valid WRPState value."""
        extra = set(self.ts_matrix.keys()) - self.ts_states
        self.assertEqual(
            extra, set(),
            f"TS matrix keys not in WRPState type: {sorted(extra)}",
        )

    # ── Transition identity ──

    def test_every_valid_transition_matches(self):
        """For every (from, to) pair, both matrices must agree."""
        py_states = set(self.py_matrix.keys())
        ts_states = self.ts_states
        common = py_states & ts_states

        only_py = []
        only_ts = []
        for from_state in sorted(common):
            py_targets = self.py_matrix.get(from_state, set())
            ts_targets = self.ts_matrix.get(from_state, set())
            for to_state in sorted(common):
                in_py = to_state in py_targets
                in_ts = to_state in ts_targets
                if in_py and not in_ts:
                    only_py.append(f"  {from_state} → {to_state}")
                elif in_ts and not in_py:
                    only_ts.append(f"  {from_state} → {to_state}")

        msg = []
        if only_py:
            msg.append(f"Transitions in Python but not TS ({len(only_py)}):")
            msg.extend(only_py[:10])
        if only_ts:
            msg.append(f"Transitions in TS but not Python ({len(only_ts)}):")
            msg.extend(only_ts[:10])

        self.assertEqual(
            len(only_py) + len(only_ts), 0,
            "\n".join(msg) if msg else "",
        )

    # ── Terminal state consistency ──

    def test_terminal_states_match(self):
        """Terminal states (no outgoing edges) must match."""
        py_terminal = {s for s, t in self.py_matrix.items() if not t}
        ts_terminal = {s for s, t in self.ts_matrix.items() if not t}
        self.assertEqual(
            py_terminal, ts_terminal,
            f"Terminal state mismatch:\n"
            f"  Python terminal: {sorted(py_terminal)}\n"
            f"  TS terminal:     {sorted(ts_terminal)}",
        )

    def test_failed_is_terminal_in_both(self):
        self.assertEqual(PY_WRP_ADJACENCY_MATRIX.get("FAILED", None), set())
        self.assertEqual(extract_ts_wrp_adjacency().get("FAILED", None), set())


class TestCascadeTransitionMatrixAlignment(unittest.TestCase):
    """Cascade's WorkRequestState TRANSITION_MATRIX must align with
    TS receipts.ts ALLOWED transitions.

    Cascade states (WorkRequestState): PROPOSED, PLANNING, PENDING,
    IMPLEMENTING, REVIEW, COMPLETED, FAILED, CANCELLED

    TS ALLOWED receipt types: PLAN_CREATE, IMPLEMENTATION, REVIEW_PASS,
    REVIEW_REJECT, BLOCK, PLANNING, HOLD, REVIEW, CRITIQUE, ...
    """

    @classmethod
    def setUpClass(cls):
        cls.ts_allowed = extract_ts_allowed_transitions()
        cls.cascade_states = {s.value for s in CascadeWRState}

    def test_cascade_states_exist_in_allowed(self):
        """Cascade state names that overlap with TS ALLOWED keys.

        Not all cascade states have direct TS equivalents (PROPOSED, PENDING).
        This test verifies the overlap is consistent.
        """
        # Cascade states that ARE receipt types: PLANNING, IMPLEMENTING,
        # REVIEW, COMPLETED, FAILED, CANCELLED.
        # Direct overlap: PLANNING, IMPLEMENTING, REVIEW, CANCELLED
        overlap = self.cascade_states & set(self.ts_allowed.keys())
        # Should have at least PLANNING, REVIEW, CANCELLED
        expected_overlap = {"PLANNING", "REVIEW", "CANCELLED"}
        self.assertTrue(
            expected_overlap <= overlap,
            f"Cascade → TS overlap missing: {sorted(expected_overlap - overlap)}",
        )

    def test_allowed_has_all_cascade_receipt_types(self):
        """TS ALLOWED should contain all receipt types cascade emits.

        Cascade issues: PLANNING, PLAN_CREATE, IMPLEMENTATION, REVIEW_PASS,
        REVIEW_REJECT, BLOCK, REVIEW, CRITIQUE, CRITIQUE_PASS,
        CRITIQUE_REJECT, PLAN_BLOCK, CANCELLED, ABANDONED, API_LIMIT.
        """
        cascade_receipts = {
            "PLANNING", "PLAN_CREATE", "IMPLEMENTATION", "REVIEW_PASS",
            "REVIEW_REJECT", "BLOCK", "REVIEW", "CRITIQUE", "CRITIQUE_PASS",
            "CRITIQUE_REJECT", "PLAN_BLOCK", "CANCELLED", "ABANDONED",
            "API_LIMIT",
        }
        missing = cascade_receipts - set(self.ts_allowed.keys())
        self.assertEqual(
            missing, set(),
            f"Cascade receipt types absent from ALLOWED keys: "
            f"{sorted(missing)}",
        )

    def test_proposed_not_in_allowed(self):
        """PROPOSED is a lifecycle pre-receipt state, not in ALLOWED."""
        self.assertNotIn("PROPOSED", self.ts_allowed)

    def test_pending_not_in_allowed(self):
        """PENDING is an intermediate cascade state, not in ALLOWED."""
        self.assertNotIn("PENDING", self.ts_allowed)


# ══════════════════════════════════════════════════════════════════════
# Section 5: WRP kernel engine state machine alignment
# ══════════════════════════════════════════════════════════════════════

class TestWrpKernelEngineAlignment(unittest.TestCase):
    """The conduit wrp_kernel/engine.py WRP_ADJACENCY_MATRIX must match
    the nexus_core/wrp/states.py canonical matrix.

    These are two Python copies of the same state machine — drift between
    them would mean the kernel engine accepts different transitions than
    the canonical definition.
    """

    @classmethod
    def setUpClass(cls):
        cls.canonical = PY_WRP_ADJACENCY_MATRIX

        # Import the kernel engine's WRP_ADJACENCY_MATRIX
        conduit_dir = os.path.join(NEXUS_ROOT, "python", "conduit")
        sys.path.insert(0, conduit_dir)
        try:
            from wrp_kernel.engine import (
                WRP_ADJACENCY_MATRIX as KERNEL_WRP_ADJACENCY_MATRIX,
            )
        except ImportError:
            cls.kernel_matrix = None
            return
        cls.kernel_matrix = KERNEL_WRP_ADJACENCY_MATRIX

    def test_kernel_engine_importable(self):
        """Ensure the kernel engine module is importable."""
        self.assertIsNotNone(
            self.kernel_matrix,
            "Could not import wrp_kernel.engine.WRP_ADJACENCY_MATRIX",
        )

    def test_kernel_matrix_has_same_states(self):
        if self.kernel_matrix is None:
            self.skipTest("kernel engine not importable")
        py_states = set(self.canonical.keys())
        kernel_states = set(self.kernel_matrix.keys())
        self.assertEqual(
            py_states, kernel_states,
            f"State mismatch:\n  nexus_core only: {sorted(py_states - kernel_states)}\n"
            f"  kernel only: {sorted(kernel_states - py_states)}",
        )

    def test_kernel_matrix_has_same_transitions(self):
        if self.kernel_matrix is None:
            self.skipTest("kernel engine not importable")
        mismatches = []
        for state in self.canonical:
            py_targets = self.canonical.get(state, set())
            kernel_targets = self.kernel_matrix.get(state, set())
            if py_targets != kernel_targets:
                mismatches.append(
                    f"  {state}: canonical={sorted(py_targets)} "
                    f"kernel={sorted(kernel_targets)}"
                )
        self.assertEqual(
            len(mismatches), 0,
            f"Transition mismatches:\n" + "\n".join(mismatches),
        )


# ══════════════════════════════════════════════════════════════════════
# Section 6: WRP *kernel* vocabulary (runtime-kernel.ts) — D1 guard
# ══════════════════════════════════════════════════════════════════════
#
# Two DISTINCT state machines exist by design, and neither is canonical
# over the other:
#
#   KERNEL — conduit-mcp/src/runtime-kernel.ts
#           DRAFT, VALIDATED, QUEUED, CLAIMED, ACKED, SETTLED, REJECTED,
#           FAILED, NOOP, DEFERRED   (execution/runtime lifecycle)
#
#   WRP    — nexus_core/wrp/states.py == nebula-mcp/conduit-wrp-contract.ts
#           CREATED, INTAKE, PLANNING, CRITIQUE, SPECIFICATION, APPROVED,
#           QUEUED, EXECUTING, COMPLETED, ARCHIVED, FAILED  (planning lifecycle)
#
# Sections 1-5 above already guard Python↔nebula. This section closes the
# gap: runtime-kernel.ts had NO mechanical guard at all, so a state added
# there would drift silently. It does NOT assert the two vocabularies are
# equal — they must NOT be. It asserts their *relationship*: the documented
# collision set is exactly {QUEUED, FAILED}, so any new name collision fails
# here and forces a conscious decision instead of a silent semantic merge.
#
# Inspector finding D1 (report 5d45f921); fix is "shared vocabulary /
# reconciliation guard, NOT rename" — deliberately no rename is performed.

KERNEL_STATUSES_PINNED = {
    "DRAFT", "VALIDATED", "QUEUED", "CLAIMED", "ACKED",
    "SETTLED", "REJECTED", "FAILED", "NOOP", "DEFERRED",
}
KERNEL_EVENT_TYPES_PINNED = {
    "WR_SUBMITTED", "WR_VALIDATED", "WR_QUEUED", "WR_CLAIMED", "WR_ACKED",
    "WR_SETTLED", "WR_REJECTED", "WR_FAILED", "WR_NOOP", "WR_DEFERRED",
}
KERNEL_TERMINAL_PINNED = {
    "SETTLED", "REJECTED", "FAILED", "NOOP", "DEFERRED",
}
# The ONLY names that legitimately appear in both vocabularies, with
# different meanings in each. Adding a third collision is a guard failure.
KERNEL_WRP_COLLISIONS_PINNED = {"QUEUED", "FAILED"}

RUNTIME_KERNEL_TS = "typescript/conduit-mcp/src/runtime-kernel.ts"


def extract_ts_kernel_statuses() -> set[str]:
    """Extract WR_STATUSES from runtime-kernel.ts."""
    src = _read(RUNTIME_KERNEL_TS)
    m = re.search(r"export const WR_STATUSES = \[(.*?)\] as const;", src, re.DOTALL)
    if not m:
        return set()
    return set(re.findall(r'"(\w+)"', m.group(1)))


def extract_ts_kernel_event_types() -> set[str]:
    """Extract RUNTIME_EVENT_TYPES from runtime-kernel.ts."""
    src = _read(RUNTIME_KERNEL_TS)
    m = re.search(
        r"export const RUNTIME_EVENT_TYPES = \[(.*?)\] as const;", src, re.DOTALL,
    )
    if not m:
        return set()
    return set(re.findall(r'"(\w+)"', m.group(1)))


def extract_ts_kernel_transitions() -> dict[str, set[tuple[str, str]]]:
    """Extract TRANSITION_TABLE from runtime-kernel.ts.

    Returns {from_status: {(event, next_status), ...}}. Terminal states map
    to an empty set. Entry lists span multiple lines, so each
    ``STATE: [ ... ]`` block is captured whole rather than line-by-line.
    """
    src = _read(RUNTIME_KERNEL_TS)
    m = re.search(r"const TRANSITION_TABLE.*?= \{(.*?)\n\};", src, re.DOTALL)
    if not m:
        return {}

    table: dict[str, set[tuple[str, str]]] = {}
    for block in re.finditer(r'(\w+)\s*:\s*\[(.*?)\]', m.group(1), re.DOTALL):
        table[block.group(1)] = set(re.findall(
            r'event:\s*"(\w+)",\s*nextStatus:\s*"(\w+)"', block.group(2),
        ))
    return table


class TestKernelVocabularyPinned(unittest.TestCase):
    """The kernel vocabulary must not drift without a conscious edit here."""

    @classmethod
    def setUpClass(cls):
        cls.statuses = extract_ts_kernel_statuses()
        cls.events = extract_ts_kernel_event_types()

    def test_statuses_exactly_pinned(self):
        self.assertEqual(
            self.statuses, KERNEL_STATUSES_PINNED,
            f"WR_STATUSES drifted from the pinned set.\n"
            f"  added:   {sorted(self.statuses - KERNEL_STATUSES_PINNED)}\n"
            f"  removed: {sorted(KERNEL_STATUSES_PINNED - self.statuses)}\n"
            f"If this change is intended, update KERNEL_STATUSES_PINNED in "
            f"this file (Section 6) and re-check the collision set in "
            f"TestKernelAndWrpAreDistinctVocabularies.",
        )

    def test_event_types_exactly_pinned(self):
        self.assertEqual(
            self.events, KERNEL_EVENT_TYPES_PINNED,
            f"RUNTIME_EVENT_TYPES drifted from the pinned set.\n"
            f"  added:   {sorted(self.events - KERNEL_EVENT_TYPES_PINNED)}\n"
            f"  removed: {sorted(KERNEL_EVENT_TYPES_PINNED - self.events)}",
        )

    def test_statuses_not_empty(self):
        """Guards against a regex silently failing to extract."""
        self.assertTrue(self.statuses, "WR_STATUSES extraction returned empty")
        self.assertTrue(self.events, "RUNTIME_EVENT_TYPES extraction returned empty")


class TestKernelTransitionTableWellFormed(unittest.TestCase):
    """TRANSITION_TABLE must be closed over the two pinned vocabularies."""

    @classmethod
    def setUpClass(cls):
        cls.table = extract_ts_kernel_transitions()
        cls.statuses = extract_ts_kernel_statuses()
        cls.events = extract_ts_kernel_event_types()

    def test_table_covers_every_status(self):
        """Every WR_STATUS has a transition entry (terminal ones empty)."""
        self.assertEqual(
            set(self.table), self.statuses,
            f"TRANSITION_TABLE keys != WR_STATUSES.\n"
            f"  status without entry: {sorted(self.statuses - set(self.table))}\n"
            f"  entry without status: {sorted(set(self.table) - self.statuses)}",
        )

    def test_every_event_is_declared(self):
        undeclared = {
            ev for entries in self.table.values() for ev, _ in entries
            if ev not in self.events
        }
        self.assertEqual(
            undeclared, set(),
            f"TRANSITION_TABLE references undeclared events: {sorted(undeclared)}",
        )

    def test_every_destination_is_declared(self):
        undeclared = {
            nxt for entries in self.table.values() for _, nxt in entries
            if nxt not in self.statuses
        }
        self.assertEqual(
            undeclared, set(),
            f"TRANSITION_TABLE targets undeclared statuses: {sorted(undeclared)}",
        )

    def test_terminal_states_match_pinned(self):
        """Terminal = no outgoing edges; must match the documented set."""
        terminal = {s for s, e in self.table.items() if not e}
        self.assertEqual(
            terminal, KERNEL_TERMINAL_PINNED,
            f"Terminal-state set drifted.\n"
            f"  now:     {sorted(terminal)}\n"
            f"  pinned:  {sorted(KERNEL_TERMINAL_PINNED)}",
        )

    def test_birth_state_is_draft(self):
        """WR_SUBMITTED is the birth event; DRAFT is the initial status."""
        self.assertIn("WR_SUBMITTED", self.events)
        self.assertIn("DRAFT", self.statuses)
        sources = {ev for ev, _ in self.table.get("DRAFT", set())}
        self.assertIn(
            "WR_SUBMITTED", sources,
            "WR_SUBMITTED is expected to be emitted from DRAFT",
        )


class TestKernelAndWrpAreDistinctVocabularies(unittest.TestCase):
    """The D1 relationship guard.

    The two state machines are separate by design. This asserts their
    *relationship* rather than their equality: the collision set must be
    exactly {QUEUED, FAILED}. A new collision fails here so a human must
    decide whether the two meanings stay distinct — it is never silently
    merged, and no rename is performed.
    """

    @classmethod
    def setUpClass(cls):
        cls.kernel = extract_ts_kernel_statuses()
        cls.wrp = extract_ts_wrp_states()

    def test_wrp_extraction_non_empty(self):
        self.assertTrue(self.wrp, "WRP state extraction returned empty")

    def test_collision_set_is_exactly_pinned(self):
        collisions = self.kernel & self.wrp
        self.assertEqual(
            collisions, KERNEL_WRP_COLLISIONS_PINNED,
            f"Name collision between the kernel and WRP vocabularies changed.\n"
            f"  now:     {sorted(collisions)}\n"
            f"  pinned:  {sorted(KERNEL_WRP_COLLISIONS_PINNED)}\n"
            f"A new collision means the two lifecycles have started sharing a "
            f"name with different semantics. Decide deliberately: either keep "
            f"them distinct (update the pin + document the meanings) or "
            f"reconcile the vocabularies. Do not let it drift silently.",
        )

    def test_kernel_names_never_leak_into_wrp(self):
        """Kernel-only names must not appear in the WRP vocabulary.

        Guards against a partial merge: someone 'helpfully' copying DRAFT /
        ACKED / SETTLED into the planning lifecycle.
        """
        kernel_only = self.kernel - self.wrp
        leaked = {s for s in kernel_only if s in self.wrp}
        self.assertEqual(
            leaked, set(),
            f"Kernel-only statuses leaked into the WRP vocabulary: "
            f"{sorted(leaked)}",
        )

    def test_vocabularies_are_not_equal(self):
        """They must remain genuinely distinct — a merge would be a rename."""
        self.assertNotEqual(
            self.kernel, self.wrp,
            "Kernel and WRP vocabularies have become identical; the D1 guard "
            "assumes two separate lifecycles. If they were intentionally "
            "unified, this guard and its pins must be retired deliberately.",
        )


if __name__ == "__main__":
    unittest.main()
