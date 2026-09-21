"""Bounded deterministic corpus support for Expression E2.

The corpus is synthetic/redacted architecture language. It is deliberately
small, committed, and reproducible; it is not a live transcript or authority
source.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any

from .contract import canonicalize_bundle
from .pipeline import build_expression_bundle

CORPUS_REVISION = "expression-e2-corpus-v0.1"
_REDACTION_PATTERNS = (
    (re.compile(r"(?i)api[_ -]?key\s*[:=]\s*[^\s,;]+"), "api_key=[REDACTED]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]+"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)password\s*[:=]\s*[^\s,;]+"), "password=[REDACTED]"),
)
_PROMPT_INJECTION_MARKERS = re.compile(
    r"(?i)\b(ignore\s+(?:all\s+)?previous\s+instructions|system\s+prompt|developer\s+message)\b"
)


def redact_text(value: str) -> tuple[str, list[str]]:
    """Remove control bytes/secrets and label prompt-injection language."""
    text = value.replace("\x00", "")
    redactions: list[str] = []
    for pattern, replacement in _REDACTION_PATTERNS:
        text, count = pattern.subn(replacement, text)
        if count:
            redactions.append("secret")
    text, count = _PROMPT_INJECTION_MARKERS.subn("[REDACTED_PROMPT_INJECTION]", text)
    if count:
        redactions.append("prompt_injection")
    return text, sorted(set(redactions))


def sanitize_transcript(transcript: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return a sanitized copy and deterministic redaction evidence."""
    result = deepcopy(transcript)
    evidence: list[dict[str, Any]] = []
    for index, turn in enumerate(result.get("turns", [])):
        original = str(turn.get("content", ""))
        sanitized, kinds = redact_text(original)
        turn["content"] = sanitized
        if kinds:
            evidence.append({"turn_index": index, "redactions": kinds})
    return result, evidence


def build_corpus(raw_corpus: dict[str, Any]) -> dict[str, Any]:
    """Build canonical review bundles from bounded redacted transcripts."""
    transcripts = raw_corpus.get("transcripts", [])
    sanitized_transcripts = []
    redactions = []
    bundles = []
    for transcript in transcripts:
        sanitized, evidence = sanitize_transcript(transcript)
        sanitized_transcripts.append(sanitized)
        redactions.append({"transcript_id": sanitized.get("transcript_id"), "evidence": evidence})
        bundles.append(canonicalize_bundle(build_expression_bundle(sanitized)))
    artifact = {
        "corpus_revision": CORPUS_REVISION,
        "source_kind": "synthetic_redacted_architecture_fixture",
        "transcript_count": len(sanitized_transcripts),
        "redaction_policy": {
            "control_bytes": "removed",
            "secrets": "replaced",
            "prompt_injection": "labeled_and_replaced",
        },
        "transcripts": sanitized_transcripts,
        "redactions": redactions,
        "bundles": bundles,
        "authority_status": "non_authoritative",
        "writes_performed": False,
    }
    artifact["artifact_fingerprint"] = hashlib.sha256(
        canonical_json(artifact).encode("utf-8")
    ).hexdigest()
    return artifact


def canonical_json(value: Any) -> str:
    """Serialize JSON with stable ordering and a terminal newline."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def artifact_bytes(artifact: dict[str, Any]) -> bytes:
    return canonical_json(artifact).encode("utf-8")
