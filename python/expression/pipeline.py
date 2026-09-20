"""Deterministic, non-authoritative Expression source pipeline.

This POC deliberately performs only reproducible extraction of explicit
identifiers, URLs, and decision-language observations. It does not resolve
identities, infer truth, mutate the graph, or grant authority.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .boundary import expression_boundary
from .taxonomy import expected_observation_contract

_IDENTIFIER_RE = re.compile(
    r"(?P<pr>\bPR\s*#\d+\b)|"
    r"(?P<migration>\bV\d{3,4}\b)|"
    r"(?P<url>https?://[^\s)]+)|"
    r"(?P<path>(?:\.?/|nexus/)[A-Za-z0-9_./§-]+)"
)
_VERSION_RE = re.compile(r"\bV\d{3,4}(?!\d)")
_DECISION_RE = re.compile(
    r"\b(?P<verb>ratif(?:y|ied|ication)|approv(?:e|ed|al)|reject(?:ed|ion)|"
    r"sign[- ]?off|decision|ruling|accept(?:ed|ance)|refus(?:e|al))\b",
    re.IGNORECASE,
)


def _text(turn: dict[str, Any]) -> str:
    value = turn.get("content", "")
    if isinstance(value, list):
        value = "\n".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in value
        )
    return str(value).replace("\x00", "")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _transcript_id(transcript: dict[str, Any]) -> str:
    return str(
        transcript.get("transcript_id")
        or transcript.get("id")
        or transcript.get("source_file")
        or "expression-unknown-transcript"
    )


def source_fingerprint(transcript: dict[str, Any]) -> str:
    """Return a stable fingerprint over ordered turn roles and text."""
    material = "\n".join(
        f"{index}\t{turn.get('role', 'unknown')}\t{_text(turn)}"
        for index, turn in enumerate(transcript.get("turns", []))
    )
    return _digest(material)


def segment_transcript(
    transcript: dict[str, Any], *, max_chars: int = 12_000
) -> list[dict[str, Any]]:
    """Create deterministic source segments from ordered transcript turns.

    A new segment begins at a role change or before a turn that would exceed
    ``max_chars``. Segment identity includes the transcript identity, ordinal,
    and exact content digest, so reprocessing is idempotent and edits produce
    new identities rather than mutating history.
    """
    transcript_id = _transcript_id(transcript)
    segments: list[dict[str, Any]] = []
    current: list[tuple[int, dict[str, Any], str]] = []
    current_role: str | None = None
    current_chars = 0

    def flush() -> None:
        nonlocal current, current_role, current_chars
        if not current:
            return
        ordinal = len(segments)
        text = "\n".join(value for _, _, value in current)
        segment_id = _digest(f"{transcript_id}\n{ordinal}\n{text}")[:32]
        first_index, first_turn, _ = current[0]
        last_index, _, _ = current[-1]
        segments.append(
            {
                "segment_id": segment_id,
                "transcript_id": transcript_id,
                "ordinal": ordinal,
                "start_turn": first_index,
                "end_turn": last_index,
                "turn_count": len(current),
                "role": first_turn.get("role", "unknown"),
                "text": text,
                "text_hash": _digest(text),
                "boundary_reason": "role_change_or_size",
            }
        )
        current = []
        current_role = None
        current_chars = 0

    for index, turn in enumerate(transcript.get("turns", [])):
        text = _text(turn)
        role = str(turn.get("role", "unknown"))
        role_changed = current_role is not None and role != current_role
        too_large = bool(current) and current_chars + len(text) + 1 > max_chars
        if role_changed or too_large:
            flush()
        current.append((index, turn, text))
        current_role = role
        current_chars += len(text) + 1
    flush()
    return segments


def _observation_id(segment_id: str, kind: str, value: str) -> str:
    return _digest(f"{segment_id}\n{kind}\n{value}")[:32]


def extract_explicit_observations(
    transcript: dict[str, Any], *, extractor_revision: str = "expression-explicit-v0.1"
) -> list[dict[str, Any]]:
    """Extract source-linked explicit observations without semantic inference."""
    fingerprint = source_fingerprint(transcript)
    segments = segment_transcript(transcript)
    observations: list[dict[str, Any]] = []
    for segment in segments:
        text = segment["text"]
        source = {
            "segment_id": segment["segment_id"],
            "transcript_id": segment["transcript_id"],
            "text_hash": segment["text_hash"],
            "start_turn": segment["start_turn"],
            "end_turn": segment["end_turn"],
        }
        for match in _IDENTIFIER_RE.finditer(text):
            value = match.group(0)
            observations.append(
                {
                    "observation_id": _observation_id(segment["segment_id"], "reference", value),
                    "kind": "reference",
                    "value": value,
                    "reference_kind": match.lastgroup,
                    "source": source,
                    "extractor_revision": extractor_revision,
                    "input_fingerprint": fingerprint,
                    "disposition": "unreviewed",
                    "authority_status": "non_authoritative",
                }
            )
        for match in _VERSION_RE.finditer(text):
            value = match.group(0)
            observations.append(
                {
                    "observation_id": _observation_id(segment["segment_id"], "version", value),
                    "kind": "version",
                    "value": value,
                    "reference_kind": "migration_version",
                    "source": source,
                    "extractor_revision": extractor_revision,
                    "input_fingerprint": fingerprint,
                    "disposition": "unreviewed",
                    "authority_status": "non_authoritative",
                }
            )
        for match in _DECISION_RE.finditer(text):
            value = match.group(0)
            observations.append(
                {
                    "observation_id": _observation_id(segment["segment_id"], "speech_act", value),
                    "kind": "speech_act",
                    "value": value,
                    "verb": match.group("verb").lower(),
                    "source": source,
                    "extractor_revision": extractor_revision,
                    "input_fingerprint": fingerprint,
                    "disposition": "unreviewed",
                    "authority_status": "non_authoritative",
                }
            )
    return sorted(observations, key=lambda item: item["observation_id"])


def validate_observations(
    observations: list[dict[str, Any]], *, taxonomy_revision: str = "expression-taxonomy-v0.1"
) -> list[dict[str, Any]]:
    """Return contract violations without mutating the extracted observations."""
    errors: list[dict[str, Any]] = []
    for observation in observations:
        for error in expected_observation_contract(observation):
            errors.append(
                {
                    "observation_id": observation.get("observation_id", "unknown"),
                    "error": error,
                    "taxonomy_revision": taxonomy_revision,
                }
            )
    return sorted(errors, key=lambda item: item["observation_id"])


def _candidate_key(value: str) -> str:
    return f"candidate:{_digest(value.strip().lower())[:24]}"


def build_candidate_links(
    observations: list[dict[str, Any]],
    candidates: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Create conservative candidate links using an explicit alias catalog.

    No catalog means no identity claim. One exact alias produces a proposed
    link, not confirmation. Multiple matches become ambiguous links. This
    keeps identity resolution reviewable and prevents name-only merges.
    """
    catalog = candidates or {}
    aliases: dict[str, list[str]] = {}
    for candidate_id, values in catalog.items():
        for value in [candidate_id, *values]:
            aliases.setdefault(value.strip().lower(), []).append(candidate_id)

    links: list[dict[str, Any]] = []
    for observation in observations:
        if observation.get("kind") != "reference":
            continue
        value = str(observation["value"])
        matches = sorted(set(aliases.get(value.strip().lower(), [])))
        if len(matches) == 1:
            links.append(
                {
                    "mention_id": observation["observation_id"],
                    "candidate_id": matches[0],
                    "method": "exact_alias",
                    "status": "proposed",
                    "evidence": [value],
                }
            )
        elif len(matches) > 1:
            links.append(
                {
                    "mention_id": observation["observation_id"],
                    "candidate_id": _candidate_key(value),
                    "method": "exact_alias_collision",
                    "status": "ambiguous",
                    "evidence": matches,
                }
            )
    return sorted(links, key=lambda item: (item["mention_id"], item["candidate_id"]))


def build_proposition_candidates(
    observations: list[dict[str, Any]],
    links: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Turn explicit speech-act observations into non-authoritative candidates.

    These propositions say only that a source segment contains decision
    language associated with a reference. They do not assert that a decision
    was valid, authorized, or actually performed.
    """
    links_by_mention = {link["mention_id"]: link for link in links or []}
    references_by_segment: dict[str, list[dict[str, Any]]] = {}
    for observation in observations:
        if observation.get("kind") == "reference":
            segment_id = observation["source"]["segment_id"]
            references_by_segment.setdefault(segment_id, []).append(observation)

    propositions: list[dict[str, Any]] = []
    for observation in observations:
        if observation.get("kind") != "speech_act":
            continue
        segment_id = observation["source"]["segment_id"]
        references = references_by_segment.get(segment_id, [])
        reference = references[0] if references else None
        object_ref = (
            links_by_mention.get(reference["observation_id"], {}).get("candidate_id")
            if reference
            else None
        ) or (f"observation:{reference['observation_id']}" if reference else None)
        propositions.append(
            {
                "proposition_id": _digest(
                    f"{observation['observation_id']}\nmentions_decision_language\n{object_ref or ''}"
                )[:32],
                "subject_ref": f"segment:{segment_id}",
                "predicate": "mentions_decision_language",
                "object_ref": object_ref,
                "source_observation_ids": [observation["observation_id"]],
                "modality": "reported",
                "status": "candidate",
                "required_read_set": None,
            }
        )
    return sorted(propositions, key=lambda item: item["proposition_id"])


def build_expression_bundle(
    transcript: dict[str, Any],
    *,
    candidates: dict[str, list[str]] | None = None,
    extractor_revision: str = "expression-explicit-v0.1",
) -> dict[str, Any]:
    """Build a reproducible review bundle without performing any writes."""
    observations = extract_explicit_observations(
        transcript, extractor_revision=extractor_revision
    )
    links = build_candidate_links(observations, candidates)
    propositions = build_proposition_candidates(observations, links)
    return {
        "contract_revision": "expression-v0.1",
        "source_fingerprint": source_fingerprint(transcript),
        "transcript_id": _transcript_id(transcript),
        "segments": segment_transcript(transcript),
        "observations": observations,
        "candidate_links": links,
        "proposition_candidates": propositions,
        "authority_status": "non_authoritative",
        "boundary": expression_boundary(),
    }
