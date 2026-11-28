#!/usr/bin/env python3
"""Hermetic tests for the vanadium runner watchdog's systemd units.

No systemd, no network: the unit files are parsed as text and the claims they
make are checked against the repo. Dual-runnable:

    python3 bin/tests/test_vanadium_runner_watchdog_units.py
    pytest bin/tests/test_vanadium_runner_watchdog_units.py

Why text-level tests for host units: this repo has paid for the alternative
(bin/tests/test_watchdog_units.py header). The properties that catch a unit
drifting from its script are static, so they are pinned here:

  - ExecStart names a script that exists in the repo under the unit's own name
  - every path the unit touches is the same checkout, so the unit cannot run a
    script from one tree while claiming to run another
  - the tick probes (--check) and never drills (--dry-run would alert nothing
    while looking green)
  - the tick names a delivery channel: a run with none refuses (exit 2) rather
    than inheriting a forum fallback and posting a test as a real outage
  - exit 0 and exit 1 are findings, but exit 2 (probe failed) must still fail
    the unit: a watchdog that cannot see must not look quiet
  - the timer fires every two minutes, points at the service, and does not
    catch up on a missed tick (meaningless for an "is it online now" probe)
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent
REPO = BIN.parent
SERVICE = BIN / "vanadium-runner-watchdog.service"
TIMER = BIN / "vanadium-runner-watchdog.timer"
SCRIPT_NAME = "vanadium_runner_watchdog.py"

# The primary checkout, per the checkout doctrine in the unit headers.
CHECKOUT = "/home/codex/dev/nexus"


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


def _execs() -> list[str]:
    return _directives(SERVICE)["ExecStart"]


def test_units_exist_and_share_a_stem():
    assert SERVICE.is_file() and TIMER.is_file()
    assert SERVICE.stem == TIMER.stem == "vanadium-runner-watchdog"


def test_execstart_names_the_repo_script():
    """The unit's script and the repo's script must not drift apart."""
    execs = _execs()
    assert len(execs) == 1, execs
    argv = execs[0].split()
    assert argv[1] == f"{CHECKOUT}/bin/{SCRIPT_NAME}", argv[1]
    assert (BIN / SCRIPT_NAME).is_file(), "unit points at a script the repo does not have"


def test_every_path_is_the_same_checkout():
    d = _directives(SERVICE)
    assert d["WorkingDirectory"] == [CHECKOUT], d.get("WorkingDirectory")
    text = SERVICE.read_text(encoding="utf-8")
    paths = [p.rstrip(".,:;") for p in re.findall(r"/home/\S+", text)]
    assert paths, text
    for path in paths:
        # Every reference is the checkout itself or something inside it (the
        # header names the checkout when telling an installer which path to
        # edit if their clone lives elsewhere).
        assert path == CHECKOUT or path.startswith(CHECKOUT + "/"), path
    doc = d["Documentation"][0]
    assert doc.startswith("file:" + CHECKOUT + "/"), doc
    assert (REPO / doc[len("file:" + CHECKOUT + "/"):]).is_file(), doc


def test_tick_probes_instead_of_drilling():
    """A --dry-run unit would write no state and alert nobody while looking green."""
    argv = _execs()[0].split()
    assert "--check" in argv, argv
    assert "--dry-run" not in argv, argv
    assert "--json" not in argv, argv


def test_tick_names_its_delivery_channel():
    """No channel means exit 2, so the timer has to name one explicitly."""
    argv = _execs()[0].split()
    assert {"--alert-cmd", "--webhook-url", "--forum"} & set(argv), argv


def test_down_is_a_finding_but_blindness_is_a_failure():
    """exit 0/1 succeed (findings land in the journal); exit 2 must fail the unit."""
    d = _directives(SERVICE)
    assert "SuccessExitStatus" in d, "exit 1 would mark every outage tick failed"
    codes = {c.strip() for entry in d["SuccessExitStatus"] for c in entry.split()}
    assert codes == {"0", "1"}, codes


def test_service_is_oneshot_with_a_bounded_timeout():
    d = _directives(SERVICE)
    assert d["Type"] == ["oneshot"], d.get("Type")
    assert d["TimeoutStartSec"] == ["120"], d.get("TimeoutStartSec")
    # Timer-activated: the service must not install itself as a boot daemon.
    assert "Install" not in d, d.get("Install")


def test_script_still_accepts_the_units_flags():
    help_text = subprocess.run(
        [sys.executable, str(BIN / SCRIPT_NAME), "--help"],
        capture_output=True, text=True, timeout=60,
    )
    assert help_text.returncode == 0, help_text.stderr
    assert "--check" in help_text.stdout


def test_timer_is_two_minutely_and_wired_to_the_service():
    d = _directives(TIMER)
    assert d["OnBootSec"] == ["2min"], d.get("OnBootSec")
    assert d["OnUnitActiveSec"] == ["2min"], d.get("OnUnitActiveSec")
    assert d["Unit"] == ["vanadium-runner-watchdog.service"], d.get("Unit")
    assert d["WantedBy"] == ["timers.target"], d.get("WantedBy")
    # A catch-up run adds nothing to an "is it online right now" probe.
    assert "Persistent" not in d, d.get("Persistent")


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
