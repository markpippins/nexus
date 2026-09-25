"""Structure→Expression relation-candidate bridge (Structure S4).

Converts recognized structural patterns in S2 observations into relation
CANDIDATES over the governed V182 relation vocabulary, per to-do thread
8f52f17e and architect ruling 143b0e04:

- **Governed vocabulary only (cond-3 contract).** A candidate's
  ``relation_type`` is either a name from the pinned V182 vocabulary
  (``resolution.relation_vocabulary``, seeded by ``sql/V182__relation_vocabulary.sql``,
  freeze draft df6b70c4) or an explicit ``__unresolved__`` sentinel with a
  reason. The bridge never invents predicates: out-of-vocabulary or
  ambiguous shapes remain findings/unresolved candidates.

- **Candidates are never admitted relations.** The state ladder is
  structural_fact ≠ relation_candidate ≠ evaluated_relation ≠
  admitted_relation. Bridge output carries ``state: "candidate"`` and no
  governed identity: subjects/objects are source names (strings as they
  appear in DDL), never Resolution UUIDs. Evaluation/admission authority
  stays with Resolution/Aspects (Analyst owns relation mapping per the S4
  thread; the bridge only recognizes shapes).

- **Recognized → ambiguous → unmapped.** Each candidate records its
  resolution as ``resolved`` (shape matched a governed pattern exactly),
  ``ambiguous`` (multiple governed types plausibly match), or ``unmapped``
  (no governed type matches). Ambiguity is first-class output, never
  silently resolved. All candidates retain their AST anchor.

- **No DB access.** The vocabulary is a pinned fixture
  (``fixtures/relation_vocabulary_v182.json``) carrying its own provenance
  (source migration + freeze draft + names fingerprint). Re-basing on a
  vocabulary change means regenerating the pin — an explicit, reviewable
  contract revision, not a silent runtime lookup.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any

try:
    from . import contract as sc  # package context
except ImportError:  # pragma: no cover - direct-run context
    import contract as sc  # type: ignore[no-redef]

UNRESOLVED = "__unresolved__"
STATE_CANDIDATE = "candidate"

_BRIDGE_REVISION = "structure-bridge-v0.1.0"
_VOCAB_FIXTURE = (
    pathlib.Path(__file__).resolve().parent / "fixtures" / "relation_vocabulary_v182.json"
)

# ---------------------------------------------------------------------------
# Vocabulary pin


def _load_vocabulary() -> dict[str, Any]:
    data = json.loads(_VOCAB_FIXTURE.read_text())
    names = data["names"]
    fp = hashlib.sha256("\n".join(sorted(names)).encode()).hexdigest()
    if fp != data["names_fingerprint"]:
        raise RuntimeError("relation vocabulary fixture fingerprint mismatch")
    return data


VOCABULARY = _load_vocabulary()
GOVERNED_NAMES: frozenset[str] = frozenset(VOCABULARY["names"])
VOCABULARY_REVISION: str = VOCABULARY["vocabulary_revision"]
NAMES_FINGERPRINT: str = VOCABULARY["names_fingerprint"]

# ---------------------------------------------------------------------------
# Pattern recognition (bounded, explicit, documented)


def _subject_object_from_fk(obs: dict[str, Any]) -> tuple[str, str] | None:
    """FK observation -> (subject table, referenced table)."""
    p = obs.get("payload") or {}
    table, ref = p.get("table"), p.get("references_table")
    if table and ref:
        return str(table), str(ref)
    return None


def _subject_object_from_seed(obs: dict[str, Any]) -> tuple[str, str] | None:
    """Seed observation -> (subject table, row identity as inserted).

    The object is the *inserted row's rendered identity* — a source-anchored
    string, never a resolved domain id.
    """
    p = obs.get("payload") or {}
    table = p.get("table")
    values = p.get("values")
    if table and isinstance(values, list):
        rendered = ", ".join(
            v if isinstance(v, str) else json.dumps(v, sort_keys=True)
            for v in values
        )
        return str(table), rendered[:120]
    return None


def _subject_object_from_operation(obs: dict[str, Any]) -> tuple[str, str] | None:
    """Derivation-shaped operations (INSERT ... SELECT) -> (target, source)."""
    p = obs.get("payload") or {}
    if p.get("action") == "insert_select" and p.get("table") and p.get("source"):
        return str(p["table"]), str(p["source"])[:120]
    return None


# subject/object extractor -> governed vocabulary types that plausibly match
# the shape. Every proposed type MUST come from this table and be checked
# against the governed set; the bridge proposes, it never decides.
#  - FK table->table: 'references' is NOT in V182; depends_on and basis_of
#    are both governed and both plausible -> structurally ambiguous, so the
#    mapping is deferred (finding), never auto-picked.
#  - seed row -> its table: member_of is the single governed reading.
#  - INSERT...SELECT target<-source: derives_from is the single governed
#    reading of the row-level derivation.
_PATTERNS: list[tuple[Any, list[str]]] = [
    (_subject_object_from_fk, ["depends_on", "basis_of"]),
    (_subject_object_from_seed, ["member_of"]),
    (_subject_object_from_operation, ["derives_from"]),
]


def _match(shape: Any) -> tuple[list[str], str | None, str]:
    """Match a structural shape to governed vocabulary types.

    Returns (governed_matches, reason, resolution). Exactly one governed
    match => 'resolved' (the one recognized case). Multiple governed
    matches => 'ambiguous' (mapping deferred — candidates must not be
    minted from ambiguity). Zero => 'unmapped' (out of vocabulary or
    unrecognized shape).
    """
    for extractor, proposed in _PATTERNS:
        pair = extractor(shape)
        if pair is None:
            continue
        governed = [t for t in proposed if t in GOVERNED_NAMES]
        missing = [t for t in proposed if t not in GOVERNED_NAMES]
        if not governed:
            return [], (
                f"proposed type(s) {sorted(proposed)} not in governed "
                f"vocabulary {VOCABULARY_REVISION}"
            ), "unmapped"
        note = (
            f" (proposed-but-ungoverned dropped: {sorted(missing)})"
            if missing else ""
        )
        if len(governed) == 1:
            return governed, None, "resolved"
        return governed, (
            f"shape plausibly matches multiple governed types {governed}; "
            "mapping deferred to Resolution/Aspects" + note
        ), "ambiguous"
    return [], "shape not recognized by any bounded pattern", "unmapped"


def derive_candidates(
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Derive relation candidates from structural observations.

    Per the S4 spec: only recognized structural patterns (EXACTLY ONE
    governed type matching the shape) become candidates. Ambiguous shapes
    (multiple governed types plausibly match) and out-of-vocabulary or
    unrecognized shapes remain explicit findings/unresolved — they never
    mint candidates and never invent predicates.

    Output contract:
      - ``candidates``: governed-referencing relation candidates
        (relation_type in V182, state 'candidate', names not UUIDs).
      - ``unresolved``: explicit findings with reasons — ambiguous
        (plausible governed types listed), out-of-vocabulary, or
        unrecognized shapes.
    """
    candidates: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for obs in observations:
        if obs.get("fact_kind") not in ("foreign_key", "seed_value", "operation"):
            continue
        if obs.get("parse_status") not in ("complete", "partial"):
            # unsupported observations make no claims about internals
            continue
        subject = obj = None
        for extractor, _ in _PATTERNS:
            pair = extractor(obs)
            if pair:
                subject, obj = pair
                break
        if subject is None:
            continue
        base = {
            "candidate_id": _candidate_id(obs),
            "state": STATE_CANDIDATE,
            "relation_type": None,
            "subject": {"kind": "source_name", "value": subject},
            "object": {"kind": "source_name", "value": obj},
            "evidence_refs": [obs.get("observation_id")],
            "anchor": obs.get("anchor"),
            "source": obs.get("source"),
            "vocabulary_revision": VOCABULARY_REVISION,
            "authority_status": sc.AUTHORITY_STATUS,
            "bridge_revision": _BRIDGE_REVISION,
        }
        governed, reason, resolution = _match(obs)
        if len(governed) == 1:
            candidates.append({
                **base,
                "relation_type": governed[0],
                "resolution": resolution,
            })
            continue
        unresolved.append({
            **base,
            "relation_type": UNRESOLVED,
            "resolution": resolution,  # 'ambiguous' or 'unmapped'
            "reason": reason,
            **({"plausible_governed_types": governed} if governed else {}),
        })

    return {
        "bridge_revision": _BRIDGE_REVISION,
        "vocabulary_revision": VOCABULARY_REVISION,
        "vocabulary_fingerprint": NAMES_FINGERPRINT,
        "candidates": candidates,
        "unresolved": unresolved,
        "authority_status": sc.AUTHORITY_STATUS,
        "state_ladder": [
            "structural_fact",
            "relation_candidate",
            "evaluated_relation",
            "admitted_relation",
        ],
        "admission_authority": "resolution-aspects",
        "disclaimer": (
            "candidates are source-anchored recognition output; evaluation "
            "and admission into the knowledge graph are governed by the "
            "Resolution/Aspects candidate → evaluation → admission path"
        ),
    }


def _candidate_id(obs: dict[str, Any]) -> str:
    """Deterministic candidate identity anchored to its evidence observation."""
    return hashlib.sha256(
        (
            "structure-bridge-v1:"
            + str(obs.get("observation_id"))
            + ":"
            + str(obs.get("fact_kind"))
        ).encode()
    ).hexdigest()


# ---------------------------------------------------------------------------
# Guardrails


def validate_candidates(bridge: dict[str, Any]) -> list[str]:
    """Conformance check on bridge output (the no-admission guarantees).

    - every candidate's relation_type is a governed V182 name;
    - no governed identity anywhere (UUIDs, asset ids, governed_* fields);
    - subjects/objects are source_name kind;
    - state is always 'candidate' with admission authority external;
    - evidence_refs non-empty and anchored.
    """
    errors: list[str] = []
    import re as _re
    uuid_re = _re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", _re.I
    )
    for cand in bridge.get("candidates", []):
        rt = cand.get("relation_type")
        if isinstance(rt, list):
            if not rt or any(t not in GOVERNED_NAMES for t in rt):
                errors.append(f"ambiguous candidate {cand.get('candidate_id','?')[:12]}: "
                              f"non-governed type in {rt}")
        elif rt not in GOVERNED_NAMES:
            errors.append(f"candidate {cand.get('candidate_id','?')[:12]}: "
                          f"relation_type {rt!r} is not governed")
        if cand.get("state") != STATE_CANDIDATE:
            errors.append(f"candidate {cand.get('candidate_id','?')[:12]}: "
                          f"state must be {STATE_CANDIDATE!r}")
        for side in ("subject", "object"):
            node = cand.get(side) or {}
            if node.get("kind") != "source_name":
                errors.append(f"candidate {cand.get('candidate_id','?')[:12]}: "
                              f"{side} must be source_name kind")
            if uuid_re.match(str(node.get("value", ""))):
                errors.append(f"candidate {cand.get('candidate_id','?')[:12]}: "
                              f"{side} value looks like a UUID — governed identity leak")
        if not cand.get("evidence_refs"):
            errors.append(f"candidate {cand.get('candidate_id','?')[:12]}: "
                          "missing evidence_refs")
        if cand.get("authority_status") != sc.AUTHORITY_STATUS:
            errors.append(f"candidate {cand.get('candidate_id','?')[:12]}: "
                          "authority escalation")
    forbidden = ("governed_tag_id", "governed_id", "resolution_uuid", "asset_id",
                 "concept_id", "proposition_id", "governed_relation_id")
    text = json.dumps(bridge, sort_keys=True)
    for f in forbidden:
        if f'"{f}"' in text:
            errors.append(f"bridge output carries forbidden governed field {f!r}")
    return errors
