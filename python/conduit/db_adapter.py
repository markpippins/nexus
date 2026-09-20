import json
import logging
import os
import re
import uuid
from contextlib import contextmanager
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

_log = logging.getLogger("conduit.db_adapter")

# C1 gate 1 (Lilac plan 8261639): process-level invocation identity for
# receipt-write provenance. systemd sets INVOCATION_ID per service run;
# outside systemd this is "unsupervised" — still deterministic per process
# environment and visible in the provenance stamp.
_INVOCATION_ID = os.environ.get("INVOCATION_ID", "unsupervised") or "unsupervised"

# ── OpenCode model-ID qualification (shared by pipeline + executor) ──
#
# opencode registers each provider instance's models under
# '<provider-id>/<config-key>', where the config key is the API model
# name exactly as configured in AI settings.  The API model name may
# itself be org-prefixed (NVIDIA's 'nvidia/nemotron-3-ultra-550b-a55b'),
# so the registered ID is '<slug>/<model_identifier>' in every case —
# double-prefixed for org-prefixed providers
# ('nvidia/nvidia/nemotron-3-ultra-550b-a55b'), single-prefixed for bare
# names ('big-pickle' → 'opencode/big-pickle').
#
# The executor's old _ensure_provider_prefix prefixed EVERY chain model
# with the ROLE's provider, producing 'nvidia/big-pickle' etc. for
# fallbacks (ProviderModelNotFoundError).  The pipeline now qualifies
# each model with its OWN provider at chain-build time (main.py
# _resolve_model_chain), and these helpers keep both layers in sync.

# provider_type values that are generic API protocols, not opencode
# provider slugs — never usable as a model-ID prefix.
_GENERIC_PROVIDER_TYPES = {"openai", "anthropic"}


def provider_prefix_slug(
    provider_name: str = "",
    provider_type: str = "",
    provider_id: str = "",
) -> str:
    """Resolve the opencode provider-prefix slug for a model's provider.

    Priority:
    1. provider_name → lowercased/dashed slug (e.g. 'Nvidia' → 'nvidia')
    2. provider_type → as-is, unless it is a generic API protocol
       ('openai'/'anthropic') that is not an opencode provider slug
    3. provider_id → strip a leading 'prov-' (numeric DB PKs are
       skipped — they are not opencode provider IDs)

    Returns '' when nothing usable is found.
    """
    slug = ""
    if provider_name:
        slug = provider_name.lower().replace(" ", "-")
    if not slug and provider_type and provider_type not in _GENERIC_PROVIDER_TYPES:
        slug = provider_type
    if not slug and provider_id:
        pid = provider_id[5:] if provider_id.startswith("prov-") else provider_id
        if pid and not pid.isdigit():
            slug = pid
    return slug


def qualify_opencode_model_id(model_identifier: str, slug: str) -> str:
    """Return the opencode-registered ID for a provider's model.

    Registered form is always '<slug>/<model_identifier>' (see module
    note above).  Models lacking a slug (no provider info available)
    pass through unchanged.
    """
    if not model_identifier or not slug:
        return model_identifier
    return f"{slug}/{model_identifier}"


def fallback_provider_prefix_slug(
    provider_name: str = "",
    provider_type: str = "",
    provider_id: str = "",
    primary_slug: str = "",
) -> str:
    """Resolve the opencode provider-prefix slug for a FALLBACK model.

    Type-first: for non-generic provider types (e.g. 'opencode', 'ollama')
    the type IS the opencode provider slug — this keeps the gemini
    fallback (OpenCode Go, type 'opencode') as 'opencode/gemini-3.5-flash'.
    Generic API types ('openai'/'anthropic' — e.g. OpenRouter) cannot be
    opencode slugs, so fall back to the provider name ('OpenRouter' →
    'openrouter'), then provider_id, then the primary's slug.
    """
    if provider_type and provider_type not in _GENERIC_PROVIDER_TYPES:
        return provider_type
    slug = provider_prefix_slug(provider_name, provider_type, provider_id)
    return slug or primary_slug


# ── PostgreSQL (mandatory — no SQLite fallback) ──────────────────────
import psycopg2
import psycopg2.pool

_pg_pool = None
def _get_schema(explicit: str | None = None) -> str:
    """Return the schema name, resolving from explicit arg, then env, then default.

    Allows tests to switch schemas after import time by setting
    CONDUIT_PG_SCHEMA, and validates the resolved schema against
    reserved names (temporal, public)."""
    schema = explicit or os.environ.get("CONDUIT_PG_SCHEMA", "conduit")
    if schema.lower() == "public":
        raise ValueError(
            "CONDUIT_PG_SCHEMA='public' is the PostgreSQL default schema. "
            "Using it for Conduit tables could conflict with other "
            "applications. Use 'conduit' (or another name) "
            "for Conduit application data."
        )
    # SECURITY: validate schema is a safe PostgreSQL identifier before
    # DDL interpolation (SET search_path doesn't support parameterized ids).
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', schema):
        raise ValueError(
            f"Invalid schema name '{schema}': must match "
            f"/^[a-zA-Z_][a-zA-Z0-9_]*$/. Only unquoted PostgreSQL "
            f"identifiers are allowed."
        )
    return schema


def _get_pg_pool():
    """Lazy-init the PG connection pool. Reads CONDUIT_PG_DSN from env."""
    global _pg_pool
    if _pg_pool is None:
        dsn = os.environ["CONDUIT_PG_DSN"]
        _log.info("_get_pg_pool: initializing connection pool from env CONDUIT_PG_DSN")
        _pg_pool = psycopg2.pool.ThreadedConnectionPool(1, 10, dsn)
        _log.debug("_get_pg_pool: pool initialized (min=1, max=10)")
    return _pg_pool


class _CursorProxy:
    """Wraps PG cursor. fetchone/fetchall return plain tuples.
    dict_fetchone/dict_fetchall return dicts for SELECT * queries."""
    def __init__(self, cursor):
        self._cursor = cursor
        self.rowcount = cursor.rowcount if hasattr(cursor, 'rowcount') else 0
        self._columns = [d.name for d in cursor.description] if cursor.description else []

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def dict_fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        return dict(zip(self._columns, row))

    def dict_fetchall(self):
        rows = self._cursor.fetchall()
        cols = self._columns
        return [dict(zip(cols, r)) for r in rows]


class _ConnectionProxy:
    """Wraps a psycopg2 connection.

    Sets search_path on init so all queries target the correct schema.
    Returns _CursorProxy that supports fetchone/fetchall (tuples) and dict_fetchone/dict_fetchall (dicts)."""
    def __init__(self, conn, schema: str = "conduit"):
        self._conn = conn
        self.total_changes = 0
        # SECURITY: validate schema is a safe PostgreSQL identifier before
        # DDL interpolation (SET search_path doesn't support parameterized ids).
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', schema):
            raise ValueError(
                f"Invalid schema name '{schema}': must match "
                f"/^[a-zA-Z_][a-zA-Z0-9_]*$/. Only unquoted PostgreSQL "
                f"identifiers are allowed."
            )
        try:
            cur = conn.cursor()
            cur.execute(f"SET search_path TO {schema},execution,vision,peb,tackle,nebula")
            cur.close()
        except Exception:
            pass

    def execute(self, sql, params=None):
        cur = self._conn.cursor()
        cur.execute(sql, params)
        self.total_changes += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        return _CursorProxy(cur)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            pass  # already rolled back or autocommit

    def close(self):
        pass  # PG connections are returned to pool, not closed

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pool = _get_pg_pool()
        pool.putconn(self._conn)


# ── v079: Ticket lifecycle constants ─────────────────────────────
DEFAULT_TICKET_TTL_HOURS = 24  # tickets expire after 24h of inactivity
DEFAULT_STALE_SECONDS = 3600 * 6  # claimed tickets become stale after 6h idle


def derive_wr_entity_key(dco_json: str, wr_id: str) -> Optional[str]:
    """Derive the deterministic WR entity_key at birth (T26 Item B).

    Mirrors ``nexus_core.wrp.identity`` — the key is the SHA256 over the
    sorted ``{domain, intent, actor, scope}`` fields of the canonical WR
    ``execute workrequest:{wr_id}`` document, so it equals the key
    ``tackle.vision_bridge`` / ``vision-srv`` stamp at creation and the key
    ``ccnf_input_from_dco_json`` would re-derive on backfill. The DCO content
    is provenance only (the entity identity is keyed on ``wr_id``); content
    identity is tracked separately by ``content_hash`` (T08 three-way split).

    Returns None when derivation cannot produce a key (identity-unknown).
    The canonical shape never raises, but the guard keeps the WR write path
    fail-safe against a malformed intent.
    """
    try:
        from nexus_core.wrp.identity import ccnf_input_from_dco_json, derive_entity_key
        return derive_entity_key(ccnf_input_from_dco_json(dco_json, wr_id))
    except ValueError:
        _log.warning("derive_wr_entity_key: no entity_key for wr=%s (intent not emittable)", wr_id)
        return None


class DBAdapter:
    def __init__(self, db_path: str = None, schema: str = None):
        dsn = os.environ.get("CONDUIT_PG_DSN")
        if not dsn:
            _log.error("DBAdapter.__init__: CONDUIT_PG_DSN not set")
            raise RuntimeError(
                "CONDUIT_PG_DSN must be set. PostgreSQL is the only supported "
                "database backend."
            )
        self.db_path = dsn  # stored for error messages
        self._schema = _get_schema(schema)
        _log.info("DBAdapter: initializing schema=%s db=%s", self._schema, dsn.rsplit("@", 1)[-1] if "@" in dsn else "(dsn)")
        self._init_db()

    @contextmanager
    def _get_connection(self):
        pool = _get_pg_pool()
        conn = pool.getconn()
        proxy = _ConnectionProxy(conn, schema=self._schema)
        try:
            yield proxy
        except Exception:
            # Rollback on error before returning connection to pool
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                proxy.commit()
            except Exception:
                pass
            pool.putconn(conn)

    def _init_db(self):
        """Ensure the manager-owned tables exist.

        Manager-owned tables (work_requests, pipeline_cursor) are created
        here because the Python pipeline manager owns them — the MCP server
        doesn't know about them.

        MCP-owned tables (plans, receipts, tickets, sessions,
        circuit_breaker) are NOT created here — the MCP server is the
        sole schema authority for those.  If they're missing, fail fast
        with a clear message so the operator knows to start MCP first.
        """
        s = self._schema  # short alias for use in f-strings below
        if "'" in s:
            _log.error("_init_db: invalid schema name '%s'", s)
            raise ValueError(
                f"Invalid schema name '{s}': single-quote characters are not "
                f"allowed. Schema names must be plain identifiers."
            )
        _log.info("_init_db: initializing schema %s", s)
        with self._get_connection() as conn:
            # ── Manager-owned tables ─────────────────────────
            # work_requests and pipeline_cursor are owned by the Python
            # pipeline manager.  The MCP server creates receipts/tickets
            # in the vision schema and sessions/circuit_breaker
            # in the conduit schema — we do not create those here.
            # Plans table lives in nebula schema (migrated from conduit).
            # NOTE: FK to nebula.plans removed — nebula.plans is a VIEW
            # (not a TABLE), and FKs cannot reference views.
            # Production tables are created by schema migrations.
            # plan_id kept nullable to match original semantics.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS work_requests (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT,
                    title TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    dco_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pipeline_cursor (
                    role TEXT PRIMARY KEY,
                    last_processed_plan_id TEXT,
                    last_work_request_id TEXT,
                    updated_at TEXT NOT NULL
                )
            """)
            _log.debug("_init_db: manager-owned tables ensured")

            # ── Fail fast if MCP-owned tables are missing ─────
            # Tables are spread across conduit, nebula, and vision schemas:
            #   conduit: sessions, circuit_breaker
            #   nebula: plans (via implementation_plans view)
            #   vision: receipts, tickets
            table_schemas = {
                "sessions": s,
                "circuit_breaker": s,
                "receipts": "vision",
                "tickets": "vision",
            }
            for table, tschema in table_schemas.items():
                row = conn.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = %s AND table_name = %s",
                    (tschema, table),
                ).fetchone()
                count = row[0] if row else 0
                if count == 0:
                    _log.error("_init_db: table '%s' missing in schema '%s'", table, tschema)
                    raise RuntimeError(
                        f"Table '{table}' not found in schema '{tschema}' on {self.db_path}. "
                        f"Start the MCP server first to initialize the database schema."
                    )
            _log.debug("_init_db: all MCP-owned tables present")

            # ── Verify critical columns on MCP-owned tables ───
            required_columns = {
                "receipts": ("vision", ["ticket_id", "tokens_used"]),
                "tickets": ("vision", ["objective", "owner", "spawn_reason"]),
                "circuit_breaker": (s, ["paused"]),
                "sessions": (s, ["cost_usd"]),
            }
            for table, (tschema, cols) in required_columns.items():
                rows = conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = %s AND table_name = %s",
                    (tschema, table),
                ).fetchall()
                existing = {r[0] for r in rows}
                missing = [c for c in cols if c not in existing]
                if missing:
                    _log.error("_init_db: columns %s missing from '%s'", missing, table)
                    raise RuntimeError(
                        f"Columns {missing} missing from '{table}' in schema '{tschema}'. "
                        f"The MCP server may need to run pending migrations."
                    )
            _log.debug("_init_db: all critical columns verified")
            conn.commit()
        _log.info("_init_db: schema %s initialized successfully", s)

    # ── Tickets (v078) ────────────────────────────────────────────

    def create_ticket_if_missing(
        self, plan_id: str, role: str, created_by_receipt: str, created_at: str,
        objective: str = "", completion_criteria: str = "",
        owner: str = "", parent_ticket_id: Optional[str] = None,
        spawn_reason: str = "", replacement_of: Optional[str] = None,
    ) -> Optional[str]:
        """Create an open Ticket with constraint fields (v079).  Idempotent via partial unique index.

        If the deterministic ID already exists AND there is no open ticket for
        the (plan_id, role) pair, a new ticket with a unique ID is created
        instead of failing silently.  This handles restart scenarios where old
        tickets are in terminal states (completed, failed, etc.) and the caller
        needs fresh authorization.
        """
        ticket_id = f"ticket-{plan_id}-{role}-{created_by_receipt}"
        _log.debug("create_ticket_if_missing: plan=%s role=%s deterministic_id=%s", plan_id, role, ticket_id)
        expires_at = (datetime.fromisoformat(created_at.replace("Z", "")) + timedelta(hours=DEFAULT_TICKET_TTL_HOURS)).isoformat() + "Z"
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO tickets
                    (id, plan_id, role, status, created_by_receipt, created_at,
                     objective, completion_criteria, owner, parent_ticket_id,
                     spawn_reason, last_activity, expires_at, replacement_of)
                VALUES (%s, %s, %s, 'open', %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (ticket_id, plan_id, role, created_by_receipt, created_at,
                 objective or "", completion_criteria or "", owner or role,
                 parent_ticket_id, spawn_reason or "", created_at, expires_at,
                 replacement_of),
            )
            conn.commit()
            if cursor.rowcount > 0:
                _log.info("create_ticket_if_missing: created %s", ticket_id)
                return ticket_id

            # Deterministic ID already exists.  If there's an open ticket use it.
            _log.debug("create_ticket_if_missing: deterministic ID exists, checking for open ticket plan=%s role=%s", plan_id, role)
            row = conn.execute(
                "SELECT id FROM tickets WHERE plan_id = %s AND role = %s AND status = 'open'",
                (plan_id, role),
            ).fetchone()
            if row:
                _log.debug("create_ticket_if_missing: reusing existing open ticket %s", row[0])
                return row[0]

            # No open ticket exists — create a new one with a unique ID.
            ts = int(datetime.utcnow().timestamp())
            ticket_id = f"ticket-{plan_id}-{role}-{ts}"
            _log.debug("create_ticket_if_missing: creating fallback ticket %s", ticket_id)
            cursor2 = conn.execute(
                """
                INSERT INTO tickets
                    (id, plan_id, role, status, created_by_receipt, created_at,
                     objective, completion_criteria, owner, parent_ticket_id,
                     spawn_reason, last_activity, expires_at, replacement_of)
                VALUES (%s, %s, %s, 'open', %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (ticket_id, plan_id, role, created_by_receipt, created_at,
                 objective or "", completion_criteria or "", owner or role,
                 parent_ticket_id, spawn_reason or "", created_at, expires_at,
                 replacement_of),
            )
            conn.commit()
            if cursor2.rowcount > 0:
                _log.info("create_ticket_if_missing: created fallback %s", ticket_id)
                return ticket_id
            _log.warning("create_ticket_if_missing: failed to create any ticket plan=%s role=%s", plan_id, role)
            return None

    def claim_ticket(self, plan_id: str, role: str, session_id: str) -> Optional[str]:
        """Atomically claim an open Ticket.  Returns the ticket_id on success, None if already claimed.
        v079: sets last_activity on claim."""
        _log.debug("claim_ticket: plan=%s role=%s session=%s", plan_id, role, session_id)
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM tickets WHERE plan_id = %s AND role = %s AND status = 'open'",
                (plan_id, role),
            ).fetchone()
            if not row:
                _log.debug("claim_ticket: no open ticket found for plan=%s role=%s", plan_id, role)
                return None
            ticket_id = row[0]
            cursor = conn.execute(
                """
                UPDATE tickets SET
                    status = 'claimed', session_id = %s, claimed_at = %s,
                    last_activity = %s
                WHERE id = %s AND status = 'open'
                """,
                (session_id, now, now, ticket_id),
            )
            conn.commit()
            if cursor.rowcount > 0:
                _log.info("claim_ticket: claimed %s session=%s", ticket_id, session_id)
            else:
                _log.warning("claim_ticket: race lost for %s session=%s", ticket_id, session_id)
            return ticket_id if cursor.rowcount > 0 else None

    def close_ticket(
        self, plan_id: str, role: str, session_id: str, terminal_status: str = "completed"
    ) -> bool:
        """Close a claimed Ticket into a terminal state.  v079: sets last_activity."""
        _log.debug("close_ticket: plan=%s role=%s session=%s status=%s", plan_id, role, session_id, terminal_status)
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE tickets SET
                    status = %s, closed_at = %s, last_activity = %s
                WHERE plan_id = %s AND role = %s AND session_id = %s AND status = 'claimed'
                """,
                (terminal_status, now, now, plan_id, role, session_id),
            )
            conn.commit()
            if cursor.rowcount > 0:
                _log.info("close_ticket: closed ticket plan=%s role=%s as %s", plan_id, role, terminal_status)
            return cursor.rowcount > 0

    def release_ticket(self, plan_id: str, role: str, session_id: str) -> bool:
        """Release a claimed Ticket back to 'open'.  v079: sets last_activity."""
        _log.debug("release_ticket: plan=%s role=%s session=%s", plan_id, role, session_id)
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE tickets SET
                    status = 'open', session_id = NULL, claimed_at = NULL,
                    last_activity = %s
                WHERE plan_id = %s AND role = %s AND session_id = %s AND status = 'claimed'
                """,
                (now, plan_id, role, session_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def abandon_ticket(self, plan_id: str, role: str, session_id: str) -> bool:
        """Mark a claimed Ticket as abandoned.  v079: sets last_activity."""
        _log.info("abandon_ticket: plan=%s role=%s session=%s", plan_id, role, session_id)
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE tickets SET
                    status = 'abandoned', closed_at = %s, last_activity = %s
                WHERE plan_id = %s AND role = %s AND session_id = %s AND status = 'claimed'
                """,
                (now, now, plan_id, role, session_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def release_session_tickets(self, session_id: str) -> int:
        """Release all Tickets claimed by *session_id* back to 'open'.  v079: sets last_activity."""
        _log.debug("release_session_tickets: session=%s", session_id)
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE tickets SET
                    status = 'open', session_id = NULL, claimed_at = NULL,
                    last_activity = %s
                WHERE session_id = %s AND status = 'claimed'
                """,
                (now, session_id),
            )
            conn.commit()
            n = cursor.rowcount
            if n:
                _log.info("release_session_tickets: released %d tickets for session=%s", n, session_id)
            return n

    # ── Invariant 5: next-Ticket creation (v079) ─────────────────

    def create_next_tickets(
        self, plan_id: str, ticket_role: str, terminal_status: str,
        parent_ticket_id: str = "", objective: str = "",
        completion_criteria: str = "", owner: str = "",
    ) -> int:
        """After a Ticket reaches a terminal state, spawn the next Ticket(s).

        Deterministic mapping:
          builder completed  → reviewer
          builder failed     → (nothing — plan blocked)
          reviewer completed → (nothing — terminal REVIEW_PASS)
          reviewer failed    → builder (re-implementation)
          planner completed  → builder + critic
          critic completed   → builder  [DEPRECATED — see below]

        Guard: if the plan's derived_status is already terminal (REVIEW_PASS,
        BLOCK, PLAN_BLOCK), no new tickets are spawned regardless of role
        transition.  This prevents late-arriving ticket completions (e.g.
        critic finishing after the plan was already REVIEW_PASS) from
        dispatching unnecessary work.

        DEPRECATION (architect, 2026-09-05, plan 0016 reconciliation): the
        critic fan-out here is NOT position-aware and is superseded by the
        MCP issue_receipt path (TS advanceTicketsOnReceipt → createNextTickets,
        which resolves admission vs artifact from the last non-CRITIQUE
        receipt). The only live caller of this method is the execution worker
        (execution_worker.py) with ROLE="builder", so the critic/planner
        branches below are currently unreachable in production. Keep them
        (harmless) or delete when the ticket/receipt/claims service lands;
        do NOT wire new callers onto the critic branch here.
        """
        _log.info("create_next_tickets: plan=%s ticket_role=%s terminal_status=%s", plan_id, ticket_role, terminal_status)
        with self._get_connection() as conn:
            latest = conn.execute(
                """
                SELECT type FROM nebula.receipts_unified
                WHERE plan_id = %s
                ORDER BY created_at DESC LIMIT 1
                """,
                (plan_id,),
            ).fetchone()
            if latest and latest[0] in ('REVIEW_PASS', 'BLOCK', 'PLAN_BLOCK'):
                _log.info("create_next_tickets: guard — plan %s latest receipt is %s, skipping", plan_id, latest[0])
                # v104: Close orphaned tickets when the plan is in a terminal state
                self.close_orphaned_tickets(plan_id)
                return 0

        next_roles: list[str] = []

        if terminal_status == "completed":
            if ticket_role == "builder":
                next_roles = ["reviewer"]
            elif ticket_role == "planner":
                next_roles = ["builder", "critic"]
            elif ticket_role == "critic":
                # DEPRECATED (architect 2026-09-05, plan 0016): not
                # position-aware; superseded by MCP advanceTicketsOnReceipt.
                # Unreachable today (live caller is execution_worker builder).
                next_roles = ["builder"]
        elif terminal_status == "failed":
            if ticket_role == "reviewer":
                next_roles = ["builder"]
            elif ticket_role == "planner":
                next_roles = ["planner"]
            elif ticket_role == "critic":
                next_roles = ["planner"]

        if not next_roles:
            _log.debug("create_next_tickets: no next roles for %s %s", ticket_role, terminal_status)
            return 0

        _log.debug("create_next_tickets: spawning next roles: %s", next_roles)

        # v104: Close tickets for roles that are no longer eligible given
        # the plan's current derived_status. This prevents stale tickets
        # (e.g. critic ticket surviving after builder issues BLOCK) from
        # dispatching work on a plan whose state has moved on.
        self.close_orphaned_tickets(plan_id)

        now = datetime.utcnow().isoformat() + "Z"
        expires_at = (datetime.utcnow() + timedelta(hours=DEFAULT_TICKET_TTL_HOURS)).isoformat() + "Z"
        count = 0
        with self._get_connection() as conn:
            for role in next_roles:
                ticket_id = f"ticket-{plan_id}-{role}-{int(datetime.utcnow().timestamp())}-{uuid.uuid4().hex[:8]}"
                spawn_reason = f"{ticket_role} {terminal_status} → {role}"
                cursor = conn.execute(
                    """
                    INSERT INTO tickets
                        (id, plan_id, role, status, created_at,
                         objective, completion_criteria, owner,
                         parent_ticket_id, spawn_reason,
                         last_activity, expires_at)
                    VALUES (%s, %s, %s, 'open', %s,
                            %s, %s, %s,
                            %s, %s,
                            %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (ticket_id, plan_id, role, now,
                     objective or "", completion_criteria or "", owner or role,
                     parent_ticket_id or None, spawn_reason,
                     now, expires_at),
                )
                if cursor.rowcount > 0:
                    count += 1
            conn.commit()
        _log.info("create_next_tickets: created %d ticket(s) for plan=%s roles=%s", count, plan_id, next_roles)
        return count

    # ── Eligibility (v079 — ticket-driven, excludes stale/expired) ──

    # v104: Each role's valid derived_status values for eligibility.
    # Prevents roles from picking up plans whose receipt-chain state
    # no longer matches what the role should process.
    _ROLE_DERIVED_STATUS_MAP: dict[str, tuple[str, ...]] = {
        'builder': ('PLAN_CREATE', 'REVIEW_REJECT', 'CRITIQUE_PASS'),
        'reviewer': ('IMPLEMENTATION',),
        'planner': ('PROPOSED', 'PLANNING'),
        'critic': ('PLAN_CREATE',),
    }

    def get_eligible_plans(self, role: str) -> List[Dict[str, Any]]:
        """Query plans that have an open, non-stale, non-expired Ticket for the given role
        AND whose derived_status matches the role's eligibility."""
        _log.debug("get_eligible_plans: role=%s", role)
        valid_statuses = self._ROLE_DERIVED_STATUS_MAP.get(role, ())
        if not valid_statuses:
            return []

        placeholders = ', '.join(['%s'] * len(valid_statuses))

        if role == "reviewer":
            query = f"""
                SELECT ps.* FROM plan_status ps
                JOIN tickets t ON t.plan_id = ps.id
                WHERE t.role = 'reviewer' AND t.status = 'open'
                AND ps.derived_status IN ({placeholders})
                AND t.created_at::timestamptz <= NOW() - INTERVAL '60 seconds'
                ORDER BY ps.created_at ASC
            """
        else:
            query = f"""
                SELECT ps.* FROM plan_status ps
                JOIN tickets t ON t.plan_id = ps.id
                WHERE t.role = %s AND t.status = 'open'
                AND ps.derived_status IN ({placeholders})
                ORDER BY ps.created_at ASC
            """

        with self._get_connection() as conn:
            if role == "reviewer":
                cursor = conn.execute(query, valid_statuses)
            else:
                cursor = conn.execute(query, (role, *valid_statuses))
            plans = cursor.dict_fetchall()
            _log.debug("get_eligible_plans: role=%s returned %d plans", role, len(plans))
            return plans

    def get_blocked_plans(self) -> List[Dict[str, Any]]:
        _log.debug("get_blocked_plans")
        query = "SELECT * FROM plan_status WHERE derived_status = 'BLOCK'"
        with self._get_connection() as conn:
            cursor = conn.execute(query)
            plans = cursor.dict_fetchall()
            if plans:
                _log.info("get_blocked_plans: found %d blocked plan(s)", len(plans))
            return plans

    def is_circuit_breaker_tripped(self) -> bool:
        query = "SELECT tripped FROM circuit_breaker WHERE id = 1"
        try:
            with self._get_connection() as conn:
                row = conn.execute(query).fetchone()
                if row is not None and row[0] is not None:
                    tripped = bool(row[0])
                else:
                    tripped = False
                _log.debug("is_circuit_breaker_tripped: %s", tripped)
                return tripped
        except Exception as exc:
            _log.warning("is_circuit_breaker_tripped: query failed: %s", exc)
            return False

    def is_conduit_paused(self) -> bool:
        query = "SELECT paused FROM circuit_breaker WHERE id = 1"
        try:
            with self._get_connection() as conn:
                row = conn.execute(query).fetchone()
                if row is not None and row[0] is not None:
                    paused = bool(row[0])
                else:
                    paused = False
                _log.debug("is_conduit_paused: %s", paused)
                return paused
        except Exception as exc:
            _log.warning("is_conduit_paused: query failed: %s", exc)
            return False

    def get_last_session_activity(self, session_id: str) -> Optional[str]:
        _log.debug("get_last_session_activity: session=%s", session_id)
        query = "SELECT last_activity FROM sessions WHERE id = %s"
        with self._get_connection() as conn:
            row = conn.execute(query, (session_id,)).fetchone()
            return row[0] if row else None

    def update_session_activity(self, session_id: str, pid: Optional[int] = None):
        _log.debug("update_session_activity: session=%s pid=%s", session_id, pid)
        now = datetime.utcnow().isoformat() + "Z"
        query = "UPDATE sessions SET last_activity = %s"
        params = [now]
        if pid is not None:
            query += ", pid = %s"
            params.append(pid)
        query += " WHERE id = %s"
        params.append(session_id)
        with self._get_connection() as conn:
            conn.execute(query, tuple(params))
            conn.commit()

    def add_session_work_time(self, session_id: str, work_seconds: float) -> None:
        _log.debug("add_session_work_time: session=%s work_seconds=%.1f", session_id, work_seconds)
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE sessions SET total_work_seconds = COALESCE(total_work_seconds, 0) + %s WHERE id = %s",
                (work_seconds, session_id),
            )
            conn.commit()

    # ── Receipts (v078 — ticket_id required) ─────────────────────

    @staticmethod
    def _receipt_provenance(
        correlation_id: Optional[str] = None,
        producer_id: Optional[str] = None,
        source_channel: Optional[str] = None,
    ) -> Dict[str, str]:
        """C1 gate 1 (Lilac plan 8261639): receipt-write provenance stamp.

        Target shapes follow the C2 contract draft (architect record
        2026-09-06): source_channel / source_system / producer identity,
        process + invocation identity, and contract_version. Correlation ID
        is caller-supplied when available (invocation context), otherwise
        generated once per receipt. Invoked lazily at write time.

        Identity split: ``producer_id``/``source_channel`` carry the
        declaring write authority (e.g. the TS front-door when it declares
        itself via the REST body), while ``process_id``/``invocation_id``
        always describe the process that physically performed the write."""
        return {
            "producer_id": producer_id or "nexus-conduit-python",
            "source_channel": source_channel or "conduit-python",
            "source_system": "conduit",
            "process_id": str(os.getpid()),
            "invocation_id": _INVOCATION_ID,
            "contract_version": "1",
            "correlation_id": correlation_id or str(uuid.uuid4()),
        }

    @staticmethod
    def _enforce_canary_policy(plan_id: str, fallback: bool, metadata: Dict[str, Any]) -> None:
        """C1 gate 3 (Lilac plan 8261639): fail-closed canary enforcement.

        Policy (env-gated; default OFF so the legacy fallback keeps working
        until C2 ratifies the producer registry):
        - ``CONDUIT_CANARY_ENFORCEMENT=1`` blocks undeclared synthetic writes
          to the frozen ``vision.receipts`` surface entirely.
        - ``CONDUIT_CANARY_ENFORCEMENT=log`` keeps the fallback open but logs
          every synthetic write with its declared canary identity.
        Synthetic writes declaring themselves via ``metadata['canary']=true``
        plus a ``canary_correlation_id`` are always permitted so the C1 canary
        procedure stays available in enforce mode."""
        mode = os.environ.get("CONDUIT_CANARY_ENFORCEMENT", "").strip().lower()
        if not fallback or not mode:
            return
        declared = bool(metadata.get("canary")) and bool(metadata.get("canary_correlation_id"))
        if declared:
            _log.info(
                "canary write declared: plan=%s canary_correlation_id=%s",
                plan_id, metadata.get("canary_correlation_id"),
            )
            return
        if mode == "log":
            _log.warning(
                "UNDECLARED synthetic receipt write (canary policy=log): "
                "plan=%s fallback target=vision.receipts",
                plan_id,
            )
            return
        raise PermissionError(
            f"C1 canary policy: undeclared synthetic receipt write refused "
            f"(plan_id={plan_id}, target=vision.receipts). Declare it by "
            f"setting metadata canary=true with canary_correlation_id, or "
            f"disable enforcement via CONDUIT_CANARY_ENFORCEMENT."
        )

    # ── C3 cutover (Lilac plan 8261639): staged writer redirection ──

    @staticmethod
    def _receipt_redirect_mode() -> str:
        """Parse CONDUIT_RECEIPT_REDIRECT (off | shadow | enforce).

        Fail-safe: unset or unknown values mean OFF — the legacy behavior
        is never silently changed by a typo. Stages per the writer
        redirection design (engineer record 281510c7):
          off     legacy write; canonical only via CONDUIT_LILAC_SHADOW seam
          shadow  legacy write + canonical record forced on for this write
          enforce canonical write FIRST (R4 outcomes gate the write);
                  the legacy synthetic courtesy copy is SKIPPED for
                  in-scope producers once the canonical write commits
                  (R2, To Do ca5941ca: legacy stays flat so the Stage D
                  freeze gate observes zero direct writers); unmapped
                  kinds stay legacy-only by contract
        """
        raw_mode = os.environ.get("CONDUIT_RECEIPT_REDIRECT", "off").strip().lower()
        if raw_mode not in ("off", "shadow", "enforce"):
            _log.warning(
                "CONDUIT_RECEIPT_REDIRECT=%r is not off|shadow|enforce — treating as off",
                raw_mode,
            )
            return "off"
        return raw_mode

    @staticmethod
    def _redirect_applies_to(producer_id: Optional[str]) -> bool:
        """Q-A blast-radius containment: CONDUIT_RECEIPT_REDIRECT_PRODUCERS
        (comma-separated) restricts the redirect to the listed producers.
        Unset → applies to all producers. The producer_registry grant
        trigger remains the real per-write authority in every mode."""
        raw = os.environ.get("CONDUIT_RECEIPT_REDIRECT_PRODUCERS", "").strip()
        if not raw:
            return True
        allowed = {p.strip() for p in raw.split(",") if p.strip()}
        return (producer_id or "") in allowed

    def insert_receipt(
        self,
        plan_id: str,
        receipt_type: str,
        agent_role: str,
        session_id: str,
        ticket_id: str,
        summary: str = "",
        artifact_path: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        tokens_used: int = 0,
        correlation_id: Optional[str] = None,
        receipt_id: Optional[str] = None,
    ):
        """Insert a receipt. D-T19-2(b): canonical surface is execution.receipts
        (request-scoped, attempt_id NULL). Falls back to vision.receipts for
        test/synthetic plans that have no execution.requests row and no
        nebula.plans row.

        C1 gate 2 (Lilac plan 8261639): single canonical identity contract for
        BOTH channels — ``receipt_id`` is honored verbatim when supplied
        (HTTP front-door path passes the caller's id through), otherwise the
        adapter derives ``rec-<plan>-<type>-<12 hex>`` exactly as the HTTP
        path's contract specifies.

        C1 gate 4 (Lilac plan 8261639) — vision.receipts fallback
        reconciliation: per architect ruling Q1, ``nebula.receipts_unified``
        is a VIEW = C5 projection surface over ``resolution.receipt`` (the
        canonical kind-discriminated receipt stream). The frozen
        ``vision.receipts`` surface remains the synthetic/standalone write
        target ONLY until C4 executes the import; at that point each
        legacy row is copied into ``resolution.receipt`` with an explicit
        ``resolution.migration_disposition`` (source schema/table/PK →
        target ref) — no silent joins, per lineage discipline R5. The
        dual write policy (request-backed → execution.receipts, synthetic →
        vision.receipts) is eliminated at C3 cutover when the Python builder
        path is redirected through the Lilac adapter; provenance stamped
        here (gate 1) is what makes each row's migration disposition
        derivable without guesswork."""
        _log.info("insert_receipt: plan=%s type=%s role=%s ticket=%s tokens=%d",
                  plan_id, receipt_type, agent_role, ticket_id, tokens_used)
        now = datetime.utcnow().isoformat() + "Z"
        # C1 gate 2 (Lilac plan 8261639): the direct Python path now derives
        # its identity from the same canonical scheme as the HTTP path —
        # caller-supplied id is honored verbatim; otherwise
        # rec-<plan>-<type>-<12 hex>. Provenance fields make the two paths
        # distinguishable while keeping the identity contract identical.
        receipt_id = receipt_id or f"rec-{plan_id}-{receipt_type}-{uuid.uuid4().hex[:12]}"
        metadata = dict(metadata or {})
        # Declaring-producer overrides (REST front-door path); stripped so
        # they are not duplicated verbatim inside the metadata blob.
        producer_id = metadata.pop("producer_id", None)
        source_channel = metadata.pop("source_channel", None)
        provenance = self._receipt_provenance(
            correlation_id=correlation_id,
            producer_id=producer_id,
            source_channel=source_channel,
        )
        metadata.update(provenance)

        # C3 cutover (Lilac plan 8261639): staged writer redirection.
        # See _receipt_redirect_mode for the stage contract. The producer
        # allowlist (Q-A) degrades the mode to off for non-listed producers.
        from lilac import receipt_type_to_kind
        redirect_mode = self._receipt_redirect_mode()
        if redirect_mode != "off" and not self._redirect_applies_to(
                provenance.get("producer_id")):
            _log.info(
                "redirect=%s not applicable to producer %s — mode off",
                redirect_mode, provenance.get("producer_id"))
            redirect_mode = "off"

        if redirect_mode == "enforce":
            kind = receipt_type_to_kind(receipt_type)
            # R2 (To Do ca5941ca): once the canonical write commits for an
            # in-scope producer, the legacy synthetic courtesy copy is
            # skipped — the Stage D freeze gate requires zero direct
            # writers on vision.receipts. Unmapped kinds and out-of-scope
            # producers keep the legacy path (see branches below).
            canonical_redirect_written = False
            if kind is None:
                # Unmapped legacy type (e.g. PROPOSED): no ratified canonical
                # kind exists — legacy-only by contract (drift fixture class
                # unmapped_type). NOT a refusal; documented divergence.
                _log.warning(
                    "redirect=enforce: receipt type %s is unmapped to a "
                    "canonical kind — legacy-only write", receipt_type)
            else:
                request_id = self.resolve_request_for_receipt(plan_id)
                canonical_payload = {
                    "plan_id": plan_id,
                    "receipt_type": receipt_type,
                    "agent_role": agent_role,
                    "session_id": session_id,
                    "ticket_id": ticket_id,
                    "summary": summary,
                    "artifact_path": artifact_path,
                    "tokens_used": tokens_used,
                    "metadata": metadata,
                    "request_id": request_id,
                    "producer_id": provenance["producer_id"],
                    "source_channel": provenance["source_channel"],
                    "correlation_id": provenance["correlation_id"],
                }
                from lilac import LilacAdapter, LilacPersistenceError
                try:
                    with self._get_connection() as conn:
                        raw_conn = getattr(conn, "_conn", conn)
                        lilac_adapter = LilacAdapter(
                            lambda: raw_conn,
                            schema=os.environ.get("CONDUIT_LILAC_SCHEMA", "resolution"),
                            # The declaring producer writes canonically —
                            # the grant trigger validates it per write.
                            producer_id=provenance["producer_id"] or "nexus-conduit-python",
                        )
                        outcome, canonical_id = lilac_adapter.insert_receipt(
                            raw_conn,
                            kind=kind,
                            source_receipt_id=receipt_id,
                            payload=canonical_payload,
                            refs={"plan_id": plan_id,
                                  "ticket_id": ticket_id or None,
                                  "request_id": request_id},
                            source_system="conduit",
                        )
                    _log.info(
                        "redirect=enforce: canonical %s accepted (outcome=%s "
                        "id=%s) — caller-owned ticket fan-out proceeds off "
                        "nebula.receipts_unified (V140 canonical branch "
                        "surfaces rows with no legacy twin)",
                        kind, outcome, canonical_id)
                    canonical_redirect_written = True
                except LilacPersistenceError as lex:
                    # R4 fail-closed: conflict / grant refusal must NOT fall
                    # back to a legacy-only write — that would fork the
                    # stream. The refusal message carries both fingerprints.
                    _log.exception(
                        "redirect=enforce: canonical write refused — failing closed")
                    self._record_producer_refusal(
                        provenance.get("producer_id") or "nexus-conduit-python",
                        receipt_type, receipt_id, plan_id, lex)
                    raise

        try:
            request_id = self.resolve_request_for_receipt(plan_id)
            if request_id is not None:
                # C1 gate 3 (Lilac plan 8261639): canary policy only governs
                # the frozen synthetic surface; request-backed canonical
                # writes are unaffected.
                self._enforce_canary_policy(plan_id, fallback=False, metadata=metadata)
                # Canonical: execution.receipts, request-scoped, no attempt.
                # The legacy "rec-*" id is preserved in lineage_original_id so
                # tickets.created_by_receipt and other legacy lookups keep working.
                # C1 gate 1: provenance rides inside metadata (no DDL before
                # C2 ratifies resolution.receipt / producer_registry).
                exec_meta = {
                    **metadata,
                    "session_id": session_id,
                    "artifact_path": artifact_path,
                    "ticket_id": ticket_id,
                    "tokens_used": tokens_used,
                }
                with self._get_connection() as conn:
                    conn.execute(
                        """INSERT INTO execution.receipts
                           (request_id, attempt_id, type, agent_role, summary, metadata,
                            lineage_source, lineage_original_id, issued_at)
                           VALUES (%s, NULL, %s, %s, %s, %s, 'conduit', %s, %s)
                           ON CONFLICT (lineage_original_id) WHERE lineage_source = 'conduit' DO NOTHING""",
                        (request_id, receipt_type, agent_role, summary,
                         json.dumps(exec_meta), receipt_id, now),
                    )
                    conn.commit()
            else:
                # C1 gate 3: fail-closed canary enforcement on the synthetic
                # surface (env-gated; no-op by default until C2 ratification).
                self._enforce_canary_policy(plan_id, fallback=True, metadata=metadata)
                # R2 (To Do ca5941ca): enforce-mode canonical success for an
                # in-scope producer skips the legacy courtesy copy — the
                # freeze gate observes zero direct writers. The canary
                # policy above still runs, so policy refusal keeps failing
                # closed even though no legacy row is attempted.
                if redirect_mode == "enforce" and canonical_redirect_written:
                    _log.info(
                        "redirect=enforce: canonical already committed — "
                        "skipping legacy courtesy copy id=%s plan=%s producer=%s",
                        receipt_id, plan_id, provenance["producer_id"],
                    )
                    return
                # Test/synthetic plan (no execution.requests + no nebula.plans row).
                # Preserve the legacy write surface — frozen read-only in D-T19-2(d).
                meta_json = json.dumps(metadata)
                query = """
                    INSERT INTO vision.receipts (id, plan_id, type, agent_role, session_id,
                        ticket_id, summary, artifact_path, metadata_json, tokens_used, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                """
                with self._get_connection() as conn:
                    conn.execute(query, (
                        receipt_id, plan_id, receipt_type, agent_role, session_id,
                        ticket_id, summary, artifact_path, meta_json, tokens_used, now,
                    ))
                    conn.commit()
                _log.info(
                    "insert_receipt: synthetic fallback id=%s plan=%s channel=%s producer=%s correlation=%s",
                    receipt_id, plan_id, provenance["source_channel"],
                    provenance["producer_id"], provenance["correlation_id"],
                )
        except Exception as e:
            if redirect_mode == "enforce":
                # Q-B asymmetry (ratified daae50b0): the canonical write
                # already committed — the operation SUCCEEDS. The legacy
                # courtesy copy's failure is never fatal, but it is NEVER
                # silent: it is recorded as the distinct
                # `legacy_shadow_failed` class (review F2) so the C6 soak
                # gate observes it. Ticket fan-out correctness does not
                # depend on the legacy row: advanceTicketsOnReceipt and
                # create_next_tickets both read nebula.receipts_unified,
                # whose canonical branch (V140) surfaces canonical rows
                # lacking a legacy twin (review F3).
                _log.warning(
                    "insert_receipt legacy shadow copy failed (non-fatal in "
                    "enforce mode) plan=%s type=%s: %s", plan_id, receipt_type, e)
                self._record_legacy_shadow_failed(
                    plan_id, receipt_type, receipt_id, e)
                return
            _log.error("insert_receipt failed plan=%s type=%s: %s", plan_id, receipt_type, e)
            self._emit_receipt_failure(plan_id, receipt_type, str(e))
            raise

        # C3 (Lilac plan 8261639) — STAGED canonical shadow write. Default
        # OFF; when CONDUIT_LILAC_SHADOW=1, record the canonical
        # resolution.receipt outcome alongside the legacy write as evidence
        # for the C2 ratification record. Never affects the legacy outcome,
        # never spawns tickets (the single live fan-out is unchanged).
        # In enforce mode the canonical row was already written above —
        # recording again would risk a fingerprint drift-conflict against
        # rows the legacy branches mutate via the canary policy.
        if redirect_mode == "enforce":
            return
        try:
            from lilac import shadow_record_receipt
            # Q3: the canonical stream lives in the fixed `resolution` schema
            # (overridable only for hermetic parity tests).
            shadow_record_receipt(
                self,
                schema=os.environ.get("CONDUIT_LILAC_SCHEMA", "resolution"),
                force=(redirect_mode == "shadow"),
                receipt_row={
                    "kind": receipt_type_to_kind(receipt_type),
                    "source_receipt_id": receipt_id,
                    "payload": {
                        "plan_id": plan_id,
                        "receipt_type": receipt_type,
                        "agent_role": agent_role,
                        "session_id": session_id,
                        "ticket_id": ticket_id,
                        "summary": summary,
                        "artifact_path": artifact_path,
                        "tokens_used": tokens_used,
                        "metadata": metadata,
                        "request_id": request_id,
                        "producer_id": provenance["producer_id"],
                        "source_channel": provenance["source_channel"],
                        "correlation_id": provenance["correlation_id"],
                    },
                    "refs": {
                        "plan_id": plan_id,
                        "ticket_id": ticket_id or None,
                        "request_id": request_id,
                    },
                },
            )
        except Exception:  # noqa: BLE001 — shadow plumbing must never break the legacy path
            _log.exception("lilac shadow hook failed (non-fatal)")

        _log.debug("insert_receipt: created %s (request=%s)", receipt_id, request_id)

    def _emit_receipt_failure(self, plan_id: str, receipt_type: str, error: str) -> None:
        """D-T19 item 5: emit a canonical receipt-failure event.

        V103 (D-T19-3) extended ``kernel.event_type`` with ``receipt.failed``,
        so receipt failures land in the DB as their own event_type instead of
        riding ``transition.rejected`` (which remains reserved for ticket
        expiry). Consumers disambiguate on event_type alone — no payload
        sniffing. Flows through trg_notify_transition onto the canonical NATS
        channel (nexus.kernel.v1.transition.receipt.failed).
        """
        try:
            with self._get_connection() as conn:
                self._record_kernel_transition(
                    conn,
                    aggregate_type="receipt",
                    aggregate_id=plan_id or "",
                    event_type="receipt.failed",
                    actor="conduit-python",
                    authority="system",
                    payload={
                        "failure_class": "receipt",
                        "plan_id": plan_id,
                        "receipt_type": receipt_type,
                        "error": error[:2000],
                    },
                )
                conn.commit()
        except Exception:
            _log.exception("_emit_receipt_failure: failed to record receipt failure")

    def _record_producer_refusal(
        self,
        producer_id: str,
        receipt_type: str,
        source_receipt_id: str,
        plan_id: str,
        error: Exception,
    ) -> None:
        """Stage C C2 gate (architect review d4c0a9ff), leg 1 feed:
        a redirect=enforce refusal of a REAL writer is durably recorded in
        resolution.producer_refusals (V145) so c2_trailing_gate() counts it.

        Canary writers (declared ``rec-zz-redirect-`` source_receipt_id
        prefix) are excluded here BY CONSTRUCTION — their refusals are
        canary evidence, not real-writer alerts. The record is best-effort:
        the raise below is the fail-closed contract; observability must not
        mask it.
        """
        if source_receipt_id.startswith("rec-zz-redirect-"):
            _log.info(
                "producer_refusal suppressed (declared canary id): producer=%s id=%s",
                producer_id, source_receipt_id)
            return
        sqlstate = getattr(error, "pgcode", None) or ""
        try:
            schema = os.environ.get("CONDUIT_LILAC_SCHEMA", "resolution")
            with self._get_connection() as conn:
                conn.execute(
                    f"""INSERT INTO {schema}.producer_refusals
                           (producer_id, receipt_type, source_receipt_id,
                            plan_id, sqlstate, error)
                        VALUES (%s, %s, %s, %s, %s, %s)""",
                    (producer_id, receipt_type, source_receipt_id,
                     plan_id, sqlstate, str(error)[:2000]),
                )
                conn.commit()
            _log.warning(
                "producer_refusal recorded (C2 leg 1) producer=%s type=%s id=%s state=%s",
                producer_id, receipt_type, source_receipt_id, sqlstate)
        except Exception:  # noqa: BLE001 — observability must never mask the raise
            _log.exception(
                "producer_refusal record failed (non-fatal) producer=%s id=%s",
                producer_id, source_receipt_id)

    def _record_legacy_shadow_failed(
        self,
        plan_id: str,
        receipt_type: str,
        source_receipt_id: str,
        error: Exception,
    ) -> None:
        """Q-B observability (architect review F2, record 761e6338):
        a failed legacy courtesy copy under redirect=enforce is recorded as
        the DISTINCT parity class ``legacy_shadow_failed`` — never a silent
        skip, never a conflict/refused. Durable event row in the V141 soak
        surface (resolution.soak_evidence, today's evidence day, green
        forced false for the day) so the C6 soak cron and the drift report
        see the failure instead of a clean scan. Best-effort: recording
        must never disturb the already-committed canonical write.
        """
        event = {
            "plan_id": plan_id,
            "receipt_type": receipt_type,
            "source_receipt_id": source_receipt_id,
            "error": str(error)[:2000],
            "recorded_at": datetime.utcnow().isoformat() + "Z",
        }
        # Fixed canonical schema in production (Q3); overridable only for
        # hermetic tests (same convention as the enforce/shadow paths).
        schema = os.environ.get("CONDUIT_LILAC_SCHEMA", "resolution")
        try:
            with self._get_connection() as conn:
                conn.execute(
                    f"""INSERT INTO {schema}.soak_evidence
                           (evidence_date, report, green, recorded_by)
                       VALUES (CURRENT_DATE, %s::jsonb, false,
                               'legacy_shadow_failed')
                       ON CONFLICT (evidence_date) DO UPDATE
                         SET report = jsonb_set(
                               {schema}.soak_evidence.report,
                               '{{legacy_shadow_failed}}',
                               COALESCE({schema}.soak_evidence.report
                                        -> 'legacy_shadow_failed',
                                        '[]'::jsonb) || %s::jsonb),
                             green = false""",
                    (json.dumps({"legacy_shadow_failed": [event]}),
                     json.dumps(event)),
                )
                conn.commit()
            _log.warning(
                "legacy_shadow_failed recorded (Q-B non-fatal class) "
                "plan=%s type=%s id=%s", plan_id, receipt_type, source_receipt_id)
        except Exception:  # noqa: BLE001 — observability must never raise
            _log.exception(
                "legacy_shadow_failed record failed (non-fatal) "
                "plan=%s id=%s", plan_id, source_receipt_id)

    def resolve_request_for_receipt(self, plan_id: str) -> Optional[str]:
        """D-T19-2(b): resolve (or get-or-create) the execution.requests id for a plan.

        Tiered per Architect decision:
          1. reuse an existing execution.requests row (source_plan_id match);
          2. get-or-create a legacy-provisioned request for a real plan
             (exists in nebula.plans);
          3. return None for test/synthetic plans (no nebula.plans row) —
             the caller falls back to vision.receipts.

        Idempotent + concurrent-safe via ON CONFLICT (business_key) RETURNING.
        Returns the request uuid (str), or None."""
        with self._get_connection() as conn:
            existing = conn.execute(
                "SELECT id FROM execution.requests WHERE source_plan_id = %s LIMIT 1",
                (plan_id,),
            ).dict_fetchone()
            if existing:
                return str(existing["id"])

            plan = conn.execute(
                "SELECT id, title, goal FROM nebula.plans WHERE id = %s LIMIT 1",
                (plan_id,),
            ).dict_fetchone()
            if plan is None:
                return None

            business_key = f"legacy-plan-{plan_id}"
            created = conn.execute(
                """INSERT INTO execution.requests
                   (business_key, title, objective, intent_type,
                    source_plan_id, source_wr_id, status)
                   VALUES (%s, %s, %s, 'legacy', %s, NULL, 'READY')
                   ON CONFLICT (business_key) DO UPDATE
                     SET business_key = EXCLUDED.business_key
                   RETURNING id""",
                (business_key,
                 plan.get("title") or f"Legacy plan {plan_id}",
                 plan.get("goal") or "",
                 plan_id),
            ).dict_fetchone()
            conn.commit()
            return str(created["id"]) if created else None

    def add_work_request(self, wr_id: str, plan_id: str, dco_json: str, title: str = ''):
        """Insert a work request into resolution.work_request (canonical).

        W3 repoint (plan 8261650 stage 2, spec 7e8c0222) — was writing to
        nebula.work_requests_history; now writes the canonical row. Per DBA
        disposition 767abf1b:
          - plan_id is NOT written to resolution.work_request.plan_id (FK to
            resolution.implementation_plan, which stays empty through Stage 2 —
            the Stage-3 blueprint migration is the authorized path for plan
            rows). The real plan id is preserved at context.plan_id, matching
            the V186 backfill convention.
          - idempotency is a legacy_id existence-check inside the same
            transaction (indexed: idx_resolution_work_request_legacy_id). The
            T26 entity_key is preserved into context (entity_key) for
            traceability during Stages 2-6, but is NOT a column.

        Args:
            wr_id: Legacy TEXT ID (e.g., wr-0130-1781781240) - stored in legacy_id column
            plan_id: Implementation plan ID - preserved at context.plan_id (not the column)
            dco_json: Decomposition Command Object JSON (full DCO dump)
            title: Work request title
        """
        import uuid
        import json as _json
        _log.info("add_work_request: wr=%s plan=%s title=%s", wr_id, plan_id, title or '(empty)')
        now = datetime.utcnow().isoformat() + "Z"
        # T26 Item B (T07 emission boundary): derive the entity_key at birth so
        # the context carries content identity from creation. Preserved into
        # context only — the canonical table has no entity_key column.
        entity_key = derive_wr_entity_key(dco_json, wr_id)
        # Build the canonical context JSONB: intent + constraints from the DCO,
        # plus plan_id (disposition 767abf1b) + entity_key + wr identity.
        try:
            dco = _json.loads(dco_json) if dco_json else {}
        except ValueError:
            dco = {}
        context = {}
        if isinstance(dco.get("intent"), dict):
            context["intent"] = dco["intent"]
        if isinstance(dco.get("constraints"), dict):
            context["constraints"] = dco["constraints"]
        if plan_id:
            context["plan_id"] = plan_id
        if entity_key:
            context["entity_key"] = entity_key
        context["work_request_uuid"] = wr_id
        with self._get_connection() as conn:
            # Idempotency: legacy_id existence-check (disposition 767abf1b).
            # Same transaction as the INSERT. No entity_key column, no
            # entity_key active-row dedup in the canonical path.
            existing = conn.execute(
                "SELECT 1 FROM resolution.work_request "
                "WHERE legacy_id = %s LIMIT 1",
                (wr_id,),
            ).fetchone()
            if existing:
                _log.info("add_work_request: wr=%s already exists, skipping", wr_id)
            else:
                conn.execute(
                    "INSERT INTO resolution.work_request "
                    "(id, title, business_status, intent, context, constraints, "
                    "dco_json, legacy_id, plan_id, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, %s)",
                    (str(uuid.uuid4()), title, 'DRAFT',
                     (dco.get("intent") or {}).get("type"),
                     _json.dumps(context), _json.dumps(dco.get("constraints") or {}),
                     dco_json, wr_id, now, now),
                )
            conn.commit()

    def update_work_request_status(self, wr_id: str, status: str):
        """Update work request business_status by legacy_id (TEXT ID).

        W3 repoint (plan 8261650 stage 2, spec 7e8c0222) — was updating
        nebula.work_requests; now updates resolution.work_request (canonical)
        by legacy_id. Mapping unchanged.

        Maps conduit statuses to business statuses:
        - pending → DRAFT
        - completed → DISPATCHED (and sets consumed_at)
        - failed → CANCELLED
        - rate_limited → DRAFT
        """
        # Map conduit status to business status
        status_map = {
            'pending': 'DRAFT',
            'completed': 'DISPATCHED',
            'failed': 'CANCELLED',
            'rate_limited': 'DRAFT',
        }
        business_status = status_map.get(status, status)
        
        _log.debug("update_work_request_status: wr=%s conduit=%s business=%s", wr_id, status, business_status)
        now = datetime.utcnow().isoformat() + "Z"
        
        with self._get_connection() as conn:
            if business_status == 'DISPATCHED':
                # Set consumed_at when work is dispatched to execution
                conn.execute(
                    "UPDATE resolution.work_request SET business_status = %s, consumed_at = %s, updated_at = %s WHERE legacy_id = %s",
                    (business_status, now, now, wr_id)
                )
            else:
                conn.execute(
                    "UPDATE resolution.work_request SET business_status = %s, updated_at = %s WHERE legacy_id = %s",
                    (business_status, now, wr_id)
                )
            conn.commit()

    def get_active_session(self, agent_role: str) -> Optional[Dict[str, Any]]:
        _log.debug("get_active_session: role=%s", agent_role)
        query = "SELECT * FROM sessions WHERE agent_role = %s AND is_running = 1 LIMIT 1"
        with self._get_connection() as conn:
            cursor = conn.execute(query, (agent_role,))
            session = cursor.dict_fetchone()
            _log.debug("get_active_session: role=%s found=%s", agent_role, session is not None)
            return session

    def get_all_active_sessions(self) -> List[Dict[str, Any]]:
        query = "SELECT * FROM sessions WHERE is_running = 1 ORDER BY start_iso ASC"
        with self._get_connection() as conn:
            cursor = conn.execute(query)
            sessions = cursor.dict_fetchall()
            _log.debug("get_all_active_sessions: count=%d", len(sessions))
            return sessions

    def trip_circuit_breaker(
        self, error: str, detail: str = "", source: str = "orchestrator", retry_after: int = 1800,
    ) -> None:
        _log.warning("trip_circuit_breaker: error=%s source=%s retry_after=%d", error, source, retry_after)
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE circuit_breaker SET tripped=1, tripped_at=%s, retry_after=%s,
                    error=%s, detail=%s, source=%s, updated_at=%s
                WHERE id=1
                """,
                (now, retry_after, error, detail, source, now),
            )
            conn.commit()

    def set_conduit_paused(self, paused: bool) -> None:
        _log.info("set_conduit_paused: paused=%s", paused)
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            conn.execute("UPDATE circuit_breaker SET paused=%s, updated_at=%s WHERE id=1", (1 if paused else 0, now))
            conn.commit()

    def delete_receipt(self, plan_id: str, receipt_type: str, session_id: str) -> bool:
        _log.debug("delete_receipt: plan=%s type=%s session=%s", plan_id, receipt_type, session_id)
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM vision.receipts WHERE plan_id=%s AND type=%s AND session_id=%s", (plan_id, receipt_type, session_id))
            conn.commit()
            deleted = cursor.rowcount > 0
            if deleted:
                _log.info("delete_receipt: deleted plan=%s type=%s", plan_id, receipt_type)
            return deleted

    def get_plan_by_id(self, plan_id: str) -> Optional[Dict[str, Any]]:
        _log.debug("get_plan_by_id: plan=%s", plan_id)
        with self._get_connection() as conn:
            # conduit.plans dropped 2026-08-02 — nebula.plans is the
            # legacy-compat VIEW over nebula.implementation_plans (canonical).
            cursor = conn.execute("SELECT * FROM nebula.plans WHERE id = %s", (plan_id,))
            plan = cursor.dict_fetchone()
            _log.debug("get_plan_by_id: plan=%s found=%s", plan_id, plan is not None)
            return plan

    def create_session(self, session_id: str, agent_role: str, plan_ids: List[str], pid: Optional[int] = None):
        _log.info("create_session: session=%s role=%s plans=%s pid=%s", session_id, agent_role, plan_ids, pid)
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO sessions (id, agent_role, start_iso, plans_processed, plan_count, is_running, created_at, last_activity, pid) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (session_id, agent_role, now, json.dumps(plan_ids), len(plan_ids), 1, now, now, pid),
            )
            conn.commit()

    def close_session(self, session_id: str, exit_code: int):
        """Close a session.  Caller must release Tickets before calling."""
        _log.info("close_session: session=%s exit_code=%d", session_id, exit_code)
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE sessions SET end_iso=%s, is_running=0, exit_code=%s, last_activity=%s WHERE id=%s",
                (now, exit_code, now, session_id),
            )
            conn.commit()

    # ── v079: Stale / expired detection ──────────────────────────

    def increment_ticket_tokens(self, ticket_id: str, tokens: int) -> None:
        _log.debug("increment_ticket_tokens: ticket=%s tokens=%d", ticket_id, tokens)
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE tickets SET tokens_used = COALESCE(tokens_used, 0) + %s WHERE id = %s",
                (tokens, ticket_id),
            )
            conn.commit()

    # ── ADR-016: Kernel transition recording (Python side) ──────────

    def _record_kernel_transition(
        self,
        conn,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        actor: str = "conduit-python",
        authority: str = "system",
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Insert into kernel.transition_event within an existing transaction.

        ADR-016: Python conduit must record kernel transitions for every
        state change so the trg_authorize_trigger enforces policy rules.
        This method is called within the same connection/transaction as the
        UPDATE it accompanies, ensuring atomicity.
        """
        event_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO kernel.transition_event
                (event_id, event_type, aggregate_type, aggregate_id,
                 actor, authority, payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                event_id,
                event_type,
                aggregate_type,
                aggregate_id,
                actor,
                authority,
                json.dumps(payload or {}),
            ),
        )

    def detect_stale_tickets(self) -> int:
        threshold = (datetime.utcnow() - timedelta(seconds=DEFAULT_STALE_SECONDS)).isoformat() + "Z"
        with self._get_connection() as conn:
            # ADR-016: SELECT affected tickets before UPDATE for kernel transition recording
            affected = conn.execute(
                "SELECT id FROM tickets WHERE status = 'claimed' AND last_activity IS NOT NULL AND last_activity < %s",
                (threshold,),
            ).fetchall()
            cursor = conn.execute(
                """
                UPDATE tickets SET status = 'stale'
                WHERE status = 'claimed'
                AND last_activity IS NOT NULL
                AND last_activity < %s
                """,
                (threshold,),
            )
            # ADR-016: Record kernel transitions within the same transaction
            for row in (affected or []):
                self._record_kernel_transition(
                    conn,
                    aggregate_type="ticket",
                    aggregate_id=row[0],
                    event_type="transition.requested",
                    payload={"from_status": "claimed", "to_status": "stale", "reason": "stale_detection"},
                )
            conn.commit()
            n = cursor.rowcount
            if n:
                _log.info("detect_stale_tickets: marked %d ticket(s) as stale", n)
            return n

    def detect_expired_tickets(self) -> int:
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            # ADR-016: SELECT affected tickets before UPDATE for kernel transition recording
            affected = conn.execute(
                "SELECT id, status FROM tickets WHERE status IN ('open', 'claimed', 'stale') AND expires_at IS NOT NULL AND expires_at < %s",
                (now,),
            ).fetchall()
            cursor = conn.execute(
                """
                UPDATE tickets SET status = 'expired'
                WHERE status IN ('open', 'claimed', 'stale')
                AND expires_at IS NOT NULL
                AND expires_at < %s
                """,
                (now,),
            )
            # ADR-016: Record kernel transitions within the same transaction
            for row in (affected or []):
                self._record_kernel_transition(
                    conn,
                    aggregate_type="ticket",
                    aggregate_id=row[0],
                    event_type="transition.rejected",
                    payload={"from_status": row[1], "to_status": "expired", "reason": "expiry_detection"},
                )
            conn.commit()
            n = cursor.rowcount
            if n:
                _log.info("detect_expired_tickets: expired %d ticket(s)", n)
            return n

    # ── v080: Supersede / cancel ticket actions ─────────────────

    def supersede_ticket(self, ticket_id: str, reason: str = "") -> Dict[str, Any]:
        _log.info("supersede_ticket: ticket=%s reason=%s", ticket_id, reason)
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            old = conn.execute(
                "SELECT plan_id, role, objective, owner FROM tickets WHERE id = %s AND status IN ('open', 'claimed', 'stale')",
                (ticket_id,),
            ).fetchone()
            if not old:
                _log.debug("supersede_ticket: ticket %s not found or not in eligible status", ticket_id)
                return {"superseded": False}

            conn.execute(
                """
                UPDATE tickets SET
                    status = 'superseded', closed_at = %s, last_activity = %s,
                    closure_reason = %s
                WHERE id = %s
                AND status IN ('open', 'claimed', 'stale')
                """,
                (now, now, reason or "superseded", ticket_id),
            )
            conn.commit()
            _log.info("supersede_ticket: superseded %s (plan=%s role=%s)", ticket_id, old[0], old[1])
            return {
                "superseded": True,
                "old_ticket": {
                    "plan_id": old[0],
                    "role": old[1],
                    "objective": old[2],
                    "owner": old[3] or "",
                },
            }

    def cancel_ticket(self, ticket_id: str, reason: str = "") -> int:
        _log.info("cancel_ticket: ticket=%s reason=%s", ticket_id, reason)
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE tickets SET
                    status = 'cancelled', closed_at = %s, last_activity = %s,
                    closure_reason = %s
                WHERE id = %s
                AND status IN ('open', 'claimed', 'stale')
                """,
                (now, now, reason or "cancelled", ticket_id),
            )
            conn.commit()
            n = cursor.rowcount
            if n:
                _log.info("cancel_ticket: cancelled %s", ticket_id)
            return n

    # ── v080: Token consumption reporting ───────────────────────

    def get_token_usage_by_plan(self, plan_id: str) -> Dict[str, Any]:
        _log.debug("get_token_usage_by_plan: plan=%s", plan_id)
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(tokens_used), 0) as total_tokens, COUNT(*) as receipts
                FROM nebula.receipts_unified WHERE plan_id = %s
                """,
                (plan_id,),
            ).fetchone()
            result = {
                "plan_id": plan_id,
                "total_tokens": row[0] if row else 0,
                "receipts": row[1] if row else 0,
            }
            _log.debug("get_token_usage_by_plan: plan=%s tokens=%d receipts=%d", plan_id, result["total_tokens"], result["receipts"])
            return result

    def get_token_usage_by_role(self, role: str) -> Dict[str, Any]:
        _log.debug("get_token_usage_by_role: role=%s", role)
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(tokens_used), 0) as total_tokens, COUNT(*) as receipts
                FROM nebula.receipts_unified WHERE agent_role = %s
                """,
                (role,),
            ).fetchone()
            result = {
                "role": role,
                "total_tokens": row[0] if row else 0,
                "receipts": row[1] if row else 0,
            }
            _log.debug("get_token_usage_by_role: role=%s tokens=%d receipts=%d", role, result["total_tokens"], result["receipts"])
            return result

    def get_token_usage_by_ticket(self, ticket_id: str) -> Dict[str, Any]:
        _log.debug("get_token_usage_by_ticket: ticket=%s", ticket_id)
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(tokens_used, 0) FROM tickets WHERE id = %s",
                (ticket_id,),
            ).fetchone()
            result = {
                "ticket_id": ticket_id,
                "tokens_used": row[0] if row else 0,
            }
            _log.debug("get_token_usage_by_ticket: ticket=%s tokens=%d", ticket_id, result["tokens_used"])
            return result

    # ── Token cost tracking (plan 1018) ────────────────────────────────

    def fetch_model_pricing(self) -> List[Dict[str, Any]]:
        _log.debug("fetch_model_pricing")
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT model_name, provider, input_price_per_token, "
                "output_price_per_token, cache_hit_price, updated_at "
                "FROM model_pricing"
            )
            rows = cursor.dict_fetchall()
            _log.debug("fetch_model_pricing: returned %d rows", len(rows))
            return rows

    def get_agent_budget(self, role: str) -> Optional[Dict[str, Any]]:
        _log.debug("get_agent_budget: role=%s", role)
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT agent_role, ceiling_usd, ceiling_tokens, "
                "current_usd, current_tokens, reset_period, reset_at "
                "FROM agent_budgets WHERE agent_role = %s",
                (role,),
            ).fetchone()
            if not row:
                return None
            return {
                "agent_role": row[0],
                "ceiling_usd": row[1],
                "ceiling_tokens": row[2],
                "current_usd": row[3],
                "current_tokens": row[4],
                "reset_period": row[5],
                "reset_at": row[6],
            }

    def update_agent_budget_usage(self, role: str, cost_usd: float, tokens: int) -> None:
        _log.debug("update_agent_budget_usage: role=%s cost=%.6f tokens=%d", role, cost_usd, tokens)
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE agent_budgets SET "
                "current_usd = COALESCE(current_usd, 0) + %s, "
                "current_tokens = COALESCE(current_tokens, 0) + %s, "
                "updated_at = %s "
                "WHERE agent_role = %s",
                (cost_usd, tokens, datetime.utcnow().isoformat() + "Z", role),
            )
            conn.commit()

    def insert_cost_log(
        self,
        session_id: str,
        ticket_id: Optional[str],
        model: str,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_usd: Optional[float],
        actual_cost_usd: Optional[float],
        tags: Optional[List[str]] = None,
    ) -> None:
        now = datetime.utcnow().isoformat() + "Z"
        tags_json = json.dumps(tags or [])
        _log.debug(
            "insert_cost_log: session=%s ticket=%s model=%s in=%d out=%d est=%s act=%s",
            session_id, ticket_id, model, input_tokens, output_tokens,
            estimated_cost_usd, actual_cost_usd,
        )
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO cost_logs "
                "(session_id, ticket_id, model, input_tokens, output_tokens, "
                "estimated_cost_usd, actual_cost_usd, recorded_at, tags) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    session_id, ticket_id, model,
                    input_tokens, output_tokens,
                    estimated_cost_usd, actual_cost_usd,
                    now, tags_json,
                ),
            )
            conn.commit()

    def update_ticket_costs(self, ticket_id: str, cost_usd: float) -> None:
        _log.debug("update_ticket_costs: ticket=%s cost=%.6f", ticket_id, cost_usd)
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE tickets SET cost_used_usd = COALESCE(cost_used_usd, 0) + %s WHERE id = %s",
                (cost_usd, ticket_id),
            )
            conn.commit()

    def get_ticket_budget(self, ticket_id: str) -> Dict[str, Any]:
        _log.debug("get_ticket_budget: ticket=%s", ticket_id)
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(token_budget, 0), COALESCE(tokens_used, 0) "
                "FROM tickets WHERE id = %s",
                (ticket_id,),
            ).fetchone()
            if not row:
                return {"token_budget": 0, "tokens_used": 0, "cost_budget_usd": 0, "cost_used_usd": 0}
            return {
                "token_budget": row[0],
                "tokens_used": row[1],
                "cost_budget_usd": 0,
                "cost_used_usd": 0,
            }

    def get_ticket_lineage(self, plan_id: str) -> List[Dict[str, Any]]:
        _log.debug("get_ticket_lineage: plan=%s", plan_id)
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, role, status, tokens_used,
                       parent_ticket_id, spawn_reason,
                       replacement_of, closure_reason,
                       created_at, closed_at
                FROM tickets WHERE plan_id = %s
                ORDER BY created_at ASC
                """,
                (plan_id,),
            )
            tickets = cursor.dict_fetchall()
            _log.debug("get_ticket_lineage: plan=%s returned %d tickets", plan_id, len(tickets))
            return tickets

    # ── v104: Orphaned ticket cleanup ──────────────────────────────

    def close_orphaned_tickets(self, plan_id: str) -> int:
        """Close open tickets for roles that no longer match the plan's current derived_status.

        When a plan enters a state where certain roles are no longer eligible
        (e.g. BLOCK makes critic ineligible, IMPLEMENTATION makes planner
        ineligible), their open tickets are cancelled to prevent them from
        being dispatched on stale authorization.
        """
        _log.debug("close_orphaned_tickets: plan=%s", plan_id)
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT derived_status FROM plan_status WHERE id = %s",
                (plan_id,),
            ).fetchone()
            if not row:
                _log.warning("close_orphaned_tickets: plan %s not found in plan_status", plan_id)
                return 0
            derived_status = row[0]

            # Build set of roles that should stay open based on current
            # derived_status
            valid_roles: list[str] = []
            for role, valid_statuses in self._ROLE_DERIVED_STATUS_MAP.items():
                if derived_status in valid_statuses:
                    valid_roles.append(role)

            now = datetime.utcnow().isoformat() + "Z"
            if not valid_roles:
                # No role is eligible — close ALL open tickets
                reason = f'orphaned: plan status {derived_status} has no eligible roles'
                cursor = conn.execute(
                    """
                    UPDATE tickets SET
                        status = 'cancelled', closed_at = %s, last_activity = %s,
                        closure_reason = %s
                    WHERE plan_id = %s AND status = 'open'
                    """,
                    (now, now, reason, plan_id),
                )
            else:
                placeholders = ', '.join(['%s'] * len(valid_roles))
                reason = f'orphaned: role no longer eligible for plan status {derived_status}'
                cursor = conn.execute(
                    f"""
                    UPDATE tickets SET
                        status = 'cancelled', closed_at = %s, last_activity = %s,
                        closure_reason = %s
                    WHERE plan_id = %s AND role NOT IN ({placeholders}) AND status = 'open'
                    """,
                    (now, now, reason, plan_id, *valid_roles),
                )
            conn.commit()
            n = cursor.rowcount
            if n:
                _log.info("close_orphaned_tickets: cleaned %d orphaned ticket(s) for plan %s (derived_status=%s)", n, plan_id, derived_status)
            return n

    # ── Cursor ────────────────────────────────────────────────────

    def get_cursor(self, role: str) -> Optional[str]:
        _log.debug("get_cursor: role=%s", role)
        with self._get_connection() as conn:
            row = conn.execute("SELECT last_processed_plan_id FROM pipeline_cursor WHERE role=%s", (role,)).fetchone()
            plan_id = row[0] if row else None
            _log.debug("get_cursor: role=%s plan_id=%s", role, plan_id)
            return plan_id

    def advance_cursor(self, role: str, plan_id: str, wr_id: str):
        _log.info("advance_cursor: role=%s plan=%s wr=%s", role, plan_id, wr_id)
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO pipeline_cursor (role, last_processed_plan_id, last_work_request_id, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT(role) DO UPDATE SET
                    last_processed_plan_id = EXCLUDED.last_processed_plan_id,
                    last_work_request_id = EXCLUDED.last_work_request_id,
                    updated_at = EXCLUDED.updated_at
                """,
                (role, plan_id, wr_id, now),
            )
            conn.commit()

    def get_role_model_config(self, role: str) -> Optional[Dict[str, str]]:
        """Resolve role config via tackle-mcp (the AI config authority).

        Delegates to tackle.db.get_role_config(role), which fetches the
        resolved config from the tackle-mcp HTTP server on :3400.
        Returns {'harness': <binary>, 'model': <model_identifier>} or None.
        """
        _log.debug("get_role_model_config: role=%s (via tackle)", role)
        from tackle.db import get_role_config

        cfg = get_role_config(role)
        if not cfg:
            _log.debug("get_role_model_config: no config for role=%s", role)
            return None
        semantics = cfg.get("invocation_semantics") or {}
        harness_binary = semantics.get("binary", "")
        if not harness_binary:
            _log.warning("get_role_model_config: no binary in semantics for role=%s", role)
            return None
        model_id = cfg.get("model_identifier", "")
        _log.debug("get_role_model_config: role=%s harness=%s model=%s", role, harness_binary, model_id)
        return {
            "harness": harness_binary,
            "model": model_id,
            # Provider fields let the pipeline qualify the model ID with
            # the model's OWN provider (see provider_prefix_slug /
            # qualify_opencode_model_id above).
            "provider_name": cfg.get("provider_name", ""),
            "provider_id": cfg.get("provider_id", ""),
            "provider_type": cfg.get("provider_type", ""),
        }

    # ── v105: Failure recovery config ────────────────────────────────

    def get_failure_recovery_config(self) -> Dict[str, Any]:
        """Return the failure recovery configuration from circuit_breaker.

        Returns:
            dict with: max_retries_per_model, retry_delay_seconds, max_fallbacks,
            push_back_to_pending, circuit_breaker_retry_after (from retry_after).
        """
        default = {
            "max_retries_per_model": 3,
            "retry_delay_seconds": 120,
            "max_fallbacks": 3,
            "push_back_to_pending": True,
            "circuit_breaker_retry_after": 1800,
        }
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT max_retries_per_model, retry_delay_seconds, max_fallbacks, "
                    "push_back_to_pending, retry_after FROM circuit_breaker WHERE id = 1"
                ).dict_fetchone()
                if not row:
                    _log.debug("get_failure_recovery_config: no row, using defaults")
                    return default
                config = {
                    "max_retries_per_model": row["max_retries_per_model"] if row.get("max_retries_per_model") is not None else 3,
                    "retry_delay_seconds": row["retry_delay_seconds"] if row.get("retry_delay_seconds") is not None else 120,
                    "max_fallbacks": row["max_fallbacks"] if row.get("max_fallbacks") is not None else 3,
                    "push_back_to_pending": bool(row.get("push_back_to_pending", 1)),
                    "circuit_breaker_retry_after": row["retry_after"] if row.get("retry_after") is not None else 1800,
                }
                _log.debug("get_failure_recovery_config: %s", config)
                return config
        except Exception as exc:
            _log.warning("get_failure_recovery_config: query failed: %s", exc)
            return default

    def trip_and_requeue(
        self, plan_id: str, role: str, session_id: str,
        error: str, detail: str = "", source: str = "conduit",
        model_cfg: Optional[Dict[str, str]] = None,
    ) -> None:
        """Trip the circuit breaker AND requeue the plan for retry.

        1. Trips the circuit breaker with the given error info and retry_after
           from failure recovery config.
        2. Inserts a REQUEUED receipt so plan_status → PLAN_CREATE.
        3. Creates a fresh builder Ticket so the plan is eligible on breaker reset.

        Called when all retries and fallbacks are exhausted.
        """
        _log.warning("trip_and_requeue: plan=%s role=%s error=%s", plan_id, role, error)
        config = self.get_failure_recovery_config()
        retry_after = config["circuit_breaker_retry_after"]

        # 1. Trip circuit breaker
        self.trip_circuit_breaker(
            error=error,
            detail=detail or f"All retries/fallbacks exhausted for plan {plan_id}",
            source=source,
            retry_after=retry_after,
        )

        now = datetime.utcnow().isoformat() + "Z"

        if not config["push_back_to_pending"]:
            _log.info("trip_and_requeue: push_back_to_pending disabled, just tripping breaker")
            return

        # 2. Create a fresh builder Ticket FIRST so we have a valid ticket ID
        #    that the REQUEUED receipt can reference (avoids FK violation).
        expires_at = (datetime.utcnow() + timedelta(hours=DEFAULT_TICKET_TTL_HOURS)).isoformat() + "Z"
        builder_ticket_id = f"ticket-{plan_id}-builder-{int(datetime.utcnow().timestamp())}"
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO tickets
                    (id, plan_id, role, status, created_at,
                     objective, owner,
                     spawn_reason, last_activity, expires_at)
                VALUES (%s, %s, %s, 'open', %s,
                        %s, %s,
                        %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (builder_ticket_id, plan_id, "builder", now,
                 f"Requeued after circuit breaker (ref: {error})", "builder",
                 "circuit_breaker_requeue", now, expires_at),
            )
            conn.commit()

        # 3. Insert REQUEUED receipt referencing the real builder ticket
        self.insert_receipt(
            plan_id=plan_id,
            receipt_type="REQUEUED",
            agent_role=role,
            session_id=session_id,
            ticket_id=builder_ticket_id,
            summary=f"Circuit breaker tripped — plan {plan_id} requeued for retry",
            metadata={
                "error": error,
                "detail": detail,
                "retry_after": retry_after,
                "model_cfg": model_cfg,
            },
        )

        _log.warning("trip_and_requeue: circuit breaker tripped — plan %s requeued (retry_after=%ds)", plan_id, retry_after)

    def get_fallback_models(self, role: str) -> List[Dict[str, Any]]:
        """Return fallback models for a role via tackle-mcp, ordered by priority.

        Delegates to tackle.db.get_fallback_models(role). Each entry has:
        priority, model_identifier, provider_type, api_key, endpoint_url,
        harness_name, invocation_semantics (parsed dict).
        """
        _log.debug("get_fallback_models: role=%s (via tackle)", role)
        from tackle.db import get_fallback_models as _tackle_fallbacks

        results = _tackle_fallbacks(role)
        _log.debug("get_fallback_models: role=%s returning %d models", role, len(results))
        return results

    # ── Kernel persistence (plan 1023) ─────────────────────────────────

    def save_kernel_delta(self, delta: "KernelDelta") -> bool:
        """Persist a KernelDelta to the kernel_delta_log table.

        Args:
            delta: The KernelDelta to persist.

        Returns:
            True if inserted, False if duplicate (already exists).
        """
        from wrp_kernel.delta import KernelDelta
        _log.debug("save_kernel_delta: delta_id=%s batch=%s version=%d",
                   delta.delta_id, delta.batch_id, delta.version)
        payload = {
            "receipts": delta.receipts,
            "affected_plans": list(delta.affected_plans),
            "invalidated_plans": list(delta.invalidated_plans),
        }
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO kernel_delta_log
                    (delta_id, batch_id, payload, version, created_at)
                VALUES (%s, %s, %s::jsonb, %s, %s)
                ON CONFLICT (delta_id) DO NOTHING
                """,
                (delta.delta_id, delta.batch_id,
                 json.dumps(payload), delta.version, now),
            )
            conn.commit()
            inserted = cursor.rowcount > 0
            if inserted:
                _log.info("save_kernel_delta: persisted delta_id=%s version=%d",
                          delta.delta_id, delta.version)
            else:
                _log.debug("save_kernel_delta: duplicate delta_id=%s", delta.delta_id)
            return inserted

    def save_kernel_snapshot(self, state: "KernelState") -> bool:
        """Save a KernelSnapshot checkpoint to the kernel_snapshot table.

        Args:
            state: The current KernelState to snapshot.

        Returns:
            True if saved.
        """
        from wrp_kernel.engine import KernelState
        _log.debug("save_kernel_snapshot: version=%d", state.version)
        state_dict = state.to_dict()
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO kernel_snapshot
                    (version, state, created_at)
                VALUES (%s, %s::jsonb, %s)
                ON CONFLICT (version)
                DO UPDATE SET state = EXCLUDED.state, created_at = EXCLUDED.created_at
                """,
                (state.version, json.dumps(state_dict), now),
            )
            conn.commit()
            _log.info("save_kernel_snapshot: saved version=%d", state.version)
            return True

    def get_latest_snapshot(self) -> Optional[dict]:
        """Get the latest (highest version) KernelSnapshot.

        Returns:
            Deserialized state dict, or None if no snapshots exist.
        """
        _log.debug("get_latest_snapshot")
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT state FROM kernel_snapshot ORDER BY version DESC LIMIT 1"
            ).fetchone()
            if not row:
                _log.debug("get_latest_snapshot: no snapshots found")
                return None
            _log.debug("get_latest_snapshot: found version=...")
            return row[0] if isinstance(row[0], dict) else json.loads(row[0])

    def get_nearest_snapshot(self, version: int) -> Optional[dict]:
        """Get the nearest valid snapshot with version <= given version.

        For KSRA: KernelState(N) = Snapshot(K) + Replay(deltas K+1 → N)
        where K = this method's output version.

        Args:
            version: The target version to reconstruct to.

        Returns:
            Deserialized state dict of the nearest ancestor, or None.
        """
        _log.debug("get_nearest_snapshot: target_version=%d", version)
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT state FROM kernel_snapshot "
                "WHERE version <= %s ORDER BY version DESC LIMIT 1",
                (version,),
            ).fetchone()
            if not row:
                _log.debug("get_nearest_snapshot: no ancestor for version=%d", version)
                return None
            snap = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            snap_version = snap.get("version", -1)
            _log.debug("get_nearest_snapshot: ancestor version=%d for target=%d",
                       snap_version, version)
            return snap

    def get_deltas_since(self, version: int) -> List[dict]:
        """Get all deltas with version > given version, ordered by version ASC.

        For replay in KSRA.

        Args:
            version: The snapshot version to replay after.

        Returns:
            List of kernel_delta_log rows as dicts (delta_id, batch_id,
            payload, version, created_at).
        """
        _log.debug("get_deltas_since: since_version=%d", version)
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT delta_id, batch_id, payload, version, created_at "
                "FROM kernel_delta_log WHERE version > %s "
                "ORDER BY version ASC",
                (version,),
            )
            rows = cursor.dict_fetchall()
            _log.debug("get_deltas_since: found %d deltas since version=%d",
                       len(rows), version)
            return rows

    def log_lineage_event(
        self,
        version: int,
        delta_id: str,
        step: str,
        event_type: str = "apply",
        affected_plans: Optional[List[str]] = None,
        detail: Optional[str] = None,
    ) -> bool:
        """Record a lineage event in the lineage_log table.

        Args:
            version: The kernel version at this event.
            delta_id: The associated delta_id.
            step: Which reduce step produced this event.
            event_type: 'apply' | 'error' | 'reconstruct'.
            affected_plans: Plans affected in this event.
            detail: Optional detail string.

        Returns:
            True if inserted.
        """
        _log.debug("log_lineage_event: version=%d delta=%s step=%s type=%s",
                   version, delta_id, step, event_type)
        plans_json = json.dumps(affected_plans or [])
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO lineage_log
                    (version, delta_id, step, event_type,
                     affected_plans, detail, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (version, delta_id, step, event_type,
                 plans_json, detail, now),
            )
            conn.commit()
            return True

    def get_lineage_events(
        self,
        version: Optional[int] = None,
        limit: int = 100,
    ) -> List[dict]:
        """Retrieve lineage events, optionally filtered by version.

        Args:
            version: Optional version filter.
            limit: Max events to return (default 100).

        Returns:
            List of lineage event dicts.
        """
        _log.debug("get_lineage_events: version=%s limit=%d", version, limit)
        with self._get_connection() as conn:
            if version is not None:
                cursor = conn.execute(
                    "SELECT * FROM lineage_log WHERE version = %s "
                    "ORDER BY id ASC LIMIT %s",
                    (version, limit),
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM lineage_log ORDER BY id DESC LIMIT %s",
                    (limit,),
                )
            events = cursor.dict_fetchall()
            _log.debug("get_lineage_events: returned %d events", len(events))
            return events

    # ── Execution Authority (ADR-006) ─────────────────────────

    def acquire_lease(
        self,
        request_id: str,
        executor_id: str,
        ttl_seconds: int = 300,
    ) -> Optional[dict]:
        """Acquire a temporal lease on an execution request.

        Only one ACTIVE lease per request at a time (enforced by partial unique index).
        Returns the lease dict, or None if an active lease already exists.
        """
        _log.info("acquire_lease: request=%s executor=%s ttl=%d", request_id, executor_id, ttl_seconds)
        with self._get_connection() as conn:
            try:
                cursor = conn.execute(
                    """INSERT INTO execution.leases
                       (request_id, executor_id, ttl_seconds, expires_at)
                       VALUES (%s, %s, %s, NOW() + (%s || ' seconds')::interval)
                       RETURNING *""",
                    (request_id, executor_id, ttl_seconds, str(ttl_seconds)),
                )
                conn.commit()
                lease = cursor.dict_fetchone()
                _log.info("acquire_lease: acquired %s", lease["id"])
                return lease
            except Exception as e:
                conn.rollback()
                if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                    _log.warning("acquire_lease: active lease already exists for request %s", request_id)
                    return None
                raise

    def release_lease(self, lease_id: str) -> bool:
        """Release an active lease. Returns True if released."""
        _log.info("release_lease: lease=%s", lease_id)
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE execution.leases SET status = 'RELEASED', released_at = NOW() "
                "WHERE id = %s AND status = 'ACTIVE'",
                (lease_id,),
            )
            conn.commit()
            released = cursor.rowcount > 0
            if released:
                _log.info("release_lease: released %s", lease_id)
            else:
                _log.warning("release_lease: lease %s not found or not ACTIVE", lease_id)
            return released

    def expire_lease(self, lease_id: str) -> bool:
        """Mark a lease as expired. Returns True if expired."""
        _log.info("expire_lease: lease=%s", lease_id)
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE execution.leases SET status = 'EXPIRED' "
                "WHERE id = %s AND status = 'ACTIVE'",
                (lease_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def expire_stale_leases(self) -> int:
        """Mark all leases past their expires_at as EXPIRED. Returns count."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE execution.leases SET status = 'EXPIRED' "
                "WHERE status = 'ACTIVE' AND expires_at < NOW()"
            )
            conn.commit()
            count = cursor.rowcount
            if count > 0:
                _log.info("expire_stale_leases: expired %d leases", count)
            return count

    def renew_lease(self, lease_id: str, ttl_seconds: int = 300) -> bool:
        """Renew an active lease (extend TTL). Returns True if renewed."""
        _log.info("renew_lease: lease=%s ttl=%d", lease_id, ttl_seconds)
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE execution.leases SET ttl_seconds = %s, "
                "expires_at = NOW() + (%s || ' seconds')::interval "
                "WHERE id = %s AND status = 'ACTIVE'",
                (ttl_seconds, str(ttl_seconds), lease_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def create_attempt(
        self,
        lease_id: str,
        request_id: str,
        executor_id: str,
    ) -> dict:
        """Create an execution attempt. Returns the attempt dict."""
        _log.info("create_attempt: lease=%s request=%s executor=%s", lease_id, request_id, executor_id)
        with self._get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO execution.attempts
                   (lease_id, request_id, executor_id, status)
                   VALUES (%s, %s, %s, 'CREATED')
                   RETURNING *""",
                (lease_id, request_id, executor_id),
            )
            conn.commit()
            return cursor.dict_fetchone()

    def start_attempt(self, attempt_id: str) -> bool:
        """Mark attempt as RUNNING. Returns True if updated."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "UPDATE execution.attempts SET status = 'RUNNING', started_at = NOW() "
                "WHERE id = %s AND status = 'CREATED'",
                (attempt_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def complete_attempt(
        self,
        attempt_id: str,
        status: str,  # SUCCEEDED, FAILED, TIMED_OUT
        exit_code: Optional[int] = None,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> bool:
        """Complete an execution attempt. Returns True if updated."""
        _log.info("complete_attempt: attempt=%s status=%s exit_code=%s", attempt_id, status, exit_code)
        with self._get_connection() as conn:
            cursor = conn.execute(
                """UPDATE execution.attempts
                   SET status = %s, completed_at = NOW(),
                       exit_code = %s, result = %s, error = %s
                   WHERE id = %s""",
                (status, exit_code, json.dumps(result or {}), error, attempt_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def issue_execution_receipt(
        self,
        attempt_id: str,
        request_id: str,
        receipt_type: str,
        agent_role: str = "",
        summary: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """Issue an immutable receipt from an attempt. Returns the receipt dict."""
        _log.info("issue_execution_receipt: attempt=%s type=%s role=%s", attempt_id, receipt_type, agent_role)
        with self._get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO execution.receipts
                   (attempt_id, request_id, type, agent_role, summary, metadata)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   RETURNING *""",
                (attempt_id, request_id, receipt_type, agent_role, summary,
                 json.dumps(metadata or {})),
            )
            conn.commit()
            return cursor.dict_fetchone()

    def get_or_create_execution_request(
        self,
        plan_id: str,
        title: str = "",
        objective: str = "",
    ) -> dict:
        """Get or create an execution request for a plan. Returns the request dict.

        Uses the plan ID as the business key. If a request already exists for this
        plan, returns the existing one.
        """
        business_key = f"plan-{plan_id}"
        with self._get_connection() as conn:
            # Try to get existing
            cursor = conn.execute(
                "SELECT * FROM execution.requests WHERE source_plan_id = %s",
                (plan_id,),
            )
            existing = cursor.dict_fetchone()
            if existing:
                return existing

            # Create new
            cursor = conn.execute(
                """INSERT INTO execution.requests
                   (business_key, title, objective, source_plan_id, status)
                   VALUES (%s, %s, %s, %s, 'DRAFT')
                   ON CONFLICT (business_key) DO UPDATE SET title = EXCLUDED.title
                   RETURNING *""",
                (business_key, title, objective, plan_id),
            )
            conn.commit()
            return cursor.dict_fetchone()

    # ── Execution Authority (ADR-006): Cascade Admission ─────────────────

    def cascade_admission(self) -> int:
        """Cascade VALIDATED → ADMITTED → READY for eligible requests.

        This is the admission subscriber that runs as part of the pipeline.
        It transitions requests through the admission gate automatically.

        Returns the number of requests transitioned to READY.
        """
        count = 0
        with self._get_connection() as conn:
            # Step 1: VALIDATED → ADMITTED
            cursor = conn.execute(
                """UPDATE execution.requests
                   SET status = 'ADMITTED', updated_at = NOW()
                   WHERE status = 'VALIDATED'
                   RETURNING id, title"""
            )
            admitted = cursor.fetchall()
            if admitted:
                _log.info("cascade_admission: admitted %d request(s)", len(admitted))
                for row in admitted:
                    _log.info("cascade_admission: admitted %s (%s)", row[0], row[1])

            # Step 2: ADMITTED → READY
            cursor = conn.execute(
                """UPDATE execution.requests
                   SET status = 'READY', updated_at = NOW()
                   WHERE status = 'ADMITTED'
                   RETURNING id, title"""
            )
            ready = cursor.fetchall()
            if ready:
                _log.info("cascade_admission: ready %d request(s)", len(ready))
                for row in ready:
                    _log.info("cascade_admission: ready %s (%s)", row[0], row[1])
                    count += 1

            conn.commit()
        return count

    def get_requests_by_status(self, status: str, limit: int = 50) -> list:
        """Get requests by status. Returns list of dicts."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """SELECT * FROM execution.requests
                   WHERE status = %s
                   ORDER BY created_at DESC
                   LIMIT %s""",
                (status, limit),
            )
            return cursor.dict_fetchall()
