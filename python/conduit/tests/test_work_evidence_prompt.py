"""The builder prompt must carry the work-evidence contract it is graded on."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import executor_cloud
from execution_worker import WORK_EVIDENCE_MARKER, _parse_work_evidence


IDS = {
    "work_request_id": "wr-8261665-1789000000-abc12345",
    "attempt_id": "5cd90c83-0eb5-4274-9ac9-e352419a81cb",
    "session_id": "worker-conduit-worker-20261101-143223-deadbeef",
}


def _req(role="builder", with_ids=True):
    metadata = {"role": role, "harness": "opencode", "model": "opencode/x"}
    if with_ids:
        metadata.update(IDS)
    req = {
        "id": IDS["work_request_id"],
        "path": "/home/codex/dev",
        "metadata": metadata,
        "intent": {"problem_statement": "decompose W-C", "desired_outcome": "docs"},
        "decomposition": {"strategy": "direct", "steps": []},
        "requirements": {"functional": []},
        "constraints": {},
        "success_criteria": {"completion_conditions": []},
        "artifacts": {
            "produced_files": [
                {"path": "docs/wc-consumer-inventory.md"},
                {"path": "docs/wc-writer-fencing.md"},
            ]
        },
        "lineage": {},
    }
    if with_ids:
        req["_plan_id"] = "8261665"
    return req


def test_builder_prompt_carries_correlation_ids_and_marker():
    prompt = executor_cloud._build_opencode_prompt(_req(), "/home/codex/dev")

    assert "## Work Evidence Protocol" in prompt
    for value in IDS.values():
        assert value in prompt
    assert "8261665" in prompt
    assert "/api/agent-records" in prompt
    assert WORK_EVIDENCE_MARKER.strip() in prompt
    # The promised marker must be one the validator can actually parse.
    marker_line = next(
        line.strip()
        for line in prompt.splitlines()
        if line.strip().startswith(WORK_EVIDENCE_MARKER)
    )
    parsed = _parse_work_evidence(marker_line)
    assert parsed, "marker emitted in the prompt must be parseable"
    for field, value in IDS.items():
        assert parsed[field] == value


def test_prompt_marker_declares_every_target_file():
    req = _req()
    prompt = executor_cloud._build_opencode_prompt(req, "/home/codex/dev")

    marker_line = next(
        line.strip()
        for line in prompt.splitlines()
        if line.strip().startswith(WORK_EVIDENCE_MARKER)
    )
    parsed = _parse_work_evidence(marker_line)
    assert [item["path"] if isinstance(item, dict) else item for item in parsed["artifacts"]] == [
        item["path"] for item in req["artifacts"]["produced_files"]
    ]


def test_reviewer_prompt_has_no_builder_evidence_block():
    prompt = executor_cloud._build_opencode_prompt(_req(role="reviewer"), "/home/codex/dev")
    assert "Work Evidence Protocol" not in prompt


def test_block_omitted_when_ids_are_unavailable():
    """Never hand the agent a contract it cannot satisfy."""
    prompt = executor_cloud._build_opencode_prompt(_req(with_ids=False), "/home/codex/dev")
    assert "Work Evidence Protocol" not in prompt


def test_evidence_record_payload_is_valid_json():
    prompt = executor_cloud._build_opencode_prompt(_req(), "/home/codex/dev")
    payload_line = next(line for line in prompt.splitlines() if "-d '{" in line)
    payload = json.loads(payload_line.split("-d '", 1)[1].rsplit("'", 1)[0])
    assert payload["recordType"] == "report"
    assert payload["role"] == "builder"
    assert payload["planRef"] == "8261665"
    assert payload["metadata"] == {
        "plan_ref": "8261665",
        **IDS,
    }
