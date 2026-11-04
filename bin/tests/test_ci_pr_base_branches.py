#!/usr/bin/env python3
"""Guard: no workflow may filter `pull_request` by BASE branch.

The failure this prevents, twice over:

1. **A CI gate that reports green while running nothing.** #847 sat `BLOCKED`
   with all 13 of its reported checks passing, because six *required* contexts
   (`sonar`, `build`, `conformance-verifier`, two `mesh-register`,
   `wr-conf-006`) never ran at all. Its base was `plan-project-view`, which
   matched none of the 51 `branches: [ main, master, dev, feature/* ]` filters
   under `pull_request:`. Nothing failed; nothing ran.

2. **Stacking becoming impossible.** A PR stacked on another PR necessarily
   targets a non-main base. With a base filter, that PR cannot obtain the
   checks its merge requires — so the only way to validate stacked work is to
   rebase it onto main, which detaches it from its dependency and (as #848
   showed) can silently absorb the commit it was stacked on. The filter, not
   the stack, was the defect.

A `pull_request` event is filtered by BASE branch; a `push` event is filtered by
the single branch being pushed. The `push:` filters are therefore untouched —
they cost a stacked PR nothing.

The rule: `pull_request.branches` may be absent or `['**']`. An enumerated list
is a lint failure here, so the next workflow added cannot quietly reintroduce
the blind spot.

Parsed with a line scanner rather than a YAML library on purpose: this guard
runs in the stdlib bin tier, and `on:` is the key PyYAML's 1.1 resolver
mangles into `True`.

Run:
  python3 -m pytest bin/tests/test_ci_pr_base_branches.py -v
"""
from __future__ import annotations

import glob
import os
import re
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
WORKFLOWS = os.path.join(REPO_ROOT, ".github", "workflows")

# A floor, not a target: it fails if the glob finds nothing, so a moved
# directory or a renamed extension cannot turn this guard into a green no-op.
MIN_PR_WORKFLOWS = 40


def pull_request_block(lines):
    """(start, end) line indices of the `  pull_request:` block, or None."""
    try:
        start = next(i for i, l in enumerate(lines) if l.rstrip() == "  pull_request:")
    except StopIteration:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^  [A-Za-z]", lines[j]):
            end = j
            break
    return (start, end)


def pr_base_filter(text):
    """{'branches': [...] | None, 'branches_ignore': [...] | None} for pull_request.

    Returns None when the workflow has no pull_request trigger at all.
    """
    lines = text.splitlines()
    block = pull_request_block(lines)
    if block is None:
        return None
    start, end = block
    out = {"branches": None, "branches_ignore": None}
    for line in lines[start + 1:end]:
        m = re.match(r"^    (branches|branches-ignore):\s*(.*)$", line)
        if not m:
            continue
        key = "branches" if m.group(1) == "branches" else "branches_ignore"
        raw = m.group(2).strip()
        if raw.startswith("[") and raw.endswith("]"):
            out[key] = [p.strip().strip("'\"") for p in raw[1:-1].split(",") if p.strip()]
        else:
            out[key] = [raw.strip("'\"")]
    return out


class RealTree(unittest.TestCase):
    def test_every_workflow_allows_any_pr_base(self):
        paths = sorted(glob.glob(os.path.join(WORKFLOWS, "*.yml")))
        self.assertGreater(len(paths), 0, "no workflows found — wrong path?")
        seen = 0
        for path in paths:
            with open(path) as fh:
                filt = pr_base_filter(fh.read())
            if filt is None:
                continue
            seen += 1
            name = os.path.basename(path)
            with self.subTest(workflow=name):
                self.assertIsNone(
                    filt["branches_ignore"],
                    f"{name}: branches-ignore under pull_request excludes PR "
                    f"bases wholesale ({filt['branches_ignore']})",
                )
                self.assertIn(
                    filt["branches"], (None, ["**"]),
                    f"{name}: pull_request filters base branches to "
                    f"{filt['branches']} — a PR stacked on any other base gets "
                    f"no CI at all",
                )

    def test_floor_on_pr_workflows(self):
        """Non-vacuity: the guard must actually be looking at workflows."""
        count = 0
        for path in glob.glob(os.path.join(WORKFLOWS, "*.yml")):
            with open(path) as fh:
                if pr_base_filter(fh.read()) is not None:
                    count += 1
        self.assertGreaterEqual(
            count, MIN_PR_WORKFLOWS,
            f"only {count} workflows with a pull_request trigger were found; "
            f"the guard would pass by covering almost nothing",
        )


class NonVacuity(unittest.TestCase):
    """The checker must fail a narrow filter — otherwise this guard is theatre."""

    def test_narrow_filter_is_detected(self):
        text = (
            "on:\n"
            "  pull_request:\n"
            "    branches: [ main, master, dev, feature/* ]\n"
            "jobs: {}\n"
        )
        filt = pr_base_filter(text)
        self.assertIsNotNone(filt)
        self.assertEqual(
            filt["branches"], ["main", "master", "dev", "feature/*"],
            "the checker failed to see a narrow base filter — it would pass a "
            "workflow that still skips stacked PRs",
        )

    def test_wildcard_is_accepted(self):
        text = "on:\n  pull_request:\n    branches: ['**']\njobs: {}\n"
        self.assertEqual(pr_base_filter(text)["branches"], ["**"])

    def test_absent_filter_is_accepted(self):
        text = "on:\n  pull_request:\njobs: {}\n"
        self.assertIsNone(pr_base_filter(text)["branches"])

    def test_branches_ignore_is_detected(self):
        text = "on:\n  pull_request:\n    branches-ignore: [ 'wip/*' ]\njobs: {}\n"
        self.assertEqual(pr_base_filter(text)["branches_ignore"], ["wip/*"])

    def test_push_filter_is_not_read_as_a_pr_filter(self):
        """`push:` filters are legitimate; they must not be flagged."""
        text = (
            "on:\n"
            "  push:\n"
            "    branches: [ main ]\n"
            "  pull_request:\n"
            "jobs: {}\n"
        )
        self.assertIsNone(pr_base_filter(text)["branches"])
        self.assertIsNone(pull_request_block(text.splitlines()[:2]))


if __name__ == "__main__":
    unittest.main()
