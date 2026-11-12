#!/usr/bin/env python3
"""Hermetic tests: canonical WorkRequest TypeSpec contract (design note phase 7).

Two layers, per the calendar/agenda precedent:

- STRUCTURE (always runs): inventory, namespace, model-only stance, root
  umbrella wiring, and pins for the decisions the contract encodes — the
  measured status vocabulary, the separation from the losm/vision wire DTOs,
  the temporary-home + deferred-nebula note, the nullable asset link, JSONB
  context/constraints, and the bitemporal columns.
- COMPILE (real `tsp compile` when a toolchain is locatable). The toolchain
  skip goes through host_tool_guard so test_host_tool_ci_visibility sees it.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SELF = Path(__file__).resolve().parent
_REPO = _SELF.parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from host_tool_guard import missing_tools, skip_reason  # noqa: E402

_TSP_V1 = _REPO / "typespec" / "v1"
_MODELS = _TSP_V1 / "workrequest" / "models.tsp"
_MAIN = _TSP_V1 / "workrequest" / "main.tsp"
_ROOT_MAIN = _TSP_V1 / "main.tsp"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ── structure ───────────────────────────────────────────────────────────────

def test_contract_files_exist():
    assert _MODELS.is_file(), f"missing {_MODELS}"
    assert _MAIN.is_file(), f"missing {_MAIN}"


def test_namespace_and_import():
    models, main = _read(_MODELS), _read(_MAIN)
    assert "namespace org.nexus.workrequest" in models
    assert "namespace org.nexus.workrequest" in main
    assert 'import "./models.tsp";' in main


def test_route_free_stance():
    body = _read(_MODELS) + _read(_MAIN)
    for banned in ("@route", "@post", "@get", "@put", "@delete", "@patch"):
        assert banned not in body, f"model-only contract must not contain {banned}"
    assert "route-free" in body.lower()


def test_root_umbrella_imports_workrequest():
    assert 'import "./workrequest/main.tsp";' in _read(_ROOT_MAIN)


def test_status_vocabulary_is_the_measured_one():
    """Live 2026-11-12: DRAFT, COMPLETED, CANCELLED across 46 rows."""
    models = _read(_MODELS)
    for entry in ('Draft: "DRAFT"', 'Completed: "COMPLETED"', 'Cancelled: "CANCELLED"'):
        assert entry in models
    assert "union WorkRequestStatus" in models


def test_contract_is_not_a_wire_dto():
    """The losm-host and vision-srv bodies model different surfaces."""
    models = _read(_MODELS)
    assert "WorkRequestRow" in models
    assert "vision-srv" in models
    assert "deliberately NOT one of\n * the wire DTOs" in models


def test_temporary_home_and_deferred_move_are_recorded():
    models = _read(_MODELS)
    assert "resolution.work_request" in models
    assert "TEMPORARY home" in models
    assert "nebula.work_requests" in models
    assert "expl" in models.lower() and "deferred" in models.lower()


def test_jsonb_columns_are_records():
    models = _read(_MODELS)
    assert "context: Record<unknown>;" in models
    assert "constraints: Record<unknown>;" in models


def test_asset_link_is_nullable():
    """Not every WorkRequest has been admitted through the asset path."""
    models = _read(_MODELS)
    assert "assetId?: string;" in models


def test_bitemporal_columns_present():
    models = _read(_MODELS)
    for field in ("validFrom", "validUntil", "recordedOn", "recordedUntil"):
        assert f"{field}?: utcDateTime;" in models


def test_uuid_convention():
    assert '@format("uuid")' in _read(_MODELS)


def test_payload_model_matches_the_v170_template():
    """The deferred move keeps the spine and one payload jsonb column."""
    models = _read(_MODELS)
    assert "model WorkRequestPayload" in models
    assert "payload jsonb" in models
    assert "V170" in models
    assert "withdrawn" in models


def test_no_bare_model_property():
    import re
    assert not re.search(r"^\s+model\??\s*:", _read(_MODELS), re.MULTILINE)


# ── compile ─────────────────────────────────────────────────────────────────

def _toolchain():
    for d in (_TSP_V1, Path("/home/codex/dev/nexus/typespec/v1")):
        npx = d / "node_modules" / ".bin" / "tsp"
        if npx.exists():
            return d, str(npx)
    if shutil.which("tsp"):
        return Path.cwd(), "tsp"
    return None, None


def test_compile_workrequest_contract():
    tsp_dir, tsp_bin = _toolchain()
    if not tsp_bin:
        pytest.skip(skip_reason(
            missing_tools("tsp"),
            "the TypeSpec toolchain is not locatable (run npm install in typespec/v1)",
        ))
    result = subprocess.run(
        [tsp_bin, "compile", str(_MAIN), "--no-emit"],
        cwd=str(tsp_dir), capture_output=True, text=True, timeout=240,
    )
    assert result.returncode == 0, f"workrequest contract failed to compile:\n{result.stdout}\n{result.stderr}"
