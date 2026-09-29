#!/usr/bin/env python3
"""Report the casing shape of every contract in typespec/v1 (Ruling 19d6f725).

The contract declares the consumer-visible surface, which is camelCase and never names a
database column. This script finds the contracts that violate that, so the violation is
visible rather than discovered during a port.

Runs in two modes:

  (default)   report every shape, and fail only if the count of violations has *increased*
              above the committed baseline. 13 files violate today; a gate that blocked on
              merge would hold all 13 corrections behind the first one. The baseline only
              moves down, so this is a ratchet, not a suggestion.

  --strict    fail on any violation. Use when the baseline reaches zero.

A mixed file is the serious case: it means storage names leaked into a consumer contract
through a raw-row return path, which breaks when the column is renamed.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TYPESPEC = ROOT / "typespec" / "v1"
BASELINE = ROOT / "bin" / "contract-casing-baseline.json"
EXEMPTIONS = ROOT / "bin" / "contract-casing-exemptions.json"

# Identifiers that are not wire field names.
NON_FIELD = {
    "model", "enum", "union", "interface", "op", "import", "namespace",
    "using", "scalar", "const", "fn", "extends", "is", "alias",
}
CAMEL = re.compile(r"^[a-z][a-zA-Z0-9]*$")
SNAKE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)+$")


def fields(path: pathlib.Path) -> list[str]:
    """Extract top-level field names from a models.tsp."""
    out = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"\s{1,6}([A-Za-z_][A-Za-z0-9_]*)\??\s*:", line)
        if not m:
            continue
        name = m.group(1)
        if name in NON_FIELD or name.startswith("_"):
            continue
        out.append(name)
    return out


def classify(path: pathlib.Path) -> str:
    names = fields(path)
    has_camel = any("_" not in n and CAMEL.match(n) and re.search(r"[a-z][A-Z]", n) for n in names)
    has_snake = any(SNAKE.match(n) for n in names)
    if has_camel and has_snake:
        return "mixed"
    if has_snake:
        return "snake"
    if has_camel:
        return "camel"
    return "empty"


def snake_fields(path: pathlib.Path) -> list[str]:
    """Every snake_case field name in the file -- the violation count, not the file count.

    Counting *files* is blind to the most likely regression: piling more column leaks onto a
    contract that already leaks. A file-count ratchet reports that file as unchanged while it
    gets worse. Counting occurrences catches it.
    """
    return [n for n in fields(path) if SNAKE.match(n)]


def load_exemptions() -> dict[str, list[dict]]:
    """Storage-shaped field exemption registry (Decision 18, record c53289ef).

    A snake_case field is exempt ONLY if declared here with the storage location
    (DB column or jsonb key) whose name the wire field is required to mirror —
    the database is canonical per Tier-1 doctrine, so storage-shaped wire names
    are an architectural property, not authoring drift. Anything undeclared —
    inside or outside the CDLC family — remains a violation. Additions are
    governed by Decision 13 condition 2: cite the PR + storage location; no
    silent growth. Missing registry file = no exemptions (fail closed).
    """
    if not EXEMPTIONS.exists():
        return {}
    data = json.loads(EXEMPTIONS.read_text(encoding="utf-8"))
    fields = data.get("fields")
    if not isinstance(fields, dict):
        raise ValueError(f"{EXEMPTIONS}: expected a top-level 'fields' object")
    return fields


def scan(exemptions: dict[str, list[dict]] | None = None) -> dict:
    if exemptions is None:
        exemptions = load_exemptions()
    result = {"mixed": [], "snake": [], "camel": [], "empty": []}
    leaks: dict[str, list[str]] = {}
    exempt: dict[str, list[str]] = {}
    total = 0
    exempt_total = 0
    for f in sorted(TYPESPEC.rglob("models.tsp")):
        shape = classify(f)
        result[shape].append(str(f.relative_to(ROOT)))
        if shape in ("mixed", "snake"):
            found = snake_fields(f)
            if found:
                kept = [n for n in found if n not in exemptions]
                exc = [n for n in found if n in exemptions]
                if kept:
                    leaks[str(f.relative_to(ROOT))] = kept
                    total += len(kept)
                if exc:
                    exempt[str(f.relative_to(ROOT))] = exc
                    exempt_total += len(exc)
    result["leaks"] = leaks
    result["leak_total"] = total
    result["exempt"] = exempt
    result["exempt_total"] = exempt_total
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true",
                    help="fail on any violation (use once the baseline is zero)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    data = scan()
    violations = data["mixed"] + data["snake"]
    base = (json.loads(BASELINE.read_text()) if BASELINE.exists()
            else {"mixed": 0, "snake": 0, "leaks": 0})
    base_leaks = base.get("leaks", 0)

    # The ratchet verdict, computed before any output path so both the JSON
    # early-return and the default-mode verdict below can use it.
    grew = (len(data["mixed"]) > base["mixed"]) or (len(data["snake"]) > base["snake"]) \
        or (data["leak_total"] > base_leaks)

    if args.json:
        # --json must be machine-pure: JSON on stdout, human verdict on
        # stderr, and an early return so the default-mode verdict print at
        # the bottom cannot leak onto stdout and corrupt the JSON. The exit
        # code still carries the ratchet verdict for shell callers.
        print(json.dumps({"counts": {k: len(v) for k, v in data.items() if isinstance(v, list)},
                          "leak_total": data["leak_total"],
                          "exempt_total": data.get("exempt_total", 0),
                          "violations": violations}), file=sys.stdout)
        print(f"{len(violations)} violation file(s), leaks {data['leak_total']} "
              f"(ratchet baseline: mixed {base['mixed']}, snake {base['snake']}, leaks {base_leaks})",
              file=sys.stderr)
        return 1 if grew else 0
    else:
        print("contract casing (Ruling 19d6f725: consumer-visible surface is camelCase)")
        for shape in ("camel", "mixed", "snake", "empty"):
            print(f"  {shape:6} {len(data[shape])}")
        if data["mixed"]:
            print("\n  mixed - storage names leaked into a consumer contract:")
            for f in data["mixed"]:
                print(f"    {f}")
        if data["snake"]:
            print("\n  snake - needs converging to camelCase:")
            for f in data["snake"]:
                print(f"    {f}")
        if data.get("exempt_total"):
            print(f"\n  storage-shaped exemptions (Decision 18 registry, declared in "
                  f"{EXEMPTIONS.name}): {data['exempt_total']} occurrence(s) across "
                  f"{len(data['exempt'])} file(s) — not counted as leaks")
        print(f"\n  leaked storage names (occurrence count, the ratchet metric): "
              f"{data['leak_total']}")

    if args.strict:
        if violations:
            print(f"\nFAIL: {len(violations)} violation(s) under --strict", file=sys.stderr)
            return 1
        print("\nOK: no violations")
        return 0

    if grew:
        print(f"\nFAIL: violations increased above baseline "
              f"(mixed {len(data['mixed'])}/{base['mixed']}, "
              f"snake {len(data['snake'])}/{base['snake']}, "
              f"leaks {data['leak_total']}/{base_leaks}) - do not add violations",
              file=sys.stderr)
        return 1
    print(f"\nOK: {len(violations)} violation file(s), at or below baseline "
          f"(mixed {len(data['mixed'])}/{base['mixed']}, "
          f"snake {len(data['snake'])}/{base['snake']}, "
          f"leaks {data['leak_total']}/{base_leaks}) - baseline only moves down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
