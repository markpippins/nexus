#!/usr/bin/env python3
"""Run the KG projection freeze's seed gate (plan 8261645, package D3).

The freeze file (`typespec/convergence/kg-projection-contract-freeze.v1.json`)
declares six checks that must pass *before* any graph seeding. This runs them
against the committed artifacts and prints one line per check plus a summary.

Read-only: it hashes files, runs the freeze package's own contract test, and
compares vocabularies. It seeds nothing, writes nothing, and contacts no
service. Exit 0 only when all six checks pass.
"""

import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
FREEZE_DIR = ROOT / "schemas" / "decision-b-freeze"
FREEZE = ROOT / "typespec" / "convergence" / "kg-projection-contract-freeze.v1.json"
CLASSIFICATION = ROOT / "schemas" / "convergence" / "resolution-jsonb-classification.v1.json"

EXPECTED_MANIFEST_FINGERPRINT = (
    "9a073a105050d7e2a8c7e618265fbb6fad92fdccecbc042a8fa12ad6374685d8"
)


def check_freeze_manifest_intact():
    manifest = json.loads((FREEZE_DIR / "FREEZE-MANIFEST.json").read_text())
    artifacts = manifest["artifacts"]
    bad, missing = [], []
    for name, expected in artifacts.items():
        path = FREEZE_DIR / name
        if not path.is_file():
            missing.append(name)
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            bad.append(name)
    # The manifest's own fingerprint is sha256 over the sorted artifacts map.
    computed = hashlib.sha256(json.dumps(artifacts, sort_keys=True).encode()).hexdigest()
    ok = not bad and not missing and computed == EXPECTED_MANIFEST_FINGERPRINT
    detail = (
        f"{len(artifacts) - len(bad) - len(missing)}/{len(artifacts)} artifact hashes match; "
        f"manifest fingerprint {computed[:12]}… == expected"
    )
    if bad:
        detail += f"; mismatched: {bad}"
    if missing:
        detail += f"; missing: {missing}"
    return ok, detail


def check_envelope_contract():
    script = FREEZE_DIR / "jsonb_contract_test.py"
    proc = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, cwd=str(ROOT), timeout=120
    )
    tail = [ln for ln in proc.stdout.splitlines() if "contract cases pass" in ln]
    ok = proc.returncode == 0 and bool(tail) and not proc.stdout.count("[FAIL]")
    detail = (tail[-1] if tail else "no summary line").strip()
    if proc.returncode != 0:
        detail += f" (exit {proc.returncode})"
    return ok, detail


def check_relation_vocabulary_equal():
    freeze = json.loads(FREEZE.read_text())
    vocab = json.loads((FREEZE_DIR / "relation-vocabulary-map.json").read_text())
    relations = freeze["relations"]
    backbone = [entry["relation_type"] for entry in relations["frozen_backbone"]]
    governed = [
        entry["relation_type"] if isinstance(entry, dict) else entry
        for entry in relations["governed_general"]
    ]
    canonical = set(vocab["canonical_vocabulary"])
    declared = set(backbone) | set(governed)
    ok = declared == canonical and len(backbone) + len(governed) == len(canonical)
    detail = (
        f"{len(declared)} declared == {len(canonical)} canonical "
        f"({len(backbone)} backbone + {len(governed)} governed)"
    )
    if not ok:
        detail += f"; only-in-freeze: {sorted(declared - canonical)}; only-in-vocabulary: {sorted(canonical - declared)}"
    return ok, detail


def check_section_owner_complete():
    freeze = json.loads(FREEZE.read_text())
    owners = {k for k in freeze["ownership"] if not k.startswith("$")}
    sections = freeze["sections"]["canonical"]
    bad = [s["section"] for s in sections if s["owner"] not in owners]
    ok = not bad and all(s.get("owner") for s in sections)
    detail = f"{len(sections) - len(bad)}/{len(sections)} canonical sections resolve to one owner"
    if bad:
        detail += f"; unknown owners for: {bad}"
    return ok, detail


def check_no_non_canonical_seed(seeded_rows=()):
    freeze = json.loads(FREEZE.read_text())
    seeded_rows = tuple(seeded_rows)
    excluded = [s["section"] for s in freeze["sections"]["non_canonical"]]
    offenders = sorted({row["section"] for row in seeded_rows if row["section"] in excluded})
    ok = not offenders
    detail = (
        f"{len(seeded_rows)} seeded row(s); non-canonical targets excluded: "
        f"{{{', '.join(excluded)}}}"
    )
    if offenders:
        detail += f"; offenders: {offenders}"
    return ok, detail


def check_jsonb_classification_present(seeded_columns=()):
    classification = json.loads(CLASSIFICATION.read_text())
    classes = set(classification["classes"])
    columns = classification["columns"]
    unclassified = [c for c in columns if c.get("class") not in classes]
    seeded_missing = [c for c in seeded_columns if c not in {f'{x["table"]}.{x["column"]}' for x in columns}]
    ok = not unclassified and not seeded_missing
    detail = (
        f"{len(columns) - len(unclassified)}/{len(columns)} classified columns carry a declared class; "
        f"{len(seeded_columns)} seeded node/edge column(s) feeding the graph "
        f"({'seeding not performed' if not seeded_columns else 'all classified'})"
    )
    if unclassified:
        detail += f"; unclassified: {[c.get('column') for c in unclassified]}"
    if seeded_missing:
        detail += f"; seeded but unclassified: {seeded_missing}"
    return ok, detail


def main():
    # This run seeds nothing, so checks 5 and 6 evaluate with zero seeded
    # rows/columns — stated explicitly rather than silently passing.
    results = [
        ("freeze_manifest_intact", check_freeze_manifest_intact()),
        ("envelope_contract", check_envelope_contract()),
        ("relation_vocabulary_equal", check_relation_vocabulary_equal()),
        ("section_owner_complete", check_section_owner_complete()),
        ("no_non_canonical_seed", check_no_non_canonical_seed(seeded_rows=())),
        ("jsonb_classification_present", check_jsonb_classification_present(seeded_columns=())),
    ]
    print("# KG projection seed gate (plan 8261645, package D3)")
    print(f"# freeze: {FREEZE.relative_to(ROOT)}")
    print(f"# result: not_run_by_the_decomposition; run now by bin/convergence_seed_gate.py")
    print()
    for name, (ok, detail) in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    passed = sum(1 for _, (ok, _) in results if ok)
    print()
    print(f"{passed}/{len(results)} checks pass")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
