#!/usr/bin/env python3
"""continuity.digest — digest v0 assembler (read-only, canonical surfaces).

Assembles a role-adoption continuity digest from three canonical surfaces:

  1. OPEN INBOX   — nebula.agent_records tagged ``to:<role>`` that are
                    unresolved (no ``status:resolved``/``status:closed`` tag)
                    or recent (< 7 days), newest first.
  2. OPEN THREADS — Assembly to-do forum threads lacking completion replies
                    (shared surface; surfaced with an explicit
                    ``shared_surface: true`` marker so consumers know the
                    thread is not role-private).
  3. RECENT RECORDS — the role's own most recent agent records,
                    **metadata only** (id/title/record_type/level/created_at).
                    No content summarization ⇒ altitude-safe by construction:
                    an L2 ceiling cannot leak L4 claims because no L4 body
                    text is ever read.

Governance encoded in v0 (per the roundtable design):

  * I2 disposition — the digest carries a hard ``disposition: context-only``
    marker. It is pointers, never verdict content: a closed decision appears
    only as its title/id, never as restated rationale.
  * Level ceiling  — ``nebula.roles.level_filter_allowed`` for the role is
    resolved at assembly time and stamped into ``level_provenance``.
    (v0 reads no record content, so the ceiling is honored structurally;
    content-bearing digests in later steps query WITH this ceiling.)
  * Model identity — ``assembled_for_model`` is a caller-supplied stamp
    (R-1..R-3): what the role knows ≠ what this model was told.
  * No chat, no LLM, no persistence — v0 is explicit. Persistence goes to
    session_context_snapshots only after the roundtable answers Q1/Q2
    (step 4).

Every fetcher is injectable; the module never opens a socket itself, so
tests are hermetic. The CLI wires the fetchers to the live REST surfaces.
"""

from __future__ import annotations

import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

DIGEST_VERSION = "v0"
DISPOSITION = "context-only"  # I2: pointers, never verdict content
DEFAULT_NEBS = "http://localhost:3101"
DEFAULT_ASSEMBLY = "http://localhost:3107"
RECENT_WINDOW_DAYS = 7
INBOX_LIMIT = 20
RECORDS_LIMIT = 10
THREADS_LIMIT = 10

Fetcher = Callable[[], Any]


# ── Fetchers (live wiring lives here; tests inject stubs) ────────────────────


def _get_json(url: str, timeout: int = 8) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json_loads(r.read())


def json_loads(b: bytes) -> Any:
    import json
    return json.loads(b.decode())


def fetch_open_inbox(role: str, base: str = DEFAULT_NEBS) -> List[Dict[str, Any]]:
    """Unresolved or recent to:<role> agent records (nebula REST :3101)."""
    items = _get_json(
        f"{base}/api/agent-records?role={role}&limit={INBOX_LIMIT * 2}") or {}
    out = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_WINDOW_DAYS)
    for it in items.get("items", []):
        tags = it.get("tags") or []
        tag_text = " ".join(tags).lower()
        addressed = f"to:{role.lower()}" in tag_text
        resolved = any(t in tag_text for t in ("status:resolved", "status:closed", "status:done"))
        created = _parse_dt(it.get("createdAt") or it.get("created_at"))
        if addressed and (not resolved or (created and created > cutoff)):
            out.append({
                "record_id": it.get("id"),
                "title": it.get("title"),
                "record_type": it.get("recordType") or it.get("record_type"),
                "tags": tags,
                "created_at": _iso(created),
            })
        if len(out) >= INBOX_LIMIT:
            break
    return out


def fetch_open_threads(base: str = DEFAULT_ASSEMBLY) -> List[Dict[str, Any]]:
    """Open to-do threads (Assembly :3107), completion-reply detection left
    to the caller thread-detail pass in later steps; v0 flags the surface."""
    threads = _get_json(f"{base}/api/forums/to-do/threads") or []
    out = []
    for t in threads[:THREADS_LIMIT]:
        out.append({
            "thread_id": t.get("id"),
            "title": t.get("title"),
            "created_at": t.get("createdAt") or t.get("created_at"),
            "shared_surface": True,  # not role-private — never authority-bearing
        })
    return out


def fetch_recent_records(role: str, base: str = DEFAULT_NEBS) -> List[Dict[str, Any]]:
    """The role's own recent records — METADATA ONLY (no content field)."""
    items = _get_json(
        f"{base}/api/agent-records?role={role}&limit={RECORDS_LIMIT}") or {}
    out = []
    for it in items.get("items", [])[:RECORDS_LIMIT]:
        created = _parse_dt(it.get("createdAt") or it.get("created_at"))
        out.append({
            "record_id": it.get("id"),
            "title": it.get("title"),
            "record_type": it.get("recordType") or it.get("record_type"),
            "level": it.get("level"),
            "created_at": _iso(created),
            # deliberately NO content — the altitude-safety guarantee
        })
    return out


def fetch_level_ceiling(role: str, base: str = DEFAULT_NEBS) -> Optional[str]:
    """nebula.roles.level_filter_allowed for the role (may be None).
    Accepts both snake_case (DB) and camelCase (REST) key conventions."""
    try:
        rows = _get_json(f"{base}/api/roles") or {}
        rows = rows.get("items", rows) if isinstance(rows, dict) else rows
        for r in rows or []:
            if (r.get("name") or "").lower() == role.lower():
                return (r.get("level_filter_allowed")
                        or r.get("levelFilterAllowed"))
    except Exception:
        return None
    return None


# ── Assembly ─────────────────────────────────────────────────────────────────


def assemble_digest(
    role: str,
    assembled_for_model: str = "",
    fetch_inbox: Optional[Fetcher] = None,
    fetch_threads: Optional[Fetcher] = None,
    fetch_records: Optional[Fetcher] = None,
    level_ceiling: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Assemble the digest payload. Never raises on source errors — failed
    surfaces degrade to ``sources_degraded`` entries (availability over
    completeness for a context-only artifact)."""
    now = now or datetime.now(timezone.utc)
    degraded: List[str] = []

    def _safe(name: str, fn: Fetcher, default):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — degrade, never fail the digest
            degraded.append(f"{name}: {e}")
            return default

    inbox = _safe("inbox", fetch_inbox or (lambda: []), [])
    threads = _safe("threads", fetch_threads or (lambda: []), [])
    records = _safe("records", fetch_records or (lambda: []), [])

    return {
        "digest_version": DIGEST_VERSION,
        "disposition": DISPOSITION,  # I2: context-only, never a decision
        "role": role,
        "assembled_for_model": assembled_for_model,  # R-1..R-3 stamp
        "as_of": now.isoformat(),
        "level_provenance": {
            "level_filter_allowed": level_ceiling,
            "applied_at": "source-query" if level_ceiling else "metadata-only-v0",
        },
        "open_inbox": inbox,
        "open_threads": threads,
        "recent_records_metadata": records,
        "sources_degraded": degraded,
        "counts": {
            "open_inbox": len(inbox),
            "open_threads": len(threads),
            "recent_records": len(records),
        },
    }


def _parse_dt(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, (int, float)):  # epoch ms
        return datetime.fromtimestamp(v / 1000.0, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(d: Optional[datetime]) -> Optional[str]:
    return d.isoformat() if d else None


# ── CLI (manual inspection + future boot-shim/operator hook) ─────────────────


def _live_fetchers(role: str):
    nebs = os.environ.get("NEBULA_URL", DEFAULT_NEBS)
    asm = os.environ.get("ASSEMBLY_URL", DEFAULT_ASSEMBLY)
    return (
        lambda: fetch_open_inbox(role, nebs),
        lambda: fetch_open_threads(asm),
        lambda: fetch_recent_records(role, nebs),
        fetch_level_ceiling(role, nebs),
    )


def main(argv: List[str]) -> int:
    import argparse
    import json
    import sys

    p = argparse.ArgumentParser(description="Assemble a role continuity digest (v0, read-only)")
    p.add_argument("role")
    p.add_argument("--model", default=os.environ.get("NEXUS_AGENT_MODEL", ""),
                   help="model identity stamp (R-1..R-3)")
    args = p.parse_args(argv)

    fin, fth, frec, ceiling = _live_fetchers(args.role)
    digest = assemble_digest(args.role, args.model, fin, fth, frec, ceiling)
    json.dump(digest, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
