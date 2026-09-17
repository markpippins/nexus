#!/usr/bin/env python3
"""attestation-chain-demo — the Wave-3 prerequisite demonstration (DBA).

The architect's Wave-3 ruling (decision c141dd7a) requires the
attested-verification chain to be *demonstrated* before the lead-engineer
greenlight grant can be ratified in its own thread:

    work -> verification request -> evidence-backed attestation
         -> greenlight citing that attestation

This module makes the chain concrete and its refusal gates enforceable.
The gate contract is the deliverable the Wave-3 thread will cite:

  G1  self-attestation refused      — the work's authoring role cannot
                                      attest its own work
  G2  evidence-free attestation     — an attestation with no citable
      refused                          evidence artifacts is void
  G3  capability probe              — the attesting role must hold
                                      can_verify_work_requests on live
  G4  greenlight must cite a valid  — greenlight_without_verification_
      attestation                      attestation otherwise

Modes:
  --report         run the full demonstration: gates against the LIVE
                   capability set (nebula.roles via CONDUIT_PG_DSN), then
                   the positive chain with real role names
  --file-request   additionally file a REAL to:tester verification request
                   (agent record) for a real work unit with real evidence,
                   so the chain can be exercised by the attester for real

Hermetic unit tests: python/nexus_core/wrp/tests/test_attestation_chain.py
(wr-conf-031) — the gate contract is pure logic; the DB is injectable.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import urllib.request
from dataclasses import dataclass, field

# ── Chain objects ───────────────────────────────────────────────────────────


@dataclass
class VerificationRequest:
    work_ref: str            # agent record / PR / artifact id of the work
    requested_by: str        # the work's AUTHORING role (self-attest anchor)
    scope: str


@dataclass
class Attestation:
    work_ref: str
    attester_role: str
    evidence: list = field(default_factory=list)   # citable artifacts


@dataclass
class Greenlight:
    work_ref: str
    authority_role: str
    attestation: Attestation | None = None


@dataclass
class Refusal(Exception):
    gate: str
    reason: str

    def __str__(self) -> str:
        return f"[{self.gate}] {self.reason}"


# ── The gate contract ───────────────────────────────────────────────────────


def gate_self_attestation(request: VerificationRequest, att: Attestation) -> None:
    """G1: the authoring role cannot attest its own work."""
    if att.attester_role == request.requested_by:
        raise Refusal(
            "G1-self-attestation",
            f"role '{att.attester_role}' authored the work ({request.work_ref}) "
            f"and cannot attest it — verification must come from a second role",
        )


def gate_evidence(att: Attestation) -> None:
    """G2: an attestation with no citable evidence is void."""
    if not att.evidence or any(not str(e).strip() for e in att.evidence):
        raise Refusal(
            "G2-evidence-free",
            f"attestation for {att.work_ref} carries no citable evidence — "
            f"'tests pass' without named runs/artifacts is not verification",
        )


def gate_capability(att: Attestation, resolve_capabilities) -> None:
    """G3: the attester must hold can_verify_work_requests on live."""
    caps = resolve_capabilities(att.attester_role)
    if not caps.get("can_verify_work_requests"):
        raise Refusal(
            "G3-capability",
            f"role '{att.attester_role}' does not hold "
            f"can_verify_work_requests on live — not a valid attester",
        )


def gate_greenlight_citation(g: Greenlight, request: VerificationRequest,
                             accepted: list) -> None:
    """G4: greenlight must cite a valid, accepted attestation."""
    if g.attestation is None:
        raise Refusal(
            "greenlight_without_verification_attestation",
            f"greenlight for {g.work_ref} cites no attestation at all",
        )
    a = g.attestation
    if a.work_ref != g.work_ref or a.work_ref != request.work_ref:
        raise Refusal(
            "greenlight_without_verification_attestation",
            "greenlight cites an attestation for a different work unit",
        )
    if id(a) not in {id(x) for x in accepted}:
        raise Refusal(
            "greenlight_without_verification_attestation",
            "greenlight cites an attestation that never passed the gates "
            "(G1-G3) — attestation is not self-declared",
        )


# ── Live capability probe (injectable; default hits nebula.roles) ──────────


def make_live_resolver(dsn_env: str = "CONDUIT_PG_DSN"):
    """Return a resolve_capabilities(role) backed by the LIVE nebula.roles
    view (open bitemporal snapshot). Raises a clear error if unreachable —
    an unreachable database must NOT be attested as absent (the auditor
    epistemic rule)."""
    def resolve_capabilities(role: str) -> dict:
        try:
            import psycopg2  # noqa: deferred — hermetic tests never import it
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("psycopg2 required for the live probe") from exc
        dsn = os.environ.get(dsn_env,
                             "postgresql://pguser:pgpass@localhost:5432/nexus")
        conn = psycopg2.connect(dsn)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT can_verify_work_requests, can_greenlight "
                    "FROM nebula.roles WHERE name = %s", (role,))
                row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            return {"can_verify_work_requests": False, "can_greenlight": False}
        return {"can_verify_work_requests": row[0], "can_greenlight": row[1]}
    return resolve_capabilities


# ── The chain runner ────────────────────────────────────────────────────────


def run_chain(request: VerificationRequest, attestation: Attestation,
              greenlight: Greenlight, resolve_capabilities) -> dict:
    """Run work -> attestation -> greenlight through the gates; return the
    step report. Refusals propagate as exceptions — the caller decides
    whether a refusal is the demonstration (negative tests) or a failure."""
    steps = []

    gate_self_attestation(request, attestation)
    steps.append({"gate": "G1-self-attestation", "result": "PASS",
                  "detail": f"attester '{attestation.attester_role}' != author "
                            f"'{request.requested_by}'"})

    gate_evidence(attestation)
    steps.append({"gate": "G2-evidence-free", "result": "PASS",
                  "detail": f"{len(attestation.evidence)} citable evidence artifact(s)"})

    gate_capability(attestation, resolve_capabilities)
    steps.append({"gate": "G3-capability", "result": "PASS",
                  "detail": f"'{attestation.attester_role}' holds "
                            f"can_verify_work_requests on live"})

    accepted = [attestation]
    gate_greenlight_citation(greenlight, request, accepted)
    steps.append({"gate": "greenlight_without_verification_attestation",
                  "result": "PASS",
                  "detail": f"greenlight cites attestation for {greenlight.work_ref} "
                            f"that passed G1-G3"})
    return {"request": request, "attestation": attestation,
            "greenlight": greenlight, "steps": steps}


# ── The real demonstration payload (V177 work unit) ─────────────────────────

REAL_REQUEST = VerificationRequest(
    work_ref="agent-record:06889fc1 (V177 roles_history audit extension, PR #303, merge 28cf953b)",
    requested_by="dba",
    scope="test-verification: migration correctness + live trigger battery",
)
REAL_EVIDENCE = [
    "CI wr-conf-027 first run green (roles-history audit E2E, 10 tests)",
    "live battery: 3 NEBULA_AUDIT rows, same txid 17773430, zero residue",
    "R1 95c897ce / R2 06889fc1; merge commit 28cf953b",
]


def file_verification_request() -> str:
    """File a REAL to:tester verification request (agent record) for the
    V177 work unit, so the chain's middle step is exercisable for real."""
    body = (
        "## Verification request (attestation-chain demonstration, Wave 3)\n\n"
        f"**Work unit:** {REAL_REQUEST.work_ref}\n"
        f"**Authoring role:** {REAL_REQUEST.requested_by}\n"
        f"**Scope:** {REAL_REQUEST.scope}\n\n"
        "**Evidence offered (attest or refute each):**\n"
        + "\n".join(f"- {e}" for e in REAL_EVIDENCE) +
        "\n\n**Requested of tester:** attest with evidence citations per the "
        "grant (ratification 2f9acb11) — or refuse with the specific gap "
        "(wr_verification_without_evidence). A refusal here is the system "
        "working.\n\n"
        "Context: the lead-engineer greenlight grant (Wave 3) requires the "
        "attested-verification chain demonstrated (architect ruling c141dd7a); "
        "this request is its live middle step."
    )
    payload = {
        "recordType": "report",
        "role": "dba",
        "title": "Verification request → tester: V177 roles_history audit work unit (attestation chain, Wave 3)",
        "content": body,
        "tags": ["to:tester", "type:verification-request", "wave3",
                 "attestation-chain"],
        "level": 1,
        "visibilityScope": "all",
    }
    req = urllib.request.Request(
        "http://localhost:3101/api/agent-records",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        out = json.loads(resp.read().decode())
    return out.get("id", "?")


# ── CLI ─────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", action="store_true",
                    help="run the demonstration against live capabilities")
    ap.add_argument("--file-request", action="store_true",
                    help="also file the real to:tester verification request")
    args = ap.parse_args()

    if args.file_request:
        rid = file_verification_request()
        print(f"verification request filed: agent record {rid} (to:tester)")

    if not args.report and not args.file_request:
        ap.print_help()
        return 0

    resolve = make_live_resolver()
    print("=== attestation chain — live capability set ===")
    for role in ("dba", "tester", "engineer", "builder", "planner"):
        caps = resolve(role)
        print(f"  {role:12s} can_verify={caps['can_verify_work_requests']} "
              f"can_greenlight={caps['can_greenlight']}")

    print("\n=== negative demonstration (gates must refuse) ===")
    negatives = [
        ("G1", Refusal, lambda: gate_self_attestation(
            REAL_REQUEST, Attestation(REAL_REQUEST.work_ref, "dba", REAL_EVIDENCE)),
         "dba attests its own work"),
        ("G2", Refusal, lambda: gate_evidence(
            Attestation(REAL_REQUEST.work_ref, "tester", [])),
         "tester attests with no evidence"),
        ("G3", Refusal, lambda: gate_capability(
            Attestation(REAL_REQUEST.work_ref, "builder", REAL_EVIDENCE), resolve),
         "builder (verify=FALSE) attests"),
        ("G4", Refusal, lambda: gate_greenlight_citation(
            Greenlight(REAL_REQUEST.work_ref, "lead-engineer", None),
            REAL_REQUEST, []),
         "lead-engineer greenlights with no attestation"),
    ]
    for label, _, fn, desc in negatives:
        try:
            fn()
            print(f"  {label} FAIL: {desc} was accepted (BUG)")
            return 1
        except Refusal as exc:
            print(f"  {label} refused as designed: {desc} -> {exc}")

    print("\n=== positive chain (real roles, live capabilities) ===")
    # One attestation object: the greenlight must cite THE attestation that
    # passed the gates, not a structurally-equal lookalike (id()-identity is
    # the point — attestations are events, not reconstructible values).
    attestation = Attestation(REAL_REQUEST.work_ref, "tester", REAL_EVIDENCE)
    result = run_chain(
        REAL_REQUEST,
        attestation,
        Greenlight(REAL_REQUEST.work_ref, "lead-engineer", attestation),
        resolve,
    )
    for s in result["steps"]:
        print(f"  {s['gate']:42s} PASS  {s['detail']}")
    print("\nCHAIN DEMONSTRATED: work(dba) -> verification request -> "
          "tester attestation(evidence) -> lead-engineer greenlight(citation)")
    print(f"generated: {datetime.datetime.now(datetime.UTC).isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
