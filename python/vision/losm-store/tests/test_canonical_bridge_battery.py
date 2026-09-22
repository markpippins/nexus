"""W2a battery — flagged dual-write repoint (plan-of-record 4c3b74bd).

Runs against live PostgreSQL per house convention (cf.
test_ingestor_validation.py). Safety rules:

* Canonical cleanup is TARGETED: only rows with created_by =
  'losm_store.w2a' (the bridge's writer pin) are removed. Stage-1 backfill
  rows and any other writers are NEVER touched.
* Legacy cleanup is intent-prefixed ('w2a-test:%') on work_requests_history;
  ingest/governance history rows are removed by work_request_id belonging to
  the battery's wr_ids. Never table-wide.
* Honest SKIPs are declared in this docstring, not silently dropped:
  - REST-level (TestClient) transition/orchestrate tests are NOT included;
    the same repository functions are exercised directly here. The
    orchestrator read-your-write check needs the full service runtime
    (PipelineCoordinator) and is deferred to deploy verification (R2 note).

Substrate correction under test (verified live 2026-09-21):
``vision.work_requests_losm`` is a read-only VIEW over
``vision.work_requests_history`` — so the battery proves the mirror through
BOTH surfaces: raw history rows (bitemporal versions) and view reads (the
open slice the losm read model exposes).

Vocabulary pre-check (plan §battery.1) is encoded as a test:
losm wr_id is the uuid4 model-default family, NOT the #400 `wr-` factory
family — therefore the battery pins canonical rows by created_by (flag-based
S2 pinning, the W3a approach) instead of relying on the wr- regex.
"""

from __future__ import annotations

import re
import uuid

import pytest
from sqlalchemy import text

from losm_ir.states import WorkStatus
from losm_store.canonical_bridge import (
    CANONICAL_STATUS_VOCABULARY,
    STATUS_MAP,
    map_status,
)
from losm_store.ingestor import ExecutionReceiptIngestor
from losm_store.models import PlanningTask
from losm_store.repository import (
    create_work_request,
    delete_work_request,
    get_work_request_by_wr_id,
    update_work_request,
)
from losm_store.session import SessionLocal

BRIDGE_CREATED_BY = "losm_store.w2a"
LEGACY_INTENT_PREFIX = "w2a-test:"


def _cleanup():
    db = SessionLocal()
    try:
        db.execute(
            text("DELETE FROM resolution.work_request WHERE created_by = :b"),
            {"b": BRIDGE_CREATED_BY},
        )
        db.execute(
            text(
                "DELETE FROM vision.governance_events_history "
                "WHERE work_request_id IN (SELECT wr_id FROM "
                "vision.work_requests_history WHERE intent LIKE :p)"
            ),
            {"p": LEGACY_INTENT_PREFIX + "%"},
        )
        db.execute(
            text(
                "DELETE FROM vision.receipt_ingest_records_history "
                "WHERE work_request_id IN (SELECT wr_id FROM "
                "vision.work_requests_history WHERE intent LIKE :p)"
            ),
            {"p": LEGACY_INTENT_PREFIX + "%"},
        )
        db.execute(
            text(
                "DELETE FROM vision.work_requests_history WHERE intent LIKE :p"
            ),
            {"p": LEGACY_INTENT_PREFIX + "%"},
        )
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def clean_w2a_rows():
    _cleanup()
    yield
    _cleanup()


def _canonical_row(db, wr_id: str):
    return db.execute(
        text(
            "SELECT id, title, business_status, legacy_id, created_by, context, plan_id "
            "FROM resolution.work_request WHERE id = :i"
        ),
        {"i": wr_id},
    ).mappings().first()


def _history_versions(db, wr_id: str):
    return db.execute(
        text(
            "SELECT status, intent, recorded_until_dt FROM "
            "vision.work_requests_history WHERE wr_id = :w "
            "ORDER BY recorded_on_dt"
        ),
        {"w": wr_id},
    ).mappings().all()


def _open_epoch(row) -> bool:
    return str(row["recorded_until_dt"]).startswith("9999-12-31")


# ── Vocabulary pre-check (plan §battery.1) ──────────────────────────────────


def test_vocabulary_precheck_total_mapping_within_canonical_vocab():
    """STATUS_MAP is total over losm WorkStatus and lands in the CHECK vocab."""
    losm_states = {s.value for s in WorkStatus}
    assert losm_states == set(STATUS_MAP), "STATUS_MAP must be total over WorkStatus"
    assert set(STATUS_MAP.values()) <= CANONICAL_STATUS_VOCABULARY


def test_vocabulary_precheck_losm_ids_are_uuid_family_not_wr_factory():
    """losm wr_id (model default uuid4) is NOT the #400 wr- factory family.

    Consequence (recorded in R1/R2): the battery pins canonical rows by
    created_by rather than the wr- regex; no #400 vocabulary change needed.
    """
    factory_family = re.compile(r"^wr-\d+-\d+-[0-9a-f]+$")
    losm_id = str(uuid.uuid4())
    assert not factory_family.match(losm_id)


def test_map_status_unknown_state_is_loud():
    with pytest.raises(ValueError):
        map_status("NOT_A_REAL_STATE")


# ── Substrate facts (pinned live 2026-09-21) ────────────────────────────────


def test_work_requests_losm_is_a_view_over_history():
    """The ORM's PlanningTask target is a VIEW, not a base table.

    This is the substrate correction the W2a implementation is built on; if
    this ever flips back to a base table, the mirror strategy should be
    re-evaluated (and the docstring of canonical_bridge updated).
    """
    db = SessionLocal()
    try:
        row = db.execute(
            text(
                "SELECT table_type FROM information_schema.tables "
                "WHERE table_schema='vision' AND table_name='work_requests_losm'"
            )
        ).mappings().one()
        assert row["table_type"] == "VIEW"
    finally:
        db.close()


# ── Create: canonical-first + mirror, same transaction ──────────────────────


def test_create_writes_canonical_and_mirror_with_shared_uuid():
    db = SessionLocal()
    wr = create_work_request(
        db,
        intent=LEGACY_INTENT_PREFIX + " create battery",
        constraints={"k": "v"},
        priority=3,
        context_data={"origin": "w2a-battery"},
    )
    row = _canonical_row(db, wr.wr_id)
    assert row is not None, "canonical row missing after create"
    assert str(row["id"]) == wr.wr_id, "PK-carried identity violated"
    assert row["legacy_id"] == f"vision.work_requests_history:{wr.wr_id}"
    assert row["business_status"] == "DRAFT"
    assert row["title"] == LEGACY_INTENT_PREFIX + " create battery"
    assert row["plan_id"] is None, "plan_id column must stay NULL (767abf1b)"
    assert row["context"]["losm_status"] == "NEW"
    assert row["context"]["losm_priority"] == 3
    assert row["context"]["origin"] == "w2a-battery"
    assert row["created_by"] == BRIDGE_CREATED_BY
    # Mirror: the VIEW (read projection) must show the new WR...
    mirror = get_work_request_by_wr_id(db, wr.wr_id)
    assert mirror is not None, "legacy mirror missing after create (view read)"
    assert mirror.status == WorkStatus.NEW
    assert mirror.context_data["origin"] == "w2a-battery"
    # ...and the HISTORY base holds exactly one OPEN version.
    versions = _history_versions(db, wr.wr_id)
    assert len(versions) == 1
    assert _open_epoch(versions[0])
    assert versions[0]["status"] == "NEW"


# ── Update: bitemporal versioning + canonical-first status mapping ──────────


def test_update_status_maps_and_versions_the_mirror():
    db = SessionLocal()
    wr = create_work_request(db, intent=LEGACY_INTENT_PREFIX + " update battery")
    updated = update_work_request(db, wr.wr_id, status=WorkStatus.EXECUTION)
    assert updated is not None and updated.status == WorkStatus.EXECUTION
    row = _canonical_row(db, wr.wr_id)
    assert row["business_status"] == "DISPATCHED"
    assert row["context"]["losm_status"] == "EXECUTION"
    # Bitemporal mirror: old version closed, new version open.
    versions = _history_versions(db, wr.wr_id)
    assert len(versions) == 2, "update must close the old version and open a new one"
    assert versions[0]["status"] == "NEW" and not _open_epoch(versions[0])
    assert versions[1]["status"] == "EXECUTION" and _open_epoch(versions[1])


def test_update_scalar_only_keeps_canonical_projection_in_sync():
    db = SessionLocal()
    wr = create_work_request(db, intent=LEGACY_INTENT_PREFIX + " scalar battery")
    update_work_request(db, wr.wr_id, intent=LEGACY_INTENT_PREFIX + " scalar renamed")
    row = _canonical_row(db, wr.wr_id)
    assert row["title"] == LEGACY_INTENT_PREFIX + " scalar renamed"
    versions = _history_versions(db, wr.wr_id)
    assert versions[-1]["intent"] == LEGACY_INTENT_PREFIX + " scalar renamed"


def test_update_preserves_unchanged_scalars_in_new_mirror_version():
    db = SessionLocal()
    wr = create_work_request(
        db, intent=LEGACY_INTENT_PREFIX + " carry battery", priority=7
    )
    update_work_request(db, wr.wr_id, status=WorkStatus.VALIDATION)
    versions = _history_versions(db, wr.wr_id)
    assert versions[-1]["intent"] == LEGACY_INTENT_PREFIX + " carry battery"
    db2 = SessionLocal()
    try:
        carried = db2.execute(
            text("SELECT priority FROM vision.work_requests_history WHERE wr_id = :w"),
            {"w": wr.wr_id},
        ).mappings().all()
        assert [int(r["priority"]) for r in carried] == [7, 7]
    finally:
        db2.close()


# ── Ingest: receipt-driven transition follows into canonical ────────────────


def _receipt_payload(wr_id: str, result: str) -> dict:
    from datetime import datetime

    return {
        "work_request_id": wr_id,
        "executor_id": "w2a-battery-executor",
        "inputs": [],
        "mutations": [],
        "timestamp": datetime.utcnow().isoformat(),
        "result": result,
        "lineage_parent": str(uuid.uuid4()),
    }


def test_ingest_success_receipt_updates_canonical_and_mirror():
    db = SessionLocal()
    wr = create_work_request(db, intent=LEGACY_INTENT_PREFIX + " ingest battery")
    assert update_work_request(db, wr.wr_id, status=WorkStatus.VALIDATION)
    result = ExecutionReceiptIngestor().ingest(
        db, _receipt_payload(wr.wr_id, "SUCCESS")
    )
    assert result["status"] == "ingested"
    row = _canonical_row(db, wr.wr_id)
    assert row["business_status"] == "COMPLETED"
    assert row["context"]["losm_status"] == "COMPLETION"
    versions = _history_versions(db, wr.wr_id)
    assert versions[-1]["status"] == "COMPLETION" and _open_epoch(versions[-1])
    # Audit mirrors landed in their history bases.
    db2 = SessionLocal()
    try:
        rec = db2.execute(
            text(
                "SELECT count(*) AS n FROM vision.receipt_ingest_records_history "
                "WHERE work_request_id = :w"
            ),
            {"w": wr.wr_id},
        ).scalar()
        evt = db2.execute(
            text(
                "SELECT count(*) AS n FROM vision.governance_events_history "
                "WHERE work_request_id = :w AND event_type = 'RECEIPT_INGESTED'"
            ),
            {"w": wr.wr_id},
        ).scalar()
        assert rec == 1 and evt == 1
    finally:
        db2.close()


# ── Delete: CANCELLED disposition + bitemporal tombstone ────────────────────


def test_delete_tombstones_mirror_and_keeps_canonical_row():
    db = SessionLocal()
    wr = create_work_request(db, intent=LEGACY_INTENT_PREFIX + " delete battery")
    assert delete_work_request(db, wr.wr_id) is True
    assert get_work_request_by_wr_id(db, wr.wr_id) is None, (
        "view read must hide the tombstoned WR"
    )
    row = _canonical_row(db, wr.wr_id)
    assert row is not None, "canonical row must NEVER be deleted"
    assert row["business_status"] == "CANCELLED"
    assert row["context"]["losm_disposition"] == "deleted_via_losm_api"
    versions = _history_versions(db, wr.wr_id)
    assert len(versions) == 1 and not _open_epoch(versions[0]), (
        "delete closes the bitemporal slice (history retains the trail)"
    )


def test_delete_never_regresses_completed():
    db = SessionLocal()
    wr = create_work_request(db, intent=LEGACY_INTENT_PREFIX + " completed battery")
    assert update_work_request(db, wr.wr_id, status=WorkStatus.COMPLETION)
    assert delete_work_request(db, wr.wr_id) is True
    row = _canonical_row(db, wr.wr_id)
    assert row["business_status"] == "COMPLETED", "COMPLETED must not regress"
    assert row["context"]["losm_disposition"] == "deleted_via_losm_api"


# ── Flag off: rollback path is legacy-only (and still functional) ───────────


def test_flag_off_restores_legacy_only_behavior(monkeypatch):
    monkeypatch.setenv("LOSM_W2_DUAL_WRITE", "0")
    db = SessionLocal()
    wr = create_work_request(db, intent=LEGACY_INTENT_PREFIX + " flagoff battery")
    assert _canonical_row(db, wr.wr_id) is None, "flag off must not write canonical"
    assert wr is not None and wr.status == WorkStatus.NEW, (
        "legacy-only mode still works (history base write, view read)"
    )
    assert update_work_request(db, wr.wr_id, status=WorkStatus.EXECUTION)
    assert _canonical_row(db, wr.wr_id) is None
    assert delete_work_request(db, wr.wr_id) is True
    assert get_work_request_by_wr_id(db, wr.wr_id) is None
    assert _canonical_row(db, wr.wr_id) is None


# ── Atomicity: canonical failure aborts the whole operation ─────────────────


def test_canonical_failure_aborts_legacy_write_too(monkeypatch):
    import losm_store.repository as repo

    def _explode(*a, **k):
        raise RuntimeError("simulated canonical failure")

    monkeypatch.setattr(repo, "canonical_insert_for_create", _explode)
    db = SessionLocal()
    with pytest.raises(RuntimeError):
        create_work_request(db, intent=LEGACY_INTENT_PREFIX + " abort battery")
    db.rollback()  # discard the aborted transaction
    fresh = SessionLocal()
    try:
        assert (
            fresh.query(PlanningTask)
            .filter(PlanningTask.intent == LEGACY_INTENT_PREFIX + " abort battery")
            .first()
            is None
        ), "mirror row must be absent when the canonical write fails"
        assert (
            fresh.execute(
                text(
                    "SELECT count(*) AS n FROM vision.work_requests_history "
                    "WHERE intent = :i"
                ),
                {"i": LEGACY_INTENT_PREFIX + " abort battery"},
            ).scalar()
            == 0
        ), "history mirror must be rolled back with the transaction"
    finally:
        fresh.close()
