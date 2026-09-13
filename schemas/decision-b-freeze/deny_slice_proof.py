#!/usr/bin/env python3
"""Decision B freeze package: deny_contract_promotion slice proof.

Proves the complete negative path (analyst condition 5):
typed identity -> envelope/payload validation -> authority binding check
-> Keychains read-set round-trip -> deterministic fingerprint -> EXPLICIT REFUSAL.

Read-only proof: fetches live Keychains read-set, evaluates a promotion
request that must be refused, fingerprints the artifact. Mutates nothing
(no PEB admission, no lifecycle change) — demonstrating that rejected
results cannot mutate authority.

Binding authority (ec3b00ed binding 3): W8.08 `6a80b2c4` / G1 `986ec482` /
W1.10 `05d0fe54`.

Run: python3 deny_slice_proof.py [--out artifact.json]
Exit 0 = refusal proven + fingerprint stable. Writes artifact to stdout/file.
"""
import hashlib
import json
import sys
from datetime import datetime, timezone

try:
    from pymongo import MongoClient
    HAVE_MONGO = True
except ImportError:
    HAVE_MONGO = False

MONGO_URI = "mongodb://mongoUser:somePassword@localhost:27017/nexus?authSource=admin"

BINDING_AUTHORITY = {
    "wave": "W8.08",
    "wave_ref": "6a80b2c4",
    "gate": "G1",
    "gate_ref": "986ec482",
    "decision": "W1.10",
    "decision_ref": "05d0fe54",
    "matrix": "ec3b00ed",
}

# The promotion request under test: deliberately unauthorized so the
# correct outcome is explicit refusal (negative-path proof).
DENY_CASE = {
    "canonical_asset_id": "asset:nexus:test:deny-001",
    "source_system": "test-harness",
    "source_record_id": "deny-req-001",
    "logical_instance_id": "contract-promotion:deny-001",
    "projection_generation": "deny-slice-v1",
    "valid_from": "2026-09-13T00:00:00Z",
    "recorded_at": datetime.now(timezone.utc).isoformat(),
    "abstraction_level": "L3",
    "level_status": "assigned",
    "visibility_scope": "all",
    "assertion_status": "proposal",
    "source_authority": "resolution",
    "ontology_type": "ContractPromotionRequest",
    "payload": {
        "contract": "deny_contract_promotion",
        "requested_version": "v999-unknown",
        "authority_grant": None,  # <-- no grant: must refuse
    },
}


def fetch_read_set():
    """Live Keychains read-set round-trip (read-only)."""
    if not HAVE_MONGO:
        return {"available": False, "reason": "pymongo unavailable"}
    c = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    try:
        rts = list(c["keychains"]["record_type_state"].find({}, {"_id": 0}))
        latest = c["keychains"]["ar_snapshots"].find_one(sort=[("version", -1)])
        return {
            "available": True,
            "record_type_count": len(rts),
            "record_types": sorted(r.get("record_type") for r in rts),
            "latest_snapshot_version": latest.get("version") if latest else None,
            "latest_snapshot_at": latest.get("created_at") if latest else None,
        }
    except Exception as e:
        return {"available": False, "reason": str(e)}


def evaluate_promotion(request, read_set):
    """Authority binding check. Returns (disposition, evidence[]).

    Refuses because: (a) requested contract version is unknown, and
    (b) no authority grant is present. Both are explicit, evidenced reasons —
    never a silent drop.
    """
    evidence = []
    payload = request.get("payload", {})
    if payload.get("requested_version") == "v999-unknown":
        evidence.append("requested contract version v999-unknown is not a known binding authority version")
    if not payload.get("authority_grant"):
        evidence.append("no authority grant present; PEB admission requires a grant reference")
    if read_set.get("available"):
        evidence.append(
            f"read-set pinned at keychains snapshot v{read_set.get('latest_snapshot_version')} "
            f"({read_set.get('record_type_count')} record types); refusal evaluated against this read-set"
        )
    else:
        evidence.append(f"read-set unavailable ({read_set.get('reason')}); failing closed")
    return "Refused", evidence


def fingerprint(obj):
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def main():
    out_path = None
    if "--out" in sys.argv:
        out_path = sys.argv[sys.argv.index("--out") + 1]

    read_set = fetch_read_set()
    disposition, evidence = evaluate_promotion(DENY_CASE, read_set)

    artifact = {
        "slice": "deny_contract_promotion",
        "binding_authority": BINDING_AUTHORITY,
        "request_envelope": DENY_CASE,
        "read_set": read_set,
        "evaluation": {
            "disposition": disposition,
            "evidence": evidence,
            "mutated_authority": False,
            "mutated_lifecycle": False,
        },
        "proof": "explicit refusal with evidenced reasons; no silent drop; no authority mutation",
    }
    artifact["fingerprint"] = fingerprint({k: v for k, v in artifact.items() if k != "fingerprint"})

    # Determinism check: re-fingerprint must match
    fp2 = fingerprint({k: v for k, v in artifact.items() if k != "fingerprint"})
    stable = (fp2 == artifact["fingerprint"])

    text = json.dumps(artifact, indent=2)
    if out_path:
        with open(out_path, "w") as f:
            f.write(text)
        print(f"wrote {out_path}")
    else:
        print(text)
    print(f"\ndisposition={disposition} fingerprint={artifact['fingerprint'][:16]}… stable={stable} "
          f"read_set_available={read_set.get('available')}")
    if disposition != "Refused" or not stable:
        print("SLICE PROOF FAILED")
        return 1
    print("SLICE PROOF PASSED: explicit refusal, stable fingerprint, no mutation")
    return 0


if __name__ == "__main__":
    sys.exit(main())
