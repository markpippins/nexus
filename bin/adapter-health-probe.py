#!/usr/bin/env python3
"""bin/adapter-health-probe.py — adapter registry health probe + transition law.

Implements the evidence-refresh design (DBA R1 d849ff31; V173 carries the
schema slice). The V172 registry's discipline says an active adapter is a
drifting claim; this probe keeps the claim live: it re-observes every live
adapter's provider, appends the observation to the evidence history, and
applies the transition law — so `active` means "recently observed", not
"once registered".

Transition law (design 2f6fc70d; all V172-vocabulary statuses):
  declared -> active    on ADAPTER_PROBE_PROMOTE_DEPTH (default 2) consecutive
                        PASS observations (promote on evidence, not optimism)
  active   -> degraded  on the first FAIL
  degraded -> active    on the first PASS (recovery easier than promotion)
  retired / declared-handling: retirement is NEVER automatic (operator
              judgment, not health); declared adapters are probed too but only
              their promote counter can move them (to active), never to
              degraded/retired
CAS on every transition: UPDATE ... WHERE adapter_status = :from — rowcount 0
means an operator changed status mid-flight; the probe logs cas-conflict and
stands down. Human intent outranks automation.

Provenance: runs under the operator standing lease (resolved fresh at run
start). If none is live it still runs — degraded-but-honest: evidence records
probe_lease: null, probe_mode: 'leaseless-fallback' (the boot-shim stance).

Evidence shape (V173): nebula.adapters.evidence is an ARRAY of observations,
newest last, truncated to ADAPTER_PROBE_HISTORY (10). Synthetic transitions
(drills/submitted observations marked synthetic) MUST carry synthetic=true —
a drill that pretends to be an observation poisons the evidence discipline.

Conventions (house, per lease-probe.py): exit 0 on completed runs — probe
failures are data (the evidence + journal carry the outcome), not unit
failures; exit 1 on config/usage errors. --print emits the outcome table.

Usage:
    python3 bin/adapter-health-probe.py              # probe all live adapters
    python3 bin/adapter-health-probe.py --print      # + outcome table
    python3 bin/adapter-health-probe.py --submit-observation  # stdin JSON:
        {"source": "...", "capability": "has-active-shrapnel-protocol",
         "provider": "postgresql", "outcome": "PASS|FAIL|SKIP",
         "detail": {...}, "synthetic": false}        # one law for all evidence
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

try:
    import psycopg2
    import psycopg2.extras
    PSYCOPG2_AVAILABLE = True
except ImportError:
    # hermetic surfaces (mesh family) must COLLECT without psycopg2 — the
    # flag (not a module-level exit) keeps import safe; main() refuses to
    # run without it
    PSYCOPG2_AVAILABLE = False

# ── Configuration (env-overridable, roundtable-tunable) ─────────────────────
PROMOTE_DEPTH = int(os.environ.get("ADAPTER_PROBE_PROMOTE_DEPTH", "2"))
HISTORY_LIMIT = int(os.environ.get("ADAPTER_PROBE_HISTORY", "10"))
STALENESS_THRESHOLD = os.environ.get("ADAPTER_STALENESS_THRESHOLD", "7 days")
DSN = os.environ.get(
    "CONDUIT_PG_DSN", "postgresql://pguser:pgpass@localhost:5432/nexus")

PROVIDER_CHECKS = {}  # provider -> fn(conn, capability_name) -> (outcome, detail)


def provider_check(name):
    """Register a provider health-check under the dispatch table."""
    def deco(fn):
        PROVIDER_CHECKS[name] = fn
        return fn
    return deco


@provider_check("postgresql")
def check_postgresql(conn, capability_name):
    """Connect (the probe's own conn IS the connect check) + per-capability
    inventory re-observation. Outcome PASS/FAIL, never raises."""
    try:
        with conn.cursor() as cur:
            # The protocol's core surfaces (refs-only observation: counts).
            cur.execute(
                "SELECT to_regclass('shrapnel.object_instance') IS NOT NULL")
            present = cur.fetchone()[0]
            if not present:
                return ("SKIP",
                        {"reason": "shrapnel schema absent on this provider"})
            cur.execute("SELECT count(*) FROM shrapnel.object_instance")
            count = cur.fetchone()[0]
            return ("PASS", {"check": "connect+inventory",
                             "object_instance_rows": count})
    except Exception as exc:  # noqa: BLE001 — failures are data, not crashes
        return ("FAIL", {"check": "connect+inventory", "error": str(exc)[:300]})


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_operator_lease(cur):
    """Freshest ACTIVE operator lease id, or None (leaseless fallback)."""
    cur.execute(
        "SELECT id FROM tackle.role_leases "
        "WHERE role ILIKE 'operator' AND status = 'ACTIVE' "
        "ORDER BY created_at DESC LIMIT 1")
    row = cur.fetchone()
    return row[0] if row else None


def _append_observation(evidence, obs):
    """Append newest-LAST and truncate to HISTORY_LIMIT (V173 array shape).
    Defensive normalization mirrors the V173 migration: legacy flat-object
    evidence is wrapped to a one-element array rather than dropped."""
    if isinstance(evidence, list):
        history = list(evidence)
    elif evidence:  # legacy flat object -> wrap, never lose it
        history = [evidence]
    else:
        history = []
    history.append(obs)
    return history[-HISTORY_LIMIT:]


def _consecutive_passes(evidence):
    """Trailing consecutive PASS count from the observation history."""
    n = 0
    for obs in reversed(evidence if isinstance(evidence, list) else []):
        if obs.get("result") == "PASS":
            n += 1
        else:
            break
    return n


def apply_transition_law(status, outcome, evidence):
    """Pure transition-law function: (from_status, outcome, history) ->
    to_status | None. Same law for probe runs and --submit-observation."""
    if status == "retired":
        return None  # retirement is never automatic, retirement is never exited by health
    if status == "active":
        return "degraded" if outcome == "FAIL" else None
    if status == "degraded":
        return "active" if outcome == "PASS" else None
    if status == "declared":
        if outcome == "FAIL":
            return None  # declared cannot degrade; it just fails to promote
        if outcome == "PASS" and _consecutive_passes(evidence) >= PROMOTE_DEPTH:
            return "active"
        return None
    return None


def transition_adapter(cur, adapter, outcome, detail, lease_id, probe_mode,
                       synthetic=False, source="health-probe"):
    """Append the observation, then CAS-apply the transition law."""
    adapter_id, status, evidence = (adapter["id"], adapter["adapter_status"],
                                    adapter["evidence"] or [])
    obs = {
        "kind": "health-probe" if source == "health-probe" else "external-observation",
        "observed_at": _now_iso(),
        "observer": source,
        "result": outcome,
        "check": detail.get("check", "external"),
        "synthetic": bool(synthetic),
        # provenance honesty (design 2f6fc70d §3): leaseless runs are
        # degraded-but-honest — recorded in the evidence itself
        "probe_mode": probe_mode,
        "probe_lease": str(lease_id) if lease_id else None,
    }
    if detail:
        obs["detail"] = detail
    new_evidence = _append_observation(evidence, obs)
    to_status = apply_transition_law(status, outcome, new_evidence)

    if to_status is None:
        cur.execute(
            "UPDATE nebula.adapters SET evidence = %s, last_checked_at = now() "
            "WHERE id = %s", (json.dumps(new_evidence), adapter_id))
        return ("observed",
                f"{adapter['provider']}: {outcome} (no transition from {status})")
    # CAS: only transition from the status we just read. rowcount 0 = an
    # operator (or a racing probe) moved the row mid-flight — stand down.
    cur.execute(
        "UPDATE nebula.adapters SET adapter_status = %s, evidence = %s, "
        "last_checked_at = now() WHERE id = %s AND adapter_status = %s",
        (to_status, json.dumps(new_evidence), adapter_id, status))
    if cur.rowcount == 0:
        return ("cas-conflict",
                f"{adapter['provider']}: status moved off {status} mid-flight; "
                "probe stands down (operator intent outranks automation)")
    return ("transitioned",
            f"{adapter['provider']}: {status} -> {to_status} "
            f"({'PASS x%d' % _consecutive_passes(new_evidence) if to_status == 'active' else outcome})")


def run_probe(print_table=False):
    """Probe all live adapters; returns the outcome list (never raises)."""
    results = []
    conn = None
    try:
        conn = psycopg2.connect(DSN)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT a.id, a.provider, a.adapter_status, a.evidence, "
                "       c.name AS capability "
                "FROM nebula.adapters a "
                "JOIN nebula.capabilities c ON c.id = a.capability_id "
                "WHERE a.recorded_until_dt = 'infinity' "
                "  AND a.valid_until = 'infinity'")
            adapters = cur.fetchall()

            lease_id = _resolve_operator_lease(cur)
            probe_mode = "standing-lease" if lease_id else "leaseless-fallback"

            for ad in adapters:
                check = PROVIDER_CHECKS.get(ad["provider"])
                if check is None:
                    outcome, detail = "SKIP", {
                        "reason": f"no provider check registered for "
                                  f"{ad['provider']}"}
                else:
                    try:
                        outcome, detail = check(conn, ad["capability"])
                    except Exception as exc:  # noqa: BLE001
                        outcome, detail = "FAIL", {"error": str(exc)[:300]}

                kind, msg = transition_adapter(
                    cur, ad, outcome, detail, lease_id, probe_mode)
                results.append({"provider": ad["provider"],
                                "capability": ad["capability"],
                                "status": ad["adapter_status"],
                                "kind": kind, "message": msg,
                                "lease_mode": probe_mode})
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — the timer must not look broken
        results.append({"provider": "(run)", "capability": "(run)",
                        "status": "-", "kind": "error",
                        "message": f"probe run failed: {exc}",
                        "lease_mode": "n/a"})
    finally:
        if conn:
            conn.close()
    if print_table:
        for r in results:
            print(f"  [{r['kind']:>12}] {r['provider']:<14} "
                  f"cap={r['capability']} from={r['status']} — {r['message']}")
    return results


def submit_observation_stream():
    """--submit-observation: one JSON observation on stdin through the SAME
    law. Profile observations and machine probes are the same kind of fact."""
    raw = sys.stdin.read()
    try:
        obs_in = json.loads(raw)
        source = obs_in.get("source", "external")
        capability = obs_in["capability"]
        provider = obs_in["provider"]
        outcome = str(obs_in.get("outcome", "")).upper()
        detail = obs_in.get("detail") or {}
        synthetic = bool(obs_in.get("synthetic", False))
        if outcome not in ("PASS", "FAIL", "SKIP"):
            raise ValueError("outcome must be PASS|FAIL|SKIP")
    except Exception as exc:  # noqa: BLE001
        print(f"submit-observation: invalid input: {exc}", file=sys.stderr)
        return 1

    conn = None
    try:
        conn = psycopg2.connect(DSN)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT a.id, a.provider, a.adapter_status, a.evidence, "
                "       c.name AS capability "
                "FROM nebula.adapters a "
                "JOIN nebula.capabilities c ON c.id = a.capability_id "
                "WHERE c.name = %s AND a.provider = %s "
                "  AND a.recorded_until_dt = 'infinity' "
                "  AND a.valid_until = 'infinity'", (capability, provider))
            ad = cur.fetchone()
            if ad is None:
                print(f"submit-observation: no live adapter for "
                      f"{capability}/{provider}", file=sys.stderr)
                return 1
            lease_id = _resolve_operator_lease(cur)
            mode = "standing-lease" if lease_id else "leaseless-fallback"
            kind, msg = transition_adapter(
                cur, ad, outcome, detail, lease_id, mode,
                synthetic=synthetic, source=f"external:{source}")
            conn.commit()
            print(f"[{kind}] {msg}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"submit-observation failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if conn:
            conn.close()


def main():
    if not PSYCOPG2_AVAILABLE:
        print("adapter-health-probe: psycopg2 is required", file=sys.stderr)
        return 1
    ap = argparse.ArgumentParser(description="Adapter registry health probe")
    ap.add_argument("--print", action="store_true",
                    help="print the per-adapter outcome table")
    ap.add_argument("--submit-observation", action="store_true",
                    help="read one JSON observation from stdin (profile seam)")
    args = ap.parse_args()

    if args.submit_observation:
        return submit_observation_stream()
    run_probe(print_table=args.print)
    return 0


if __name__ == "__main__":
    sys.exit(main())
