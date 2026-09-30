#!/usr/bin/env python3
"""supersede_digest_guard.py — parity-drift tripwire for the landed
bin/supersede-record.sh.

PR #673 (supersede-record-tooling) carries bin/supersede-record.sh, the
Decision 24 supersession tool. Its blob digest was pinned BEFORE landing
(bin/supersede-record.pin, provenance record fd2796e4) so that the post-merge
parity protocol can prove the squash landed the exact bytes that CI tested
and that the Decision 24 dogfood ran on the worktree copy. A squash merge can
in principle land different bytes than the PR head tested — and no CI job
checks the POST-squash state, because CI builds fresh checkouts of the PR.

This guard closes that gap mechanically. On every tick it computes the
digest of the blob at bin/supersede-record.sh on a git rev (default
origin/main) and compares it against the pin:

    exit 0  PIN_MATCH    landed digest == pin (the expected outcome once
                         #673 has landed)
    exit 1  TOOL_ERROR   git failure, malformed/missing pin — the guard
                         itself is broken; unit shows failed
    exit 2  PIN_PENDING  bin/supersede-record.sh is ABSENT from the target
                         rev (i.e. #673 has not landed yet) — not an error;
                         the guard is waiting. Output says so on stdout.
    exit 3  PIN_DRIFT    the file EXISTS on the target rev but its digest
                         differs from the pin — parity drift ALERT. The
                         systemd unit fails and the journal carries both
                         digests; a human decides (re-pin via a PR only if
                         the change is intended, else investigate the
                         squash).

The guard is read-only by construction: it runs `git cat-file` /
`git show` against an existing checkout, writes nothing, touches no
records, no APIs, no forums. Scheduling: systemd user timer
supersede-digest-guard.timer (hourly; unit files in bin/ follow the
attestation-janitor deploy doctrine). Parity drift is treated as a hard
alert: a mutated supersede-record.sh undermines the Decision 24 audit
trail (sha256 round-trips) that the whole supersession convention rests on.

Usage:
  supersede_digest_guard.py                              # check origin/main
  supersede_digest_guard.py --rev 7f43c6e6d              # check a PR head
  supersede_digest_guard.py --expect-digest <hex>        # override the pin
                                                         # (tests / drills)
  supersede_digest_guard.py --pin PATH --repo PATH       # hermetic runs
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

TARGET_PATH = "bin/supersede-record.sh"
DEFAULT_REPO = str(Path(__file__).resolve().parent.parent)
DEFAULT_PIN = str(Path(__file__).resolve().parent / "supersede-record.pin")
DEFAULT_REV = "origin/main"

SHA256_RE = re.compile(r"\b[0-9a-f]{64}\b")

EXIT_MATCH = 0
EXIT_TOOL_ERROR = 1
EXIT_PENDING = 2
EXIT_DRIFT = 3

LABELS = {
    EXIT_MATCH: "PIN_MATCH",
    EXIT_TOOL_ERROR: "TOOL_ERROR",
    EXIT_PENDING: "PIN_PENDING",
    EXIT_DRIFT: "PIN_DRIFT",
}


def _git(repo: Path, args: List[str]) -> subprocess.CompletedProcess:
    """Run a git command in repo; never raises on nonzero (caller decides)."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=60,
    )


def load_pin(pin_path: Path) -> Tuple[str, Optional[str]]:
    """Extract the first 64-hex sha256 from the pin file.

    Returns (digest, error). Comment/blank lines are ignored; a missing or
    digest-less file is a TOOL_ERROR (the guard must not guess).
    """
    try:
        text = pin_path.read_text()
    except OSError as exc:
        return "", f"cannot read pin file {pin_path}: {exc}"
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        found = SHA256_RE.search(stripped)
        if found:
            return found.group(0), None
        # First non-comment line that is not a digest -> malformed pin.
        return "", f"malformed pin file {pin_path}: first content line is not a sha256: {stripped!r}"
    return "", f"pin file {pin_path} contains no sha256 digest"


def blob_exists(repo: Path, rev: str, path: str) -> bool:
    proc = _git(repo, ["cat-file", "-e", f"{rev}:{path}"])
    return proc.returncode == 0


def blob_digest(repo: Path, rev: str, path: str) -> Tuple[str, Optional[str]]:
    """sha256 of the file content at rev:path, via git show (exact bytes)."""
    proc = _git(repo, ["show", f"{rev}:{path}"])
    if proc.returncode != 0:
        return "", f"git show {rev}:{path} failed: {(proc.stderr or '').strip()[:200]}"
    return hashlib.sha256(proc.stdout.encode("utf-8", errors="surrogateescape")).hexdigest(), None


def run_guard(
    *,
    repo: Path,
    pin_path: Path,
    rev: str,
    expect_digest: Optional[str],
    out=sys.stdout,
) -> int:
    # ── pin ──────────────────────────────────────────────────────────────
    if expect_digest is not None:
        pin = expect_digest.strip().lower()
        if not SHA256_RE.fullmatch(pin):
            print(f"supersede-digest-guard: TOOL_ERROR: --expect-digest is not a sha256: {expect_digest!r}", file=out)
            return EXIT_TOOL_ERROR
    else:
        pin, err = load_pin(pin_path)
        if err:
            print(f"supersede-digest-guard: TOOL_ERROR: {err}", file=out)
            return EXIT_TOOL_ERROR

    # ── rev sanity (fail loudly on a bogus rev, never confuse with drift) ─
    proc = _git(repo, ["rev-parse", "--verify", rev + "^{commit}"])
    if proc.returncode != 0:
        print(f"supersede-digest-guard: TOOL_ERROR: rev {rev!r} not resolvable in {repo}: {(proc.stderr or '').strip()[:200]}", file=out)
        return EXIT_TOOL_ERROR
    commit = proc.stdout.strip()

    # ── presence ─────────────────────────────────────────────────────────
    if not blob_exists(repo, rev, TARGET_PATH):
        print(
            f"supersede-digest-guard: PIN_PENDING: {TARGET_PATH} absent from {rev} "
            f"({commit[:9]}) — PR #673 has not landed yet; nothing to verify",
            file=out,
        )
        return EXIT_PENDING

    # ── digest ───────────────────────────────────────────────────────────
    digest, err = blob_digest(repo, rev, TARGET_PATH)
    if err:
        print(f"supersede-digest-guard: TOOL_ERROR: {err}", file=out)
        return EXIT_TOOL_ERROR

    if digest == pin:
        print(
            f"supersede-digest-guard: PIN_MATCH: {TARGET_PATH} @ {rev} ({commit[:9]}) "
            f"sha256={digest} == pin — parity verified",
            file=out,
        )
        return EXIT_MATCH

    print(
        f"supersede-digest-guard: PIN_DRIFT: {TARGET_PATH} @ {rev} ({commit[:9]}) "
        f"sha256={digest} != pin {pin} — parity drift; investigate the squash "
        f"merge before trusting Decision 24 round-trip digests",
        file=out,
    )
    return EXIT_DRIFT


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Parity-drift tripwire for the landed bin/supersede-record.sh (see module docstring).")
    ap.add_argument("--repo", default=DEFAULT_REPO, help="git checkout to interrogate (default: this checkout)")
    ap.add_argument("--pin", default=DEFAULT_PIN, help=f"pin file (default: {DEFAULT_PIN})")
    ap.add_argument("--rev", default=DEFAULT_REV, help=f"git rev to check (default: {DEFAULT_REV})")
    ap.add_argument("--expect-digest", default=None,
                    help="override the pin file with an explicit digest (tests / drills)")
    args = ap.parse_args(argv)

    return run_guard(
        repo=Path(args.repo),
        pin_path=Path(args.pin),
        rev=args.rev,
        expect_digest=args.expect_digest,
    )


if __name__ == "__main__":
    sys.exit(main())
