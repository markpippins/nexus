"""DatabaseLoader representation scope (V217).

The SQL resolver owns the equivalence and ambiguity proofs
(bin/tests/test_v217_representation_bindings.py). These unit guards cover the
Python half:

- a concept with two executable representations and no explicit selection
  fails closed instead of loading both;
- an explicit selection narrows the read to one representation;
- an unknown read_adapter fails closed;
- before V217 (catalog probe absent/failing) the loader keeps the original
  resolution-only policy, so merged code works ahead of the staged migration.
"""

from __future__ import annotations

from typing import Any, List

import pytest

from solscript import ResolutionInterpreter
from solscript.database_loader import DatabaseLoader

from .conftest import _row, _run


class ProbeConn:
    """Connection whose scoped probe answer is scripted."""

    def __init__(self, reps: List[Any], scoped: bool = True) -> None:
        self._reps = reps
        self._scoped = scoped
        self.table_reads: List[str] = []

    async def fetchval(self, sql: str, *args: Any) -> bool:
        return self._scoped

    async def fetch(self, sql: str, *args: Any) -> List[Any]:
        if "FROM resolution.representation" in sql:
            reps = self._reps
            if "r.id = $" in sql:  # honour the explicit-selection predicate
                wanted = set(args)
                reps = [r for r in reps if r["id"] in wanted]
            return reps
        self.table_reads.append(sql)
        return [_row(id="e1", asset_id="asset:1")]


class NoProbeConn:
    """Pre-V217 / minimal connection: fetchval is unavailable."""

    def __init__(self, reps: List[Any]) -> None:
        self._reps = reps
        self.table_reads: List[str] = []

    async def fetchval(self, sql: str, *args: Any) -> bool:
        raise AttributeError("fetchval")

    async def fetch(self, sql: str, *args: Any) -> List[Any]:
        if "FROM resolution.representation" in sql:
            return self._reps
        self.table_reads.append(sql)
        return [_row(id="e1", asset_id="asset:1")]


class Pool:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return None

        return _Ctx()


def _rep(concept: str, schema: str, table: str, adapter: Any = None) -> Any:
    return _row(
        id=f"rep-{table}",
        concept_id=concept,
        schema_name=schema,
        table_name=table,
        read_adapter=adapter,
        identity_expression="id",
    )


def test_ambiguous_executable_representations_fail_closed() -> None:
    conn = ProbeConn([
        _rep("c1", "resolution", "a"),
        _rep("c1", "nebula", "b"),
    ])
    loader = DatabaseLoader(ResolutionInterpreter(), Pool(conn))
    with pytest.raises(RuntimeError, match="ambiguous executable representations"):
        _run(loader.load_entities())


def test_explicit_selection_narrows_to_one_representation() -> None:
    conn = ProbeConn([
        _rep("c1", "resolution", "a"),
        _rep("c1", "nebula", "b"),
    ])
    loader = DatabaseLoader(ResolutionInterpreter(), Pool(conn), representation_id="rep-a")
    _run(loader.load_entities())
    assert any("resolution.a" in sql for sql in conn.table_reads)
    assert not any("nebula.b" in sql for sql in conn.table_reads)


def test_unknown_read_adapter_fails_closed() -> None:
    conn = ProbeConn([_rep("c1", "resolution", "a", adapter="mongodb")])
    loader = DatabaseLoader(ResolutionInterpreter(), Pool(conn))
    with pytest.raises(RuntimeError, match="read_adapter"):
        _run(loader.load_entities())


def test_pre_v217_keeps_resolution_only_policy() -> None:
    conn = NoProbeConn([
        _rep("c1", "nebula", "a"),
        _rep("c2", "resolution", "b"),
    ])
    loader = DatabaseLoader(ResolutionInterpreter(), Pool(conn))
    _run(loader.load_entities())
    assert any("resolution.b" in sql for sql in conn.table_reads)
    assert not any("nebula.a" in sql for sql in conn.table_reads)
