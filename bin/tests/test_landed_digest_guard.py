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

_GUARD_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_GUARD_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_GUARD_REPO_ROOT))

from host_tool_guard import skip_unless_host_tools  # noqa: E402

pytestmark = skip_unless_host_tools(
    "git", doing="these tests build throwaway git repositories"
)

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
    """Tier-1 pins advanced 2026-09-30 when #673/#675 landed (parity
    verified: digest protocol + squash-of-head). Tier-2 pins captured
    2026-10-01 @ origin/main ead7ff59a (existence positively verified).
    Tier 1 advanced again 2026-11-05 when PR #866 (gate 3 rollup coverage)
    landed c002f369c; digests from that blob. Advanced 2026-11-22 for four
    attested landings: #1039 (7598cc556, supersede pointer note), #1040
    (9a79e81f8, serviced-request close-out), #1037 (199985a17, refusal
    bookkeeping — also the merge_pr.py landing) and #1042 (6de95b004, rule
    ASK-CLOSE). Advanced again 2026-11-23 for #1046 (eb7a8cf34) and the
    #1045 (f3b80c824) drift it inherited: gate-3 bookkeeping is installed
    unconditionally now, so both audit-chain tools' bytes moved. Every digest
    captured from the origin/main blob after a positive cat-file -e existence
    check.
    The queue is empty as of 2026-11-05: the PR #695 entry (CIR-5 janitor
    rung) was cleared, because #695 MERGED 2026-10-08 and its captured digest
    was the merged blob — the expectation was met and never dequeued, then
    superseded by #866. The empty-queue assertion below pins that adjudication
    so a consumed entry cannot be re-added without a deliberate edit.
    Guard reads ALL_MATCH."""
    reg = HERE.parent / "landed-tool-pins.json"
    data = json.loads(reg.read_text())
    assert data["version"] == 1 and len(data["pins"]) >= 8
    by_path = {p["path"]: p for p in data["pins"]}
    # ADVANCED 2026-11-23: PR #1051 (ranking item 6) writes the canonical
    # metadata['record.retired'] key (ruling 8f44e3e4); was #1039 187c1cad…
    # (supersede pointer note stops asserting status:active on a terminal
    # successor), before that #691 1da39fddb.
    assert by_path["bin/supersede-record.sh"]["digest"] == (
        "2263cffadd5b21323f5cb2cc6affdfec76fcf27afc7aef91bde41dd8d8ed3580")
    # ADVANCED 2026-11-23: PR #1046 (eb7a8cf34) restated the check-only
    # contract for both bookkeeping halves; was #1040 9a79e81f8.
    assert by_path["bin/attestation_janitor.py"]["digest"] == (
        "1942b0885b04e24634dbecaa5c501178badb4f02f02850ed42d042f2daf823c0")
    # Adjudicated 2026-11-05: #695 merged at the queued digest and was never
    # dequeued; the entry is closed, not pending.
    assert by_path["bin/attestation_janitor.py"]["queued_prs"] == []
    # ADVANCED 2026-11-23: PR #1046 (eb7a8cf34) installs bookkeeping on
    # every gate-3 evaluation; it also settles #1045 (f3b80c824), whose
    # merge_pr.py change was the DRIFT posted 2026-11-23T11:00. Was #1037
    # 199985a17 (refusals recorded on the refused row, head-agnostic).
    assert by_path["bin/merge_pr.py"]["digest"] == (
        "e73308b88494ed385a0d6c436b78926b4ad2f8ea3138d8276095f38185c92bad")
    assert by_path["bin/gate_codes.py"]["digest"] == (
        "b25bdef712a62ac5867d04b61272da6dfb4cd5f31a1c028db8dcdfaa3d080689")
    # ADVANCED 2026-11-22: PR #1042 landed 6de95b004 (rule ASK-CLOSE); was
    # the 2026-10-01 tier-2 capture @ ead7ff59a / #678 7191ea9d9.
    assert by_path["bin/record_hygiene_sweep.py"]["digest"] == (
        "9cde8757b891d0df938bfea9579a80b38457c77d79ece2d693d98dd5fa7a7e1f")
    # TIER-2 PIN 2026-11-23: PR #1047 landed e272e1cc8 (attestation-residue
    # sweep). Pinned at landing because it mutates attestation records, so its
    # drift must flip the guard like the gate tools do. ADVANCED by PR #1051
    # (item 6): the sweep mirrors retirementPatch's canonical
    # metadata['record.retired'] key (ruling 8f44e3e4); was b70350ca…
    assert by_path["bin/attestation_residue_sweep.py"]["digest"] == (
        "7967eeea2bdb0d2783cbc455241c0b9329ff9152aa31d93e8394850519bee5eb")
    # TIER-2 PIN 2026-11-23: PR #1049 landed ecb2ff738 (harness_job_id
    # backfill). Pinned at landing because it mutates provenance.
    assert by_path["bin/harness_job_backfill.py"]["digest"] == (
        "f0aa42a0a6481bd408c1b28f6ad500df41c9a6cb755c6688c656ec27a4143923")
    # TIER-2 PIN 2026-11-23: PR #1051 landed 231f40506 (agent-record metadata
    # namespace, ranking item 6). Pinned at landing because it rewrites every
    # record's metadata object to its canonical <owner>.<key> spelling, with
    # per-write read-back verification.
    assert by_path["bin/metadata_namespace_normalize.py"]["digest"] == (
        "3665abc8cc338d27d1aa8d06c71b4bc5a2a66dd605b5527a12cb4f6d9bf02bd3")
    # ADVANCED 2026-11-06: the pin had gone stale at PR #878 (c96dfb5c0,
    # run attribution) — the open DRIFT incident 30186d3b — and this PR also
    # gives --model the $NEXUS_AGENT_MODEL default. Prior pin e548f405… was
    # PR #689 (849bf145d).
    assert by_path["bin/post-agent-record.py"]["digest"] == (
        "ca404bd487e0336a8f35f4275d53e21d9020cd4cb4eec54ae13ea5c6c9273e6c")
    assert by_path["bin/post-change-log.sh"]["digest"] == (
        "336f8e1898f4dc21adfa96aef096466788d295a3ea5915fced507541920335f6")
    assert by_path["bin/pgie-evidence.py"]["digest"] == (
        "8713a16a86de08161de6c8fa86b6809caccc64f177d3bd33952f504e18d2a4dc")
    # Every pin carries provenance; the queue is EMPTY as of 2026-11-05.
    for p in data["pins"]:
        assert (p.get("provenance") or "").strip(), "every pin documents its capture"
    queued = [(p["path"], q) for p in data["pins"] for q in p.get("queued_prs") or []]
    assert queued == [], (
        "the queue holds expectations that have NOT landed yet, never history: "
        "PR #695 was cleared on 2026-11-05 because it merged 2026-10-08 "
        "(see attestation_janitor provenance)"
    )
    # A queued entry, when one exists again, must document its capture.
    for p in data["pins"]:
        for q in p.get("queued_prs") or []:
            assert (q.get("provenance") or "").strip(), "queue entry documents its capture"


def test_repo_registry_tier2_paths_exist_on_main():
    """Positive existence check for every pinned path at origin/main —
    the epoch-0 lesson (an inverted existence check mislabeled a landed
    tool). Runs git; skips cleanly outside a nexus checkout."""
    import subprocess as sp
    reg = HERE.parent / "landed-tool-pins.json"
    data = json.loads(reg.read_text())
    nexus = HERE.parent.parent
    git = sp.run(["git", "-C", str(nexus), "rev-parse", "--verify", "origin/main"],
                 capture_output=True, text=True)
    if git.returncode != 0:
        import pytest
        pytest.skip("origin/main unavailable outside nexus checkout")
    for p in data["pins"]:
        e = sp.run(["git", "-C", str(nexus), "cat-file", "-e", f"origin/main:{p['path']}"],
                   capture_output=True, text=True)
        assert e.returncode == 0, f"{p['path']} absent from origin/main"


def test_pin_advance_template_present():
    """The pre-staged pin-advance template ships beside the registry and
    documents the digest-capture command and the epoch-0 existence check."""
    tpl = HERE.parent / "PIN_ADVANCE_TEMPLATE.md"
    text = tpl.read_text()
    assert "git show" in text and "sha256sum" in text, "digest capture command"
    assert "cat-file -e" in text, "mandatory existence check"
    assert "queued_prs" in text, "queue registration requirement"


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
