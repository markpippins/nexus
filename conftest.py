"""Fail CI when a suite skips because a host tool is missing.

Several suites drive an external tool (bash, git, psql, createdb/dropdb,
node, docker) and skip with "missing host tools for this suite: <tools>"
when it is absent. Off CI that is the right answer: the suite cannot run on
that machine. On a CI runner the tool is expected to be provisioned, so the
same skip means the suite covered nothing — this hook turns it into a failed
session and names the suites involved.

Inert unless CI is set and such a skip actually happened.
"""

from __future__ import annotations

import os

import pytest

SKIP_MARKER = "missing host tools for this suite:"

_skipped = {}


def pytest_sessionstart(session):
    _skipped.clear()


def pytest_runtest_logreport(report):
    if not report.skipped:
        return
    text = str(report.longrepr)
    start = text.find(SKIP_MARKER)
    if start == -1:
        return
    reason = text[start:]
    # report.longrepr for a skip is a (file, line, reason) tuple, so the
    # rendered text closes with the reason's own paren plus the tuple's quote.
    for suffix in ("')", '")'):
        if reason.endswith(suffix):
            reason = reason[: -len(suffix)] + suffix[1]
            break
    _skipped[report.nodeid] = reason


def pytest_sessionfinish(session, exitstatus):
    if not _skipped or not os.environ.get("CI"):
        return

    lines = [
        f"{len(_skipped)} test(s) skipped because a host tool is missing on "
        "this CI runner:"
    ]
    for nodeid, reason in sorted(_skipped.items()):
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

    if exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
