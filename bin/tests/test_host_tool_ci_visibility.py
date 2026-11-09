"""CI must not silently skip suites whose host tool is missing.

The repo-root conftest.py escalates a skip whose reason carries the
shared host-tool marker into a failed session when CI is set.
That behaviour only exists at session level, so these tests drive pytest in a
throwaway directory with the real conftest copied in, instead of reimplementing
the hook's logic.
"""

import ast
import importlib.util
import os
import re
import warnings
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
    assert f"{SKIP_MARKER} bash" in result.stdout
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
    assert result.stdout.count(f"{SKIP_MARKER} bash") == 1


def test_nexus_core_bridge_exposes_the_hooks() -> None:
    """python/nexus_core pins rootdir, so it must load the hook itself."""
    spec = importlib.util.spec_from_file_location("core_conftest_under_test", CORE_CONFTEST)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    for hook in ("pytest_sessionstart", "pytest_runtest_logreport", "pytest_sessionfinish"):
        assert callable(getattr(module, hook)), hook


def test_the_host_tool_guard_has_a_single_home() -> None:
    """The reason literal may only come from the shared helper.

    A copy that re-inlines the reason — even with different variable names —
    would silently stop matching the marker the CI hook escalates, so this
    scans every Python file in the repo, not just the two roots that hold the
    converted suites today.
    """
    here = Path(__file__).resolve()
    helper = (REPO_ROOT / "host_tool_guard.py").resolve()
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache"}
    offenders = []
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = Path(dirpath) / name
            if path.resolve() in (here, helper):
                continue  # the helper produces the literal; this file names it
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", SyntaxWarning)
                    tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and SKIP_MARKER in node.value:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert offenders == []


#: Test modules that skip for something other than a missing host tool. A
#: missing Python package, a service or database, or a checked-in artifact is
#: not a tool a CI runner provisions, so those keep their own wording.
EXEMPT_TOOL_SKIPS = {
    "bin/tests/test_canonical_shape_parity.py": "skips on missing psycopg2 (Python package)",
    "bin/tests/test_on_conflict_arbiter.py": "skips on missing psycopg2 (Python package)",
    "bin/tests/test_persona_bridge_parity.py": "skips on missing psycopg2 (Python package)",
    "bin/tests/test_role_vocab_parity_live.py": "skips when the live role probe has no state",
    "bin/tests/test_jetstream_unconsumed_monitor.py": "skips on missing nats-py (Python package)",
    "python/absorb/tests/test_emitter.py": "skips on missing nats-py or unreachable NATS",
    "python/SOLScript/tests/test_resolution_shrapnel_bridge_sql.py": "skips on missing psycopg2 or unreachable database",
    "python/SOLScript/tests/test_shrapnel_field_metadata_sync_sql.py": "skips on missing psycopg2 or unreachable database",
    "python/nebula/tests/test_v205_w_a_reconciliation_e2e.py": "skips on a missing migration file",
    "python/nexus_core/wrp/tests/test_conformance_ccnf_identity.py": "skips on a missing checked-in Go/Rust binary",
    "python/nexus_core/wrp/tests/test_conformance_ccnf_compile.py": "skips on a missing checked-in Rust binary",
    "python/nexus_core/wrp/tests/test_conformance_failure_matrix.py": "skips when no interactive lease is available",
    "python/nexus_core/sol/tests/test_t24_projection_conformance.py": "skips on an unimplemented feature, not a tool",
}

_TOOL_SKIP_HINT = re.compile(
    r"not installed|not available|not found|unavailable|missing|not on PATH",
    re.IGNORECASE,
)


def _test_modules():
    for root in (REPO_ROOT / "bin" / "tests", REPO_ROOT / "python", REPO_ROOT / "tests", REPO_ROOT / "tools"):
        if root.is_dir():
            yield from root.rglob("test_*.py")


def _probes_path(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr == "which":
                return node.lineno
            if isinstance(fn, ast.Name) and fn.id == "which":
                return node.lineno
    return None


def _skips_for_a_missing_thing(tree):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name not in {"skipTest", "SkipTest", "skip"} or not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            if _TOOL_SKIP_HINT.search(arg.value):
                return node.lineno
    return None


def test_host_tool_skips_go_through_the_helper() -> None:
    """A module that skips for a host tool must use host_tool_guard.

    Two shapes count: probing PATH with shutil.which, and skipping with a
    "missing/unavailable" reason. Either way the module must import the
    helper, so CI escalation sees the marker; anything that legitimately
    skips for a package, service, or repo artifact must be recorded in
    EXEMPT_TOOL_SKIPS with its reason.
    """
    offenders = []
    for path in _test_modules():
        rel = str(path.relative_to(REPO_ROOT))
        text = path.read_text(encoding="utf-8")
        if "host_tool_guard" in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        line = _probes_path(tree) or _skips_for_a_missing_thing(tree)
        if line is None or rel in EXEMPT_TOOL_SKIPS:
            continue
        offenders.append(f"{rel}:{line}")
    assert offenders == [], (
        "these test modules skip on something missing without the shared guard: "
        f"{offenders}. Use host_tool_guard for host tools, or record the exemption "
        "in EXEMPT_TOOL_SKIPS with its reason."
    )


def test_every_exemption_is_current_and_explained() -> None:
    """An exemption without a live file or a reason is stale documentation."""
    problems = []
    for rel, reason in EXEMPT_TOOL_SKIPS.items():
        if not (REPO_ROOT / rel).is_file():
            problems.append(f"{rel}: file no longer exists")
        if not reason.strip():
            problems.append(f"{rel}: empty reason")
    assert problems == []


def test_marked_unittest_setupclass_skip_is_escalated_on_ci(tmp_path: Path) -> None:
    """The unittest setUpClass shape the converted suites use must escalate.

    convert-or-exempt work moved several suites from unittest.skipUnless to
    raising SkipTest(skip_reason(...)); that reason has to reach the same CI
    escalation as a pytest mark, or the conversion would hide coverage.
    """
    body = (
        "import unittest\n\n"
        "from host_tool_guard import missing_tools, skip_reason\n\n\n"
        "class Suite(unittest.TestCase):\n"
        "    @classmethod\n"
        "    def setUpClass(cls):\n"
        "        missing = missing_tools(\"definitely-not-installed-tool\")\n"
        "        if missing:\n"
        "            raise unittest.SkipTest(\n"
        "                skip_reason(missing, \"these tests need a host tool\")\n"
        "            )\n\n"
        "    def test_one(self):\n"
        "        self.assertTrue(True)\n"
    )
    result = _session(tmp_path, body, ci=True)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "host tools missing on CI" in result.stdout
    assert "definitely-not-installed-tool" in result.stdout
