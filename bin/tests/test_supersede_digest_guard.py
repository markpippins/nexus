#!/usr/bin/env python3
"""Hermetic tests for bin/supersede_digest_guard.py.

No network, no live checkout, no pinfile in the repo tree: every test builds
a scratch git repo in a tmp dir, commits controlled content at a chosen path,
and runs the guard's run_guard() core against it (via --repo/--pin/--rev, or
--expect-digest for pin-override paths). Pins:

  - pin parsing: valid pin, comment/blank tolerance, missing file,
    digest-less file, malformed first content line
  - exit 2 PIN_PENDING: target path absent from the checked rev
  - exit 0 PIN_MATCH: landed digest equals pin (exact-byte via git show)
  - exit 3 PIN_DRIFT: landed bytes differ from pin by even one byte
  - exit 1 TOOL_ERROR: unresolvable rev, bad --expect-digest shape
  - drift content sensitivity: appended trailing newline / edited comment
    inside the script MUST flip MATCH to DRIFT (the whole point of the pin)

Dual-runnable: pytest or python3 bin/tests/test_supersede_digest_guard.py.
"""
from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
GUARD_PATH = HERE.parent / "supersede_digest_guard.py"
PIN_PATH = HERE.parent / "supersede-record.pin"

_spec = importlib.util.spec_from_file_location("supersede_digest_guard", GUARD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

PINNED = "bd39b5e646c37f819c6ba00ae2aac893ad23124682413a65631435ada97156fb"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GUARD_PATH), *args],
        capture_output=True, text=True, timeout=60,
    )


class _ScratchRepo:
    """A throwaway git repo with bin/supersede-record.sh-shaped commits."""

    def __init__(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="sdg-test-"))
        self._git("init", "-q")
        self._git("config", "user.email", "guard-test@example.invalid")
        self._git("config", "user.name", "guard-test")
        self._git("commit", "-q", "--allow-empty", "-m", "root")

    def _git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.dir), *args],
                       check=True, capture_output=True, text=True, timeout=60)

    def commit_file(self, path: str, content: bytes) -> str:
        full = self.dir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(content)
        self._git("add", path)
        self._git("commit", "-q", "-m", f"set {path}")
        return self._rev()

    def _rev(self) -> str:
        proc = subprocess.run(["git", "-C", str(self.dir), "rev-parse", "HEAD"],
                              check=True, capture_output=True, text=True, timeout=60)
        return proc.stdout.strip()

    def write_pin(self, digest: str) -> Path:
        pin = self.dir / "scratch.pin"
        pin.write_text(f"# scratch pin\n{digest}\n")
        return pin


def _script_bytes(extra: bytes = b"") -> bytes:
    return b"#!/bin/bash\nset -euo pipefail\necho supersede\n" + extra


# ── pin parsing ──────────────────────────────────────────────────────────

def test_pin_file_in_repo_is_wellformed_and_matches_pr673_pin():
    digest, err = guard.load_pin(PIN_PATH)
    assert err is None, err
    assert digest == PINNED, f"repo pin drifted from the PR #673 pre-merge pin: {digest}"


def test_load_pin_ignores_comments_and_blanks():
    repo = _ScratchRepo()
    pin = repo.dir / "p.pin"
    pin.write_text("# header\n\n   \n" + PINNED + "\n")
    digest, err = guard.load_pin(pin)
    assert err is None and digest == PINNED


def test_load_pin_missing_file_is_tool_error():
    digest, err = guard.load_pin(Path("/nonexistent/pin"))
    assert digest == "" and err is not None


def test_load_pin_digestless_file_is_tool_error():
    repo = _ScratchRepo()
    pin = repo.dir / "p.pin"
    pin.write_text("# nothing but comments\n\n")
    digest, err = guard.load_pin(pin)
    assert digest == "" and err is not None


def test_load_pin_malformed_first_line_is_tool_error():
    repo = _ScratchRepo()
    pin = repo.dir / "p.pin"
    pin.write_text("not-a-digest\n")
    digest, err = guard.load_pin(pin)
    assert digest == "" and err is not None


# ── CLI-level runs against scratch repos ─────────────────────────────────

def test_pending_when_file_absent_from_rev():
    repo = _ScratchRepo()
    rev = repo.commit_file("bin/other.txt", b"no supersede tool here\n")
    proc = _run("--repo", str(repo.dir), "--rev", rev,
                "--pin", str(repo.write_pin(PINNED)))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "PIN_PENDING" in proc.stdout


def test_match_when_landed_bytes_equal_pin():
    repo = _ScratchRepo()
    rev = repo.commit_file("bin/supersede-record.sh", _script_bytes())
    proc = _run("--repo", str(repo.dir), "--rev", rev,
                "--expect-digest", hashlib.sha256(_script_bytes()).hexdigest())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PIN_MATCH" in proc.stdout
    assert PINNED not in proc.stdout  # override path must not leak the real pin


def test_drift_when_landed_bytes_differ_from_pin():
    repo = _ScratchRepo()
    rev = repo.commit_file("bin/supersede-record.sh", _script_bytes(b"# tail comment\n"))
    proc = _run("--repo", str(repo.dir), "--rev", rev,
                "--expect-digest", PINNED)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "PIN_DRIFT" in proc.stdout


def test_drift_on_single_trailing_newline_difference():
    landed = _script_bytes()
    repo = _ScratchRepo()
    rev = repo.commit_file("bin/supersede-record.sh", landed + b"\n")
    proc = _run("--repo", str(repo.dir), "--rev", rev,
                "--expect-digest", hashlib.sha256(landed).hexdigest())
    assert proc.returncode == 3, proc.stdout + proc.stderr


def test_drift_when_comment_inside_script_is_edited():
    landed = _script_bytes(b"# provenance: v1\n")
    mutated = _script_bytes(b"# provenance: v2\n")
    repo = _ScratchRepo()
    rev = repo.commit_file("bin/supersede-record.sh", mutated)
    proc = _run("--repo", str(repo.dir), "--rev", rev,
                "--expect-digest", hashlib.sha256(landed).hexdigest())
    assert proc.returncode == 3, proc.stdout + proc.stderr


def test_tool_error_on_unresolvable_rev():
    repo = _ScratchRepo()
    proc = _run("--repo", str(repo.dir), "--rev", "does-not-exist",
                "--expect-digest", PINNED)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "TOOL_ERROR" in proc.stdout


def test_tool_error_on_bad_expect_digest_shape():
    repo = _ScratchRepo()
    rev = repo.commit_file("bin/supersede-record.sh", _script_bytes())
    proc = _run("--repo", str(repo.dir), "--rev", rev,
                "--expect-digest", "zz-not-hex")
    assert proc.returncode == 1, proc.stdout + proc.stderr


def test_tool_error_when_pinfile_missing_and_no_override():
    repo = _ScratchRepo()
    rev = repo.commit_file("bin/supersede-record.sh", _script_bytes())
    proc = _run("--repo", str(repo.dir), "--rev", rev,
                "--pin", str(repo.dir / "absent.pin"))
    assert proc.returncode == 1, proc.stdout + proc.stderr


def test_run_guard_core_direct_match_and_drift():
    landed = _script_bytes()
    repo = _ScratchRepo()
    rev = repo.commit_file("bin/supersede-record.sh", landed)
    pin = repo.write_pin(hashlib.sha256(landed).hexdigest())
    out = _StringSink()
    rc = guard.run_guard(repo=repo.dir, pin_path=pin, rev=rev,
                         expect_digest=None, out=out)
    assert rc == 0 and "PIN_MATCH" in out.text

    rev2 = repo.commit_file("bin/supersede-record.sh", landed + b"# changed\n")
    out2 = _StringSink()
    rc2 = guard.run_guard(repo=repo.dir, pin_path=pin, rev=rev2,
                          expect_digest=None, out=out2)
    assert rc2 == 3 and "PIN_DRIFT" in out2.text


class _StringSink:
    def __init__(self) -> None:
        self.text = ""

    def write(self, s: str) -> None:
        self.text += s


# ── dual-runnable runner (keep at EOF) ───────────────────────────────────

def _main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
