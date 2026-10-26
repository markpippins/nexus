#!/usr/bin/env python3
"""CLI Executor — second executor proving the Execution Authority abstraction.

This standalone script demonstrates that any executor can:
1. Find a pending WorkRequest
2. Acquire a lease (mutual exclusion)
3. Create and complete attempts
4. Issue execution receipts
5. Release the lease

It operates independently of the Python conduit, proving the
Execution Authority protocol is executor-agnostic.

Usage:
    python3 cli_executor.py --list                    # List pending requests
    python3 cli_executor.py --claim <request_id>      # Claim and execute a request
    python3 cli_executor.py --status <request_id>     # Show request status
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

# Add parent dir to path for db_adapter import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db_adapter import DBAdapter


def _emit_vision_receipt(wr_id: str, result: str, summary: str, executor: str) -> None:
    """Best-effort: emit a LOSM ExecutionReceipt into vision.receipts.

    Mirrors agent_chat.py's guarded receipt emission — the vision.receipts
    insert triggers trg_receipt_governance, which writes a governance event
    into peb.governance_events so THIS execution channel leaves a trace in
    the governance ledger (previously only the interactive channel did).

    Deliberately best-effort: import failures (losm_ir/tackle unavailable)
    or server errors are logged, never fatal — cli_executor must keep
    working standalone.

    NOTE: the default executor_id "cli-executor" is not in
    DEFAULT_KNOWN_EXECUTORS, so issue_receipt's pass-through whitelist maps
    the governance event's agent_role to "builder"; the real identity is
    preserved in metadata.losm_executor_id. That fallback is intentional.
    """
    try:
        from tackle.vision_bridge import issue_receipt
        from losm_ir.execution_receipt import ExecutionReceipt

        from datetime import datetime, timezone
        receipt = ExecutionReceipt(
            work_request_id=wr_id,
            executor_id=executor,
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            result=result,  # SUCCESS / FAILED / PARTIAL
            lineage_parent=summary,
        )
        issue_receipt(receipt, plan_id=wr_id, session_id=f"cli-{executor}")
    except ImportError:
        # losm_ir or tackle not available — skip (standalone mode)
        pass
    except Exception as e:
        print(f"[cli-executor] vision receipt emission failed: {e}", file=sys.stderr)


def list_pending_requests(db: DBAdapter) -> None:
    """List requests that are READY (can be claimed by an executor)."""
    with db._get_connection() as conn:
        cursor = conn.execute(
            """SELECT id, title, objective, status, created_at
               FROM execution.requests
               WHERE status IN ('ADMITTED', 'READY')
               ORDER BY created_at DESC
               LIMIT 20"""
        )
        rows = cursor.dict_fetchall()

    if not rows:
        print("No pending requests found.")
        return

    print(f"{'ID':<40} {'Status':<12} {'Created':<20} Title")
    print("-" * 100)
    for row in rows:
        created = row["created_at"].strftime("%Y-%m-%d %H:%M") if row["created_at"] else "?"
        print(f"{row['id']:<40} {row['status']:<12} {created:<20} {row['title'][:50]}")


def claim_and_execute(db: DBAdapter, request_id: str, executor_id: str = "cli-executor") -> None:
    """Acquire a lease, execute work, and release the lease."""
    print(f"Attempting to claim request {request_id} as {executor_id}...")

    # Step 1: Acquire lease (mutual exclusion)
    lease = db.acquire_lease(request_id=request_id, executor_id=executor_id, ttl_seconds=300)
    if not lease:
        print("FAILED: Could not acquire lease. Another executor may hold it.")
        return

    lease_id = lease["id"]
    print(f"Lease acquired: {lease_id}")

    try:
        # Step 2: Create attempt
        attempt = db.create_attempt(
            lease_id=lease_id,
            request_id=request_id,
            executor_id=executor_id,
            work_role="builder",
        )
        if attempt is None:
            print("FAILED: Plan became terminal before attempt creation; dispatch refused.")
            db.release_lease(lease_id)
            return
        attempt_id = attempt["id"]
        print(f"Attempt created: {attempt_id}")

        # Step 3: Start attempt
        db.start_attempt(attempt_id)
        print("Attempt started. Executing work...")

        # Step 4: Simulate work (replace with real work)
        time.sleep(2)
        result = {
            "executor": executor_id,
            "work_performed": "CLI execution test",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "artifacts": ["test_output.txt"],
        }

        # Step 5: Complete attempt
        db.complete_attempt(attempt_id, "SUCCEEDED", exit_code=0, result=result)
        print("Attempt completed with SUCCEEDED status.")

        # Step 6: Issue execution receipt
        receipt = db.issue_execution_receipt(
            attempt_id=attempt_id,
            request_id=request_id,
            receipt_type="IMPLEMENTATION",
            agent_role=executor_id,
            summary=f"CLI executor completed request {request_id[:8]}...",
            metadata={"result": result},
        )
        print(f"Execution receipt issued: {receipt['id']}")

        # Step 6b: Emit LOSM vision receipt (governance event via trigger)
        _emit_vision_receipt(
            wr_id=request_id,
            result="SUCCESS",
            summary=f"CLI executor completed request {request_id[:8]}...",
            executor=executor_id,
        )

        # Step 7: Release lease
        db.release_lease(lease_id)
        print("Lease released.")

        # Update request status to COMPLETED
        with db._get_connection() as conn:
            conn.execute(
                """UPDATE execution.requests
                   SET status = 'COMPLETED', updated_at = NOW()
                   WHERE id = %s""",
                (request_id,),
            )

        print(f"\nSUCCESS: Request {request_id[:8]}... executed by {executor_id}")

    except Exception as e:
        print(f"\nERROR during execution: {e}")
        # Best-effort: record the failure in the governance ledger too
        _emit_vision_receipt(
            wr_id=request_id,
            result="FAILED",
            summary=f"CLI executor failed request {request_id[:8]}...: {e}",
            executor=executor_id,
        )
        # Try to release lease on error
        try:
            db.release_lease(lease_id)
            print("Lease released after error.")
        except Exception:
            print("WARNING: Could not release lease after error.")
        raise


def show_status(db: DBAdapter, request_id: str) -> None:
    """Show detailed status of a request."""
    with db._get_connection() as conn:
        # Request info
        cursor = conn.execute(
            "SELECT * FROM execution.requests WHERE id = %s", (request_id,)
        )
        row = cursor.dict_fetchone()

        if not row:
            print(f"Request {request_id} not found.")
            return

        print(f"Request: {row['id']}")
        print(f"  Title: {row['title']}")
        print(f"  Status: {row['status']}")
        print(f"  Created: {row['created_at']}")

        # Leases
        cursor = conn.execute(
            "SELECT * FROM execution.leases WHERE request_id = %s ORDER BY created_at DESC",
            (request_id,),
        )
        leases = cursor.dict_fetchall()

        if leases:
            print(f"\n  Leases ({len(leases)}):")
            for l in leases:
                print(f"    {l['id'][:16]}... | {l['status']} | executor={l['executor_id']} | expires={l['expires_at']}")

        # Attempts
        cursor = conn.execute(
            "SELECT * FROM execution.attempts WHERE request_id = %s ORDER BY created_at DESC",
            (request_id,),
        )
        attempts = cursor.dict_fetchall()

        if attempts:
            print(f"\n  Attempts ({len(attempts)}):")
            for a in attempts:
                print(f"    {a['id'][:16]}... | {a['status']} | executor={a['executor_id']} | started={a['started_at']}")

        # Receipts
        cursor = conn.execute(
            "SELECT * FROM execution.receipts WHERE request_id = %s ORDER BY issued_at DESC",
            (request_id,),
        )
        receipts = cursor.dict_fetchall()

        if receipts:
            print(f"\n  Execution Receipts ({len(receipts)}):")
            for r in receipts:
                print(f"    {r['id'][:16]}... | type={r['type']} | role={r['agent_role']} | issued={r['issued_at']}")


def main():
    parser = argparse.ArgumentParser(description="CLI Executor — proves Execution Authority abstraction")
    parser.add_argument("--list", action="store_true", help="List pending requests")
    parser.add_argument("--claim", metavar="REQUEST_ID", help="Claim and execute a request")
    parser.add_argument("--status", metavar="REQUEST_ID", help="Show request status")
    parser.add_argument("--executor-id", default="cli-executor", help="Executor identity (default: cli-executor)")

    args = parser.parse_args()

    db = DBAdapter("")

    if args.list:
        list_pending_requests(db)
    elif args.claim:
        claim_and_execute(db, args.claim, args.executor_id)
    elif args.status:
        show_status(db, args.status)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
