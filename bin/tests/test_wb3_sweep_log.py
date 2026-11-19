"""Unit tests for the W-B3 sweep-log derivation helpers (plan 8261675)."""

from __future__ import annotations

import importlib.util
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
    waivers = wb3.load_waivers(wb3.DEFAULT_WAIVERS)
    assert len(waivers) == 89
    assert set(waivers.values()) == {wb3.RULING_REF}
