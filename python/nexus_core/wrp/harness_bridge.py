"""Harness bridge — invoke the git-claim producer from the TS broker (phase B).

Ruling ac38fa9b (git-intent marker contract, Q2/Q3): the completion site
derives the claim from the repository itself — never from executor-provided
values — and producer failure is non-blocking (evidence, not a gate).

Contract (one JSON object on stdin → one JSON object on stdout, exit 0):
  stdin:  {
            "attempt_id":      "<uuid>",       # required
            "repository_root": "/abs/path",    # required, absolute
            "base_ref":        "refs/heads/x", # required (refs/ or heads/x)
            "declared_paths":  ["a/b", ...]    # optional, repo-relative
          }
  stdout: { "requested": <bool>, "reason"?: str, "outcome"?: str,
            "claim_id"?: str, "evidence_id"?: str, "admitted"?: bool,
            "peb_transaction_id"?: str|null, "resolved_commit"?: str,
            "claimed_ref"?: str }

Exit codes: 0 = ran (inspect stdout.result), 1 = bad input,
            2 = unexpected bridge failure (broker treats as unavailable).
PEBProducerError (rejected/unavailable outcome) is NOT an exit-2 failure:
it is a legitimate non-blocking outcome reported in stdout.

The bridge derives claimed_ref/claimed_commit via `git rev-parse` inside
repository_root (Q2) and lets the producer's verifier do scope checks
(declared_paths are already enforced by git_verifier). Idempotent replays
ride the producer's unique-index dedup (claim_key/evidence content hash).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# python/ is the package root (nexus_core lives directly under it):
# wrp/ → parents[0], nexus_core/ → parents[1], python/ → parents[2].
REPO_SRC = Path(__file__).resolve().parents[2]
if str(REPO_SRC) not in sys.path:
    sys.path.insert(0, str(REPO_SRC))

from nexus_core.wrp.git_claim_producer import (  # noqa: E402
    GrantContext,
    PEBProducerError,
    _policy_hash,
    produce,
)


def _fail(code: int, reason: str) -> int:
    json.dump({"requested": False, "reason": reason}, sys.stdout)
    sys.stdout.write("\n")
    return code


def _rev_parse(repo_root: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "rev-parse", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        return _fail(1, f"invalid JSON on stdin: {exc}")

    if not isinstance(payload, dict):
        return _fail(1, "payload must be a JSON object")

    attempt_id = payload.get("attempt_id")
    repository_root = payload.get("repository_root")
    base_ref = payload.get("base_ref")
    declared_paths = payload.get("declared_paths") or []

    if not isinstance(attempt_id, str) or not attempt_id.strip():
        return _fail(1, "attempt_id is required")
    if not isinstance(repository_root, str) or not repository_root.strip():
        return _fail(1, "repository_root is required")
    if not isinstance(base_ref, str) or not base_ref.strip():
        return _fail(1, "base_ref is required")
    if not isinstance(declared_paths, list) or not all(
        isinstance(p, str) and p for p in declared_paths
    ):
        return _fail(1, "declared_paths must be a list of non-empty strings")

    repo_root = Path(repository_root)
    if not repo_root.is_absolute():
        return _fail(1, "repository_root must be absolute")

    # Q2: derive the claim from the repository, not from the caller.
    try:
        resolved_commit = _rev_parse(repo_root, "HEAD")
        branch = _rev_parse(repo_root, "--abbrev-ref", "HEAD")
    except (subprocess.CalledProcessError, OSError) as exc:
        return _fail(2, f"git rev-parse failed inside repository_root: {exc}")
    if not branch or branch == "HEAD":  # detached HEAD — cannot form refs/heads/
        return _fail(
            2,
            "detached HEAD inside repository_root: no refs/heads/<branch> to claim",
        )

    ctx = GrantContext(
        grant_id=f"harness-attempt:{attempt_id}",
        lease_id=payload.get("lease_id") or attempt_id,
        policy_version_hash="",  # filled below from the same context shape
        repository_root=str(repo_root),
        base_ref=base_ref if base_ref.startswith("refs/") else f"refs/heads/{base_ref}",
        claimed_ref=f"refs/heads/{branch}",
        claimed_commit=resolved_commit,
        declared_paths=tuple(declared_paths),
    )
    ctx = GrantContext(
        **{**ctx.__dict__, "policy_version_hash": _policy_hash(ctx)}
    )

    try:
        import psycopg2

        dsn = os.getenv(
            "NEXUS_PG_DSN",
            "host={h} port={p} user={u} password={pw} dbname={d}".format(
                h=os.getenv("PG_HOST", "localhost"),
                p=os.getenv("PG_PORT", "5432"),
                u=os.getenv("PG_USER", "pguser"),
                pw=os.getenv("PG_PASSWORD", "pgpass"),
                d=os.getenv("PG_DB_NAME", "nexus"),
            ),
        )
        conn = psycopg2.connect(dsn)
        try:
            submission = produce(attempt_id, ctx, conn)
        finally:
            conn.close()
    except PEBProducerError as exc:
        # Q3: rejected/unavailable is a legitimate non-blocking outcome.
        json.dump(
            {
                "requested": False,
                "reason": str(exc),
                "outcome": getattr(exc, "outcome", "unavailable"),
                "claimed_ref": ctx.claimed_ref,
                "resolved_commit": resolved_commit,
            },
            sys.stdout,
        )
        sys.stdout.write("\n")
        return 0
    except Exception as exc:  # noqa: BLE001 — kernel down, DSN bad, etc.
        json.dump(
            {
                "requested": False,
                "reason": f"producer unavailable: {exc}",
                "outcome": "unavailable",
                "claimed_ref": ctx.claimed_ref,
                "resolved_commit": resolved_commit,
            },
            sys.stdout,
        )
        sys.stdout.write("\n")
        return 0

    json.dump(
        {
            "requested": True,
            "outcome": submission.outcome,
            "admitted": submission.admitted,
            "reason": submission.reason,
            "claim_id": submission.claim_id,
            "evidence_id": submission.evidence_id,
            "peb_transaction_id": submission.peb_transaction_id,
            "claimed_ref": ctx.claimed_ref,
            "resolved_commit": resolved_commit,
        },
        sys.stdout,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
