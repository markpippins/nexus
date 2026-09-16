#!/usr/bin/env python3
"""continuity.digest — role-adoption digest assembler (read-only, canonical surfaces).

Assembles a role-adoption continuity digest from four canonical surfaces:

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
  4. LIVE LEASE   — the role's newest live role lease (v0.1): the binding
                    the V167 snapshot design expects. ``lease_ref`` is
                    nullable-but-explicit — an unleased digest says so
                    rather than omitting the question. A lease whose role
                    disagrees with the digest role is REFUSED (role_agreement
                    false, lease_ref null): a lease is a scope artifact,
                    not an authority grant (mirrors V167 SNAP002).

Governance encoded (per the roundtable design):

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

DIGEST_VERSION = "v0.1"  # v0.1: additive lease_binding block (projection shape change ⇒ new version)
DISPOSITION = "context-only"  # I2: pointers, never verdict content
DEFAULT_NEBS = "http://localhost:3101"
DEFAULT_ASSEMBLY = "http://localhost:3107"
RECENT_WINDOW_DAYS = 7
INBOX_LIMIT = 20
RECORDS_LIMIT = 10
THREADS_LIMIT = 10

Fetcher = Callable[[], Any]
LeaseFetcher = Callable[[str], Optional[Dict[str, Any]]]


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


def fetch_live_lease(role: str, base: str = DEFAULT_NEBS,
                     now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Newest live lease for role via nebula REST (may be None).

    Liveness predicate is applied client-side and mirrors BOTH
    operator_svc.lease_check and nebula-srv /api/role-leases/stale:
    status ACTIVE + expires_at (when set) not past + budget (when set)
    not exhausted. One definition of "live lease", three implementations,
    test-pinned to match.
    """
    resp = _get_json(
        f"{base}/api/role-leases?role={role}&status=ACTIVE&limit=10") or {}
    items = resp.get("items", []) if isinstance(resp, dict) else []
    now = now or datetime.now(timezone.utc)
    for row in items:
        if (row.get("status") != "ACTIVE"):
            continue
        exp = _parse_dt(row.get("expires_at"))
        if exp is not None and exp < now:
            continue
        budget = row.get("budget_units")
        consumed = row.get("consumed_units")
        if budget is not None and consumed is not None and consumed >= budget:
            continue
        return {
            "lease_ref": row.get("id"),
            "lease_role": row.get("role"),
            "lease_model": row.get("model"),
            "expires_at": _iso(exp),
        }
    return None


def _lease_binding(role: str, lease: Optional[Dict[str, Any]],
                   bound_at: str) -> Dict[str, Any]:
    """Build the lease_binding block. Refuses role-mismatched leases
    (SNAP002 mirror): lease_ref null, role_agreement false, reason set."""
    if lease is None:
        return {
            "lease_ref": None,
            "lease_role": role,
            "lease_model": None,
            "expires_at": None,
            "bound_at": bound_at,
            "role_agreement": None,   # no lease to agree or disagree
            "reason": "no live lease for role (unbound digest — nullable-but-explicit)",
        }
    lease_role = (lease.get("lease_role") or "").lower()
    if lease_role != role.lower():
        return {
            "lease_ref": None,
            "lease_role": lease.get("lease_role"),
            "lease_model": lease.get("lease_model"),
            "expires_at": lease.get("expires_at"),
            "bound_at": bound_at,
            "role_agreement": False,  # REFUSED — scope artifact, not authority grant
            "reason": f"lease role {lease.get('lease_role')!r} != digest role {role!r} "
                      "(binding refused, SNAP002 mirror)",
        }
    return {
        "lease_ref": lease.get("lease_ref"),
        "lease_role": lease.get("lease_role"),
        "lease_model": lease.get("lease_model"),
        "expires_at": lease.get("expires_at"),
        "bound_at": bound_at,
        "role_agreement": True,
    }


# ── Assembly ─────────────────────────────────────────────────────────────────


def assemble_digest(
    role: str,
    assembled_for_model: str = "",
    fetch_inbox: Optional[Fetcher] = None,
    fetch_threads: Optional[Fetcher] = None,
    fetch_records: Optional[Fetcher] = None,
    level_ceiling: Optional[str] = None,
    now: Optional[datetime] = None,
    fetch_lease: Optional[LeaseFetcher] = None,
) -> Dict[str, Any]:
    """Assemble the digest payload. Never raises on source errors — failed
    surfaces degrade to ``sources_degraded`` entries (availability over
    completeness for a context-only artifact).

    ``fetch_lease`` (v0.1) takes the role and returns the live-lease dict
    (or None). It is invoked AFTER the other surfaces so a lease-source
    failure degrades without affecting inbox/threads/records."""
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

    if fetch_lease is None:
        binding = _lease_binding(role, None, now.isoformat())
        binding["reason"] = "no lease fetcher configured (unbound — caller may inject)"
    else:
        try:
            lease = fetch_lease(role)
        except Exception as e:  # noqa: BLE001 — lease source down ≠ digest down
            degraded.append(f"lease: {e}")
            lease, _ = None, None
        binding = _lease_binding(role, lease, now.isoformat())

    return {
        "digest_version": DIGEST_VERSION,
        "disposition": DISPOSITION,  # I2: context-only, never a decision
        "role": role,
        "assembled_for_model": assembled_for_model,  # R-1..R-3 stamp
        "as_of": now.isoformat(),
        "lease_binding": binding,  # v0.1: V167 lease_ref binding (nullable-but-explicit)
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
        lambda r: fetch_live_lease(r, nebs),  # v0.1: lease binding (role-taking per LeaseFetcher)
    )


def main(argv: List[str]) -> int:
    import argparse
    import json
    import sys

    p = argparse.ArgumentParser(description="Assemble a role continuity digest (v0.1, read-only)")
    p.add_argument("role")
    p.add_argument("--model", default=os.environ.get("NEXUS_AGENT_MODEL", ""),
                   help="model identity stamp (R-1..R-3)")
    args = p.parse_args(argv)

    fin, fth, frec, ceiling, flease = _live_fetchers(args.role)
    digest = assemble_digest(args.role, args.model, fin, fth, frec, ceiling,
                             fetch_lease=flease)
    json.dump(digest, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
