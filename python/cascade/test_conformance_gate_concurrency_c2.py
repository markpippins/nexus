"""test_conformance_gate_concurrency_c2.py — inspector C2 conformance.

Proves the per-call evaluation-context contract for the SOL-framed gates
(inspector report 5d45f921 finding C2 / architect plan 8261648):

  C1  concurrent evaluations with different frames produce results
      matching their own call context (plan criterion: thread pool,
      barrier-synchronized, differing backends/families/versions)
  C2  the shared built state is never mutated on the evaluation path —
      after any number of evaluations the shared propositions still carry
      empty frame_values and the shared interpreter has no registered
      entity (the pre-fix code failed both deterministically)
  C3  single-threaded semantics are identical to the pre-fix contract —
      verdicts and reasons unchanged (plus the existing suites
      test_watch_gate.py / test_cir_sdm_gate.py run unmodified)
  C4  every evaluation still records to peb_admission under its own gate
      name (advisory record-then-act preserved)

Hermetic: no database, no network — cascade.peb_admission is patched to
an in-memory recorder (house pattern from test_watch_gate.py).
"""

import os
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

_HERE = os.path.dirname(os.path.abspath(__file__))
_PY = os.path.dirname(_HERE)
if _PY not in sys.path:
    sys.path.insert(0, _PY)

from cascade import watch_gate  # noqa: E402
import cascade.peb_admission  # noqa: E402
from nexus_core.wrp import cir_sdm_gate  # noqa: E402


# ── In-memory PEB recorder (advisory path, no DB) ─────────────────────

_RECORDED = []


def _fake_record_gate_outcome(**kwargs):
    _RECORDED.append(kwargs)


# ── Fixtures ──────────────────────────────────────────────────────────

def _watch(status="active", backend="operator"):
    return {
        "id": f"watch-{backend}-{status}",
        "status": status,
        "max_turns": 0,
        "turn_count": 0,
        "idle_timeout_ms": 0,
        "last_activity": None,
        "execution_backend": backend,
    }


def _violation(family, violation_id):
    return {
        "violation_id": violation_id,
        "rule_id": family,
        "severity": "high",
        "event_id": "evt-1",
        "description": "conformance",
        "blocking": True,
    }


_POSTURE_AUTHORIZED = [
    {"family": f, "mode": "enforced", "authorized_by": "dec-c2"}
    for f in cir_sdm_gate._ALL_FAMILIES
]


def _barrier_pool(fns):
    """Run callables concurrently, released together by a barrier."""
    barrier = threading.Barrier(len(fns))
    results = [None] * len(fns)

    def _run(i, fn):
        barrier.wait()
        results[i] = fn()

    with ThreadPoolExecutor(max_workers=len(fns)) as pool:
        futures = [pool.submit(_run, i, fn) for i, fn in enumerate(fns)]
        for f in futures:
            f.result()
    return results


# ── watch_gate ────────────────────────────────────────────────────────

class WatchGateConcurrencyC2(unittest.TestCase):

    def setUp(self):
        _RECORDED.clear()
        self._orig = cascade.peb_admission.record_gate_outcome
        cascade.peb_admission.record_gate_outcome = _fake_record_gate_outcome

    def tearDown(self):
        cascade.peb_admission.record_gate_outcome = self._orig

    def test_concurrent_backends_frame_isolation(self):
        """C1 — differing backends evaluated concurrently never cross frames."""
        backends = ("operator", "harness", "freebuff")
        expected = {b: (True, "") for b in backends}
        for round_no in range(40):
            fns = [
                (lambda b=b: watch_gate.evaluate_watch_admission(
                    _watch(backend=b), now_ms=1_000_000,
                    enforce_preflights=False))
                for b in backends
            ]
            results = _barrier_pool(fns)
            for backend, outcome in zip(backends, results):
                self.assertEqual(
                    outcome, expected[backend],
                    f"round {round_no}: backend {backend} got {outcome}",
                )

    def test_concurrent_active_and_closed_never_cross(self):
        """C1 — an admitted and a rejected watch evaluated together stay
        each on their own verdict (entity contamination would flip one)."""
        fns = [
            lambda: watch_gate.evaluate_watch_admission(
                _watch(status="active", backend="operator"),
                now_ms=1_000_000, enforce_preflights=False),
            lambda: watch_gate.evaluate_watch_admission(
                _watch(status="closed", backend="harness"),
                now_ms=1_000_000, enforce_preflights=False),
        ]
        for round_no in range(40):
            results = _barrier_pool(fns)
            self.assertEqual(results[0], (True, ""), f"round {round_no}")
            self.assertEqual(
                results[1], (False, "assertion failed (status=closed)"),
                f"round {round_no}")

    def test_unknown_backend_still_fails_closed_concurrently(self):
        """C1 — ungoverned backend fails closed while governed ones admit."""
        fns = [
            lambda: watch_gate.evaluate_watch_admission(
                _watch(backend="operator"), now_ms=1_000_000,
                enforce_preflights=False),
            lambda: watch_gate.evaluate_watch_admission(
                _watch(backend="bogus"), now_ms=1_000_000,
                enforce_preflights=False),
        ]
        for round_no in range(25):
            results = _barrier_pool(fns)
            self.assertEqual(results[0], (True, ""), f"round {round_no}")
            self.assertEqual(
                results[1][0], False, f"round {round_no}")
            self.assertIn("context_mismatch", results[1][1], f"round {round_no}")

    def test_shared_state_never_mutated(self):
        """C2 — after evaluations, shared frame_values empty, no entity
        registered on the shared interpreter. The pre-fix code failed
        this deterministically."""
        watch_gate.evaluate_watch_admission(
            _watch(backend="operator"), now_ms=1_000_000,
            enforce_preflights=False)
        st = watch_gate._STATE
        self.assertIsNotNone(st)
        self.assertEqual(st["prop"].frame_values, [])
        self.assertIsNone(st["interp"].entities.get(watch_gate._ENTITY_ID))

    def test_single_threaded_semantics_identical(self):
        """C3 — verdicts and reasons match the pre-fix contract."""
        cases = [
            (_watch(backend="operator"), (True, "")),
            (_watch(backend="harness"), (True, "")),
            (_watch(backend="freebuff"), (True, "")),
            (_watch(status="closed", backend="operator"),
             (False, "assertion failed (status=closed)")),
            (_watch(status="paused", backend="operator"),
             (False, "assertion failed (status=paused)")),
            (_watch(backend="bogus"), (False, "context_mismatch (backend=bogus)")),
            (None, (False, "no watch provided")),
        ]
        for watch, expected in cases:
            self.assertEqual(
                watch_gate.evaluate_watch_admission(
                    watch, now_ms=1_000_000, enforce_preflights=False),
                expected)

    def test_peb_recording_preserved_per_gate(self):
        """C4 — advisory record-then-act still fires with the gate name."""
        watch_gate.evaluate_watch_admission(
            _watch(backend="operator"), now_ms=1_000_000,
            enforce_preflights=False)
        self.assertEqual(len(_RECORDED), 1)
        self.assertEqual(
            _RECORDED[0]["gate"], "watch_gate.evaluate_watch_admission")
        self.assertTrue(_RECORDED[0]["admitted"])


# ── cir_sdm_gate ──────────────────────────────────────────────────────

class CirSdmGateConcurrencyC2(unittest.TestCase):

    def setUp(self):
        _RECORDED.clear()
        self._orig = cascade.peb_admission.record_gate_outcome
        cascade.peb_admission.record_gate_outcome = _fake_record_gate_outcome

    def tearDown(self):
        cascade.peb_admission.record_gate_outcome = self._orig

    def test_concurrent_families_frame_isolation(self):
        """C1 — differing (family, mode) pairs evaluated concurrently
        never cross frames."""
        families = cir_sdm_gate._ALL_FAMILIES
        for round_no in range(25):
            fns = [
                (lambda fam=fam: cir_sdm_gate.evaluate_cir_sdm_violation(
                    _violation(fam, f"v-{round_no}-{fam}"),
                    _POSTURE_AUTHORIZED))
                for fam in families
            ]
            results = _barrier_pool(fns)
            for fam, outcome in zip(families, results):
                governed, reason = outcome
                self.assertTrue(
                    governed, f"round {round_no}: {fam} -> {reason}")
                self.assertIn(f"family={fam}", reason, f"round {round_no}")

    def test_concurrent_governed_and_shadow_never_cross(self):
        """C1 — an enforced/authorized family and a shadow family
        evaluated together each keep their own verdict."""
        shadow_posture = [
            {"family": "cir-sdm.one-way-gate", "mode": "shadow",
             "authorized_by": None},
            {"family": "cir-sdm.version-lock", "mode": "enforced",
             "authorized_by": "dec-c2"},
        ]

        def _governed():
            return cir_sdm_gate.evaluate_cir_sdm_violation(
                _violation("cir-sdm.version-lock", "v-g"),
                _POSTURE_AUTHORIZED)

        def _shadow():
            return cir_sdm_gate.evaluate_cir_sdm_violation(
                _violation("cir-sdm.one-way-gate", "v-s"), shadow_posture)

        for round_no in range(40):
            results = _barrier_pool([_governed, _shadow])
            self.assertTrue(results[0][0], f"round {round_no}")
            self.assertFalse(results[1][0], f"round {round_no}")

    def test_concurrent_ccnf_versions_frame_isolation(self):
        """C1 — differing ccnf_version scalars evaluated concurrently
        never cross typed_scalar frames."""
        event = lambda ver: {"event_id": f"e-{ver}", "ccnf_version": ver}  # noqa: E731
        versions = (33, 34, 35)
        for round_no in range(25):
            fns = [
                (lambda ver=ver: cir_sdm_gate.evaluate_ccnf_version_lock(
                    event(ver), _POSTURE_AUTHORIZED))
                for ver in versions
            ]
            results = _barrier_pool(fns)
            for ver, outcome in zip(versions, results):
                governed, reason = outcome
                # A crossed frame (another call's version pfv against this
                # call's context) yields context_mismatch -> not governed,
                # so the governed verdict itself proves frame isolation.
                self.assertTrue(
                    governed, f"round {round_no}: v{ver} -> {reason}")
                self.assertIn(
                    "version governed", reason, f"round {round_no}")

    def test_shared_state_never_mutated_cir(self):
        """C2 — shared propositions keep empty frame_values and the shared
        interpreter keeps no entities after evaluations of both gates."""
        cir_sdm_gate.evaluate_cir_sdm_violation(
            _violation("cir-sdm.one-way-gate", "v-1"), _POSTURE_AUTHORIZED)
        cir_sdm_gate.evaluate_ccnf_version_lock(
            {"event_id": "e-1", "ccnf_version": 34}, _POSTURE_AUTHORIZED)
        st = cir_sdm_gate._STATE
        self.assertIsNotNone(st)
        self.assertEqual(st["cir_prop"].frame_values, [])
        self.assertEqual(st["ccnf_prop"].frame_values, [])
        self.assertIsNone(
            st["interp"].entities.get(cir_sdm_gate._CIR_ENTITY_ID))
        self.assertIsNone(
            st["interp"].entities.get(cir_sdm_gate._CCNF_ENTITY_ID))

    def test_single_threaded_semantics_identical_cir(self):
        """C3 — verdicts and reasons match the pre-fix contract."""
        governed, reason = cir_sdm_gate.evaluate_cir_sdm_violation(
            _violation("cir-sdm.one-way-gate", "v-1"), _POSTURE_AUTHORIZED)
        self.assertTrue(governed)
        self.assertIn("authorized=dec-c2", reason)

        ungoverned, reason2 = cir_sdm_gate.evaluate_cir_sdm_violation(
            _violation("cir-sdm.one-way-gate", "v-2"),
            [{"family": "cir-sdm.one-way-gate", "mode": "shadow",
              "authorized_by": None}])
        self.assertFalse(ungoverned)
        self.assertEqual(
            reason2, "not governed (family=cir-sdm.one-way-gate mode=shadow)")

        nogov, reason3 = cir_sdm_gate.evaluate_cir_sdm_violation(
            _violation("cir-sdm.not-a-family", "v-3"), _POSTURE_AUTHORIZED)
        self.assertFalse(nogov)
        self.assertIn("not in frame vocabulary", reason3)

        # ccnf: authorized version-lock admits; malformed envelope raises.
        ok, reason4 = cir_sdm_gate.evaluate_ccnf_version_lock(
            {"event_id": "e-2", "ccnf_version": 34}, _POSTURE_AUTHORIZED)
        self.assertTrue(ok)
        self.assertIn("version governed", reason4)
        self.assertIn("authorized=dec-c2", reason4)
        with self.assertRaises(ValueError):
            cir_sdm_gate.evaluate_ccnf_version_lock(
                {"event_id": "e-3"}, _POSTURE_AUTHORIZED)

    def test_peb_recording_preserved_per_gate_cir(self):
        """C4 — both cir gates still record under their own gate names."""
        cir_sdm_gate.evaluate_cir_sdm_violation(
            _violation("cir-sdm.one-way-gate", "v-1"), _POSTURE_AUTHORIZED)
        cir_sdm_gate.evaluate_ccnf_version_lock(
            {"event_id": "e-1", "ccnf_version": 34}, _POSTURE_AUTHORIZED)
        gates = [r["gate"] for r in _RECORDED]
        self.assertEqual(
            gates,
            ["cir_sdm_gate.evaluate_cir_sdm_violation",
             "cir_sdm_gate.evaluate_ccnf_version_lock"])


# ── cross-module ──────────────────────────────────────────────────────

class CrossModuleConcurrencyC2(unittest.TestCase):

    def setUp(self):
        _RECORDED.clear()
        self._orig = cascade.peb_admission.record_gate_outcome
        cascade.peb_admission.record_gate_outcome = _fake_record_gate_outcome

    def tearDown(self):
        cascade.peb_admission.record_gate_outcome = self._orig

    def test_watch_and_cir_gates_concurrently(self):
        """C1 — the two gate modules run concurrently with isolated frames."""
        def _watch_op():
            return watch_gate.evaluate_watch_admission(
                _watch(backend="operator"), now_ms=1_000_000,
                enforce_preflights=False)

        def _watch_closed():
            return watch_gate.evaluate_watch_admission(
                _watch(status="closed", backend="freebuff"),
                now_ms=1_000_000, enforce_preflights=False)

        def _cir():
            return cir_sdm_gate.evaluate_cir_sdm_violation(
                _violation("cir-sdm.version-lock", "v-x"),
                _POSTURE_AUTHORIZED)

        def _ccnf():
            return cir_sdm_gate.evaluate_ccnf_version_lock(
                {"event_id": "e-x", "ccnf_version": 34}, _POSTURE_AUTHORIZED)

        for round_no in range(25):
            results = _barrier_pool([_watch_op, _watch_closed, _cir, _ccnf])
            self.assertEqual(results[0], (True, ""), f"round {round_no}")
            self.assertEqual(
                results[1], (False, "assertion failed (status=closed)"),
                f"round {round_no}")
            self.assertTrue(results[2][0], f"round {round_no}")
            self.assertTrue(results[3][0], f"round {round_no}")
        # Shared state of BOTH modules untouched after the storm.
        self.assertEqual(watch_gate._STATE["prop"].frame_values, [])
        self.assertEqual(cir_sdm_gate._STATE["cir_prop"].frame_values, [])
        self.assertEqual(cir_sdm_gate._STATE["ccnf_prop"].frame_values, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
