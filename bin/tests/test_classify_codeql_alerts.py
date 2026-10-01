"""Hermetic tests for the Ruling 1/4 CodeQL copied-vs-novel classifier
(bin/classify_codeql_alerts.py) and its ledger
(moleculer/codeql-backfill-ledger.yaml).

Hermetic: no network, no services. The gh CLI is faked with throwaway shell
scripts so the REAL subprocess path (arg parsing, JSON-lines pagination,
state filtering) is exercised end to end. Pins:

  registry     — canary rows load; duplicates/missing fields abort loudly
  resolution   — moleculer/<name>/rest resolves via the port registry;
                 non-twin and unknown-service paths do not
  ledger       — schema: required fields, known status, non-empty owner,
                 repo paths, unique (service, rule_id, incumbent_path)
  classify     — copied+ledgered ok; copied unledgered FAILS; novel FAILS
                 unless an open owned time-boxed entry classifies it;
                 unresolvable twin path FAILS (silence is not classification)
  refs         — main -> refs/heads/main; pr/N -> refs/pull/N/head
  exits        — 0 clean, 1 violations (named), 2 setup/gh errors;
                 --json is machine-pure; the tool NEVER writes files
  wiring       — workflow triggers after default-setup CodeQL with
                 security-events: read; README references the ledger;
                 real-repo ledger entries are consistent with the registry
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
CLASSIFIER = REPO / "bin" / "classify_codeql_alerts.py"
LEDGER = REPO / "moleculer" / "codeql-backfill-ledger.yaml"
WORKFLOW = REPO / ".github" / "workflows" / "codeql-twin-classification.yml"

_spec = importlib.util.spec_from_file_location("classify_codeql_alerts", CLASSIFIER)
cc = importlib.util.module_from_spec(_spec)
# Register before exec: the module's dataclasses resolve their string
# annotations through sys.modules at class-creation time (py3.13).
sys.modules["classify_codeql_alerts"] = cc
_spec.loader.exec_module(cc)


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def make_root(tmp: Path, registry_yaml: str | None = None, ledger_yaml: str | None = None) -> Path:
    """Synthetic repo root with a registry and (optionally) a ledger."""
    root = tmp / "repo"
    root.mkdir(parents=True, exist_ok=True)
    write(
        root / "moleculer" / "ports.yaml",
        registry_yaml
        or (
            "canary:\n"
            "  - port: 4115\n"
            "    name: substance\n"
            "    namespace: substance\n"
            "    incumbent: typescript/substance-srv\n"
            "    incumbent_port: 3115\n"
            "  - port: 4116\n"
            "    name: twin-two\n"
            "    namespace: twin-two\n"
            "    incumbent: typescript/two-srv\n"
            "    incumbent_port: 3116\n"
        ),
    )
    if ledger_yaml is not None:
        write(root / "moleculer" / "codeql-backfill-ledger.yaml", ledger_yaml)
    return root


VALID_LEDGER = (
    "entries:\n"
    "  - service: substance\n"
    "    rule_id: js/cors-permissive-configuration\n"
    "    incumbent_path: typescript/substance-srv/src/index.ts\n"
    "    twin_path: moleculer/substance/services/express-app.ts\n"
    "    status: open\n"
    "    owner: engineer\n"
    "    opened: 2026-10-01\n"
    "    timebox: 2026-11-01\n"
    "    note: copied CORS config; backfill pending\n"
)


def gh_fake(tmp: Path, payload: str, rc: int = 0) -> str:
    """A fake `gh` binary: prints payload (JSON lines), exits rc."""
    p = tmp / f"gh-{abs(hash(payload + str(rc)))}.sh"
    p.write_text(f"#!/usr/bin/env bash\ncat <<'JSONL'\n{payload}\nJSONL\nexit {rc}\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


ALERT_COPIED = {
    "number": 780,
    "state": "open",
    "rule": {"id": "js/cors-permissive-configuration"},
    "most_recent_instance": {"location": {"path": "moleculer/substance/services/express-app.ts", "start_line": 41}},
}


class Registry(unittest.TestCase):
    def test_valid_registry_loads(self):
        with tempfile.TemporaryDirectory() as d:
            reg = cc.load_registry(make_root(Path(d)))
        self.assertEqual(set(reg), {"substance", "twin-two"})
        self.assertEqual(reg["substance"]["incumbent"], "typescript/substance-srv")

    def test_missing_canary_list_aborts(self):
        with tempfile.TemporaryDirectory() as d:
            root = make_root(Path(d), registry_yaml="infra: []\n")
            with self.assertRaises(cc.SetupError):
                cc.load_registry(root)

    def test_duplicate_service_aborts(self):
        dup = (
            "canary:\n"
            "  - port: 4115\n    name: substance\n    incumbent: i\n    incumbent_port: 3115\n"
            "  - port: 4117\n    name: substance\n    incumbent: i\n    incumbent_port: 3117\n"
        )
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(cc.SetupError):
                cc.load_registry(make_root(Path(d), registry_yaml=dup))

    def test_missing_field_aborts(self):
        bad = "canary:\n  - port: 4115\n    name: substance\n"
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(cc.SetupError):
                cc.load_registry(make_root(Path(d), registry_yaml=bad))


class Resolution(unittest.TestCase):
    def setUp(self):
        self.reg = cc.load_registry(REPO)

    def test_twin_path_resolves(self):
        self.assertEqual(
            cc.resolve_twin_path("moleculer/substance/services/express-app.ts", self.reg),
            ("substance", "services/express-app.ts"),
        )

    def test_non_twin_path_is_none(self):
        self.assertIsNone(cc.resolve_twin_path("typescript/substance-srv/src/index.ts", self.reg))

    def test_unknown_service_is_none(self):
        self.assertIsNone(cc.resolve_twin_path("moleculer/not-a-twin/x.js", self.reg))

    def test_longest_name_wins(self):
        reg = {"twin": {}, "twin-two": {}}
        self.assertEqual(
            cc.resolve_twin_path("moleculer/twin-two/svc/x.js", reg),
            ("twin-two", "svc/x.js"),
        )

    def test_incumbent_matches_are_rule_level_within_the_service_pair(self):
        # The registry relates SERVICES, not files: the twin may bundle the
        # incumbent's src/index.ts into services/express-app.ts, so matching
        # must be rule-level across the incumbent service tree.
        open_pairs = {
            ("typescript/substance-srv/src/index.ts", "js/cors-permissive-configuration"),
            ("typescript/substance-srv/src/other.js", "js/cors-permissive-configuration"),
            ("typescript/other-srv/src/index.ts", "js/cors-permissive-configuration"),
        }
        matches = cc.incumbent_matches(
            "substance", "js/cors-permissive-configuration", open_pairs, self.reg
        )
        self.assertEqual(
            matches,
            [
                "typescript/substance-srv/src/index.ts",
                "typescript/substance-srv/src/other.js",
            ],
        )
        self.assertEqual(
            cc.incumbent_matches("substance", "js/absent", open_pairs, self.reg), []
        )


class Ledger(unittest.TestCase):
    def load(self, tmp, yaml_text):
        return cc.load_ledger(make_root(Path(tmp), ledger_yaml=yaml_text))

    def test_valid_ledger_loads(self):
        with tempfile.TemporaryDirectory() as d:
            entries = self.load(Path(d), VALID_LEDGER)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["owner"], "engineer")

    def test_missing_field_aborts(self):
        bad = VALID_LEDGER.replace("    owner: engineer\n", "")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(cc.SetupError):
                self.load(Path(d), bad)

    def test_bad_status_aborts(self):
        bad = VALID_LEDGER.replace("status: open", "status: waived-forever")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(cc.SetupError):
                self.load(Path(d), bad)

    def test_empty_owner_aborts(self):
        bad = VALID_LEDGER.replace("owner: engineer", "owner: \"\"")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(cc.SetupError):
                self.load(Path(d), bad)

    def test_duplicate_key_aborts(self):
        dup = VALID_LEDGER + VALID_LEDGER.split("entries:\n", 1)[1]
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(cc.SetupError):
                self.load(Path(d), dup)

    def test_non_repo_path_aborts(self):
        bad = VALID_LEDGER.replace(
            "incumbent_path: typescript/substance-srv/src/index.ts",
            "incumbent_path: src/index.ts",
        )
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(cc.SetupError):
                self.load(Path(d), bad)

    def test_missing_ledger_file_is_setup_error(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(cc.SetupError):
                cc.load_ledger(make_root(Path(d)))


class Classification(unittest.TestCase):
    def setUp(self):
        self.reg = cc.load_registry(REPO)
        self.entries = cc.load_ledger(REPO)
        self.incumbent_open = {
            ("typescript/substance-srv/src/index.ts", "js/cors-permissive-configuration")
        }

    def alert(self, path, rule="js/cors-permissive-configuration"):
        return cc.Alert(None, path, rule)

    def test_copied_and_ledgered_is_ok(self):
        oks, bad = cc.classify(
            [self.alert("moleculer/substance/services/express-app.ts")],
            self.reg,
            [
                e
                for e in self.entries
                if e["incumbent_path"] == "typescript/substance-srv/src/index.ts"
                and e["rule_id"] == "js/cors-permissive-configuration"
            ],
            self.incumbent_open,
        )
        self.assertEqual((len(oks), len(bad)), (1, 0))
        self.assertEqual(oks[0].outcome, "copied_ledgered")
        self.assertEqual(oks[0].matched_incumbent, "typescript/substance-srv/src/index.ts")

    def test_copied_without_ledger_entry_fails(self):
        oks, bad = cc.classify(
            [self.alert("moleculer/substance/services/express-app.ts")],
            self.reg,
            [],  # no ledger entry
            self.incumbent_open,
        )
        self.assertEqual((len(oks), len(bad)), (0, 1))
        self.assertEqual(bad[0].outcome, "copied_unledgered")

    def test_novel_fails(self):
        oks, bad = cc.classify(
            [self.alert("moleculer/substance/services/express-app.ts", "js/novel-rule")],
            self.reg,
            self.entries,
            self.incumbent_open,
        )
        self.assertEqual((len(oks), len(bad)), (0, 1))
        self.assertEqual(bad[0].outcome, "novel")
        self.assertIn("Ruling 1/4", bad[0].reason)

    def test_novel_with_open_entry_is_classified(self):
        entry = {
            "service": "substance",
            "rule_id": "js/novel-rule",
            "incumbent_path": "typescript/substance-srv/src/index.ts",
            "twin_path": "moleculer/substance/services/express-app.ts",
            "status": "open",
            "owner": "engineer",
            "opened": "2026-10-01",
            "timebox": "2026-10-15",
            "note": "owned time-boxed divergence",
        }
        oks, bad = cc.classify(
            [self.alert("moleculer/substance/services/express-app.ts", "js/novel-rule")],
            self.reg,
            [entry],
            self.incumbent_open,
        )
        self.assertEqual((len(oks), len(bad)), (1, 0))
        self.assertEqual(oks[0].outcome, "novel_ledgered")

    def test_unresolvable_twin_path_fails(self):
        oks, bad = cc.classify(
            [self.alert("moleculer/undeclared-svc/x.js")],
            self.reg,
            self.entries,
            self.incumbent_open,
        )
        self.assertEqual((len(oks), len(bad)), (0, 1))
        self.assertEqual(bad[0].outcome, "unresolved")

    def test_backfilled_entry_does_not_classify_a_firing_novel_alert(self):
        entry = {
            "service": "substance",
            "rule_id": "js/novel-rule",
            "incumbent_path": "typescript/substance-srv/src/index.ts",
            "twin_path": "moleculer/substance/services/express-app.ts",
            "status": "backfilled",
            "owner": "engineer",
            "opened": "2026-10-01",
            "timebox": "2026-10-15",
            "note": "done",
        }
        oks, bad = cc.classify(
            [self.alert("moleculer/substance/services/express-app.ts", "js/novel-rule")],
            self.reg,
            [entry],
            self.incumbent_open,
        )
        self.assertEqual((len(oks), len(bad)), (0, 1))


class Refs(unittest.TestCase):
    def test_branch(self):
        self.assertEqual(cc.normalize_ref("main"), "refs/heads/main")

    def test_pr(self):
        self.assertEqual(cc.normalize_ref("pr/42"), "refs/pull/42/head")

    def test_passthrough(self):
        self.assertEqual(cc.normalize_ref("refs/heads/x"), "refs/heads/x")


class ExitsEndToEnd(unittest.TestCase):
    """main() against a synthetic root with a fake gh binary."""

    def run_main(self, root, gh, extra=()):
        return subprocess.run(
            [sys.executable, str(CLASSIFIER), "--root", str(root), "--gh", gh, *extra],
            capture_output=True,
            text=True,
        )

    def test_clean_classification_exits_zero(self):
        with tempfile.TemporaryDirectory() as d:
            root = make_root(Path(d), ledger_yaml=VALID_LEDGER)
            gh = gh_fake(Path(d), json.dumps([ALERT_COPIED]))
            r = self.run_main(root, gh, ["--json"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout)
        self.assertTrue(out["ok"])
        self.assertEqual(out["ok_count"], 1)

    def test_fixed_alerts_are_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            root = make_root(Path(d), ledger_yaml=VALID_LEDGER)
            fixed = dict(ALERT_COPIED, state="fixed")
            gh = gh_fake(Path(d), json.dumps([fixed]))
            r = self.run_main(root, gh, ["--json"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout)["ok_count"], 0)

    def test_novel_alert_fails_with_named_violation(self):
        with tempfile.TemporaryDirectory() as d:
            root = make_root(Path(d), ledger_yaml=VALID_LEDGER)
            novel = dict(ALERT_COPIED, rule={"id": "js/brand-new"})
            gh = gh_fake(Path(d), json.dumps([novel]))
            r = self.run_main(root, gh)
        self.assertEqual(r.returncode, 1)
        self.assertIn("novel", r.stdout)

    def test_gh_failure_exits_two(self):
        with tempfile.TemporaryDirectory() as d:
            root = make_root(Path(d), ledger_yaml=VALID_LEDGER)
            gh = gh_fake(Path(d), "boom", rc=1)
            r = self.run_main(root, gh)
        self.assertEqual(r.returncode, 2)
        self.assertIn("gh api failed", r.stderr)

    def test_missing_ledger_exits_two(self):
        with tempfile.TemporaryDirectory() as d:
            root = make_root(Path(d))  # no ledger file
            gh = gh_fake(Path(d), json.dumps([ALERT_COPIED]))
            r = self.run_main(root, gh)
        self.assertEqual(r.returncode, 2)
        self.assertIn("backfill ledger not found", r.stderr)

    def test_tool_never_writes_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = make_root(Path(d), ledger_yaml=VALID_LEDGER)
            gh = gh_fake(Path(d), json.dumps([ALERT_COPIED]))
            before = {
                str(p.relative_to(root)): p.read_bytes()
                for p in root.rglob("*")
                if p.is_file()
            }
            r = self.run_main(root, gh)
            after = {
                str(p.relative_to(root)): p.read_bytes()
                for p in root.rglob("*")
                if p.is_file()
            }
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(before, after)

    def test_zero_twin_alerts_is_clean(self):
        with tempfile.TemporaryDirectory() as d:
            root = make_root(Path(d), ledger_yaml=VALID_LEDGER)
            gh = gh_fake(Path(d), json.dumps([{
                "number": 1, "state": "open",
                "rule": {"id": "js/x"},
                "most_recent_instance": {"location": {"path": "typescript/other/x.js"}},
            }]))
            r = self.run_main(root, gh, ["--json"])
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout)["ok_count"], 0)


class Wiring(unittest.TestCase):
    """The gate is only real if it is wired and the repo artifacts agree."""

    def test_workflow_triggers_after_codeql_with_read_scope(self):
        text = WORKFLOW.read_text()
        self.assertIn("workflow_run:", text)
        self.assertIn('workflows: ["CodeQL"]', text)
        self.assertIn("security-events: read", text)
        self.assertIn("classify_codeql_alerts.py", text)
        self.assertIn("workflow_dispatch:", text)

    def test_repo_ledger_is_schema_valid(self):
        entries = cc.load_ledger(REPO)
        self.assertTrue(entries)

    def test_repo_registry_is_loadable(self):
        reg = cc.load_registry(REPO)
        self.assertTrue(reg)

    def test_ledger_entries_consistent_with_registry(self):
        reg = cc.load_registry(REPO)
        for e in cc.load_ledger(REPO):
            svc = str(e["service"])
            self.assertTrue(
                str(e["twin_path"]).startswith(f"moleculer/{svc}/"),
                f"{svc}: twin_path must live under the service directory",
            )
            if svc in reg:
                self.assertTrue(
                    str(e["incumbent_path"]).startswith(reg[svc]["incumbent"] + "/"),
                    f"{svc}: incumbent_path must sit inside the registry incumbent",
                )

    def test_readme_references_the_ledger(self):
        text = (REPO / "moleculer" / "README.md").read_text()
        self.assertIn("codeql-backfill-ledger.yaml", text)

    def test_classifier_imports_pyyaml_only_beyond_stdlib(self):
        src = CLASSIFIER.read_text()
        for banned in ("import requests", "import psycopg2", "import docker"):
            self.assertNotIn(banned, src)


if __name__ == "__main__":
    unittest.main()
