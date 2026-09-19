"""
cascade/wrp conformance: CIR-SDM + CCNF version-lock SOL-framed gates (R-D).

Locks RULING R-D (record 2487aef3, 2026-08-25): #5 CIR-SDM and #6 CCNF
version-lock are SOL-framed admission gates — propositions on the registered
`enforcement_rule_family` / `cir_sdm_mode` / `ccnf_version` dimensions,
outcomes recorded to `peb.transactions` (advisory record-then-act), posture
persisted in `resolution.enforcement_posture` (DB wins once seeded; the
CIR_SDM_ENFORCE env is a bootstrap default only).

  AC1 — CIR violation in enforced + architect-authorized family → governed.
  AC2 — shadow family / unknown family / empty rule_id → not governed, fail
        closed; an enforced row WITHOUT authorized_by never enforces (no
        silent addition, 4a57c089).
  AC3 — every evaluation records (gate=…, admitted, reason) — best-effort,
        a recorder failure never raises and never flips the outcome.
  AC4 — CCNF version-lock: enforced+authorized → governed; shadow → not;
        genuinely malformed envelope (no / non-integer ccnf_version) raises
        ValueError (R4 raw-exception contract), never a fake record.
  AC5 — enforcement DB-wins: posture rows override CIR_SDM_ENFORCE in both
        directions; zero rows → bootstrap (env=0 → shadow, else default);
        divergence surfaces as a warning line.

Usage:
    cd /home/codex/dev/nexus
    python3 -m pytest python/nexus_core/wrp/tests/test_cir_sdm_gate.py -v
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

_SELF_DIR = os.path.dirname(os.path.abspath(__file__))
_NEXUS_PYTHON = os.path.abspath(os.path.join(_SELF_DIR, "..", "..", ".."))
if _NEXUS_PYTHON not in sys.path:
    sys.path.insert(0, _NEXUS_PYTHON)

from nexus_core.wrp.cir_sdm import (                                  # noqa: E402
    RULE_ONE_WAY_GATE,
    RULE_VERSION_LOCK,
)
from nexus_core.wrp.cir_sdm_gate import (                             # noqa: E402
    evaluate_ccnf_version_lock,
    evaluate_cir_sdm_violation,
)
from nexus_core.wrp.enforce_cli import (                              # noqa: E402
    _record_gate_outcomes,
    _record_version_lock_outcomes,
    enforce_stream,
)
from nexus_core.wrp.enforcement import (                              # noqa: E402
    DEFAULT_ENFORCED_RULES,
    load_posture_rows,
    posture_enforced_rules,
    render_enforcement_state,
    resolve_enforced_rules,
)


def _posture(rows):
    """Accept simple (family, mode, authorized_by) tuples as posture rows
    and expand them to the dict shape enforcement/gates expect."""
    out = []
    for r in rows:
        family, mode, auth = (list(r) + [None, None, None])[:3]
        out.append({"family": family, "mode": mode,
                    "authorized_by": auth})
    return out


ENFORCED_ONE_WAY = _posture([(RULE_ONE_WAY_GATE, "enforced", "4a57c089")])
SHADOW_ALL = _posture([
    (RULE_ONE_WAY_GATE, "shadow", None),
    (RULE_VERSION_LOCK, "shadow", None),
])
ENFORCED_VERSION_LOCK = _posture(
    [(RULE_VERSION_LOCK, "enforced", "4a57c089")])


def _violation_dict(rule_id=RULE_ONE_WAY_GATE, violation_id="v-1",
                    event_id="e1", severity="blocking", blocking=True):
    return {
        "violation_id": violation_id,
        "rule_id": rule_id,
        "rule_version": "1",
        "severity": severity,
        "event_id": event_id,
        "cer_id": event_id,
        "description": f"test violation of {rule_id}",
        "detected_at": None,
        "blocking": blocking,
    }


def _event(event_id="e1", ccnf_version=1, **extra):
    ev = {"event_id": event_id, "ccnf_version": ccnf_version}
    ev.update(extra)
    return ev


class TestCIRGateAdmission(unittest.TestCase):
    """AC1/AC2 — the CIR-SDM proposition admits only enforced+authorized
    families."""

    def setUp(self):
        self.rec = mock.patch(
            "cascade.peb_admission.record_gate_outcome",
            return_value=True,
        ).start()
        self.addCleanup(self.rec.stop)

    def test_enforced_authorized_family_governed(self):
        governed, reason = evaluate_cir_sdm_violation(
            _violation_dict(), ENFORCED_ONE_WAY)
        self.assertTrue(governed)
        self.assertIn("governed", reason)

    def test_shadow_family_not_governed(self):
        governed, reason = evaluate_cir_sdm_violation(
            _violation_dict(), SHADOW_ALL)
        self.assertFalse(governed)
        self.assertIn("not governed", reason)

    def test_posture_defaults_to_shadow_when_absent(self):
        # No posture rows at all → the family defaults to shadow (fail
        # closed) even though it is the bootstrap-enforced family.
        governed, reason = evaluate_cir_sdm_violation(
            _violation_dict(), None)
        self.assertFalse(governed)

    def test_unknown_family_fails_closed(self):
        governed, reason = evaluate_cir_sdm_violation(
            _violation_dict(rule_id="cir-sdm.not-a-family"),
            ENFORCED_ONE_WAY)
        self.assertFalse(governed)
        self.assertIn("not in frame vocabulary", reason)

    def test_empty_rule_id_not_governed(self):
        governed, reason = evaluate_cir_sdm_violation(
            _violation_dict(rule_id=""), ENFORCED_ONE_WAY)
        self.assertFalse(governed)
        self.assertIn("no rule family", reason)

    def test_enforced_without_authorization_never_enforces(self):
        # "no silent addition" — an enforced row with no cited decision does
        # not govern.
        rows = _posture([(RULE_ONE_WAY_GATE, "enforced", None)])
        governed, _ = evaluate_cir_sdm_violation(
            _violation_dict(), rows)
        self.assertFalse(governed)


class TestCIRuleRecording(unittest.TestCase):
    def test_outcome_recorded(self):
        with mock.patch("cascade.peb_admission.record_gate_outcome") as rec:
            evaluate_cir_sdm_violation(_violation_dict(), ENFORCED_ONE_WAY)
            rec.assert_called_once()
            kwargs = rec.call_args.kwargs
            self.assertEqual(
                kwargs["gate"], "cir_sdm_gate.evaluate_cir_sdm_violation")
            self.assertEqual(kwargs["entity_id"], "v-1")
            self.assertTrue(kwargs["admitted"])
            self.assertIn("governed", kwargs["reason"])

    def test_refused_outcome_recorded(self):
        with mock.patch("cascade.peb_admission.record_gate_outcome") as rec:
            evaluate_cir_sdm_violation(_violation_dict(), SHADOW_ALL)
            self.assertFalse(rec.call_args.kwargs["admitted"])

    def test_recording_failure_never_raises_and_never_flips(self):
        with mock.patch("cascade.peb_admission.record_gate_outcome",
                        side_effect=RuntimeError("db down")):
            governed, _ = evaluate_cir_sdm_violation(
                _violation_dict(), ENFORCED_ONE_WAY)
        self.assertTrue(governed)   # advisory: outcome independent of record


class TestCcnfVersionLock(unittest.TestCase):
    """AC4 — the #6 proposition frames on the event's ccnf_version."""

    def setUp(self):
        self._post = mock.patch(
            "cascade.peb_admission.record_gate_outcome",
            return_value=True,
        )
        self._post.start()
        self.addCleanup(self._post.stop)

    def test_version_lock_enforced_governed(self):
        governed, reason = evaluate_ccnf_version_lock(
            _event(ccnf_version=1), ENFORCED_VERSION_LOCK)
        self.assertTrue(governed)
        self.assertIn("version governed", reason)

    def test_version_lock_shadow_not_governed(self):
        governed, _ = evaluate_ccnf_version_lock(
            _event(ccnf_version=1), SHADOW_ALL)
        self.assertFalse(governed)

    def test_missing_version_raises_value_error(self):
        with self.assertRaises(ValueError):
            evaluate_ccnf_version_lock({"event_id": "e1"}, None)

    def test_non_integer_version_raises_value_error(self):
        with self.assertRaises(ValueError):
            evaluate_ccnf_version_lock(
                {"event_id": "e1", "ccnf_version": "newer-than-1"}, None)

    def test_version_lock_records_gate_and_version(self):
        with mock.patch("cascade.peb_admission.record_gate_outcome") as rec:
            evaluate_ccnf_version_lock(_event(ccnf_version=2), ENFORCED_VERSION_LOCK)
            rec.assert_called_once()
            kwargs = rec.call_args.kwargs
            self.assertEqual(
                kwargs["gate"], "cir_sdm_gate.evaluate_ccnf_version_lock")
            self.assertEqual(kwargs["entity_id"], "e1")
            self.assertTrue(kwargs["admitted"])


class TestPostureResolution(unittest.TestCase):
    """AC5 — the enforcement layer is DB-first once rows exist."""

    def test_database_wins_over_env_enforced(self):
        # env says "1" but the DB posture shadows everything → shadow wins.
        rules = resolve_enforced_rules("1", SHADOW_ALL)
        self.assertEqual(rules, frozenset())

    def test_database_wins_over_env_zero(self):
        # env says "0" but the DB posture explicitly enforces a family →
        # DB wins.
        rows = _posture([(RULE_ONE_WAY_GATE, "enforced", "some-dec")])
        rules = resolve_enforced_rules("0", rows)
        self.assertEqual(rules, frozenset({RULE_ONE_WAY_GATE}))

    def test_bootstrap_env_zero_all_shadow(self):
        self.assertEqual(resolve_enforced_rules("0", None), frozenset())

    def test_bootstrap_default_enforced(self):
        self.assertEqual(resolve_enforced_rules(None, None),
                         DEFAULT_ENFORCED_RULES)
        self.assertEqual(resolve_enforced_rules("1", None),
                         DEFAULT_ENFORCED_RULES)

    def test_no_silent_addition_in_posture_rules(self):
        rows = _posture([
            (RULE_ONE_WAY_GATE, "enforced", "dec"),
            (RULE_VERSION_LOCK, "enforced", None),   # no authorization
        ])
        self.assertEqual(posture_enforced_rules(rows),
                         frozenset({RULE_ONE_WAY_GATE}))


class TestRenderState(unittest.TestCase):
    def test_database_source_line(self):
        line = render_enforcement_state("1", ENFORCED_ONE_WAY)
        self.assertIn("database: resolution.enforcement_posture", line)
        self.assertIn("enforced", line)

    def test_database_shadow_line(self):
        line = render_enforcement_state(None, SHADOW_ALL)
        self.assertIn("shadow", line)
        self.assertIn("database", line)

    def test_bootstrap_line(self):
        line = render_enforcement_state(None, None)
        self.assertIn("bootstrap", line)

    def test_env_divergence_warning(self):
        line = render_enforcement_state("0", ENFORCED_ONE_WAY)
        self.assertIn("WARNING: CIR_SDM_ENFORCE", line)
        self.assertIn("database wins", line)


class TestLoadPostureRows(unittest.TestCase):
    def test_parses_rows(self):
        proc = SimpleNamespace(
            returncode=0,
            stdout=(
                "cir-sdm.one-way-gate|enforced|4a57c089|2026-08-25 01:00:00\n"
                "cir-sdm.audit-non-influence|shadow||2026-08-25 01:00:00\n"
            ),
        )
        with mock.patch("subprocess.run", return_value=proc) as run:
            rows = load_posture_rows(psql=["psql"])
        run.assert_called_once()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["family"], "cir-sdm.one-way-gate")
        self.assertEqual(rows[0]["mode"], "enforced")
        self.assertEqual(rows[1]["authorized_by"], None)

    def test_failure_returns_empty(self):
        with mock.patch("subprocess.run",
                        side_effect=OSError("no docker")):
            self.assertEqual(load_posture_rows(psql=["psql"]), [])

    def test_nonzero_returncode_returns_empty(self):
        proc = mock.Mock(returncode=1, stdout="", stderr="err")
        with mock.patch("subprocess.run", return_value=proc):
            self.assertEqual(load_posture_rows(psql=["psql"]), [])

    def test_dsn_pathway_selected_by_env(self):
        """CONDUIT_PG_DSN set -> direct psql (no docker), injected psql wins (G1)."""
        proc = SimpleNamespace(returncode=0, stdout="")
        with mock.patch.dict(os.environ, {"CONDUIT_PG_DSN": "postgresql://u:p@h:5432/nexus"}):
            with mock.patch("subprocess.run", return_value=proc) as run:
                load_posture_rows()
        argv = run.call_args[0][0]
        self.assertEqual(argv[0], "psql")
        self.assertIn("postgresql://u:p@h:5432/nexus", argv)
        self.assertNotIn("docker", argv)

    def test_injected_psql_wins_over_dsn(self):
        """Explicit psql= injection still wins over the env DSN (G1 precedence)."""
        proc = SimpleNamespace(returncode=0, stdout="")
        with mock.patch.dict(os.environ, {"CONDUIT_PG_DSN": "postgresql://u:p@h:5432/nexus"}):
            with mock.patch("subprocess.run", return_value=proc) as run:
                load_posture_rows(psql=["psql"])
        argv = run.call_args[0][0]
        self.assertEqual(argv[0], "psql")
        self.assertNotIn("postgresql://u:p@h:5432/nexus", argv)

    def test_legacy_docker_fallback_without_dsn(self):
        """No DSN, no injection -> legacy docker-exec command unchanged."""
        saved = os.environ.pop("CONDUIT_PG_DSN", None)
        try:
            proc = SimpleNamespace(returncode=0, stdout="")
            with mock.patch("subprocess.run", return_value=proc) as run:
                load_posture_rows()
        finally:
            if saved is not None:
                os.environ["CONDUIT_PG_DSN"] = saved
        argv = run.call_args[0][0]
        self.assertEqual(argv[:4], ["docker", "exec", "-i", "pgvector_db"])


class TestEnforceStreamPosture(unittest.TestCase):
    """The CLI decision path honors DB posture without flip-flopping the
    legacy bootstrap semantics when no rows exist."""

    FORWARD = [
        {"event_id": "e1", "type": "WR_SUBMITTED", "wrId": "wr-1",
         "timestamp": 1, "domain": "execution", "actor": {"type": "system"}},
        {"event_id": "e2", "type": "WR_VALIDATED", "wrId": "wr-1",
         "timestamp": 2, "domain": "execution", "actor": {"type": "system"}},
        {"event_id": "e3", "type": "WR_QUEUED", "wrId": "wr-1",
         "timestamp": 3, "domain": "execution", "actor": {"type": "system"}},
        {"event_id": "e4", "type": "WR_CLAIMED", "wrId": "wr-1",
         "timestamp": 4, "domain": "execution", "actor": {"type": "system"}},
        {"event_id": "e5", "type": "WR_ACKED", "wrId": "wr-1",
         "timestamp": 5, "domain": "execution", "actor": {"type": "system"}},
    ]

    def _run(self, proposed, **kw):
        return enforce_stream(self.FORWARD, proposed, **kw)

    def test_posture_source_database_when_rows(self):
        result = self._run(
            {"event_id": "e6", "type": "WR_CLAIMED", "wrId": "wr-1",
             "timestamp": 6},
            posture_rows=ENFORCED_ONE_WAY,
        )
        self.assertEqual(result["posture_source"], "database")

    def test_posture_source_bootstrap_when_no_rows(self):
        result = self._run(
            {"event_id": "e6", "type": "WR_CLAIMED", "wrId": "wr-1",
             "timestamp": 6},
        )
        self.assertEqual(result["posture_source"], "bootstrap")

    def test_enforced_one_way_rejects_reverse_transition(self):
        result = self._run(
            {"event_id": "e6", "type": "WR_CLAIMED", "wrId": "wr-1",
             "timestamp": 6},
            posture_rows=ENFORCED_ONE_WAY,
        )
        self.assertTrue(result["reject"])
        self.assertEqual(len(result["decisions"]), 1)
        self.assertEqual(result["decisions"][0]["rule_id"], RULE_ONE_WAY_GATE)

    def test_db_shadow_overrides_env(self):
        # env "1" would enforce, but the DB shadows the family → no reject.
        result = self._run(
            {"event_id": "e6", "type": "WR_CLAIMED", "wrId": "wr-1",
             "timestamp": 6},
            env_value="1",
            posture_rows=SHADOW_ALL,
        )
        self.assertFalse(result["reject"])
        self.assertEqual(result["decisions"], [])
        self.assertEqual(result["state"], "shadow")

    def test_bare_ac2_reverse_transition_still_rejects_bootstrap(self):
        # Legacy behavior (no posture): bootstrap default enforces the
        # one-way gate family.
        result = self._run(
            {"event_id": "e6", "type": "WR_CLAIMED", "wrId": "wr-1",
             "timestamp": 6},
        )
        self.assertTrue(result["reject"])
        self.assertEqual(result["state"], "enforced")


class TestGateWiringHelpers(unittest.TestCase):
    """The CLI's advisory recording helpers never raise and route each
    failure through the SOL gates."""

    def test_record_gate_outcomes_advisory(self):
        with mock.patch(
            "cascade.peb_admission.record_gate_outcome"
        ) as rec:
            _record_gate_outcomes(
                [_violation_dict(), _violation_dict(violation_id="v2")],
                ENFORCED_ONE_WAY,
            )
            self.assertEqual(rec.call_count, 2)

    def test_record_gate_outcomes_survives_gate_failure(self):
        with mock.patch("cascade.peb_admission.record_gate_outcome",
                        side_effect=RuntimeError("boom")):
            _record_gate_outcomes([_violation_dict()], ENFORCED_ONE_WAY)
        # No exception escapes (advisory path).

    def test_version_lock_events_routed(self):
        events = [
            _event("e1", ccnf_version=1),
            _event("e2", ccnf_version=2),
        ]
        violations = [
            _violation_dict(rule_id=RULE_VERSION_LOCK,
                            violation_id="vl-1", event_id="e2"),
        ]
        with mock.patch("cascade.peb_admission.record_gate_outcome") as rec:
            _record_version_lock_outcomes(events, violations, None)
            self.assertEqual(rec.call_count, 1)
            self.assertEqual(rec.call_args.kwargs["entity_id"], "e2")

    def test_version_lock_missing_event_skipped(self):
        events = [_event("e1", ccnf_version=1)]
        violations = [
            _violation_dict(rule_id=RULE_VERSION_LOCK,
                            violation_id="v-1", event_id="nowhere"),
        ]
        with mock.patch("cascade.peb_admission.record_gate_outcome") as rec:
            _record_version_lock_outcomes(events, violations, None)
            rec.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)