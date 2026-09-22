"""blackboard — boot-shim slice 2 (thread 88385a46, V192 companion).

Renders the per-role coordination digest from nebula.v_coordination_blackboard
at session start, with a read-through Redis TTL cache.

Design contract (position post 97131e6f, hardening points 1-3):
- READ-ONLY: this module never writes blackboard items. The only write it may
  perform is the deliberate checkpoint advance (advance_checkpoints), which is
  an explicit agent act (--blackboard-advance), never automatic.
- Degrade, never fail: absent view (V192 not applied) -> inert-skip; PG down ->
  degraded; Redis down -> render without cache. Nothing here raises to the
  caller on environmental conditions; the shim's boot is never failed.
- Cache: one key per role (nexus:blackboard:<role>), JSON payload, TTL
  seconds (default 300). Read-through: PG is queried only on miss. Redis
  speaks the house raw-socket protocol (cascade-event-bridge precedent —
  no client dependency).

Bucket vocabulary is To Do lifecycle policy v0.1 (ac2d1382): action-needed,
awaiting-pickup, stale, in-flight, done, unrouted (to-do fold); action-needed,
seen (inbox fold); checkpoint (freshness rows).
"""
from __future__ import annotations

import json
import socket

VIEW = "nebula.v_coordination_blackboard"
TODO_BUCKETS = ("action-needed", "awaiting-pickup", "stale",
                "in-flight", "done", "unrouted")
INBOX_BUCKETS = ("action-needed", "seen")
CACHE_PREFIX = "nexus:blackboard:"
DEFAULT_TTL = 300

DIGEST_VERSION = "bb-v0.1"


# ── Redis (zero-dependency, house raw-socket pattern) ────────────────────────

class _BufReader:
    """Buffered socket reader: never loses bytes past a CRLF (a raw recv in
    the line reader would drop bulk payloads arriving in the same segment)."""

    def __init__(self, sock):
        self.sock = sock
        self.buf = b""

    def _fill(self):
        chunk = self.sock.recv(4096)
        if not chunk:
            raise EOFError("redis closed connection")
        self.buf += chunk

    def read_line(self) -> bytes:
        """One CRLF-terminated line, terminator INCLUDED."""
        while b"\r\n" not in self.buf:
            self._fill()
        i = self.buf.index(b"\r\n")
        line, self.buf = self.buf[:i + 2], self.buf[i + 2:]
        return line

    def read_exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            self._fill()
        out, self.buf = self.buf[:n], self.buf[n:]
        return out


def _redis_cmd(sock, *parts: bytes) -> bytes | None:
    """Send one RESP array command, read one reply (None on nil)."""
    out = [b"*%d\r\n" % len(parts)]
    for p in parts:
        out.append(b"$%d\r\n%s\r\n" % (len(p), p))
    sock.sendall(b"".join(out))
    r = _BufReader(sock)
    line = r.read_line()
    t, rest = line[:1], line[1:-2]
    if t == b"$":
        n = int(rest)
        if n == -1:
            return None
        body = r.read_exact(n)
        r.read_exact(2)  # trailing CRLF
        return body
    if t in (b"+", b":"):
        return rest
    if t == b"-":
        raise RuntimeError(rest.decode(errors="replace"))
    raise RuntimeError(f"unexpected RESP type {t!r}")


class RedisCache:
    """Minimal GET/SET-EX read-through cache. `available` flips False on any
    environmental failure so callers degrade instead of retrying."""

    def __init__(self, host="localhost", port=6379, timeout=1.5):
        self.host, self.port, self.timeout = host, port, timeout
        self.available = True

    def _conn(self):
        s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        s.settimeout(self.timeout)
        return s

    def get(self, key: str):
        if not self.available:
            return None
        try:
            with self._conn() as s:
                return _redis_cmd(s, b"GET", key.encode())
        except Exception:  # noqa: BLE001 — degrade to no-cache
            self.available = False
            return None

    def setex(self, key: str, ttl: int, value: str) -> None:
        if not self.available:
            return
        try:
            with self._conn() as s:
                _redis_cmd(s, b"SET", key.encode(), value.encode(),
                           b"EX", str(int(ttl)).encode())
        except Exception:  # noqa: BLE001
            self.available = False


# ── DB surface ───────────────────────────────────────────────────────────────

def view_present(conn) -> bool:
    """Inert gate: is nebula.v_coordination_blackboard live?"""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (VIEW,))
        return bool(cur.fetchone()[0])


def fetch_role_rows(conn, role: str) -> list[dict]:
    role = canonical_role(role)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT item_kind, bucket, title, status_rating, created, reason "
            "FROM nebula.v_coordination_blackboard WHERE routed_role = %s "
            "ORDER BY item_kind, bucket, created DESC NULLS LAST",
            (role,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def canonical_role(role: str) -> str:
    """Canonical form for coordination surfaces: lowercase, trimmed.

    nebula.roles stores lowercase names (dba, lead-engineer,
    design-synthesist) and nebula.coordination_checkpoints.role carries a
    FK resolved against them — but callers pass harness-case roles (DBA).
    Before this guard, the mixed-case upsert silently degraded session
    bookkeeping (FK violation swallowed as 'degraded'). Canonicalizing
    here covers every entry point: advance_checkpoints (upsert),
    fetch_role_rows (view read), and the digest cache key.
    """
    return (role or "").strip().lower()


def advance_checkpoints(conn, role: str, model: str | None = None,
                        kinds: tuple[str, ...] = ("inbox", "todo")) -> int:
    """The deliberate agent act: mark every kind reviewed up to now().
    Returns the number of checkpoint rows advanced. Upsert so roles created
    after V192's born-seed still get a row. Role is canonicalized
    (lowercase) before the FK-resolved upsert — mixed-case input used to
    fail the role FK and silently degrade the advance."""
    role = canonical_role(role)
    n = 0
    with conn.cursor() as cur:
        for kind in kinds:
            cur.execute(
                "INSERT INTO nebula.coordination_checkpoints "
                " (role, item_kind, last_reviewed_at, reviewed_by_model, note) "
                "VALUES (%s, %s, now(), %s, 'advanced by boot-shim slice 2') "
                "ON CONFLICT (role, item_kind) DO UPDATE "
                " SET last_reviewed_at = now(), reviewed_by_model = EXCLUDED.reviewed_by_model,"
                "     note = EXCLUDED.note, updated_at = now()",
                (role, kind, model))
            n += cur.rowcount
    conn.commit()
    return n


def advance_role_checkpoints(role: str, dsn: str, model: str | None = None,
                             conn_factory=None,
                             kinds: tuple[str, ...] = ("inbox", "todo")) -> dict:
    """Standalone end-of-turn advance (session-protocol v2, R17.1).

    Checkpoints only — no digest, no other boot work. Returns
      {status: ok, advanced: n} | {status: degraded, reason}
    and never raises on environmental conditions. conn_factory is
    injectable for hermetic tests (defaults to real psycopg2, 5s timeout);
    psycopg2 absent without an injected factory degrades honestly rather
    than crashing (CI-hermeticity pitfall #15).
    """
    try:
        import psycopg2  # noqa: F401 — needed by the default factory only
    except ImportError:
        if conn_factory is None:
            return {"status": "degraded", "reason": "psycopg2 unavailable"}
    if conn_factory is None:
        def conn_factory(dsn_, _psycopg2=psycopg2):
            return _psycopg2.connect(dsn_, connect_timeout=5)
    try:
        conn = conn_factory(dsn)
        try:
            n = advance_checkpoints(conn, role, model, kinds=kinds)
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        return {"status": "ok", "advanced": n}
    except Exception as e:  # noqa: BLE001 — degraded, never turn-fatal
        return {"status": "degraded", "reason": f"{e.__class__.__name__}: {e}"}


# ── render ───────────────────────────────────────────────────────────────────

def render_digest(rows: list[dict], role: str) -> dict:
    """Pure function: rows -> digest. Bucket-order stability pinned by tests."""
    d: dict = {"digest_version": DIGEST_VERSION, "role": role,
               "todo": {b: [] for b in TODO_BUCKETS},
               "inbox": {"action-needed": [], "seen_count": 0},
               "checkpoints": {}}
    for r in rows:
        kind, bucket = r["item_kind"], r["bucket"]
        if kind == "checkpoint":
            d["checkpoints"][r["title"]] = r["reason"]
        elif kind == "todo" and bucket in d["todo"]:
            d["todo"][bucket].append(
                {"title": r["title"], "rating": r["status_rating"],
                 "age_created": str(r["created"]), "reason": r["reason"]})
        elif kind == "inbox":
            if bucket == "action-needed":
                d["inbox"]["action-needed"].append(
                    {"title": r["title"], "reason": r["reason"]})
            else:
                d["inbox"]["seen_count"] += 1
    d["counts"] = {
        "todo_action_needed": len(d["todo"]["action-needed"]),
        "todo_awaiting_pickup": len(d["todo"]["awaiting-pickup"]),
        "todo_stale": len(d["todo"]["stale"]),
        "todo_in_flight": len(d["todo"]["in-flight"]),
        "todo_unrouted": len(d["todo"]["unrouted"]),
        "inbox_action_needed": len(d["inbox"]["action-needed"]),
        "inbox_seen": d["inbox"]["seen_count"],
        "checkpoints_never_reviewed": sum(
            1 for v in d["checkpoints"].values() if "never" in str(v)),
    }
    return d


def format_digest(d: dict) -> str:
    """Human-facing one-screen summary (the shim prints this, then the JSON)."""
    c = d["counts"]
    lines = [f"blackboard [{d['role']}]: "
             f"todo action-needed={c['todo_action_needed']} "
             f"awaiting-pickup={c['todo_awaiting_pickup']} "
             f"stale={c['todo_stale']} in-flight={c['todo_in_flight']} "
             f"unrouted={c['todo_unrouted']} | "
             f"inbox new={c['inbox_action_needed']} "
             f"seen={c['inbox_seen']}"]
    for item in d["todo"]["action-needed"][:5]:
        lines.append(f"  ! {item['title'][:90]}")
    for item in d["inbox"]["action-needed"][:5]:
        lines.append(f"  + {item['title'][:90]}")
    if c["todo_stale"]:
        for item in d["todo"]["stale"][:3]:
            lines.append(f"  ~ STALE {item['title'][:85]}")
    if c["checkpoints_never_reviewed"]:
        lines.append(f"  · {c['checkpoints_never_reviewed']} checkpoint(s) never reviewed")
    return "\n".join(lines)


# ── entry point ──────────────────────────────────────────────────────────────

def render_role_digest(role: str, dsn: str, cache: RedisCache | None = None,
                       cache_ttl: int = DEFAULT_TTL, advance: bool = False,
                       model: str | None = None,
                       conn_factory=None) -> dict:
    """One call for the shim: inert-gate -> cache -> PG -> digest -> advance.

    Returns a result dict:
      {status: ok|inert-skip|degraded, digest?, format?, reason?, cache: hit|miss|off}
    Never raises on environmental conditions. conn_factory is injectable for
    hermetic tests (defaults to a real psycopg2 connect with 5s timeout).
    """
    try:
        import psycopg2  # noqa: F401 — needed by the default factory only
    except ImportError:
        if conn_factory is None:
            return {"status": "degraded", "reason": "psycopg2 unavailable",
                    "cache": "off"}

    if conn_factory is None:
        def conn_factory(dsn_, _psycopg2=psycopg2):
            return _psycopg2.connect(dsn_, connect_timeout=5)

    role = canonical_role(role)
    try:
        conn = conn_factory(dsn)
    except Exception as e:  # noqa: BLE001
        return {"status": "degraded",
                "reason": f"pg connect failed: {e.__class__.__name__}",
                "cache": "off"}

    try:
        if not view_present(conn):
            return {"status": "inert-skip",
                    "reason": f"{VIEW} absent — V192 staged inert, apply on operator go",
                    "cache": "off"}

        key = CACHE_PREFIX + role
        cached = cache.get(key) if cache else None
        if cached:
            digest = json.loads(cached)
            src = "hit"
        else:
            rows = fetch_role_rows(conn, role)
            digest = render_digest(rows, role)
            src = "miss"
            if cache is not None:
                cache.setex(key, cache_ttl, json.dumps(digest, default=str))

        if advance:
            digest["checkpoints_advanced"] = advance_checkpoints(conn, role, model)
            if cache is not None and src == "miss":
                cache.setex(key, cache_ttl, json.dumps(digest, default=str))

        return {"status": "ok", "digest": digest,
                "format": format_digest(digest), "cache": src}
    except Exception as e:  # noqa: BLE001 — degraded, never boot-fatal
        return {"status": "degraded", "reason": f"{e.__class__.__name__}: {e}",
                "cache": "off"}
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
