#!/usr/bin/env python3
"""role-vocab-drift — live role-vocabulary parity comparator (V190 unit).

Three-way pairwise check, exit 1 on any drift:

  LIVE   nebula.agent_records_history.agent_records_role_check (read-only)
  PIN    the ROLE-VOCAB PIN in the sql/ migration carrying the marker
         (exactly one repo-wide; discovery is dynamic)
  BOOT   sql/ci-bootstrap/nexus-ci-bootstrap.sql's agent_records_role_check

Chain of custody for the role vocabulary:
  * LIVE == PIN is enforced at V190 apply time by the migration's preflight
    drift gate (this tool extends that check to any moment, not just apply)
  * PIN == BOOT is enforced on every PR by wr-conf-042's hermetic parity
    suite (bin/tests/test_role_vocab_parity.py)
  * this tool verifies the whole chain against the LIVE database on demand —
    run it after any manual vocabulary change, any V-series apply, or on a
    timer if desired (exit 0 = green, JSON summary via --json)

Vocabulary comparison is set-based on the role literals minus the ''
structural escape; formatting differences never block.

Usage:
  role-vocab-drift.py            # human-readable table
  role-vocab-drift.py --json     # machine summary only
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

_REPO_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), ".."))
BOOT_PATH = os.path.join(_REPO_ROOT, "sql", "ci-bootstrap",
                         "nexus-ci-bootstrap.sql")


def _pin_path():
    """Locate the sql/ file carrying the ROLE-VOCAB PIN marker.

    The marker MOVES between widening migrations (V190 -> V197 -> ...), so
    discovery is dynamic; exactly one may exist (wr-conf-042 P3 enforces).
    """
    hits = []
    sql_dir = os.path.join(_REPO_ROOT, "sql")
    for base, _dirs, files in os.walk(sql_dir):
        for name in files:
            if not name.endswith(".sql"):
                continue
            path = os.path.join(base, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    if "ROLE-VOCAB PIN" in fh.read():
                        hits.append(path)
            except OSError:
                continue
    if len(hits) != 1:
        raise RuntimeError(
            "ROLE-VOCAB PIN marker must exist in exactly one sql/ file; "
            "found %d: %s" % (len(hits), ", ".join(hits) or "none"))
    return hits[0]

DSN = os.environ.get(
    "NEXUS_PG_DSN", "postgresql://pguser:pgpass@localhost:5432/nexus")

LITERAL_RE = re.compile(r"'([^']*)'")


def _vocab(text: str):
    """Role vocabulary = literal set minus the '' structural escape."""
    return sorted({m for m in LITERAL_RE.findall(text) if m})


def load_pin() -> str:
    with open(_pin_path(), encoding="utf-8") as fh:
        text = fh.read()
    mk = text.find("ROLE-VOCAB PIN")
    if mk < 0:
        raise RuntimeError("ROLE-VOCAB PIN marker missing from V190")
    arr = text.find("ARRAY[", mk)
    return text[arr:text.find("]", arr)]


def load_boot() -> str:
    with open(BOOT_PATH, encoding="utf-8") as fh:
        boot = fh.read()
    m = re.search(r"agent_records_role_check CHECK.*?ARRAY\[(.*?)\]\)\)\)",
                  boot, re.S)
    if not m:
        raise RuntimeError("agent_records_role_check not found in bootstrap")
    return m.group(0)


def load_live() -> str:
    """Read nebula's live constraint via psql (read-only)."""
    sql = ("SELECT pg_get_constraintdef(con.oid) FROM pg_constraint con "
           "WHERE con.conrelid = 'nebula.agent_records_history'::regclass "
           "AND con.conname = 'agent_records_role_check'")
    p = subprocess.run(
        ["psql", DSN, "-X", "-qAt", "-c", sql],
        capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        raise RuntimeError("live query failed: %s" % p.stderr.strip()[:200])
    out = p.stdout.strip()
    if not out:
        raise RuntimeError(
            "agent_records_role_check not found on nebula.agent_records_history")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    surfaces = {}
    errors = []
    for name, fn in (("LIVE", load_live), ("PIN", load_pin),
                     ("BOOT", load_boot)):
        try:
            surfaces[name] = sorted(_vocab(fn()))
        except Exception as exc:  # noqa: BLE001 — report, don't crash
            errors.append("%s: %s" % (name, exc))

    pairs = {}
    if len(surfaces) == 3:
        pairs["LIVE==PIN"] = surfaces["LIVE"] == surfaces["PIN"]
        pairs["PIN==BOOT"] = surfaces["PIN"] == surfaces["BOOT"]
        pairs["LIVE==BOOT"] = surfaces["LIVE"] == surfaces["BOOT"]
    drifted = bool(errors) or not all(pairs.values())

    if args.json:
        print(json.dumps({
            "tool": "role-vocab-drift",
            "drifted": drifted,
            "pairs": pairs,
            "counts": {k: len(v) for k, v in surfaces.items()},
            "vocab": surfaces,
            "errors": errors,
        }))
        return 1 if drifted else 0

    for name in ("LIVE", "PIN", "BOOT"):
        if name in surfaces:
            print("%-5s (%2d): %s" % (name, len(surfaces[name]),
                                      ", ".join(surfaces[name])))
    for label, ok in pairs.items():
        print("%-10s %s" % (label, "OK" if ok else "DRIFT"))
    for err in errors:
        print("ERROR     %s" % err)
    if drifted:
        print("\nDRIFT DETECTED — bring the three surfaces back to the pin "
              "(see ROLE-VOCAB PIN in V190)")
        return 1
    print("\nChain green: live == pin == bootstrap (%d roles)"
          % len(surfaces.get("PIN", [])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
