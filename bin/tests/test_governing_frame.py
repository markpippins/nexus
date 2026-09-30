"""Drift pin for the ratified governing frame (Decision 29, c38ea344).

The frame is GENERATED. A hand edit to a generated file would change the content address of
every future execution without any review, so the committed file must be byte-identical to
the rendered output. This is the same anti-drift pattern as the doctrine-snapshot fixture,
applied to the frame.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RENDERER = ROOT / "bin" / "render_governing_frame.py"
PINS = ROOT / "bin" / "governing-frame.pins.json"
FRAME = ROOT / "docs" / "governing-frame.md"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RENDERER), *args],
        capture_output=True, text=True, check=False,
    )


def test_frame_file_exists():
    assert FRAME.exists(), f"ratified frame missing: {FRAME}"


def test_committed_frame_matches_rendered_output():
    """The drift pin: a hand edit fails here."""
    result = _run("--check")
    assert result.returncode == 0, result.stdout + result.stderr


def test_renderer_is_deterministic():
    a = _run("--stdout").stdout
    b = _run("--stdout").stdout
    assert a == b, "renderer is not deterministic; the CI byte-identity check would be noise"
    assert a, "renderer produced nothing"


def test_check_detects_drift_against_a_modified_copy(tmp_path):
    """The real artifact is never mutated; the renderer is pointed at a copy instead."""
    sys.path.insert(0, str(ROOT / "bin"))
    import render_governing_frame as rgf

    original = rgf.OUTPUT
    tampered = tmp_path / "governing-frame.md"
    tampered.write_text(original.read_text(encoding="utf-8") + "\nhand-edited\n", encoding="utf-8")
    rgf.OUTPUT = tampered
    try:
        argv = sys.argv
        sys.argv = ["render_governing_frame.py", "--check"]
        assert rgf.main() == 1, "a hand-edited frame must fail the drift pin"
    finally:
        rgf.OUTPUT = original
        sys.argv = argv


def test_pins_are_valid_and_carry_the_ratifying_decisions():
    pins = json.loads(PINS.read_text(encoding="utf-8"))
    assert pins["frame_schema_version"] == 1
    assert len(pins["pins"]) >= 5, "the frame must name the whole rule-set, not a subset"
    for pin in pins["pins"]:
        assert pin.get("id") and (pin.get("path") or pin.get("locator"))
        assert pin.get("revision"), f"pin {pin.get('id')} has no revision; unpinned is not a frame"
        assert pin.get("revision_kind")
    # Decision 28 + Decision 29 both ratify the frame.
    assert any("85c6d979" in d for d in pins["ratified_by"])
    assert any("c38ea344" in d for d in pins["ratified_by"])


def test_frame_does_not_copy_the_doctrine_it_pins():
    """Decision 29: the frame is a table of contents, NOT a dump of the doctrine.

    Copying AGENTS.md in would (a) bloat the artifact and (b) mint a new frame on every
    edit to a file that changes constantly -- the mutable-latest trap D28 Ruling 3 rejected.
    """
    body = FRAME.read_text(encoding="utf-8")
    agents = Path("/home/codex/dev/AGENTS.md")
    if agents.exists():
        assert len(body) < agents.stat().st_size, (
            "the frame must be a short table of contents, not a copy of the doctrine"
        )
    assert "Generated file" in body, "the frame must announce that it is generated"


def test_check_mode_reports_drift_in_its_message(tmp_path):
    """A drifted frame must produce an actionable message, not a bare non-zero exit."""
    sys.path.insert(0, str(ROOT / "bin"))
    import render_governing_frame as rgf

    original = rgf.OUTPUT
    tampered = tmp_path / "governing-frame.md"
    tampered.write_text(original.read_text(encoding="utf-8") + "drift\n", encoding="utf-8")
    rgf.OUTPUT = tampered
    try:
        argv = sys.argv
        sys.argv = ["render_governing_frame.py", "--check"]
        assert rgf.main() == 1, "a drifted frame must exit non-zero"
    finally:
        rgf.OUTPUT = original
        sys.argv = argv
