#!/usr/bin/env python3
"""Hermetic tests: continuity.attest (boot-shim attestation wiring).

No database, no network: every DB interaction goes through an injected
exec_fn (pitfall #15 — inject the dependency boundary). Pins the contract
from the module docstring and the ratified event-identity rules:

- open-ness: satisfaction-based (nothing cites the root), never the
  refuted interval reading (recorded_until_dt, which never closes)
- scan: never raises; table-absence and exec-failure are honest SKIP data
- scan dispositions: actionable / g1_refused / uninvolved, classified by
  capability + authorship, case-insensitively
- the boolean-sentinel trap: caps compare psql's RENDERED 't'/'f' — a
  fake returning 'true' (the ::text form) must NOT read as capability
- record_attestation client-side gates: G2 empty evidence, G3 fail-closed
  (missing row, verify=f), G1 author refusal — all refused locally with
  zero exec calls beyond the check
- server gate pass-through: ATP0001/ATP0003 refusals surfaced verbatim,
  generic errors degraded without raising
- success path: INSERT issued with kind='attestation', cites_id bound,
  evidence as a JSON array; row read back (attestation_id + txid)
"""

import json
import os
import sys
import unittest

_SELF = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_SELF, "..", "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "python"))

from continuity import attest as att  # noqa: E402


def fn_ok(rows=None):
    """exec_fn returning queued rows per call; records every SQL."""
    calls = []

    def run(sql):
        calls.append(sql)
        if rows:
            return rows.pop(0)
        return ""

    run.calls = calls
    return run


def fn_raises(exc):
    def run(sql):
        raise exc
    run.calls = []
    return run


class TableAbsenceTests(unittest.TestCase):
    def test_absent_table_is_skip_data(self):
        f = fn_ok([""])  # to_regclass -> empty
        s = att.scan(f, "tester")
        self.assertFalse(s["scanned"])
        self.assertIn("inert", s["reason"])
        self.assertEqual(s["open"], [])

    def test_exec_failure_degrades_never_raises(self):
        s = att.scan(fn_raises(RuntimeError("conn refused")), "tester")
        self.assertFalse(s["scanned"])
        self.assertIn("scan error", s["reason"])


class CapabilityTests(unittest.TestCase):
    def test_rendered_boolean_t_means_capability(self):
        f = fn_ok(["t"])
        self.assertTrue(att.attestor_capabilities(f, "tester")["can_verify"])
        self.assertIn("can_verify_work_requests, false", f.calls[0])

    def test_boolean_text_cast_trap_is_not_capability(self):
        # 'true' is the ::text form; psql renders t/f — the module must read
        # the rendered form, so 'true' must NOT be treated as capability.
        self.assertFalse(att.attestor_capabilities(fn_ok(["true"]), "x")["can_verify"])
        self.assertFalse(att.attestor_capabilities(fn_ok(["f"]), "x")["can_verify"])
        self.assertFalse(att.attestor_capabilities(fn_ok([""]), "x")["can_verify"])

    def test_missing_role_fails_closed(self):
        self.assertFalse(att.attestor_capabilities(fn_ok([""]), "ghost")["can_verify"])


class ScanDispositionTests(unittest.TestCase):
    REQ = ("11111111-1111-1111-1111-111111111111|agent-record:abc (V177)|dba|"
           '[{"run": "wr-conf-031: 17 passed"}]|2026-09-17 15:13')

    def _scan(self, caps_row, role):
        # fetch_open_requests gets ONE exec call returning both rows
        # (one psql invocation -> one multi-line result)
        return att.scan(fn_ok(["t", caps_row, self.REQ + "\n" + self.REQ]), role)

    def test_actionable_for_capable_non_author(self):
        s = self._scan("t", "tester")
        self.assertEqual(s["actionable"], 2)
        self.assertEqual({i["disposition"] for i in s["open"]}, {"actionable"})
        self.assertIn("--attest 11111111", s["open"][0]["note"])

    def test_g1_refused_for_the_author(self):
        s = self._scan("t", "DBA")  # case-insensitive author match
        self.assertEqual(s["actionable"], 0)
        self.assertEqual({i["disposition"] for i in s["open"]}, {"g1_refused"})

    def test_uninvolved_without_capability(self):
        s = self._scan("f", "critic")
        self.assertEqual({i["disposition"] for i in s["open"]}, {"uninvolved"})


def fn_satisfaction_db(roots, citers):
    """exec_fn that evaluates the emitted open-ness rule over fixtures.

    Models only what the statement says: a root survives when no citer with
    kind='attestation' points at it. If the statement ever reintroduces the
    refuted recorded_until_dt test, this fails loudly instead of quietly
    returning every root (which is how the original defect behaved).
    """
    calls = []

    def run(sql):
        calls.append(sql)
        if "to_regclass" in sql:
            return "nebula.attestations"
        if "nebula.roles" in sql:
            return "t"
        if "'verification_request'" in sql:
            assert "recorded_until_dt" not in sql, "interval predicate restored"
            assert "NOT EXISTS" in sql, "open-ness must be satisfaction-based"
            rows = []
            for rid, work_ref in roots:
                cited = any(a_kind == "attestation" and a_cite == rid
                            for a_kind, a_cite in citers)
                if not cited:
                    rows.append(f"{rid}|{work_ref}|dba|[]|2026-09-17 15:13")
            return "\n".join(rows)
        return ""

    run.calls = calls
    return run


class OpennessPredicateTests(unittest.TestCase):
    """Open-ness is satisfaction-based; both documented dispositions pinned.

    The rule lives in SQL, so these tests pin the statement's shape and model
    its semantics over fixtures. Real execution against PostgreSQL is the
    live acceptance run recorded on the PR, not a unit test.
    """

    CITED = "11111111-1111-1111-1111-111111111111"
    UNCITED = "22222222-2222-2222-2222-222222222222"

    def test_statement_is_not_exists_over_attestation_citers(self):
        f = fn_ok()
        att.fetch_open_requests(f)
        sql = f.calls[0]
        self.assertIn("NOT EXISTS", sql)
        self.assertIn("a.cites_id = r.attestation_id", sql)
        self.assertIn("a.kind = 'attestation'", sql)
        self.assertNotIn("recorded_until_dt", sql)

    def test_cited_root_disappears_uncited_root_survives(self):
        f = fn_satisfaction_db(
            [(self.CITED, "agent-record:cited"),
             (self.UNCITED, "agent-record:uncited")],
            [("attestation", self.CITED)] * 4)
        s = att.scan(f, "tester")
        self.assertTrue(s["scanned"])
        self.assertEqual([i["attestation_id"] for i in s["open"]], [self.UNCITED])
        self.assertEqual(s["actionable"], 1)

    def test_greenlight_citer_does_not_satisfy_a_request_root(self):
        # Pinned disposition: ATP0005 lets greenlight cite attestations only,
        # so a greenlight row citing a root cannot mean the root was verified.
        # Ambiguity stays open (fail-closed).
        f = fn_satisfaction_db([(self.CITED, "agent-record:cited")],
                               [("greenlight", self.CITED)])
        s = att.scan(f, "tester")
        self.assertEqual([i["attestation_id"] for i in s["open"]], [self.CITED])

    def test_nothing_clears_every_root(self):
        f = fn_satisfaction_db([(self.CITED, "a"), (self.UNCITED, "b")], [])
        self.assertEqual(len(att.scan(f, "tester")["open"]), 2)


class ActionableWordingTests(unittest.TestCase):
    def test_note_says_citing_attestation_not_recording_closes(self):
        note = att.classify("tester", {"attestation_id": "abc"}, True)["note"]
        self.assertIn("cleared by a citing attestation", note)
        self.assertIn("--attest ", note)


class RecordGateTests(unittest.TestCase):
    def test_g2_empty_evidence_refused_with_no_db_call(self):
        f = fn_ok()
        r = att.record_attestation(f, "tester", "c", ["", "   "])
        self.assertFalse(r["recorded"])
        self.assertIn("G2", r["reason"])
        self.assertEqual(f.calls, [])

    def test_g3_missing_row_fails_closed(self):
        f = fn_ok([""])  # caps lookup -> empty
        r = att.record_attestation(f, "ghost", "c", ["run: x"])
        self.assertFalse(r["recorded"])
        self.assertIn("G3", r["reason"])
        self.assertEqual(len(f.calls), 1)

    def test_g3_verify_false_refused(self):
        f = fn_ok(["f"])
        r = att.record_attestation(f, "critic", "c", ["run: x"])
        self.assertFalse(r["recorded"])
        self.assertIn("G3", r["reason"])

    def test_g1_author_refused_after_cite_resolution(self):
        f = fn_ok(["t", "dba|agent-record:abc"])  # caps=t, cite resolves to dba's work
        r = att.record_attestation(f, "DBA", "c", ["run: x"])
        self.assertFalse(r["recorded"])
        self.assertIn("G1", r["reason"])
        self.assertEqual(len(f.calls), 2)  # no INSERT issued

    def test_unresolvable_cite_refused(self):
        f = fn_ok(["t", ""])  # caps=t, cite does not resolve
        r = att.record_attestation(f, "tester", "deadbeef", ["run: x"])
        self.assertFalse(r["recorded"])
        self.assertIn("does not resolve", r["reason"])

    def test_success_inserts_and_reads_back(self):
        # calls: caps, cite-resolve, INSERT (empty out), read-back
        f = fn_ok(["t", "dba|agent-record:abc", "",
                   "22222222-2222-2222-2222-222222222222|17899999"])
        r = att.record_attestation(f, "tester", "11111111-1111-1111-1111-111111111111",
                                   ["wr-conf-031: 17 passed", " txid 17773430 "])
        self.assertTrue(r["recorded"])
        self.assertEqual(r["attestation_id"], "22222222-2222-2222-2222-222222222222")
        self.assertEqual(r["txid"], "17899999")
        insert = f.calls[2]
        self.assertIn("INSERT INTO nebula.attestations", insert)
        self.assertIn("'attestation'", insert)
        self.assertIn("11111111-1111-1111-1111-111111111111", insert)
        self.assertIn(json.dumps(["wr-conf-031: 17 passed", "txid 17773430"]).replace("'", "''"),
                      insert)
        self.assertIn("NULLIF('','')::uuid", insert)  # no fabricated record ref

    def test_server_gate_refusal_surfaced_verbatim(self):
        calls = []
        def run(sql):
            calls.append(sql)
            raise RuntimeError("ATP0001: [G1-self-attestation] role tester ...")
        r = att.record_attestation(run, "tester", "c", ["run: x"])
        self.assertFalse(r["recorded"])
        self.assertIn("ATP0001", r["reason"])

    def test_generic_error_degraded_never_raises(self):
        def run(sql):
            raise OSError("docker down")
        r = att.record_attestation(run, "tester", "c", ["run: x"])
        self.assertFalse(r["recorded"])
        self.assertIn("record error", r["reason"])


if __name__ == "__main__":
    unittest.main()
