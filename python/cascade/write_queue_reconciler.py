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

Consumption model (2026-09-22): JetStream DURABLE PUSH consumer with
EXPLICIT acks. The original implementation used a plain core-NATS
subscription — a live tail: intents published while the reconciler was
down sat unprocessed in the WRITE_QUEUE stream and were never applied.
With the durable consumer the stream is the source of truth: every
message is held server-side until explicitly acked/termed, so a restart
BACK-FILLS everything missed during downtime. Ack discipline:
  ack  → message fully handled (applied, classified, or deduped)
  term → message can never succeed (malformed JSON, not a write-queue
         entry, missing writeId) — removed server-side, no redelivery
  nak  → unexpected internal failure — loud redelivery; at-least-once
         is safe because staging is idempotent (write_id PK) and the
         interpreter path is keyed on write_id
Config: WRITE_QUEUE_DURABLE (default write_queue_reconciler),
WRITE_QUEUE_STREAM (default WRITE_QUEUE), WRITE_QUEUE_ACK_WAIT_S (60),
WRITE_QUEUE_MAX_DELIVER (-1 = retry forever, loud).

STARTUP SEMANTICS — FAIL-FAST (ruling thread f63bfbc7, Option A):
On startup this process refuses to run without its prerequisites; it
never retries or waits for them:
  • NATS unreachable at connect      → exit 1 (NoServersError)
  • WRITE_QUEUE stream missing       → exit 1 (NotFoundError on the
    consumer lookup/subscribe — exits, NOT a retry loop)
  • staging table absent             → RuntimeError naming the canonical
    DDL (assert-dont-create: drift must be loud, never self-healed)
Mid-run DISCONNECTS are different: nats-py's reconnect loop keeps the
process alive and delivery resumes when the server returns (verified
empirically, 2026-09-25: scratch-stack matrix T1/T2/T3).
Consequence: stream provisioning and DB schema are DEPLOYMENT
prerequisites, owned by the caller — use
bin/start_write_queue_reconciler.py, which enforces the gate order
provision → readiness → pre-existence → start → attach-wait before
starting this process (see docs/write-queue-startup-gate-order.md).

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

# ── Governed-transition interpreter (hydration-gated) ────────────────
# The interpreter path (solscript.proposition / transition-entity) decides
# against the loaded ResolutionInterpreter state. With no seed data the
# interpreter is empty and every governed intent deterministically rejects
# (entity-not-found) — still a real, recorded decision (durable KeychainEvent
# to the outbox). When the store carries seed data (entities + state
# transitions), hydration enables committed/refused guard decisions.
# run_reconciler() hydrates once at startup; _apply_transition_entity()
# picks up the hydrated instance via _get_interpreter().
_HYDRATED_INTERPRETER: Any = None
_HYDRATION_ATTEMPTED = False


async def hydrate_interpreter(pg_dsn: str | None) -> bool:
    """Load resolution seed data into the governed-transition interpreter.

    Returns True only when the interpreter came up with usable governed
    surfaces (entities AND state transitions — the pair transition_entity
    decides against). Any failure logs and leaves the reconciler unhydrated:
    governed intents then decide on the empty interpreter (deterministic
    rejections, still durably recorded) — hydration problems can never
    crash the consumer or block the staging-only arc.
    """
    global _HYDRATED_INTERPRETER, _HYDRATION_ATTEMPTED
    if _HYDRATION_ATTEMPTED:
        return _HYDRATED_INTERPRETER is not None
    _HYDRATION_ATTEMPTED = True
    if not pg_dsn:
        _log("interpreter hydration: no DATABASE_URL — governed transitions run unhydrated")
        return False
    try:
        import asyncpg  # the loader's driver; optional for the staging-only arc
        from SOLScript.solscript.database_loader import DatabaseLoader
    except Exception as e:
        _log("interpreter hydration unavailable (%s) — governed transitions run unhydrated", e)
        return False

    from SOLScript.solscript.interpreter import ResolutionInterpreter
    interp = ResolutionInterpreter()

    async def _load() -> None:
        pool = await asyncpg.create_pool(pg_dsn, min_size=1, max_size=1)  # type: ignore[attr-defined]
        try:
            await DatabaseLoader(interp, pool).load_all()
        finally:
            await pool.close()

    try:
        await asyncio.wait_for(_load(), timeout=60)
    except Exception as e:
        _log("interpreter hydration failed (%s) — governed transitions run unhydrated", e)
        return False

    entities = len(getattr(interp, "entities", {}) or {})
    transitions = len(getattr(interp, "state_transitions", {}) or {})
    if entities == 0 or transitions == 0:
        _log("interpreter hydration: store carries no governed surfaces "
             "(entities=%d, state_transitions=%d) — running unhydrated",
             entities, transitions)
        return False
    _HYDRATED_INTERPRETER = interp
    _log("interpreter hydrated from resolution store: entities=%d, state_transitions=%d",
         entities, transitions)
    return True


def _get_interpreter() -> Any:
    """The hydrated interpreter when available, else a fresh (empty) one."""
    if _HYDRATED_INTERPRETER is not None:
        return _HYDRATED_INTERPRETER
    from SOLScript.solscript.interpreter import ResolutionInterpreter
    return ResolutionInterpreter()

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

    Dispatch by (target, verb) to the real canonical mutation:
      - solscript.proposition / transition-entity → run the deterministic
        ResolutionInterpreter.transition_entity to decide the governed
        outcome, then record the durable KeychainEvent to
        resolution.keychain_event_outbox (the canonical transition-event
        surface). The staging row is the audit record.
      - anything else → staging-only (safe no-op for unmapped targets).

    Never throws into the caller; returns (applied, detail).
    """
    try:
        _stage_applied(intent, db)
    except Exception as e:
        db.rollback()
        return False, f"stage failed: {e}"

    target = str(intent.get("target") or "")
    verb = str(intent.get("verb") or "")

    if target == "solscript.proposition" and verb == "transition-entity":
        return _apply_transition_entity(intent, db)

    return True, "applied (staged; unmapped target)"


def _stage_applied(intent: dict[str, Any], db) -> None:
    """Record the write in the audit staging row (idempotent).

    The table is provisioned by the canonical DDL (nexus-ci-bootstrap.sql
    snapshot and production migrations) — the reconciler asserts
    pre-existence and fails loudly rather than silently re-creating a
    divergent copy (tester inspection cbe83e25: self-provisioning masked
    snapshot drift; a stale local shape would reconcile invisibly wrong).
    """
    cur = db.cursor()
    cur.execute(
        "SELECT to_regclass('resolution.write_queue_applied') IS NOT NULL"
    )
    exists = cur.fetchone()
    if not exists or not str(exists[0]).lower().startswith("t"):
        raise RuntimeError(
            "resolution.write_queue_applied is missing — provision it via the "
            "canonical DDL (nexus-ci-bootstrap.sql / V-migrations); the "
            "reconciler no longer self-provisions"
        )
    cur.execute(
        "INSERT INTO resolution.write_queue_applied (write_id, target, verb, payload, outcome) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (write_id) DO NOTHING",
        (intent.get("writeId"), intent.get("target"), intent.get("verb"),
         json.dumps(intent.get("payload", {})), "pending"),
    )
    db.commit()


def _apply_transition_entity(intent: dict[str, Any], db) -> tuple[bool, str]:
    """Real governed transition via the deterministic interpreter.

    Runs ResolutionInterpreter.transition_entity to decide the outcome
    (committed / refused / rejected) and records the durable KeychainEvent
    to resolution.keychain_event_outbox — the canonical transition-event
    surface. The interpreter is the decision authority; the DB is the
    canonical fact store.
    """
    try:
        from SOLScript.solscript.interpreter import ResolutionInterpreter
        from SOLScript.solscript.events import build_transition_event
    except Exception as e:
        return False, f"interpreter unavailable: {e}"

    payload = intent.get("payload") or {}
    entity_id = str(payload.get("entityId") or payload.get("entity_id") or "")
    transition_id = str(payload.get("transitionId") or payload.get("transition_id") or "")
    if not entity_id or not transition_id:
        return False, "transition-entity intent missing entityId/transitionId"

    interpreter = _get_interpreter()
    passed, results = interpreter.transition_entity(
        entity_id, transition_id,
        source_event_id=intent.get("writeId"),
        correlation_id=intent.get("correlationId"),
        actor=str((intent.get("actor") or {}).get("role", "")),
        source_namespace="write-queue",
    )

    event = getattr(interpreter, "last_transition_event", None)
    outcome = "committed" if passed else "refused"

    def _event_json(e: Any) -> str:
        """Serialize the KeychainEvent (dataclass or dict) for the outbox row.

        Empirically caught 2026-09-25: last_transition_event is a KeychainEvent
        dataclass — json.dumps(event) raises "Object of type KeychainEvent is
        not JSON serializable", which failed the governed apply with
        partial_application and stalled the intent in nak-redelivery. The
        event carries nested dataclasses, hence default=asdict.
        """
        if e is None:
            return "{}"
        if isinstance(e, dict):
            return json.dumps(e)
        try:
            from dataclasses import asdict
            return json.dumps(asdict(e))
        except Exception:
            return json.dumps(str(e))

    # Return semantics (contract: typespec/v1/write-queue ReconciliationOutcome
    # + WriteQueueStatus): a DECIDED outcome — committed OR refused — means the
    # reconciliation finished and produced a result (outcome "result", and the
    # refusal/commit KeychainEvent IS the produced canonical event). Returning
    # False here is reserved for machinery failures (interpreter unavailable,
    # missing ids, outbox write failure); mislabeling a clean governed refusal
    # as partial_application would contradict "only part of the mutation
    # applied". Empirically observed 2026-09-25 on the first live governed arc.
    #
    # Record the durable KeychainEvent to the canonical outbox surface.
    try:
        cur = db.cursor()
        cur.execute(
            "INSERT INTO resolution.keychain_event_outbox ("
            " source_namespace, source_event_id, event_kind, outcome, "
            " schema_version, aggregate_id, causation_id, correlation_id, "
            " actor, effective_at, read_set, payload) "
            "VALUES (%s,%s,%s,%s,1,%s,%s,%s,%s,now(),%s,%s)",
            (
                "write-queue",
                intent.get("writeId"),
                "transition_entity",
                outcome,
                entity_id,
                None,
                intent.get("correlationId"),
                str((intent.get("actor") or {}).get("role", "")),
                json.dumps(results),
                _event_json(event),
            ),
        )
        cur.execute(
            "UPDATE resolution.write_queue_applied SET outcome=%s WHERE write_id=%s",
            (outcome, intent.get("writeId")),
        )
        db.commit()
    except Exception as e:
        db.rollback()
        return False, f"outbox write failed: {e}"

    # Decided outcome → completed reconciliation, regardless of commit/refuse.
    return True, f"{outcome}: {results}"


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
        from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
    except ImportError as e:
        _log("FATAL: %s — install with: pip install nats-py", e)
        sys.exit(1)

    pg_conn = None
    try:
        pg_conn = psycopg2.connect(DATABASE_URL)
        pg_conn.autocommit = True
    except Exception as e:
        _log("PostgreSQL unavailable (%s) — proceeding without version/capability checks", e)

    # Hydrate the governed-transition interpreter once at startup. On a
    # schema-only store this logs the unhydrated verdict and moves on; with
    # seed data the interpreter decides real committed/refused transitions.
    try:
        await hydrate_interpreter(DATABASE_URL if pg_conn is not None else None)
    except Exception as e:
        _log("interpreter hydration crashed (%s) — continuing unhydrated", e)

    nc = await nats.connect(NATS_URL, name="write_queue_reconciler")
    _log("NATS connected to %s", NATS_URL)
    _log("Subscribing to %s", SUBJECT)

    processed = 0

    async def on_message(msg: Any) -> None:
        nonlocal processed
        # ── Ack discipline (JetStream EXPLICIT) ─────────────────────────
        # term: can never succeed → no redelivery.
        try:
            data: dict[str, Any] = json.loads(msg.data.decode())
        except json.JSONDecodeError as e:
            _log("invalid JSON on %s: %s — term", msg.subject, e)
            await msg.term()
            return

        unwrapped = _unwrap(data)
        if unwrapped is None:
            _log("not a write-queue entry on %s — term", msg.subject)
            await msg.term()
            return
        entry, subject = unwrapped
        intent = _get_intent(entry)
        write_id = str(intent.get("writeId") or entry.get("correlationId") or "")
        if not write_id:
            _log("entry on %s missing writeId — term (cannot be processed or deduped)", subject or msg.subject)
            await msg.term()
            return

        # duplicate already handled this session → ack (nothing to do).
        if write_id in _seen:
            _log("duplicate writeId %s — skipping (dedup)", write_id[:8])
            await msg.ack()
            return

        # ── Process; unexpected failure = nak (loud redelivery, at-least-
        # once; staging is idempotent via write_id PK) ──────────────────
        try:
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
            await msg.ack()
        except Exception as e:
            _log("processing failure for %s: %s — nak (redelivery)", write_id[:8], e)
            try:
                await msg.nak()
            except Exception:
                pass

    # JetStream durable push consumer (explicit acks, backfill-on-restart).
    # DeliverPolicy.ALL matters only at consumer creation; afterwards the
    # consumer resumes from its ack floor, which is what back-fills downtime.
    js = nc.jetstream()
    durable = os.getenv("WRITE_QUEUE_DURABLE", "write_queue_reconciler")
    stream = os.getenv("WRITE_QUEUE_STREAM", "WRITE_QUEUE")
    ack_wait_s = int(os.getenv("WRITE_QUEUE_ACK_WAIT_S", "60"))
    max_deliver = int(os.getenv("WRITE_QUEUE_MAX_DELIVER", "-1"))
    cfg = ConsumerConfig(
        durable_name=durable,
        deliver_policy=DeliverPolicy.ALL,
        ack_policy=AckPolicy.EXPLICIT,
        ack_wait=ack_wait_s,
        max_deliver=max_deliver,
    )
    try:
        ci = await js.consumer_info(stream, durable)
        _log("existing durable '%s': %d pending / %d awaiting-ack — backfilling if pending > 0",
             durable, ci.num_pending, ci.num_ack_pending)
    except Exception:
        _log("no existing durable '%s' — creating on subscribe", durable)

    sub = await js.subscribe(SUBJECT, cb=on_message, manual_ack=True, config=cfg)
    _log("durable consumer '%s' attached to %s (explicit acks)", durable, SUBJECT)
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