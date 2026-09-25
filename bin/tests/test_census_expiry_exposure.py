"""Hermetic tests for bin/census_expiry_exposure.py (pure logic only:
the D-4 yield-group extraction). No psql, no network."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


C = _load("census_expiry_exposure", REPO / "bin" / "census_expiry_exposure.py")


def test_yield_groups_empty_when_no_exposure():
    assert C.yield_groups([]) == set()


def test_yield_groups_dedupes_by_plan_and_role():
    rows = [
        "8261653|builder|expired|expires 09-23 01:28",
        "8261653|builder|open|expires 09-23 01:28",
        "8261654|builder|open|expires 09-23 19:33",
    ]
    assert C.yield_groups(rows) == {"8261653|builder", "8261654|builder"}


def test_yield_groups_tolerates_malformed_rows():
    assert C.yield_groups(["garbage", "", "8261655|builder|open|expires x"]) == {
        "8261655|builder"}
