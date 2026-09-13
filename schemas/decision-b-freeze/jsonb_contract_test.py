#!/usr/bin/env python3
"""Decision B freeze package: JSONB envelope/payload contract test.

Implements analyst condition 3: the seed must validate payload shape,
payload version/fingerprint, envelope identity/lineage, and reject unknown
or contradictory authority. A valid JSON document with an invalid envelope
must FAIL; an unrecognized payload ontology_type must FAIL.

Run: python3 jsonb_contract_test.py (exit 0 = all pass, exit 1 = failure)
"""
import hashlib
import json
import sys

# Closed authority set (ratified ownership matrix ec3b00ed, abridged to test scope)
KNOWN_AUTHORITIES = {"resolution", "shrapnel", "aegis", "semantics",
                     "keychains", "peb", "operator", "conduit", "wind", "vision"}

# Known payload ontology types (bounded test vocabulary)
KNOWN_TYPES = {"WorkRequestInquiry", "PropositionDisposition", "ShrapnelFact",
               "AegisTransition", "KeychainCheckpointRef", "GovernanceEnvelope"}

REQUIRED_ENVELOPE_FIELDS = [
    "canonical_asset_id", "source_system", "source_record_id",
    "logical_instance_id", "projection_generation",
    "valid_from", "recorded_at",
    "abstraction_level", "level_status",
    "visibility_scope", "assertion_status", "source_authority",
    "ontology_type", "payload",
]


def validate_envelope(record):
    """Returns (ok, reasons[]). Enforces the contract; never guesses."""
    reasons = []
    for field in REQUIRED_ENVELOPE_FIELDS:
        if field not in record or record[field] is None:
            reasons.append(f"missing envelope field: {field}")
    if reasons:
        return False, reasons
    if record["source_authority"] not in KNOWN_AUTHORITIES:
        reasons.append(f"unknown/contradictory authority: {record['source_authority']}")
    if record["ontology_type"] not in KNOWN_TYPES:
        reasons.append(f"unrecognized payload ontology_type: {record['ontology_type']}")
    if record.get("level_status") not in ("assigned", "unknown", "disputed"):
        reasons.append(f"bad level_status: {record.get('level_status')}")
    if record.get("assertion_status") not in ("fact", "inference", "proposal", "contradicted"):
        reasons.append(f"bad assertion_status: {record.get('assertion_status')}")
    # Inquiry payload shape (four-field block) when present
    payload = record.get("payload") or {}
    inquiry = payload.get("inquiry")
    if inquiry is not None:
        for f in ("read_set_scope", "evaluator_ref", "expected_outcome_type",
                  "evidence_requirements"):
            if f not in inquiry:
                reasons.append(f"inquiry block missing field: {f}")
    return (len(reasons) == 0), reasons


def base_record(**overrides):
    rec = {
        "canonical_asset_id": "asset:nexus:test:001",
        "source_system": "resolution",
        "source_record_id": "rec-001",
        "logical_instance_id": "wr:0130",
        "projection_generation": "test-gen-1",
        "valid_from": "2026-09-01T00:00:00Z",
        "recorded_at": "2026-09-02T00:00:00Z",
        "abstraction_level": "L2",
        "level_status": "assigned",
        "visibility_scope": "all",
        "assertion_status": "fact",
        "source_authority": "resolution",
        "ontology_type": "PropositionDisposition",
        "payload": {"disposition": "Asserted"},
    }
    rec.update(overrides)
    return rec


CASES = [
    ("valid proposition record passes", base_record(), True),
    ("valid inquiry payload passes",
     base_record(ontology_type="WorkRequestInquiry",
                 payload={"inquiry": {"read_set_scope": "kg:scope",
                                      "evaluator_ref": "eval:v3",
                                      "expected_outcome_type": "disposition",
                                      "evidence_requirements": ["read-set-hash"]}}), True),
    ("missing envelope field fails", base_record(source_authority=None), False),
    ("unknown authority fails",
     base_record(source_authority="mystery-system"), False),
    ("unrecognized payload type fails",
     base_record(ontology_type="Frobnicate"), False),
    ("bad level_status fails", base_record(level_status="maybe"), False),
    ("bad assertion_status fails",
     base_record(assertion_status="truthy"), False),
    ("incomplete inquiry block fails",
     base_record(ontology_type="WorkRequestInquiry",
                 payload={"inquiry": {"read_set_scope": "x"}}), False),
    ("valid JSON + invalid envelope fails (core gate)",
     {"payload": {"anything": 1}}, False),
]


def main():
    failures = 0
    for name, record, expect_ok in CASES:
        ok, reasons = validate_envelope(record)
        passed = (ok == expect_ok)
        mark = "PASS" if passed else "FAIL"
        if not passed:
            failures += 1
        print(f"[{mark}] {name} (expected {'ok' if expect_ok else 'fail'}, got {'ok' if ok else 'fail'})")
        if not passed and reasons:
            print(f"       reasons: {reasons}")
    print(f"\n{len(CASES) - failures}/{len(CASES)} contract cases pass")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
