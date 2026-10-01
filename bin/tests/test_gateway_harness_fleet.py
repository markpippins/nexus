#!/usr/bin/env python3
"""Fleet guard: every moleculer dispatch twin's jest suite carries the
live-gateway harness block (LIVE-GATEWAY HARNESS marker).

Closes the CI gap that let the fleet-wide $req/$res defect class hide
(2026-09-30 wave, PRs #688/#690): the twins' supertest suites drive the
verbatim Express app directly, so nothing exercised the moleculer-web
gateway in CI. The harness block boots the real broker + ApiService + twin
service on an ephemeral port and asserts matched-route 200 (proving the
onBeforeCall $req/$res stash), the twin's exact 404 body on unmatched
paths, and wrong-method 404.

This guard pins, per twin (tackle, conduit, harness, peb, execution, aegis):
  - test/<twin>.test.ts carries the LIVE-GATEWAY HARNESS marker block
  - the block sets SERVICE_PORT="0" BEFORE the dynamic service imports
  - the block requires BOTH the gateway (services/api.service) and the
    twin service module — dropping either defeats the harness
  - the twin boots the real broker with transporter: null (hermetic)
Plus, fleet-wide:
  - execution's package-lock.json is TRACKED in git (tester finding,
    record acfcee27: no lockfile -> no npm ci -> excluded from the CI
    twin matrix)
  - .github/workflows/broker-e2e.yml's twin matrix names execution

Hermetic: filesystem reads + one `git ls-files` call. No network, no DB.

Run:
  python3 -m pytest bin/tests/test_gateway_harness_fleet.py -v
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MARKER = "LIVE-GATEWAY HARNESS (hermetic)"
DISPATCH_TWINS = ["tackle", "conduit", "harness", "peb", "execution", "aegis"]


def twin_test_file(twin: str) -> Path:
    return REPO / "moleculer" / twin / "test" / f"{twin}.test.ts"


def block(twin: str) -> str:
    src = twin_test_file(twin).read_text(encoding="utf-8")
    start = src.find(MARKER)
    assert start != -1, f"{twin}: harness marker missing"
    return src[start:]


def tracked(path: str) -> bool:
    out = subprocess.run(
        ["git", "ls-files", path], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.strip()
    return bool(out)


import pytest


@pytest.mark.parametrize("twin", DISPATCH_TWINS)
def test_twin_carries_harness_block(twin: str):
    src = twin_test_file(twin).read_text(encoding="utf-8")
    assert MARKER in src, (
        f"moleculer/{twin}: live-gateway harness block missing — the "
        f"$req/$res defect class is invisible to this twin's CI again"
    )


@pytest.mark.parametrize("twin", DISPATCH_TWINS)
def test_harness_sets_ephemeral_port_before_imports(twin: str):
    blk = block(twin)
    port_line = 'process.env.SERVICE_PORT = "0"'
    assert port_line in blk, f"{twin}: harness must pin SERVICE_PORT=0 (ephemeral)"
    assert blk.index(port_line) < blk.index('require("../services/api.service")'), (
        f"{twin}: SERVICE_PORT must be set BEFORE the api.service import — the "
        f"gateway captures settings.port at construction"
    )


@pytest.mark.parametrize("twin", DISPATCH_TWINS)
def test_harness_boots_gateway_and_twin_service(twin: str):
    blk = block(twin)
    assert 'require("../services/api.service").default' in blk, (
        f"{twin}: harness must boot the real gateway (services/api.service)"
    )
    assert f'require("../services/{twin}.service").default' in blk, (
        f"{twin}: harness must boot the twin service module"
    )


@pytest.mark.parametrize("twin", DISPATCH_TWINS)
def test_harness_is_hermetic(twin: str):
    blk = block(twin)
    assert "transporter: null" in blk, (
        f"{twin}: broker must run with transporter: null (no NATS in CI)"
    )
    assert "broker.stop()" in blk, f"{twin}: harness must stop the broker (no leaked handles)"


def test_matched_route_asserts_reqres_stash():
    for twin in DISPATCH_TWINS:
        blk = block(twin)
        assert "$req/$res stash" in blk, (
            f"{twin}: matched-route test must document the $req/$res proof"
        )


def test_execution_lockfile_is_tracked():
    rel = "moleculer/execution/package-lock.json"
    assert tracked(rel), (
        f"{rel} is not tracked in git — without a lockfile `npm ci` fails and "
        f"execution is excluded from the CI twin matrix (tester finding, "
        f"record acfcee27)"
    )


def test_broker_e2e_matrix_names_execution():
    wf = (REPO / ".github" / "workflows" / "broker-e2e.yml").read_text(encoding="utf-8")
    assert "service: [tackle, conduit, harness, peb, execution, aegis]" in wf, (
        "broker-e2e.yml twin matrix must include execution"
    )
