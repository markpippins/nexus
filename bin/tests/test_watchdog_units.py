#!/usr/bin/env python3
"""Hermetic tests for the inbox-pointer watchdog's systemd units.

No systemd, no network: the unit files are parsed as text and the claims they
make are checked against the repo. Dual-runnable:

    python3 bin/tests/test_watchdog_units.py
    pytest bin/tests/test_watchdog_units.py

Why text-level tests for a host unit: this repo has already paid for the
alternative. A unit pointed at the primary checkout ran whatever branch
happened to be checked out, and the comment claiming it "runs the gate from
main" could not fail (attestation-janitor.service header; 329 duplicate
threads over three days). The properties that would have caught that are all
static, so they can be pinned here rather than discovered in the journal:

  - ExecStart names a script that exists in the repo at the same name as the
    unit, so the unit cannot outlive a rename of the script
  - every path the unit touches is the SAME dedicated deploy checkout, so a
    fetch/reset cannot harden one tree while another one runs
  - the tick runs from origin/main rather than a mutable working tree
  - a flagged backlog (exit 1) is a finding, not a unit failure — but a failed
    lookup (exit 2) must still fail the unit, because a watchdog that cannot
    see must not look quiet
  - the timer is persistent and points at the service unit
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent
REPO = BIN.parent
SERVICE = BIN / "inbox-pointer-watchdog.service"
TIMER = BIN / "inbox-pointer-watchdog.timer"
SCRIPT_NAME = "inbox_pointer_watchdog.py"

# The dedicated deploy checkout. Written by nothing else, so reset --hard is
# safe there; the primary checkout is mutable and must never be the target.
DEPLOY_ROOT = "/home/codex/dev/nexus-watchdog"


def _directives(path: Path) -> dict[str, list[str]]:
    """Parse KEY=VALUE directives, ignoring comments and section headers."""
    out: dict[str, list[str]] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        key, _, value = line.partition("=")
        out.setdefault(key.strip(), []).append(value.strip())
    return out


def _exec_paths():
    return _directives(SERVICE)["ExecStart"]


def test_units_exist_and_share_a_stem():
    assert SERVICE.is_file() and TIMER.is_file()
    assert SERVICE.stem == TIMER.stem == "inbox-pointer-watchdog"


def test_execstart_names_the_repo_script():
    """The unit's script and the repo's script must not drift apart."""
    execs = _exec_paths()
    assert len(execs) == 1, execs
    argv = execs[0].split()
    script = argv[1]
    assert script == f"{DEPLOY_ROOT}/bin/{SCRIPT_NAME}", script
    # The same relative name must exist in the corpus this unit claims to run.
    assert (BIN / SCRIPT_NAME).is_file(), "unit points at a script the repo does not have"


def test_single_dedicated_checkout_everywhere():
    """WorkingDirectory, both ExecStartPre resets and ExecStart agree."""
    d = _directives(SERVICE)
    assert d["WorkingDirectory"] == [DEPLOY_ROOT], d.get("WorkingDirectory")
    pres = d.get("ExecStartPre", [])
    assert len(pres) == 2, pres
    for pre in pres:
        assert f"-C {DEPLOY_ROOT}" in pre, pre
        assert DEPLOY_ROOT in pre, pre
    assert _exec_paths()[0].split()[1].startswith(DEPLOY_ROOT + "/")
    # A checkout under the primary tree would reintroduce the drift bug.
    assert "/home/codex/dev/nexus/" not in SERVICE.read_text(encoding="utf-8")


def test_tick_hardens_to_origin_main_before_running():
    pres = " ".join(_directives(SERVICE).get("ExecStartPre", []))
    assert re.search(r"\bfetch\b.*\borigin\b.*\bmain\b", pres), pres
    assert re.search(r"\breset\s+--hard\b.*\borigin/main\b", pres), pres


def test_missing_script_skips_instead_of_failing():
    cond = _directives(SERVICE).get("ExecCondition", [])
    assert len(cond) == 1, cond
    assert cond[0].startswith("/usr/bin/test -f "), cond[0]
    assert cond[0].endswith(f"/bin/{SCRIPT_NAME}"), cond[0]


def test_flagged_backlog_is_not_a_unit_failure_but_blindness_is():
    """exit 1 (mail waiting) is a finding; exit 2 (lookup failed) is a failure."""
    d = _directives(SERVICE)
    assert "SuccessExitStatus" in d, "exit 1 would mark every backlogged tick failed"
    codes = {c.strip() for entry in d["SuccessExitStatus"] for c in entry.split()}
    assert codes == {"1"}, codes


def test_service_is_oneshot_with_a_bounded_timeout():
    d = _directives(SERVICE)
    assert d["Type"] == ["oneshot"], d.get("Type")
    assert d["TimeoutStartSec"] == ["180"], d.get("TimeoutStartSec")
    assert d["WantedBy"] == ["default.target"], d.get("WantedBy")


def test_max_age_flag_is_accepted_by_the_script():
    """The unit passes --max-age-days; the script must still accept it."""
    flag = re.search(r"--max-age-days\s+(\d+)", _exec_paths()[0])
    assert flag, "unit no longer pins the staleness threshold"
    assert int(flag.group(1)) > 0
    help_text = subprocess.run(
        [sys.executable, str(BIN / SCRIPT_NAME), "--help"],
        capture_output=True, text=True, timeout=60,
    )
    assert help_text.returncode == 0, help_text.stderr
    assert "--max-age-days" in help_text.stdout


def test_timer_is_hourly_persistent_and_wired_to_the_service():
    d = _directives(TIMER)
    assert d["OnCalendar"] == ["*-*-* *:07:00 UTC"], d.get("OnCalendar")
    assert d["Persistent"] == ["true"], d.get("Persistent")
    assert d["Unit"] == ["inbox-pointer-watchdog.service"], d.get("Unit")
    assert d["WantedBy"] == ["timers.target"], d.get("WantedBy")


def test_headers_document_deployment():
    """Host-only copies drift; the deploy commands must stay in the corpus."""
    for path in (SERVICE, TIMER):
        text = path.read_text(encoding="utf-8")
        assert "~/.config/systemd/user/" in text, path.name
        assert "daemon-reload" in text or "systemctl --user" in text, path.name


def _run_all():
    failures = []
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failures.append((name, exc))
    for name, exc in failures:
        print(f"FAIL {name}: {exc}")
    print(f"{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
