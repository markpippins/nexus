#!/usr/bin/env python3
"""continuity.census — the agent connection record (session affordance census).

Complement to continuity.digest: the digest documents what a role should
KNOW; the census documents what an execution context could actually SEE AND
DO at session start — MCP tools registered, procedure-card visibility, inbox
status, handoff/digest availability, keychain availability. Proposed by the
operator in the continuity thread (65fe85a8) after the 2026-09-16 crosstalk
incident made the gap concrete: agent records claim role actions while the
context may have had no affordances at all.

Design positions (mirroring continuity.persist):

* **Refs-only.** The census records WHAT WAS AVAILABLE, never WHAT IT SAID —
  tool names, card counts/slugs, inbox counts, digest counts. No payloads,
  no card bodies, no record content. Altitude-safe by construction.
* **Absence is data.** A surface that could not be reached is recorded as
  ``{"available": false, "reason": ...}`` — never silently omitted, never
  faked. Keychains are recorded as explicitly absent: no keychain surface
  exists in the system today (repo-wide audit 2026-09-16).
* **Inert by table detection.** ``nebula.agent_connections`` (V169 pre-stage)
  does not exist on live until applied; every record attempt short-circuits
  with an explicit reason, and undefined_table (42P01) is classified as the
  inert gate, not an error. When V169 lands, recording activates with zero
  code changes.
* **Never raises on the recording path** — every failure mode returns a
  result dict; the boot shim degrades and keeps booting.
* **"Leased but blind" is a valid record.** No constraint forces non-empty
  affordances: a leased boot with zero reachable surfaces is exactly the
  observation the census exists to catch.

Result shape::

    {"recorded": bool, "reason": str, "conn_id": str | None}
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Callable, Dict, List, Optional

CONNECTIONS_TABLE = "nebula.agent_connections"
DEFAULT_PSQL = ["docker", "exec", "-i", "pgvector_db", "psql",
                "-U", "pguser", "-d", "nexus", "-X", "-qAt", "-v",
                "ON_ERROR_STOP=1"]

# Servers probed for tool registries (name -> base URL env override key).
MCP_SERVERS = (
    ("nebula-mcp", "NEBULA_MCP_BASE", "http://localhost:3102"),
    ("tackle-mcp", "TACKLE_MCP_BASE", "http://localhost:3400"),
)

KEYCHAINS_ABSENT = {
    "available": False,
    "reason": "no keychain surface exists in the system (repo-wide audit 2026-09-16)",
}


def _parse_mcp_result(mcp_result: Any) -> Dict[str, Any]:
    """Flatten an MCP tool result (text content blocks) into a dict.

    Local mirror of the shim's text_of/parse_json pair (the client library
    ships no JSON helpers). Returns {} on any parse failure — the probe
    layer turns that into an explicit absent marker.
    """
    try:
        if isinstance(mcp_result, dict) and isinstance(mcp_result.get("content"), list):
            text = "".join(
                c.get("text", "") for c in mcp_result["content"]
                if isinstance(c, dict) and c.get("type") == "text")
        elif isinstance(mcp_result, str):
            text = mcp_result
        else:
            text = json.dumps(mcp_result or {})
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


# ── probes (each individually exception-safe) ───────────────────────────────

def probe_mcp_tools(client_factory: Callable[[str], Any]) -> List[Dict[str, str]]:
    """[{server, tool}] refs for every registered MCP tool, per server.

    ``client_factory(base_url) -> client`` with a ``list_tools()`` method.
    A server that cannot be reached contributes nothing to the list — its
    absence IS the census datum (the shim's degraded preflight records why).
    """
    tools: List[Dict[str, str]] = []
    for name, env_key, default in MCP_SERVERS:
        base = os.environ.get(env_key, default)
        try:
            client = client_factory(base)
            for t in client.list_tools() or []:
                tool = t.get("name") if isinstance(t, dict) else getattr(t, "name", None)
                if tool:
                    tools.append({"server": name, "tool": str(tool)})
        except Exception:  # noqa: BLE001 — unreachable server = absent tools
            continue
    return tools


def probe_procedure_cards(role: str, fetch_index: Callable[[str], Dict[str, Any]]) -> Dict[str, Any]:
    """Procedure-card visibility for the role: {available, count, index}."""
    try:
        data = fetch_index(role) or {}
        count = int(data.get("count", 0) or 0)
        slugs = [p.get("slug") for p in (data.get("procedures") or [])
                 if isinstance(p, dict) and p.get("slug")]
        return {"available": True, "count": count, "index": slugs}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": f"{type(e).__name__}: {str(e)[:120]}"}


def probe_inbox(role: str, fetch_inbox: Callable[[str], Dict[str, Any]]) -> Dict[str, Any]:
    """Inbox status at boot: {available, pending_count} (refs-only)."""
    try:
        data = fetch_inbox(role) or {}
        return {"available": True,
                "pending_count": len(data.get("items") or []),
                "pointer": data.get("pointer")}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "reason": f"{type(e).__name__}: {str(e)[:120]}"}


def build_handoff(digest_result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Handoff-context availability from the shim's digest step outcome.

    ``digest_result`` is the shim's digest-step summary: either None/absent
    (flag off, package missing, or assembly degraded) or a dict carrying
    at least counts. Snapshot availability is asserted at write time by
    the recording layer (same inert probe as V167's).
    """
    if not digest_result:
        return {"digest_available": False,
                "reason": "no digest assembled (flag off, package absent, or degraded)"}
    counts = digest_result.get("counts") or {}
    return {
        "digest_available": True,
        "digest_version": digest_result.get("digest_version"),
        "counts": {k: counts.get(k) for k in
                   ("open_inbox", "open_threads", "recent_records")},
        "degraded_surfaces": len(digest_result.get("sources_degraded") or []),
    }


# ── census assembly ─────────────────────────────────────────────────────────

def collect_census(role: str, model: str, channel: str,
                   session_id: Optional[str] = None,
                   lease_ref: Optional[str] = None,
                   digest_result: Optional[Dict[str, Any]] = None,
                   client_factory: Optional[Callable[[str], Any]] = None,
                   fetch_index: Optional[Callable[[str], Dict[str, Any]]] = None,
                   fetch_inbox: Optional[Callable[[str], Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Assemble the census row dict (refs-only). Never raises.

    Inject ``client_factory``/``fetch_index``/``fetch_inbox`` in tests; the
    defaults probe the real MCP servers via the house client.
    """
    if client_factory is None:
        from nebula_mcp_client import McpClient  # type: ignore

        def client_factory(base_url: str):  # noqa: F811 — default over house path
            return McpClient(base_url)

    if fetch_index is None:
        def fetch_index(r: str) -> Dict[str, Any]:  # noqa: F811
            client = client_factory(os.environ.get(
                "TACKLE_MCP_BASE", "http://localhost:3400"))
            return _parse_mcp_result(client.call("memory_get_procedures", {"role": r}))

    if fetch_inbox is None:
        def fetch_inbox(r: str) -> Dict[str, Any]:  # noqa: F811
            client = client_factory(os.environ.get(
                "NEBULA_MCP_BASE", "http://localhost:3102"))
            return _parse_mcp_result(client.call(
                "nebula_get_inbox", {"role": r, "limit": 1}))

    return {
        "session_id": session_id,  # nullable: un-shimmed sessions have none
        "role": role,
        "model": model,
        "channel": channel,
        "lease_ref": lease_ref,    # nullable-but-explicit: unleased boot is marked
        "mcp_tools": probe_mcp_tools(client_factory),
        "procedure_cards": probe_procedure_cards(role, fetch_index),
        "inbox_status": probe_inbox(role, fetch_inbox),
        "handoff_context": build_handoff(digest_result),
        "keychains": dict(KEYCHAINS_ABSENT),
    }


# ── recording (inert until V169 applies) ────────────────────────────────────

def _psql_args() -> list:
    return [a for a in os.environ.get(
        "CONTINUITY_PSQL_ARGS", "").split("\x1f") if a] or list(DEFAULT_PSQL)


def table_exists(exec_fn: Callable[[str], str]) -> bool:
    out = exec_fn(f"SELECT to_regclass('{CONNECTIONS_TABLE}');")
    return bool(out and out.strip() and out.strip() != "")


def record_connection(census: Dict[str, Any], exec_fn=None) -> Dict[str, Any]:
    """Record the census as an agent_connections row. Never raises.

    ``exec_fn(sql) -> str`` injectable for tests; defaults to the house
    docker-psql path. Inert (explicit reason) while V169 is unapplied.
    """
    if exec_fn is None:
        def exec_fn(sql: str) -> str:  # noqa: F811 — default over the house path
            r = subprocess.run(_psql_args(), input=sql, capture_output=True,
                               text=True, timeout=30)
            if r.returncode != 0:
                raise RuntimeError((r.stderr or "psql error").strip()[:300])
            return r.stdout.strip()

    role = (census.get("role") or "").strip()
    if not role:
        return {"recorded": False, "reason": "census has no role — cannot attribute",
                "conn_id": None}

    def _j(v: Any) -> Optional[str]:
        return json.dumps(v) if v is not None else None

    def _lit(v: Optional[str]) -> str:
        return "" if v is None else str(v).replace("'", "''")

    lease_ref = census.get("lease_ref")
    try:
        if not table_exists(exec_fn):
            return {
                "recorded": False,
                "reason": ("V169 pre-stage not applied — inert gate: no "
                           f"{CONNECTIONS_TABLE} table"),
                "conn_id": None,
            }

        lease_sql = f"'{_lit(str(lease_ref))}'::uuid" if lease_ref else "NULL"
        conn_id = exec_fn(
            "WITH ins AS ("
            " INSERT INTO nebula.agent_connections"
            " (session_id, role, model, channel, lease_ref,"
            "  mcp_tools, procedure_cards, inbox_status, handoff_context, keychains)"
            " VALUES ("
            f" '{_lit(census.get('session_id'))}'"
            f", '{_lit(role)}'"
            f", '{_lit(census.get('model'))}'"
            f", '{_lit(census.get('channel'))}'"
            f", {lease_sql}"
            f", '{_lit(_j(census.get('mcp_tools') or []))}'::jsonb"
            f", '{_lit(_j(census.get('procedure_cards')))}'::jsonb"
            f", '{_lit(_j(census.get('inbox_status')))}'::jsonb"
            f", '{_lit(_j(census.get('handoff_context')))}'::jsonb"
            f", '{_lit(_j(census.get('keychains') or dict(KEYCHAINS_ABSENT)))}'::jsonb"
            " ) RETURNING conn_id::text"
            ") SELECT conn_id FROM ins;"
        )
        return {
            "recorded": True,
            "reason": "connection record persisted (boot-time affordance attestation)",
            "conn_id": (conn_id or "").strip() or None,
        }
    except Exception as e:  # noqa: BLE001 — recording path never raises
        text = str(e)
        if "42P01" in text or ("does not exist" in text and "agent_connections" in text):
            # undefined_table: surface vanished between probe and INSERT —
            # classify as the inert gate, not a generic error.
            return {
                "recorded": False,
                "reason": (f"V169 pre-stage not applied — inert gate: "
                           f"{CONNECTIONS_TABLE} absent"),
                "conn_id": None,
            }
        return {"recorded": False,
                "reason": f"recording error (inert, caller continues): {e}",
                "conn_id": None}
