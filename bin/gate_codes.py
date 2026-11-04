#!/usr/bin/env python3
"""gate_codes.py -- structured gate-failure codes for the merge gate family.

Spec: agent record 86017db0 (proposal thread b18fadc0). One shared module
imported by both bin/merge_pr.py (emitter) and bin/attestation_janitor.py
(consumer) so the vocabulary cannot drift between them.

Contract:
- Each gate emits at most ONE code per run (first failing condition in
  deterministic order); a run's code set is the union across gates.
- Codes are stable identifiers; the human detail strings are unchanged and
  remain the reading surface. Codes are ADDITIVE: a report line without a
  code must be treated exactly as before (unknown/absent code = generic
  gate failure, fail closed, surfaced).
- Codes never narrow what a consumer treats as failure.

Emission format (merge_pr.py format_report):
    [FAIL] tester attestation (ATT_STALE_HEAD): newest attestation ...

Note on HEAD_DATE_UNKNOWN: the spec lists it under the GitHub-lookup gate
because the head-date FETCH lives there, but it surfaces on the attestation
gate (that is where the missing date changes the decision), so it is emitted
there. Routing treats it as a lookup-failure class either way.

Consumer routing (attestation_janitor.py, per spec table):
    ATT_MISSING        silent skip (state file only)
    ATT_TIMESTAMP_MISSING
                       operator action: a serialization regression on the
                       record endpoint, not a tester action. Routed as a
                       cross-PR SERVER DEFECT finding (one post per episode,
                       deduped on the code set alone — one defect blocks many
                       PRs), with wording stating that re-attesting will not
                       clear it. Retry next cycle; the janitor does NOT queue
                       a re-attestation (that is the misdiagnosis this code
                       replaces). Distinct from ATT_STALE_HEAD /
                       ATT_NO_CI_EVIDENCE, which stay per-(PR, codes, head)
                       because each needs that PR's tester to re-attest.
    ATT_STALE_HEAD     queue tester re-attestation; 1 post per dedup key
    ATT_NO_CI_EVIDENCE queue tester re-attestation (attestation lacks CI run
                       references, or a cited run is genuinely absent — gh
                       404); 1 post per dedup key
    ATT_CI_LOOKUP_FAILED transient lookup failure (gh 429/5xx/network);
                       retry next cycle, no posts (Decision 15 item D)
    ATT_SHAPE_UNSEEN   route to tester/analyst adjudication; 1 post per key
    ATT_POLICY_UNSUPPORTED
                       operator/gate action: the row declares a contract this
                       gate cannot read; upgrade the gate, do not re-attest
    ATT_VERDICT_MISSING
                       queue tester re-attestation: policy v2 requires a
                       verdict and the row states none (CI success is not a
                       judgement); 1 post per dedup key
    ATT_VERDICT_INVALID
                       route to tester/analyst adjudication: a verdict was
                       declared but is not in the v2 vocabulary; 1 post per key
    ATT_VERDICT_HOLD   author action: the tester withheld approval ("hold")
                       without naming a defect; 1 post per dedup key
    ATT_VENDOR_RULE_UNVERIFIED
                       operator/author action: an "approved by rule" claim
                       whose rule conditions do not hold at this head; 1 post
                       per dedup key
    MERGE_CONFLICT     NOT an anomaly (author action); 1 post per key
    MERGE_UNKNOWN      transient, retry next cycle, no posts
    CI_PENDING         transient, retry next cycle, no posts
    CI_NO_CHECKS       existing repairable path (unchanged, per-attempt)
    CI_FAIL            surface once per dedup key
    ATT_BYPASSED       refuse merge (unchanged); 1 post per dedup key
    GH_LOOKUP_FAILED / ATT_LOOKUP_FAILED / HEAD_DATE_UNKNOWN
                       retry, then surface (1 post per dedup key)

Dedup key: (PR, code-set, head) -- see attestation_janitor.anomaly_dedup.
"""

from __future__ import annotations

import re
from typing import List

# ── Gate 0: GitHub lookup ────────────────────────────────────────────────────
GH_LOOKUP_FAILED = "GH_LOOKUP_FAILED"
HEAD_DATE_UNKNOWN = "HEAD_DATE_UNKNOWN"

# ── Gate 1: PR ready ─────────────────────────────────────────────────────────
PR_NOT_OPEN = "PR_NOT_OPEN"
PR_DRAFT = "PR_DRAFT"
MERGE_CONFLICT = "MERGE_CONFLICT"
MERGE_UNKNOWN = "MERGE_UNKNOWN"

# ── Gate 2: CI ───────────────────────────────────────────────────────────────
CI_NO_CHECKS = "CI_NO_CHECKS"
CI_PENDING = "CI_PENDING"
CI_FAIL = "CI_FAIL"

# ── Gate 3: tester attestation ───────────────────────────────────────────────
ATT_MISSING = "ATT_MISSING"
ATT_SHAPE_UNSEEN = "ATT_SHAPE_UNSEEN"
ATT_STALE_HEAD = "ATT_STALE_HEAD"
ATT_LOOKUP_FAILED = "ATT_LOOKUP_FAILED"
ATT_BYPASSED = "ATT_BYPASSED"

# A matching attestation row exists but carries no usable createdAt, so the
# freshness predicate cannot be evaluated at all. This is a SERVER-side
# serialization defect, NOT a tester action: before this code the evaluator
# coerced the missing timestamp to 0, the row bound at epoch 0, and the gate
# reported ATT_STALE_HEAD — telling the tester to re-attest for a condition
# that no re-attestation can ever fix. That misdiagnosis is what froze the
# merge queue (DBA records 48ac13e2 / 2a51e900). Distinct from
# HEAD_DATE_UNKNOWN, which is the HEAD commit's date being unavailable.
ATT_TIMESTAMP_MISSING = "ATT_TIMESTAMP_MISSING"
# Attestation content carries no machine-verifiable CI evidence (no
# "CI run <id>" reference). Stated test counts ("54/54 pass") are the
# engine's self-attestation, not the tester's verification; verification
# references CI run IDs whose conclusion + head SHA the gate checks against
# GitHub. Routed like ATT_STALE_HEAD: the tester re-attests with real run
# references (spec: agent record d7989f31 follow-up family).
ATT_NO_CI_EVIDENCE = "ATT_NO_CI_EVIDENCE"

# A cited CI run could not be LOOKED UP transiently (gh 429/5xx/network/
# timeout; anything unparseable short of a definitive 404). The attestation
# itself is fine - re-running the gate next cycle likely verifies it, so
# nagging the tester to re-attest would be noise (Decision 15 item D, work
# order a49acfc9 review finding). Routed TRANSIENT: silent retry, no posts.
# Genuinely-absent references (gh 404) still route ATT_NO_CI_EVIDENCE.
ATT_CI_LOOKUP_FAILED = "ATT_CI_LOOKUP_FAILED"

# The attestation row itself DECLARES a rejecting verdict ("Verdict:
# REJECTED", "changes requested", "blocked"). Before this code, gate 3 read
# the row's shape, freshness and CI citation but never its verdict, so a
# rejection that happened to cite a CI run was a PASSING attestation — the
# gate would merge exactly the code the tester refused (record ebb0a911).
# #726/#823 were stopped only by their missing citation, one cited run away
# from merging.
#
# Routed as AUTHOR action, not a re-attestation request: re-attesting the
# same head cannot clear a rejection, the work has to change first.
ATT_VERDICT_REJECTED = "ATT_VERDICT_REJECTED"

# Policy v2 (operator ruling 2026-11-04): an attestation declares the contract
# it was written under, and under v2 it MUST declare a verdict. Green CI + a
# fresh head prove the mechanical checks passed at a head; they cannot prove a
# tester exercised judgement, and the gate had been treating the former as
# sufficient evidence for the merge doctrine. Legacy rows (no policy line) keep
# their original reading, so this is not a retroactive judgement of history.
#
# MISSING and REJECTED stay separate codes on purpose: they are different
# operational diagnoses. MISSING means the required judgement never happened
# (ask the tester for one); REJECTED means it happened and the answer was no
# (the work has to change). Collapsing them would send both down one path.
ATT_VERDICT_MISSING = "ATT_VERDICT_MISSING"
# A verdict WAS declared, but it is outside the v2 vocabulary (e.g. "verdict:
# deferred"). Distinct from MISSING for the same reason: the tester acted, and
# the right response is adjudication, not a request to re-attest.
ATT_VERDICT_INVALID = "ATT_VERDICT_INVALID"
# The tester answered "hold" — judgement happened, approval withheld, no
# specific defect asserted (live row acc7f963, #841). Author action, like a
# rejection, but without a defect to fix until the tester says what blocks.
ATT_VERDICT_HOLD = "ATT_VERDICT_HOLD"
# The row declares a policy the gate does not implement (a future v3, say).
# Reading newer evidence under looser rules could accept exactly what it was
# written to forbid, so an unknown contract fails closed.
ATT_POLICY_UNSUPPORTED = "ATT_POLICY_UNSUPPORTED"
# An "approved by rule" verdict that does not satisfy the vendor rule at gate
# time. Vendor automation is a different authority from a tester's judgement
# and must never impersonate one: the tag, the author, the live diff and the
# recorded rule/version are all re-proved. #860/#861 carry type:vendor-bump
# while touching bin/ and config/ — a classification label is not evidence of
# eligibility.
ATT_VENDOR_RULE_UNVERIFIED = "ATT_VENDOR_RULE_UNVERIFIED"

ALL_CODES = frozenset({
    GH_LOOKUP_FAILED, HEAD_DATE_UNKNOWN,
    PR_NOT_OPEN, PR_DRAFT, MERGE_CONFLICT, MERGE_UNKNOWN,
    CI_NO_CHECKS, CI_PENDING, CI_FAIL,
    ATT_MISSING, ATT_SHAPE_UNSEEN, ATT_STALE_HEAD,
    ATT_LOOKUP_FAILED, ATT_BYPASSED, ATT_NO_CI_EVIDENCE,
    ATT_CI_LOOKUP_FAILED, ATT_TIMESTAMP_MISSING, ATT_VERDICT_REJECTED,
    ATT_VERDICT_MISSING, ATT_VERDICT_INVALID, ATT_VERDICT_HOLD,
    ATT_POLICY_UNSUPPORTED, ATT_VENDOR_RULE_UNVERIFIED,
})

# Codes whose condition may clear on the next cycle without anyone acting:
# consumers retry silently and post nothing.
TRANSIENT_CODES = frozenset({MERGE_UNKNOWN, CI_PENDING, ATT_CI_LOOKUP_FAILED})

# Codes that are routine (no forum-visible surface): a PR without an
# attestation yet is normal, not news.
SILENT_CODES = frozenset({ATT_MISSING})

# Token shape: parenthesized ALL-CAPS identifier, e.g. "(ATT_STALE_HEAD)".
# "(BYPASSED)" and human parentheses do not match ALL_CODES and are ignored.
_CODE_TOKEN = re.compile(r"\(([A-Z][A-Z0-9_]{2,})\)")


def extract_codes(report_text: str) -> List[str]:
    """Ordered, de-duplicated codes found in a merge_pr.py report.

    Empty list when the report predates code emission (or carries none) --
    callers must fall back to their pre-code behavior on empty.
    """
    seen: List[str] = []
    for tok in _CODE_TOKEN.findall(report_text or ""):
        if tok in ALL_CODES and tok not in seen:
            seen.append(tok)
    return seen
