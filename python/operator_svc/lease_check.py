#!/usr/bin/env python3
"""operator.lease_check — role-lease liveness resolution for the /chat adoption path.

Closes the lease-check gap identified in discussions thread 65fe85a8: the
/chat role parameter is currently lease-blind (any role string yields that
role's model config + procedure cards — and, once continuity ships, the
role's interaction digest). This module resolves the requested role against
tackle.role_leases at the adoption boundary, BEFORE any role-scoped context
(model config, procedure cards, future continuity digests) is assembled.

Semantics
---------
* Liveness predicate mirrors nebula-srv's stale-lease sweep exactly
  (routes.ts /api/role-leases/stale): status = 'ACTIVE' AND expires_at
  (when set) not in the past AND budget (when set) not exhausted.
* Resolution is ROLE-level: /chat carries no caller model identity, so this
  check cannot authenticate a model binding — that stays a lease-issue-time
  concern (freebuff-boot.py). A live lease proves the ROLE is currently
  adopted; it does not prove WHO is calling. Documented limitation, tracked
  in the continuity thread.
* Enforcement modes via OPERATOR_LEASE_CHECK env:
    off      — no check (pre-continuity behavior)
    warn     — resolve + report, never refuse (default: observability first)
    enforce  — refuse adoption (HTTP 403 at the server layer) when no live
               lease exists for the role. Flip after warn-mode soak.
* Fail-open on infrastructure error in off/warn; enforce is fail-closed
  (an unresolved lease is a refusal, with the error surfaced in the reason).
* Synthetic probes (soak evidence, thread 65fe85a8 comment 42a11672): the
  daily lease-probe timer drives /chat with probe=true. Probe resolutions
  log the standard lease-check line plus probe=synthetic so soak analysis
  can separate scheduled evidence from real traffic. Real-traffic lines are
  byte-identical to the pre-probe format.
"""

import logging
import os
import subprocess
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

_log = logging.getLogger("operator.lease_check")

MODE_ENV = "OPERATOR_LEASE_CHECK"
VALID_MODES = ("off", "warn", "enforce")
DEFAULT_MODE = "warn"

DOCKER_PSQL = ["docker", "exec", "-i", "pgvector_db", "psql", "-U", "pguser", "-d", "nexus"]


def _mode() -> str:
    mode = (os.environ.get(MODE_ENV) or DEFAULT_MODE).strip().lower()
    if mode not in VALID_MODES:
        _log.warning("invalid %s=%r — falling back to %s", MODE_ENV, mode, DEFAULT_MODE)
        return DEFAULT_MODE
    return mode


def _psql(sql: str, timeout: int = 15) -> tuple:
    """Run a SQL statement via the house docker-psql path (chat_store style)."""
    try:
        result = subprocess.run(
            DOCKER_PSQL + ["-t", "-A"],
            input=sql, capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return 1, "(timeout)"
    except Exception as e:  # noqa: BLE001 — surface as resolution error
        return 1, str(e)


# Newest LIVE lease for a role. Predicate mirrors nebula-srv /role-leases/stale
# (status ACTIVE + not past expiry + budget not exhausted) — a lease the sweep
# would expire does not count as live here.
_LIVE_LEASE_SQL = """
SELECT id::text
       || '|' || COALESCE(model, '')
       || '|' || COALESCE(expires_at::text, '')
       || '|' || COALESCE(window_end::text, '')
       || '|' || COALESCE(budget_units::text, '')
       || '|' || COALESCE(consumed_units::text, '')
FROM tackle.role_leases
WHERE role = '{role}'
  AND status = 'ACTIVE'
  AND (expires_at IS NULL OR expires_at >= NOW())
  AND (budget_units IS NULL OR consumed_units < budget_units)
ORDER BY acquired_at DESC NULLS LAST, created_at DESC
LIMIT 1
"""


def _query_lease(role: str) -> Optional[Dict[str, Any]]:
    """Newest live lease for role, or None. Raises on infrastructure error."""
    safe = role.replace("'", "''")
    code, out = _psql(_LIVE_LEASE_SQL.format(role=safe))
    if code != 0:
        raise RuntimeError(f"lease query failed: {out or 'psql error'}")
    if not out:
        return None
    parts = (out.splitlines() or [""])[0].split("|")
    return {
        "id": parts[0] if len(parts) > 0 else None,
        "role": role,
        "model": parts[1] if len(parts) > 1 and parts[1] else None,
        "expires_at": parts[2] if len(parts) > 2 and parts[2] else None,
        "window_end": parts[3] if len(parts) > 3 and parts[3] else None,
        "budget_units": int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else None,
        "consumed_units": int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else None,
        "status": "ACTIVE",
    }


def check_adoption(
    role: str,
    now: Optional[datetime] = None,
    query: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
    probe: bool = False,
) -> Dict[str, Any]:
    """Resolve role adoption at the boundary.

    Returns a dict: {allowed, mode, adopted, reason, lease, error}.
    `adopted` is None only in off mode (check not performed).
    `query` is injectable for tests; defaults to the docker-psql resolver.
    `now` is accepted for signature symmetry (liveness is evaluated in SQL).
    `probe=True` marks the observation as synthetic (scheduled probe): the
    journal line gains a trailing probe=synthetic field; real-traffic lines
    are unchanged.
    """
    mode = _mode()
    if mode == "off":
        return {"allowed": True, "mode": mode, "adopted": None,
                "reason": "lease check disabled", "lease": None, "error": None}

    resolve = query or _query_lease
    try:
        lease = resolve(role)
        error = None
    except Exception as e:  # noqa: BLE001 — classified below
        lease, error = None, str(e)

    if error is not None:
        if probe:
            _log.warning("lease-check role=%s mode=%s outcome=error probe=synthetic error=%r",
                         role, mode, error)
        else:
            _log.warning("lease-check role=%s mode=%s outcome=error error=%r", role, mode, error)
        if mode == "enforce":
            return {"allowed": False, "mode": mode, "adopted": False,
                    "reason": f"lease resolution failed (fail-closed in enforce): {error}",
                    "lease": None, "error": error}
        return {"allowed": True, "mode": mode, "adopted": False,
                "reason": f"lease resolution failed (fail-open in {mode}): {error}",
                "lease": None, "error": error}

    if lease is not None:
        if probe:
            _log.info("lease-check role=%s mode=%s outcome=adopted lease_ref=%s probe=synthetic",
                      role, mode, lease.get("id"))
        else:
            _log.info("lease-check role=%s mode=%s outcome=adopted lease_ref=%s",
                      role, mode, lease.get("id"))
        return {"allowed": True, "mode": mode, "adopted": True,
                "reason": f"live lease for role ({mode})",
                "lease": lease, "error": None}

    if mode == "enforce":
        if probe:
            _log.warning("lease-check role=%s mode=enforce outcome=refused probe=synthetic", role)
        else:
            _log.warning("lease-check role=%s mode=enforce outcome=refused", role)
        return {"allowed": False, "mode": mode, "adopted": False,
                "reason": "no live lease for role (enforce mode)",
                "lease": None, "error": None}
    if probe:
        _log.warning("lease-check role=%s mode=%s outcome=unadopted probe=synthetic", role, mode)
    else:
        _log.warning("lease-check role=%s mode=%s outcome=unadopted", role, mode)
    return {"allowed": True, "mode": mode, "adopted": False,
            "reason": "no live lease for role (warn mode)",
            "lease": None, "error": None}


def probe_adoption(
    role: str,
    query: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Run a synthetic probe resolution — never raises.

    Identical to check_adoption(role, probe=True) with infrastructure
    failures converted into an outcome dict (outcome=error path). The
    scheduled probe runner uses this so a broken resolver is DATA (an
    error outcome in the journal) rather than a failed systemd unit.
    """
    try:
        return check_adoption(role, query=query, probe=True)
    except Exception as e:  # noqa: BLE001 — probes are data, not unit failures
        _log.warning("lease-check role=%s probe=synthetic outcome=error error=%r", role, e)
        mode = _mode()
        return {"allowed": mode != "enforce", "mode": mode, "adopted": False,
                "reason": f"probe raised (fail-{'closed' if mode == 'enforce' else 'open'}): {e}",
                "lease": None, "error": str(e)}
