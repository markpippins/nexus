"""Expression taxonomy expectations without implementation drift."""

from __future__ import annotations

from typing import Any

EXPLICIT_KINDS = {
    "reference",
    "version",
    "speech_act",
}

OBSERVATION_KIND_EXPECTATIONS = {
    "reference": {
        "required_fields": ["observation_id", "kind", "value", "source", "disposition"],
        "disposition_expectation": "unreviewed",
        "authority_expectation": "non_authoritative",
        "interpretation_rule": "explicit text match only; no entity resolution",
    },
    "version": {
        "required_fields": ["observation_id", "kind", "value", "source", "disposition"],
        "disposition_expectation": "unreviewed",
        "authority_expectation": "non_authoritative",
        "interpretation_rule": "version string observed; no release validity asserted",
    },
    "speech_act": {
        "required_fields": ["observation_id", "kind", "value", "verb", "source", "disposition"],
        "disposition_expectation": "unreviewed",
        "authority_expectation": "non_authoritative",
        "interpretation_rule": "decision language observed; no decision authority asserted",
    },
}


def expected_observation_contract(observation: dict[str, Any]) -> list[str]:
    """Return contract violations for an observation relative to current POC expectations."""
    errors: list[str] = []
    kind = observation.get("kind")
    expectation = OBSERVATION_KIND_EXPECTATIONS.get(kind)
    if expectation is None:
        return [f"unsupported observation kind: {kind!r}"]
    for field in expectation["required_fields"]:
        if field not in observation:
            errors.append(f"missing required field {field!r} for kind {kind!r}")
    if observation.get("disposition") != expectation["disposition_expectation"]:
        errors.append(
            f"observation kind {kind!r} must be {expectation['disposition_expectation']!r}, "
            f"got {observation.get('disposition')!r}"
        )
    if observation.get("authority_status") != expectation["authority_expectation"]:
        errors.append(
            f"observation kind {kind!r} must remain {expectation['authority_expectation']!r}"
        )
    disposition = observation.get("disposition")
    if disposition is not None and disposition != expectation["disposition_expectation"]:
        errors.append(
            f"observation kind {kind!r} must be {expectation['disposition_expectation']!r}, "
            f"got {disposition!r}"
        )
    authority = observation.get("authority_status")
    if authority is not None and authority != expectation["authority_expectation"]:
        errors.append(
            f"observation kind {kind!r} must remain {expectation['authority_expectation']!r}, "
            f"got {authority!r}"
        )
    return errors
