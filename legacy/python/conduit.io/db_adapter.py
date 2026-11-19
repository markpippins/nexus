import json
import logging
import os
import uuid
from contextlib import contextmanager
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

_log = logging.getLogger("conduit.db_adapter")

# ── PostgreSQL (mandatory — no SQLite fallback) ──────────────────────
import psycopg2
import psycopg2.pool

_pg_pool = None
def _get_schema() -> str:
    """Return a validated schema name.
    The name must be a PostgreSQL identifier consisting only of letters,
    digits and underscores, and must not be empty.  Reserved names ("temporal"
    and "public") are rejected earlier.  Validation prevents SQL injection via
    the SET search_path command which cannot use bind parameters for identifiers.
    """
    schema = os.environ.get("CONDUIT_PG_SCHEMA", "conduit")
    # Basic identifier validation – must start with a letter or underscore,
    # followed by letters, digits or underscores.
    import re
    if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', schema):
        raise ValueError(f"Invalid schema name '{schema}'")
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
        if "'" in schema:
            raise ValueError(
                f"Invalid schema name '{schema}': single-quote characters are not "
                f"allowed."
            )
        try:
            cur = conn.cursor()
            from psycopg2 import sql
            cur.execute(sql.SQL("SET search_path TO {},tackle,vision").format(sql.Identifier(schema)))
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
        self._schema = schema or _get_schema()
        _log.info("DBAdapter: initializing schema=%s db=%s", self._schema, dsn.rsplit("@", 1)[-1] if "@" in dsn else "(dsn)")
        self._init_db()

    @contextmanager
    def _get_connection(self):
        pool = _get_pg_pool()
        conn = pool.getconn()
        proxy = _ConnectionProxy(conn, schema=self._schema)
        try:
            yield proxy
        finally:
            proxy.commit()
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS work_requests (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT REFERENCES plans(id),
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
            for table in ("plans", "receipts", "tickets", "sessions", "session_logs", "circuit_breaker"):
                row = conn.execute(
                    f"SELECT COUNT(*) FROM information_schema.tables "
                    f"WHERE table_schema IN ('{s}','tackle','vision') AND table_name = %s",
                    (table,),
                ).fetchone()
                count = row[0] if row else 0
                if count == 0:
                    _log.error("_init_db: table '%s' missing in schemas [%s,tackle,vision]", table, s)
                    raise RuntimeError(
                        f"Table '{table}' not found in [{s},tackle,vision] on {self.db_path}. "
                        f"Start the MCP server first to initialize the database schema."
                    )
            _log.debug("_init_db: all MCP-owned tables present")

            # ── Verify critical columns on MCP-owned tables ───
            required_columns = {
                "receipts": ["ticket_id", "tokens_used"],
                "tickets": ["objective", "owner", "spawn_reason"],
                "circuit_breaker": ["paused"],
                "sessions": ["cost_usd"],
                "session_logs": ["session_id", "level", "line"],
            }
            for table, cols in required_columns.items():
                rows = conn.execute(
                    f"SELECT column_name FROM information_schema.columns "
                    f"WHERE table_schema IN ('{s}','tackle','vision') AND table_name = '{table}'"
                ).fetchall()
                existing = {r[0] for r in rows}
                missing = [c for c in cols if c not in existing]
                if missing:
                    _log.error("_init_db: columns %s missing from '%s'", missing, table)
                    raise RuntimeError(
                        f"Columns {missing} missing from '{table}' in schema '{s}'. "
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
          critic completed   → builder

        Guard: if the plan's derived_status is already terminal (REVIEW_PASS,
        BLOCK, PLAN_BLOCK), no new tickets are spawned regardless of role
        transition.  This prevents late-arriving ticket completions (e.g.
        critic finishing after the plan was already REVIEW_PASS) from
        dispatching unnecessary work.
        """
        _log.info("create_next_tickets: plan=%s ticket_role=%s terminal_status=%s", plan_id, ticket_role, terminal_status)
        with self._get_connection() as conn:
            latest = conn.execute(
                """
                SELECT type FROM receipts
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
                next_roles = ["builder"]
        elif terminal_status == "failed":
            if ticket_role == "reviewer":
                next_roles = ["builder"]
            elif ticket_role == "planner":
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
                ticket_id = f"ticket-{plan_id}-{role}-{int(datetime.utcnow().timestamp())}"
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
        AND whose derived_status matches the role's eligibility.

        Excludes plans that already have a running session for the same role+plan
        (i.e. a Temporal workflow is already executing them), so the scheduler
        doesn't waste cycles re-dispatching idempotent workflow starts."""
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
                AND NOT EXISTS (
                    SELECT 1 FROM sessions s
                    WHERE s.is_running = 1
                    AND s.workflow_id = 'plan-' || ps.id || '-reviewer'
                )
                ORDER BY ps.created_at ASC
            """
        else:
            query = f"""
                SELECT ps.* FROM plan_status ps
                JOIN tickets t ON t.plan_id = ps.id
                WHERE t.role = %s AND t.status = 'open'
                AND ps.derived_status IN ({placeholders})
                AND NOT EXISTS (
                    SELECT 1 FROM sessions s
                    WHERE s.is_running = 1
                    AND s.workflow_id = 'plan-' || ps.id || '-' || %s
                )
                ORDER BY ps.created_at ASC
            """

        with self._get_connection() as conn:
            if role == "reviewer":
                cursor = conn.execute(query, valid_statuses)
            else:
                cursor = conn.execute(query, (role, *valid_statuses, role))
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
                tripped = bool(row[0]) if row else False
                _log.debug("is_circuit_breaker_tripped: %s", tripped)
                return tripped
        except Exception as exc:
            _log.warning("is_circuit_breaker_tripped: query failed: %s", exc)
            return False

    def is_role_circuit_breaker_tripped(self, role: str) -> bool:
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT tripped FROM role_circuit_breaker WHERE role = %s",
                    (role,),
                ).fetchone()
                tripped = bool(row[0]) if row else False
                _log.debug("is_role_circuit_breaker_tripped: role=%s %s", role, tripped)
                return tripped
        except Exception as exc:
            _log.warning("is_role_circuit_breaker_tripped: role=%s query failed: %s", role, exc)
            return False

    def consume_scheduler_wake(self, since: str) -> bool:
        """Check if scheduler wake was requested since `since` timestamp.
        Returns True and clears wake_requested_at if a wake was requested."""
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT wake_requested_at FROM circuit_breaker WHERE id = 1 "
                    "AND wake_requested_at IS NOT NULL AND wake_requested_at > %s",
                    (since,),
                ).fetchone()
                if row and row[0]:
                    conn.execute(
                        "UPDATE circuit_breaker SET wake_requested_at = NULL, updated_at = %s WHERE id = 1",
                        (datetime.utcnow().isoformat() + "Z",),
                    )
                    conn.commit()
                    return True
                return False
        except Exception as exc:
            _log.debug("consume_scheduler_wake: query failed: %s", exc)
            return False

    def trip_role_circuit_breaker(
        self, role: str, error: str, retry_after: int = 1800,
    ) -> None:
        _log.warning("trip_role_circuit_breaker: role=%s error=%s retry_after=%d", role, error, retry_after)
        now = datetime.utcnow().isoformat() + "Z"
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO role_circuit_breaker (role, tripped, tripped_at, retry_after, error, failure_count, updated_at)
                    VALUES (%s, 1, %s, %s, %s, 1, %s)
                    ON CONFLICT (role) DO UPDATE SET
                        tripped = 1, tripped_at = %s, retry_after = %s,
                        error = %s, failure_count = role_circuit_breaker.failure_count + 1,
                        updated_at = %s
                    """,
                    (role, now, retry_after, error, now, now, retry_after, error, now),
                )
                conn.commit()
        except Exception as exc:
            _log.warning("trip_role_circuit_breaker: role=%s failed: %s", role, exc)

    def reset_role_circuit_breaker(self, role: str) -> None:
        _log.info("reset_role_circuit_breaker: role=%s", role)
        now = datetime.utcnow().isoformat() + "Z"
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM role_circuit_breaker WHERE role = %s", (role,))
                # Wake the scheduler so it re-polls immediately instead of
                # waiting out the idle backoff (SCHEDULER_IDLE_BACKOFF, 60s).
                conn.execute(
                    "UPDATE circuit_breaker SET wake_requested_at = %s, updated_at = %s WHERE id = 1",
                    (now, now),
                )
                conn.commit()
        except Exception as exc:
            _log.warning("reset_role_circuit_breaker: role=%s failed: %s", role, exc)

    def is_conduit_paused(self) -> bool:
        query = "SELECT paused FROM circuit_breaker WHERE id = 1"
        try:
            with self._get_connection() as conn:
                row = conn.execute(query).fetchone()
                paused = bool(row[0]) if row else False
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

    # ── Session Logs ────────────────────────────────────────────────

    def append_session_log(self, session_id: str, line: str, level: str = "INFO") -> None:
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO session_logs (session_id, level, line) VALUES (%s, %s, %s)",
                (session_id, level, line),
            )
            conn.commit()

    def append_session_logs(self, session_id: str, lines: list[tuple[str, str]]) -> None:
        """Insert multiple (level, line) pairs in a single transaction."""
        if not lines:
            return
        with self._get_connection() as conn:
            for level, line in lines:
                conn.execute(
                    "INSERT INTO session_logs (session_id, level, line) VALUES (%s, %s, %s)",
                    (session_id, level, line),
                )
            conn.commit()

    # ── Receipts (v078 — ticket_id required) ─────────────────────

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
    ):
        """Insert a receipt linked to exactly one Ticket (Invariant 2)."""
        _log.info("insert_receipt: plan=%s type=%s role=%s ticket=%s tokens=%d",
                  plan_id, receipt_type, agent_role, ticket_id, tokens_used)
        now = datetime.utcnow().isoformat() + "Z"
        receipt_id = f"rec-{plan_id}-{receipt_type}-{uuid.uuid4().hex[:8]}"
        meta_json = json.dumps(metadata or {})
        query = """
            INSERT INTO receipts (id, plan_id, type, agent_role, session_id,
                ticket_id, summary, artifact_path, metadata_json, tokens_used, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        with self._get_connection() as conn:
            conn.execute(query, (
                receipt_id, plan_id, receipt_type, agent_role, session_id,
                ticket_id, summary, artifact_path, meta_json, tokens_used, now,
            ))
            conn.execute("UPDATE plans SET updated_at = %s WHERE id = %s", (now, plan_id))
            conn.commit()
        _log.debug("insert_receipt: created %s", receipt_id)

    def add_work_request(self, wr_id: str, plan_id: str, dco_json: str):
        _log.info("add_work_request: wr=%s plan=%s", wr_id, plan_id)
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO work_requests (id, plan_id, status, dco_json, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                (wr_id, plan_id, 'pending', dco_json, now, now),
            )
            conn.commit()

    def update_work_request_status(self, wr_id: str, status: str):
        _log.debug("update_work_request_status: wr=%s status=%s", wr_id, status)
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            conn.execute("UPDATE work_requests SET status = %s, updated_at = %s WHERE id = %s", (status, now, wr_id))
            conn.commit()

    def get_work_request(self, wr_id: str) -> Optional[Dict[str, Any]]:
        _log.debug("get_work_request: wr=%s", wr_id)
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM work_requests WHERE id = %s",
                (wr_id,),
            ).dict_fetchone()
            if not row:
                _log.debug("get_work_request: wr=%s not found", wr_id)
                return None
            try:
                row["dco_json"] = json.loads(row.get("dco_json", "{}"))
            except (json.JSONDecodeError, TypeError):
                row["dco_json"] = {}
            try:
                row["step_outputs"] = json.loads(row.get("step_outputs", "{}"))
            except (json.JSONDecodeError, TypeError):
                row["step_outputs"] = {}
            _log.debug("get_work_request: wr=%s found", wr_id)
            return row

    def update_work_request_step_outputs(self, wr_id: str, step_outputs: Dict[str, Any]):
        _log.debug("update_work_request_step_outputs: wr=%s keys=%s", wr_id, list(step_outputs.keys()))
        now = datetime.utcnow().isoformat() + "Z"
        outputs_json = json.dumps(step_outputs)
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE work_requests SET step_outputs = %s, updated_at = %s WHERE id = %s",
                (outputs_json, now, wr_id),
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
            cursor = conn.execute("DELETE FROM receipts WHERE plan_id=%s AND type=%s AND session_id=%s", (plan_id, receipt_type, session_id))
            conn.commit()
            deleted = cursor.rowcount > 0
            if deleted:
                _log.info("delete_receipt: deleted plan=%s type=%s", plan_id, receipt_type)
            return deleted

    def get_plan_by_id(self, plan_id: str) -> Optional[Dict[str, Any]]:
        _log.debug("get_plan_by_id: plan=%s", plan_id)
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM plans WHERE id = %s", (plan_id,))
            plan = cursor.dict_fetchone()
            _log.debug("get_plan_by_id: plan=%s found=%s", plan_id, plan is not None)
            return plan

    def create_session(self, session_id: str, agent_role: str, plan_ids: List[str], pid: Optional[int] = None,
                       workflow_id: Optional[str] = None, run_id: Optional[str] = None,
                       workflow_start_time: Optional[str] = None):
        _log.info("create_session: session=%s role=%s plans=%s pid=%s wf=%s", session_id, agent_role, plan_ids, pid, workflow_id)
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO sessions (id, agent_role, start_iso, plans_processed, plan_count, "
                "is_running, created_at, last_activity, pid, workflow_id, run_id, workflow_start_time) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                (session_id, agent_role, now, json.dumps(plan_ids), len(plan_ids), 1, now, now,
                 pid, workflow_id, run_id, workflow_start_time),
            )
            conn.commit()

    def close_session(self, session_id: str, exit_code: int,
                      workflow_close_time: Optional[str] = None,
                      workflow_run_time_ms: Optional[float] = None,
                      workflow_result: Optional[str] = None):
        """Close a session.  Caller must release Tickets before calling."""
        _log.info("close_session: session=%s exit_code=%d wf_close=%s wf_runtime=%.0fms wf_result=%s",
                  session_id, exit_code, workflow_close_time, workflow_run_time_ms or 0, workflow_result)
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE sessions SET end_iso=%s, is_running=0, exit_code=%s, last_activity=%s, "
                "workflow_close_time = %s, "
                "workflow_run_time_ms = %s, "
                "workflow_result = %s "
                "WHERE id=%s",
                (now, exit_code, now,
                 workflow_close_time, workflow_run_time_ms, workflow_result,
                 session_id),
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

    def detect_stale_tickets(self) -> int:
        threshold = (datetime.utcnow() - timedelta(seconds=DEFAULT_STALE_SECONDS)).isoformat() + "Z"
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE tickets SET status = 'stale'
                WHERE status = 'claimed'
                AND last_activity IS NOT NULL
                AND last_activity < %s
                """,
                (threshold,),
            )
            conn.commit()
            n = cursor.rowcount
            if n:
                _log.info("detect_stale_tickets: marked %d ticket(s) as stale", n)
            return n

    def detect_expired_tickets(self) -> int:
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE tickets SET status = 'expired'
                WHERE status IN ('open', 'claimed', 'stale')
                AND expires_at IS NOT NULL
                AND expires_at < %s
                """,
                (now,),
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
                FROM receipts WHERE plan_id = %s
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
                FROM receipts WHERE agent_role = %s
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

    def get_model_config(self, model_id: str) -> Optional[Dict[str, Any]]:
        """Look up a model by ID, returning model + harness + provider info.

        Returns dict with keys: id, name, model_identifier, harness_name,
        invocation_semantics, provider_name, provider_type, endpoint_url.
        Returns None if not found.
        """
        _log.debug("get_model_config: model=%s", model_id)
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT m.id, m.name, m.model_identifier,
                       h.name AS harness_name,
                       h.invocation_semantics,
                       p.name AS provider_name,
                       p.type AS provider_type,
                       p.endpoint_url
                FROM models m
                LEFT JOIN harnesses h ON h.id = m.harness_id
                LEFT JOIN providers p ON p.id = m.provider_id
                WHERE m.id = %s
                """,
                (model_id,),
            ).dict_fetchone()
            _log.debug("get_model_config: model=%s found=%s", model_id, row is not None)
            return row

    def get_role_model_config(self, role: str) -> Optional[Dict[str, str]]:
        _log.debug("get_role_model_config: role=%s", role)
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT h.invocation_semantics, m.model_identifier, p.type AS provider_type
                FROM config_bundle cb
                JOIN harnesses h ON cb.harness_id = h.id
                JOIN models m ON cb.model_id = m.id
                JOIN providers p ON cb.provider_id = p.id
                WHERE cb.role = %s AND cb.is_active = 1
                ORDER BY cb.priority ASC
                LIMIT 1
                """,
                (role,),
            ).fetchone()
            if not row:
                _log.debug("get_role_model_config: no config for role=%s", role)
                return None
            semantics_str, model_id, provider_type = row
            try:
                semantics = json.loads(semantics_str or '{}')
            except json.JSONDecodeError as exc:
                _log.warning("get_role_model_config: failed to parse semantics for role=%s: %s", role, exc)
                return None
            harness_binary = semantics.get('binary', '')
            if not harness_binary:
                _log.warning("get_role_model_config: no binary in semantics for role=%s", role)
                return None
            _log.debug("get_role_model_config: role=%s harness=%s model=%s provider=%s",
                       role, harness_binary, model_id, provider_type)
            return {'harness': harness_binary, 'model': model_id, 'provider_type': provider_type}

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

    def get_requeue_count(self, plan_id: str) -> int:
        """Count how many times this plan has been requeued.

        Each failed model chain produces a REQUEUED receipt via
        trip_and_requeue.  This is the number of full requeue cycles.
        """
        _log.debug("get_requeue_count: plan=%s", plan_id)
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM receipts WHERE plan_id = %s AND type = 'REQUEUED'",
                (plan_id,),
            ).fetchone()
            count = row[0] if row else 0
            _log.debug("get_requeue_count: plan=%s count=%d", plan_id, count)
            return count

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

        # 1. Trip circuit breaker (global + per-role)
        self.trip_circuit_breaker(
            error=error,
            detail=detail or f"All retries/fallbacks exhausted for plan {plan_id}",
            source=source,
            retry_after=retry_after,
        )
        self.trip_role_circuit_breaker(
            role=role,
            error=error,
            retry_after=retry_after,
        )

        now = datetime.utcnow().isoformat() + "Z"

        if not config["push_back_to_pending"]:
            _log.info("trip_and_requeue: push_back_to_pending disabled, just tripping breaker")
            return

        # 2. Cancel existing open ticket (if any) for this plan/role,
        #    then create a fresh builder Ticket so the REQUEUED receipt
        #    has a valid FK target.
        expires_at = (datetime.utcnow() + timedelta(hours=DEFAULT_TICKET_TTL_HOURS)).isoformat() + "Z"
        builder_ticket_id = f"ticket-{plan_id}-builder-{int(datetime.utcnow().timestamp())}"
        with self._get_connection() as conn:
            # Cancel any existing open ticket first (partial unique index
            # idx_tickets_open only allows one open ticket per plan/role).
            conn.execute(
                "UPDATE tickets SET status = 'cancelled', closure_reason = 'superseded_by_requeue', "
                "closed_at = %s WHERE plan_id = %s AND role = 'builder' AND status = 'open'",
                (now, plan_id),
            )
            conn.execute(
                """
                INSERT INTO tickets
                    (id, plan_id, role, status, created_at,
                     objective, owner,
                     spawn_reason, last_activity, expires_at)
                VALUES (%s, %s, %s, 'open', %s,
                        %s, %s,
                        %s, %s, %s)
                """,
                (builder_ticket_id, plan_id, "builder", now,
                 f"Requeued after circuit breaker (ref: {error})", "builder",
                 "circuit_breaker_requeue", now, expires_at),
            )
            conn.commit()

        # 3. Insert REQUEUED receipt referencing the fresh builder ticket
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
        """Return fallback models for a role from config_bundle, ordered by priority.

        Each entry has: model_identifier, provider_type, provider_id, api_key,
        endpoint_url, harness_name, harness_id, invocation_semantics, priority.
        """
        _log.debug("get_fallback_models: role=%s", role)
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT cb.priority,
                       m.model_identifier,
                       p.type          AS provider_type,
                       p.api_key,
                       p.endpoint_url,
                       p.id            AS provider_id,
                       h.name          AS harness_name,
                       h.id            AS harness_id,
                       h.invocation_semantics
                FROM config_bundle cb
                JOIN models m        ON cb.model_id = m.id
                LEFT JOIN providers p ON cb.provider_id = p.id
                LEFT JOIN harnesses h ON cb.harness_id = h.id
                WHERE cb.role = %s AND cb.is_active = 1
                ORDER BY cb.priority ASC
                """,
                (role,),
            )
            rows = cursor.dict_fetchall()

        _log.debug("get_fallback_models: role=%s found %d models", role, len(rows))
        results: list = []
        for row in rows:
            semantics = row.get("invocation_semantics") or "{}"
            try:
                semantics_parsed = json.loads(semantics)
            except (json.JSONDecodeError, TypeError):
                semantics_parsed = {}
            results.append({
                "priority": row["priority"],
                "model_identifier": row["model_identifier"],
                "provider_type": row.get("provider_type") or "",
                "provider_id": row.get("provider_id") or "",
                "api_key": row.get("api_key") or "",
                "endpoint_url": row.get("endpoint_url") or "",
                "harness_name": row.get("harness_name") or "",
                "harness_id": row.get("harness_id") or "",
                "invocation_semantics": semantics_parsed,
            })
        _log.debug("get_fallback_models: role=%s returning %d models with semantics", role, len(results))
        return results


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
        if "'" in schema:
            raise ValueError(
                f"Invalid schema name '{schema}': single-quote characters are not "
                f"allowed."
            )
        try:
            cur = conn.cursor()
            from psycopg2 import sql
            cur.execute(sql.SQL("SET search_path TO {},tackle,vision").format(sql.Identifier(schema)))
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
        self._schema = schema or _get_schema()
        _log.info("DBAdapter: initializing schema=%s db=%s", self._schema, dsn.rsplit("@", 1)[-1] if "@" in dsn else "(dsn)")
        self._init_db()

    @contextmanager
    def _get_connection(self):
        pool = _get_pg_pool()
        conn = pool.getconn()
        proxy = _ConnectionProxy(conn, schema=self._schema)
        try:
            yield proxy
        finally:
            proxy.commit()
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS work_requests (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT REFERENCES plans(id),
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
            for table in ("plans", "receipts", "tickets", "sessions", "session_logs", "circuit_breaker"):
                row = conn.execute(
                    f"SELECT COUNT(*) FROM information_schema.tables "
                    f"WHERE table_schema IN ('{s}','tackle','vision') AND table_name = %s",
                    (table,),
                ).fetchone()
                count = row[0] if row else 0
                if count == 0:
                    _log.error("_init_db: table '%s' missing in schemas [%s,tackle,vision]", table, s)
                    raise RuntimeError(
                        f"Table '{table}' not found in [{s},tackle,vision] on {self.db_path}. "
                        f"Start the MCP server first to initialize the database schema."
                    )
            _log.debug("_init_db: all MCP-owned tables present")

            # ── Verify critical columns on MCP-owned tables ───
            required_columns = {
                "receipts": ["ticket_id", "tokens_used"],
                "tickets": ["objective", "owner", "spawn_reason"],
                "circuit_breaker": ["paused"],
                "sessions": ["cost_usd"],
                "session_logs": ["session_id", "level", "line"],
            }
            for table, cols in required_columns.items():
                rows = conn.execute(
                    f"SELECT column_name FROM information_schema.columns "
                    f"WHERE table_schema IN ('{s}','tackle','vision') AND table_name = '{table}'"
                ).fetchall()
                existing = {r[0] for r in rows}
                missing = [c for c in cols if c not in existing]
                if missing:
                    _log.error("_init_db: columns %s missing from '%s'", missing, table)
                    raise RuntimeError(
                        f"Columns {missing} missing from '{table}' in schema '{s}'. "
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
          critic completed   → builder

        Guard: if the plan's derived_status is already terminal (REVIEW_PASS,
        BLOCK, PLAN_BLOCK), no new tickets are spawned regardless of role
        transition.  This prevents late-arriving ticket completions (e.g.
        critic finishing after the plan was already REVIEW_PASS) from
        dispatching unnecessary work.
        """
        _log.info("create_next_tickets: plan=%s ticket_role=%s terminal_status=%s", plan_id, ticket_role, terminal_status)
        with self._get_connection() as conn:
            latest = conn.execute(
                """
                SELECT type FROM receipts
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
                next_roles = ["builder"]
        elif terminal_status == "failed":
            if ticket_role == "reviewer":
                next_roles = ["builder"]
            elif ticket_role == "planner":
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
                ticket_id = f"ticket-{plan_id}-{role}-{int(datetime.utcnow().timestamp())}"
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
        AND whose derived_status matches the role's eligibility.

        Excludes plans that already have a running session for the same role+plan
        (i.e. a Temporal workflow is already executing them), so the scheduler
        doesn't waste cycles re-dispatching idempotent workflow starts."""
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
                AND NOT EXISTS (
                    SELECT 1 FROM sessions s
                    WHERE s.is_running = 1
                    AND s.workflow_id = 'plan-' || ps.id || '-reviewer'
                )
                ORDER BY ps.created_at ASC
            """
        else:
            query = f"""
                SELECT ps.* FROM plan_status ps
                JOIN tickets t ON t.plan_id = ps.id
                WHERE t.role = %s AND t.status = 'open'
                AND ps.derived_status IN ({placeholders})
                AND NOT EXISTS (
                    SELECT 1 FROM sessions s
                    WHERE s.is_running = 1
                    AND s.workflow_id = 'plan-' || ps.id || '-' || %s
                )
                ORDER BY ps.created_at ASC
            """

        with self._get_connection() as conn:
            if role == "reviewer":
                cursor = conn.execute(query, valid_statuses)
            else:
                cursor = conn.execute(query, (role, *valid_statuses, role))
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
                tripped = bool(row[0]) if row else False
                _log.debug("is_circuit_breaker_tripped: %s", tripped)
                return tripped
        except Exception as exc:
            _log.warning("is_circuit_breaker_tripped: query failed: %s", exc)
            return False

    def is_role_circuit_breaker_tripped(self, role: str) -> bool:
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT tripped FROM role_circuit_breaker WHERE role = %s",
                    (role,),
                ).fetchone()
                tripped = bool(row[0]) if row else False
                _log.debug("is_role_circuit_breaker_tripped: role=%s %s", role, tripped)
                return tripped
        except Exception as exc:
            _log.warning("is_role_circuit_breaker_tripped: role=%s query failed: %s", role, exc)
            return False

    def consume_scheduler_wake(self, since: str) -> bool:
        """Check if scheduler wake was requested since `since` timestamp.
        Returns True and clears wake_requested_at if a wake was requested."""
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT wake_requested_at FROM circuit_breaker WHERE id = 1 "
                    "AND wake_requested_at IS NOT NULL AND wake_requested_at > %s",
                    (since,),
                ).fetchone()
                if row and row[0]:
                    conn.execute(
                        "UPDATE circuit_breaker SET wake_requested_at = NULL, updated_at = %s WHERE id = 1",
                        (datetime.utcnow().isoformat() + "Z",),
                    )
                    conn.commit()
                    return True
                return False
        except Exception as exc:
            _log.debug("consume_scheduler_wake: query failed: %s", exc)
            return False

    def trip_role_circuit_breaker(
        self, role: str, error: str, retry_after: int = 1800,
    ) -> None:
        _log.warning("trip_role_circuit_breaker: role=%s error=%s retry_after=%d", role, error, retry_after)
        now = datetime.utcnow().isoformat() + "Z"
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO role_circuit_breaker (role, tripped, tripped_at, retry_after, error, failure_count, updated_at)
                    VALUES (%s, 1, %s, %s, %s, 1, %s)
                    ON CONFLICT (role) DO UPDATE SET
                        tripped = 1, tripped_at = %s, retry_after = %s,
                        error = %s, failure_count = role_circuit_breaker.failure_count + 1,
                        updated_at = %s
                    """,
                    (role, now, retry_after, error, now, now, retry_after, error, now),
                )
                conn.commit()
        except Exception as exc:
            _log.warning("trip_role_circuit_breaker: role=%s failed: %s", role, exc)

    def reset_role_circuit_breaker(self, role: str) -> None:
        _log.info("reset_role_circuit_breaker: role=%s", role)
        now = datetime.utcnow().isoformat() + "Z"
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM role_circuit_breaker WHERE role = %s", (role,))
                # Wake the scheduler so it re-polls immediately instead of
                # waiting out the idle backoff (SCHEDULER_IDLE_BACKOFF, 60s).
                conn.execute(
                    "UPDATE circuit_breaker SET wake_requested_at = %s, updated_at = %s WHERE id = 1",
                    (now, now),
                )
                conn.commit()
        except Exception as exc:
            _log.warning("reset_role_circuit_breaker: role=%s failed: %s", role, exc)

    def is_conduit_paused(self) -> bool:
        query = "SELECT paused FROM circuit_breaker WHERE id = 1"
        try:
            with self._get_connection() as conn:
                row = conn.execute(query).fetchone()
                paused = bool(row[0]) if row else False
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

    # ── Session Logs ────────────────────────────────────────────────

    def append_session_log(self, session_id: str, line: str, level: str = "INFO") -> None:
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO session_logs (session_id, level, line) VALUES (%s, %s, %s)",
                (session_id, level, line),
            )
            conn.commit()

    def append_session_logs(self, session_id: str, lines: list[tuple[str, str]]) -> None:
        """Insert multiple (level, line) pairs in a single transaction."""
        if not lines:
            return
        with self._get_connection() as conn:
            for level, line in lines:
                conn.execute(
                    "INSERT INTO session_logs (session_id, level, line) VALUES (%s, %s, %s)",
                    (session_id, level, line),
                )
            conn.commit()

    # ── Receipts (v078 — ticket_id required) ─────────────────────

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
    ):
        """Insert a receipt linked to exactly one Ticket (Invariant 2)."""
        _log.info("insert_receipt: plan=%s type=%s role=%s ticket=%s tokens=%d",
                  plan_id, receipt_type, agent_role, ticket_id, tokens_used)
        now = datetime.utcnow().isoformat() + "Z"
        receipt_id = f"rec-{plan_id}-{receipt_type}-{uuid.uuid4().hex[:8]}"
        meta_json = json.dumps(metadata or {})
        query = """
            INSERT INTO receipts (id, plan_id, type, agent_role, session_id,
                ticket_id, summary, artifact_path, metadata_json, tokens_used, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        with self._get_connection() as conn:
            conn.execute(query, (
                receipt_id, plan_id, receipt_type, agent_role, session_id,
                ticket_id, summary, artifact_path, meta_json, tokens_used, now,
            ))
            conn.execute("UPDATE plans SET updated_at = %s WHERE id = %s", (now, plan_id))
            conn.commit()
        _log.debug("insert_receipt: created %s", receipt_id)

    def add_work_request(self, wr_id: str, plan_id: str, dco_json: str):
        _log.info("add_work_request: wr=%s plan=%s", wr_id, plan_id)
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO work_requests (id, plan_id, status, dco_json, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                (wr_id, plan_id, 'pending', dco_json, now, now),
            )
            conn.commit()

    def update_work_request_status(self, wr_id: str, status: str):
        _log.debug("update_work_request_status: wr=%s status=%s", wr_id, status)
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            conn.execute("UPDATE work_requests SET status = %s, updated_at = %s WHERE id = %s", (status, now, wr_id))
            conn.commit()

    def get_work_request(self, wr_id: str) -> Optional[Dict[str, Any]]:
        _log.debug("get_work_request: wr=%s", wr_id)
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM work_requests WHERE id = %s",
                (wr_id,),
            ).dict_fetchone()
            if not row:
                _log.debug("get_work_request: wr=%s not found", wr_id)
                return None
            try:
                row["dco_json"] = json.loads(row.get("dco_json", "{}"))
            except (json.JSONDecodeError, TypeError):
                row["dco_json"] = {}
            try:
                row["step_outputs"] = json.loads(row.get("step_outputs", "{}"))
            except (json.JSONDecodeError, TypeError):
                row["step_outputs"] = {}
            _log.debug("get_work_request: wr=%s found", wr_id)
            return row

    def update_work_request_step_outputs(self, wr_id: str, step_outputs: Dict[str, Any]):
        _log.debug("update_work_request_step_outputs: wr=%s keys=%s", wr_id, list(step_outputs.keys()))
        now = datetime.utcnow().isoformat() + "Z"
        outputs_json = json.dumps(step_outputs)
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE work_requests SET step_outputs = %s, updated_at = %s WHERE id = %s",
                (outputs_json, now, wr_id),
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
            cursor = conn.execute("DELETE FROM receipts WHERE plan_id=%s AND type=%s AND session_id=%s", (plan_id, receipt_type, session_id))
            conn.commit()
            deleted = cursor.rowcount > 0
            if deleted:
                _log.info("delete_receipt: deleted plan=%s type=%s", plan_id, receipt_type)
            return deleted

    def get_plan_by_id(self, plan_id: str) -> Optional[Dict[str, Any]]:
        _log.debug("get_plan_by_id: plan=%s", plan_id)
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM plans WHERE id = %s", (plan_id,))
            plan = cursor.dict_fetchone()
            _log.debug("get_plan_by_id: plan=%s found=%s", plan_id, plan is not None)
            return plan

    def create_session(self, session_id: str, agent_role: str, plan_ids: List[str], pid: Optional[int] = None,
                       workflow_id: Optional[str] = None, run_id: Optional[str] = None,
                       workflow_start_time: Optional[str] = None):
        _log.info("create_session: session=%s role=%s plans=%s pid=%s wf=%s", session_id, agent_role, plan_ids, pid, workflow_id)
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO sessions (id, agent_role, start_iso, plans_processed, plan_count, "
                "is_running, created_at, last_activity, pid, workflow_id, run_id, workflow_start_time) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                (session_id, agent_role, now, json.dumps(plan_ids), len(plan_ids), 1, now, now,
                 pid, workflow_id, run_id, workflow_start_time),
            )
            conn.commit()

    def close_session(self, session_id: str, exit_code: int,
                      workflow_close_time: Optional[str] = None,
                      workflow_run_time_ms: Optional[float] = None,
                      workflow_result: Optional[str] = None):
        """Close a session.  Caller must release Tickets before calling."""
        _log.info("close_session: session=%s exit_code=%d wf_close=%s wf_runtime=%.0fms wf_result=%s",
                  session_id, exit_code, workflow_close_time, workflow_run_time_ms or 0, workflow_result)
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE sessions SET end_iso=%s, is_running=0, exit_code=%s, last_activity=%s, "
                "workflow_close_time = %s, "
                "workflow_run_time_ms = %s, "
                "workflow_result = %s "
                "WHERE id=%s",
                (now, exit_code, now,
                 workflow_close_time, workflow_run_time_ms, workflow_result,
                 session_id),
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

    def detect_stale_tickets(self) -> int:
        threshold = (datetime.utcnow() - timedelta(seconds=DEFAULT_STALE_SECONDS)).isoformat() + "Z"
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE tickets SET status = 'stale'
                WHERE status = 'claimed'
                AND last_activity IS NOT NULL
                AND last_activity < %s
                """,
                (threshold,),
            )
            conn.commit()
            n = cursor.rowcount
            if n:
                _log.info("detect_stale_tickets: marked %d ticket(s) as stale", n)
            return n

    def detect_expired_tickets(self) -> int:
        now = datetime.utcnow().isoformat() + "Z"
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE tickets SET status = 'expired'
                WHERE status IN ('open', 'claimed', 'stale')
                AND expires_at IS NOT NULL
                AND expires_at < %s
                """,
                (now,),
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
                FROM receipts WHERE plan_id = %s
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
                FROM receipts WHERE agent_role = %s
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

    def get_model_config(self, model_id: str) -> Optional[Dict[str, Any]]:
        """Look up a model by ID, returning model + harness + provider info.

        Returns dict with keys: id, name, model_identifier, harness_name,
        invocation_semantics, provider_name, provider_type, endpoint_url.
        Returns None if not found.
        """
        _log.debug("get_model_config: model=%s", model_id)
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT m.id, m.name, m.model_identifier,
                       h.name AS harness_name,
                       h.invocation_semantics,
                       p.name AS provider_name,
                       p.type AS provider_type,
                       p.endpoint_url
                FROM models m
                LEFT JOIN harnesses h ON h.id = m.harness_id
                LEFT JOIN providers p ON p.id = m.provider_id
                WHERE m.id = %s
                """,
                (model_id,),
            ).dict_fetchone()
            _log.debug("get_model_config: model=%s found=%s", model_id, row is not None)
            return row

    def get_role_model_config(self, role: str) -> Optional[Dict[str, str]]:
        _log.debug("get_role_model_config: role=%s", role)
        with self._get_connection() as conn:
            row = conn.execute(
                """
                SELECT h.invocation_semantics, m.model_identifier, p.type AS provider_type
                FROM config_bundle cb
                JOIN harnesses h ON cb.harness_id = h.id
                JOIN models m ON cb.model_id = m.id
                JOIN providers p ON cb.provider_id = p.id
                WHERE cb.role = %s AND cb.is_active = 1
                ORDER BY cb.priority ASC
                LIMIT 1
                """,
                (role,),
            ).fetchone()
            if not row:
                _log.debug("get_role_model_config: no config for role=%s", role)
                return None
            semantics_str, model_id, provider_type = row
            try:
                semantics = json.loads(semantics_str or '{}')
            except json.JSONDecodeError as exc:
                _log.warning("get_role_model_config: failed to parse semantics for role=%s: %s", role, exc)
                return None
            harness_binary = semantics.get('binary', '')
            if not harness_binary:
                _log.warning("get_role_model_config: no binary in semantics for role=%s", role)
                return None
            _log.debug("get_role_model_config: role=%s harness=%s model=%s provider=%s",
                       role, harness_binary, model_id, provider_type)
            return {'harness': harness_binary, 'model': model_id, 'provider_type': provider_type}

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

    def get_requeue_count(self, plan_id: str) -> int:
        """Count how many times this plan has been requeued.

        Each failed model chain produces a REQUEUED receipt via
        trip_and_requeue.  This is the number of full requeue cycles.
        """
        _log.debug("get_requeue_count: plan=%s", plan_id)
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM receipts WHERE plan_id = %s AND type = 'REQUEUED'",
                (plan_id,),
            ).fetchone()
            count = row[0] if row else 0
            _log.debug("get_requeue_count: plan=%s count=%d", plan_id, count)
            return count

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

        # 1. Trip circuit breaker (global + per-role)
        self.trip_circuit_breaker(
            error=error,
            detail=detail or f"All retries/fallbacks exhausted for plan {plan_id}",
            source=source,
            retry_after=retry_after,
        )
        self.trip_role_circuit_breaker(
            role=role,
            error=error,
            retry_after=retry_after,
        )

        now = datetime.utcnow().isoformat() + "Z"

        if not config["push_back_to_pending"]:
            _log.info("trip_and_requeue: push_back_to_pending disabled, just tripping breaker")
            return

        # 2. Cancel existing open ticket (if any) for this plan/role,
        #    then create a fresh builder Ticket so the REQUEUED receipt
        #    has a valid FK target.
        expires_at = (datetime.utcnow() + timedelta(hours=DEFAULT_TICKET_TTL_HOURS)).isoformat() + "Z"
        builder_ticket_id = f"ticket-{plan_id}-builder-{int(datetime.utcnow().timestamp())}"
        with self._get_connection() as conn:
            # Cancel any existing open ticket first (partial unique index
            # idx_tickets_open only allows one open ticket per plan/role).
            conn.execute(
                "UPDATE tickets SET status = 'cancelled', closure_reason = 'superseded_by_requeue', "
                "closed_at = %s WHERE plan_id = %s AND role = 'builder' AND status = 'open'",
                (now, plan_id),
            )
            conn.execute(
                """
                INSERT INTO tickets
                    (id, plan_id, role, status, created_at,
                     objective, owner,
                     spawn_reason, last_activity, expires_at)
                VALUES (%s, %s, %s, 'open', %s,
                        %s, %s,
                        %s, %s, %s)
                """,
                (builder_ticket_id, plan_id, "builder", now,
                 f"Requeued after circuit breaker (ref: {error})", "builder",
                 "circuit_breaker_requeue", now, expires_at),
            )
            conn.commit()

        # 3. Insert REQUEUED receipt referencing the fresh builder ticket
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
        """Return fallback models for a role from config_bundle, ordered by priority.

        Each entry has: model_identifier, provider_type, provider_id, api_key,
        endpoint_url, harness_name, harness_id, invocation_semantics, priority.
        """
        _log.debug("get_fallback_models: role=%s", role)
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT cb.priority,
                       m.model_identifier,
                       p.type          AS provider_type,
                       p.api_key,
                       p.endpoint_url,
                       p.id            AS provider_id,
                       h.name          AS harness_name,
                       h.id            AS harness_id,
                       h.invocation_semantics
                FROM config_bundle cb
                JOIN models m        ON cb.model_id = m.id
                LEFT JOIN providers p ON cb.provider_id = p.id
                LEFT JOIN harnesses h ON cb.harness_id = h.id
                WHERE cb.role = %s AND cb.is_active = 1
                ORDER BY cb.priority ASC
                """,
                (role,),
            )
            rows = cursor.dict_fetchall()

        _log.debug("get_fallback_models: role=%s found %d models", role, len(rows))
        results: list = []
        for row in rows:
            semantics = row.get("invocation_semantics") or "{}"
            try:
                semantics_parsed = json.loads(semantics)
            except (json.JSONDecodeError, TypeError):
                semantics_parsed = {}
            results.append({
                "priority": row["priority"],
                "model_identifier": row["model_identifier"],
                "provider_type": row.get("provider_type") or "",
                "provider_id": row.get("provider_id") or "",
                "api_key": row.get("api_key") or "",
                "endpoint_url": row.get("endpoint_url") or "",
                "harness_name": row.get("harness_name") or "",
                "harness_id": row.get("harness_id") or "",
                "invocation_semantics": semantics_parsed,
            })
        _log.debug("get_fallback_models: role=%s returning %d models with semantics", role, len(results))
        return results
