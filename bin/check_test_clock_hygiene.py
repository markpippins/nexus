#!/usr/bin/env python3
"""Guard against date-rot in test fixtures: the mixed-clock signature.

WHY THIS IS NOT "no hardcoded dates in tests"
---------------------------------------------
Hardcoded dates in tests are overwhelmingly legitimate and this repo uses them
correctly.  ``test_contract_casing_exemptions.py`` freezes ``2026-01-01`` /
``2099-01-01`` / ``2026-09-29`` and passes that frozen "today" explicitly to
``expired_computed()`` -- fully deterministic and correct.  A guard that
banned hardcoded dates would have to exempt all of that, and would still miss
the real defect.

THE DEFECT THIS CATCHES
-----------------------
Mixing a FROZEN time baseline with a LIVE clock read in the same module, where
the frozen baseline is used as the temporal operand.  Found in the wild on
2026-10-03: ``bin/tests/test_resolver_soak_report.py`` pinned ``NOW`` to
2026-10-02 while deriving ``OLD``/``RECENT`` from ``datetime.now()``.  Because
``resolver-soak-report.analyze()`` measures the window from the OLDEST fixture
timestamp to ``now``, the measured age decayed exactly one day per calendar
day -- 15d at authoring, under its 14d threshold on 2026-10-03 -- so a correct
test failed on schedule with no code change on the branch, reddening #715's
``mesh-register probe tests`` on Python 3.11 and 3.13.

WHAT IT DELIBERATELY DOES NOT CATCH
------------------------------------
A fixture that is *consistently* live but asserts something that was only true
at authoring time.  That failure mode needs the assertion's own logic to
change, not the clock.  ``test_contract_casing_exemptions.py`` is the live
example: it is correct to fail, because 11 shipped contract-casing exemptions
carry ``target: 2026-11-30`` and the test is a live canary on unmigrated debt.
Making it date-stable would silence a real alarm.  This guard therefore does
NOT touch it, and that is a feature.

For the behavioural counterpart (run the suite under a displaced clock and
diff failures), see the clock-shift detector in the audit record ``c801f521``.
This static guard is the cheap, always-on half; it is not a substitute.

Exit codes: 0 = clean, 1 = rot signature found, 2 = tool error.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

# Keyword arguments that mean "this value is the temporal reference point".
TEMPORAL_KWARGS = frozenset({
    "now", "today", "as_of", "at", "since", "until", "reference", "ref",
    "start", "end", "from_ts", "to_ts", "cutoff",
})

# Calls that read the real wall clock.
LIVE_CLOCK_METHODS = frozenset({"now", "utcnow", "today", "fromtimestamp"})
LIVE_CLOCK_FUNCS = frozenset({"time", "monotonic", "perf_counter"})

TEST_DIRS = ("bin/tests", "python", "typescript")


@dataclass
class Finding:
    path: str
    line: int
    rule: str
    baseline: str
    detail: str


def _is_frozen_temporal(node: ast.AST) -> bool:
    """A frozen temporal literal: datetime(Y,M,...) / date(Y,M,...) / ISO string."""
    # datetime(2026, 10, 2, 12, 0, 0, tzinfo=...) / date(2026, 10, 2)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in ("datetime", "date"):
        return bool(node.args) and isinstance(node.args[0], ast.Constant) \
            and isinstance(node.args[0].value, int)
    # "2026-10-02" / "2026-10-02T12:00:00" / "2026-09-18T09:13:44-0400"
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        v = node.value
        return (len(v) >= 10 and v[:4].isdigit() and v[4] == "-"
                and v[5:7].isdigit() and v[7] == "-" and v[8:10].isdigit())
    return False


def _is_live_clock(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Attribute) and f.attr in LIVE_CLOCK_METHODS:
        return True
    if isinstance(f, ast.Attribute) and f.attr == "time":
        return True
    if isinstance(f, ast.Name) and f.id in LIVE_CLOCK_FUNCS:
        return True
    return False


def _is_temporal_operand_use(node: ast.AST, name: str) -> bool:
    """True if `name` is used as the temporal reference (kwarg or comparison)."""
    # Used as now=<name> / today=<name> / ...
    if isinstance(node, ast.keyword) and node.arg in TEMPORAL_KWARGS:
        if isinstance(node.value, ast.Name) and node.value.id == name:
            return True
    # Used as a comparison operand:  x - name  or  name < x
    if isinstance(node, ast.Compare):
        operands = [node.left, *node.comparators]
        for o in operands:
            if isinstance(o, ast.Name) and o.id == name:
                return True
            if isinstance(o, ast.BinOp) and isinstance(o.left, ast.Name) \
                    and o.left.id == name:
                return True
    # Used as a positional arg to a temporal kwarg is not detectable; also treat
    # bare arithmetic on the name as temporal use: `now - REF`.
    if isinstance(node, ast.BinOp):
        for side in (node.left, node.right):
            if isinstance(side, ast.Name) and side.id == name:
                return True
    return False


def scan_source(src: str) -> list[Finding]:
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []

    findings: list[Finding] = []

    live_reads = [n for n in ast.walk(tree) if _is_live_clock(n)]
    if not live_reads:
        return []  # No live clock => cannot mix => cannot rot this way.

    # Module-level ALL-CAPS constants bound to a frozen temporal literal.
    frozen: list[tuple[str, ast.AST]] = []
    for stmt in tree.body:
        targets: list[ast.AST] = []
        if isinstance(stmt, ast.Assign):
            targets = stmt.targets
            value = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets = [stmt.target]
            value = stmt.value
        else:
            continue
        for t in targets:
            if isinstance(t, ast.Name) and t.id.isupper() and _is_frozen_temporal(value):
                frozen.append((t.id, value))

    for name, value in frozen:
        used = any(_is_temporal_operand_use(n, name) for n in ast.walk(tree))
        if not used:
            continue
        live_desc = _describe_live(live_reads[0])
        findings.append(Finding(
            path="", line=getattr(value, "lineno", 0), rule="frozen-baseline-with-live-clock",
            baseline=name,
            detail=(f"module-level frozen temporal baseline {name!r} is used as a "
                    f"temporal operand, but the module also reads the live clock "
                    f"({live_desc}). Derive both sides from one reference clock."),
        ))
    return findings


def _describe_live(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Attribute):
            base = f.value.id if isinstance(f.value, ast.Name) else "?"
            return f"{base}.{f.attr}()"
        if isinstance(f, ast.Name):
            return f"{f.id}()"
    return "live clock read"


def iter_test_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for d in TEST_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for p in base.rglob("*.py"):
            if "__pycache__" in p.parts or not p.is_file():
                continue
            if "test" in p.name.lower() or "tests" in p.parts:
                out.append(p)
    return sorted(out)


def scan_repo(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for p in iter_test_files(root):
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for f in scan_source(src):
            f.path = str(p.relative_to(root))
            findings.append(f)
    return findings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent))
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    root = Path(args.repo).resolve()
    if not root.is_dir():
        print(f"error: repo root not found: {root}", file=sys.stderr)
        return 2

    findings = scan_repo(root)

    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        if not findings:
            print("test clock hygiene: clean — no mixed-clock rot signatures.")
        else:
            print(f"test clock hygiene: {len(findings)} mixed-clock rot signature(s)\n")
            for f in findings:
                print(f"  {f.path}:{f.line}  [{f.rule}]  {f.detail}")

    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())