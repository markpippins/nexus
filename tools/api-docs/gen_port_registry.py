#!/usr/bin/env python3
"""Generate the moleculer port-map docs from the single-source registry.

Commissioned by architect ruling 7c97ea63 (decision 91540185) §2: the approved
port set was stored three times as hand-maintained prose (jvm/ARCHITECTURE.md,
moleculer/README.md, moleculer/PORT-MAP.md), so every port PR collided O(N²)
against every other. The registry `moleculer/ports.yaml` declares a port ONCE
(append-only, in ratification/merge order); this tool regenerates

  1. moleculer/PORT-MAP.md — table rows + the canary-port enumeration in the
     freeze prose + the Ruling-4 ratification ledger (previously hand-written
     narrative that silently lacked clauses for late-arriving ports)
  2. moleculer/README.md — twin table rows
  3. jvm/ARCHITECTURE.md — the canary-twin port enumeration

Each generated region sits between explicit markers
`<!-- BEGIN GENERATED: moleculer-port-registry/<region> ... -->`. Row
semantics are architect-owned (Ruling 4); this tool only renders what the
registry declares.

Usage:
    python3 tools/api-docs/gen_port_registry.py           # regenerate in place
    python3 tools/api-docs/gen_port_registry.py --check   # exit 1 on drift, never writes
    python3 tools/api-docs/gen_port_registry.py --root R  # operate on repo root R (tests)
"""
import argparse
import os
import re
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

MARKER = "<!-- GENERATED: moleculer-port-registry {kind} {region} -->"


def begin_marker(region):
    return MARKER.format(kind="BEGIN", region=region)


def end_marker(region):
    return MARKER.format(kind="END", region=region)


def load_registry(root):
    path = os.path.join(root, "moleculer", "ports.yaml")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "infra" not in data or "canary" not in data:
        raise SystemExit(f"{path}: expected top-level 'infra:' and 'canary:' lists")
    seen = set()
    for section in ("infra", "canary"):
        for e in data[section] or []:
            p = int(e["port"])
            if p in seen:
                raise SystemExit(f"{path}: duplicate port {p}")
            seen.add(p)
    return data


# ── region renderers ─────────────────────────────────────────────────────────

def readme_rows(reg):
    """Twin table rows for moleculer/README.md, in registry order."""
    lines = []
    for e in reg["infra"]:
        lines.append(f"| `{e['name']}/` | {e['port']} | {e['readme_counterpart']} | "
                     f"{e['readme_gate']} | {e['readme_status']} |")
    for e in reg["canary"]:
        lines.append(f"| `{e['name']}/` | {e['port']} | `{e['incumbent']}` (:{e['incumbent_port']}) | "
                     f"**`{e['incumbent']}/openapi.yaml`** via `tools/api-docs/check_drift.py` | "
                     f"{e['readme_status']} |")
    return "\n".join(lines)


def portmap_rows(reg):
    """Table rows for moleculer/PORT-MAP.md, in registry order."""
    lines = [e["map_row"] for e in reg["infra"]]
    for e in reg["canary"]:
        lines.append(f"| {e['port']} | `{e['name']}` (canary twin of `{e['incumbent']}`) | "
                     f"`{e['namespace']}` | `{e['incumbent']} :{e['incumbent_port']}` "
                     f"({e['description']}) | {e['map_registry_status']} |")
    return "\n".join(lines)


def canary_port_list(reg):
    return "/".join(str(e["port"]) for e in reg["canary"])


def ratification_ledger(reg):
    """Ruling-4 ratification ledger, generated from per-port registry fields.

    Replaces the hand-written narrative that silently lacked clauses for ports
    arriving after the last prose pass. `ratified: null` renders as PENDING —
    loud, not silent.
    """
    lines = [
        "- Canary rows in this table require architect ratification under Ruling 4",
        "  (port map is architect-owned). Per-port status, from the registry",
        "  (`moleculer/ports.yaml` — a port is declared once; this ledger is generated):",
    ]
    for e in reg["canary"]:
        mark = f"{e['ratified']} —" if e.get("ratified") else "PENDING —"
        lines.append(f"  - {e['port']} ({e['name']}): {mark} {e['ratification']}")
    return "\n".join(lines)


# ── region application ───────────────────────────────────────────────────────

def replace_region(text, region, body, relpath):
    """Swap the body between a named BEGIN/END marker pair.

    Exact-one-match enforcement: a missing pair is a setup error; a duplicated
    pair would silently regenerate the wrong region.
    """
    b, e = begin_marker(region), end_marker(region)
    starts = [i for i in range(len(text)) if text.startswith(b, i)]
    ends = [i for i in range(len(text)) if text.startswith(e, i)]
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        raise SystemExit(f"{relpath}: expected exactly one {b!r} ... {e!r} pair "
                         f"(found {len(starts)}/{len(ends)})")
    s = starts[0]
    return text[:s] + b + "\n" + body + "\n" + text[ends[0]:]


def replace_enumeration(text, prefix, new_value, relpath):
    """Replace '(a/b/c)' after `prefix` on the single line carrying it."""
    lines = text.splitlines(True)
    hits = [i for i, ln in enumerate(lines) if prefix in ln]
    if len(hits) != 1:
        raise SystemExit(f"{relpath}: expected exactly one line containing {prefix!r} "
                         f"(found {len(hits)})")
    line = lines[hits[0]]
    i = line.find(prefix) + len(prefix)
    j = line.find(")", i)
    if j == -1:
        raise SystemExit(f"{relpath}: enumeration not closed on: {line.strip()[:80]}")
    old = line[i:j]
    lines[hits[0]] = line[:i] + new_value + line[j:]
    return "".join(lines), old == new_value


def replace_arch_enumeration(text, new_value, relpath):
    """jvm/ARCHITECTURE.md shape: a line ending in 'canary twins', with the
    N/.../M enumeration starting the next line and closing at its first ')'."""
    lines = text.splitlines(True)
    hits = [i for i, ln in enumerate(lines) if ln.rstrip("\n").endswith("canary twins")]
    if len(hits) != 1:
        raise SystemExit(f"{relpath}: expected exactly one line ending in 'canary twins' "
                         f"(found {len(hits)})")
    i = hits[0] + 1
    line = lines[i]
    j = line.find(")")
    if j == -1:
        raise SystemExit(f"{relpath}: canary enumeration not closed: {line.strip()[:80]}")
    seg = line[:j]
    m = re.match(r"^(\s*)([0-9/]+)$", seg)
    if not m:
        raise SystemExit(f"{relpath}: unexpected canary enumeration shape: {line.strip()[:80]}")
    old = m.group(2)
    lines[i] = f"{m.group(1)}{new_value}{line[j:]}"
    return "".join(lines), old == new_value


def regenerate(root, reg, check=False):
    results = []  # (relpath, region, unchanged: bool)

    def apply(relpath, text, region, body):
        new = replace_region(text, region, body, relpath)
        results.append((relpath, region, new == text))
        return new

    # ── moleculer/README.md ──────────────────────────────────────────────
    rel = os.path.join("moleculer", "README.md")
    text = open(os.path.join(root, rel), encoding="utf-8").read()
    text = apply(rel, text, "readme-twin-table", readme_rows(reg))
    if not check:
        open(os.path.join(root, rel), "w", encoding="utf-8").write(text)

    # ── moleculer/PORT-MAP.md ────────────────────────────────────────────
    rel = os.path.join("moleculer", "PORT-MAP.md")
    text = open(os.path.join(root, rel), encoding="utf-8").read()
    text = apply(rel, text, "portmap-table", portmap_rows(reg))
    text, unchanged = replace_enumeration(
        text, "Canary/deployment ports (", canary_port_list(reg), rel)
    results.append((rel, "canary-enum", unchanged))
    text = apply(rel, text, "ratification-ledger", ratification_ledger(reg))
    if not check:
        open(os.path.join(root, rel), "w", encoding="utf-8").write(text)

    # ── jvm/ARCHITECTURE.md ──────────────────────────────────────────────
    rel = os.path.join("jvm", "ARCHITECTURE.md")
    text = open(os.path.join(root, rel), encoding="utf-8").read()
    text, unchanged = replace_arch_enumeration(text, canary_port_list(reg), rel)
    results.append((rel, "canary-enum", unchanged))
    if not check:
        open(os.path.join(root, rel), "w", encoding="utf-8").write(text)

    return results


def main():
    ap = argparse.ArgumentParser(
        description="Generate moleculer port docs from moleculer/ports.yaml")
    ap.add_argument("--check", action="store_true",
                    help="verify generated regions are byte-identical; exit 1 on drift; never writes")
    ap.add_argument("--root", default=DEFAULT_ROOT, help="repository root (default: auto)")
    args = ap.parse_args()

    reg = load_registry(args.root)
    results = regenerate(args.root, reg, check=args.check)

    for rel, region, unchanged in results:
        print(("OK   " if unchanged else "WROTE" if not args.check else "DRIFT")
              + f" {rel} [{region}]")

    if args.check:
        drifted = [r for r in results if not r[2]]
        if drifted:
            print(f"\n{len(drifted)} generated region(s) drifted from the registry.")
            print("Regenerate with: python3 tools/api-docs/gen_port_registry.py")
            return 1
        print("\nAll generated regions match the registry.")
        return 0
    print("Run with --check to verify byte-identical (CI does).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
