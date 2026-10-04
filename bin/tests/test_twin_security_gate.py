"""Hermetic tests for the twin CodeQL security gate (queue items 4-6).

Covers tools/security/classify_twin_alerts.py (copied/novel/unclassified
classification + ledger cross-check), tools/security/backfill-ledger.yaml
(schema + dedup key), tools/security/codeql-baseline.json (dated snapshot
shape), and .github/workflows/twin-security-gate.yml (required gate steps).
No network access: every test builds its own fixture world under tmp_path.
"""

import datetime as dt
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
_SEC = str(REPO / "tools" / "security")
if _SEC not in sys.path:
    sys.path.insert(0, _SEC)

import classify_twin_alerts as clf  # noqa: E402
import capture_baseline as cap  # noqa: E402

WORKFLOW = REPO / ".github/workflows/twin-security-gate.yml"
BASELINE = REPO / "tools/security/codeql-baseline.json"
LEDGER = REPO / "tools/security/backfill-ledger.yaml"


# ---------------------------------------------------------------------------
# Fixture world


def make_world(tmp_path: Path, ledger_entries: list[dict] | None = None) -> Path:
    """Minimal repo root: ports.yaml with two canary rows (+ optional ledger)."""
    (tmp_path / "moleculer").mkdir()
    (tmp_path / "moleculer" / "ports.yaml").write_text(
        "canary:\n"
        "  - port: 4115\n"
        "    name: substance\n"
        "    incumbent: typescript/substance-srv\n"
        "    incumbent_port: 3115\n"
        "  - port: 4100\n"
        "    name: kernel\n"
        "    incumbent: typescript/kernel-srv\n"
        "    incumbent_port: 8100\n"
    )
    if ledger_entries is not None:
        sec = tmp_path / "tools" / "security"
        sec.mkdir(parents=True)
        (sec / "backfill-ledger.yaml").write_text(
            yaml.safe_dump({"entries": ledger_entries})
        )
    return tmp_path


def alert(number: int, rule: str, path: str) -> dict:
    return {
        "number": number,
        "rule": {"id": rule},
        "most_recent_instance": {"location": {"path": path}},
    }


SUBSTANCE_COPY_ALERT = alert(
    780, "js/cors-permissive-configuration",
    "moleculer/substance/services/express-app.ts",
)
SUBSTANCE_INCUMBENT_ALERT = alert(
    774, "js/cors-permissive-configuration",
    "typescript/substance-srv/src/index.ts",
)


def write_alerts(tmp_path: Path, alerts: list[dict]) -> Path:
    p = tmp_path / "alerts.json"
    p.write_text(json.dumps(alerts))
    return p


LEDGER_ENTRY = {
    "service": "typescript/substance-srv",
    "rule_id": "js/cors-permissive-configuration",
    "incumbent_path": "typescript/substance-srv",
    "twin_path": "moleculer/substance/services/express-app.ts",
    "status": "backfill-planned",
    "opened": "2026-10-01",
    "owner": "engineer-lane",
    "review_by": "2026-11-01",
}


# ---------------------------------------------------------------------------
# Classifier


def test_seeded_substance_copy_classifies_copied(tmp_path):
    root = make_world(tmp_path, [LEDGER_ENTRY])
    alerts = [SUBSTANCE_COPY_ALERT, SUBSTANCE_INCUMBENT_ALERT]
    report = clf.classify_alerts(alerts, clf.load_twin_map(root), clf.load_ledger(root))
    f = report["findings"][0]
    assert f["classification"] == "copied"
    assert f["incumbent"] == "typescript/substance-srv"
    assert f["ledger_entry_present"] is True
    # the incumbent's own alert is on a non-twin path → unclassified, not copied
    assert report["counts"] == {"copied": 1, "unclassified": 1}


def test_copy_without_incumbent_alert_is_novel(tmp_path):
    root = make_world(tmp_path, [LEDGER_ENTRY])
    alerts = [alert(1, "js/xss", "moleculer/substance/services/express-app.ts")]
    report = clf.classify_alerts(alerts, clf.load_twin_map(root))
    assert report["findings"][0]["classification"] == "novel"


def test_novel_rule_on_incumbent_subtree_still_novel_for_twin(tmp_path):
    # an incumbent alert for a DIFFERENT rule must not clear a twin alert
    root = make_world(tmp_path)
    alerts = [
        alert(1, "js/xss", "moleculer/substance/services/express-app.ts"),
        alert(2, "js/ssrf", "typescript/substance-srv/src/index.ts"),
    ]
    report = clf.classify_alerts(alerts, clf.load_twin_map(root))
    assert report["findings"][0]["classification"] == "novel"


def test_non_twin_path_is_unclassified(tmp_path):
    root = make_world(tmp_path)
    alerts = [alert(9, "js/cors-permissive-configuration", "typescript/web/src/app.ts")]
    report = clf.classify_alerts(alerts, clf.load_twin_map(root))
    assert report["findings"][0]["classification"] == "unclassified"


def test_missing_twin_directory_name_is_unclassified(tmp_path):
    # moleculer/<app> present but <app> not in the registry
    root = make_world(tmp_path)
    alerts = [alert(3, "js/xss", "moleculer/ghost-town/src/app.ts")]
    report = clf.classify_alerts(alerts, clf.load_twin_map(root))
    assert report["findings"][0]["classification"] == "unclassified"


# ---------------------------------------------------------------------------
# CLI exit codes


def test_copied_without_ledger_entry_is_hard_failure(tmp_path, capsys):
    root = make_world(tmp_path, [])  # ledger exists but EMPTY
    alerts_file = write_alerts(tmp_path, [SUBSTANCE_COPY_ALERT, SUBSTANCE_INCUMBENT_ALERT])
    code = clf.main(
        ["--alerts", str(alerts_file), "--root", str(root), "--ledger", "--fail-on", "novel"]
    )
    assert code == clf.EXIT_LEDGER
    assert "COPIED WITHOUT LEDGER ENTRY" in capsys.readouterr().err


def test_copied_with_ledger_entry_passes_when_fail_on_novel_only(tmp_path, capsys):
    root = make_world(tmp_path, [LEDGER_ENTRY])
    alerts_file = write_alerts(tmp_path, [SUBSTANCE_COPY_ALERT, SUBSTANCE_INCUMBENT_ALERT])
    code = clf.main(
        ["--alerts", str(alerts_file), "--root", str(root), "--ledger", "--fail-on", "novel"]
    )
    assert code == 0
    assert "copied" in capsys.readouterr().out


def test_novel_fails_with_exit_4(tmp_path):
    root = make_world(tmp_path, [LEDGER_ENTRY])
    alerts_file = write_alerts(
        tmp_path, [alert(1, "js/xss", "moleculer/substance/services/express-app.ts")]
    )
    code = clf.main(["--alerts", str(alerts_file), "--root", str(root), "--fail-on", "novel"])
    assert code == clf.EXIT_NOVEL


def test_unclassified_fails_with_exit_5(tmp_path):
    root = make_world(tmp_path)
    alerts_file = write_alerts(tmp_path, [alert(9, "js/xss", "typescript/web/src/app.ts")])
    code = clf.main(
        ["--alerts", str(alerts_file), "--root", str(root), "--fail-on", "unclassified"]
    )
    assert code == clf.EXIT_UNCLASSIFIED


def test_pr_payload_copied_only_still_classifies_copied_via_incumbent_truth(tmp_path):
    # THE decisive case (found in live rehearsal): CodeQL default-setup gives
    # a PR merge ref NEW-alerts-only, so a copied finding's incumbent alert
    # is NOT in the PR payload. The incumbent truth (main's alerts) must be
    # consulted, or copied misclassifies as novel and wrongly blocks.
    root = make_world(tmp_path, [LEDGER_ENTRY])
    pr_payload = [SUBSTANCE_COPY_ALERT]  # no incumbent alert in the PR payload
    report = clf.classify_alerts(
        pr_payload,
        clf.load_twin_map(root),
        clf.load_ledger(root),
        incumbent_alerts=[SUBSTANCE_INCUMBENT_ALERT],
    )
    f = report["findings"][0]
    assert f["classification"] == "copied"
    assert report["counts"] == {"copied": 1}


def test_pr_payload_novel_despite_unrelated_incident_alerts(tmp_path):
    root = make_world(tmp_path)
    pr_payload = [alert(1, "js/xss", "moleculer/substance/services/express-app.ts")]
    report = clf.classify_alerts(
        pr_payload,
        clf.load_twin_map(root),
        incumbent_alerts=[alert(2, "js/ssrf", "typescript/substance-srv/src/index.ts")],
    )
    assert report["findings"][0]["classification"] == "novel"


def test_cli_passes_incumbent_alerts_through(tmp_path, capsys):
    root = make_world(tmp_path, [LEDGER_ENTRY])
    pr_file = write_alerts(tmp_path, [SUBSTANCE_COPY_ALERT])
    inc_file = tmp_path / "main-alerts.json"
    inc_file.write_text(json.dumps([SUBSTANCE_INCUMBENT_ALERT]))
    code = clf.main(
        [
            "--alerts", str(pr_file),
            "--incumbent-alerts", str(inc_file),
            "--root", str(root),
            "--ledger", "--fail-on", "novel",
        ]
    )
    assert code == 0
    assert '"classification": "copied"' in capsys.readouterr().out


def test_classify_only_never_fails(tmp_path):
    root = make_world(tmp_path)
    alerts_file = write_alerts(
        tmp_path, [alert(1, "js/xss", "moleculer/substance/services/express-app.ts")]
    )
    code = clf.main(
        [
            "--alerts", str(alerts_file), "--root", str(root),
            "--fail-on", "novel", "--classify-only",
        ]
    )
    assert code == 0


def test_malformed_payload_is_usage_error(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert clf.main(["--alerts", str(bad), "--root", str(tmp_path)]) == clf.EXIT_USAGE


# ---------------------------------------------------------------------------
# Ledger schema + dedup key (live file in this worktree)


def _ledger_entries() -> list[dict]:
    data = yaml.safe_load(LEDGER.read_text())
    return data.get("entries", [])


ALLOWED_STATUSES = {"pending-entry", "backfill-planned", "backfilled"}


def test_ledger_dedup_key_unique():
    entries = _ledger_entries()
    seen: set[tuple] = set()
    dupes: list[tuple] = []
    for e in entries:
        key = (e.get("service"), e.get("rule_id"), e.get("incumbent_path"))
        if key in seen:
            dupes.append(key)
        seen.add(key)
    assert dupes == []


def test_ledger_entries_have_owner_timebox_and_allowed_status():
    for e in _ledger_entries():
        assert e.get("owner"), e
        assert e.get("status") in ALLOWED_STATUSES, e
        for field in ("opened", "review_by"):
            dt.date.fromisoformat(str(e[field]))  # raises if malformed
        # a time box must actually lie in the future relative to opening
        assert dt.date.fromisoformat(str(e["review_by"])) > dt.date.fromisoformat(
            str(e["opened"])
        ), e


def test_ledger_no_permanent_status_value():
    text = LEDGER.read_text()
    # the ruling forbids "permanent deliberate divergence" as an outcome:
    # no entry may carry a permanent-style status
    assert "status: permanent" not in text
    for e in _ledger_entries():
        assert "permanent" not in str(e.get("status", ""))


# ---------------------------------------------------------------------------
# Baseline snapshot (live file in this worktree)


def test_baseline_snapshot_is_dated_and_wellformed():
    d = json.loads(BASELINE.read_text())
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", d["captured"])
    assert re.fullmatch(r"[0-9a-f]{40}", d["commit"])
    assert d["ref"] == "refs/heads/main"
    assert isinstance(d["alert_count"], int) and d["alert_count"] > 0
    assert d["alert_count"] >= len(d["fingerprints"])
    assert all(isinstance(f, str) and "|" in f for f in d["fingerprints"])


def test_baseline_is_not_a_suppression_file():
    # the snapshot must contain only descriptive fields — no ignore/flag
    # semantics of any kind (ruling 6: dated snapshot for diffing, not a
    # suppression; no exclusions, no threshold bump)
    d = json.loads(BASELINE.read_text())
    assert set(d) == {"captured", "ref", "commit", "alert_count", "fingerprints"}


# ---------------------------------------------------------------------------
# Registry resolution covers every declared twin


def test_every_registry_twin_resolves_to_existing_incumbent():
    twins = clf.load_twin_map(REPO)
    assert len(twins) >= 15  # 15 merged canary twins today
    for name, incumbent in twins.items():
        assert (REPO / incumbent).is_dir(), (name, incumbent)


# ---------------------------------------------------------------------------
# Workflow gate


def test_workflow_parses_and_enforces_all_three_classifications():
    wf = yaml.safe_load(WORKFLOW.read_text())
    steps = wf["jobs"]["classify-twin-alerts"]["steps"]
    blob = json.dumps(steps)
    assert "refs/pull/" in blob and "--paginate" in blob
    assert "classify_twin_alerts.py" in blob
    assert "--ledger" in blob
    assert "--fail-on novel" in blob
    assert "--fail-on unclassified" in blob
    assert "codeql-baseline.json" in blob


def test_workflow_does_not_skip_on_api_failure():
    text = WORKFLOW.read_text()
    assert "set -euo pipefail" in text
    assert "|| true" not in text and "continue-on-error" not in text


def test_capture_baseline_schema_matches_committed_snapshot():
    snap = cap.build_snapshot(
        [SUBSTANCE_COPY_ALERT, SUBSTANCE_INCUMBENT_ALERT],
        ref="refs/heads/main",
        commit="a" * 40,
        now="2026-10-01T00:00:00Z",
    )
    assert snap["alert_count"] == 2
    assert snap["fingerprints"] == [
        "js/cors-permissive-configuration|moleculer/substance/services/express-app.ts",
        "js/cors-permissive-configuration|typescript/substance-srv/src/index.ts",
    ]
