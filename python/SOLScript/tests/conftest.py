"""Shared fixtures for SOLScript tests."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any, Dict, List

import pytest

from solscript import (
    Concept,
    ConceptAttribute,
    ConceptRelationship,
    ConceptStateTransition,
    Disposition,
    Entity,
    Expression,
    ExpressionKind,
    FunctionBinding,
    Operator,
    Proposition,
    ResolutionInterpreter,
    Rule,
    RuleType,
    Severity,
)
from solscript.expression_compiler import ExpressionCompiler


def _uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _ensure_legacy_event_loop():
    """Guarantee a usable global event loop for legacy asyncio callers.

    Some tests (pytest-asyncio mode=strict, asyncio.run, private-loop helpers)
    leave the process-global loop unset or set-and-closed when they finish.
    Legacy helpers in this package call the deprecated
    ``asyncio.get_event_loop().run_until_complete(...)`` pattern, which raises
    ``RuntimeError: There is no current event loop`` once the global loop has
    been unset. This fixture makes sure every test starts with a healthy loop
    and removes it afterwards so no closed loop is ever left behind.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    asyncio.set_event_loop(None)
    loop.close()


@pytest.fixture
def interp() -> ResolutionInterpreter:
    """A fresh interpreter with no data loaded."""
    return ResolutionInterpreter()


@pytest.fixture
def wr_concept(interp: ResolutionInterpreter) -> Concept:
    """A WorkRequest concept with status (state attr) and title."""
    cid = _uid()
    concept = Concept(id=cid, name="WorkRequest", description="A work request")
    interp.add_concept(concept)

    status_attr = ConceptAttribute(
        id=_uid(),
        concept_id=cid,
        name="status",
        description="Current status",
        value_type="text",
        is_state_attribute=True,
        allowed_values=["DRAFT", "APPROVED", "DISPATCHED", "COMPLETED", "CANCELLED"],
    )
    concept.attributes[status_attr.id] = status_attr

    title_attr = ConceptAttribute(
        id=_uid(),
        concept_id=cid,
        name="title",
        description="Title",
        value_type="text",
        is_state_attribute=False,
    )
    concept.attributes[title_attr.id] = title_attr

    return concept


@pytest.fixture
def wr_entity(interp: ResolutionInterpreter, wr_concept: Concept) -> Entity:
    """A DRAFT WorkRequest entity."""
    return interp.add_entity_by_concept_name(
        "WorkRequest",
        {"status": "DRAFT", "title": "Fix bug #123"},
        external_id="WR-001",
    )


@pytest.fixture
def wr_invariant(interp: ResolutionInterpreter, wr_concept: Concept) -> Rule:
    """Invariant rule: status attribute must not be null."""
    attr = next(
        a for a in wr_concept.attributes.values() if a.name == "status"
    )
    expr = Expression(
        id=_uid(),
        kind=ExpressionKind.ATTRIBUTE_REF,
        return_type="text",
        attribute_id=attr.id,
    )
    rule = Rule(
        id=_uid(),
        name="Status must not be null",
        rule_type=RuleType.INVARIANT,
        expression=expr,
        severity=Severity.HARD,
        concept_id=wr_concept.id,
    )
    wr_concept.invariants.append(rule)
    interp.rules[rule.id] = rule
    return rule


def _run(coro: Any) -> Any:
    return asyncio.get_event_loop().run_until_complete(coro)


class FakeRow(dict):
    """dict that also supports row["col"] lookup (asyncpg Record-like)."""

    def __getitem__(self, key: str) -> Any:
        return dict.__getitem__(self, key)


class FakeConn:
    """Minimal asyncpg connection: fetch() returns scripted responses."""

    def __init__(self, script: List[List[FakeRow]]) -> None:
        self._script = script

    async def fetch(self, sql: str, *args: Any) -> List[FakeRow]:
        if not self._script:
            return []
        return self._script.pop(0)

    async def close(self) -> None:
        return None


class BoomConn:
    """Connection that raises on every query (missing schema simulation)."""

    async def fetch(self, sql: str, *args: Any) -> List[FakeRow]:
        raise Exception('relation "shrapnel.field" does not exist')


class Ctx:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def __aenter__(self) -> Any:
        return self._conn

    async def __aexit__(self, *exc: Any) -> None:
        return None


class FakePool:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def acquire(self) -> Ctx:
        return Ctx(self._conn)


def _row(**kwargs: Any) -> FakeRow:
    return FakeRow(kwargs)
