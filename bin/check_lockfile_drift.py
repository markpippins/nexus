#!/usr/bin/env python3
"""Report lockfile-vs-installed drift across every tracked service.

    python3 bin/check_lockfile_drift.py              # human table, exit 1 on drift
    python3 bin/check_lockfile_drift.py --json       # machine-readable
    python3 bin/check_lockfile_drift.py --strict     # also fail when services were SKIPPED
    python3 bin/check_lockfile_drift.py --service moleculer/nexus-broker

Exit codes
    0  no drift (skips allowed unless --strict)
    1  at least one service DRIFTED
    2  bad usage / repo unreadable

A SKIPPED service means "could not check" -- typically no `node_modules`,
because a fresh CI checkout has none. Skips never count as passes: the summary
always prints them separately, so a run that checked nothing cannot be mistaken
for a run that checked everything. Use --strict in CI to turn that gap into a
failure rather than a footnote.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "lib"))

from lockfile_drift import (  # noqa: E402
    DRIFTED,
    MATCHED,
    SKIPPED,
    ServiceResult,
    check_service,
    iter_lockfiles,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _rel(lockfile: pathlib.Path, root: pathlib.Path) -> str:
    """Path relative to the scanned root, tolerating roots that differ from ours."""
    try:
        return str(lockfile.relative_to(root))
    except ValueError:
        return str(lockfile)


def _filter(results: list[ServiceResult], wanted: str | None, root: pathlib.Path) -> list[ServiceResult]:
    if not wanted:
        return results
    needle = wanted.rstrip("/")
    keep = [r for r in results if str(r.lockfile.parent).replace(str(root) + "/", "", 1).removesuffix("/") == needle]
    if not keep:
        raise SystemExit(f"no tracked lockfile for service {wanted!r}")
    return keep


def _show(results: list[ServiceResult], root: pathlib.Path) -> dict[str, int]:
    tally = {MATCHED: 0, DRIFTED: 0, SKIPPED: 0}
    drifted: list[ServiceResult] = []
    skipped: list[ServiceResult] = []

    for r in results:
        rel = _rel(r.lockfile, root)
        tally[r.status] += 1
        if r.status == DRIFTED:
            drifted.append(r)
            print(f"DRIFTED  {rel}  ({r.checked} packages checked, {len(r.drifts)} differ)")
            for d in r.drifts[:10]:
                print(f"           {d.kind:<8} {d.path}")
                print(f"                   pinned={d.pinned}  installed={d.installed or '<absent>'}")
            if len(r.drifts) > 10:
                print(f"           ... and {len(r.drifts) - 10} more")
        elif r.status == SKIPPED:
            skipped.append(r)
            print(f"SKIPPED  {rel}  ({r.reason})")
        else:
            print(f"MATCHED  {rel}  ({r.checked} packages)")

    print()
    print(f"{len(results)} service(s): {tally[MATCHED]} matched, {tally[DRIFTED]} drifted, {tally[SKIPPED]} skipped")
    if skipped:
        print(
            f"note: {len(skipped)} service(s) COULD NOT BE CHECKED (skipped != passed). "
            "Run where node_modules exists, or use --strict to fail on this."
        )
    return tally


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="emit machine-readable results")
    ap.add_argument("--strict", action="store_true", help="exit non-zero if any service was SKIPPED")
    ap.add_argument("--service", help="limit to one service directory, e.g. moleculer/nexus-broker")
    ap.add_argument("--root", default=str(REPO_ROOT), help="repository root (default: this repo)")
    args = ap.parse_args()

    root = pathlib.Path(args.root).resolve()
    lockfiles = list(iter_lockfiles(root))
    if not lockfiles:
        print(f"no package-lock.json found under {root}", file=sys.stderr)
        return 2

    results = [check_service(lf) for lf in lockfiles]
    try:
        results = _filter(results, args.service, root)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {
                    "total": len(results),
                    "matched": sum(1 for r in results if r.status == MATCHED),
                    "drifted": sum(1 for r in results if r.status == DRIFTED),
                    "skipped": sum(1 for r in results if r.status == SKIPPED),
                    "services": [
                        {
                            "service": str(r.lockfile.parent).replace(str(root) + "/", "", 1),
                            "lockfile": _rel(r.lockfile, root),
                            "status": r.status,
                            "checked": r.checked,
                            "reason": r.reason,
                            "drifts": [
                                {"path": d.path, "pinned": d.pinned, "installed": d.installed, "kind": d.kind}
                                for d in r.drifts
                            ],
                        }
                        for r in results
                    ],
                },
                indent=2,
            )
        )
    else:
        tally = _show(results, root)

    if tally[DRIFTED]:
        return 1
    if args.strict and tally[SKIPPED]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
