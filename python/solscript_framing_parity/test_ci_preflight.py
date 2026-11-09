"""Regression tests for the CI toolchain preflight in framing-parity-guard.yml.

The workflow asserts a TypeScript floor and must fail closed when the compiler
reports no usable version. Those are shell conditions embedded in YAML, which
``wf_lint`` can only lint, never execute — so this module extracts the step body
from the workflow and runs it under ``bash -e`` (the shell GitHub Actions uses
for a ``run:`` block) against stubbed tool binaries.

Two behaviours are pinned here because both regressed silently once already:
the 4.9 floor must reject 4.0-4.8 while accepting 4.10, and an unreadable
version must fail the step rather than fall through to a pass.

Executing the step needs a POSIX shell. Where ``bash`` is absent the module
skips with that reason instead of erroring, so a Windows checkout without Git
Bash reports "not applicable here" rather than a broken suite.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

import pytest

import sys
_GUARD_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_GUARD_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_GUARD_REPO_ROOT))

from host_tool_guard import missing_tools, skip_reason  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "framing-parity-guard.yml"
STEP_NAME = "- name: Verify the guard toolchain"

#: Versions the guard was proven against, plus the prerelease form `tsc` emits
#: for nightlies. All must pass the preflight.
SUPPORTED = ["4.9.0", "4.9.5", "4.10.1", "5.8.3", "5.0.0-dev", "7.0.2"]

#: Below the documented floor: must be rejected with the floor message.
TOO_OLD = ["4.0.5", "4.8.4", "4.8.9"]

#: Nothing a version comparison can work with. Must be rejected explicitly —
#: bare numeric tests on these error out, and bash reads an erroring test as
#: false, which would let the step pass without checking anything.
UNUSABLE = ["", "garbage", "4", "4a.9.5", "v4.9.5"]

def missing_bash_reason() -> Optional[str]:
    """Why this module cannot run here, or ``None`` when ``bash`` is present.

    The workflow's ``run:`` block is shell, so there is nothing to execute
    without one. Reporting the reason keeps a machine-specific gap visible in
    the pytest summary instead of surfacing as a traceback.
    """
    missing = missing_tools("bash")
    if not missing:
        return None
    return skip_reason(
        missing,
        "the workflow's run: block is shell and cannot be executed here "
        "(install bash or run on a POSIX host)",
    )


_SKIP_REASON = missing_bash_reason()

pytestmark = pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")


STUB_BODIES: Dict[str, str] = {
    "tsc": '#!/bin/sh\nprintf "Version %s\\n" "$FAKE_TSC_VERSION"\n',
    "psql": '#!/bin/sh\necho "psql (PostgreSQL) 17.0"\n',
    "node": '#!/bin/sh\necho "v22.0.0"\n',
    "pg_isready": '#!/bin/sh\nexit 0\n',
}


def extract_preflight_step() -> str:
    """The ``run:`` body of the toolchain step, dedented to column zero."""
    if not WORKFLOW.is_file():  # pragma: no cover - wrong checkout layout
        pytest.fail(f"workflow not found: {WORKFLOW}")
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == STEP_NAME]
    if not starts:  # pragma: no cover - guards against a rename
        pytest.fail(f"step {STEP_NAME!r} not found in {WORKFLOW.name}")

    start = starts[0]
    runs = [i for i in range(start, len(lines)) if lines[i].strip() == "run: |"]
    if not runs:  # pragma: no cover - guards against a restructure
        pytest.fail(f"step {STEP_NAME!r} has no literal run block")

    body: List[str] = []
    for line in lines[runs[0] + 1 :]:
        if line.strip() and not line.startswith(" " * 10):
            break
        body.append(line[10:])

    script = "\n".join(body) + "\n"
    # Extraction must not silently truncate: the last line is the PG probe.
    assert "pg_isready" in script, "extraction lost the tail of the step"
    assert "tsc --version" in script, "extraction lost the version probe"
    return script


def run_preflight(version: str, tmp_path: Path) -> "subprocess.CompletedProcess[str]":
    """Run the real step body with stubbed tools reporting ``version``."""
    stub_bin = tmp_path / "bin"
    stub_bin.mkdir(parents=True, exist_ok=True)
    for name, body in STUB_BODIES.items():
        stub = stub_bin / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(0o755)

    script = tmp_path / "preflight.sh"
    script.write_text(extract_preflight_step(), encoding="utf-8")

    env: Dict[str, str] = dict(os.environ)
    env["PATH"] = f"{stub_bin}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_TSC_VERSION"] = version

    return subprocess.run(
        ["bash", "-e", str(script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def test_missing_bash_is_reported_as_a_skip_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """No bash must yield a reason, so the module skips instead of erroring."""
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    reason = missing_bash_reason()

    assert reason is not None
    assert "bash" in reason
    assert "install" in reason.lower()


def test_present_bash_yields_no_skip_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/bash")

    assert missing_bash_reason() is None


@pytest.mark.parametrize("version", SUPPORTED)
def test_supported_compiler_passes_the_preflight(version: str, tmp_path: Path) -> None:
    result = run_preflight(version, tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "::error::" not in result.stdout
    assert f"tsc {version}" in result.stdout


@pytest.mark.parametrize("version", TOO_OLD)
def test_compiler_below_the_floor_is_rejected(version: str, tmp_path: Path) -> None:
    """4.0-4.8 must fail: the floor is 4.9, not "any 4.x"."""
    result = run_preflight(version, tmp_path)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "older than the guard supports (need >= 4.9)" in result.stdout


@pytest.mark.parametrize("version", UNUSABLE, ids=["empty", "garbage", "bare-major", "non-numeric-major", "v-prefixed"])
def test_unusable_version_fails_closed(version: str, tmp_path: Path) -> None:
    """No parsable version must exit 1, not fall through to a pass.

    Regression guard for #928: with an empty version the numeric floor tests
    errored, bash treated that as false, and the step returned 0.
    """
    result = run_preflight(version, tmp_path)

    assert result.returncode == 1, (
        f"preflight exited 0 for version {version!r} — fail-open regression\n"
        + result.stdout
    )
    assert "did not report a usable version" in result.stdout
