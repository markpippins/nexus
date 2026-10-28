"""Focused positive-work-evidence contract tests (plan 8261664)."""

import os
import sys
import time
import hashlib

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import execution_worker as worker
import executor_cloud
from test_execution_outcome_contract import _WorkerDB


def _run(monkeypatch, tmp_path, output, *, plan_id="plan-1", expected="artifact.md", durable=False,
         write_artifact=None):
    db = _WorkerDB()
    db.ticket_write_committed_then_failed = False
    db.has_builder_work_evidence = lambda _plan, **_kwargs: durable
    request = {"id": "request-1", "source_plan_id": plan_id, "title": "evidence"}
    req = {
        "id": "wr-1",
        "metadata": {
            "model": "",
            "harness": "opencode",
            "evidence_contract": {
                "expected_files": [expected],
                "allowed_root": str(tmp_path),
                "durable_reference_required": True,
            },
        },
        "artifacts": {"produced_files": [{"path": expected}]},
    }
    monkeypatch.setattr(worker, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(worker, "_build_req_from_request", lambda *_args, **_kwargs: req)
    monkeypatch.setattr(worker, "_resolve_model_chain", lambda *_args: [
        {"harness": "opencode", "model": "provider/model"},
    ])
    def run_builder(req, *_args, **_kwargs):
        if write_artifact is not None:
            (tmp_path / expected).write_text(write_artifact)
        if durable:
            (tmp_path / expected).write_text("reviewable artifact\n")
            os.utime(tmp_path / expected, (time.time() + 1, time.time() + 1))
            marker = {
                "work_request_id": req["metadata"]["work_request_id"],
                "attempt_id": req["metadata"]["attempt_id"],
                "session_id": req["metadata"]["session_id"],
                "artifacts": [{"path": expected, "sha256": hashlib.sha256(
                    (tmp_path / expected).read_bytes()).hexdigest()}],
                "agent_record": "record-current",
            }
            evidence_output = f'NEXUS_WORK_EVIDENCE: {__import__("json").dumps(marker)}'
            if isinstance(durable, tuple) and durable[0] == "stderr":
                return executor_cloud.HarnessExecutionResult(0, "", evidence_output)
            return executor_cloud.HarnessExecutionResult(0, evidence_output, "")
        return executor_cloud.HarnessExecutionResult(0, output, "")
    monkeypatch.setattr(worker.executor_cloud, "run_opencode_outcome", run_builder)
    return db, request


@pytest.mark.parametrize("output", ["", "NEXUS_WORK_EVIDENCE: not-json"])
def test_zero_exit_empty_or_unstructured_output_is_evidence_failure(monkeypatch, tmp_path, output):
    db, request = _run(monkeypatch, tmp_path, output)

    assert worker._process_one(db, request, "test-executor", False) == "evidence-failed"
    assert db.attempt_status == "SUCCEEDED"
    assert db.attempt_exit_code == 0
    assert db.attempt_result["work_outcome"] == "evidence_failed"
    assert db.request_status == "FAILED"
    assert db.execution_receipts == {}
    assert len(db.plan_receipts) == 1
    assert db.session_exit_code == 0


def test_permission_rejection_marker_cannot_be_hidden_by_exit_zero(monkeypatch, tmp_path):
    db, request = _run(monkeypatch, tmp_path, "OpenCode permission denied while running tool")

    assert worker._process_one(db, request, "test-executor", False) == "evidence-failed"
    assert any("permission denied" in reason for reason in db.attempt_result["evidence_failure"]["reasons"])
    assert db.request_status == "FAILED"


def test_wc_plan_requires_reviewable_artifact_and_durable_record(monkeypatch, tmp_path):
    db, request = _run(monkeypatch, tmp_path, "implemented", plan_id="8261655")

    assert worker._process_one(db, request, "test-executor", False) == "evidence-failed"
    assert db.attempt_result["work_outcome"] == "evidence_failed"
    assert db.request_status == "FAILED"


def test_directory_target_requires_concrete_reported_artifact(monkeypatch, tmp_path):
    target = tmp_path / "docs"
    target.mkdir()
    (target / "inventory.md").write_text("inventory\n")
    db = _WorkerDB()
    db.has_builder_work_evidence = lambda _plan, **_kwargs: True
    req = {
        "metadata": {"evidence_contract": {
            "expected_files": ["docs"],
            "allowed_root": str(tmp_path),
            "durable_reference_required": True,
        }},
        "artifacts": {"produced_files": []},
    }
    started = time.time()
    baseline = worker.capture_artifact_baseline(req, project_root=str(tmp_path))
    (target / "inventory.md").write_text("inventory changed\n")
    inventory_hash = hashlib.sha256((target / "inventory.md").read_bytes()).hexdigest()
    assert worker.validate_work_evidence(
        db,
        req,
        'NEXUS_WORK_EVIDENCE: {"work_request_id":"wr-1","attempt_id":"attempt-1",'
        '"session_id":"session-1","agent_record":"record-current",'
        f'"artifacts":[{{"path":"docs/inventory.md","sha256":"{inventory_hash}"}}]}}',
        "8261655",
        project_root=str(tmp_path),
        work_request_id="wr-1", attempt_id="attempt-1", session_id="session-1",
        attempt_started_at=started - 1,
        artifact_baseline=baseline,
    )["artifact_refs"] == ["docs/inventory.md"]


def test_permission_rejection_in_stderr_is_also_rejected(tmp_path):
    db = _WorkerDB()
    req = {"metadata": {}, "artifacts": {"produced_files": []}}
    with pytest.raises(worker.WorkEvidenceError, match="permission denied"):
        worker.validate_work_evidence(
            db, req, "implemented", "plan-1", project_root=str(tmp_path),
            diagnostics="permission denied by tool policy",
        )


@pytest.mark.parametrize(
    "field,wrong_value",
    [("work_request_id", "wr-stale"), ("attempt_id", "attempt-stale"),
     ("session_id", "session-stale")],
)
def test_marker_must_match_each_current_identity(tmp_path, field, wrong_value):
    db = _WorkerDB()
    marker = {"work_request_id": "wr-1", "attempt_id": "attempt-1",
              "session_id": "session-1", "artifacts": []}
    marker[field] = wrong_value
    with pytest.raises(worker.WorkEvidenceError, match=field):
        worker.validate_work_evidence(
            db,
            {"metadata": {"evidence_contract": {"expected_files": [],
                                                  "allowed_root": str(tmp_path)}}},
            f'NEXUS_WORK_EVIDENCE: {__import__("json").dumps(marker)}',
            "plan-1", project_root=str(tmp_path),
            work_request_id="wr-1", attempt_id="attempt-1", session_id="session-1",
        )


def test_marker_requires_current_work_request_attempt_and_session(tmp_path):
    db = _WorkerDB()
    with pytest.raises(worker.WorkEvidenceError, match="missing current work_request_id"):
        worker.validate_work_evidence(
            db,
            {"metadata": {"evidence_contract": {"expected_files": [],
                                                  "allowed_root": str(tmp_path)}}},
            'NEXUS_WORK_EVIDENCE: {"work_request_id":"wr-1","attempt_id":"attempt-1",'
            '"session_id":"session-1"}',
            "plan-1", project_root=str(tmp_path),
            attempt_id="attempt-1", session_id="session-1",
        )


def test_preexisting_artifact_and_stale_record_are_rejected(tmp_path):
    db = _WorkerDB()
    db.has_builder_work_evidence = lambda _plan, **_kwargs: True
    artifact = tmp_path / "artifact.md"
    artifact.write_text("old\n")
    req = {"metadata": {"evidence_contract": {"expected_files": ["artifact.md"],
                                                  "allowed_root": str(tmp_path),
                                                  "durable_reference_required": True}},
           "artifacts": {"produced_files": []}}
    baseline = worker.capture_artifact_baseline(req, project_root=str(tmp_path))
    marker = ('NEXUS_WORK_EVIDENCE: {"work_request_id":"wr-1",'
              '"attempt_id":"attempt-1","session_id":"session-1",'
              '"artifacts":["artifact.md"],"agent_record":"record-old"}')
    with pytest.raises(worker.WorkEvidenceError, match="not changed"):
        worker.validate_work_evidence(
            db,
            req,
            marker, "plan-1", project_root=str(tmp_path),
            work_request_id="wr-1", attempt_id="attempt-1", session_id="session-1",
            attempt_started_at=time.time() + 10,
            artifact_baseline=baseline,
        )


def test_content_hash_does_not_authorize_preexisting_file(tmp_path):
    db = _WorkerDB()
    db.has_builder_work_evidence = lambda _plan, **_kwargs: True
    content = b"content addressed\n"
    (tmp_path / "artifact.md").write_bytes(content)
    req = {"metadata": {"evidence_contract": {"expected_files": ["artifact.md"],
                                                  "allowed_root": str(tmp_path),
                                                  "durable_reference_required": True}},
           "artifacts": {"produced_files": []}}
    baseline = worker.capture_artifact_baseline(req, project_root=str(tmp_path))
    digest = hashlib.sha256(content).hexdigest()
    marker = ('NEXUS_WORK_EVIDENCE: {"work_request_id":"wr-1",'
              '"attempt_id":"attempt-1","session_id":"session-1",'
              f'"artifacts":[{{"path":"artifact.md","sha256":"{digest}"}}],'
              '"agent_record":"record-current"}')
    with pytest.raises(worker.WorkEvidenceError, match="not changed"):
        worker.validate_work_evidence(
            db,
            req,
            marker, "plan-1", project_root=str(tmp_path),
            work_request_id="wr-1", attempt_id="attempt-1", session_id="session-1",
            attempt_started_at=time.time() + 10,
            artifact_baseline=baseline,
        )


def test_touching_unchanged_stale_artifact_is_evidence_failure(tmp_path):
    db = _WorkerDB()
    db.has_builder_work_evidence = lambda _plan, **_kwargs: True
    artifact = tmp_path / "artifact.md"
    artifact.write_text("stale\n")
    req = {"metadata": {"evidence_contract": {"expected_files": ["artifact.md"],
                                                  "allowed_root": str(tmp_path),
                                                  "durable_reference_required": True}},
           "artifacts": {"produced_files": []}}
    baseline = worker.capture_artifact_baseline(req, project_root=str(tmp_path))
    os.utime(artifact, (time.time() + 1, time.time() + 1))
    marker = ('NEXUS_WORK_EVIDENCE: {"work_request_id":"wr-1",'
              '"attempt_id":"attempt-1","session_id":"session-1",'
              '"artifacts":[{"path":"artifact.md","sha256":"'
              + hashlib.sha256(b"stale\n").hexdigest()
              + '"}],"agent_record":"record-current"}')
    with pytest.raises(worker.WorkEvidenceError, match="content was not changed"):
        worker.validate_work_evidence(
            db, req, marker, "plan-1", project_root=str(tmp_path),
            work_request_id="wr-1", attempt_id="attempt-1", session_id="session-1",
            artifact_baseline=baseline,
        )


def test_modified_artifact_with_matching_hash_is_valid(tmp_path):
    db = _WorkerDB()
    db.has_builder_work_evidence = lambda _plan, **_kwargs: True
    artifact = tmp_path / "artifact.md"
    artifact.write_text("stale\n")
    req = {"metadata": {"evidence_contract": {"expected_files": ["artifact.md"],
                                                  "allowed_root": str(tmp_path),
                                                  "durable_reference_required": True}},
           "artifacts": {"produced_files": []}}
    baseline = worker.capture_artifact_baseline(req, project_root=str(tmp_path))
    artifact.write_text("changed\n")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    marker = ('NEXUS_WORK_EVIDENCE: {"work_request_id":"wr-1",'
              '"attempt_id":"attempt-1","session_id":"session-1",'
              f'"artifacts":[{{"path":"artifact.md","sha256":"{digest}"}}],'
              '"agent_record":"record-current"}')
    result = worker.validate_work_evidence(
        db, req, marker, "plan-1", project_root=str(tmp_path),
        work_request_id="wr-1", attempt_id="attempt-1", session_id="session-1",
        artifact_baseline=baseline,
    )
    assert result["status"] == "valid"


def test_current_artifact_requires_matching_hash_when_marker_supplies_one(tmp_path):
    db = _WorkerDB()
    db.has_builder_work_evidence = lambda _plan, **_kwargs: True
    artifact = tmp_path / "artifact.md"
    started = time.time()
    artifact.write_text("current\n")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    marker = ('NEXUS_WORK_EVIDENCE: {"work_request_id":"wr-1",'
              '"attempt_id":"attempt-1","session_id":"session-1",'
              f'"artifacts":[{{"path":"artifact.md","sha256":"{digest}"}}],'
              '"agent_record":"record-current"}')
    result = worker.validate_work_evidence(
        db,
        {"metadata": {"evidence_contract": {"expected_files": ["artifact.md"],
                                              "allowed_root": str(tmp_path),
                                              "durable_reference_required": True}}},
        marker, "plan-1", project_root=str(tmp_path),
        work_request_id="wr-1", attempt_id="attempt-1", session_id="session-1",
        attempt_started_at=started - 1,
    )
    assert result["status"] == "valid"


@pytest.mark.parametrize("stage", ["attempt", "receipt", "request"])
def test_evidence_failure_persistence_is_fail_closed(monkeypatch, stage):
    db = _WorkerDB()
    if stage == "attempt":
        db.update_attempt_result = lambda *_args: (_ for _ in ()).throw(RuntimeError("attempt write"))
    elif stage == "receipt":
        db.insert_receipt = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("receipt write"))
    else:
        class BrokenConnection:
            def execute(self, *_args):
                raise RuntimeError("request write")
            def commit(self):
                pass
        @__import__("contextlib").contextmanager
        def broken_connection():
            yield BrokenConnection()
        db._get_connection = broken_connection
    result = worker._persist_evidence_failure(
        db, "request-1", "plan-1", "session-1", "ticket-1", "attempt-1",
        "wr-1", "provider/model", {"process_outcome": {"returncode": 0}},
        worker.WorkEvidenceError(["missing artifact"]),
    )
    assert result["non_runnable"] is True
    assert result["evidence_failure"]["persistence_errors"]
    if stage != "request":
        assert db.request_status == "FAILED"


def test_request_update_failure_blocks_plan_before_next_ready_consumption(monkeypatch):
    db = _WorkerDB()
    db.plan_eligible = True
    db.close_ticket = lambda *_args: setattr(db, "plan_eligible", False) or True

    class BrokenConnection:
        def execute(self, *_args):
            raise RuntimeError("request write")

        def commit(self):
            pass

    @__import__("contextlib").contextmanager
    def broken_connection():
        yield BrokenConnection()

    db._get_connection = broken_connection
    result = worker._persist_evidence_failure(
        db, "request-1", "plan-1", "session-1", "ticket-1", "attempt-1",
        "wr-1", "provider/model", {"process_outcome": {"returncode": 0}},
        worker.WorkEvidenceError(["missing artifact"]),
    )

    assert result["non_runnable"] is True
    assert any(item["receipt_type"] == worker.FAIL_RECEIPT for item in db.plan_receipts.values())
    assert db.plan_eligible is False

    monkeypatch.setattr(worker, "_recover_orphans", lambda *_args: 0)
    monkeypatch.setattr(worker, "_retry_pending_governance_finalizations", lambda *_args: (0, 0))
    monkeypatch.setattr(worker, "_surface_session_exit_observations", lambda *_args: 0)
    monkeypatch.setattr(worker, "_role_lease_blocked", lambda *_args: None)
    monkeypatch.setattr(worker, "_reconcile_plans", lambda *_args: [])
    monkeypatch.setattr(db, "get_eligible_plans", lambda *_args: (
        [{"id": "plan-1"}] if db.plan_eligible else []
    ))
    consumed = []
    monkeypatch.setattr(worker, "_process_one", lambda *_args, **_kwargs: consumed.append(True))

    summary = worker._run_pass(db, "test-executor", False, False)

    assert summary["consumed"] == 0
    assert consumed == []


def test_valid_artifact_and_durable_builder_record_settle_normally(monkeypatch, tmp_path):
    db, request = _run(monkeypatch, tmp_path, "implemented", durable=True)

    assert worker._process_one(db, request, "test-executor", False) == "completed"
    assert db.attempt_status == "SUCCEEDED"
    assert db.attempt_result["work_evidence"]["status"] == "valid"
    assert db.request_status == "COMPLETED"
    assert len(db.execution_receipts) == 1
    assert len(db.plan_receipts) == 1


def test_changed_artifact_with_empty_stdout_and_stderr_marker_succeeds(monkeypatch, tmp_path):
    db, request = _run(
        monkeypatch, tmp_path, "", durable=("stderr", True)
    )

    assert worker._process_one(db, request, "test-executor", False) == "completed"
    assert db.attempt_result["work_evidence"]["status"] == "valid"
    assert db.attempt_result["work_evidence"]["correlation"] == {
        "work_request_id": "wr-1",
        "attempt_id": "attempt-1",
        "session_id": db.session_id,
    }
    assert db.request_status == "COMPLETED"


def test_missing_marker_fails_even_when_artifact_changed(monkeypatch, tmp_path):
    db, request = _run(
        monkeypatch, tmp_path, "", write_artifact="changed but no marker\n"
    )

    assert worker._process_one(db, request, "test-executor", False) == "evidence-failed"
    assert any("missing machine-readable" in reason
               for reason in db.attempt_result["evidence_failure"]["reasons"])
    assert db.request_status == "FAILED"


def test_partial_process_output_remains_process_failure(monkeypatch, tmp_path):
    db = _WorkerDB()
    request = {"id": "request-1", "source_plan_id": "plan-1", "title": "partial"}
    monkeypatch.setattr(worker, "_build_req_from_request", lambda *_args, **_kwargs: {
        "id": "wr-1", "metadata": {"model": "", "harness": "opencode"},
    })
    monkeypatch.setattr(worker, "_resolve_model_chain", lambda *_args: [
        {"harness": "opencode", "model": "provider/model"},
    ])
    monkeypatch.setattr(
        worker.executor_cloud,
        "run_opencode_outcome",
        lambda *_args, **_kwargs: executor_cloud.HarnessExecutionResult(19, "partial output", "failed"),
    )

    assert worker._process_one(db, request, "test-executor", False) == "failed"
    assert db.attempt_status == "FAILED"
    assert db.attempt_exit_code == 19
    assert db.request_status == "FAILED"


def test_evidence_failure_persistence_is_idempotent_and_requeue_gated():
    db = _WorkerDB()
    error = worker.WorkEvidenceError(["missing artifact: artifact.md"])
    result = {
        "work_request_id": "wr-1",
        "process_outcome": {"returncode": 0},
        "session_id": "session-1",
    }

    first = worker._persist_evidence_failure(
        db, "request-1", "plan-1", "session-1", "ticket-1", "attempt-1",
        "wr-1", "provider/model", result, error,
    )
    second = worker._persist_evidence_failure(
        db, "request-1", "plan-1", "session-1", "ticket-1", "attempt-1",
        "wr-1", "provider/model", first, error,
    )

    assert first["work_outcome"] == second["work_outcome"] == "evidence_failed"
    assert len(db.plan_receipts) == 1
    assert db.execution_receipts == {}
    assert db.request_status == "FAILED"


def test_success_settlement_rejects_mismatched_correlation_before_receipt():
    db = _WorkerDB()
    result = {
        "attempt_id": "attempt-stale",
        "request_id": "request-1",
        "session_id": "session-1",
        "work_request_id": "wr-1",
        "work_evidence": {
            "status": "valid",
            "correlation": {"attempt_id": "attempt-stale", "request_id": "request-1",
                           "session_id": "session-1", "work_request_id": "wr-1"},
        },
    }

    with pytest.raises(RuntimeError, match="identity does not match"):
        worker._ensure_success_execution_evidence(
            db, "attempt-1", "request-1", result, "conduit-worker"
        )
    assert db.execution_receipts == {}
    assert db.request_status == "READY"


def test_pending_governance_retry_cannot_settle_evidence_failed_attempt():
    db = _WorkerDB()
    result = {
        "work_outcome": "evidence_failed",
        "governance_finalization": {"status": "pending"},
        "request_id": "request-1",
    }

    with pytest.raises(RuntimeError, match="evidence-failed"):
        worker._finalize_governance_row(db, {
            "attempt_id": "attempt-1",
            "request_id": "request-1",
            "source_plan_id": "plan-1",
            "result": result,
            "attempt_status": "SUCCEEDED",
            "exit_code": 0,
        })
    assert db.execution_receipts == {}
