"""admission_subscriber.py — Cascade Admission (ADR-006) NATS subscriber.

Subscribes to ``nexus.kernel.v1.transition.>`` via NATS and reacts to the
kernel event ``work_request.created`` (emitted by conduit-mcp ``appendEvent``
on WR_SUBMITTED → ``recordTransition`` → pg_notify →
``cascade-kernel-subscriber`` → NATS).

For each event it:

1. Calls conduit-mcp ``runtime_transition {wrId, type: WR_VALIDATED}`` so the
   WorkRequest folds VALIDATED → QUEUED and becomes runnable by
   ``runtime_tick`` (ADR-006 removes manual admission).
2. Mirrors the WorkRequest into ``execution.requests`` with
   ``business_key = wr_id``, ``source_wr_id = <wr uuid>``, ``status = READY``
   (idempotent ``ON CONFLICT (business_key) DO NOTHING`` — no duplicate
   mirrors).

Idempotency (AC): replaying the same NATS event must not double-validate or
double-mirror. Dedup layers:

- in-memory ``_seen`` set of event IDs (guards within one process lifetime)
- ``validateTransition`` rejection: once the WR is QUEUED+, a replayed
  WR_VALIDATED is refused by the state machine → treated as already-admitted
- ``ON CONFLICT (business_key) DO NOTHING`` on the mirror insert

Survivability (AC): runs as a systemd user service with ``Restart=always``,
reconnects to NATS automatically (nats-py), and has no BindsTo coupling to
conduit-mcp — a conduit-mcp restart does not cascade a stop to this daemon.

Architecture::

    conduit-mcp appendEvent(WR_SUBMITTED)
        └─→ kernel.transition_event ('work_request.created')
                └─→ trg_notify_transition → pg_notify
                        └─→ cascade-kernel-subscriber → NATS
                                └─→ nexus.kernel.v1.transition.work_request.created
                                        └─→ admission_subscriber.py  (this daemon)
                                                ├─→ conduit-mcp runtime_transition WR_VALIDATED
                                                └─→ execution.requests mirror (READY)

Usage::

    DATABASE_URL=postgres://pguser:pgpass@localhost:5432/nexus \\
        NATS_URL=nats://localhost:4222 \\
        python3 admission_subscriber.py
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import signal
import sys
import time
import urllib.request
import uuid as uuid_mod
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
NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
NATS_SUBJECT = os.getenv("ADMISSION_NATS_SUBJECT", "nexus.kernel.v1.transition.>")
CONDUIT_MCP_URL = os.getenv("CONDUIT_MCP_URL", "http://localhost:3100")

# ── Logging ─────────────────────────────────────────────────────────

def _log(msg: str, *args: Any) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    print(f"[{ts}] [admission-sub] {msg % args}", flush=True)


# ── Signal handling ─────────────────────────────────────────────────

_shutdown = asyncio.Event()
_seen: set[str] = set()  # event IDs already processed (in-process dedup)
_SEEN_CAP = 10_000  # bound memory: oldest processed IDs are dropped; a replay
                     # is still safe (state machine + ON CONFLICT idempotency)


def _remember(event_id: str) -> None:
    """Add an event ID to the dedup set, evicting entries past the cap."""
    if event_id not in _seen and len(_seen) >= _SEEN_CAP:
        # sets are unordered; evicting an arbitrary entry keeps the cap.
        # Idempotency is still guaranteed by the state machine + ON CONFLICT,
        # so a lost dedup entry only costs a redundant-but-safe reprocess.
        _seen.pop()
    _seen.add(event_id)


def _signal_handler() -> None:
    _log("Shutdown signal received — draining...")
    _shutdown.set()


# ═══════════════════════════════════════════════════════════════════════
#  Conduit-mcp runtime_transition (JSON-RPC over HTTP)
# ═══════════════════════════════════════════════════════════════════════

_rpc_id = 0


def _rpc_call(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call a conduit-mcp tool via Streamable-HTTP JSON-RPC (POST /)."""
    global _rpc_id
    _rpc_id += 1
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": _rpc_id,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }).encode("utf-8")
    req = urllib.request.Request(
        CONDUIT_MCP_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _extract_tool_text(response: dict[str, Any]) -> str:
    """Pull the text out of a conduit-mcp tools/call response."""
    result = response.get("result", {})
    content = result.get("content", [])
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            return item.get("text", "")
    return ""


def emit_wr_validated(wr_uuid: str) -> str:
    """Advance a VALIDATED WorkRequest → QUEUED via conduit-mcp.

    Returns "advanced", "already_advanced", or "error".
    Idempotent: if the state machine rejects WR_VALIDATED (WR no longer in
    VALIDATED state), the WR was already admitted — not an error.
    """
    try:
        resp = _rpc_call("runtime_transition", {
            "wrId": wr_uuid,
            "type": "WR_VALIDATED",
        })
    except Exception as e:  # noqa: BLE001 — network/rpc failures are retried by systemd
        _log("runtime_transition call failed: %s", e)
        return "error"

    error = resp.get("error")
    if error:
        message = str(error.get("message", ""))
        if message.upper().startswith("INVALID_TRANSITION"):
            _log("WR %s not in VALIDATED state (%s) — already advanced", wr_uuid[:8], message[:120])
            return "already_advanced"
        _log("runtime_transition error: %s", message[:200])
        return "error"

    text = _extract_tool_text(resp)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = {}
    status = payload.get("state", {}).get("status")
    _log("WR_VALIDATED emitted for %s → status=%s", wr_uuid[:8], status)
    return "advanced"


# ═══════════════════════════════════════════════════════════════════════
#  execution.requests mirror
# ═══════════════════════════════════════════════════════════════════════

def _derive_entity_key(dco_json: str | None, wr_id: str) -> str | None:
    """Derive the deterministic WR entity_key at birth (T26 Item B).

    Mirrors ``nexus_core.wrp.identity`` (SHA256 over sorted
    {domain,intent,actor,scope} for the canonical ``execute workrequest:{wr_id}``
    document) so the key equals the one ``db_adapter.add_work_request`` and
    ``tackle.vision_bridge`` stamp at creation. Returns None when derivation
    cannot produce a key (identity-unknown) — the WR write path stays fail-safe.
    """
    try:
        from nexus_core.wrp.identity import ccnf_input_from_dco_json, derive_entity_key
        return derive_entity_key(ccnf_input_from_dco_json(dco_json or "", wr_id))
    except ValueError:
        _log("no entity_key for wr=%s (intent not emittable)", wr_id)
        return None


def ensure_nebula_work_request(
    pg_conn: Any,
    wr_uuid: str,
    wr_id: str,
    title: str,
    dco_json: str | None,
    plan_id: str | None,
) -> str:
    """Ensure the WR exists (canonical-first) and return its id.

    W4 repoint (plan 8261650 stage 2, spec 7e8c0222, DBA pin 76572fb3).
    The WR is ensured on the canonical ``resolution.work_request`` store
    (resolve-then-bind). ``execution.requests.source_wr_id`` currently has an
    FK to ``nebula.work_requests_history(id)``; until the DBA's V193 retargets
    it to ``resolution.work_request(id)`` at the Stage-6 go, we keep a derived
    nebula bridge row SHARING the canonical uuid so the FK binding stays valid
    and the two rows are identity-linked, not accidentally divergent.

    Contract (DBA pin 76572fb3):
      1. Resolve canonical first: ``SELECT id FROM resolution.work_request
         WHERE legacy_id = %s OR id = %s::uuid``. If found, return it — no
         write at all.
      2. On miss, INSERT canonical: ``legacy_id = 'vision.work_requests:' ||
         wr_uuid`` (crosswalk style), ``business_status='DRAFT'``, ``dco_json``,
         ``created_by='cascade-admission'`` (fixes the T26 NULL-created_by
         lesson), ``context.plan_id`` + ``context.entity_key`` preserved,
         ``plan_id`` column NULL (W3 disposition 767abf1b carries over).
      3. Bridge nebula write: ``INSERT INTO nebula.work_requests_history ...
         ON CONFLICT (id) DO NOTHING`` using the SAME uuid the canonical row
         got, so the FK stays valid during the window and the rows are
         identity-linked. Return the canonical id.
      4. Entity-key active-reuse: check canonical (``context->>'entity_key'``
         + active bitemporal window) before insert, mirroring today's nebula
         logic.
      5. ``mirror_to_execution_requests`` is untouched — ``source_wr_id`` =
         the returned (canonical-first) id; the FK still validates because the
         bridge row shares the uuid.
    """
    now = datetime.datetime.utcnow().isoformat() + "Z"
    entity_key = _derive_entity_key(dco_json, wr_id)
    legacy_id = f"vision.work_requests:{wr_uuid}"

    with pg_conn.cursor() as cur:
        # 1. Resolve canonical first — no write if already present.
        cur.execute(
            "SELECT id FROM resolution.work_request "
            "WHERE legacy_id = %s OR id = %s::uuid LIMIT 1",
            (wr_id, wr_uuid),
        )
        row = cur.fetchone()
        if row:
            return str(row[0])

        # 4. Entity-key active-reuse on the canonical store (bitemporal window).
        if entity_key:
            cur.execute(
                "SELECT id FROM resolution.work_request "
                "WHERE context->>'entity_key' = %s "
                "AND now() >= valid_from AND now() < valid_until LIMIT 1",
                (entity_key,),
            )
            row = cur.fetchone()
            if row:
                _log("WR %s entity_key already active — reusing canonical %s (idempotent emission)",
                     wr_uuid[:8], str(row[0]))
                return str(row[0])

        # 2. INSERT canonical. Build context with plan_id + entity_key (767abf1b
        #    disposition: plan_id preserved at context.plan_id, column NULL).
        import json as _json
        dco = {}
        if dco_json:
            try:
                dco = _json.loads(dco_json) if isinstance(dco_json, str) else dco_json
                if not isinstance(dco, dict):
                    dco = {}
            except (_json.JSONDecodeError, AttributeError):
                dco = {}
        intent = dco.get("intent") if isinstance(dco.get("intent"), dict) else {}
        constraints = dco.get("constraints") if isinstance(dco.get("constraints"), dict) else {}
        context = {}
        if intent:
            context["intent"] = intent
        if constraints:
            context["constraints"] = constraints
        if plan_id:
            context["plan_id"] = plan_id
        if entity_key:
            context["entity_key"] = entity_key
        context["work_request_uuid"] = wr_uuid

        cur.execute(
            """INSERT INTO resolution.work_request
                   (id, title, business_status, intent, context, constraints,
                    created_by, dco_json, legacy_id, plan_id, created_at, updated_at)
               VALUES (%s::uuid, %s, 'DRAFT', %s, %s, %s, 'cascade-admission',
                       %s, %s, NULL, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (wr_uuid, title, intent.get("type"),
             _json.dumps(context), _json.dumps(constraints),
             dco_json, legacy_id, now, now),
        )

        # 3. Bridge nebula write sharing the canonical uuid (FK stays valid).
        cur.execute(
            """INSERT INTO nebula.work_requests_history
                   (id, legacy_id, plan_id, title, business_status, dco_json,
                    created_at, updated_at, entity_key)
               VALUES (%s::uuid, %s, %s, %s, 'DRAFT', %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (wr_uuid, legacy_id, None, title, dco_json, now, now, entity_key),
        )
        pg_conn.commit()

        # Return the canonical id (the bridge row shares the same uuid).
        return wr_uuid


def mirror_to_execution_requests(pg_conn: Any, wr_uuid: str) -> bool:
    """Idempotently mirror a WorkRequest into execution.requests (READY).

    business_key = vision.work_requests.wr_id;
    source_wr_id = nebula.work_requests_history.id (FK target).
    ON CONFLICT (business_key) DO NOTHING → replay-safe, no duplicate rows.
    """
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT wr_id, dco_json, title, context FROM vision.work_requests "
            "WHERE work_request_uuid = %s",
            (wr_uuid,),
        )
        row = cur.fetchone()
        if not row:
            _log("No vision.work_requests row for %s — skipping mirror", wr_uuid[:8])
            return False
        wr_id, dco_json, wr_title, wr_context = row[0], row[1], row[2] or "", row[3] or {}

        title, objective, intent_type = wr_title or wr_id, "", "task"
        if dco_json:
            try:
                dco = json.loads(dco_json) if isinstance(dco_json, str) else dco_json
                intent = dco.get("intent", {}) or {}
                title = title or intent.get("desired_outcome") or intent.get("objective") or wr_id
                objective = intent.get("problem_statement") or intent.get("objective") or ""
                intent_type = intent.get("type") or "task"
            except (json.JSONDecodeError, AttributeError):
                pass

        # context.plan_id is often a placeholder (the runtime kernel stores
        # the wr_id there when no real plan exists). Only forward it if it
        # actually resolves in implementation_plans_history — otherwise NULL
        # (the FK fk_work_requests_plan requires a real plan_number).
        plan_id = None
        if isinstance(wr_context, dict):
            candidate = wr_context.get("plan_id") or None
            if candidate:
                with pg_conn.cursor() as _plan_cur:
                    _plan_cur.execute(
                        "SELECT 1 FROM nebula.implementation_plans_history "
                        "WHERE plan_number = %s LIMIT 1",
                        (candidate,),
                    )
                    if _plan_cur.fetchone():
                        plan_id = candidate
                    else:
                        _log("context.plan_id %r not in implementation_plans_history — using NULL", candidate)
        nebula_id = ensure_nebula_work_request(
            pg_conn, wr_uuid, wr_id, title, dco_json, plan_id
        )

        cur.execute(
            """INSERT INTO execution.requests
                   (business_key, title, intent_type, objective, inputs,
                    status, source_wr_id)
               VALUES (%s, %s, %s, %s, '{}'::jsonb, 'READY', %s)
               ON CONFLICT (business_key) DO NOTHING""",
            (wr_id, title, intent_type, objective, nebula_id),
        )
        pg_conn.commit()
        inserted = cur.rowcount if cur.rowcount is not None else 0
        _log("Mirror %s → execution.requests (business_key=%s, status=READY)%s",
             wr_uuid[:8], wr_id, " [duplicate skipped]" if inserted == 0 else "")
        return True


# ═══════════════════════════════════════════════════════════════════════
#  Event handling
# ═══════════════════════════════════════════════════════════════════════

def extract_wr_uuid(data: dict[str, Any]) -> str:
    """Locate the WorkRequest UUID (aggregate_id) inside the envelope.

    kernel_subscriber publishes CanonicalEnvelopes whose inner payload wraps
    the raw kernel transition_event row. The pg_notify payload itself also
    carries aggregate_id / work_request_id at the top level.
    """
    envelope_payload = data.get("payload", {}) or {}
    inner = (
        envelope_payload.get("payload", envelope_payload)
        if isinstance(envelope_payload, dict) else envelope_payload
    )
    raw = inner.get("raw", inner) if isinstance(inner, dict) else inner

    candidates: list[Any] = []
    if isinstance(raw, dict):
        candidates.append(raw.get("aggregate_id"))
    if isinstance(inner, dict):
        candidates.append(inner.get("aggregate_id"))
    candidates.append(data.get("work_request_id"))
    candidates.append(data.get("aggregate_id"))
    for c in candidates:
        if c:
            return str(c)
    return ""


def is_work_request_created(data: dict[str, Any], subject: str) -> bool:
    """True when this event is the kernel 'work_request.created' event."""
    if subject.endswith("work_request.created"):
        return True
    return data.get("event_type") == "work_request.created"


class AdmissionFailure(Exception):
    """Raised when a WorkRequest could not be admitted this pass.

    The event is left unprocessed so NATS redelivery / daemon restart retries
    it. Idempotency is preserved: a replayed WR_VALIDATED is rejected by the
    state machine (already advanced) and the mirror is ON CONFLICT-safe.
    """


async def handle_work_request_created(
    nc: Any,
    pg_conn: Any,
    event_envelope: dict[str, Any],
    event_id: str,
) -> None:
    """Admit one WorkRequest: WR_VALIDATED + execution.requests mirror.

    Raises AdmissionFailure (or the underlying error) when the WR could not be
    fully admitted, so the caller leaves the event unseen and retries.
    """
    wr_uuid = extract_wr_uuid(event_envelope)
    if not wr_uuid:
        _log("Missing aggregate_id in event — skipping")
        return
    try:
        uuid_mod.UUID(wr_uuid)
    except (ValueError, AttributeError):
        _log("aggregate_id %r is not a UUID — skipping", wr_uuid)
        return

    _log("Admitting WorkRequest %s", wr_uuid[:8])

    # 1. Advance VALIDATED → QUEUED (idempotent)
    result = emit_wr_validated(wr_uuid)

    if result == "error":
        # Transition failed transiently (conduit-mcp down/error). Do NOT mirror
        # — an execution.requests READY row for a WR still VALIDATED would be
        # inconsistent cross-domain state.
        # D-T19 item 5: admission failure is observable on the canonical channel.
        try:
            from nats_publisher import build_failure_envelope
            subject, env = build_failure_envelope(
                "admission",
                f"runtime_transition WR_VALIDATED failed for {wr_uuid}",
                aggregate_id=wr_uuid,
                correlation_id=wr_uuid,
            )
            await nc.publish(subject, json.dumps(env.to_dict()).encode())
            await nc.flush()
        except Exception as e:  # noqa: BLE001
            _log("failed to emit admission.failed: %s", e)
        raise AdmissionFailure(
            f"runtime_transition WR_VALIDATED failed for {wr_uuid[:8]}"
        )

    # 2. Mirror into execution.requests (idempotent upsert). Mirror even when
    #    the WR was already advanced — the mirror may be the missing half.
    mirror_to_execution_requests(pg_conn, wr_uuid)


# ═══════════════════════════════════════════════════════════════════════
#  NATS subscriber
# ═══════════════════════════════════════════════════════════════════════

async def run_admission_subscriber() -> None:
    """Main loop: connect NATS + DB, subscribe to kernel transitions."""
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

    # ── Connect to PostgreSQL ──
    _log("Connecting to PostgreSQL...")
    pg_conn = psycopg2.connect(DATABASE_URL)
    pg_conn.autocommit = True
    _log("PostgreSQL connected")

    # ── Connect to NATS ──
    _log("Connecting to NATS at %s...", NATS_URL)
    nc = await nats.connect(NATS_URL, name="admission_subscriber")
    _log("NATS connected")

    processed_count = 0

    async def on_message(msg: Any) -> None:
        nonlocal processed_count

        try:
            data: dict[str, Any] = json.loads(msg.data.decode())
            event_id = str(data.get("event_id", ""))
            _log("Received event on %s (event_id=%s)", msg.subject, event_id[:8])

            if event_id in _seen:
                _log("Event %s already processed — skipping (dedup)", event_id[:8])
                return
            if not is_work_request_created(data, msg.subject):
                return  # not an admission event; ignore silently

            try:
                await handle_work_request_created(nc, pg_conn, data, event_id)
            except Exception:  # noqa: BLE001
                # Keep the event UNSEEN so redelivery / restart can retry it.
                # Idempotency is guaranteed by the state machine (rejects a
                # replayed WR_VALIDATED) and ON CONFLICT on the mirror.
                raise
            _remember(event_id)
            processed_count += 1

        except json.JSONDecodeError as e:
            _log("Invalid JSON: %s", e)
        except Exception as e:  # noqa: BLE001
            _log("Error processing message: %s", e)
            import traceback
            _log(traceback.format_exc())

    # ── Subscribe ──
    sub = await nc.subscribe(NATS_SUBJECT, cb=on_message)
    _log("Subscribed to %s — waiting for work_request.created events...", NATS_SUBJECT)

    # ── Wait for shutdown signal ──
    try:
        await _shutdown.wait()
    except asyncio.CancelledError:
        pass
    finally:
        _log("Shutting down — %d events processed", processed_count)
        await sub.unsubscribe()
        await nc.drain()
        pg_conn.close()
        _log("Connections closed")


# ── Entry point ─────────────────────────────────────────────────────

def main() -> None:
    """Entry point — installs signal handlers and runs the async loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    _log("Starting Cascade Admission Subscriber (ADR-006)...")
    _log("NATS: %s | Subject: %s | conduit-mcp: %s", NATS_URL, NATS_SUBJECT, CONDUIT_MCP_URL)
    try:
        loop.run_until_complete(run_admission_subscriber())
    except KeyboardInterrupt:
        _log("Interrupted")
    finally:
        loop.close()
        _log("Cascade Admission Subscriber stopped")


if __name__ == "__main__":
    main()
