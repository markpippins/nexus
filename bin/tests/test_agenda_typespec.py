#!/usr/bin/env python3
"""Hermetic tests: agenda TypeSpec contract (design note phase 6).

Two layers, per the calendar precedent:

- STRUCTURE (always runs): inventory, namespace, model-only stance, root
  umbrella wiring, and pins for the decisions this contract encodes — the
  measured status vocabulary, the forum_agendas distinction, item identity vs
  source reference, JSONB-as-Record, bitemporal columns, and the storage
  profile being a documented hint rather than an invented decorator.
- COMPILE (real `tsp compile` when a toolchain is locatable: worktree, shared
  checkout, then PATH; explicit skip otherwise). node_modules is git-ignored,
  so a fresh checkout without deps SKIPS rather than fails, and the skip
  names itself.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

_SELF = Path(__file__).resolve().parent
_REPO = _SELF.parent.parent
_TSP_V1 = _REPO / "typespec" / "v1"
_MODELS = _TSP_V1 / "agenda" / "models.tsp"
_MAIN = _TSP_V1 / "agenda" / "main.tsp"
_ROOT_MAIN = _TSP_V1 / "main.tsp"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ── structure ───────────────────────────────────────────────────────────────

def test_contract_files_exist():
    assert _MODELS.is_file(), f"missing {_MODELS}"
    assert _MAIN.is_file(), f"missing {_MAIN}"


def test_namespace_and_import():
    models, main = _read(_MODELS), _read(_MAIN)
    assert "namespace org.nexus.agenda" in models
    assert "namespace org.nexus.agenda" in main
    assert 'import "./models.tsp";' in main


def test_route_free_stance():
    body = _read(_MODELS) + _read(_MAIN)
    for banned in ("@route", "@post", "@get", "@put", "@delete", "@patch"):
        assert banned not in body, f"model-only contract must not contain {banned}"
    assert "route-free" in body.lower()


def test_root_umbrella_imports_agenda():
    root = _read(_ROOT_MAIN)
    assert 'import "./agenda/main.tsp";' in root


def test_status_vocabulary_is_the_measured_one():
    """Live 2026-11-12: draft, specified. A union is exhaustive, so it is not
    widened to a speculative lifecycle no writer emits."""
    models = _read(_MODELS)
    assert 'Draft: "draft"' in models
    assert 'Specified: "specified"' in models
    assert "model AgendaStatus" not in models  # it is a union, not a model


def test_forum_agendas_is_explicitly_a_different_object():
    models = _read(_MODELS)
    assert "assembly.forum_agendas" in models
    assert "grouping" in models


def test_item_identity_is_not_its_source():
    models = _read(_MODELS)
    assert "model AgendaItem" in models
    assert "agendaId: string;" in models
    assert "sourceId?: string;" in models
    assert "sourceType?: AgendaItemSourceType" in models
    # the doc must say the reference is not the identity
    assert "never its identity" in models


def test_jsonb_fields_are_records_not_strings():
    models = _read(_MODELS)
    for field in ("overlapMatrix", "plannerConflicts", "plannerGaps", "metadata"):
        assert f"{field}?: Record<unknown>;" in models, f"{field} should be JSONB-shaped"


def test_inclusion_keeps_the_item():
    """included=false is a planner decision, not a delete."""
    models = _read(_MODELS)
    assert "included?: boolean;" in models
    assert "False keeps the item" in models


def test_bitemporal_columns_present():
    models = _read(_MODELS)
    for field in ("validFrom", "validUntil", "recordedOn", "recordedUntil"):
        assert f"{field}?: utcDateTime;" in models


def test_uuid_convention():
    assert '@format("uuid")' in _read(_MODELS)


def test_storage_profile_is_a_documented_hint_not_a_decorator():
    """TypeSpec has no @instanceStorage decorator; inventing one fails closed
    at compile time. The profile belongs in prose, so the prose may name it but
    no decorator application may exist."""
    body = _read(_MODELS) + _read(_MAIN)
    assert "@instanceStorage(" not in body
    assert "profile hint" in body.lower()


def test_no_bare_model_property():
    """`model` is a TypeSpec keyword; house precedent is @encodedName."""
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


def test_compile_agenda_contract():
    tsp_dir, tsp_bin = _toolchain()
    if not tsp_bin:
        pytest.skip("TypeSpec toolchain not locatable (npm install in typespec/v1)")
    # The toolchain may come from the shared checkout while this worktree owns
    # the contract; compile the worktree's absolute entry point so imports
    # resolve beside the file, not beside node_modules.
    result = subprocess.run(
        [tsp_bin, "compile", str(_MAIN), "--no-emit"],
        cwd=str(tsp_dir), capture_output=True, text=True, timeout=240,
    )
    assert result.returncode == 0, f"agenda contract failed to compile:\n{result.stdout}\n{result.stderr}"


def test_compile_root_umbrella_includes_agenda():
    """The umbrella needs THIS checkout's dependencies.

    A shared checkout's node_modules can be an older compiler (observed:
    1.13.0 against the pinned 1.16.0), which does not resolve @typespec/http
    decorators and fails every service contract for reasons unrelated to this
    change. Fall back only when this checkout has its own install; otherwise
    skip and say why. CI installs typespec/v1, so this runs there.
    """
    own = _TSP_V1 / "node_modules" / ".bin" / "tsp"
    if not own.exists():
        pytest.skip(
            "root-umbrella compile needs this checkout's typespec/v1/node_modules "
            "(run npm install); the shared checkout may carry a different compiler version"
        )
    result = subprocess.run(
        [str(own), "compile", str(_ROOT_MAIN), "--no-emit"],
        cwd=str(_TSP_V1), capture_output=True, text=True, timeout=240,
    )
    assert result.returncode == 0, f"root umbrella failed to compile:\n{result.stdout}\n{result.stderr}"
