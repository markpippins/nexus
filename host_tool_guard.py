"""One home for the "this suite needs a host tool" skip guard.

Suites that drive an external program (bash, git, psql, createdb/dropdb, node,
docker) must not crash with FileNotFoundError on a host that lacks it: they
skip instead, naming the tool. Both halves of that live here — the PATH check
and the reason text — so a suite declares only what it needs and why:

    from host_tool_guard import skip_unless_host_tools

    pytestmark = skip_unless_host_tools(
        "bash", doing="these tests execute the bin/ shell scripts"
    )

The reason prefix is load-bearing: the repo-root conftest.py fails a CI session
that skipped for this reason, because a CI runner is expected to provision the
tools. Keep SKIP_MARKER here as its only copy.
"""

from __future__ import annotations

import shutil

import pytest

SKIP_MARKER = "missing host tools for this suite:"


def missing_tools(*tools: str) -> list[str]:
    """Return the tools that are not on PATH."""
    return [tool for tool in tools if shutil.which(tool) is None]


def skip_reason(tools: list[str], doing: str) -> str:
    """The reason naming `tools`, with `doing` saying why the suite needs them."""
    return f"{SKIP_MARKER} {', '.join(tools)} ({doing})"


def skip_unless_host_tools(*tools: str, doing: str):
    """A module-level pytest marker that skips when any of `tools` is absent.

    `doing` completes the reason, e.g. "these tests execute the bin/ scripts".
    """
    missing = missing_tools(*tools)
    return pytest.mark.skipif(bool(missing), reason=skip_reason(missing, doing))
