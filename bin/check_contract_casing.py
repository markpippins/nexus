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

  --registry  print the storage-shaped exemption registry and its effect, then exit.

Storage-shaped exemptions (Architect Decision 18): a snake_case field whose wire name is
*required* to equal a database column name is exempt, but only when DECLARED in
`bin/contract-casing-storage-exempt.json` with the column it mirrors and the PR that introduced
it. Every undeclared snake_case field remains a violation, inside or outside the CDLC family --
this is a field-level registry, deliberately not a family-level exemption, so the ratchet keeps
measuring real drift instead of acquiring a family-shaped blind spot. The `leaks` floor is
compared against the UNEXEMPT count.

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
REGISTRY = ROOT / "bin" / "contract-casing-storage-exempt.json"

# Identifiers that are not wire field names.
NON_FIELD = {
    "model", "enum", "union", "interface", "op", "import", "namespace",
    "using", "scalar", "const", "fn", "extends", "is", "alias",
}
CAMEL = re.compile(r"^[a-z][a-zA-Z0-9]*$")
SNAKE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)+$")


class RegistryError(Exception):
    """A malformed registry entry. Hard failure: an unvalidated registry can over-exempt."""


def load_registry() -> list[dict]:
    """Load and VALIDATE the storage-shaped exemption registry (Decision 18).

    Every entry must carry field + a `table.column` + the introducing PR + a rationale. The
    column requirement is the whole point: it forces the justification to be checkable, and it
    is what stops the registry becoming a suppression list for fields that merely look
    storage-shaped. A registry that cannot be validated is an error, never a warning -- a
    silently-ignored registry would report violations as exempt.
    """
    if not REGISTRY.exists():
        return []
    raw = json.loads(REGISTRY.read_text())
    entries = raw.get("entries", [])
    seen: set[tuple[str, str]] = set()
    for i, entry in enumerate(entries):
        where = f"registry entry #{i}"
        for key in ("field", "column", "introduced_by", "rationale"):
            if not str(entry.get(key, "")).strip():
                raise RegistryError(f"{where}: missing required key '{key}'")
        field = entry["field"]
        if not SNAKE.match(field):
            raise RegistryError(
                f"{where}: field '{field}' is not snake_case, so it could never be a violation "
                "and the entry is dead weight")
        column = entry["column"]
        if "." not in column or column.startswith(".") or column.endswith("."):
            raise RegistryError(
                f"{where}: column '{column}' must be a real `table.column` (or "
                "schema.table.column). Decision 18 requires the storage shape to be nameable; "
                "a field with no underlying column does not belong in this registry.")
        contract = entry.get("contract")
        if contract is not None and not str(contract).strip():
            raise RegistryError(f"{where}: 'contract' present but empty; omit it or give a path")
        key = (field, contract or "*")
        if key in seen:
            raise RegistryError(f"{where}: duplicate entry for field '{field}' scope {contract or '*'}")
        seen.add(key)
    return entries


def is_exempt(field_name: str, contract: str, entries: list[dict]) -> bool:
    """Exempt only on an exact declared match, or an unscoped declaration."""
    for entry in entries:
        if entry["field"] != field_name:
            continue
        scope = entry.get("contract")
        if scope is None or scope == contract:
            return True
    return False


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


def scan(entries: list[dict] | None = None) -> dict:
    result = {"mixed": [], "snake": [], "camel": [], "empty": []}
    leaks: dict[str, list[str]] = {}
    exempt: dict[str, list[str]] = {}
    total = 0
    exempt_total = 0
    for f in sorted(TYPESPEC.rglob("models.tsp")):
        rel = str(f.relative_to(ROOT))
        shape = classify(f)
        result[shape].append(rel)
        if shape in ("mixed", "snake"):
            found = snake_fields(f)
            unexempt = []
            for name in found:
                if entries is not None and is_exempt(name, rel, entries):
                    exempt.setdefault(rel, []).append(name)
                    exempt_total += 1
                else:
                    unexempt.append(name)
            if unexempt:
                leaks[rel] = unexempt
                total += len(unexempt)
            if rel in exempt and not unexempt:
                # Every leak in this file is declared storage-shaped. It is still not
                # camelCase, so it stays classified mixed/snake, but it no longer counts
                # against the floor.
                leaks.setdefault(rel, [])
    result["leaks"] = leaks
    result["exempt"] = exempt
    result["leak_total"] = total
    result["leak_total_raw"] = total + exempt_total
    result["exempt_total"] = exempt_total
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true",
                    help="fail on any violation (use once the baseline is zero)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--registry", action="store_true",
                    help="print the storage-shaped exemption registry and exit")
    args = ap.parse_args()

    try:
        entries = load_registry()
    except RegistryError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    if args.registry:
        print("storage-shaped exemption registry (Decision 18)")
        print("  a field is exempt ONLY if declared here, with the column it mirrors.")
        print("  any undeclared snake_case field is a violation, in or out of the CDLC family.")
        print(f"\n  declared entries: {len(entries)}")
        by_field = {}
        for e in entries:
            by_field.setdefault(e["field"], []).append(e)
        for field in sorted(by_field):
            for e in by_field[field]:
                scope = e.get("contract") or "* (unscoped)"
                print(f"    {field:28} -> {e['column']:44} [{e['introduced_by']}]  scope: {scope}")
        reg = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {}
        for note in reg.get("_deliberately_not_registered", []):
            label = note.get("field") or ", ".join(note.get("fields", []))
            print(f"\n  deliberately NOT registered: {label}")
            if note.get("why"):
                print(f"    {note['why'][:150]}")
        return 0

    data = scan(entries)
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
                          "leak_total_raw": data["leak_total_raw"],
                          "exempt_total": data["exempt_total"],
                          "registry_entries": len(entries),
                          "violations": violations}), file=sys.stdout)
        print(f"{len(violations)} violation file(s), leaks {data['leak_total']} "
              f"(+{data['exempt_total']} declared storage-shaped, "
              f"{data['leak_total_raw']} raw; ratchet baseline: mixed {base['mixed']}, "
              f"snake {base['snake']}, leaks {base_leaks})",
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
        print(f"\n  leaked storage names (occurrence count, the ratchet metric): "
              f"{data['leak_total']} unexempt"
              f"  (+{data['exempt_total']} declared storage-shaped, "
              f"{data['leak_total_raw']} raw)")
        if data["exempt"]:
            print("\n  declared storage-shaped (exempt, Decision 18 registry):")
            for f, names in sorted(data["exempt"].items()):
                print(f"    {f}")
                print(f"      {', '.join(sorted(set(names)))}")

    if args.strict:
        if data["leak_total"]:
            print(f"\nFAIL: {data['leak_total']} unexempt violation(s) under --strict",
                  file=sys.stderr)
            return 1
        print("\nOK: no violations")
        return 0

    grew = (len(data["mixed"]) > base["mixed"]) or (len(data["snake"]) > base["snake"]) \
        or (data["leak_total"] > base_leaks)
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
