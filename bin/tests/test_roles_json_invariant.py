"""Hermetic invariant guard for config/roles/roles.json (Ruling 8).

Ruling 8 (record 7d42fc22): the role vocabulary must be normalized BEFORE any
write path starts rejecting routing tags, because a reject step in front of an
unnormalized vocabulary converts a *deliverable* address into an *undeliverable*
one. The named instance: `to:Rover` normalizes to `rover`, which is not in the
vocabulary, so a reject step would strand a live deliverable address. And
`config/roles/roles.json` itself carried capitalized role keys (`DBA`, `Rover`)
with no invariant test over the file — this module is that test.

Why it matters mechanically. Inbox routing matches a tag built as

    "to:" + role.strip().lower()

(bin/check-inbox.sh; mirrors nebula-mcp `normalizeRole()`). So a role key that is
not already lowercase produces a routing tag that reaches no inbox unless the
lowercased form is ALSO a canonical key. The real cost is on record: the
supervisor's own ruling `24162dba`, tagged `to:DBA`, stayed undelivered for ~6.5h
before it was found by accident.

CLOSED 2026-10-03 (PR #712, Ruling 8 normalization applied live). This guard
was authored while the drift was still live: roles.json carried capitalized
`DBA` and `Rover` keys, and the guard PINNED them via KNOWN_NONCANONICAL rather
than silently canonicalizing (that ruling belongs to the supervisor). PR #712
deleted both keys, so KNOWN_NONCANONICAL is now EMPTY and this guard asserts the
fully canonical end-state: it goes RED on any NEW non-canonical key, and also on
a stale exemption. It must be rebased onto the normalized head before landing —
against pre-#712 main it is red BY DESIGN (the exemptions are no longer live).

What this guard enforces
------------------------
1. every role key is canonical lowercase `[a-z0-9_-]+` (the routing vocabulary
   shape — hyphens are required: `engineer-ii`, `analyst-ii`) — OR is listed in
   KNOWN_NONCANONICAL with a citation;
2. no two role keys collide case-insensitively — the true deliverability hazard,
   which must fail even when a colliding key is exempted;
3. every KNOWN_NONCANONICAL entry is live and genuinely non-canonical (stale
   exemptions must be deleted, not left to rot).

Deliberately NOT done here
--------------------------
* This guard does not *canonicalize* the keys. Ruling 8 assigns that to a
  canonicalization ruling (supervisor thread "DBA spelling canonicalization",
  re-filed 2026-10-01), and I1 bars one role from unilaterally closing another's
  domain. It makes the drift LOUD and pinned; it does not silently rewrite it.
* This guard does not enable or implement routing-tag rejection. That is a
  separate change; doing it here would re-open the exact hazard Ruling 8 forbids.

No network, no services: hermetic.
"""

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
ROLES_FILE = REPO / "config" / "roles" / "roles.json"

# The canonical key shape. `to:` routing lowercases, so any key not already in
# this shape normalizes into a different string than it was written as.
CANONICAL_KEY = re.compile(r"^[a-z0-9_-]+$")

# Known non-canonical keys, pending supervisor canonicalization.
#
# CLOSED 2026-10-03: the two historical exemptions (`DBA`, `Rover`) are gone —
# PR #712 applied the Ruling 8 normalization live and deleted them from
# roles.json, so the pending set is EMPTY and the vocabulary is fully canonical.
# Kept as an empty dict rather than deleted so the fail-closed shape survives:
# a non-canonical key NOT listed here fails the guard, and a listed key that has
# become canonical (or disappeared) fails as a stale exemption. Any new entry
# MUST carry a citation naming the ruling that tolerates it.
KNOWN_NONCANONICAL: dict[str, str] = {}

# Sanity floor: a truncated or emptied roles file must not pass silently.
MIN_ROLES = 20


# ── pure checker (fixture-free, so synthetic vocabularies can exercise it) ──

def load_roles(path=ROLES_FILE):
    return json.loads(Path(path).read_text())["roles"]


def canonical_violations(roles, known_noncanonical):
    """Return a list of violation strings for a role-key mapping. Empty == sound."""
    problems = []

    for key in roles:
        if not CANONICAL_KEY.match(key) and key not in known_noncanonical:
            problems.append(
                f"non-canonical role key {key!r} (expected {key.lower()!r}); "
                f"not in KNOWN_NONCANONICAL"
            )

    # Case-collision: two keys whose lowercase forms are equal. This is the
    # deliverability hazard and is NOT exemptible — `to:` routing cannot tell
    # them apart, so the tag is ambiguous by construction.
    seen = {}
    for key in roles:
        low = key.lower()
        if low in seen:
            problems.append(
                f"case-collision: {seen[low]!r} and {key!r} both normalize to "
                f"{low!r} — `to:{key}` is ambiguous and cannot be delivered"
            )
        else:
            seen[low] = key

    # Stale exemptions: listed, but missing or already canonical.
    for key, reason in known_noncanonical.items():
        if key not in roles:
            problems.append(
                f"stale exemption: {key!r} is in KNOWN_NONCANONICAL but not in "
                f"roles.json — remove it"
            )
        elif CANONICAL_KEY.match(key):
            problems.append(
                f"stale exemption: {key!r} is canonical now — remove it from "
                f"KNOWN_NONCANONICAL"
            )
        if not str(reason).strip():
            problems.append(f"exemption {key!r} carries no citation")

    return problems


# ── structure ──────────────────────────────────────────────────────────────

class TestStructure:
    def test_file_parses_and_has_roles(self):
        data = json.loads(ROLES_FILE.read_text())
        assert isinstance(data.get("roles"), dict), "roles.json has no `roles` object"
        assert len(data["roles"]) >= MIN_ROLES, (
            f"only {len(data['roles'])} roles — expected >= {MIN_ROLES}"
        )

    def test_every_role_value_is_an_object(self):
        for key, val in load_roles().items():
            assert isinstance(val, dict), f"{key!r}: role value must be an object"


# ── the invariant ──────────────────────────────────────────────────────────

class TestCanonicalVocabulary:
    def test_no_unknown_noncanonical_keys(self):
        problems = canonical_violations(load_roles(), KNOWN_NONCANONICAL)
        assert not problems, (
            "roles.json violates the Ruling 8 vocabulary invariant:\n  "
            + "\n  ".join(problems)
            + "\n\nA new non-canonical role key produces a routing tag that "
            "reaches no inbox. Canonicalize it, or add a CITED entry to "
            "KNOWN_NONCANONICAL if a pending ruling owns it."
        )

    def test_known_noncanonical_is_exactly_the_pending_set(self):
        # Pins the size of the tolerated set: it may only shrink (via the
        # canonicalization ruling), never grow without this test being edited.
        roles = load_roles()
        live = {k for k in roles if not CANONICAL_KEY.match(k)}
        assert live == set(KNOWN_NONCANONICAL), (
            f"tolerated non-canonical keys drifted: file has {sorted(live)}, "
            f"KNOWN_NONCANONICAL has {sorted(KNOWN_NONCANONICAL)}"
        )

    def test_no_case_collision_between_keys(self):
        roles = load_roles()
        lowered = {}
        collisions = []
        for key in roles:
            lowered.setdefault(key.lower(), []).append(key)
        for low, keys in lowered.items():
            if len(keys) > 1:
                collisions.append(f"{keys} all normalize to {low!r}")
        assert not collisions, (
            "case-collision among role keys (not exemptible — the routing tag is "
            "ambiguous):\n  " + "\n  ".join(collisions)
        )

    def test_normalized_forms_do_not_shadow_a_different_existing_key(self):
        # The precise Ruling 8 shape: `X` normalizes to `x`, and if `x` were a
        # *different* key, `to:X` would silently reach x's inbox or none at all.
        roles = load_roles()
        for key in roles:
            low = key.lower()
            assert not (low in roles and low != key), (
                f"{key!r} normalizes to {low!r}, which is a separate role key — "
                f"`to:{key}` is undeliverable-or-misdelivered (Ruling 8)"
            )


# ── meta: the checker must detect drift, not merely pass ────────────────────

class TestCheckerIsNotVacuous:
    def test_clean_vocabulary_is_sound(self):
        assert canonical_violations({"alpha": {}, "beta-ii": {}}, {}) == []

    def test_new_capitalized_key_is_detected(self):
        problems = canonical_violations({"DBA": {}, "Engineer": {}}, {"DBA": "cited"})
        assert any("Engineer" in p for p in problems), problems

    def test_case_collision_is_detected_even_when_exempted(self):
        problems = canonical_violations(
            {"DBA": {}, "dba": {}}, {"DBA": "cited"}
        )
        assert any("case-collision" in p for p in problems), problems

    def test_stale_exemption_is_detected(self):
        problems = canonical_violations({"alpha": {}}, {"Ghost": "cited"})
        assert any("stale exemption" in p and "Ghost" in p for p in problems), problems

    def test_uncited_exemption_is_detected(self):
        problems = canonical_violations({"Rover": {}}, {"Rover": "  "})
        assert any("no citation" in p for p in problems), problems
