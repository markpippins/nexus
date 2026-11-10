#!/usr/bin/env python3
"""Hermetic parity tests: WRP event vocabulary + ledger state graph (plan 8261649).

The cutover has four places that must agree, and no runtime path that would
notice if they drifted apart:

  1. ``typescript/conduit-mcp/src/runtime-kernel.ts`` — the kernel
     ``TRANSITION_TABLE`` and the constants derived from it
     (``LEDGER_STATE_GRAPH``, ``RUNTIME_TO_CANONICAL_EVENT``,
     ``CANONICAL_EVENT_TYPES``).
  2. ``typescript/conduit-mcp/migrations/034-wr-event-vocabulary.sql`` — the
     live/CI migration that creates the owned graph and narrows the CHECK.
  3. ``typescript/conduit-mcp/src/db.ts`` — the fresh-schema seed and the
     version-19 CHECK narrowing.
  4. ``python/cascade/event_store.py`` — the Python write boundary.

A drift here is silent: the projector would reject a legal transition, or a
writer would store a value the CHECK refuses. These tests parse the four
surfaces and compare them, so the disagreement fails in CI instead of in
production. No database, no network, no toolchain.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KERNEL = REPO / "typescript" / "conduit-mcp" / "src" / "runtime-kernel.ts"
DB_TS = REPO / "typescript" / "conduit-mcp" / "src" / "db.ts"
MIGRATION = REPO / "typescript" / "conduit-mcp" / "migrations" / "034-wr-event-vocabulary.sql"
CI_BOOTSTRAP = REPO / "sql" / "ci-bootstrap" / "nexus-ci-bootstrap.sql"
PY_STORE = REPO / "python" / "cascade" / "event_store.py"

GRAPH_TABLE = "work_request_state_graph"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _block(text: str, start_marker: str, end_marker: str) -> str:
    """Return the text between the first start_marker and the next end_marker."""
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end]


def _pairs(block: str, pattern: str) -> list[tuple[str, str]]:
    return re.findall(pattern, block)


# ── kernel ────────────────────────────────────────────────────────────


def kernel_status_to_ledger() -> dict[str, str]:
    block = _block(
        _read(KERNEL),
        "const RUNTIME_STATUS_TO_LEDGER_STATE",
        "};",
    )
    return dict(_pairs(block, r'(\w+):\s*"(\w+)"'))


def kernel_transitions() -> list[tuple[str, str]]:
    """(from_status, next_status) pairs from TRANSITION_TABLE."""
    block = _block(_read(KERNEL), "const TRANSITION_TABLE", "\n};")
    edges: list[tuple[str, str]] = []
    for status, body in re.findall(r"(\w+):\s*\[(.*?)\n  \]", block, re.S):
        for next_status in re.findall(r'nextStatus:\s*"(\w+)"', body):
            edges.append((status, next_status))
    return edges


def kernel_transition_entries() -> list[tuple[str, str, str]]:
    """(from_status, event, next_status) triples from TRANSITION_TABLE."""
    block = _block(_read(KERNEL), "const TRANSITION_TABLE", "\n};")
    out: list[tuple[str, str, str]] = []
    for status, body in re.findall(r"(\w+):\s*\[(.*?)\n  \]", block, re.S):
        for event, next_status in re.findall(
            r'event:\s*"(\w+)",\s*nextStatus:\s*"(\w+)"', body
        ):
            out.append((status, event, next_status))
    return out


def kernel_ledger_edges() -> set[tuple[str, str]]:
    mapping = kernel_status_to_ledger()
    return {(mapping[a], mapping[b]) for a, _event, b in kernel_transition_entries()}


def kernel_runtime_ledger_state() -> dict[str, str]:
    block = _block(_read(KERNEL), "export const RUNTIME_EVENT_LEDGER_STATE", "};")
    return dict(_pairs(block, r'(\w+):\s*"(\w+)"'))


# The ledger chain, in the order the runtime verbs advance it. CREATED seeds
# PROPOSED, then each verb records the stage it completes.
_LEDGER_CHAIN = [
    "WR_SUBMITTED",
    "WR_VALIDATED",
    "WR_QUEUED",
    "WR_CLAIMED",
    "WR_ACKED",
    "WR_SETTLED",
]
_FAILURE_VERBS = {
    "WR_FAILED": "FAILED",
    "WR_REJECTED": "CANCELLED",
    "WR_NOOP": "CANCELLED",
    "WR_DEFERRED": "CANCELLED",
}


def kernel_canonical_events() -> set[str]:
    block = _block(_read(KERNEL), "export const CANONICAL_EVENT_TYPES", "] as const;")
    return set(re.findall(r'"([A-Z][A-Z_.]+)"', block))


def kernel_runtime_to_canonical() -> dict[str, str]:
    block = _block(_read(KERNEL), "export const RUNTIME_TO_CANONICAL_EVENT", "};")
    return dict(_pairs(block, r'(\w+):\s*"([A-Z][A-Z_.]+)"'))


# ── SQL surfaces ──────────────────────────────────────────────────────


def _graph_insert_edges(text: str, table: str) -> set[tuple[str, str]]:
    """Edges from the INSERT INTO <table> ... VALUES (...) statement."""
    start = text.index(f"INSERT INTO {table}")
    end = text.index(";", start)
    return set(_pairs(text[start:end], r"\('(\w+)',\s*'(\w+)'\)"))


def migration_edges() -> set[tuple[str, str]]:
    return _graph_insert_edges(_read(MIGRATION), f"conduit.{GRAPH_TABLE}")


def db_seed_edges() -> set[tuple[str, str]]:
    return _graph_insert_edges(_read(DB_TS), "${PG_SCHEMA}." + GRAPH_TABLE)


def ci_bootstrap_edges() -> set[tuple[str, str]]:
    return _graph_insert_edges(_read(CI_BOOTSTRAP), f"conduit.{GRAPH_TABLE}")


def migration_canonical_events() -> set[str]:
    text = _read(MIGRATION)
    block = _block(text, "CHECK (event_type IN (", "));")
    return set(re.findall(r"'([A-Z][A-Z_.]+)'", block))


def ci_bootstrap_canonical_events() -> set[str]:
    text = _read(CI_BOOTSTRAP)
    match = re.search(
        r"CONSTRAINT work_request_events_event_type_check CHECK \(\(event_type = ANY \(ARRAY\[(.*?)\]\)\)\)",
        text,
        re.S,
    )
    assert match, "CI bootstrap lost the work_request_events event_type CHECK"
    return set(re.findall(r"'([A-Z][A-Z_.]+)'::text", match.group(1)))


# ── Python write boundary ─────────────────────────────────────────────


def py_canonical_events() -> set[str]:
    block = _block(_read(PY_STORE), "CANONICAL_EVENT_TYPES = frozenset(", "\n)")
    return set(re.findall(r'"([A-Z][A-Z_.]+)"', block))


def py_runtime_to_canonical() -> dict[str, str]:
    block = _block(_read(PY_STORE), "RUNTIME_TO_CANONICAL_EVENT: dict[str, str] = {", "}")
    return dict(_pairs(block, r'"(\w+)":\s*"([A-Z][A-Z_.]+)"'))


def py_runtime_ledger_state() -> dict[str, str]:
    block = _block(_read(PY_STORE), "RUNTIME_EVENT_LEDGER_STATE: dict[str, str] = {", "}")
    return dict(_pairs(block, r'"(\w+)":\s*"(\w+)"'))


# ── tests ─────────────────────────────────────────────────────────────


def test_kernel_graph_is_eleven_edges():
    """The kernel table is the authority; the count is pinned so a silent
    transition addition cannot slip past the migration's own 11-edge check."""
    assert len(kernel_ledger_edges()) == 11


def test_migration_graph_matches_kernel():
    assert migration_edges() == kernel_ledger_edges()


def test_db_seed_matches_kernel():
    assert db_seed_edges() == kernel_ledger_edges()


def test_ci_bootstrap_graph_matches_kernel():
    assert ci_bootstrap_edges() == kernel_ledger_edges()


def test_migration_uses_the_shared_graph_table_name():
    text = _read(MIGRATION)
    assert f"conduit.{GRAPH_TABLE}" in text
    assert "wr_transition_graph" not in text, (
        "migration 034 must use the same graph table as db.ts and the CI bootstrap"
    )
    # allowed_transitions() must read the owned graph, not a second CASE table.
    fn = _block(text, "CREATE OR REPLACE FUNCTION conduit.allowed_transitions", "$$ LANGUAGE")
    assert f"conduit.{GRAPH_TABLE}" in fn


def test_canonical_event_vocabulary_agrees_everywhere():
    kernel = kernel_canonical_events()
    assert kernel, "kernel CANONICAL_EVENT_TYPES not found"
    assert migration_canonical_events() == kernel
    assert ci_bootstrap_canonical_events() == kernel
    assert py_canonical_events() == kernel


def test_runtime_to_canonical_mapping_agrees():
    assert py_runtime_to_canonical() == kernel_runtime_to_canonical()


def test_python_ledger_state_mapping_matches_kernel():
    """The two write boundaries must declare the same event → state table."""
    assert py_runtime_ledger_state() == kernel_runtime_ledger_state()


def test_ledger_mapping_is_an_allowed_walk():
    """Every mapped step must be an edge in the owned graph.

    This is the property that matters: a value the graph does not allow is an
    INVALID_TRANSITION at projection time, however plausible the table looks.
    """
    mapping = kernel_runtime_ledger_state()
    edges = kernel_ledger_edges()
    for verb in _LEDGER_CHAIN:
        assert verb in mapping, f"{verb} missing from the ledger-state table"
    walk = [mapping[verb] for verb in _LEDGER_CHAIN]
    assert walk[0] == "PROPOSED", "CREATED must seed the projection at PROPOSED"
    for current, following in zip(walk, walk[1:]):
        assert (current, following) in edges, f"no graph edge {current} → {following}"
    for verb, expected in _FAILURE_VERBS.items():
        assert mapping[verb] == expected
        assert any(edge[1] == expected for edge in edges), (
            f"{expected} is not a reachable graph state"
        )


def test_db_v19_transcodes_before_narrowing_the_check():
    """A database holding WR_* rows must be transcoded before the canonical
    CHECK is added, or the bootstrap fails on its own history."""
    text = _read(DB_TS)
    block = _block(text, "version: 19,", "version: 21,")
    transcode = block.index("UPDATE ${PG_SCHEMA}.work_request_events")
    narrow = block.index("ADD CONSTRAINT work_request_events_event_type_check")
    assert transcode < narrow
    assert "'WR_SUBMITTED' THEN 'WORKREQUEST.CREATED'" in block
