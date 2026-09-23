#!/usr/bin/env python3
"""Shared harness for the moleculer write-path canaries (A/B against live state).

Implements the ground rules ratified by the architect (ruling 59f8e2af on
design 714abe42):

- Both twins hit the SAME live database; the DB is the arbiter.
- Marked synthetic rows only; cleanup via the tested HTTP surface.
- A/B per case: write via incumbent (A), capture response + DB state, clean,
  re-baseline, write via twin (B), capture, compare, clean, assert residue.
- Timestamps/UUIDs normalized before compare; byte-compare where the harness
  declares determinism.
- Failure containment: first mismatch aborts the series (exit 1), residue
  reported, no retry-until-green.
- Results land in a file (JSON + text), never only stdout (window discipline).

Library usage (each service script imports this):
    from write_canary_lib import Canary, Case, approx, pg, run_series
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

RUN_ID = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
ISO_TS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(\+\d{2}(:?\d{2})?|Z)$"
)


# ── environment ──────────────────────────────────────────────────────────

def pg_env() -> dict:
    """Connection env for the DB-arbiter psql calls (same creds the services use)."""
    return {
        "PGPASSWORD": os.environ.get("PGPASSWORD", "pgpass"),
        "PGHOST": os.environ.get("PGHOST", "localhost"),
        "PGPORT": os.environ.get("PGPORT", "5432"),
        "PGUSER": os.environ.get("PGUSER", "pguser"),
        "PGDATABASE": os.environ.get("PGDATABASE", "nexus"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/home/codex"),
    }


def pg(sql: str, params: Optional[list] = None) -> list[dict]:
    """Run read-only SQL via psql, return rows as dicts. The canary's DB arbiter."""
    cmd = ["psql", "-h", pg_env()["PGHOST"], "-p", pg_env()["PGPORT"],
           "-U", pg_env()["PGUSER"], "-d", pg_env()["PGDATABASE"],
           "-t", "-A", "-F", "\x1f", "-c", sql]
    if params:
        # psql has no parameter binding on -c; callers must inline literals via
        # pg_lit() helpers only. Refuse obviously-unsafe direct interpolation.
        raise TypeError("pg() is for parameterless SQL; inline via sql_lit()")
    env = pg_env()
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"psql failed rc={r.returncode}: {r.stderr.strip()[:300]}")
    rows = []
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        rows.append({f"col{i}": v for i, v in enumerate(line.split("\x1f"))})
    return rows


def sql_lit(v: Any) -> str:
    """SQL literal quoting for marked canary values only (they are generated here)."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("'", "''")
    return f"'{s}'"


# ── HTTP client ──────────────────────────────────────────────────────────

class Twin:
    """One side of the A/B: an HTTP surface (incumbent or moleculer twin)."""

    def __init__(self, name: str, port: int, secret: Optional[str] = None):
        self.name = name
        self.port = port
        self.secret = secret

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        headers: Optional[dict] = None,
        timeout: float = 20,
        raw: bool = False,
    ) -> tuple[int, Any]:
        conn = http.client.HTTPConnection("localhost", self.port, timeout=timeout)
        try:
            hdrs = dict(headers or {})
            payload = None
            if body is not None:
                payload = body if isinstance(body, (str, bytes)) else json.dumps(body)
                hdrs.setdefault("Content-Type", "application/json")
            if self.secret:
                hdrs["X-Nexus-Internal"] = self.secret
            conn.request(method, path, body=payload, headers=hdrs)
            resp = conn.getresponse()
            data = resp.read()
            if raw:
                return resp.status, data
            try:
                return resp.status, json.loads(data) if data else None
            except Exception:
                return resp.status, data.decode("utf-8", "replace")
        finally:
            conn.close()


# ── comparison (ground rule 5: semantic equality, normalize generated) ───

def normalize(obj: Any, path: str = "") -> Any:
    """Normalize generated fields (UUIDs, ISO timestamps) to placeholders.

    Values we generated ourselves stay literal (the canary marked them);
    everything UUID/ISO-shaped is treated as server-generated.
    """
    if isinstance(obj, dict):
        return {k: normalize(v, f"{path}.{k}") for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [normalize(v, f"{path}[{i}]") for i, v in enumerate(obj)]
    if isinstance(obj, str):
        if UUID_RE.match(obj):
            return "<uuid>"
        if ISO_TS_RE.match(obj):
            return "<ts>"
    return obj


def deep_equal(a: Any, b: Any) -> tuple[bool, str]:
    na, nb = normalize(a), normalize(b)
    if na == nb:
        return True, ""
    ja = json.dumps(na, sort_keys=False, indent=1, default=str)
    jb = json.dumps(nb, sort_keys=False, indent=1, default=str)
    # first-difference pointer for the report
    for i, (x, y) in enumerate(zip(ja.splitlines(), jb.splitlines())):
        if x != y:
            return False, f"line {i}: A={x.strip()[:120]}  B={y.strip()[:120]}"
    return False, f"structure differs (A {len(ja)} bytes vs B {len(jb)} bytes)"


# ── case machinery ───────────────────────────────────────────────────────

@dataclass
class Case:
    name: str
    run: Callable[["Canary", "Case"], Any]   # raises AssertionError on mismatch
    cleanup: Optional[Callable[[], None]] = None
    notes: str = ""


@dataclass
class Canary:
    service: str
    port_a: int
    port_b: int
    secret: Optional[str] = None
    cases: list[Case] = field(default_factory=list)
    residue: list[str] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)
    aborted: bool = False

    def twin_a(self) -> Twin:
        return Twin("incumbent", self.port_a, self.secret)

    def twin_b(self) -> Twin:
        return Twin("twin", self.port_b, self.secret)

    def note_residue(self, what: str) -> None:
        self.residue.append(what)

    def record(self, case: str, ok: bool, detail: str = "") -> None:
        self.results.append(
            {"case": case, "ok": ok, "detail": detail, "at": time.strftime("%H:%M:%S")}
        )
        mark = "  ok " if ok else "DIFF "
        print(f"  {mark}{case}" + (f" — {detail}" if detail else ""), flush=True)

    def run(self) -> int:
        print(f"== write-path canary: {self.service} (A=:>{self.port_a} B=:>{self.port_b}) "
              f"runid={RUN_ID} ==", flush=True)
        failures = 0
        for case in self.cases:
            try:
                case.run(self, case)
                self.record(case.name, True)
            except AssertionError as e:
                failures += 1
                self.record(case.name, False, str(e)[:400])
                self.aborted = True
                print(f"  ABORT series ({self.service}): {e}", flush=True)
                break
            except Exception as e:  # noqa: BLE001 — containment rule
                failures += 1
                self.record(case.name, False, f"harness error: {e}")
                self.aborted = True
                print(f"  ABORT series ({self.service}): harness error {e}", flush=True)
                break
            finally:
                if case.cleanup and not self.aborted:
                    try:
                        case.cleanup()
                    except Exception as e:  # noqa: BLE001
                        self.note_residue(f"{case.name}: cleanup failed: {e}")
        self.report(failures)
        return 1 if failures or self.residue else 0

    def report(self, failures: int) -> None:
        total = len(self.results)
        passed = sum(1 for r in self.results if r["ok"])
        print(f"\n{passed}/{total} cases match" + (f", {failures} FAILED" if failures else "")
              + (f", RESIDUE: {self.residue}" if self.residue else ", zero residue"),
              flush=True)
        out = {
            "service": self.service,
            "runid": RUN_ID,
            "port_a": self.port_a,
            "port_b": self.port_b,
            "aborted": self.aborted,
            "passed": passed,
            "total": total,
            "residue": self.residue,
            "cases": self.results,
        }
        path = os.environ.get("CANARY_RESULT_DIR", "/tmp")
        jpath = os.path.join(path, f"write-canary-{self.service}-{RUN_ID}.json")
        with open(jpath, "w") as f:
            json.dump(out, f, indent=2)
        print(f"results: {jpath}", flush=True)


def ab_write_pair(
    canary: Canary,
    method: str,
    path: str,
    body_fn: Callable[[Twin], Any],
    expect_status: Optional[int] = None,
    db_probe: Optional[Callable[[Twin, tuple[int, Any]], Any]] = None,
    compare_db: bool = True,
) -> tuple[tuple[int, Any], tuple[int, Any]]:
    """Run one write through A and B, compare status + envelope (+ DB probe state).

    body_fn(twin) lets each side carry its own marked identifiers while keeping
    the compare domain identical after normalization.
    """
    ra = canary.twin_a().request(method, path, body_fn(canary.twin_a()))
    rb = canary.twin_b().request(method, path, body_fn(canary.twin_b()))
    detail = ""
    if expect_status is not None and (ra[0] != expect_status or rb[0] != expect_status):
        raise AssertionError(
            f"status A={ra[0]} B={rb[0]} expected {expect_status}; "
            f"A body={json.dumps(ra[1], default=str)[:200]}"
        )
    ok, diff = deep_equal(ra[1], rb[1])
    if not ok:
        raise AssertionError(f"envelope mismatch: {diff}")
    if db_probe and compare_db:
        sa = db_probe(canary.twin_a(), ra)
        sb = db_probe(canary.twin_b(), rb)
        ok, diff = deep_equal(sa, sb)
        if not ok:
            raise AssertionError(f"DB-state mismatch: {diff}")
    return ra, rb


def require_secret() -> str:
    secret = os.environ.get("NEXUS_INTERNAL_SECRET")
    if not secret:
        print("NEXUS_INTERNAL_SECRET not set — draft canary cannot run", flush=True)
        raise SystemExit(2)
    return secret


def sse_first_frames(port: int, path: str, seconds: float = 3.0) -> bytes:
    """Read the SSE stream for a window; return raw body bytes (HTTP/1.0, unframed)."""
    s = socket.create_connection(("localhost", port), timeout=seconds + 2)
    try:
        s.sendall(f"GET {path} HTTP/1.0\r\nHost: localhost\r\n\r\n".encode())
        s.settimeout(seconds)
        buf = b""
        end = time.time() + seconds
        while time.time() < end:
            try:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
            except socket.timeout:
                break
        return buf
    finally:
        s.close()
