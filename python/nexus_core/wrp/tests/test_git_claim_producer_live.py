"""Live integration: git-claim producer → resolution admission → R6 gate (6677c394).

Proves the producer work unit end-to-end against the real database and the
worktree PEB kernel (:8099 — prod :8098 runs main-checkout code without the
admission-adapter wiring):

  AC1 (producer wiring) — one full `produce()` cycle: the read-only git
       verifier verifies a real commit in this repository, the claim/evidence/
       confirmed-link rows persist into resolution.*, and the PEB admission
       transaction (`toolName='peb_admit_git_execution_claim'`) lands with a
       real minted `peb_transaction_id` and the real attempt UUID as
       `attempt_id`.

  AC2 (R6 gate) — the ruling's gate query returns ≥1 live row (non-fixture).

  AC5 (idempotency) — a replayed cycle reuses the same claim/evidence rows and
       the same PEB transaction (idempotency key `execution:attempt:<uuid>`),
       never a second admission receipt.

  R7 (fixture cleanup) — the three synthetic `attempt-001` fixture rows are
       deleted from the live DB in the same change (direct SQL, mirrored in
       the agent record; no migration).

The test seeds its own execution.requests/attempts/leases rows. They are
NOT cleaned up on exit: the producer writes real admission receipts against
them, and those receipts ARE the R6 gate's live correlated data (deleting
the seeds would orphan them). Skipped when PG or the kernel are unreachable,
per the repo's live-test convention.

Usage:
    cd /home/codex/dev/nexus
    python3 -m pytest python/nexus_core/wrp/tests/test_git_claim_producer_live.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

_SELF_DIR = os.path.dirname(os.path.abspath(__file__))
_NEXUS_PYTHON = os.path.abspath(os.path.join(_SELF_DIR, "..", "..", ".."))
if _NEXUS_PYTHON not in sys.path:
    sys.path.insert(0, _NEXUS_PYTHON)

_REPO_ROOT = Path(__file__).resolve().parents[4]

# The worktree kernel on :8099 carries this branch's producer contract
# (toolName mapping, adapter wiring, idempotent replay). Prod :8098 does not.
_KERNEL_BASE = os.getenv("PEB_BASE_URL", "http://localhost:8099")

from nexus_core.wrp.git_claim_producer import (  # noqa: E402
    PEB_TOOL_NAME,
    GrantContext,
    _policy_hash,
    produce,
)
from nexus_core.wrp.git_verifier import GitVerificationAdapter  # noqa: E402

try:
    import psycopg2
    import psycopg2.extras

    _PSYCOPG_AVAILABLE = True
except ImportError:
    _PSYCOPG_AVAILABLE = False


def _dsn() -> str:
    env = Path(_REPO_ROOT / "moleculer" / "nexus-broker" / ".env")
    if env.exists():
        vals = {}
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                vals[k.strip()] = v.strip()
        return (
            f"host={vals.get('PG_HOST', 'localhost')} port={vals.get('PG_PORT', '5432')} "
            f"user={vals.get('PG_USER', 'pguser')} password={vals.get('PG_PASSWORD', '')} "
            f"dbname={vals.get('PG_DB_NAME', 'nexus')}"
        )
    return os.getenv("PEB_DATABASE_URL", "postgresql://pguser:pgpass@localhost:5432/nexus")


def _pg_up() -> bool:
    if not _PSYCOPG_AVAILABLE:
        return False
    try:
        conn = psycopg2.connect(_dsn())
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.close()
        return True
    except Exception:
        return False


def _kernel_up() -> bool:
    try:
        import urllib.request

        with urllib.request.urlopen(f"{_KERNEL_BASE}/actuator/health", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not (_pg_up() and _kernel_up()),
    reason="live PG + PEB kernel (PEB_BASE_URL, default :8099) not both reachable",
)

_HEAD_COMMIT = subprocess.run(
    ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT, capture_output=True, text=True, check=True
).stdout.strip()

# The verifier requires a FULL branch ref for the claim (short names and HEAD
# are rejected). Base and claim point at the same branch ref, so the verified
# diff is empty — deterministic regardless of branch content. The verifier's
# own semantics are unit-tested elsewhere; this test proves the wiring.
_BRANCH = subprocess.run(
    ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=_REPO_ROOT, capture_output=True, text=True, check=True
).stdout.strip()
_BRANCH_REF = f"refs/heads/{_BRANCH}"


@pytest.fixture()
def seeded_attempt():
    """One real execution.requests/attempts/leases chain, cleaned up after."""
    conn = psycopg2.connect(_dsn())
    cur = conn.cursor()
    request_id = str(uuid.uuid4())
    lease_id = str(uuid.uuid4())
    attempt_id = str(uuid.uuid4())
    business_key = f"git-claim-producer-test:{uuid.uuid4()}"
    cur.execute(
        "INSERT INTO execution.requests (id, business_key, title, intent_type, objective, status) "
        "VALUES (%s, %s, 'git-claim producer live test', 'IMPLEMENTATION', 'test', 'COMPLETED')",
        (request_id, business_key),
    )
    cur.execute(
        "INSERT INTO execution.leases (id, request_id, executor_id, status, ttl_seconds, acquired_at, expires_at) "
        "VALUES (%s, %s, 'git-claim-producer-test', 'RELEASED', 60, NOW(), NOW() + INTERVAL '1 minute')",
        (lease_id, request_id),
    )
    cur.execute(
        "INSERT INTO execution.attempts (id, lease_id, request_id, executor_id, status, exit_code, result, created_at) "
        "VALUES (%s, %s, %s, 'git-claim-producer-test', 'SUCCEEDED', 0, %s::jsonb, NOW())",
        (attempt_id, lease_id, request_id, json.dumps({"test": True})),
    )
    conn.commit()
    yield {"request_id": request_id, "lease_id": lease_id, "attempt_id": attempt_id}
    # Deliberately NO teardown: the admission receipts written against these
    # rows are the R6 gate's live data (ruling 6677c394). Only truly failed
    # runs (no admission row yet) are safe to remove.
    cur.execute(
        "SELECT count(*) FROM resolution.execution_admission_receipt WHERE attempt_id = %s",
        (attempt_id,),
    )
    (admissions,) = cur.fetchone()
    if not admissions:
        cur.execute("DELETE FROM execution.attempts WHERE id = %s", (attempt_id,))
        cur.execute("DELETE FROM execution.leases WHERE id = %s", (lease_id,))
        cur.execute("DELETE FROM execution.requests WHERE id = %s", (request_id,))
        conn.commit()
    cur.close()
    conn.close()


def _grant_context(attempt_id: str, lease_id: str) -> GrantContext:
    ctx = GrantContext(
        grant_id="git-claim-producer-test-grant",
        lease_id=lease_id,
        policy_version_hash="",  # filled below from the SAME context shape
        repository_root=str(_REPO_ROOT),
        base_ref=_BRANCH_REF,
        claimed_ref=_BRANCH_REF,
        claimed_commit=_HEAD_COMMIT,
        declared_paths=("python/nexus_core/wrp/git_claim_producer.py",),
    )
    return replace(ctx, policy_version_hash=_policy_hash(ctx))


def _r6_gate_rows(cur) -> list:
    cur.execute(
        """
        SELECT ar.id, ar.attempt_id, ar.peb_transaction_id, a.id
        FROM resolution.execution_admission_receipt ar
        JOIN execution.attempts a ON a.id::text = ar.attempt_id
        WHERE ar.admitted IS NOT NULL
          AND ar.peb_transaction_id <> '00000000-0000-0000-0000-000000000001'
        """
    )
    return cur.fetchall()


def test_producer_cycle_and_r6_gate(seeded_attempt):
    attempt_id = seeded_attempt["attempt_id"]
    lease_id = seeded_attempt["lease_id"]
    submission = produce(
        attempt_id,
        _grant_context(attempt_id, lease_id),
        psycopg2.connect(_dsn()),
        adapter=GitVerificationAdapter(),
        peb_base_url=_KERNEL_BASE,
    )

    assert submission.attempt_id == attempt_id
    assert submission.outcome == "verified", f"verifier rejected: {submission.reason}"
    assert submission.admitted, f"admission failed: {submission.reason}"
    assert submission.peb_transaction_id, "no PEB transaction id returned"

    conn = psycopg2.connect(_dsn())
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # AC2: the R6 gate query returns ≥1 live row, and this attempt is among them.
    rows = _r6_gate_rows(cur)
    assert rows, "R6 gate query returned no live correlated rows"
    assert any(str(r["attempt_id"]) == attempt_id for r in rows), (
        "this attempt's admission row missing from R6 gate results"
    )

    # AC1: the receipt references the real minted PEB transaction.
    cur.execute(
        "SELECT peb_transaction_id, admitted FROM resolution.execution_admission_receipt "
        "WHERE attempt_id = %s",
        (attempt_id,),
    )
    receipt = cur.fetchone()
    assert receipt is not None
    assert str(receipt["peb_transaction_id"]) == submission.peb_transaction_id
    assert receipt["admitted"] is True

    # The PEB transaction exists on the kernel side with the ruling's key.
    cur.execute(
        "SELECT idempotency_key, tool_name FROM peb.transactions WHERE id = %s",
        (receipt["peb_transaction_id"],),
    )
    tx = cur.fetchone()
    assert tx is not None, "PEB transaction row missing"
    assert tx["idempotency_key"] == f"execution:attempt:{attempt_id}"
    assert tx["tool_name"] == PEB_TOOL_NAME

    cur.close()
    conn.close()


def test_producer_replay_is_idempotent(seeded_attempt):
    """Second cycle for the same attempt reuses claim/evidence/PEB transaction."""
    attempt_id = seeded_attempt["attempt_id"]
    lease_id = seeded_attempt["lease_id"]

    first = produce(
        attempt_id,
        _grant_context(attempt_id, lease_id),
        psycopg2.connect(_dsn()),
        adapter=GitVerificationAdapter(),
        peb_base_url=_KERNEL_BASE,
    )
    second = produce(
        attempt_id,
        _grant_context(attempt_id, lease_id),
        psycopg2.connect(_dsn()),
        adapter=GitVerificationAdapter(),
        peb_base_url=_KERNEL_BASE,
    )

    assert first.admitted and second.admitted
    assert first.claim_id == second.claim_id, "replay created a second claim row"
    assert first.evidence_id == second.evidence_id, "replay created a second evidence row"
    assert first.peb_transaction_id == second.peb_transaction_id, (
        "replay minted a second PEB transaction for the same attempt"
    )


def test_r7_fixture_rows_deleted():
    """R7: the three synthetic fixture receipts are gone from the live DB."""
    conn = psycopg2.connect(_dsn())
    cur = conn.cursor()
    cur.execute(
        "SELECT count(*) FROM resolution.execution_admission_receipt WHERE attempt_id = 'attempt-001'"
    )
    (count,) = cur.fetchone()
    assert count == 0, (
        f"{count} synthetic fixture rows remain (ruling 6677c394 R7 requires their deletion)"
    )
    cur.close()
    conn.close()
