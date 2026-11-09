"""CI must not silently skip suites whose host tool is missing.

The repo-root conftest.py escalates a skip whose reason carries
"missing host tools for this suite:" into a failed session when CI is set.
That behaviour only exists at session level, so these tests drive pytest in a
throwaway directory with the real conftest copied in, instead of reimplementing
the hook's logic.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from host_tool_guard import SKIP_MARKER

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFTEST = REPO_ROOT / "conftest.py"
CORE_CONFTEST = REPO_ROOT / "python" / "nexus_core" / "conftest.py"
HELPER = REPO_ROOT / "host_tool_guard.py"

MARKED_SKIP = f'pytest.skip("{SKIP_MARKER} bash (test fixture)")'
OTHER_SKIP = 'pytest.skip("unrelated: live service not reachable")'

pytestmark = pytest.mark.skipif(
    not CONFTEST.is_file(),
    reason="repository conftest.py not present in this checkout",
)


def _env(ci: bool) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "CI"}
    if ci:
        env["CI"] = "true"
    return env


def _run(directory: Path, target: str, ci: bool) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q", "-p", "no:cacheprovider"],
        cwd=str(directory),
        capture_output=True,
        text=True,
        env=_env(ci),
        timeout=120,
    )


def _sample(skip_call: str) -> str:
    return f"import pytest\n\n\ndef test_one():\n    {skip_call}\n"


def _session(tmp_path: Path, body: str, ci: bool) -> subprocess.CompletedProcess:
    """Run one sample test file under the real conftest."""
    (tmp_path / "conftest.py").write_text(
        CONFTEST.read_text(encoding="utf-8"), encoding="utf-8"
    )
    # The conftest imports the shared guard; the throwaway tree needs it too.
    (tmp_path / HELPER.name).write_text(
        HELPER.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "test_sample.py").write_text(body, encoding="utf-8")
    return _run(tmp_path, "test_sample.py", ci)


def test_marked_skip_fails_the_session_on_ci(tmp_path: Path) -> None:
    result = _session(tmp_path, _sample(MARKED_SKIP), ci=True)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "host tools missing on CI" in result.stdout
    assert "missing host tools for this suite: bash" in result.stdout
    assert "install them instead of letting the suite skip" in result.stdout
    # Pin the rendered tail: the reason's own closing paren, then end of line.
    # A stray quote or an added paren from unwrapping the (file, line, reason)
    # tuple must fail here.
    assert "(test fixture)\n" in result.stdout
    assert "(test fixture))" not in result.stdout
    assert "(test fixture)'" not in result.stdout


def test_marked_skip_stays_a_skip_off_ci(tmp_path: Path) -> None:
    result = _session(tmp_path, _sample(MARKED_SKIP), ci=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 skipped" in result.stdout
    assert "host tools missing on CI" not in result.stdout


def test_unrelated_skip_is_not_escalated_on_ci(tmp_path: Path) -> None:
    result = _session(tmp_path, _sample(OTHER_SKIP), ci=True)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 skipped" in result.stdout
    assert "host tools missing on CI" not in result.stdout


def test_root_and_bridge_together_report_only_once(tmp_path: Path) -> None:
    """A single-rootdir run loads both conftests; the summary must not repeat.

    Layout mirrors the repo: a root conftest plus the nexus_core bridge two
    directories down, which loads the root hook by path (parents[2]).
    """
    root = tmp_path / "repo"
    nested = root / "x" / "y"
    nested.mkdir(parents=True)
    (root / "conftest.py").write_text(
        CONFTEST.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (root / HELPER.name).write_text(
        HELPER.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (nested / "conftest.py").write_text(
        CORE_CONFTEST.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (nested / "test_sample.py").write_text(_sample(MARKED_SKIP), encoding="utf-8")

    result = _run(root, "x/y/test_sample.py", ci=True)

    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.count("host tools missing on CI") == 1
    assert result.stdout.count("missing host tools for this suite: bash") == 1


def test_nexus_core_bridge_exposes_the_hooks() -> None:
    """python/nexus_core pins rootdir, so it must load the hook itself."""
    spec = importlib.util.spec_from_file_location("core_conftest_under_test", CORE_CONFTEST)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    for hook in ("pytest_sessionstart", "pytest_runtest_logreport", "pytest_sessionfinish"):
        assert callable(getattr(module, hook)), hook


def test_the_host_tool_guard_has_a_single_home() -> None:
    """Guarded suites import the shared helper; none may re-grow a copy.

    The duplication this replaced drifted (26 copies of the reason text), and
    a copy re-introduced here would silently stop matching the CI hook.
    """
    offenders = []
    candidates = list((REPO_ROOT / "bin" / "tests").glob("test_*.py"))
    candidates += list((REPO_ROOT / "python").rglob("test_*.py"))
    here = Path(__file__).resolve()
    for path in candidates:
        if path.resolve() == here:
            continue  # this file names the markers in order to police them
        text = path.read_text(encoding="utf-8")
        if "_REQUIRED_TOOLS" in text or "_MISSING_TOOLS" in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []
