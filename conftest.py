"""Fail CI when a suite skips because a host tool is missing.

Several suites drive an external tool (bash, git, psql, createdb/dropdb,
node, docker) and skip with the shared host-tool marker when it is absent. Off CI that is the right answer: the suite cannot run on
that machine. On a CI runner the tool is expected to be provisioned, so the
same skip means the suite covered nothing — this hook turns it into a failed
session and names the suites involved.

Inert unless CI is set and such a skip actually happened. The collected state
lives on the pytest config, not in module globals, so this hook can be loaded
twice in one session (the root conftest plus the python/nexus_core bridge)
and still report and fail exactly once.

The reason prefix comes from host_tool_guard.py, which also owns the check
that produces it, so the two cannot drift apart.
"""

from __future__ import annotations

import os

import pytest

from host_tool_guard import SKIP_MARKER

_STATE_ATTR = "_nexus_host_tool_skip_state"

_config = None


def _state(config):
    state = getattr(config, _STATE_ATTR, None)
    if state is None:
        state = {"skipped": {}, "reported": False}
        setattr(config, _STATE_ATTR, state)
    return state


def pytest_sessionstart(session):
    global _config
    _config = session.config


def pytest_runtest_logreport(report):
    if not report.skipped or _config is None:
        return
    text = str(report.longrepr)
    start = text.find(SKIP_MARKER)
    if start == -1:
        return

    reason = text[start:]
    # A skipped report's longrepr is a (file, line, reason) tuple, so the
    # rendered text ends with the reason's own closing paren plus the tuple's
    # quote. Dropping that suffix leaves the reason as it was written.
    for suffix in ("')", '")'):
        if reason.endswith(suffix):
            reason = reason[: -len(suffix)]
            break
    _state(_config)["skipped"][report.nodeid] = reason


def pytest_sessionfinish(session, exitstatus):
    if _config is None or not os.environ.get("CI"):
        return

    state = _state(_config)
    if state["reported"] or not state["skipped"]:
        return
    state["reported"] = True

    lines = [
        f"{len(state['skipped'])} test(s) skipped because a host tool is missing on "
        "this CI runner:"
    ]
    for nodeid, reason in sorted(state["skipped"].items()):
        lines.append(f"  {nodeid}: {reason}")
    lines.append(
        "CI runners are expected to provision these tools; install them "
        "instead of letting the suite skip."
    )

    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_sep("=", "host tools missing on CI")
        for line in lines:
            reporter.write_line(line)
    else:  # pragma: no cover - non-terminal runs (e.g. -p no:terminal)
        print("\n".join(lines))

    # Only a would-be success is promoted; a genuine failure or interruption
    # keeps its own (non-zero) status.
    if exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
