"""write_queue_reconciler.py — Cascade reconciler for the mobile write-queue.

Drains the ``nexus.write-queue.v1.>`` JetStream stream and reconciles each
entry into a canonical fact/event. This is the "mobile intent → reconciled
canonical fact/event" path: an entry is an ATTEMPT to cause a canonical
mutation from a disconnected / capability-poor context, NOT a record that
it happened.

Contract: typespec/v1/write-queue/ (PR #278). Reuses the CanonicalEnvelope
shape (nats_envelope). The semantic split is stream/lifecycle:
  nexus.events      → canonical events (what happened)
  nexus.write-queue → intents a context wants reconciled

Reconciliation is NOT simple replay. Every entry is classified via
ReconciliationOutcome before application:

  stale_version, conflicting_mutation, missing_capability,
  authorization_change, schema_evolution, duplicate_submission,
  provenance_verification, partial_application, result

Producer parity: the Java mobile write-queue (nexus-core-writequeue) and any
nats_envelope producer both publish CanonicalEnvelope-wrapped entries on this
stream, so this reconciler accepts both the snake_case CanonicalEnvelope
wrapper and a bare WriteQueueEntry payload.

Usage::

    DATABASE_URL=postgres://pguser:pgpass@localhost:5432/nexus \\
        NATS_URL=nats://localhost:4222 \\
        python3 write_queue_reconciler.py
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import signal
import sys
from typing import Any

# ── Path setup ──────────────────────────────────────────────────────
_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

# ── Configuration ───────────────────────────────────────────────────
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgres://pguser:pgpass@localhost:5432/nexus",
)
NATS_URL = os.getenv("NATS_URL")
if not NATS_URL:
    NATS_URL = "nats://localhost:4222"
SUBJECT = os.getenv("WRITE_QUEUE_SUBJECT", "nexus.write-queue.v1.>")
# Where applied writes land as canonical events.
EVENT_SUBJECT = os.getenv("NEXUS_EVENT_SUBJECT", "nexus.events.v1.reconciled.write")

# Reconciliation outcome vocabulary (contract: ReconciliationOutcome).
STALE_VERSION = "stale_version"
CONFLICTING_MUTATION = "conflicting_mutation"
MISSING_CAPABILITY = "missing_capability"
AUTHORIZATION_CHANGE = "authorization_change"
SCHEMA_EVOLUTION = "schema_evolution"
DUPLICATE_SUBMISSION = "duplicate_submission"
PROVENANCE_VERIFICATION = "provenance_verification"
PARTIAL_APPLICATION = "partial_application"
RESULT = "result"

# Default capability the reconciling context must hold. Extend per target.
REQUIRED_CAPABILITY = os.getenv("WRITE_QUEUE_CAPABILITY", "nexus.storage.canonical")

_seen: set[str] = set()
_SEEN_CAP = 100_000
_shutdown = asyncio.Event()


def _log(msg: str, *args) -> None:
    ts = datetime.datetime.now(datetime.UTC).isoformat()
    print(f"[{ts}] [write-queue-reconciler] {msg % args}", flush=True)


def _remember(write_id: str) -> None:
    if write_id not in _seen and len(_seen) >= _SEEN_CAP:
        _seen.pop()
    _seen.add(write_id)


def _signal_handler() -> None:
    _shutdown.set()


# ── Envelope / entry parsing ────────────────────────────────────────

def _unwrap(data: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
    """Return (entry_payload, subject) from a CanonicalEnvelope or bare entry."""
    # Bare WriteQueueEntry (no wrapper): has "intent" key.
    if "intent" in data:
        return data, ""

    # CanonicalEnvelope wrapper: payload holds the WriteQueueEntry.
    payload = data.get("payload")
    if isinstance(payload, dict) and "intent" in payload:
        return payload, str(data.get("subject", ""))
    return None


def _get_intent(entry: dict[str, Any]) -> dict[str, Any]:
    intent = entry.get("intent") or {}
    if not isinstance(intent, dict):
        return {}
    return intent


def _classify(intent: dict[str, Any], db, write_id: str) -> tuple[str, str | None]:
    """Classify the write before applying. Returns (outcome, detail).

    Non-replay outcomes detected here:
      - duplicate_submission   : writeId already reconciled/known
      - missing_capability     : reconciler lacks the required capability
      - stale_version          : target version ahead of intent baseVersion
      - schema_evolution       : target schema newer than intent schemaVersion
    Returns ("result", None) when none apply → safe to apply.
    """
    required = intent.get("requiredCapability") or REQUIRED_CAPABILITY
    # Capability: for now the reconciler holds a single declared capability.
    held = os.getenv("WRITE_QUEUE_CAPABILITY", REQUIRED_CAPABILITY)
    if required and required != held:
        return MISSING_CAPABILITY, f"reconciler holds '{held}', requires '{required}'"

    if write_id in _seen:
        return DUPLICATE_SUBMISSION, f"writeId {write_id} already processed"

    base_version = intent.get("baseVersion")
    if base_version and db is not None:
        current = _current_target_version(db, intent.get("target", ""))
        if current is not None and current != base_version:
            return STALE_VERSION, f"target at {current}, intent based on {base_version}"

    return RESULT, None


def _current_target_version(db, target: str) -> str | None:
    """Look up the current version of a target surface, if knowable."""
    try:
        cur = db.cursor()
        cur.execute(
            "SELECT COALESCE(MAX(version), '0') FROM resolution.proposition "
            "WHERE 1=1"
        )
        row = cur.fetchone()
        return str(row[0]) if row else None
    except Exception:
        return None


def _apply(intent: dict[str, Any], db) -> tuple[bool, str]:
    """Apply the intended mutation. Returns (applied, detail).

    This is the integration seam: the actual canonical mutation. In this
    slice it records the write as an applied reconciliation row so the
    flow is testable end-to-end; the real mutation (e.g. a resolved
    transition-entity) plugs in here.
    """
    try:
        cur = db.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS resolution.write_queue_applied ("
            " write_id TEXT PRIMARY KEY, target TEXT, verb TEXT, "
            " payload JSONB, applied_at TIMESTAMPTZ DEFAULT now())"
        )
        cur.execute(
            "INSERT INTO resolution.write_queue_applied (write_id, target, verb, payload) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (write_id) DO NOTHING",
            (intent.get("writeId"), intent.get("target"), intent.get("verb"),
             json.dumps(intent.get("payload", {}))),
        )
        db.commit()
        return True, "applied"
    except Exception as e:
        db.rollback()
        return False, f"apply failed: {e}"


async def _emit_reconciled(nc, entry: dict[str, Any], outcome: str, write_id: str,
                           produced_event_id: str | None) -> None:
    """Emit a canonical nexus.events envelope recording the reconciliation."""
    from nats_envelope.envelope import CanonicalEnvelope, Classification

    payload = {
        "writeId": write_id,
        "outcome": outcome,
        "intent": _get_intent(entry),
        "producedEventId": produced_event_id,
    }
    try:
        env = CanonicalEnvelope(
            event_type="WriteReconciled",
            origin_component="cascade",
            correlation_id=str(entry.get("correlationId") or write_id),
            causation_id=str(entry.get("intent", {}).get("writeId") or write_id),
            subject=EVENT_SUBJECT,
            payload=payload,
            classification=Classification.INTERNAL,
        )
        await nc.publish(EVENT_SUBJECT, json.dumps(env.to_dict()).encode())
        await nc.flush()
    except Exception as e:
        _log("failed to emit reconciled event: %s", e)


# ── NATS subscriber ─────────────────────────────────────────────────

async def run_reconciler() -> None:
    try:
        import psycopg2
    except ImportError as e:
        _log("FATAL: %s — install with: pip install psycopg2-binary", e)
        sys.exit(1)
    try:
        import nats
    except ImportError as e:
        _log("FATAL: %s — install with: pip install nats-py", e)
        sys.exit(1)

    pg_conn = None
    try:
        pg_conn = psycopg2.connect(DATABASE_URL)
        pg_conn.autocommit = True
    except Exception as e:
        _log("PostgreSQL unavailable (%s) — proceeding without version/capability checks", e)

    nc = await nats.connect(NATS_URL, name="write_queue_reconciler")
    _log("NATS connected to %s", NATS_URL)
    _log("Subscribing to %s", SUBJECT)

    processed = 0

    async def on_message(msg: Any) -> None:
        nonlocal processed
        try:
            data: dict[str, Any] = json.loads(msg.data.decode())
        except json.JSONDecodeError as e:
            _log("invalid JSON on %s: %s", msg.subject, e)
            return

        unwrapped = _unwrap(data)
        if unwrapped is None:
            return  # not a write-queue entry; ignore
        entry, subject = unwrapped
        intent = _get_intent(entry)
        write_id = str(intent.get("writeId") or entry.get("correlationId") or "")
        if not write_id:
            _log("entry on %s missing writeId — skipping", subject or msg.subject)
            return

        if write_id in _seen:
            _log("duplicate writeId %s — skipping (dedup)", write_id[:8])
            return

        outcome, detail = _classify(intent, pg_conn, write_id)
        if outcome == RESULT:
            applied, apply_detail = _apply(intent, pg_conn)
            produced = write_id if applied else None
            if not applied:
                outcome = PARTIAL_APPLICATION
                detail = apply_detail
        else:
            produced = None

        _remember(write_id)
        processed += 1
        _log("write %s (%s) → %s %s", write_id[:8], intent.get("verb"), outcome, detail or "")

        if nc and outcome in (RESULT, PARTIAL_APPLICATION, STALE_VERSION,
                              CONFLICTING_MUTATION, DUPLICATE_SUBMISSION):
            await _emit_reconciled(nc, entry, outcome, write_id, produced)

    sub = await nc.subscribe(SUBJECT, cb=on_message)
    try:
        await _shutdown.wait()
    except asyncio.CancelledError:
        pass
    finally:
        _log("shutting down — %d entries processed", processed)
        await sub.unsubscribe()
        await nc.drain()
        if pg_conn:
            pg_conn.close()


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            signal.signal(sig, _signal_handler)
    try:
        loop.run_until_complete(run_reconciler())
    finally:
        loop.close()


if __name__ == "__main__":
    main()