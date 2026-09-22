"""E8.1 hermetic loader tests (no live database).

Exercises ``DatabaseLoader.load_propositions`` against a fake asyncpg pool
that reproduces the real resolution-schema shapes:

- disposition vocabulary from ``resolution.concept_attribute_value`` (via
  the Proposition/disposition attribute join);
- ``resolution.proposition_assertion`` rows joined to ``resolution.rule``;
- ``resolution.proposition_frame_value`` rows;
- ``resolution.proposition`` rows.

The gate acceptance criteria: a DB-loaded framed proposition with a failing
assertion evaluates REJECTED (not ASSERTED), and frame-gate outcomes
(context_required / context_mismatch) fire from DB-loaded frame values.
"""
from __future__ import annotations

import asyncio
import unittest
import uuid
from typing import Any, Dict, List

from solscript.database_loader import DatabaseLoader
from solscript.interpreter import ResolutionInterpreter
from solscript.models import (
    Concept,
    ConceptAttribute,
    Disposition,
    Entity,
    Expression,
    ExpressionKind,
    FrameDimension,
    FrameDimensionValue,
    Operator,
    Proposition,
    Rule,
    RuleType,
    Severity,
)


def _uid() -> str:
    return str(uuid.uuid4())


class _FakeResult:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeConnection:
    def __init__(self, tables: Dict[str, List[Dict[str, Any]]]):
        self._tables = tables

    async def fetch(self, sql: str, *params: Any) -> List[Dict[str, Any]]:
        if "semantic_type_required_dimension" in sql:
            return self._tables.get("required_dimensions", [])
        if "concept_attribute_value" in sql:
            return self._tables["disp_values"]
        if "proposition_assertion" in sql:
            return self._tables["assertions"]
        if "proposition_frame_value pfv" in sql:
            return self._tables["frames"]
        if "FROM resolution.proposition" in sql:
            return self._tables["propositions"]
        raise AssertionError(f"unexpected query: {sql[:80]}")


class _FakePool:
    def __init__(self, tables: Dict[str, List[Dict[str, Any]]]):
        self._tables = tables

    def acquire(self):
        conn = _FakeConnection(self._tables)
        return self

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_args):
        return False

    def __init__(self, tables):  # noqa: E801 — keep the simple shape
        self._tables = tables
        self._conn = _FakeConnection(tables)

    def acquire(self):  # context-manager returning the connection
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool._conn

            async def __aexit__(self, *_args):
                return False

        return _Ctx()


class _FakePoolBase:
    pass


def _make_pool(tables: Dict[str, List[Dict[str, Any]]]) -> Any:
    conn = _FakeConnection(tables)

    class _Ctx:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *_args):
            return False

    class _Pool:
        def acquire(self):
            return _Ctx()

    return _Pool()


def _build_interp_with_rule(*, rule_passes: bool) -> Any:
    """Interpreter with one concept, entity, and a rule over entity.status."""
    interp = ResolutionInterpreter()
    concept = Concept(id="concept-1", name="Fixture", description=None)
    attr = ConceptAttribute(
        id="attr-1",
        concept_id=concept.id,
        name="status",
        description=None,
        value_type="text",
        is_state_attribute=True,
        allowed_values=["open", "closed"],
        default_value="open",
    )
    concept.attributes[attr.id] = attr
    interp.concepts[concept.id] = concept
    entity = Entity(
        id="entity-1",
        concept_id=concept.id,
        attributes={"status": "closed" if not rule_passes else "open"},
        external_id="fixture-entity",
    )
    interp.entities[entity.id] = entity

    expr = Expression(
        id=_uid(),
        kind=ExpressionKind.OPERATOR,
        operator=Operator.EQ,
        return_type="boolean",
        operands=[
            Expression(
                id=_uid(),
                kind=ExpressionKind.ATTRIBUTE_REF,
                return_type="text",
                attribute_id=attr.id,
            ),
            Expression(
                id=_uid(),
                kind=ExpressionKind.LITERAL,
                return_type="text",
                literal_value="open",
            ),
        ],
    )
    rule = Rule(
        id="rule-1",
        name="status_is_open",
        rule_type=RuleType.INVARIANT,
        expression=expr,
        severity=Severity.HARD,
        concept_id=concept.id,
    )
    interp.rules[rule.id] = rule
    return interp, concept, entity, rule


# Disposition vocabulary shaped like resolution.concept_attribute_value rows.
def _disp_value(value: str) -> Dict[str, Any]:
    return {"id": _uid(), "value": value}


class LoaderPropositionTests(unittest.TestCase):
    def _tables(self, *, disposition_value_id=None, rule_exists=True, frames=True):
        disp_pending = _disp_value("Pending")
        disp_asserted = _disp_value("Asserted")
        dim_id = _uid()
        dim_val_id = _uid()
        prop_id = "fc000000-0000-4000-8000-0000000000c1"
        tables = {
            "disp_values": [disp_pending, disp_asserted],
            "assertions": (
                [
                    {
                        "proposition_id": prop_id,
                        "rule_id": "rule-1",
                        "rule_exists": "rule-1" if rule_exists else None,
                    }
                ]
                if rule_exists is not None
                else []
            ),
            "frames": (
                [
                    {
                        "id": _uid(),
                        "proposition_id": prop_id,
                        "dimension_id": dim_id,
                        "reference_value_id": dim_val_id,
                        "scalar_value": None,
                    }
                ]
                if frames
                else []
            ),
            "propositions": [
                {
                    "id": uuid.UUID(prop_id),
                    "title": "fc — context gate four-outcome test",
                    "description": None,
                    "asset_concept_id": uuid.UUID(_uid()),
                    "subject_entity_id": uuid.UUID("00000000-0000-0000-0000-000000000001"),
                    "disposition_value_id": disposition_value_id or uuid.UUID(disp_pending["id"]),
                    "value": None,
                    "grounding_status_value_id": None,
                    "semantic_type_id": uuid.UUID("e3cf4625-e7fe-4154-9f12-5cb9688efa7b"),
                }
            ],
        }
        tables["required_dimensions"] = [
            {
                "semantic_type_id": "e3cf4625-e7fe-4154-9f12-5cb9688efa7b",
                "dimension_id": dim_id,
            }
        ]
        return tables, dim_id, dim_val_id

    def test_disposition_comes_from_vocabulary_not_default(self):
        interp, _c, _e, _r = _build_interp_with_rule(rule_passes=True)
        tables, _dim, _dv = self._tables()
        loader = DatabaseLoader(interp, _make_pool(tables))
        asyncio.get_event_loop().run_until_complete(loader.load_propositions())
        prop = interp.propositions["fc000000-0000-4000-8000-0000000000c1"]
        self.assertEqual(prop.disposition, Disposition.PENDING)
        self.assertEqual(loader.load_report["disposition_vocabulary_size"], 2)

    def test_assertions_are_loaded_from_db(self):
        interp, _c, _e, rule = _build_interp_with_rule(rule_passes=True)
        tables, _dim, _dv = self._tables()
        loader = DatabaseLoader(interp, _make_pool(tables))
        asyncio.get_event_loop().run_until_complete(loader.load_propositions())
        prop = interp.propositions["fc000000-0000-4000-8000-0000000000c1"]
        self.assertEqual(len(prop.assertions), 1)
        self.assertIs(prop.assertions[0], interp.rules["rule-1"])

    def test_frame_values_are_loaded_from_db(self):
        interp, _c, _e, _r = _build_interp_with_rule(rule_passes=True)
        tables, dim_id, dv_id = self._tables()
        loader = DatabaseLoader(interp, _make_pool(tables))
        asyncio.get_event_loop().run_until_complete(loader.load_propositions())
        prop = interp.propositions["fc000000-0000-4000-8000-0000000000c1"]
        self.assertEqual(len(prop.frame_values), 1)
        self.assertEqual(prop.frame_values[0].dimension_id, dim_id)
        self.assertEqual(prop.frame_values[0].reference_value_id, dv_id)

    def test_failing_assertion_proposition_evaluates_rejected(self):
        """THE E8.1 gate: DB-loaded failing assertion must not be ASSERTED.

        The fixture proposition is framed, so evaluation without a context
        correctly stops at the gate (context_required, disposition None).
        The assertion verdict is verified by evaluating with a matching
        context value so the gate passes and the assertion runs.
        """
        interp, concept, entity, _r = _build_interp_with_rule(rule_passes=False)
        tables, dim_id, dv_id = self._tables()
        loader = DatabaseLoader(interp, _make_pool(tables))
        asyncio.get_event_loop().run_until_complete(loader.load_propositions())
        prop = interp.propositions["fc000000-0000-4000-8000-0000000000c1"]
        # Register the frame dimension + value so the gate can match context.
        interp.frame_dimensions[dim_id] = FrameDimension(
            id=dim_id, name="migration_phase", description=None, value_kind="governed_reference"
        )
        interp.frame_dimension_values[dv_id] = FrameDimensionValue(
            id=dv_id, dimension_id=dim_id, value="pre_migration", description=None
        )
        disposition, all_passed, context_status = interp.evaluate_proposition(
            prop, context={"migration_phase": "pre_migration"}
        )
        self.assertEqual(context_status, "scoped")
        self.assertEqual(disposition, Disposition.REJECTED)
        self.assertFalse(all_passed)

    def test_context_required_fires_from_loaded_frames(self):
        """Framed proposition + no context → context_required (gate fires)."""
        interp, _c, _e, _r = _build_interp_with_rule(rule_passes=True)
        tables, _dim, _dv = self._tables()
        loader = DatabaseLoader(interp, _make_pool(tables))
        asyncio.get_event_loop().run_until_complete(loader.load_propositions())
        prop = interp.propositions["fc000000-0000-4000-8000-0000000000c1"]
        # Register the type-level requirement and dimension so the E8.4
        # class-level gate can look it up.
        interp.register_semantic_type_required_dimension(
            str(prop.semantic_type_id), prop.frame_values[0].dimension_id
        )
        interp.frame_dimensions[_uid()] = FrameDimension(
            id=list({prop.frame_values[0].dimension_id})[0],
            name="migration_phase",
            description=None,
            value_kind="governed_reference",
        )
        disposition, _passed, context_status = interp.evaluate_proposition(prop, context=None)
        self.assertIsNone(disposition)
        self.assertEqual(context_status, "context_required")

    def test_unknown_disposition_value_fails_loud_and_skips(self):
        interp, _c, _e, _r = _build_interp_with_rule(rule_passes=True)
        tables, _dim, _dv = self._tables()
        tables["propositions"][0]["disposition_value_id"] = uuid.UUID(_uid())  # not in vocabulary
        loader = DatabaseLoader(interp, _make_pool(tables))
        asyncio.get_event_loop().run_until_complete(loader.load_propositions())
        self.assertNotIn("fc000000-0000-4000-8000-0000000000c1", interp.propositions)
        self.assertEqual(loader.load_report["skipped"]["unknown_disposition"], 1)

    def test_missing_assertion_rule_skips_proposition(self):
        # The rule row exists in proposition_assertion but is absent from
        # interpreter.rules (load_rules subset/failed) → the proposition is
        # skipped, not loaded with partial assertions.
        interp, _c, _e, _r = _build_interp_with_rule(rule_passes=True)
        interp.rules.clear()  # simulate load_rules having not loaded rule-1
        tables, _dim, _dv = self._tables(rule_exists=True)
        loader = DatabaseLoader(interp, _make_pool(tables))
        asyncio.get_event_loop().run_until_complete(loader.load_propositions())
        self.assertNotIn("fc000000-0000-4000-8000-0000000000c1", interp.propositions)
        self.assertEqual(loader.load_report["skipped"]["missing_assertion_rule"], 1)

if __name__ == "__main__":
    unittest.main()
