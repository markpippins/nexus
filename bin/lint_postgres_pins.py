#!/usr/bin/env python3
"""lint_postgres_pins.py — enforce the fleet PostgreSQL standard on CI pins.

Why: fleet PG audit (record 4879ebe2, thread ecb91216) found 9 workflows
silently pinned to postgres:16 while the fleet standard is 17 (R1 direction;
ratified standard per the DBA thread and operator instruction). The same
class had bitten before — PR #300 failed restoring a PG17 bootstrap on a 16
service (documented in wr-conf-025.yml). This lint makes stale pins
un-reintroducible: any workflow pin below the standard fails CI.

Scope     : *.yml / *.yaml under a target dir (default .github/workflows)
FAILS on  : postgres:<major>... or pgvector/pgvector:pg<major> with
            major < PG_PIN_MIN_MAJOR (default 17)
WARNs on  : unversioned / latest postgres images (drifts silently; not a
            standard violation, but worth eyes)
IGNORES   : whole-line comments (e.g. the historical PR #300 note in
            wr-conf-025.yml); lines carrying the documented escape marker
            '# pg-pin-allow:' (use sparingly, cite the reason inline)

Exit codes: 0 clean (warnings ok) | 1 violations | 2 usage error

Env/args  : PG_PIN_MIN_MAJOR (default 17); argv[1] = target dir (optional)
"""
from __future__ import annotations

import os
import re
import sys

DEFAULT_MIN_MAJOR = 17
DEFAULT_DIR = ".github/workflows"

PIN_PATTERNS = (
    re.compile(r"\bpostgres:(\d+)"),               # postgres:16, postgres:17-alpine
    re.compile(r"pgvector/pgvector:pg(\d+)"),      # pgvector/pgvector:pg17
)
UNVERSIONED_RE = re.compile(
    r"\bimage:\s*['\"]?postgres(?:\s|['\"]|$)"  # bare `image: postgres`
    r"|\bpostgres:['\"]?latest\b"                # `postgres:latest`
)
ALLOW_MARKER = "pg-pin-allow"


def scan_path(root: str, min_major: int) -> tuple[list[str], list[str], int]:
    """Return (violations, warnings, allowed_count).

    violations/warnings are formatted 'file:line: message' strings, sorted
    by file then line.
    """
    violations: list[str] = []
    warnings: list[str] = []
    allowed = 0

    for dirpath, _dirnames, filenames in sorted(os.walk(root)):
        for name in sorted(filenames):
            if not name.endswith((".yml", ".yaml")):
                continue
            path = os.path.join(dirpath, name)
            # report paths relative to the scan root (annotation-friendly)
            rel = os.path.relpath(path, root)
            try:
                with open(path, encoding="utf-8") as fh:
                    lines = fh.readlines()
            except OSError as exc:
                warnings.append(f"{rel}:?: unreadable ({exc})")
                continue
            for lineno, raw in enumerate(lines, start=1):
                stripped = raw.strip()
                if stripped.startswith("#"):
                    continue  # whole-line comment (the wr-conf-025 case)
                if ALLOW_MARKER in raw:
                    allowed += 1
                    continue
                for pattern in PIN_PATTERNS:
                    for m in pattern.finditer(raw):
                        major = int(m.group(1))
                        if major < min_major:
                            violations.append(
                                f"{rel}:{lineno}: postgres pin major {major} < "
                                f"fleet standard {min_major}: {stripped[:100]}"
                            )
                if UNVERSIONED_RE.search(raw):
                    warnings.append(
                        f"{rel}:{lineno}: unversioned/latest postgres image "
                        f"(drifts silently): {stripped[:100]}"
                    )
    return violations, warnings, allowed


def main(argv: list[str]) -> int:
    target = argv[1] if len(argv) > 1 else DEFAULT_DIR
    if not os.path.isdir(target):
        print(f"pg-pin-lint: target dir not found: {target}", file=sys.stderr)
        return 2
    try:
        min_major = int(os.environ.get("PG_PIN_MIN_MAJOR", str(DEFAULT_MIN_MAJOR)))
    except ValueError:
        print("pg-pin-lint: PG_PIN_MIN_MAJOR must be an integer", file=sys.stderr)
        return 2

    violations, warnings, allowed = scan_path(target, min_major)

    for v in violations:
        # GitHub annotation + plain line (annotation needs file=path:line form)
        path, _, rest = v.partition(":")
        lineno, _, msg = rest.partition(":")
        print(f"::error file={path},line={lineno.strip()}::{msg.strip()}")
        print(f"pg-pin-lint: {v}", file=sys.stderr)
    for w in warnings:
        print(f"pg-pin-lint: WARNING {w}", file=sys.stderr)

    total = len(violations)
    print(
        f"pg-pin-lint: {total} violation(s), {len(warnings)} warning(s), "
        f"{allowed} allow-marker line(s) — standard: PG {min_major}"
    )
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
