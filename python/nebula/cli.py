"""Command-line entry point for the W-A duplicate-history tooling.

    python3 -m nebula.cli inventory --json /tmp/inventory.json
    python3 -m nebula.cli reconcile --log /tmp/reconciliation.jsonl --plan
    python3 -m nebula.cli reconcile --log /tmp/reconciliation.jsonl --apply
    python3 -m nebula.cli verify-log --log /tmp/reconciliation.jsonl

``inventory`` is read-only. ``reconcile --apply`` appends dispositions to
the append-only log; ``--plan`` prints them without writing. The DB
migration is never applied from here — ``reconcile --sql`` renders the
statements for the DBA lane to apply.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .collect import DEFAULT_DSN, build_live_inventory, resolve_manifest_dir
from .duplicate_history import Inventory
from .reconciliation_log import ReconciliationLog, dispositions_for

DEFAULT_ROOT = Path(__file__).resolve().parents[2]


def _inventory_from_args(args: argparse.Namespace) -> Inventory:
    manifest_dir = resolve_manifest_dir(args.root, args.manifest_dir)
    inventory, _census = build_live_inventory(dsn=args.dsn, manifest_dir=manifest_dir)
    return inventory


def cmd_inventory(args: argparse.Namespace) -> int:
    inventory = _inventory_from_args(args)
    doc = inventory.to_dict()
    if args.json:
        Path(args.json).write_text(json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8")
        print(f"wrote {args.json}")
    else:
        print(json.dumps(doc, indent=2, sort_keys=True))

    print(
        f"\n{len(inventory.sets)} duplicate-history sets across "
        f"{len(inventory.package_ids)} packages "
        f"({len(inventory.open_packages)} still open)",
        file=sys.stderr,
    )
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    inventory = _inventory_from_args(args)
    log = ReconciliationLog(args.log)
    proposals = list(dispositions_for(inventory))

    if args.plan:
        for s, ref, disposition, reason in proposals:
            print(f"{s.package_id}\t{ref}\t{disposition}\t{reason}")
        print(f"\n{len(proposals)} proposed dispositions (nothing written)", file=sys.stderr)
        return 0

    if not args.apply:
        print("refusing to write without --apply", file=sys.stderr)
        return 2

    written = 0
    for s, ref, disposition, reason in proposals:
        member = next((m for m in s.members if m.ref == ref), None)
        log.append(
            set_id=s.id,
            surface=s.surface,
            kind=s.kind,
            condition=s.condition,
            member_ref=ref,
            member_fingerprint=member.fingerprint if member else "",
            disposition=disposition,
            reason=reason,
            primary_ref=s.members[0].ref if s.members else "",
        )
        written += 1

    ok, problems = log.verify()
    print(f"appended {written} dispositions to {args.log}", file=sys.stderr)
    print(f"chain verify: {'OK' if ok else 'BROKEN'}", file=sys.stderr)
    for p in problems:
        print(f"  {p}", file=sys.stderr)
    return 0 if ok else 1


def cmd_sql(args: argparse.Namespace) -> int:
    log = ReconciliationLog(args.log)
    for stmt in log.to_sql():
        print(stmt)
    return 0


def cmd_verify_log(args: argparse.Namespace) -> int:
    log = ReconciliationLog(args.log)
    ok, problems = log.verify()
    entries = log.entries()
    print(f"{len(entries)} entries in {args.log}")
    print(f"chain verify: {'OK' if ok else 'BROKEN'}")
    for p in problems:
        print(f"  {p}")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nebula.cli", description=__doc__)
    parser.add_argument("--dsn", default=DEFAULT_DSN, help="PostgreSQL DSN")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="repository root")
    parser.add_argument("--manifest-dir", default=None, help="override manifest directory")
    sub = parser.add_subparsers(dest="command", required=True)

    p_inv = sub.add_parser("inventory", help="build the duplicate-history inventory")
    p_inv.add_argument("--json", help="write the inventory to this path")
    p_inv.set_defaults(func=cmd_inventory)

    p_rec = sub.add_parser("reconcile", help="record dispositions in the append-only log")
    p_rec.add_argument("--log", required=True, help="path to the JSONL reconciliation log")
    p_rec.add_argument("--plan", action="store_true", help="print proposals, write nothing")
    p_rec.add_argument("--apply", action="store_true", help="append the dispositions")
    p_rec.set_defaults(func=cmd_reconcile)

    p_sql = sub.add_parser("sql", help="render the log as DBA-lane SQL")
    p_sql.add_argument("--log", required=True)
    p_sql.set_defaults(func=cmd_sql)

    p_ver = sub.add_parser("verify-log", help="verify the log's hash chain")
    p_ver.add_argument("--log", required=True)
    p_ver.set_defaults(func=cmd_verify_log)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
