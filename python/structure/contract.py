"""Canonical Structure v0.1 contract and fingerprint helpers (Structure S1).

Structure is the deterministic structural-observation layer: it reads source
artifacts and emits bounded, replayable structural facts with full provenance
(AST-parsing thread 2ecc4700; architect ruling record 143b0e04 — conditional
concurrence, producer-not-authority boundary approved).

The architect's REQUIRED identity correction is implemented here as two
identities, not one:

- ``source_fact_id`` — canonical source identity + content hash + node path +
  fact kind. Identifies the *inspected source location*. Parser-independent:
  stable across parsers and grammar revisions.
- ``observation_id`` — source_fact_id + parser identity/version + grammar
  revision + canonical payload + read-set fingerprint. Identifies *what a
  particular parser run claimed*. A changed grammar revision creates a new
  observation population (acceptance-gate property, pinned by tests).

Parse honesty: observations carry ``parse_status`` (complete | partial |
unsupported | failed); partial/unsupported/failed parses must carry
diagnostics. Absence of a fact must never become an assertion that the
construct does not exist. Structure is a reader, never an authority: facts
never grant governed identity, never self-authorize relations, and never
invent free-form predicates (references/consumes/projects) — unresolved
referential shapes become explicit unresolved candidates, and governed
mapping belongs to Resolution/Aspects through the optional relation-mapping
slot.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

STRUCTURE_CONTRACT_REVISION = "structure-v0.1"
STRUCTURE_CONTRACT_FINGERPRINT_VERSION = 1
STRUCTURE_SOURCE_FACT_ID_VERSION = 1
STRUCTURE_OBSERVATION_ID_VERSION = 1
STRUCTURE_CANDIDATE_ID_VERSION = 1

SOURCE_KINDS = [
    "jvm_source",
    "python_source",
    "seed_sql",
    "solscript",
    "sql_ddl",
    "sql_migration",
    "typespec",
    "unknown",
]

FACT_KINDS = [
    "column",
    "enum_type",
    "foreign_key",
    "index",
    "named_constraint",
    "operation",
    "seed_value",
    "table",
    "unsupported_syntax",
]

PARSE_STATUSES = ["complete", "failed", "partial", "unsupported"]

CANDIDATE_STATUSES = ["ambiguous", "resolved", "unmapped", "unsupported"]

RELATION_MAPPING_STATUSES = ["mapped", "unmapped"]

FINDING_CODES = [
    "authority_escalation_denied",
    "grammar_drift",
    "identity_collision",
    "missing_anchor",
    "parser_drift",
    "stale_source_revision",
    "unsupported_syntax",
]

AUTHORITY_STATUS = "non_authoritative"

_READ_SET_FINGERPRINT_ALGORITHM = "sha256-canonical-json-v1"

# Expression-envelope compatibility terms. The envelope fields we share are
# exactly these; bridging fields (asset_id, recorded_at) are deliberately
# absent and must not be smuggled in here — bridging is explicit (S4).
_EXPRESSION_ENVELOPE = {
    "shared_fields": ["revision", "content_hash", "source_uri"],
    "field_semantics": {
        "content_hash": "sha256 hex digest of the exact bytes parsed",
        "revision": "source revision identifier (git rev, migration stamp)",
        "source_uri": "canonical URI of the parsed artifact",
    },
    "implicit_taxonomy_growth": False,
    "bridging_fields": [],
}

# Authority boundary terms — the architect ruling restated at contract level
# (record 143b0e04): producer, not authority; the state boundary is explicit.
_AUTHORITY_BOUNDARY = {
    "structure_is": "reader",
    "grants_governed_identity": False,
    "self_authorizes_relations": False,
    "governed_identity_owner": "aspects",
    "relation_authority_owner": "resolution-aspects",
    "authority_status": AUTHORITY_STATUS,
    "escalation_finding_code": "authority_escalation_denied",
    "invents_predicates": False,
    "invented_predicates": [],
    "unresolved_shapes_become": "explicit_unresolved_candidate",
    "absence_never_asserts_nonexistence": True,
    "state_boundary": [
        "structural_fact",
        "relation_candidate",
        "evaluated_relation",
        "admitted_relation",
    ],
}

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def contract_manifest() -> dict[str, Any]:
    """Return the language-neutral Structure contract manifest."""
    return {
        "contract_revision": STRUCTURE_CONTRACT_REVISION,
        "fingerprint_version": STRUCTURE_CONTRACT_FINGERPRINT_VERSION,
        "owner": "structure",
        "authority_status": AUTHORITY_STATUS,
        "source_kinds": SOURCE_KINDS,
        "fact_kinds": FACT_KINDS,
        "parse_statuses": PARSE_STATUSES,
        "relation_mapping_statuses": RELATION_MAPPING_STATUSES,
        "candidate_statuses": CANDIDATE_STATUSES,
        "finding_codes": FINDING_CODES,
        "source_fact_id_version": STRUCTURE_SOURCE_FACT_ID_VERSION,
        "observation_id_version": STRUCTURE_OBSERVATION_ID_VERSION,
        "read_set_fingerprint_algorithm": _READ_SET_FINGERPRINT_ALGORITHM,
        "expression_envelope": _EXPRESSION_ENVELOPE,
        "authority_boundary": _AUTHORITY_BOUNDARY,
    }


def contract_fingerprint() -> str:
    """Fingerprint the semantic manifest, not source formatting or file paths."""
    return _digest(contract_manifest())


def read_set_fingerprint(read_set: list[dict[str, Any]]) -> str:
    """Fingerprint a run's read set under read-set-fingerprint v1.

    Canonical JSON over the entries sorted by (source_uri, revision). Replay
    of the same run over the same inputs must reproduce this value byte for
    byte; a mismatch is a replay failure, not a new fact.
    """
    normalized = [
        {
            "content_hash": entry.get("content_hash", ""),
            "revision": entry.get("revision", ""),
            "role": entry.get("role", ""),
            "source_uri": entry.get("source_uri", ""),
        }
        for entry in sorted(
            read_set, key=lambda e: (e.get("source_uri", ""), e.get("revision", ""))
        )
    ]
    return _digest(normalized)


def source_fact_id_v1(
    *,
    source_uri: str,
    revision: str,
    content_hash: str,
    node_path: str,
    fact_kind: str,
) -> str:
    """Identity of the inspected source location (fact-id v1).

    Function of the canonical source identity, the parsed bytes (content
    hash), where in the parse tree the fact lives, and the fact kind.
    Deliberately parser-independent: the same location in the same bytes has
    the same source_fact_id under any parser or grammar revision — that is
    what makes changed-grammar reruns a *new observation population over the
    same source facts* rather than orphaned identities.
    """
    for name, value in (
        ("source_uri", source_uri),
        ("revision", revision),
        ("content_hash", content_hash),
        ("node_path", node_path),
        ("fact_kind", fact_kind),
    ):
        if not value:
            raise ValueError(f"source_fact_id_v1: {name} must be non-empty")
    if not _HEX64_RE.match(content_hash):
        raise ValueError("source_fact_id_v1: content_hash must be sha256 hex")
    if fact_kind not in FACT_KINDS:
        raise ValueError(f"source_fact_id_v1: unknown fact_kind {fact_kind!r}")
    return _digest(
        {
            "source_fact_id_version": STRUCTURE_SOURCE_FACT_ID_VERSION,
            "source_uri": source_uri,
            "revision": revision,
            "content_hash": content_hash,
            "node_path": node_path,
            "fact_kind": fact_kind,
        }
    )


def observation_id_v1(
    *,
    source_fact_id: str,
    parser_identity: str,
    parser_revision: str,
    grammar_revision: str,
    payload: Any,
    read_set_fingerprint: str,
) -> str:
    """Identity of what one parser run claimed (observation-id v1).

    Function of the source fact identity, the pinned parser/grammar
    revisions, the canonical payload, and the run's read-set fingerprint.
    Two replays under the same pinned revisions must produce the same
    observation_id; a changed grammar revision must not (new population).
    """
    for name, value in (
        ("source_fact_id", source_fact_id),
        ("parser_identity", parser_identity),
        ("parser_revision", parser_revision),
        ("grammar_revision", grammar_revision),
        ("read_set_fingerprint", read_set_fingerprint),
    ):
        if not value:
            raise ValueError(f"observation_id_v1: {name} must be non-empty")
    if not _HEX64_RE.match(source_fact_id):
        raise ValueError("observation_id_v1: source_fact_id must be sha256 hex")
    if not _HEX64_RE.match(read_set_fingerprint):
        raise ValueError("observation_id_v1: read_set_fingerprint must be sha256 hex")
    if payload is None:
        raise ValueError("observation_id_v1: payload must not be null")
    return _digest(
        {
            "observation_id_version": STRUCTURE_OBSERVATION_ID_VERSION,
            "source_fact_id": source_fact_id,
            "parser_identity": parser_identity,
            "parser_revision": parser_revision,
            "grammar_revision": grammar_revision,
            "canonical_payload": _canonical(payload),
            "read_set_fingerprint": read_set_fingerprint,
        }
    )


def candidate_id_v1(
    *,
    source_content_hash: str,
    parser_identity: str,
    parser_revision: str,
    grammar_revision: str,
    node_path: str,
    reason: str,
) -> str:
    """Compute an unresolved-candidate identity under fact-id v1.

    Same digest family as observation-id — candidates are first-class output
    and get stable identities, so a rerun that still cannot map the same
    shape re-emits the *same* candidate rather than a new one.
    """
    for name, value in (
        ("source_content_hash", source_content_hash),
        ("parser_identity", parser_identity),
        ("parser_revision", parser_revision),
        ("grammar_revision", grammar_revision),
        ("node_path", node_path),
        ("reason", reason),
    ):
        if not value:
            raise ValueError(f"candidate_id_v1: {name} must be non-empty")
    if not _HEX64_RE.match(source_content_hash):
        raise ValueError("candidate_id_v1: source_content_hash must be sha256 hex")
    if reason not in CANDIDATE_STATUSES:
        raise ValueError(f"candidate_id_v1: unknown reason {reason!r}")
    return _digest(
        {
            "candidate_id_version": STRUCTURE_CANDIDATE_ID_VERSION,
            "source_content_hash": source_content_hash,
            "parser_identity": parser_identity,
            "parser_revision": parser_revision,
            "grammar_revision": grammar_revision,
            "node_path": node_path,
            "reason": reason,
        }
    )


def validate_structural_observation(observation: dict[str, Any]) -> list[str]:
    """Conformance check for one structural observation.

    Mirrors the StructuralObservation TypeSpec model. Observations must be
    non-authoritative (authority escalation is the founding defect this layer
    exists to prevent), carry full provenance and the two-identity scheme,
    state their parse status honestly (non-complete parses must carry
    diagnostics — absence never asserts non-existence), and declare a
    relation mapping only in the governed shape (unmapped, or mapped with
    relation id + evidence refs — mapping itself is Resolution/Aspects work).
    """
    errors: list[str] = []
    for field in (
        "observation_id",
        "source_fact_id",
        "source",
        "parser",
        "anchor",
        "fact_kind",
        "payload",
        "parse_status",
        "read_set_fingerprint",
        "authority_status",
    ):
        if field not in observation or observation.get(field) in (None, ""):
            errors.append(f"structural observation missing required field {field!r}")
    if errors:
        return errors

    if observation["fact_kind"] not in FACT_KINDS:
        errors.append(f"unknown fact_kind {observation['fact_kind']!r}")

    authority = observation.get("authority_status")
    if authority != AUTHORITY_STATUS:
        errors.append(
            f"authority_status must be {AUTHORITY_STATUS!r}, got {authority!r}; "
            "Structure never grants governed identity"
        )

    if not _HEX64_RE.match(observation["observation_id"]):
        errors.append("observation_id must be sha256 hex")
    if not _HEX64_RE.match(observation["source_fact_id"]):
        errors.append("source_fact_id must be sha256 hex")
    if not _HEX64_RE.match(observation["read_set_fingerprint"]):
        errors.append("read_set_fingerprint must be sha256 hex")

    source = observation.get("source") or {}
    for field in ("source_uri", "revision", "content_hash", "language", "source_kind"):
        if not source.get(field):
            errors.append(f"structural observation source missing required field {field!r}")
    if source.get("source_kind") not in SOURCE_KINDS:
        errors.append(f"unknown source_kind {source.get('source_kind')!r}")
    if source.get("content_hash") and not _HEX64_RE.match(source["content_hash"]):
        errors.append("source content_hash must be sha256 hex")

    parser = observation.get("parser") or {}
    for field in ("parser_identity", "parser_revision", "grammar_revision"):
        if not parser.get(field):
            errors.append(f"structural observation parser missing required field {field!r}")

    anchor = observation.get("anchor") or {}
    if not anchor.get("node_path"):
        errors.append("structural observation anchor missing node_path")

    parse_status = observation.get("parse_status")
    if parse_status not in PARSE_STATUSES:
        errors.append(f"unknown parse_status {parse_status!r}")
    diagnostics = observation.get("diagnostics") or []
    if parse_status in ("partial", "unsupported", "failed") and not diagnostics:
        errors.append(
            f"parse_status {parse_status!r} requires diagnostics; a partial or "
            "failed parse must never silently assert the construct does not exist"
        )

    mapping = observation.get("relation_mapping")
    if mapping is not None:
        status = mapping.get("status")
        if status not in RELATION_MAPPING_STATUSES:
            errors.append(f"unknown relation_mapping status {status!r}")
        elif status == "mapped":
            if not mapping.get("governed_relation_id"):
                errors.append(
                    "relation_mapping status 'mapped' requires governed_relation_id"
                )
            if not mapping.get("evidence_refs"):
                errors.append(
                    "relation_mapping status 'mapped' requires evidence_refs"
                )
    return errors
