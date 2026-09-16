#!/usr/bin/env python3
"""operator-lease.py — standing operator-role lease for UI-origin /chat calls.

The enforce-flip prerequisite (soak review 42a11672, continuity thread
65fe85a8): when the /chat lease check (PR #272) flips to enforce, UI-origin
operator-role calls are refused unless an ACTIVE operator lease exists.
Interactive agent sessions are covered by the boot shim; the UI fleet is
covered here.

Semantics
---------
* ensure  — renew-if-live-else-issue, idempotent. Safe to call from both
  the fleet start script AND a scheduled renewal timer; concurrent calls
  converge (the issue route itself refuses a second ACTIVE lease per
  (role, channel) with 409).
* channel — 'ui-fleet', distinct from the agents' 'interactive' channel:
  one ACTIVE lease per (role, channel), so this lease never collides with
  a boot-shim session lease and its renewal never extends an agent's.
* ttl     — default 3600s; the timer cadence (default 30 min) renews well
  inside the window, so the lease is effectively standing.
* degradation is honest and non-fatal: if nebula-srv is unreachable the
  script reports 'degraded' and exits 0 — fleet start must never fail
  because lease infrastructure is down. The next timer fire retries.

Usage:
    python3 bin/operator-lease.py ensure            # renew-or-issue
    python3 bin/operator-lease.py status            # print lease state
    python3 bin/operator-lease.py release           # explicit release
Env:
    NEBULA_URL (default http://localhost:3101)
    OPERATOR_LEASE_TTL_SECONDS (default 3600)
    OPERATOR_LEASE_CHANNEL (default ui-fleet)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional

DEFAULT_BASE = "http://localhost:3101"
DEFAULT_TTL = 3600
DEFAULT_CHANNEL = "ui-fleet"
ROLE = "operator"


def _base() -> str:
    return (os.environ.get("NEBULA_URL") or DEFAULT_BASE).rstrip("/")


def _ttl() -> int:
    try:
        return int(os.environ.get("OPERATOR_LEASE_TTL_SECONDS") or DEFAULT_TTL)
    except ValueError:
        return DEFAULT_TTL


def _channel() -> str:
    return os.environ.get("OPERATOR_LEASE_CHANNEL") or DEFAULT_CHANNEL


def _http(url: str, method: str = "GET", body: Optional[Dict] = None,
          timeout: int = 8,
          do_open: Optional[Callable] = None) -> Any:
    """HTTP JSON helper; returns (status, parsed-or-raw). Injectable for tests."""
    opener = do_open or urllib.request.urlopen
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with opener(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    # urllib.error.URLError and sockets propagate — classified by the caller


def find_live_lease(base: str, channel: str,
                    do_open: Optional[Callable] = None) -> Optional[Dict[str, Any]]:
    """The live (ACTIVE, unexpired) operator lease on the channel, if any."""
    status, body = _http(f"{base}/api/role-leases?role={ROLE}&status=ACTIVE&channel={channel}",
                         do_open=do_open)
    if status != 200:
        raise RuntimeError(f"lease list HTTP {status}")
    now_ms = _now_ms()
    for item in (body or {}).get("items", []):
        exp = item.get("expires_at")
        if exp is None or _parse_ts(exp) >= now_ms:
            return item
    return None


def ensure(base: Optional[str] = None, ttl: Optional[int] = None,
           channel: Optional[str] = None,
           do_open: Optional[Callable] = None) -> Dict[str, Any]:
    """renew-if-live-else-issue. Returns an outcome dict; NEVER raises —
    transport failures degrade to {'action': 'degraded'} right here, because
    every caller (fleet start script, renewal timer, direct import) must be
    safe. Remote refusal semantics it can resolve (409 race) converge to the
    renew path."""
    try:
        return _ensure_inner(base, ttl, channel, do_open)
    except Exception as e:  # noqa: BLE001 — honest degradation is the contract
        return {"action": "degraded",
                "detail": f"{type(e).__name__}: {str(e)[:160]}"}


def _ensure_inner(base, ttl, channel, do_open) -> Dict[str, Any]:
    base = base or _base()
    ttl = ttl if ttl is not None else _ttl()
    channel = channel or _channel()
    lease = find_live_lease(base, channel, do_open=do_open)
    if lease is not None:
        status, body = _http(f"{base}/api/role-leases/{lease['id']}/renew",
                             method="POST", body={"ttlSeconds": ttl},
                             do_open=do_open)
        if status == 200:
            return {"action": "renewed", "lease_id": (body or {}).get("id"),
                    "expires_at": (body or {}).get("expires_at")}
        return {"action": "degraded",
                "detail": f"renew failed HTTP {status}: {json.dumps(body)[:140]}"}
    status, body = _http(f"{base}/api/role-leases/issue", method="POST",
                         body={"role": ROLE, "channel": channel,
                               "model": "ui-fleet-standing", "ttlSeconds": ttl},
                         do_open=do_open)
    if status in (200, 201):
        return {"action": "issued", "lease_id": (body or {}).get("id"),
                "expires_at": (body or {}).get("expires_at")}
    if status == 409 and (body or {}).get("existingLeaseId"):
        # concurrent issuer won the race — renew that lease instead
        status2, body2 = _http(
            f"{base}/api/role-leases/{body['existingLeaseId']}/renew",
            method="POST", body={"ttlSeconds": ttl}, do_open=do_open)
        if status2 == 200:
            return {"action": "renewed", "lease_id": (body2 or {}).get("id"),
                    "expires_at": (body2 or {}).get("expires_at"),
                    "note": "409 race converged to existing lease"}
        return {"action": "degraded",
                "detail": f"409 then renew failed HTTP {status2}"}
    return {"action": "degraded",
            "detail": f"issue failed HTTP {status}: {json.dumps(body)[:140]}"}


def release(base: Optional[str] = None,
            channel: Optional[str] = None,
            do_open: Optional[Callable] = None) -> Dict[str, Any]:
    base = base or _base()
    channel = channel or _channel()
    lease = find_live_lease(base, channel, do_open=do_open)
    if lease is None:
        return {"action": "noop", "detail": "no live lease on channel"}
    status, body = _http(f"{base}/api/role-leases/{lease['id']}/revoke",
                         method="POST", do_open=do_open)
    if status == 200:
        return {"action": "released", "lease_id": lease["id"]}
    return {"action": "degraded", "detail": f"revoke failed HTTP {status}"}


def status(base: Optional[str] = None, channel: Optional[str] = None,
           do_open: Optional[Callable] = None) -> Dict[str, Any]:
    base = base or _base()
    channel = channel or _channel()
    lease = find_live_lease(base, channel, do_open=do_open)
    if lease is None:
        return {"live": False, "channel": channel}
    return {"live": True, "lease_id": lease.get("id"),
            "expires_at": lease.get("expires_at"), "channel": channel}


def _now_ms() -> float:
    import time
    return time.time() * 1000


def _parse_ts(v: Any) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    from datetime import datetime, timezone
    return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp() * 1000


def main(argv) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Standing operator lease (UI fleet)")
    p.add_argument("command", choices=["ensure", "status", "release"])
    args = p.parse_args(argv)
    try:
        out = {"ensure": ensure, "status": status, "release": release}[args.command]()
    except Exception as e:  # noqa: BLE001 — honest degradation, non-fatal by design
        out = {"action": "degraded", "detail": f"{type(e).__name__}: {str(e)[:160]}"}
    print(json.dumps(out))
    # ensure returning 'degraded' still exits 0: fleet start / timer must not
    # fail on lease infrastructure; the next fire retries.
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
