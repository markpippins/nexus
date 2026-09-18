#!/usr/bin/env python3
"""Hermetic tests: calendar TypeSpec contract (Q1 first deliverable, a330914e).

Two layers, per the house pattern:

- STRUCTURE (always runs, no toolchain): file inventory, namespace, the
  model-only stance (no routes/verbs), root-umbrella wiring, and pins for
  the six design decisions from post a330914e — deterministic ids (Q2),
  kind epistemics, extendable windows, refs-only ContextBundle, address,
  reconcile states — plus the `model`-keyword encodedName precedent.
- COMPILE (real `tsp compile` when a toolchain is locatable: worktree,
  shared checkout, then PATH; explicit skip otherwise). node_modules is
  git-ignored, so a fresh checkout without deps SKIPS rather than fails —
  pass-by-skip is not pass-by-accident: the skip names itself.
"""

import os
import shutil
import subprocess
import unittest
from pathlib import Path

_SELF = Path(__file__).resolve().parent
_REPO = _SELF.parent.parent
_TSP_V1 = _REPO / "typespec" / "v1"
_CAL_MODELS = _TSP_V1 / "calendar" / "models.tsp"
_CAL_MAIN = _TSP_V1 / "calendar" / "main.tsp"
_ROOT_MAIN = _TSP_V1 / "main.tsp"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


class StructureTests(unittest.TestCase):
    """Contract shape — pins that never need a toolchain."""

    def setUp(self):
        self.assertTrue(_CAL_MODELS.exists(), f"missing {_CAL_MODELS}")
        self.assertTrue(_CAL_MAIN.exists(), f"missing {_CAL_MAIN}")
        self.models = _read(_CAL_MODELS)
        self.main = _read(_CAL_MAIN)

    # ── inventory & namespace ────────────────────────────────────────

    def test_namespace_is_org_nexus_calendar(self):
        self.assertIn("namespace org.nexus.calendar", self.models)
        self.assertIn("namespace org.nexus.calendar", self.main)

    def test_route_free_stance(self):
        """write-queue precedent: model-only, no HTTP surface."""
        body = self.models + self.main
        for banned in ("@route", "@post", "@get", "@put", "@delete", "@patch"):
            self.assertNotIn(banned, body, f"model-only contract must not contain {banned}")
        self.assertIn("route-free", self.main.lower() + self.models.lower())

    def test_main_imports_models(self):
        self.assertIn('import "./models.tsp";', self.main)

    def test_root_umbrella_imports_calendar(self):
        root = _read(_ROOT_MAIN)
        self.assertIn('import "./calendar/main.tsp";', root)
        # doc comment explains why it exists
        self.assertIn("a330914e", root)

    # ── the six design decisions (post a330914e section 4) ───────────

    def test_deterministic_identity_q2(self):
        """eventId derived from (machine, emitter, window.start) — dedupe
        without coordination; idempotent re-application."""
        self.assertIn("model CalendarEvent", self.models)
        doc = self.models.split("model CalendarEvent {")[0]
        self.assertIn("Deterministic identity (Q2)", doc)
        # the derivation formula itself, pinned as text
        self.assertIn("source.machine", doc)
        self.assertIn("source.emitter", doc)
        self.assertIn("window.start", doc)

    def test_kind_epistemics(self):
        """scheduled/occurred/observed — the V174 unreachable-vs-absent
        discipline applied to time."""
        for kind in ('Scheduled: "scheduled"', 'Occurred: "occurred"', 'Observed: "observed"'):
            self.assertIn(kind, self.models)
        self.assertIn("CalendarEventKind", self.models)

    def test_extendable_window(self):
        """`end` optional; extension appends, never mutates."""
        self.assertIn("end?: utcDateTime;", self.models)
        self.assertIn("never mutates", self.models)

    def test_refs_only_context_bundle(self):
        """ContextBundle carries opaque references, never inlined payloads
        (V167/V169 stance)."""
        self.assertIn("model ContextBundle", self.models)
        self.assertIn("snapshotRefs", self.models)
        self.assertIn("digestRefs", self.models)
        self.assertIn("keychains", self.models)
        self.assertIn("procedureCards", self.models)

    def test_session_address(self):
        """ip/port where agents join or services start (Q5 open)."""
        self.assertIn("model SessionAddress", self.models)
        self.assertIn("port: int32;", self.models)
        self.assertIn("Q5", self.models)

    def test_reconcile_lifecycle(self):
        """open -> closed -> reconciled (close-then-insert discipline)."""
        for state in ('Open: "open"', 'Closed: "closed"', 'Reconciled: "reconciled"'):
            self.assertIn(state, self.models)
        self.assertIn("ReconcileState", self.models)

    # ── house conventions ────────────────────────────────────────────

    def test_model_keyword_encodedname_precedent(self):
        """`model` is a TypeSpec keyword; house precedent (aegis-srv,
        tackle-registry) is @encodedName on a keyword-free field name."""
        self.assertIn('@encodedName("application/json", "model")', self.models)
        self.assertIn("modelId?", self.models)
        # no bare `model` property declaration
        self.assertNotRegex(self.models, r"^\s+model\??\s*:", re_m := __import__("re").MULTILINE)

    def test_uuid_convention(self):
        """@format("uuid") on string, matching aegis-srv/ui-event-bus."""
        self.assertIn('@format("uuid")', self.models)

    def test_minutes_and_schedule_semantics_documented(self):
        """Session doc carries the operator's vocabulary: minutes on
        reconcile, schedules as recurring calendars."""
        session_doc = self.models.split("model Session {")[0]
        self.assertIn("minutes", session_doc)
        self.assertIn("recurring", session_doc)


class CompileTests(unittest.TestCase):
    """Real `tsp compile --no-emit` when a toolchain is locatable."""

    @classmethod
    def _toolchain(cls):
        """Locate (node, npx/tsp) — worktree, shared checkout, then PATH."""
        candidates = [
            _TSP_V1,  # this checkout (worktree or main)
            Path("/home/codex/dev/nexus/typespec/v1"),
        ]
        for d in candidates:
            npx = d / "node_modules" / ".bin" / "tsp"
            if npx.exists():
                return d, str(npx)
        if shutil.which("tsp"):
            return Path.cwd(), "tsp"
        return None, None

    @classmethod
    def setUpClass(cls):
        cls.tsp_dir, cls.tsp_bin = cls._toolchain()

    def test_compile_calendar_contract(self):
        if not self.tsp_bin:
            self.skipTest(
                "TypeSpec toolchain not locatable (node_modules is git-ignored; "
                "install with `cd typespec/v1 && npm install` to run the "
                "compile check)"
            )
        r = subprocess.run(
            [self.tsp_bin, "compile", "calendar/main.tsp", "--no-emit"],
            cwd=str(self.tsp_dir), capture_output=True, text=True, timeout=240,
        )
        self.assertEqual(r.returncode, 0, f"calendar contract failed to compile:\n{r.stdout}\n{r.stderr}")
        self.assertIn("successfully", r.stdout + r.stderr)

    def test_compile_root_umbrella_includes_calendar(self):
        if not self.tsp_bin:
            self.skipTest(
                "TypeSpec toolchain not locatable (node_modules is git-ignored; "
                "install with `cd typespec/v1 && npm install` to run the "
                "compile check)"
            )
        r = subprocess.run(
            [self.tsp_bin, "compile", "main.tsp", "--no-emit"],
            cwd=str(self.tsp_dir), capture_output=True, text=True, timeout=240,
        )
        self.assertEqual(r.returncode, 0, f"root umbrella failed to compile:\n{r.stdout}\n{r.stderr}")


if __name__ == "__main__":
    unittest.main()
