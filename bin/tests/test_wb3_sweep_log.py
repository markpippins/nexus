"""Unit tests for the W-B3 sweep-log derivation helpers (plan 8261675)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import datetime
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parent.parent / "wb3_sweep_log.py"
SPEC = importlib.util.spec_from_file_location("wb3_sweep_log", MODULE_PATH)
assert SPEC and SPEC.loader
wb3 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wb3)

SWEPT = "2026-11-19T02:00:00Z"
PAST = "2026-11-18T00:00:00Z"
FUTURE = "2026-11-20T00:00:00Z"

COHORT = "wb3_pre_fix_expired_generation"
COHORT_RULING = "ea38297d-1f4e-4cc5-8cda-ef8273702bf8"
WINDOW_FROM = datetime.fromisoformat("2026-06-18T00:00:00+00:00")
WINDOW_TO = datetime.fromisoformat("2026-07-27T00:00:00+00:00")
# sha256 of the sorted 89 ticket ids (newline-joined): pins exact membership.
IDS_SHA256 = "5e2de948a5c71c0fc5d3c19db44de4091291d01ded10dfe7e323bd9aa546a50a"


def _registry():
    return json.loads(wb3.DEFAULT_WAIVERS.read_text(encoding="utf-8"))


def _cohort_rows(data):
    return [w for w in data["waivers"] if w.get("cohort") == COHORT]


def _write(tmp_path, data):
    path = tmp_path / "waivers.json"
    path.write_text(json.dumps(data))
    return path


@pytest.mark.parametrize(
    "status,expires_at,expected",
    [
        ("expired", None, True),
        ("stale", FUTURE, True),
        ("open", PAST, True),
        ("claimed", PAST, True),
        ("open", FUTURE, False),
        ("claimed", FUTURE, False),
        ("open", None, False),
        ("completed", PAST, False),
    ],
)
def test_is_disposition_required(status: str, expires_at: str | None, expected: bool) -> None:
    assert wb3.is_disposition_required(status, expires_at, SWEPT) is expected


def test_reason_precedence_and_effective_value() -> None:
    assert wb3.closure_reason_source("receipt:BLOCK", True) == "ticket"
    assert wb3.closure_reason_effective("expired", "receipt:BLOCK", True) == "receipt:BLOCK"
    assert wb3.closure_reason_source(None, True) == "expiry_event"
    assert wb3.closure_reason_effective("expired", None, True) == "expiry_detection"
    assert wb3.closure_reason_source(None, False) == "status_fallback"
    assert wb3.closure_reason_effective("cancelled", None, False) == "status:cancelled"


def test_disposition_vocabulary_is_closed() -> None:
    assert wb3.derive_disposition("expired", None, True, SWEPT) == "expired_with_event"
    assert (
        wb3.derive_disposition("expired", None, False, SWEPT)
        == "expired_without_event_waived"
    )
    assert (
        wb3.derive_disposition("open", PAST, False, SWEPT)
        == "open_past_expiry_disposed"
    )
    assert (
        wb3.derive_disposition("completed", PAST, False, SWEPT)
        == "no_disposition_required"
    )
    with pytest.raises(ValueError, match="unknown ticket status"):
        wb3.derive_disposition("bogus", PAST, False, SWEPT)


def test_open_generation_statuses() -> None:
    assert wb3.is_open_generation_status("open")
    assert wb3.is_open_generation_status("claimed")
    assert wb3.is_open_generation_status("stale")
    assert not wb3.is_open_generation_status("expired")


def test_waiver_registry_cohort_shape() -> None:
    # W-B3 may only lean on a cohort an architect explicitly ruled. The
    # shipped surface carries exactly the 89-row `wb3_pre_fix_expired_generation`
    # cohort under ruling ea38297d; load_waivers returns those rows stamped
    # with THAT ruling id, never the legacy 6b42dd3f one. The exact id set and
    # the fixed expires_at window are asserted so the cohort cannot drift open.
    data = _registry()
    cohort = data["cohorts"][COHORT]
    assert cohort["ruling_ref"] == COHORT_RULING
    assert cohort["count"] == 89
    assert cohort["expires_at_from"] == "2026-06-18T00:00:00Z"
    assert cohort["expires_at_to"] == "2026-07-27T00:00:00Z"
    rows = _cohort_rows(data)
    assert len(rows) == 89
    ids = [w["ticket_id"] for w in rows]
    assert len(ids) == len(set(ids)) == 89
    assert set(ids) == set(cohort["ticket_ids"])
    assert hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest() == IDS_SHA256
    assert {w["waiver_ref"] for w in rows} == {COHORT_RULING}
    for w in rows:
        expires_at = datetime.fromisoformat(w["expires_at"].replace("Z", "+00:00"))
        assert WINDOW_FROM <= expires_at < WINDOW_TO
    waivers = wb3.load_waivers(wb3.DEFAULT_WAIVERS)
    assert set(waivers) == set(ids)
    assert set(waivers.values()) == {COHORT_RULING}


def test_waiver_registry_refuses_post_cutoff_row(tmp_path) -> None:
    data = _registry()
    _cohort_rows(data)[0]["expires_at"] = "2026-08-01T00:00:00+00:00"
    with pytest.raises(ValueError, match="window"):
        wb3.load_waivers(_write(tmp_path, data))


def test_waiver_registry_refuses_unenumerated_row(tmp_path) -> None:
    data = _registry()
    _cohort_rows(data)[0]["ticket_id"] = "ticket-not-enumerated"
    with pytest.raises(ValueError, match="enumerated"):
        wb3.load_waivers(_write(tmp_path, data))


def test_committed_sweep_artifact_matches_ruling() -> None:
    # The landed S2/S3 re-stamp must attribute the cohort to ea38297d and carry
    # exactly the 89 ruled rows with 0 synthesized events.
    path = Path(__file__).resolve().parents[2] / "docs" / "promotion-flow" / "evidence" / "wb3-sweep-log-8261675.json"
    artifact = json.loads(path.read_text(encoding="utf-8"))
    assert artifact["assertions"]["S2_disposition_vocabulary"]["nulls"] == 0
    assert artifact["disposition_census"]["expired_without_event_waived"] == 89
    assert artifact["waivers"]["ruling_ref"] == COHORT_RULING
    assert artifact["waivers"]["rows"] == 89
    assert artifact["waivers"]["events_synthesized"] == 0
    assert artifact["assertions"]["S3_waiver_refs"]["unwaived"] == 0
    assert artifact["assertions"]["S3_waiver_refs"]["waived"] == 89
    assert artifact["assertions"]["S3_waiver_refs"]["ruling_ref"] == COHORT_RULING
    waived = [
        row
        for row in artifact["generations"]
        if row.get("sweep_disposition") == "expired_without_event_waived"
    ]
    assert len(waived) == 89
    assert {row["waiver_ref"] for row in waived} == {COHORT_RULING}
