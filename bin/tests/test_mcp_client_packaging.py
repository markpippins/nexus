#!/usr/bin/env python3
"""Guard: the canonical MCP client must be installable, not PYTHONPATH-dependent.

Background
----------
`python/nebula-mcp-client/` holds the platform's canonical MCP Streamable-HTTP
JSON-RPC client (to-do `8e09a57f`, Gap 4). Its own README documents

    from nebula_mcp_client import McpClient
    python -m nebula_mcp_client http://localhost:3102 call nebula_get_inbox '{...}'

and `AGENTS.md` R17 names the CLI form as the **canonical** inbox path for every
role. Both require the module to be importable from an installed environment.

It was not. `python/nebula-mcp-client/` shipped no packaging metadata, so:

    $ python3 -m nebula_mcp_client
    /usr/bin/python3: No module named nebula_mcp_client
    $ python3 -c 'import nebula_mcp_client'
    ModuleNotFoundError: No module named 'nebula_mcp_client'

Only a manual `PYTHONPATH=/home/codex/dev/nexus/python/nebula-mcp-client` made it
work. Every sibling package under `python/` (`timeclock`, `governance-envelope`,
`conduit`, `nexus_core`, `voyager`, the `vision/*` set, ...) has a
`pyproject.toml`; this one was the outlier, and the outlier is the one the R17
inbox path depends on. So the documented R17 check was broken by default and
every role silently lost its inbox.

Why the failure is quiet
------------------------
`bin/check-inbox.sh` sets `LIB_DIR` on `PYTHONPATH` itself (line 47), so the
script kept working. The defect was only visible to anyone following the
documented command rather than the script — which is exactly why it survived
unnoticed. A fix that only made `check-inbox.sh` work again would leave the
documented path broken.

What this test pins
-------------------
1. Packaging metadata exists and declares the module as a top-level `py-module`.
   The directory name contains hyphens, so it can never be imported as a
   package; the distribution must ship `nebula_mcp_client.py` as a top-level
   module.
2. **End-to-end**: a real `pip install` of the directory yields a module that
   imports and runs as `python3 -m nebula_mcp_client` in a subprocess whose
   `PYTHONPATH` is scrubbed and whose `sys.path` contains no repository
   directory. This is the actual fix, tested the way a consumer experiences it.
3. The client stays dependency-free. It is a deliberate design decision
   (to-do `8e09a57f`, Gap 4); a runtime dependency added here would silently
   reintroduce an install-time burden on every role's inbox check.

Non-vacuity
-----------
Test [2] alone could pass for the wrong reason — e.g. if some ambient
`site-packages` already provided the module, the install would prove nothing.
Test [3] therefore asserts the *negative* half: with no repository directory on
`sys.path` and no `PYTHONPATH`, the bare interpreter **cannot** import the
module. If that ever starts succeeding, the environment has been mutated and
test [2] has stopped proving anything.
"""

import json
import os
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG_DIR = REPO_ROOT / "python" / "nebula-mcp-client"
MODULE = "nebula_mcp_client"


def _run(cmd, **kw):
    """Run a command with PYTHONPATH scrubbed and stdin closed."""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=180,
        stdin=subprocess.DEVNULL, env=env, **kw
    )


# ── 1. structural ────────────────────────────────────────────────────

def test_pyproject_exists():
    """The directory must ship packaging metadata, unlike before."""
    assert (PKG_DIR / "pyproject.toml").is_file(), (
        "python/nebula-mcp-client/pyproject.toml is missing; the canonical MCP "
        "client is not installable, so the AGENTS.md R17 documented inbox path "
        "(`python3 -m nebula_mcp_client ...`) fails with ModuleNotFoundError."
    )


def test_declares_top_level_py_module():
    """Hyphenated directory name => the module must ship as a top-level module.

    An `[tool.setuptools.packages.find]` stanza would find nothing (the
    directory is not a valid identifier), producing an empty distribution that
    installs cleanly and then still cannot be imported. That failure mode is
    quieter than having no metadata at all, so it is pinned explicitly.
    """
    text = (PKG_DIR / "pyproject.toml").read_text()
    assert 'py-modules' in text, (
        "pyproject.toml must declare [tool.setuptools] py-modules = "
        f'["{MODULE}"]. Without it setuptools finds no packages in a '
        "hyphenated directory and the install is empty."
    )
    assert f'"{MODULE}"' in text, (
        f"py-modules must name {MODULE} explicitly."
    )
    assert "packages.find" not in text, (
        "packages.find cannot work in a hyphenated directory name; it would "
        "yield an empty distribution."
    )


def test_stays_dependency_free():
    """The dependency-free property is a decision, not an accident.

    to-do 8e09a57f Gap 4 specifies this client as dependency-free so that any
    role can use the R17 inbox path without provisioning an environment. A
    runtime dependency reintroduces exactly the install friction this fix removes.
    """
    text = (PKG_DIR / "pyproject.toml").read_text()
    deps_block = text.split("dependencies = [", 1)
    assert len(deps_block) == 2, "pyproject.toml must declare a dependencies list"
    body = deps_block[1].split("]", 1)[0].strip()
    assert body == "", (
        f"nebula-mcp-client must stay dependency-free; found runtime "
        f"dependencies: {body!r}"
    )


# ── 2. end-to-end install ────────────────────────────────────────────

@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    """`pip install` the client into a throwaway target dir.

    Flags chosen to keep this hermetic and offline: --no-index (no network),
    --no-deps (nothing to resolve, the client has no dependencies), and
    --no-build-isolation (use the already-installed setuptools rather than
    provisioning a fresh build env from PyPI).
    """
    target = tmp_path_factory.mktemp("mcp-client-install")
    proc = _run([
        sys.executable, "-m", "pip", "install",
        "--target", str(target),
        "--no-index", "--no-deps", "--no-build-isolation",
        "--disable-pip-version-check", "--quiet",
        str(PKG_DIR),
    ])
    assert proc.returncode == 0, (
        f"pip install of {PKG_DIR} failed:\n{proc.stdout}\n{proc.stderr}"
    )
    return target


def test_install_yields_importable_module(installed):
    """After install, `import nebula_mcp_client` works with no repo on sys.path."""
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        f"import {MODULE} as m;"
        "print(m.__file__);"
        "print(m.McpClient.__name__, m.McpError.__name__, m.PROTOCOL_VERSION)"
    )
    proc = _run([sys.executable, "-c", probe, str(installed)])
    assert proc.returncode == 0, (
        f"installed client is not importable:\n{proc.stdout}\n{proc.stderr}"
    )
    lines = proc.stdout.strip().splitlines()
    assert str(installed) in lines[0], (
        f"import resolved to {lines[0]!r}, not the install target {installed} — "
        "the test is not exercising the packaged artifact."
    )
    assert "McpClient" in lines[1] and "McpError" in lines[1], (
        f"public API missing from installed module: {lines[1]!r}"
    )


def test_module_runs_as_documented_cli(installed):
    """`python3 -m nebula_mcp_client` must run, per README + AGENTS.md R17.

    Invoked with no arguments it prints usage and exits 1 (see `_main`), so this
    asserts the documented CLI entry point is reachable rather than asserting any
    network behaviour — the test must not contact a live MCP server.
    """
    proc = _run([sys.executable, "-m", MODULE], cwd=str(installed))
    combined = proc.stdout + proc.stderr
    assert "No module named" not in combined, (
        f"`python3 -m {MODULE}` still fails to resolve:\n{combined}"
    )
    # No args => usage output, exit 1. Exit 1 is correct here, not a failure.
    assert "Streamable-HTTP" in combined or "usage" in combined.lower(), (
        f"module ran but printed neither usage nor its docstring:\n{combined}"
    )


# ── 3. non-vacuity ───────────────────────────────────────────────────

def test_install_is_load_bearing(installed):
    """Prove the install is what makes it work, so test [2] is not vacuous.

    Same interpreter, same scrubbed environment, but WITHOUT the install target
    on sys.path. If this import unexpectedly succeeds then something ambient
    already provides the module, and the end-to-end test above has stopped
    demonstrating anything about this repository's packaging.
    """
    probe = f"import {MODULE}"
    proc = _run([sys.executable, "-c", probe])
    assert proc.returncode != 0, (
        f"`import {MODULE}` succeeded without the install target on sys.path. "
        "An ambient copy is shadowing the test; the packaging assertions are "
        "no longer proving anything about this repo."
    )
    assert "No module named" in (proc.stdout + proc.stderr), (
        f"expected ModuleNotFoundError, got:\n{proc.stdout}\n{proc.stderr}"
    )


def test_check_inbox_script_not_the_only_working_path():
    """check-inbox.sh sets PYTHONPATH itself, so it masked this defect.

    Pin the asymmetry: the script remains self-sufficient, but the documented
    module form must be installable too. If someone "fixes" a future regression
    by editing only the script, this still passes — which is why the real
    end-to-end assertions above exist alongside it. This test documents the
    masking rather than duplicating it.
    """
    script = (REPO_ROOT / "bin" / "check-inbox.sh").read_text()
    assert "PYTHONPATH" in script, (
        "check-inbox.sh is expected to set PYTHONPATH itself; if that changed, "
        "re-verify whether the module still needs to be installable."
    )
