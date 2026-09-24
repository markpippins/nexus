"""Hermetic tests for post-agent-record.py path-content guard + --file mode.

Background (incident 2026-09-24): `--content` was handed a file PATH
(e.g. `-c /tmp/wp3_close.md`) and the poster stored the path string as
the record body — 49 hollow records across the fleet (46 repaired from
surviving /tmp sources, 5 unrecoverable and re-filed as I4 replacements
+ annotations). The tool now (a) offers --file/-F to read the body from
a file and (b) refuses --content that names an existing file unless
--force-content is passed.

Hermetic strategy: same as test_post_agent_record_tags.py — invoke the
CLI against an unreachable nebula URL. A valid invocation fails at the
POST (connection error, rc != 0, no guard message); a guard rejection
exits 2 with the guard message BEFORE any network call.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

SCRIPT = (
    __import__("pathlib")
    .Path(__file__)
    .resolve()
    .parents[1]
    / "post-agent-record.py"
)
DEAD_URL = "http://localhost:9"  # nothing listens here

GUARD_MSG = "looks like a path to an existing file"
MISSING_MSG = "cannot read --file"


def run_cli(*extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "-r", "dba", "-t", "path-probe",
         "--nebula-url", DEAD_URL, *extra],
        capture_output=True, text=True, timeout=30,
    )


@pytest.fixture()
def body_file(tmp_path):
    f = tmp_path / "body.md"
    f.write_text("# real body\n\ntext with 'quotes' and \"doubles\" — safe via -F\n")
    return str(f)


def test_file_flag_reads_body_and_reaches_post(body_file):
    """-F reads the file body; invocation fails only at the POST."""
    r = run_cli("-F", body_file)
    assert r.returncode != 0
    assert GUARD_MSG not in r.stderr
    assert MISSING_MSG not in r.stderr
    assert "connection" in (r.stderr + r.stdout).lower() or "urlopen" in (r.stderr + r.stdout).lower() or r.returncode == 1


def test_file_flag_missing_file_fails_cleanly(tmp_path):
    r = run_cli("-F", str(tmp_path / "nope.md"))
    assert r.returncode == 2
    assert MISSING_MSG in r.stderr


def test_file_and_content_mutually_exclusive(body_file):
    r = run_cli("-F", body_file, "-c", "some body")
    assert r.returncode == 2
    assert "mutually exclusive" in r.stderr


def test_path_as_content_is_refused(body_file):
    """The incident shape: -c <existing-path> must be refused, not stored."""
    r = run_cli("-c", body_file)
    assert r.returncode == 2
    assert GUARD_MSG in r.stderr
    assert "--file" in r.stderr  # actionable guidance


def test_path_as_content_with_force_content_proceeds(body_file):
    """Escape hatch: deliberate path-as-body must reach the POST."""
    r = run_cli("-c", body_file, "--force-content")
    assert r.returncode != 0
    assert GUARD_MSG not in r.stderr


def test_plain_body_still_accepted():
    """Ordinary content never trips the guard."""
    r = run_cli("-c", "an ordinary short body")
    assert r.returncode != 0
    assert GUARD_MSG not in r.stderr


def test_multiline_body_not_pathlike(tmp_path, body_file):
    """A body containing newlines is never treated as a path, even if the
    first line looks like one."""
    tricky = tmp_path / "decoy.md"
    tricky.write_text("decoy")
    r = run_cli("-c", f"{tricky}\n\nsecond line")
    assert r.returncode != 0
    assert GUARD_MSG not in r.stderr


def test_nonexistent_path_string_accepted_as_body():
    """A string that merely LOOKS like a path but names no file is content."""
    r = run_cli("-c", "/tmp/definitely-not-here-0924.md")
    assert r.returncode != 0
    assert GUARD_MSG not in r.stderr


def test_body_file_is_a_directory_refused(tmp_path):
    r = run_cli("-F", str(tmp_path))
    assert r.returncode == 2
    assert MISSING_MSG in r.stderr


def test_long_body_from_file_not_pathlike(body_file, monkeypatch):
    """Files >512 bytes are still only reachable via -F; the guard applies
    to the -c string regardless of the file's size."""
    big = body_file
    with open(big, "a", encoding="utf-8") as fh:
        fh.write("x" * 600)
    r = run_cli("-c", big)
    assert r.returncode == 2
    assert GUARD_MSG in r.stderr
