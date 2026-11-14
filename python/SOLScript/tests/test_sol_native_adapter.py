"""Tests for the SolNativeAdapter (cutover 06).

Verifies the sol-native adapter produces normalized contract objects from
`sol`-shaped source rows (resolution.concept, shrapnel EAV, semantics.evidence)
with NO Nexus dependency. Uses a fake asyncpg-style pool with scripted rows.
"""

from __future__ import annotations

from typing import Any, List

import pytest

from solscript.adapters import (
    ContractConcept,
    ContractShrapnelFact,
    ContractSubject,
    SolNativeAdapter,
)

from .conftest import FakeConn, FakePool, _row, _run


def test_list_concepts_normalizes() -> None:
    conn = FakeConn(
        [
            [_row(id="c1", name="Bike", description="a bicycle")],
        ]
    )
    adapter = SolNativeAdapter(dsn="", pool=FakePool(conn))
    concepts = _run(adapter.list_concepts())
    assert len(concepts) == 1
    assert isinstance(concepts[0], ContractConcept)
    assert concepts[0].id == "c1"
    assert concepts[0].name == "Bike"


def test_list_subjects_reads_representation_table() -> None:
    # list_subjects first queries representations (returns one row), then
    # selects all from resolution.<table> (returns two subject rows).
    conn = FakeConn(
        [
            [_row(schema_name="resolution", table_name="bike")],
            [
                _row(id="s1", asset_id="asset:bike:1", external_id="b1", frame_material="steel"),
                _row(id="s2", asset_id="asset:bike:2", external_id="b2", frame_material="carbon"),
            ],
        ]
    )
    adapter = SolNativeAdapter(dsn="", pool=FakePool(conn))
    subjects = _run(adapter.list_subjects("concept-bike"))
    assert len(subjects) == 2
    assert all(isinstance(s, ContractSubject) for s in subjects)
    assert subjects[0].canonical_asset_id == "asset:bike:1"
    assert subjects[0].attributes["frame_material"] == "steel"


def test_list_subjects_unknown_read_adapter_fails_closed() -> None:
    conn = FakeConn(
        [[_row(schema_name="resolution", table_name="bike", read_adapter="mongodb")]]
    )
    adapter = SolNativeAdapter(dsn="", pool=FakePool(conn))
    with pytest.raises(RuntimeError, match="read_adapter"):
        _run(adapter.list_subjects("concept-bike"))


def test_list_subjects_ambiguous_executable_representations_fail_closed() -> None:
    conn = FakeConn(
        [[
            _row(schema_name="resolution", table_name="bike", read_adapter=None),
            _row(schema_name="nebula", table_name="bike", read_adapter=None),
        ]]
    )
    adapter = SolNativeAdapter(dsn="", pool=FakePool(conn))
    with pytest.raises(RuntimeError, match="ambiguous executable representations"):
        _run(adapter.list_subjects("concept-bike"))


def test_list_shrapnel_facts_reads_eav() -> None:
    # The adapter queries object_instance+oav+field+value, then typed value
    # extension tables per type code.
    conn = FakeConn(
        [
            [
                _row(object_id=1, created_at=None, field_name="tire_clearance", field_type_code=3,
                     value_type_code=3, value_id=10),
                _row(object_id=1, created_at=None, field_name="is_steel", field_type_code=4,
                     value_type_code=4, value_id=11),
            ],
            [_row(value=2.4)],  # value_double for id 10
            [_row(value=True)],  # value_boolean for id 11
        ]
    )
    adapter = SolNativeAdapter(dsn="", pool=FakePool(conn))
    facts = _run(adapter.list_shrapnel_facts())
    assert len(facts) == 1
    assert isinstance(facts[0], ContractShrapnelFact)
    assert facts[0].object_id == "1"
    assert facts[0].attributes["tire_clearance"] == 2.4
    assert facts[0].attributes["is_steel"] is True


def test_list_evidence_normalizes() -> None:
    conn = FakeConn(
        [
            [_row(id="e1", uri="src://a", excerpt="note", captured_at=None)],
        ]
    )
    adapter = SolNativeAdapter(dsn="", pool=FakePool(conn))
    ev = _run(adapter.list_evidence())
    assert len(ev) == 1
    assert ev[0].source == "src://a"
    assert ev[0].content == "note"


def test_adapter_implements_port() -> None:
    from solscript.adapters.contract import SolStoragePort

    adapter = SolNativeAdapter(dsn="", pool=None)
    assert isinstance(adapter, SolStoragePort)  # runtime_checkable Protocol


if __name__ == "__main__":
    pytest.main([__file__, "-v"])