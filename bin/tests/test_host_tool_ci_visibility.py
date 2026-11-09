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

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFTEST = REPO_ROOT / "conftest.py"
CORE_CONFTEST = REPO_ROOT / "python" / "nexus_core" / "conftest.py"

MARKED_SKIP = (
    'pytest.skip("missing host tools for this suite: bash (test fixture)")'
)
OTHER_SKIP = 'pytest.skip("unrelated: live service not reachable")'

pytestmark = pytest.mark.skipif(
    not CONFTEST.is_file(),
    reason="repository conftest.py not present in this checkout",
)


def _session(tmp_path: Path, body: str, ci: bool) -> subprocess.CompletedProcess:
    """Run one sample test file under the real conftest."""
    (tmp_path / "conftest.py").write_text(
        CONFTEST.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "test_sample.py").write_text(body, encoding="utf-8")

    env = {k: v for k, v in os.environ.items() if k != "CI"}
    if ci:
        env["CI"] = "true"

    return subprocess.run(
        [sys.executable, "-m", "pytest", "test_sample.py", "-q", "-p", "no:cacheprovider"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def _sample(skip_call: str) -> str:
    return f"import pytest\n\n\ndef test_one():\n    {skip_call}\n"


def test_marked_skip_fails_the_session_on_ci(tmp_path: Path) -> None:
    result = _session(tmp_path, _sample(MARKED_SKIP), ci=True)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "host tools missing on CI" in result.stdout
    assert "missing host tools for this suite: bash" in result.stdout
    assert "install them instead of letting the suite skip" in result.stdout


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


def test_nexus_core_bridge_exposes_the_hooks() -> None:
    """python/nexus_core pins rootdir, so it must load the hook itself."""
    spec = importlib.util.spec_from_file_location("core_conftest_under_test", CORE_CONFTEST)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    for hook in ("pytest_sessionstart", "pytest_runtest_logreport", "pytest_sessionfinish"):
        assert callable(getattr(module, hook)), hook
