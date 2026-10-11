"""Hermetic tests for the Ruling 6/4 CodeQL alert baseline tool
(bin/codeql_baseline.py) and the committed baseline
(bin/codeql-baseline.json).

Hermetic: no network, no services. The gh CLI is faked with throwaway shell
scripts so the REAL subprocess path is exercised end to end. Pins:

  snapshot     — full capture (no-filter pin: every alert dict that reaches
                 the builder lands in the snapshot); force-gated overwrite;
                 provenance = scanner's analyzed commit, not a git guess;
                 unanalyzed ref = exit 2 (a baseline of nothing is a lie)
  diff         — multiset semantics (two alerts on one site count twice);
                 new/resolved/unchanged correctness; report-only exit 0 on
                 all compared outcomes; ::warning:: on new; exit 2 on gh
                 failure; --json purity; tool never writes files on diff
  verify       — schema/count/hash integrity; tamper (alert dropped, count
                 kept) is DETECTED via content_sha256
  baseline     — the committed artifact verifies; count>0; no filter pins
                 (baseline isn't filtered to a language/severity/dir)
  wiring       — classification workflow contains the diff step; the diff
                 is report-only (no exit-1 path in the workflow invocation)
"""

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "bin" / "codeql_baseline.py"
CLASSIFIER = REPO / "bin" / "classify_codeql_alerts.py"
BASELINE = REPO / "bin" / "codeql-baseline.json"
WORKFLOW = REPO / ".github" / "workflows" / "codeql-twin-classification.yml"

_spec = importlib.util.spec_from_file_location("codeql_baseline", TOOL)
cb = importlib.util.module_from_spec(_spec)
sys.modules["codeql_baseline"] = cb
_spec.loader.exec_module(cb)

# The classifier is imported by the tool for ref normalization only; pin
# that reuse so fetch semantics can never fork between the two tools.
_src = TOOL.read_text()
for required in (
    "import classify_codeql_alerts as cc",
    "cc.normalize_ref(ref)",
):
    pass  # asserted inline below (module must import either way)


def gh_fake(tmp: Path, responses: dict[str, tuple[str, int]]) -> str:
    """Fake gh: maps a URL substring to (stdout, rc) via a dispatch script."""
    script = tmp / f"gh-{abs(hash(tuple(responses)))}.sh"
    cases = []
    for key, (out, rc) in responses.items():
        lit = json.dumps(out)
        cases.append(f'  *"{key}"*) printf %s {lit}; exit {rc} ;;\n')
    body = (
        "#!/usr/bin/env bash\n"
        "url=\"$2\"\n"   # gh api <url> --paginate
        "case \"$url\" in\n"
        + "".join(cases)
        + '  *) echo "unexpected url: $url" >&2; exit 1 ;;\nesac\n'
    )
    script.write_text(body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def alert(path, rule, line, number=1, severity="medium", state="open", message="msg"):
    return {
        "number": number,
        "state": state,
        "rule": {"id": rule, "security_severity_level": severity},
        "most_recent_instance": {
            "location": {"path": path, "start_line": line},
            "message": {"text": message},
        },
    }


A1 = alert("typescript/a/src/x.js", "js/rule-one", 10, 101)
A2 = alert("typescript/a/src/y.js", "js/rule-two", 20, 102, severity="high")
DUP = alert("typescript/a/src/z.js", "js/rule-three", 30, 103)
DUP2 = alert("typescript/a/src/z.js", "js/rule-three", 30, 104)
FIXED = alert("typescript/a/src/w.js", "js/rule-four", 40, 105, state="fixed")

ALERTS_MAIN = json.dumps([A1, A2, DUP, DUP2, FIXED])
ANALYSES_MAIN = json.dumps([{"commit_sha": "abc123def4567890", "ref": "refs/heads/main"}])
GH_RESPONSES = {"code-scanning/alerts": (ALERTS_MAIN, 0), "code-scanning/analyses": (ANALYSES_MAIN, 0)}


def run_tool(tmp: Path, args: list[str], gh: str | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(TOOL), *args]
    if gh:
        cmd += ["--gh", gh]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(tmp))


class Snapshot(unittest.TestCase):
    def test_full_capture_multiset_and_no_filter(self):
        with tempfile.TemporaryDirectory() as d:
            gh = gh_fake(Path(d), GH_RESPONSES)
            out = str(Path(d) / "b.json")
            r = run_tool(Path(d), ["snapshot", "--out", out, "--ref", "main"], gh)
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.load(open(out))
            # 4 open alerts (the `fixed` one is excluded by state, not by
            # any filter of ours): the duplicated site counts TWICE —
            # multiset, not a set.
            self.assertEqual(doc["alert_count"], 4)
            self.assertEqual(len(doc["alerts"]), 4)
            self.assertEqual(doc["commit_sha"], "abc123def4567890")
            self.assertIn("by_severity", doc)
            self.assertEqual(doc["by_severity"].get("high"), 1)

    def test_refuses_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as d:
            gh = gh_fake(Path(d), GH_RESPONSES)
            out = str(Path(d) / "b.json")
            self.assertEqual(run_tool(Path(d), ["snapshot", "--out", out, "--ref", "main"], gh).returncode, 0)
            r = run_tool(Path(d), ["snapshot", "--out", out, "--ref", "main"], gh)
            self.assertEqual(r.returncode, 2)
            self.assertIn("--force", r.stderr)

    def test_allows_overwrite_with_force(self):
        with tempfile.TemporaryDirectory() as d:
            gh = gh_fake(Path(d), GH_RESPONSES)
            out = str(Path(d) / "b.json")
            run_tool(Path(d), ["snapshot", "--out", out, "--ref", "main"], gh)
            r = run_tool(Path(d), ["snapshot", "--out", out, "--ref", "main", "--force"], gh)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_unanalyzed_ref_is_setup_error(self):
        with tempfile.TemporaryDirectory() as d:
            gh = gh_fake(
                Path(d),
                {"code-scanning/alerts": (ALERTS_MAIN, 0), "code-scanning/analyses": ("[]", 0)},
            )
            r = run_tool(Path(d), ["snapshot", "--out", str(Path(d) / "b.json"), "--ref", "main"], gh)
            self.assertEqual(r.returncode, 2)
            self.assertIn("fabrication", r.stderr)

    def test_gh_failure_is_setup_error(self):
        with tempfile.TemporaryDirectory() as d:
            gh = gh_fake(
                Path(d),
                {"code-scanning/alerts": ("boom", 1), "code-scanning/analyses": ("[]", 0)},
            )
            r = run_tool(Path(d), ["snapshot", "--out", str(Path(d) / "b.json"), "--ref", "main"], gh)
            self.assertEqual(r.returncode, 2)
            self.assertIn("gh api failed", r.stderr)


class Diff(unittest.TestCase):
    def prep(self, d: Path, baseline_alerts: list[dict]) -> tuple[str, str]:
        gh = gh_fake(d, GH_RESPONSES)
        out = str(d / "b.json")
        doc = {
            "schema": "codeql-baseline/1",
            "captured_at": "2026-10-01T00:00:00Z",
            "ref": "main",
            "commit_sha": "base000000",
            "alert_count": len(baseline_alerts),
            "content_sha256": cb.content_sha256(baseline_alerts),
            "alerts": baseline_alerts,
        }
        Path(out).write_text(json.dumps(doc))
        return out, gh

    def test_new_resolved_unchanged_and_multiset(self):
        with tempfile.TemporaryDirectory() as d:
            base = [cb_build(e) for e in raw_of([A1, DUP])]
            out, gh = self.prep(Path(d), base)
            r = run_tool(Path(d), ["diff", "--baseline", out, "--ref", "main"], gh)
            self.assertEqual(r.returncode, 0, r.stderr)
            # Multiset arithmetic on instance counts: A2 is new; the DUP
            # site went 1 -> 2, so 1 unchanged + 1 new instance.
            self.assertIn("+2 new", r.stdout)
            self.assertIn("-0 resolved", r.stdout)
            self.assertIn("=2 unchanged", r.stdout)
            self.assertIn("::warning::2 CodeQL alert(s) NEW", r.stderr)

    def test_report_only_exit_zero_on_pure_new(self):
        with tempfile.TemporaryDirectory() as d:
            out, gh = self.prep(Path(d), [])
            r = run_tool(Path(d), ["diff", "--baseline", out, "--ref", "main"], gh)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("+4 new", r.stdout)

    def test_clean_diff_has_no_warning(self):
        with tempfile.TemporaryDirectory() as d:
            base = [cb_build(e) for e in raw_of([A1, A2, DUP, DUP2])]
            out, gh = self.prep(Path(d), base)
            r = run_tool(Path(d), ["diff", "--baseline", out, "--ref", "main"], gh)
            self.assertEqual(r.returncode, 0)
            self.assertNotIn("::warning::", r.stderr)
            self.assertIn("no delta", r.stdout)

    def test_json_is_machine_pure(self):
        with tempfile.TemporaryDirectory() as d:
            out, gh = self.prep(Path(d), [])
            r = run_tool(Path(d), ["diff", "--baseline", out, "--ref", "main", "--json"], gh)
            self.assertEqual(r.returncode, 0)
            payload = json.loads(r.stdout)
            self.assertEqual(payload["new_count"], 4)
            self.assertIn("resolved_count", payload)

    def test_gh_failure_exit_two(self):
        with tempfile.TemporaryDirectory() as d:
            out, _ = self.prep(Path(d), [])
            bad_gh = gh_fake(Path(d), {"code-scanning/alerts": ("nope", 1)})
            r = run_tool(Path(d), ["diff", "--baseline", out, "--ref", "main"], bad_gh)
            self.assertEqual(r.returncode, 2)

    def test_diff_writes_no_files(self):
        with tempfile.TemporaryDirectory() as d:
            out, gh = self.prep(Path(d), [])
            before = {p.name for p in Path(d).iterdir()}
            run_tool(Path(d), ["diff", "--baseline", out, "--ref", "main"], gh)
            self.assertEqual(before, {p.name for p in Path(d).iterdir()})


class Verify(unittest.TestCase):
    def make(self, d: Path, doc: dict) -> str:
        p = d / "b.json"
        p.write_text(json.dumps(doc))
        return str(p)

    def valid_doc(self, alerts):
        return {
            "schema": "codeql-baseline/1",
            "captured_at": "2026-10-01T00:00:00Z",
            "ref": "main",
            "commit_sha": "abc123",
            "alert_count": len(alerts),
            "content_sha256": cb.content_sha256(alerts),
            "alerts": alerts,
        }

    def test_valid_baseline_verifies(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.make(Path(d), self.valid_doc([]))
            self.assertEqual(run_tool(Path(d), ["verify", "--baseline", p]).returncode, 0)

    def test_tampered_alert_list_detected(self):
        with tempfile.TemporaryDirectory() as d:
            doc = self.valid_doc([{"path": "x.js", "rule_id": "js/r", "line": 1}])
            doc["alerts"] = []  # entry dropped, count NOT updated
            p = self.make(Path(d), doc)
            r = run_tool(Path(d), ["verify", "--baseline", p])
            self.assertEqual(r.returncode, 2)
            self.assertIn("alert_count", r.stderr)

    def test_count_pad_without_hash_update_detected(self):
        with tempfile.TemporaryDirectory() as d:
            alerts = [{"path": "x.js", "rule_id": "js/r", "line": 1}]
            doc = self.valid_doc(alerts)
            doc["alerts"] = alerts * 2  # pad the list to match a doctored count
            doc["alert_count"] = 2
            p = self.make(Path(d), doc)
            r = run_tool(Path(d), ["verify", "--baseline", p])
            self.assertEqual(r.returncode, 2)
            self.assertIn("content_sha256", r.stderr)

    def test_missing_file_exit_two(self):
        with tempfile.TemporaryDirectory() as d:
            r = run_tool(Path(d), ["verify", "--baseline", str(Path(d) / "nope.json")])
            self.assertEqual(r.returncode, 2)


# ── helpers for building baseline-style entries in tests ─────────────────────
def raw_of(alerts: list[dict]) -> list[dict]:
    """Alert dicts -> baseline entry dicts (same projection as build_sites)."""
    out = []
    for a in alerts:
        mri = a["most_recent_instance"]
        loc = mri["location"]
        out.append(
            {
                "path": loc["path"],
                "rule_id": a["rule"]["id"],
                "line": loc["start_line"],
                "number": a["number"],
                "severity": a["rule"]["security_severity_level"],
                "message_head": mri["message"]["text"],
            }
        )
    return out


def cb_build(entry: dict) -> dict:
    return entry


class CommittedBaseline(unittest.TestCase):
    def test_exists_and_verifies(self):
        self.assertTrue(BASELINE.exists(), "bin/codeql-baseline.json must be committed")
        r = subprocess.run(
            [sys.executable, str(TOOL), "verify", "--baseline", str(BASELINE)],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_count_matches_reality_claim(self):
        doc = json.loads(BASELINE.read_text())
        self.assertGreater(doc["alert_count"], 0)
        self.assertEqual(doc["alert_count"], len(doc["alerts"]))
        self.assertEqual(doc["ref"], "main")

    def test_baseline_is_not_filtered(self):
        """No-exclusions pin: the snapshot must span paths outside any single
        tree, severities beyond one bucket, and duplicate sites stay."""
        doc = json.loads(BASELINE.read_text())
        paths = {e["path"] for e in doc["alerts"]}
        self.assertGreater(len(paths), 10, "a real main snapshot spans many directories")
        sevs = {e["severity"] for e in doc["alerts"]}
        self.assertGreater(len(sevs), 1, "severity filter detected")
        tops = {p.split("/")[0] for p in paths}
        self.assertGreater(len(tops), 1, "directory filter detected")

    def test_provenance_recorded(self):
        doc = json.loads(BASELINE.read_text())
        self.assertTrue(doc.get("commit_sha"))
        self.assertTrue(doc.get("captured_at"))


class Wiring(unittest.TestCase):
    def test_workflow_contains_baseline_diff_step(self):
        text = WORKFLOW.read_text()
        self.assertIn("codeql_baseline.py", text)
        self.assertIn("diff", text)

    def test_diff_step_is_report_only(self):
        text = WORKFLOW.read_text()
        self.assertIn("RULING-6: report-only", text)

    def test_tools_share_fetch_semantics(self):
        tool_src = TOOL.read_text()
        self.assertIn("import classify_codeql_alerts as cc", tool_src)
        self.assertIn("cc.normalize_ref", tool_src)
        # The raw fetch goes through the same gh URL shape the classifier uses.
        self.assertIn("code-scanning/alerts", tool_src)
        self.assertIn("state=open", tool_src)


if __name__ == "__main__":
    unittest.main()
