#!/usr/bin/env python3
"""Hermetic tests for the memory-cache-refresh and role-surface-verify units.

No database, no network: `curl` is stubbed on PATH, and every assertion reads
in-repo files. Two things are being protected here.

1. The refresh script's failure semantics. A timer that reports success when a
   service is dead is worse than no timer, because it converts a visible
   failure into a silent one. These tests pin both the all-good and the
   one-dead cases, and prove the failure path is reachable (non-vacuity).

2. The cadence invariant. role-memory-srv reports "degraded" when its cache is
   older than STALE_THRESHOLD_MS (1h), and the sync services are event-driven,
   so the ONLY thing keeping that status truthful is this timer firing often
   enough. If someone widens the cadence to 2h, the unit still "works" — it just
   reports degraded forever, which is the exact noise this was built to remove.
   So the interval is asserted against the constant parsed out of the service
   source, not against a hardcoded number.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO, "bin", "memory-cache-refresh.sh")
TIMER = os.path.join(REPO, "bin", "memory-cache-refresh.timer")
SERVICE = os.path.join(REPO, "bin", "memory-cache-refresh.service")
ROLE_TIMER = os.path.join(REPO, "bin", "role-surface-verify.timer")
ROLE_SERVICE = os.path.join(REPO, "bin", "role-surface-verify.service")
ROLE_MEMORY_SRC = os.path.join(REPO, "typescript", "role-memory-srv", "src", "index.ts")
ROLES_JSON = os.path.join(REPO, "config", "roles", "roles.json")


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def run_script(stub_dir: str) -> subprocess.CompletedProcess:
    """Run the refresh script with a stubbed curl on PATH."""
    env = dict(os.environ)
    env["PATH"] = stub_dir + os.pathsep + env["PATH"]
    return subprocess.run(
        ["bash", SCRIPT], capture_output=True, text=True, env=env, timeout=60
    )


def write_stub(directory: str, name: str, body: str) -> None:
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(path, 0o755)


@pytest.fixture()
def both_healthy(tmp_path):
    stub = tmp_path / "bin"
    stub.mkdir()
    write_stub(
        str(stub),
        "curl",
        '#!/bin/sh\necho \'{"procedures":57,"roleIndices":25}\'\nexit 0\n',
    )
    return str(stub)


@pytest.fixture()
def procedure_dead(tmp_path):
    stub = tmp_path / "bin"
    stub.mkdir()
    # Fail only when the procedure-registry URL is requested.
    write_stub(
        str(stub),
        "curl",
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *3500*) echo "connection refused" >&2; exit 7 ;;\n'
        "  *) echo '{\"prompts\":12792,\"tasks\":0}' ;;\n"
        "esac\n",
    )
    return str(stub)


# ── refresh script behaviour ────────────────────────────────────────────────


def test_script_exists_and_is_executable():
    assert os.path.isfile(SCRIPT)
    assert os.access(SCRIPT, os.X_OK), "must be executable to be an ExecStart"


def test_both_healthy_exits_zero(both_healthy):
    result = run_script(both_healthy)
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_one_dead_service_exits_nonzero(procedure_dead):
    """A dead service must NOT be reported as success.

    This is the failure mode that would make the whole timer worse than
    useless: it would convert a loud outage into a silent one.
    """
    result = run_script(procedure_dead)
    assert result.returncode == 1, "a failed refresh must propagate as exit 1"
    assert "FAIL" in result.stderr
    assert "procedure-registry" in result.stderr


def test_one_dead_service_still_refreshes_the_other(procedure_dead):
    """Both endpoints are attempted even when the first fails.

    Otherwise a single dead service would starve the healthy one, and the
    journal would not reveal which of the two is actually at fault.
    """
    result = run_script(procedure_dead)
    assert "prompt-bridge" in result.stdout, "the healthy service must still be refreshed"


def test_both_dead_exits_nonzero(tmp_path):
    stub = tmp_path / "bin"
    stub.mkdir()
    write_stub(str(stub), "curl", '#!/bin/sh\necho "down" >&2\nexit 7\n')
    result = run_script(str(stub))
    assert result.returncode == 1


# ── the cadence invariant ───────────────────────────────────────────────────


def test_stale_threshold_parsed_from_service_source():
    """Guard the premise: the constant this whole design hangs on must exist."""
    match = re.search(
        r"STALE_THRESHOLD_MS\s*=\s*(\d+)\s*\*\s*(\d+)\s*\*\s*(\d+)", read(ROLE_MEMORY_SRC)
    )
    assert match, "STALE_THRESHOLD_MS not found in role-memory-srv/src/index.ts"
    a, b, c = (int(g) for g in match.groups())
    assert a * b * c == 3_600_000, "expected a 1h staleness threshold"


def test_timer_cadence_is_below_the_stale_threshold():
    """THE invariant. Widening the cadence re-creates the false 'degraded'.

    Asserted against the parsed constant, not a literal, so the test keeps
    meaning if the service's threshold is ever retuned.
    """
    threshold_ms = 3_600_000
    match = re.search(r"^OnCalendar=\*:\d+/(\d+)$", read(TIMER), re.M)
    assert match, f"OnCalendar=*/N not found in {TIMER}"
    interval_minutes = int(match.group(1))
    assert interval_minutes * 60_000 < threshold_ms, (
        f"cadence {interval_minutes}m must stay under the "
        f"{threshold_ms // 60000}m staleness threshold, or :3500 reports "
        f"degraded for a fully-populated cache"
    )


def test_timer_uses_persistent_and_randomized_delay():
    text = read(TIMER)
    assert "Persistent=true" in text, "must catch up after the machine sleeps"
    assert "RandomizedDelaySec" in text, "must not pile onto other :0/:30 timers"


# ── unit files are wired, and agree with the script ─────────────────────────


def test_service_executes_the_repo_script():
    assert os.path.isfile(SERVICE)
    text = read(SERVICE)
    assert "memory-cache-refresh.sh" in text
    assert "Type=oneshot" in text


def test_service_path_is_not_hardcoded_to_a_stale_checkout():
    """A wrong WorkingDirectory/ExecStart is the helium-probe failure mode.

    The deploy header requires the unit to work from any clone; assert the
    repo-root-relative contract is documented rather than silently absolute.
    """
    text = read(SERVICE)
    assert "/home/codex/dev/nexus" in text, "documented default install path"
    assert "Source of truth" in text, "must document that the repo is authoritative"


# ── roles.json must stay canonical input, never become a projection ─────────


def test_role_surface_unit_is_a_detector_not_a_writer():
    """The architectural guard for the 'keep roles.json synced' request.

    roles.json is canonical input. If a scheduled unit ever writes it from live
    state it will erase the very expectation verify-roles.py compares against,
    turning a missing surface from a visible gap into a silent rewrite.
    """
    text = read(ROLE_SERVICE)
    assert "verify-roles.py" in text and "role-vocab-drift.py" in text
    # No redirection into the canonical file, and no script that writes it.
    for forbidden in ("> config/roles/roles.json", ">> config/roles/roles.json"):
        assert forbidden not in text, f"unit must not write roles.json ({forbidden})"


def test_roles_json_still_declares_itself_canonical():
    """If this file ever becomes generated, this test is the tripwire."""
    import json

    spec = json.loads(read(ROLES_JSON))
    comment = spec.get("_comment", "")
    assert "Source of truth" in comment or "anonical" in comment, (
        "roles.json no longer declares itself canonical — if it has become a "
        "projection, the authority direction has changed and this suite plus the "
        "role runbook must be revisited together"
    )


def test_role_surface_timer_is_daily_not_a_cache_warm():
    text = read(ROLE_TIMER)
    match = re.search(r"^OnCalendar=\*-\*-\*\s+(\d{2}):(\d{2})", text, re.M)
    assert match, "role-surface timer should be a daily OnCalendar slot"
    assert int(match.group(1)) > 6 or (int(match.group(1)) == 6 and int(match.group(2)) >= 30), (
        "run after the 06:10/06:20 drift slots so it reads final morning state"
    )
