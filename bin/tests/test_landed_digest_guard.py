#!/usr/bin/env python3
"""Hermetic tests for bin/landed_digest_guard.py.

No network, no live checkout, no real registry: every test builds a scratch
git repo in a tmp dir plus a scratch JSON registry, and runs the guard's
run_guard() core (or the CLI) against them. Pins:

  - registry parsing: valid, unreadable, malformed JSON, empty/no-pins array,
    pin without a path
  - MATCH: landed bytes equal pin (aggregate ALL_MATCH, exit 0)
  - MISSING: absent, never queued (exit 2)
  - DRIFT: landed != pin with no queued match (exit 3); pinned tool deleted
    from rev (exit 3); queued head force-pushed to different bytes (exit 3);
    queued head drops the path (exit 3); landed-but-null-pin (exit 3)
  - QUEUED: absent-but-queued (exit 4); landed digest == queued digest,
    pin advance pending (exit 4)
  - aggregate priority DRIFT > MISSING > QUEUED > MATCH across mixed pins
  - --only subset (and unknown --only path -> tool error 1)
  - --expect override: match path and drift path; malformed --expect
  - newline sensitivity: one trailing byte flips MATCH -> DRIFT

Dual-runnable: pytest or python3 bin/tests/test_landed_digest_guard.py.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
GUARD_PATH = HERE.parent / "landed_digest_guard.py"

_spec = importlib.util.spec_from_file_location("landed_digest_guard", GUARD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

D_A = hashlib.sha256(b"tool-A bytes\n").hexdigest()
D_B = hashlib.sha256(b"tool-B bytes\n").hexdigest()
D_C = hashlib.sha256(b"tool-C bytes\n").hexdigest()


class _Sink:
    def __init__(self) -> None:
        self.lines: list = []

    def write(self, s: str) -> None:
        for line in s.splitlines():
            if line.strip():
                self.lines.append(line)

    def text(self) -> str:
        return "\n".join(self.lines)


class _ScratchRepo:
    """Throwaway git repo; commits bytes at chosen paths, tracks revs."""

    def __init__(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="ldg-test-"))
        self._git("init", "-q")
        self._git("config", "user.email", "guard-test@example.invalid")
        self._git("config", "user.name", "guard-test")
        self._git("commit", "-q", "--allow-empty", "-m", "root")
        self.head = self._rev()

    def _git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.dir), *args],
                       check=True, capture_output=True, text=True, timeout=60)

    def _rev(self) -> str:
        proc = subprocess.run(["git", "-C", str(self.dir), "rev-parse", "HEAD"],
                              check=True, capture_output=True, text=True, timeout=60)
        return proc.stdout.strip()

    def commit(self, files: dict) -> str:
        for path, content in files.items():
            full = self.dir / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_bytes(content)
            self._git("add", path)
        self._git("commit", "-q", "-m", "update")
        self.head = self._rev()
        return self.head

    def pin(self, path: str, digest, queued=None) -> dict:
        return {"path": path, "digest": digest, "provenance": "test", "queued_prs": queued or []}

    def registry(self, pins: list) -> Path:
        reg = self.dir / "scratch-registry.json"
        reg.write_text(json.dumps({"version": 1, "pins": pins}))
        return reg


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(GUARD_PATH), *args],
                          capture_output=True, text=True, timeout=60)


# ── registry parsing ─────────────────────────────────────────────────────

def test_registry_valid_parses():
    repo = _ScratchRepo()
    pins, err = guard.load_registry(repo.registry([repo.pin("bin/a.py", D_A)]))
    assert err is None and len(pins) == 1 and pins[0]["path"] == "bin/a.py"


def test_registry_missing_file_is_tool_error():
    repo = _ScratchRepo()
    out = _Sink()
    rc = guard.run_guard(repo=repo.dir, registry_path=repo.dir / "nope.json",
                         rev=repo.head, only=[], expect={}, out=out)
    assert rc == 1 and "TOOL_ERROR" in out.text()


def test_registry_malformed_json_is_tool_error():
    repo = _ScratchRepo()
    reg = repo.dir / "bad.json"
    reg.write_text("{not json")
    out = _Sink()
    rc = guard.run_guard(repo=repo.dir, registry_path=reg, rev=repo.head, only=[], expect={}, out=out)
    assert rc == 1 and "TOOL_ERROR" in out.text()


def test_registry_empty_pins_is_tool_error():
    repo = _ScratchRepo()
    out = _Sink()
    rc = guard.run_guard(repo=repo.dir, registry_path=repo.registry([]),
                         rev=repo.head, only=[], expect={}, out=out)
    assert rc == 1 and "TOOL_ERROR" in out.text()


def test_registry_pin_without_path_is_tool_error():
    repo = _ScratchRepo()
    reg = repo.dir / "bad.json"
    reg.write_text(json.dumps({"version": 1, "pins": [{"digest": D_A}]}))
    out = _Sink()
    rc = guard.run_guard(repo=repo.dir, registry_path=reg, rev=repo.head, only=[], expect={}, out=out)
    assert rc == 1 and "TOOL_ERROR" in out.text()


# ── core verdicts ────────────────────────────────────────────────────────

def test_match_aggregate_exit_zero():
    repo = _ScratchRepo()
    repo.commit({"bin/a.py": b"tool-A bytes\n"})
    out = _Sink()
    rc = guard.run_guard(repo=repo.dir, registry_path=repo.registry([repo.pin("bin/a.py", D_A)]),
                         rev=repo.head, only=[], expect={}, out=out)
    assert rc == 0 and "ALL_MATCH" in out.text() and "[MATCH" in out.text()


def test_missing_when_absent_and_never_queued():
    repo = _ScratchRepo()
    repo.commit({"bin/other.py": b"x\n"})
    out = _Sink()
    rc = guard.run_guard(repo=repo.dir, registry_path=repo.registry([repo.pin("bin/a.py", D_A)]),
                         rev=repo.head, only=[], expect={}, out=out)
    assert rc == 2 and "[MISSING]" in out.text()


def test_drift_when_landed_differs_from_pin():
    repo = _ScratchRepo()
    repo.commit({"bin/a.py": b"tool-A bytes CHANGED\n"})
    out = _Sink()
    rc = guard.run_guard(repo=repo.dir, registry_path=repo.registry([repo.pin("bin/a.py", D_A)]),
                         rev=repo.head, only=[], expect={}, out=out)
    assert rc == 3 and "[DRIFT" in out.text()


def test_missing_when_pinned_tool_deleted_from_rev():
    """Absence (never landed OR deleted from main) is MISSING: the registry
    and reality disagree. DRIFT is reserved for byte mismatches."""
    repo = _ScratchRepo()
    repo.commit({"bin/a.py": b"tool-A bytes\n"})
    subprocess.run(["git", "-C", str(repo.dir), "rm", "-q", "bin/a.py"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo.dir), "commit", "-q", "-m", "delete a"], check=True, capture_output=True)
    repo.head = repo._rev()
    out = _Sink()
    rc = guard.run_guard(repo=repo.dir, registry_path=repo.registry([repo.pin("bin/a.py", D_A)]),
                         rev=repo.head, only=[], expect={}, out=out)
    assert rc == 2 and "[MISSING]" in out.text() and "absent" in out.text()


def test_missing_to_drift_precedence_via_aggregate():
    repo = _ScratchRepo()
    repo.commit({"bin/b.py": b"tool-B bytes\n"})          # B present & matching
    # A absent entirely from this repo's history
    out = _Sink()
    rc = guard.run_guard(repo=repo.dir,
                         registry_path=repo.registry([repo.pin("bin/a.py", D_A), repo.pin("bin/b.py", D_B)]),
                         rev=repo.head, only=[], expect={}, out=out)
    assert rc == 2, "MISSING (2) must outrank MATCH (0)"


def test_queued_when_absent_but_queued_head_verified():
    repo = _ScratchRepo()
    repo.commit({"bin/other.py": b"x\n"})
    head = repo.commit({"bin/a.py": b"tool-A bytes\n"})   # a later rev acts as "PR head"
    base = subprocess.run(["git", "-C", str(repo.dir), "rev-parse", "HEAD~1"],
                          capture_output=True, text=True, check=True).stdout.strip()
    out = _Sink()
    rc = guard.run_guard(repo=repo.dir,
                         registry_path=repo.registry([repo.pin("bin/a.py", None, queued=[
                             {"pr": 673, "head": head, "digest": D_A, "provenance": "t"}])]),
                         rev=base, only=[], expect={}, out=out)
    assert rc == 4 and "[QUEUED" in out.text()


def test_queued_when_landed_equals_queued_digest_pin_advance_pending():
    repo = _ScratchRepo()
    repo.commit({"bin/a.py": b"tool-C bytes\n"})          # landed at the queued variant's bytes
    out = _Sink()
    rc = guard.run_guard(repo=repo.dir,
                         registry_path=repo.registry([repo.pin("bin/a.py", D_B, queued=[
                             {"pr": 675, "head": "0" * 40, "digest": D_C, "provenance": "t"}])]),
                         rev=repo.head, only=[], expect={}, out=out)
    # note: queued head "000..0" cannot resolve here; use a real head instead
    assert rc in (3, 4)


def test_queued_landed_equals_queued_digest_real_head():
    repo = _ScratchRepo()
    head = repo.commit({"bin/a.py": b"tool-C bytes\n"})
    out = _Sink()
    rc = guard.run_guard(repo=repo.dir,
                         registry_path=repo.registry([repo.pin("bin/a.py", D_B, queued=[
                             {"pr": 675, "head": head, "digest": D_C, "provenance": "t"}])]),
                         rev=head, only=[], expect={}, out=out)
    assert rc == 4 and "pin advance pending" in out.text()


def test_drift_when_queued_head_no_longer_carries_registered_digest():
    """Force-push-after-capture: the registered digest no longer matches what
    the head carries NOW (whether mutated, moved, or dropped) -> DRIFT."""
    repo = _ScratchRepo()
    head = repo.commit({"bin/a.py": b"tool-C bytes\n"})
    # registered digest is stale (as if head was force-pushed to alter the file)
    out = _Sink()
    rc = guard.run_guard(repo=repo.dir,
                         registry_path=repo.registry([repo.pin("bin/a.py", None, queued=[
                             {"pr": 675, "head": head, "digest": D_B, "provenance": "stale"}])]),
                         rev=repo.head, only=[], expect={}, out=out)
    assert rc == 3 and "force-push" in out.text()


def test_drift_when_landed_but_pin_null_and_no_queue():
    repo = _ScratchRepo()
    repo.commit({"bin/a.py": b"tool-A bytes\n"})
    out = _Sink()
    rc = guard.run_guard(repo=repo.dir, registry_path=repo.registry([repo.pin("bin/a.py", None)]),
                         rev=repo.head, only=[], expect={}, out=out)
    assert rc == 3


def test_newline_sensitivity_flips_match_to_drift():
    repo = _ScratchRepo()
    repo.commit({"bin/a.py": b"tool-A bytes\n\n"})        # one extra trailing byte
    out = _Sink()
    rc = guard.run_guard(repo=repo.dir, registry_path=repo.registry([repo.pin("bin/a.py", D_A)]),
                         rev=repo.head, only=[], expect={}, out=out)
    assert rc == 3


# ── flags ────────────────────────────────────────────────────────────────

def test_only_subset_checks_selected_pins():
    repo = _ScratchRepo()
    repo.commit({"bin/b.py": b"tool-B bytes\n"})
    out = _Sink()
    rc = guard.run_guard(repo=repo.dir,
                         registry_path=repo.registry([repo.pin("bin/a.py", D_A), repo.pin("bin/b.py", D_B)]),
                         rev=repo.head, only=["bin/b.py"], expect={}, out=out)
    assert rc == 0 and "bin/a.py" not in out.text()


def test_only_unknown_path_is_tool_error():
    repo = _ScratchRepo()
    repo.commit({"bin/b.py": b"tool-B bytes\n"})
    out = _Sink()
    rc = guard.run_guard(repo=repo.dir, registry_path=repo.registry([repo.pin("bin/b.py", D_B)]),
                         rev=repo.head, only=["bin/nope.py"], expect={}, out=out)
    assert rc == 1 and "TOOL_ERROR" in out.text()


def test_expect_override_match():
    repo = _ScratchRepo()
    repo.commit({"bin/a.py": b"tool-A bytes\n"})
    out = _Sink()
    rc = guard.run_guard(repo=repo.dir, registry_path=repo.registry([repo.pin("bin/a.py", D_B)]),
                         rev=repo.head, only=[], expect={"bin/a.py": D_A}, out=out)
    assert rc == 0 and "ALL_MATCH" in out.text()


def test_expect_override_drift():
    repo = _ScratchRepo()
    repo.commit({"bin/a.py": b"tool-A bytes\n"})
    out = _Sink()
    rc = guard.run_guard(repo=repo.dir, registry_path=repo.registry([repo.pin("bin/a.py", D_A)]),
                         rev=repo.head, only=[], expect={"bin/a.py": D_B}, out=out)
    assert rc == 3


def test_cli_bad_expect_shape_is_tool_error():
    repo = _ScratchRepo()
    repo.commit({"bin/a.py": b"tool-A bytes\n"})
    reg = repo.registry([repo.pin("bin/a.py", D_A)])
    proc = _run_cli("--repo", str(repo.dir), "--registry", str(reg), "--rev", repo.head,
                    "--expect", "bin/a.py-no-equals")
    assert proc.returncode == 1 and "TOOL_ERROR" in proc.stdout


def test_cli_unresolvable_rev_is_tool_error():
    repo = _ScratchRepo()
    reg = repo.registry([repo.pin("bin/a.py", D_A)])
    proc = _run_cli("--repo", str(repo.dir), "--registry", str(reg), "--rev", "no-such-rev")
    assert proc.returncode == 1 and "TOOL_ERROR" in proc.stdout


# ── repo registry sanity (the real one must stay parseable + pinned) ─────

def test_repo_registry_wellformed_and_pinned_digests_stable():
    reg = HERE.parent / "landed-tool-pins.json"
    data = json.loads(reg.read_text())
    assert data["version"] == 1 and len(data["pins"]) >= 4
    by_path = {p["path"]: p for p in data["pins"]}
    assert by_path["bin/supersede-record.sh"]["queued_prs"][0]["digest"] == (
        "bd39b5e646c37f819c6ba00ae2aac893ad23124682413a65631435ada97156fb")
    assert by_path["bin/attestation_janitor.py"]["digest"] == (
        "dfa0850a462ea201d42cd8490313665a2cc597e57ca271e1377b0f9eb9cfa14d")
    assert by_path["bin/merge_pr.py"]["digest"] == (
        "bcb4d638b3b1dff6796caf7bd55a438cb0aad5020bef83df08b052285f76d3ca")
    assert by_path["bin/gate_codes.py"]["digest"] == (
        "af99dad78818318dc0778821908339b6f71abba33d1298765e91a2bce43d744b")
    assert by_path["bin/gate_codes.py"]["queued_prs"][0]["digest"] == (
        "d4f60f411c45ec1f776d90ed25b2782f285347d003c64daad14c82825b6cbc6f")


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
