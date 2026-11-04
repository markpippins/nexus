#!/usr/bin/env python3
"""Guard: an Adonis app that has specs must have a CI job that runs them.

The failure this prevents is the one this guard was born from: `adonisjs/
nexus-control-edge` carried two functional spec files (~20 assertions across the
health, conduit, tackle, wind, kernel, ui-tools and vision surfaces) and
*no workflow ran them*. A grep of `.github/workflows/` for "adonis" returned one
hit, and it was a comment inside an unrelated bcrypt-migration E2E.

That is the same class of defect as the 38 guards referenced by no workflow
(fb11ad19) and the base-filtered `pull_request` triggers (#847): the artifact
exists, reads as coverage, and silently asserts nothing. Unlike a failing test,
nothing ever tells you. It has to be checked structurally.

**The rule.** For every app under `adonisjs/` that contains at least one
`tests/**/*.spec.{ts,js,mjs}` file, some workflow must (a) name that app's
directory and (b) run its suite. A workflow that merely mentions the path does
not count — the comment in wr-conf-046.yml is exactly that, and it is what made
the original grep look reassuring.

**Not checked on purpose:** an app with no specs (`broker-gateway-proxy` today —
only `tests/bootstrap.ts`). Fabricating a job that runs `npm test` over zero
specs would produce a green check that asserts nothing, which is the defect this
guard exists to catch, inverted. That gap is recorded in the PR and in
engineer record 351a1c03 instead; when the app gains specs, rule (a)+(b) above
starts applying to it automatically, with no edit here.

Run:
  python3 -m pytest bin/tests/test_adonis_suites_are_wired.py -v
"""
from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ADONIS_DIR = REPO_ROOT / "adonisjs"
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

# Spec file names AdonisJS' own adonisrc.ts test suites glob for.
SPEC_PATTERNS = ("*.spec.ts", "*.spec.js", "*.spec.mjs")

# A run, as opposed to a mention. `npm test`, `npm run test`, or the underlying
# command. Kept deliberately narrow: `npm ci`+`npm test` is the shape in use.
_RUNS_SUITE = re.compile(r"\b(?:npm\s+(?:run\s+)?test|node\s+ace\s+test)\b")

# Floor, not a target: fails if discovery breaks, so a renamed directory or an
# extension change cannot turn this guard into a green no-op.
MIN_APPS_WITH_SPECS = 1


def apps() -> list[Path]:
    """Every Adonis app directory (has a package.json) under adonisjs/."""
    if not ADONIS_DIR.is_dir():
        return []
    return sorted(
        p for p in ADONIS_DIR.iterdir() if p.is_dir() and (p / "package.json").is_file()
    )


def specs(app: Path) -> list[Path]:
    tests = app / "tests"
    if not tests.is_dir():
        return []
    found: list[Path] = []
    for pattern in SPEC_PATTERNS:
        found.extend(tests.rglob(pattern))
    return sorted(found)


def workflows() -> list[Path]:
    if not WORKFLOW_DIR.is_dir():
        return []
    return sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(WORKFLOW_DIR.glob("*.yaml"))


def workflow_runs_app(text: str, app_name: str) -> bool:
    """True when this workflow both names the app's directory and runs a suite."""
    return f"adonisjs/{app_name}" in text and bool(_RUNS_SUITE.search(text))


def unwired(apps_with_specs: list[tuple[str, list[Path]]], wf_texts: dict[str, str]):
    """(app_name, spec_count) for apps no workflow exercises."""
    missing = []
    for name, spec_files in apps_with_specs:
        if not any(workflow_runs_app(text, name) for text in wf_texts.values()):
            missing.append((name, len(spec_files)))
    return missing


class AdonisSuitesAreWired(unittest.TestCase):
    def test_every_app_with_specs_is_run_by_some_workflow(self):
        discovered = [(a.name, specs(a)) for a in apps()]
        with_specs = [(n, s) for n, s in discovered if s]
        self.assertGreaterEqual(
            len(with_specs),
            MIN_APPS_WITH_SPECS,
            f"discovery found no Adonis app with spec files under {ADONIS_DIR} — "
            "the walk is broken, not the suite",
        )

        wf_texts = {str(p.name): p.read_text(encoding="utf-8", errors="replace") for p in workflows()}
        self.assertTrue(wf_texts, f"no workflow files found under {WORKFLOW_DIR}")

        missing = unwired(with_specs, wf_texts)
        self.assertEqual(
            missing,
            [],
            "Adonis app(s) have spec files that NO workflow runs: "
            + ", ".join(f"adonisjs/{n} ({c} spec files)" for n, c in missing)
            + ". A suite nothing executes reports nothing; add a workflow that "
            "runs `npm test` for that app (see .github/workflows/adonis-control-edge.yml).",
        )

    # ── non-vacuity: the matcher must be able to say NO ──────────────────────
    # Without these, a matcher that returned True for everything would pass the
    # test above and the guard would be decoration.

    def test_mentioning_the_path_without_running_the_suite_is_not_wired(self):
        # The literal wr-conf-046.yml shape: the path appears in prose.
        text = (
            "# known plaintext writers (assembly-srv createUser,\n"
            "# adonisjs/nexus-control-edge changeme default) keep working\n"
            "run: python3 -m pytest python/nexus_core/wrp/tests/test_x.py -v\n"
        )
        self.assertFalse(workflow_runs_app(text, "nexus-control-edge"))

    def test_running_a_suite_for_another_app_is_not_wired(self):
        text = "working-directory: adonisjs/broker-gateway-proxy\nrun: npm test\n"
        self.assertFalse(workflow_runs_app(text, "nexus-control-edge"))

    def test_naming_the_app_and_running_the_suite_is_wired(self):
        text = "working-directory: adonisjs/nexus-control-edge\nrun: npm test\n"
        self.assertTrue(workflow_runs_app(text, "nexus-control-edge"))

    def test_unwired_identifies_the_planted_app(self):
        planted = [("planted-app", [Path("tests/x.spec.ts")])]
        wf = {"only.yml": "working-directory: adonisjs/other\nrun: npm test\n"}
        self.assertEqual(unwired(planted, wf), [("planted-app", 1)])
        wf_ok = {"ok.yml": "working-directory: adonisjs/planted-app\nrun: npm test\n"}
        self.assertEqual(unwired(planted, wf_ok), [])


if __name__ == "__main__":
    unittest.main()
