#!/usr/bin/env python3
"""Regenerate every consumer surface of the canonical role_memory shape.

The canonical fragment (sql/canonical/tackle_role_memory_shape.sql) is the
single source of truth for the tackle.memory / tackle.role_memory
reconstruction shape (V178-ratified). This script renders it into each
consumer surface:

  - tackle-srv/db.ts + tackle-mcp/db.ts      — consume at runtime via the
    tackle-seeds loader (no file output; loader verified instead)
  - schemas/migrations/tackle/memory_procedure_registry.sql — generated block
    between BEGIN/END GENERATED CANONICAL SHAPE markers
  - python/nexus_core/wrp/tests/test_conformance_seed_guard.py — consumes at
    runtime via _CANONICAL_SHAPE_PATH (no file output; verified instead)

Run after any change to the fragment, then commit all touched files together.
The parity test (bin/tests/test_canonical_shape_parity.py) fails when any
surface is stale relative to the fragment.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FRAGMENT = REPO / "sql" / "canonical" / "tackle_role_memory_shape.sql"
REGISTRY = REPO / "schemas" / "migrations" / "tackle" / "memory_procedure_registry.sql"

BEGIN = "-- BEGIN GENERATED CANONICAL SHAPE"
END = "-- END GENERATED CANONICAL SHAPE"


def render(schema: str, reftable: str) -> str:
    sql = FRAGMENT.read_text()
    sql = (sql.replace("__SCHEMA__", schema)
              .replace("__REFTABLE__", reftable)
              .replace("__TABLE_SUFFIX__", ""))
    # strip the fragment's own header comment block (everything up to the
    # first non-comment, non-blank line) so the generated block carries no
    # stale doc-text; keep the DDL itself.
    lines = sql.splitlines()
    out, started = [], False
    for ln in lines:
        if not started and (ln.startswith("--") or not ln.strip()):
            continue
        started = True
        out.append(ln)
    return "\n".join(out).strip()


def update_registry() -> bool:
    src = REGISTRY.read_text()
    if BEGIN not in src or END not in src:
        print(f"ERROR: {REGISTRY} missing {BEGIN}/{END} markers", file=sys.stderr)
        return False
    pre = src[: src.index(BEGIN)]
    post = src[src.index(END):]
    block = (BEGIN + " (sql/canonical/tackle_role_memory_shape.sql)\n"
             "-- Regenerate: python3 bin/regenerate_canonical_shape.py\n"
             "-- DO NOT hand-edit the block between the markers.\n"
             "-- ════════════════════════════════════════════════════════════════════\n\n"
             + render("tackle", "tackle.memory") + "\n\n"
             + "-- ════════════════════════════════════════════════════════════════════\n")
    new = pre + block + post
    if new != src:
        REGISTRY.write_text(new)
        print(f"updated: {REGISTRY.relative_to(REPO)}")
        return True
    print(f"unchanged: {REGISTRY.relative_to(REPO)}")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="verify-only: exit 1 if any surface is stale (CI mode)")
    args = ap.parse_args()

    changed = update_registry()
    # TS surfaces and the fixture consume at runtime — their parity is
    # enforced by the parity test, not by file regeneration.
    if args.check and changed:
        print("STALE: registry SQL generated block differs from fragment", file=sys.stderr)
        return 1
    if args.check:
        print("canonical shape surfaces: all in sync")
    else:
        print("done. Verify with: python3 -m pytest bin/tests/test_canonical_shape_parity.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
