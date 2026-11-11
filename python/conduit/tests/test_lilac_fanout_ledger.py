#!/usr/bin/env python3
"""Idempotent receipt-to-ticket fan-out + transition ledger (plan 8261671).

Hermetic: a FakeConn emulates the canonical SQL surfaces in memory — no
database, no live state. Covers AC1 (single fan-out ratchet), AC2
(idempotent ledger replay), AC3 (duplicate/conflict/refused classes with
fingerprint pairs), AC4 (ledger-derived position/generation; forged
input refused pre-write).

DB tier: hermetic only. The same flows against a real throwaway schema
live in test_c3_lilac_persistence.py (PG-backed, CONDUIT_PG_DSN).
"""
from __future__ import annotations

import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lilac import (  # noqa: E402
    FANOUT_POLICY_VERSION,
    LilacAdapter,
    LilacPersistenceError,
    payload_fingerprint,
)

NEXUS_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))


class FakeUniqueViolation(Exception):
    def __init__(self, msg="duplicate key"):
        super().__init__(msg)
        self.pgcode = "23505"


class FakeRefused(Exception):
    def __init__(self, msg="grant violation"):
        super().__init__(msg)
        self.pgcode = "P0004"


class FakeBackend:
    """In-memory canonical surfaces keyed exactly like the R4 contract."""

    def __init__(self):
        self.receipts = {}   # (source_system, source_receipt_id) -> (fp, id)
        self.tickets = {}    # (ref, role, pos, gen) -> id
        self.by_id = {}      # ticket id -> (ref, role, pos, gen)
        self.transitions = set()  # (ticket, from, to, input, policy)
        self.transition_ids = {}
        self.fanouts = {}    # (input, kind, policy) -> (outcome, produced_json)
        self.seq = 0
        self.fail_next_receipt = None

    def new_id(self, prefix):
        self.seq += 1
        return "%s-%04d" % (prefix, self.seq)


class FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self._row = None

    def execute(self, sql, params=()):
        b = self._conn.backend
        self._conn.statements.append((sql, params))
        if "INSERT INTO resolution.receipt" in sql:
            if b.fail_next_receipt:
                raise b.fail_next_receipt
            _prod, _kind, ss, srid, fp = params[0], params[1], params[2], params[3], params[4]
            if (ss, srid) in b.receipts:
                raise FakeUniqueViolation()
            rid = b.new_id("rec")
            b.receipts[(ss, srid)] = (fp, rid)
            self._row = (rid,)
        elif "FROM resolution.receipt" in sql:
            ss, srid = params[0], params[1]
            hit = b.receipts.get((ss, srid))
            self._row = hit if hit is None else (hit[0], hit[1])
        elif "INSERT INTO resolution.ticket_transition" in sql:
            key = (params[0], params[2], params[3], params[1], params[4])
            if key in b.transitions:
                raise FakeUniqueViolation()
            b.transitions.add(key)
            tid = b.new_id("trn")
            b.transition_ids[key] = tid
            self._row = (tid,)
        elif "FROM resolution.ticket_transition" in sql:
            key = (params[0], params[1], params[2], params[3], params[4])
            tid = b.transition_ids.get(key)
            self._row = (tid,) if tid else None
        elif "INSERT INTO resolution.fanout_transition" in sql:
            key = (params[0], params[1], params[2])
            if key in b.fanouts:
                raise FakeUniqueViolation()
            b.fanouts[key] = (params[3], params[4])
            self._row = (b.new_id("fan"),)
        elif "FROM resolution.fanout_transition" in sql:
            hit = b.fanouts.get((params[0], params[1], params[2]))
            self._row = hit
        elif "INSERT INTO resolution.ticket" in sql:
            key = (params[0], params[1], params[2], params[4])
            if key in b.tickets:
                raise FakeUniqueViolation()
            tid = b.new_id("tkt")
            b.tickets[key] = tid
            b.by_id[tid] = key
            self._row = (tid,)
        elif "FROM resolution.ticket" in sql and "MAX(position)" in sql:
            ref, role = params[0], params[1]
            positions = [k[2] for k in b.tickets if k[0] == ref and k[1] == role]
            self._row = (max(positions) if positions else -1,)
        elif "FROM resolution.ticket" in sql:
            tid = b.tickets.get((params[0], params[1], params[2], params[3]))
            self._row = (tid,) if tid else None
        elif "UPDATE resolution.ticket" in sql:
            tid = params[1]
            self._row = (tid,) if tid in b.by_id else None
        else:
            raise AssertionError("fake backend has no route for: %.80s" % sql)
        return self

    def fetchone(self):
        return self._row


class FakeConn:
    def __init__(self):
        self.backend = FakeBackend()
        self.statements = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def writes(self):
        return [s for s, _ in self.statements
                if re.search(r"\b(INSERT|UPDATE|DELETE)\b", s, re.IGNORECASE)]


def _adapter():
    return LilacAdapter(lambda: None)


def _receipt_kwargs(**over):
    kw = dict(kind="implementation", source_receipt_id="src-1",
              payload={"a": 1})
    kw.update(over)
    return kw


# ── AC1: single fan-out ratchet ──────────────────────────────────────

ALLOWED_FANOUT_FILES = {
    "python/conduit/lilac.py",
    "python/conduit/db_adapter.py",
    "typescript/conduit-mcp/src/db.ts",
}


def test_ac1_exactly_one_fanout_implementation_per_allowed_file():
    found = {}
    for base, exts, pattern in (
        ("python", (".py",), r"^\s*def (apply_fanout|create_next_tickets)\b"),
        ("typescript", (".ts",),
         r"^\s*(export\s+)?(async\s+)?function advanceTicketsOnReceipt\b"),
    ):
        for root, dirs, files in os.walk(os.path.join(NEXUS_ROOT, base)):
            if any(seg in root for seg in ("tests", "_archived",
                                           "node_modules", "dist")):
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if d != "node_modules"
                       and d != "dist"]
            for fn in files:
                if not fn.endswith(exts):
                    continue
                p = os.path.join(root, fn)
                with open(p, encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if re.search(pattern, line):
                            found.setdefault(os.path.relpath(p, NEXUS_ROOT), []).append(line.strip()[:80])
    assert set(found) == ALLOWED_FANOUT_FILES, (
        "fan-out implementation set changed — a new fan-out path appeared "
        "outside the allowed files: %r" % sorted(set(found) ^ ALLOWED_FANOUT_FILES))
    assert any("apply_fanout" in m for ms in found.values() for m in ms)
    assert any("create_next_tickets" in m for ms in found.values() for m in ms)
    assert any("advanceTicketsOnReceipt" in m for ms in found.values() for m in ms)


# ── AC2: idempotent ledger replay ────────────────────────────────────

def test_ac2_apply_fanout_replay_is_duplicate_equivalent():
    conn, ad = FakeConn(), _adapter()
    specs = [{"workflow_ref": "wr-1", "role": "engineer", "objective": "x"}]
    out1, ids1 = ad.apply_fanout(conn, input_receipt_id="in-1",
                                 kind="implementation", spawn_specs=specs)
    assert out1 == "spawned" and len(ids1) == 1
    n_tickets = len(conn.backend.tickets)
    out2, ids2 = ad.apply_fanout(conn, input_receipt_id="in-1",
                                 kind="implementation", spawn_specs=specs)
    assert out2 == "duplicate-equivalent"
    assert ids2 == ids1
    assert len(conn.backend.tickets) == n_tickets, "replay created tickets"


def test_ac2_record_transition_replay_is_duplicate_equivalent():
    conn, ad = FakeConn(), _adapter()
    kw = dict(ticket_id="t-1", from_status="open", to_status="closed",
              input_receipt_id="in-9")
    out1, id1 = ad.record_transition(conn, **kw)
    out2, id2 = ad.record_transition(conn, **kw)
    assert (out1, out2) == ("accepted", "duplicate-equivalent")
    assert id1 == id2


# ── AC3: duplicate / conflict / refused classes ──────────────────────

def test_ac3_identical_receipt_replay_is_duplicate_equivalent():
    conn, ad = FakeConn(), _adapter()
    out1, id1 = ad.insert_receipt(conn, **_receipt_kwargs())
    out2, id2 = ad.insert_receipt(conn, **_receipt_kwargs())
    assert (out1, out2) == ("accepted", "duplicate-equivalent")
    assert id1 == id2


def test_ac3_conflicting_payload_records_both_fingerprints():
    conn, ad = FakeConn(), _adapter()
    ad.insert_receipt(conn, **_receipt_kwargs(payload={"a": 1}))
    with pytest.raises(LilacPersistenceError) as ei:
        ad.insert_receipt(conn, **_receipt_kwargs(payload={"a": 2}))
    msg = str(ei.value)
    assert "conflict" in msg
    assert payload_fingerprint({"a": 1}) in msg
    assert payload_fingerprint({"a": 2}) in msg


def test_ac3_grant_violation_is_refused_not_merged():
    conn, ad = FakeConn(), _adapter()
    conn.backend.fail_next_receipt = FakeRefused("grant violation")
    with pytest.raises(LilacPersistenceError, match="refused"):
        ad.insert_receipt(conn, **_receipt_kwargs())
    assert conn.backend.receipts == {}, "refused write must not persist"


# ── AC4: ledger-derived position/generation ──────────────────────────

def test_ac4_position_derived_from_empty_ledger_is_zero():
    conn, ad = FakeConn(), _adapter()
    assert ad.derive_next_position(conn, workflow_ref="wr-1", role="eng") == 0


def test_ac4_position_derived_from_max_plus_one():
    conn, ad = FakeConn(), _adapter()
    ad.issue_ticket(conn, workflow_ref="wr-1", role="eng", position=0,
                    predecessor_receipt_id=None)
    ad.issue_ticket(conn, workflow_ref="wr-1", role="eng", position=4,
                    predecessor_receipt_id=None)
    assert ad.derive_next_position(conn, workflow_ref="wr-1", role="eng") == 5
    assert ad.derive_next_position(conn, workflow_ref="wr-1", role="other") == 0


def test_ac4_spec_without_position_gets_derived_position():
    conn, ad = FakeConn(), _adapter()
    out, ids = ad.apply_fanout(
        conn, input_receipt_id="in-2", kind="implementation",
        spawn_specs=[{"workflow_ref": "wr-1", "role": "eng"},
                     {"workflow_ref": "wr-1", "role": "eng"}])
    assert out == "spawned"
    positions = sorted(k[2] for k in conn.backend.tickets)
    assert positions == [0, 1], positions


def test_ac4_forged_position_refused_before_any_write():
    conn, ad = FakeConn(), _adapter()
    with pytest.raises(LilacPersistenceError, match="[Ff]orged|forged"):
        ad.apply_fanout(
            conn, input_receipt_id="in-3", kind="implementation",
            spawn_specs=[{"workflow_ref": "wr-1", "role": "eng",
                          "position": 99}])
    assert conn.writes() == [], "forged spec must not reach the write path"
    assert conn.backend.tickets == {}
    assert conn.backend.fanouts == {}


def test_ac4_forged_generation_refused_before_any_write():
    conn, ad = FakeConn(), _adapter()
    with pytest.raises(LilacPersistenceError, match="[Ff]orged|generation"):
        ad.apply_fanout(
            conn, input_receipt_id="in-4", kind="implementation",
            spawn_specs=[{"workflow_ref": "wr-1", "role": "eng",
                          "generation": 2}])
    assert conn.writes() == []
    assert conn.backend.tickets == {}


def test_ac4_pg_suite_scenario_explicit_positions_still_work():
    # Mirrors the PG-backed test_c3_lilac_persistence fan-out flow so the
    # AC4 gate cannot break it: pre-existing ticket at position 1, first
    # fan-out spawns explicit position 2, replay returns duplicate.
    conn, ad = FakeConn(), _adapter()
    ad.issue_ticket(conn, workflow_ref="zz-c3", role="reviewer", position=1,
                    predecessor_receipt_id=None)
    spec = [{"workflow_ref": "zz-c3", "role": "reviewer", "position": 2,
             "objective": "review the artifact"}]
    o1, p1 = ad.apply_fanout(conn, input_receipt_id="in-r",
                              kind="implementation", spawn_specs=spec)
    assert o1 == "spawned"
    o2, p2 = ad.apply_fanout(conn, input_receipt_id="in-r",
                              kind="implementation", spawn_specs=spec)
    assert o2 == "duplicate-equivalent"
    assert p2 == p1


def test_ac4_matching_explicit_position_accepted():
    conn, ad = FakeConn(), _adapter()
    out, ids = ad.apply_fanout(
        conn, input_receipt_id="in-5", kind="implementation",
        spawn_specs=[{"workflow_ref": "wr-1", "role": "eng", "position": 0,
                      "generation": 0}])
    assert out == "spawned" and len(ids) == 1
