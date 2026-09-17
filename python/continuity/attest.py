#!/usr/bin/env python3
"""continuity.attest — boot-shim attestation wiring (V179 live).

The ratified event-identity contract in one design line: **the shim never
mints attestations.** An attestation is the record of a verification act —
minting one automatically would be G2 fabrication with extra steps. What the
shim can lawfully do:

1. **Attest-scan (default-on, read-only).** At session start, list open
   verification_request roots, classify each against the booting role
   (lawful attestor / work author → G1-refused / uninvolved), and surface
   cites_id + the pre-staged evidence refs. Absence-of-table or absence-of-
   psycopg2 is honest SKIP data, never a boot failure.
2. ``--attest CITES_ID --evidence REF[,REF...]`` — an explicit, gated
   recording path so chains persist from the session where the verification
   act happens, through the server-side gates, instead of hand-rolled psql.
   G1/G2/G3 client-side too (fail-closed, absent ≠ attestable), with a
   recorded receipt on success.

Both paths use the house docker-psql convention, exec_fn-injectable for
hermetic tests (pitfall #15: inject the dependency boundary).
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Callable, Dict, List, Optional

ATTEST_TABLE = "nebula.attestations"
DEFAULT_PSQL = ["docker", "exec", "-i", "pgvector_db", "psql",
                "-U", "pguser", "-d", "nexus", "-X", "-qAt", "-v", "ON_ERROR_STOP=1"]


def default_exec_fn() -> Callable[[str], str]:
    """House docker-psql path (persist.py convention), overridable for tests.

    Runs one SQL statement via psql, returns stripped stdout; a non-zero
    exit (e.g. a trigger refusal) raises RuntimeError with the stderr.
    """
    def exec_fn(sql: str) -> str:  # noqa: F811 — same shape as persist.py
        r = subprocess.run(_psql_args(), input=sql, capture_output=True,
                           text=True, timeout=30)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or "psql error").strip()[:300])
        return r.stdout.strip()
    return exec_fn


def _psql_args() -> List[str]:
    """House docker-psql path (persist.py convention), overridable for tests."""
    return [a for a in os.environ.get(
        "CONTINUITY_PSQL_ARGS", "").split("\x1f") if a] or list(DEFAULT_PSQL)


def table_exists(exec_fn) -> bool:
    """True when the V179 surface exists (to_regclass probe)."""
    out = exec_fn(f"SELECT to_regclass('{ATTEST_TABLE}');")
    return bool(out and out.strip() and out.strip() != "")


def fetch_open_requests(exec_fn) -> List[Dict[str, Any]]:
    """Open verification_request roots with their pre-staged evidence."""
    rows = exec_fn(
        "SELECT attestation_id, work_ref, requested_by, "
        "coalesce(evidence::text, '[]'), to_char(recorded_on_dt, 'YYYY-MM-DD HH24:MI') "
        f"FROM {ATTEST_TABLE} "
        "WHERE kind = 'verification_request' "
        "AND recorded_until_dt = 'infinity'::timestamptz "
        "ORDER BY recorded_on_dt;")
    out = []
    for line in (rows or "").splitlines():
        parts = line.split("|", 4)
        if len(parts) != 5:
            continue
        out.append({"attestation_id": parts[0], "work_ref": parts[1],
                    "requested_by": parts[2], "evidence": parts[3],
                    "recorded_on": parts[4]})
    return out


def attestor_capabilities(exec_fn, role: str) -> Dict[str, Any]:
    """G3 precondition lookup against the bitemporal roles view (fail-closed).

    Absence is not attestable: a missing row resolves to verify=False, never
    to an exception the caller might swallow into a permissive default.
    """
    # NOTE: compare psql's RENDERED boolean (t/f), never a ::text cast —
    # boolean::text yields 'true'/'false', so `bool_col::text = 't'` silently
    # reads FALSE. Pitfall-banked.
    out = exec_fn(
        "SELECT coalesce(can_verify_work_requests, false) "
        "FROM nebula.roles WHERE name = '%s' AND valid_until = '9999-12-31';"
        % role.replace("'", "''"))
    return {"role": role, "can_verify": bool(out and out.strip() == "t")}


def classify(role: str, req: Dict[str, Any], can_verify: bool) -> Dict[str, Any]:
    """One open request against the booting role — pure, testable."""
    author = (req.get("requested_by") or "").strip().lower()
    me = (role or "").strip().lower()
    if author and author == me:
        return {**req, "disposition": "g1_refused",
                "note": "you authored this work — G1 forbids self-attestation"}
    if can_verify:
        return {**req, "disposition": "actionable",
                "note": ("you hold can_verify_work_requests; record with "
                         "boot-shim --attest %s --evidence <run>,<artifact>"
                         % req.get("attestation_id", ""))}
    return {**req, "disposition": "uninvolved",
            "note": "open request; not yours to attest (no verify capability)"}


def scan(exec_fn: Optional[Callable[[str], str]], role: str) -> Dict[str, Any]:
    """Read-only attest-scan. Never raises; absence is data."""
    result: Dict[str, Any] = {"scanned": False, "role": role,
                              "open": [], "actionable": 0, "reason": ""}
    exec_fn = exec_fn or default_exec_fn()
    try:
        if not table_exists(exec_fn):
            result["reason"] = "V179 surface absent (to_regclass NULL) — inert"
            return result
        caps = attestor_capabilities(exec_fn, role)
        reqs = fetch_open_requests(exec_fn)
    except Exception as e:  # noqa: BLE001 — degrade, don't fail the boot
        result["reason"] = f"scan error: {e.__class__.__name__}: {str(e)[:120]}"
        return result
    result["scanned"] = True
    result["can_verify"] = caps.get("can_verify", False)
    for req in reqs:
        item = classify(role, req, caps.get("can_verify", False))
        result["open"].append(item)
        if item["disposition"] == "actionable":
            result["actionable"] += 1
    result["reason"] = f"{len(reqs)} open verification_request(s)"
    return result


def record_attestation(exec_fn: Optional[Callable[[str], str]], role: str,
                       cites_id: str,
                       evidence: List[str],
                       session_id: Optional[str] = None,
                       agent_record_id: Optional[str] = None) -> Dict[str, Any]:
    """Gated recording of one attestation row.

    Client-side G1/G2/G3 first (fail-closed); the INSERT then re-derives
    everything server-side (triggers remain authoritative). On success the
    row is read back and the resulting lineage depth reported.
    """
    res: Dict[str, Any] = {"recorded": False, "reason": ""}
    exec_fn = exec_fn or default_exec_fn()
    ev = [e.strip() for e in (evidence or []) if e and e.strip()]
    if not ev:
        res["reason"] = "G2 client-side: evidence list empty — refused locally"
        return res
    try:
        caps = attestor_capabilities(exec_fn, role)
        if not caps.get("can_verify"):
            res["reason"] = ("G3 client-side: role does not hold "
                             "can_verify_work_requests — refused locally")
            return res
        row = exec_fn(
            "SELECT requested_by, work_ref FROM %s "
            "WHERE attestation_id = '%s' AND kind = 'verification_request';"
            % (ATTEST_TABLE, cites_id.replace("'", "''")))
        parts = (row or "").split("|")
        if len(parts) != 2:
            res["reason"] = f"cites_id does not resolve to an open request ({cites_id[:8]})"
            return res
        requested_by, work_ref = parts[0].strip(), parts[1].strip()
        if requested_by.strip().lower() == (role or "").strip().lower():
            res["reason"] = ("G1 client-side: you authored this work (%s) — "
                             "attestation refused locally" % work_ref[:60])
            return res
        ev_json = json.dumps(ev).replace("'", "''")
        sess = (session_id or "").replace("'", "''")
        rec = (agent_record_id or "").replace("'", "''")
        exec_fn(
            "INSERT INTO %s (work_ref, kind, requested_by, attester_role, "
            "evidence, cites_id, session_id, agent_record_id) VALUES ("
            "'%s', 'attestation', '%s', '%s', '%s'::jsonb, '%s', "
            "NULLIF('%s',''), NULLIF('%s','')::uuid);"
            % (ATTEST_TABLE, work_ref.replace("'", "''"), requested_by,
               role.replace("'", "''"), ev_json, cites_id.replace("'", "''"),
               sess, rec))
        back = exec_fn(
            "SELECT attestation_id::text, txid FROM %s "
            "WHERE cites_id = '%s' AND kind = 'attestation' "
            "AND attester_role = '%s' AND recorded_until_dt = 'infinity'::timestamptz;"
            % (ATTEST_TABLE, cites_id.replace("'", "''"), role.replace("'", "''")))
        bits = (back or "").split("|")
        res["recorded"] = bool(bits and bits[0].strip())
        res["attestation_id"] = bits[0].strip() if bits else None
        res["txid"] = bits[1].strip() if len(bits) > 1 else None
        res["reason"] = ("attestation recorded; chain root %s now has a child"
                         % cites_id[:8])
    except Exception as e:  # noqa: BLE001 — trigger refusals surface verbatim
        msg = str(e)
        for tag in ("ATP0001", "ATP0002", "ATP0003", "ATP0004", "ATP0005",
                    "ATP010", "ATP011", "ATP012"):
            if tag in msg:
                res["reason"] = "server gate refused: " + msg.splitlines()[0][:200]
                return res
        res["reason"] = f"record error: {e.__class__.__name__}: {msg[:160]}"
    return res
