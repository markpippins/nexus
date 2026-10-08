"""Test-only inquiry worker; JSON lines on stdin/stdout, no service or DB access.

The retained manifest covers this deliberately narrow fixture, NOT arbitrary
ontology dependency discovery. Semantics stay downstream of Vision's structural
executor. No production worker/registry binding is installed by this proof.
"""
from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from dataclasses import asdict
from enum import Enum
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [
    str(ROOT / "python"),
    str(ROOT / "python/SOLScript"),
    str(ROOT / "python/vision/losm-ir/src"),
]

from expression.test_solscript_adapter import _build_interpreter
from expression.solscript_adapter import INTERPRETER_REVISION
from losm_ir.execution_receipt import ExecutionReceipt

WORK_REQUEST_ID = "7dd334e6-c760-4e4e-8b6e-c3c98eaab471"
EXECUTION_ID = "82651675-6060-4205-a243-ccae4bd9b36f"
EXECUTOR_ID = "test.solscript-inquiry-worker"


def canonical(value):
    # Fixture values are ASCII strings, bools, integers and null; this matches
    # Wind canonicalJson for that domain (not a general cross-language codec).
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value).encode()).hexdigest()


def wire(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [wire(item) for item in value]
    return value


def manifest(interpreter):
    stores = ("propositions", "entities", "concepts", "rules", "expressions",
              "relationships", "representations", "state_transitions",
              "frame_dimensions", "frame_dimension_values", "frame_dimension_meanings")
    return {
        "schema": "test.solscript-fixture-read-set.v1",
        "source_revision": "fixture-v1",
        "context": {},
        "required_frame_dimensions": [],
        "semantic_type_required_dimensions": {},
        "stores": {
            name: {key: wire(asdict(item)) for key, item in getattr(interpreter, name).items()}
            for name in stores
        },
    }


class InquiryWorker:
    def __init__(self):
        self.calls = 0
        self.result = None
        self.pinned = None

    def prepare(self, status):
        if self.pinned is not None:
            raise ValueError("one snapshot per worker")
        if status not in {"open", "closed"}:
            raise ValueError("fixture status must be open or closed")
        source, prop = _build_interpreter()
        # The existing fixture generates expression UUIDs. Pin stable IDs on
        # this test instance only so fresh-worker replay is reproducible.
        expression = prop.assertions[0].expression
        for node, name in zip([expression, *expression.operands], ["eq", "status", "open"]):
            node.id = "expression-fixture-" + name
        source.entities[prop.subject_entity_id].attributes["status"] = status
        self.pinned = deepcopy(source)
        self.retained = canonical(manifest(self.pinned))
        self.read_set = json.loads(self.retained)
        self.read_digest = digest(self.read_set)
        self.artifact = self.read_set["stores"]["propositions"][prop.id]
        files = sorted(str(path.relative_to(ROOT)) for path in
                       (ROOT / "python/SOLScript/solscript").rglob("*.py"))
        files += ["python/expression/solscript_adapter.py",
                  "python/expression/test_solscript_adapter.py",
                  str(Path(__file__).relative_to(ROOT))]
        self.evaluator = {
            "operation": "ResolutionInterpreter.evaluate_proposition",
            "revision": INTERPRETER_REVISION,
            "worker_contract": "test.single-proposition-inquiry.v1",
            "source_digests": {
                path: "sha256:" + hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                for path in files
            },
            "authority_status": "evaluation_only",
            "mutation_policy": "forbidden",
        }
        self.evaluator_digest = digest(self.evaluator)
        # Simulate a live-source change AFTER pinning; evaluation must still
        # use retained inputs. Never write this source to a database.
        source.entities[prop.subject_entity_id].attributes["status"] = (
            "closed" if status == "open" else "open"
        )
        return {
            "work_request": {
                "id": WORK_REQUEST_ID,
                "intent": "Evaluate prop-expression-001 read-only",
                "constraints": {"mutation_policy": "forbidden"},
                "priority": 5,
                "context": {"inquiry": {
                    "read_set_scope": {"proposition_id": prop.id, "closure": "fixture dependencies"},
                    "evaluator_ref": {"operation": self.evaluator["operation"], "revision": INTERPRETER_REVISION},
                    "expected_outcome_type": "EvaluationDisposition",
                    "evidence_requirements": ["retained_manifest", "evaluator_digest", "result_digest", "wind_refs"],
                }},
            },
            "manifest": self.read_set,
            "evaluator": self.evaluator,
            "artifact_fingerprint": digest(self.artifact),
            "read_set_digest": self.read_digest,
            "evaluator_contract_digest": self.evaluator_digest,
            "source_after_pin_digest": digest(manifest(source)),
        }

    def evaluate(self):
        if self.pinned is None:
            raise ValueError("prepare before evaluation")
        if self.result is not None:
            raise ValueError("evaluation already completed; replay retained result instead")
        if canonical(manifest(self.pinned)) != self.retained:
            raise ValueError("pinned inputs drifted")
        self.calls += 1
        prop = self.pinned.propositions["prop-expression-001"]
        disposition, all_passed, context_status = self.pinned.evaluate_proposition(prop, {})
        self.result = {
            "proposition_id": prop.id,
            "disposition": disposition.value.lower(),
            "all_passed": bool(all_passed),
            "context_status": context_status,
            "read_set_digest": self.read_digest,
            "evaluator_contract_digest": self.evaluator_digest,
            "authority_status": "evaluation_only",
            "mutation_policy": "forbidden",
        }
        if canonical(manifest(self.pinned)) != self.retained:
            raise AssertionError("evaluation changed ontology/proposition state")
        return {"result": self.result, "result_digest": digest(self.result),
                "evaluation_calls": self.calls, "state_unchanged": True}

    def receipt(self, wind_refs):
        if self.result is None:
            raise ValueError("no observed result")
        receipt = ExecutionReceipt(
            work_request_id=WORK_REQUEST_ID,
            executor_id=EXECUTOR_ID,
            inputs=[{"read_set_digest": self.read_digest,
                     "evaluator_contract_digest": self.evaluator_digest}],
            mutations=[], timestamp="2026-10-05T00:00:00Z", result="SUCCESS",
            lineage_parent=EXECUTION_ID, inquiry_outcome=self.result,
            result_digest=digest(self.result), wind_refs=wind_refs,
        )
        return {"receipt": receipt.model_dump(mode="json"), "evaluation_calls": self.calls}


if __name__ == "__main__":
    worker = InquiryWorker()
    for line in sys.stdin:
        try:
            command = json.loads(line)
            operation = command["operation"]
            if operation == "prepare":
                output = worker.prepare(command["status"])
            elif operation == "evaluate":
                output = worker.evaluate()
            elif operation == "receipt":
                output = worker.receipt(command["wind_refs"])
            else:
                raise ValueError("unknown operation")
            print(canonical(output), flush=True)
        except Exception as exc:
            print(canonical({"error": type(exc).__name__, "message": str(exc)}), flush=True)
