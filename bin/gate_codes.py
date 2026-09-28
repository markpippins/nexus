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
    ATT_STALE_HEAD     queue tester re-attestation; 1 post per dedup key
    ATT_SHAPE_UNSEEN   route to tester/analyst adjudication; 1 post per key
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

ALL_CODES = frozenset({
    GH_LOOKUP_FAILED, HEAD_DATE_UNKNOWN,
    PR_NOT_OPEN, PR_DRAFT, MERGE_CONFLICT, MERGE_UNKNOWN,
    CI_NO_CHECKS, CI_PENDING, CI_FAIL,
    ATT_MISSING, ATT_SHAPE_UNSEEN, ATT_STALE_HEAD,
    ATT_LOOKUP_FAILED, ATT_BYPASSED,
})

# Codes whose condition may clear on the next cycle without anyone acting:
# consumers retry silently and post nothing.
TRANSIENT_CODES = frozenset({MERGE_UNKNOWN, CI_PENDING})

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
