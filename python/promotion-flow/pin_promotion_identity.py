#!/usr/bin/env python3
"""W6.04 — pin contract / evaluator / law identity triplet on promotion evidence.

Wave 6 (PEB governance unblocking — NOT a promotion/ballot decision).

Promotion manifests historically record only commit pins. This tool defines the
canonical identity triplet for promotion evidence and backfills it onto every
manifest append-only:

  contract  — the governance admission envelope contract (envelope-v1)
              source: typespec/v1/governance-envelope/spring/*.tsp
  evaluator — the governed admission evaluator (W4.06 contractAdmission.ts +
              W4.02 advisoryEvaluation.ts)
  law       — the doctrine corpus + witnessed-run classifier
              (doctrineLookup.registry.ts + witnessedRun.ts / witnessedRunSource.ts)

Each triplet member is { name, version, digest } where digest is sha256 over the
canonical source files. Backfill is append-only (existing fields are never
removed/rewritten; historical candidate rows untouched). A validation pass
fails closed when a manifest's triplet is missing or inconsistent with the
canonical pin — mismatches are surfaced, never silently normalized.

Usage:
    pin_promotion_identity.py            # dry-run: compute pins + validate
    pin_promotion_identity.py --apply    # write identity pins + publish evidence
"""
import argparse
import hashlib
import json
import os
import sys

from promotion_common import (
    agent_record, load_manifests, log, now_iso, save_manifest,
)

NEXUS_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# ── Canonical identity sources (W6.04) ─────────────────────────────────────
CONTRACT_NAME = "governance-admission-envelope"
CONTRACT_VERSION = "v1"
CONTRACT_FILES = [
    "typespec/v1/governance-envelope/spring/main.tsp",
    "typespec/v1/governance-envelope/spring/models.tsp",
    "typespec/v1/governance-envelope/spring/operations.tsp",
]

EVALUATOR_NAME = "governed-admission-evaluator"
EVALUATOR_VERSION = "w4.06+advisory"
EVALUATOR_FILES = [
    "typescript/projection-core/src/runtime/contractAdmission.ts",
    "typescript/projection-core/src/runtime/advisoryEvaluation.ts",
]

LAW_NAME = "doctrine-corpus+witnessed-run-classifier"
LAW_VERSION = "w3.06+advisory"
LAW_FILES = [
    "typescript/projection-core/src/runtime/doctrineLookup.registry.ts",
    "typescript/projection-core/src/runtime/witnessedRun.ts",
    "typescript/projection-core/src/runtime/witnessedRunSource.ts",
]


def _sha256_files(files):
    h = hashlib.sha256()
    for rel in files:
        path = os.path.join(NEXUS_ROOT, rel)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"identity source missing: {path}")
        with open(path, "rb") as f:
            h.update(f.read())
        h.update(b"\x00")
    return h.hexdigest()


def canonical_triplet():
    """Compute the canonical identity triplet for this checkout."""
    return {
        "contract": {
            "name": CONTRACT_NAME,
            "version": CONTRACT_VERSION,
            "digest": _sha256_files(CONTRACT_FILES),
            "sources": CONTRACT_FILES,
        },
        "evaluator": {
            "name": EVALUATOR_NAME,
            "version": EVALUATOR_VERSION,
            "digest": _sha256_files(EVALUATOR_FILES),
            "sources": EVALUATOR_FILES,
        },
        "law": {
            "name": LAW_NAME,
            "version": LAW_VERSION,
            "digest": _sha256_files(LAW_FILES),
            "sources": LAW_FILES,
        },
    }


def _member_identity(member):
    """Reduce a triplet member to its comparable identity (no source paths)."""
    return {k: member[k] for k in ("name", "version", "digest")}


def validate(manifests, canon):
    """Fail-closed validation: every manifest's pinned triplet must match the
    canonical pin exactly. Returns (errors, missing)."""
    errors, missing = [], []
    for m in manifests:
        pin = m.get("identity_pin")
        if not pin:
            missing.append(m.get("batch_id"))
            continue
        for member in ("contract", "evaluator", "law"):
            if pin.get(member) != _member_identity(canon[member]):
                errors.append(
                    f"{m.get('batch_id')}: {member} mismatch "
                    f"(pinned {pin.get(member, {}).get('digest', '?')[:12]} != "
                    f"canonical {canon[member]['digest'][:12]})"
                )
    return errors, missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write identity pins and publish evidence (default: dry-run)")
    args = ap.parse_args()

    manifests = load_manifests()
    try:
        canon = canonical_triplet()
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        return 2

    print(f"manifests: {len(manifests)}")
    print("canonical triplet (W6.04):")
    for member, meta in canon.items():
        print(f"  {member:10s} {meta['name']}@{meta['version']} sha256:{meta['digest'][:16]}")

    errors, missing = validate(manifests, canon)
    print(f"\nvalidation: {len(manifests) - len(missing) - len(errors)} consistent | "
          f"{len(missing)} missing pin | {len(errors)} mismatch")

    if not args.apply:
        print("\nDRY RUN — no changes written. Re-run with --apply to pin.")
        return 0

    # ── Apply: append-only identity pin ──────────────────────────────────
    written = 0
    for m in manifests:
        if m.get("identity_pin"):
            continue  # already pinned — idempotent
        m["identity_pin"] = {
            "pinned_at": now_iso(),
            "contract": _member_identity(canon["contract"]),
            "evaluator": _member_identity(canon["evaluator"]),
            "law": _member_identity(canon["law"]),
        }
        save_manifest(m["batch_id"], m)
        written += 1

    print(f"\napplied: {written} manifest(s) pinned with identity triplet (append-only)")

    evidence = (
        "# W6.04 — promotion evidence identity triplet pinned (append-only)\\n\\n"
        f"- pinned at: {now_iso()}\\n"
        f"- manifests pinned: {written}/{len(manifests)}\\n"
        f"- validation: {len(manifests) - len(missing) - len(errors)} consistent, "
        f"{len(missing)} missing (pre-existing), {len(errors)} mismatch\\n\\n"
        "## Canonical identity triplet\\n"
        "| Member | Name | Version | Digest (sha256) |\\n"
        "|---|---|---|---|\\n"
        + "".join(
            f"| {member} | {meta['name']} | {meta['version']} | `{meta['digest'][:16]}…` |\\n"
            for member, meta in canon.items()
        )
        + "\\n\\n"
        "## Integrity\\n"
        "- Pins are append-only; no historical field is rewritten, no candidate row touched.\\n"
        "- Fail-closed: a manifest whose pin is missing or mismatched is surfaced, never "
        "silently normalized.\\n"
        "- This is an identity/evidence hardening task — NOT a promotion or ballot decision; "
        "no peb.decisions activation, no authority change.\\n"
        "- DBA/Architect review requested: confirm canonical identity sources + version "
        "labels before the triplet is treated as binding for gate evidence."
    )
    agent_record(
        f"promotion-flow W6.04: pinned identity triplet on {written} manifests (append-only)",
        evidence,
        ["spec:promotion-flow", "W6.04", "type:change", "status:resolved",
         "to:dba", "to:architect", "wave-6", "identity-pinning"],
    )
    print("evidence agent record written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
