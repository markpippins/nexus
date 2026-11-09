#!/usr/bin/env python3
"""Pins where bin/regenerate_memory_seed.py writes.

The generator used to resolve its target from ``__file__`` alone, so running
the main checkout's copy from a linked worktree rewrote the main checkout's
seed and manifest. That is how a clean ``main`` can acquire uncommitted seed
changes nobody authored. These tests pin the resolution order and prove the
main-checkout case is actually reachable (non-vacuity).

Hermetic: a throwaway ``git init`` repo in tmp_path, no network, no database.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import shutil
import pytest

# Skip (naming what is missing) rather than crashing with FileNotFoundError on a
# host that lacks the external tool this suite drives.
_REQUIRED_TOOLS = ("git",)
_MISSING_TOOLS = [tool for tool in _REQUIRED_TOOLS if shutil.which(tool) is None]
pytestmark = pytest.mark.skipif(
    bool(_MISSING_TOOLS),
    reason="missing host tools for this suite: "
    + ", ".join(_MISSING_TOOLS)
    + " (these tests build throwaway git repositories)",
)

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO, "bin", "regenerate_memory_seed.py")


def load_generator():
    spec = importlib.util.spec_from_file_location("regen_seed_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _git_init_repo(path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    os.makedirs(os.path.join(path, "typescript", "tackle-seeds"), exist_ok=True)


def test_explicit_env_wins(tmp_path, monkeypatch):
    generator = load_generator()
    monkeypatch.setenv("NEXUS_REPO_ROOT", str(tmp_path))
    assert generator._resolve_repo_root() == os.path.abspath(str(tmp_path))


def test_cwd_worktree_is_preferred_over_script_checkout(tmp_path, monkeypatch):
    generator = load_generator()
    monkeypatch.delenv("NEXUS_REPO_ROOT", raising=False)
    worktree = tmp_path / "some-worktree"
    worktree.mkdir()
    _git_init_repo(worktree)
    monkeypatch.chdir(worktree)
    resolved = generator._resolve_repo_root()
    assert resolved == os.path.realpath(str(worktree))
    # Non-vacuity: the script's own checkout is a DIFFERENT path here, so this
    # assertion fails if resolution ever falls back to __file__.
    assert resolved != os.path.dirname(os.path.dirname(os.path.abspath(SCRIPT)))


def test_falls_back_to_script_checkout_outside_a_repo(tmp_path, monkeypatch):
    generator = load_generator()
    monkeypatch.delenv("NEXUS_REPO_ROOT", raising=False)
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)
    assert generator._resolve_repo_root() == os.path.dirname(
        os.path.dirname(os.path.abspath(SCRIPT))
    )


def test_foreign_git_repo_is_not_adopted(tmp_path, monkeypatch):
    # A git toplevel without typescript/tackle-seeds is some other project;
    # treating it as the seed root would write into it.
    generator = load_generator()
    monkeypatch.delenv("NEXUS_REPO_ROOT", raising=False)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=foreign, check=True)
    monkeypatch.chdir(foreign)
    assert generator._resolve_repo_root() == os.path.dirname(
        os.path.dirname(os.path.abspath(SCRIPT))
    )
