#!/usr/bin/env python3
"""Decision B freeze package: deterministic level-assignment fixtures.

Implements the v2 level policy (analyst condition 2): level assigned by
CONTENT CLASS, never by source table, role, or visibility. Unclassifiable
content returns explicit unknown/disputed — population code must not guess.

Run: python3 level_fixtures.py (exit 0 = all pass, exit 1 = failure)
"""
import sys

# Content-class patterns -> level. Ordered most-specific first.
# Each rule inspects ONLY the content text/kind, never source/role/visibility.
LEVEL_RULES = [
    ("L4", ["doctrine", "ownership boundary", "governance rule", "ontology definition",
            "role boundary", "authority matrix"]),
    ("L3", ["architectural rationale", "subsystem relationship", "trade-off",
            "migration philosophy", "planning rationale", "interpretation of"]),
    ("L2", ["data model", "state model", "schema", "relationship", "subsystem design",
            "organized evaluation", "semantic structure"]),
    ("L1", ["execution result", "model-check output", "receipt", "log entry",
            "fact", "observation", "measurement", "raw evidence", "mechanics"]),
]

FORBIDDEN_SIGNALS = ["source_table", "source_system", "role", "visibility",
                     "visibility_scope", "producer"]


def assign_level(content_kind, content_text):
    """Deterministic level assignment. Returns (level, status).

    status is 'assigned', 'unknown', or 'disputed'. Never guesses.
    Inspects content only; raises if caller passes source/role/visibility signals.
    """
    text = f"{content_kind} {content_text}".lower()
    # Refuse to classify on forbidden signals (defense: policy violation is explicit)
    for sig in FORBIDDEN_SIGNALS:
        if f"{sig}:" in text or f"{sig}=" in text:
            return ("unknown", "disputed")
    hits = []
    for level, patterns in LEVEL_RULES:
        if any(p in text for p in patterns):
            hits.append(level)
    if len(hits) == 1:
        return (hits[0], "assigned")
    if len(hits) > 1:
        # Multi-level content splits; here we report the lowest (most-evidence)
        # and mark disputed so a human splits it into per-level projections.
        order = ["L1", "L2", "L3", "L4"]
        hits.sort(key=order.index)
        return (hits[0], "disputed")
    return ("unknown", "unknown")


FIXTURES = [
    # (content_kind, content_text, expected_level, expected_status)
    ("receipt", "execution receipt for transition approval with outcome verified", "L1", "assigned"),
    ("model_check", "model-check output: invariant holds across 1200 states", "L1", "assigned"),
    ("fact", "shrapnel fact: object 9001 field asset_id", "L1", "assigned"),
    ("schema", "subsystem design: concept_relationship binding for candidate state", "L2", "assigned"),
    ("evaluation", "organized evaluation of proposition disposition with evidence refs", "L2", "assigned"),
    ("rationale", "architectural rationale: why aegis verifies but never decides (trade-off)", "L3", "assigned"),
    ("interpretation", "interpretation of the model-check result for the registry", "L3", "assigned"),
    ("doctrine", "doctrine: level is abstraction altitude, ownership boundary for roles", "L4", "assigned"),
    ("governance", "governance rule: PEB admits, KG never decides (authority matrix)", "L4", "assigned"),
    ("vague", "something happened somewhere", "unknown", "unknown"),
    ("mixed", "execution result with architectural rationale for the migration philosophy", "L1", "disputed"),
    ("policy_violation", "role:builder visibility:builder content about facts", "unknown", "disputed"),
]


def main():
    failures = 0
    for kind, text, exp_level, exp_status in FIXTURES:
        got_level, got_status = assign_level(kind, text)
        ok = (got_level == exp_level and got_status == exp_status)
        mark = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"[{mark}] {kind}: got ({got_level},{got_status}) expected ({exp_level},{exp_status})")
    print(f"\n{len(FIXTURES) - failures}/{len(FIXTURES)} level fixtures pass")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
