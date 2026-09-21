"""Database loader — populates the interpreter from the resolution PostgreSQL schema."""

from __future__ import annotations

import logging
import os
import uuid
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

# Config-namespace for in-memory fallback ids (e.g. a ShrapnelFact concept
# created by the loader when the DB carries no resolution row for it).
# Domain-agnostic per directive d6ffdc06: derived from the same env-overridable
# namespace state_bridge.py uses, never a database literal.
SOLSCRIPT_MODEL_NAMESPACE = os.environ.get(
    "SOLSCRIPT_CANDIDATE_STATE_NAMESPACE", "solscript:candidate-state"
)


def _ns_uuid(seed: str) -> str:
    """Deterministic UUID in the configured namespace (stable across runs)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{SOLSCRIPT_MODEL_NAMESPACE}:{seed}"))

from .models import (
    AttributeBinding,
    Concept,
    ConceptAttribute,
    ConceptRelationship,
    ConceptStateTransition,
    Entity,
    Expression,
    ExpressionKind,
    FrameDimension,
    FrameDimensionMeaning,
    FrameDimensionValue,
    Operator,
    Proposition,
    PropositionFrameValue,
    Quantifier,
    RelationshipBinding,
    Representation,
    RepresentationIdentity,
    Rule,
    RuleType,
    Severity,
)

if TYPE_CHECKING:
    from .interpreter import ResolutionInterpreter

try:
    import asyncpg  # type: ignore[import-untyped]
except ImportError:
    asyncpg = None  # type: ignore[assignment]


class DatabaseLoader:
    """Load schema and data from the resolution database into the interpreter."""

    def __init__(self, interpreter: ResolutionInterpreter, pool: Any) -> None:
        self.interpreter = interpreter
        self.pool = pool
        self.load_report: Dict[str, Any] = {}

    async def load_all(self) -> None:
        """Load all schema and data from the database."""
        await self.load_concepts()
        await self.load_attributes()
        await self.load_relationships()
        await self.load_expressions()
        await self.load_state_transitions()
        await self.load_rules()
        await self.load_frame_dimensions()
        await self.load_propositions()
        await self.load_frame_dimension_meanings()
        await self.load_entities()
        await self.load_shrapnel_facts()

    # ── Shrapnel facts (EAV object store) ───────────────────────

    # Physical column names of the typed value extension tables.  The EAV
    # store keeps the type in shrapnel.value.value_type_code and the actual
    # payload in the matching shrapnel.value_<type> row (1:1 by id).
    _SHRAPNEL_TYPE_COLUMNS: Dict[int, str] = {
        1: "value_long",         # bigint
        2: "value_string",       # varchar(255)
        3: "value_double",       # double precision
        4: "value_boolean",      # boolean
        5: "value_timestamp",    # timestamptz
        6: "value_jsonb",        # jsonb
        7: "value_uuid",         # uuid
    }

    async def load_shrapnel_facts(
        self, concept_name: str = "ShrapnelFact"
    ) -> None:
        """Materialize shrapnel EAV objects as interpreter entities.

        Shrapnel is the standalone "facts" datastore (fields/objects/values
        in an EAV layout).  Resolution reasons *about* those facts, so each
        shrapnel object becomes an Entity whose attributes are the object's
        field values (keyed by property_name).  Objects are attached to a
        concept named `concept_name` so query_builder/inference can reference
        them like any other resolution entity.

        The load is best-effort: if the shrapnel schema is absent or any
        object is malformed, that part is skipped without failing the whole
        load (mirrors the external-projection tolerance in load_entities).
        """
        async with self.pool.acquire() as conn:
            try:
                field_rows = await conn.fetch(
                    "SELECT id, name, property_name, field_type_code "
                    "FROM shrapnel.field"
                )
            except Exception:
                # shrapnel schema not present (or not migrated) — fine
                return

            fields: Dict[int, Dict[str, Any]] = {}
            for fr in field_rows:
                fields[fr["id"]] = {
                    "name": fr["name"],
                    "property_name": fr["property_name"],
                    "field_type_code": fr["field_type_code"],
                }
            if not fields:
                return

            # All objects and their attribute bindings in one shot.
            object_rows = await conn.fetch(
                "SELECT o.id AS object_id, oav.field_id, oav.value_id, "
                "v.value_type_code "
                "FROM shrapnel.object_instance o "
                "JOIN shrapnel.object_attribute_value oav ON oav.object_id = o.id "
                "JOIN shrapnel.value v ON v.id = oav.value_id"
            )

            # Pull typed values per extension table (best-effort per table).
            typed: Dict[Tuple[int, str], Any] = {}
            for table in self._SHRAPNEL_TYPE_COLUMNS.values():
                try:
                    rows = await conn.fetch(f"SELECT id, value FROM shrapnel.{table}")
                except Exception:
                    continue
                for row in rows:
                    typed[(row["id"], table)] = row["value"]

            # Assemble per-object attribute dicts.
            objects: Dict[int, Dict[str, Any]] = {}
            for orow in object_rows:
                oid = orow["object_id"]
                field = fields.get(orow["field_id"])
                if not field:
                    continue
                value = None
                table = self._SHRAPNEL_TYPE_COLUMNS.get(orow["value_type_code"])
                if table is not None:
                    value = typed.get((orow["value_id"], table))
                attr_key = field["property_name"] or field["name"]
                objects.setdefault(oid, {})[attr_key] = value

            if not objects:
                return

            # Register entities under the given concept (create if absent).
            # The fallback id is config-namespace-derived, never a database
            # literal (directive d6ffdc06 — no nexus ids in solscript).
            concept = self.interpreter.get_concept_by_name(concept_name)
            if not concept:
                concept = Concept(
                    id=_ns_uuid(f"concept:{concept_name}"),
                    name=concept_name,
                    description="Shrapnel EAV fact objects (standalone facts store)",
                )
                self.interpreter.add_concept(concept)

            for oid, attrs in objects.items():
                entity = Entity(
                    id=f"shrapnel:{oid}",
                    concept_id=concept.id,
                    attributes=attrs,
                    external_id=str(oid),
                )
                self.interpreter.entities[entity.id] = entity

    # ── Concepts ─────────────────────────────────────────────────

    async def load_concepts(self) -> None:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, name, description FROM resolution.concept "
                "WHERE expired_at IS NULL"
            )
            for row in rows:
                concept = Concept(
                    id=str(row["id"]),
                    name=row["name"],
                    description=row["description"],
                )
                self.interpreter.add_concept(concept)

    # ── Attributes ───────────────────────────────────────────────

    async def load_attributes(self) -> None:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT ca.id, ca.concept_id, ca.name, ca.description, "
                "ca.value_type, ca.is_state_attribute, "
                "cab.schema_name, cab.table_name, cab.column_name "
                "FROM resolution.concept_attribute ca "
                "LEFT JOIN resolution.concept_attribute_binding cab "
                "ON cab.attribute_id = ca.id "
                "WHERE ca.concept_id IN "
                "(SELECT id FROM resolution.concept WHERE expired_at IS NULL)"
            )
            for row in rows:
                binding = None
                if row["schema_name"] and row["table_name"] and row["column_name"]:
                    binding = AttributeBinding(
                        schema_name=row["schema_name"],
                        table_name=row["table_name"],
                        column_name=row["column_name"],
                    )
                attr = ConceptAttribute(
                    id=str(row["id"]),
                    concept_id=str(row["concept_id"]),
                    name=row["name"],
                    description=row["description"],
                    value_type=row["value_type"],
                    is_state_attribute=row["is_state_attribute"],
                    binding=binding,
                )
                values = await conn.fetch(
                    "SELECT value FROM resolution.concept_attribute_value "
                    "WHERE attribute_id = $1",
                    row["id"],
                )
                attr.allowed_values = [v["value"] for v in values]

                concept = self.interpreter.get_concept(str(row["concept_id"]))
                if concept:
                    concept.attributes[attr.id] = attr

    # ── Relationships ────────────────────────────────────────────

    async def load_relationships(self) -> None:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT cr.id, cr.from_concept_id, cr.to_concept_id, "
                "cr.relationship_type, cr.path, cr.notes, "
                "crb.from_schema, crb.from_table, crb.from_column, "
                "crb.to_schema, crb.to_table, crb.to_column "
                "FROM resolution.concept_relationship cr "
                "LEFT JOIN resolution.concept_relationship_binding crb "
                "  ON crb.concept_relationship_id = cr.id "
                "WHERE cr.expired_at IS NULL"
            )
            for row in rows:
                binding = None
                # Hydrate the binding whenever the binding ROW exists — even
                # attribute-level bindings (empty schema/table, columns only),
                # which the candidate-state model uses. The LEFT JOIN gives NULL
                # for a missing row; a present row is always non-NULL.
                if row["from_column"] is not None:
                    binding = RelationshipBinding(
                        from_schema=row["from_schema"],
                        from_table=row["from_table"],
                        from_column=row["from_column"],
                        to_schema=row["to_schema"],
                        to_table=row["to_table"],
                        to_column=row["to_column"],
                    )
                rel = ConceptRelationship(
                    id=str(row["id"]),
                    from_concept_id=str(row["from_concept_id"]),
                    to_concept_id=str(row["to_concept_id"]),
                    relationship_type=row["relationship_type"],
                    path=row["path"],
                    notes=row["notes"],
                    binding=binding,
                )
                self.interpreter.relationships[rel.id] = rel
                concept = self.interpreter.get_concept(rel.from_concept_id)
                if concept:
                    concept.relationships[rel.id] = rel

    # ── Expressions ──────────────────────────────────────────────

    async def load_expressions(self) -> None:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, kind, operator, literal_value, attribute_id, "
                "function_name, return_type, label, "
                "concept_relationship_id, quantifier, "
                "referenced_proposition_id, proposition_ref_field "
                "FROM resolution.expression"
            )
            expressions: Dict[str, Expression] = {}
            for row in rows:
                expr = Expression(
                    id=str(row["id"]),
                    kind=ExpressionKind(row["kind"]),
                    return_type=row["return_type"],
                    operator=Operator(row["operator"]) if row["operator"] else None,
                    literal_value=row["literal_value"],
                    attribute_id=str(row["attribute_id"]) if row["attribute_id"] else None,
                    function_name=row["function_name"],
                    concept_relationship_id=(
                        str(row["concept_relationship_id"])
                        if row["concept_relationship_id"]
                        else None
                    ),
                    quantifier=(
                        Quantifier(row["quantifier"]) if row["quantifier"] else None
                    ),
                    referenced_proposition_id=(
                        str(row["referenced_proposition_id"])
                        if row["referenced_proposition_id"]
                        else None
                    ),
                    proposition_ref_field=row["proposition_ref_field"],
                    label=row["label"],
                )
                expressions[expr.id] = expr
                self.interpreter.expressions[expr.id] = expr

            operand_rows = await conn.fetch(
                "SELECT parent_expression_id, child_expression_id, position "
                "FROM resolution.expression_operand "
                "ORDER BY parent_expression_id, position"
            )
            for row in operand_rows:
                parent = self.interpreter.expressions.get(str(row["parent_expression_id"]))
                child = self.interpreter.expressions.get(str(row["child_expression_id"]))
                if parent and child:
                    pos = row["position"] - 1
                    while len(parent.operands) <= pos:
                        parent.operands.append(
                            Expression(
                                id="placeholder",
                                kind=ExpressionKind.LITERAL,
                                return_type="any",
                            )
                        )
                    parent.operands[pos] = child

    # ── Rules ────────────────────────────────────────────────────

    async def load_state_transitions(self) -> None:
        """Load concept state transitions (resolution.concept_state_transition).

        `from_value_id`/`to_value_id` reference value rows in
        `resolution.concept_attribute_value`; we join to resolve the display
        strings the interpreter compares against state-attribute values.
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT st.id, st.concept_id, st.from_value_id, st.to_value_id, "
                "st.name, st.notes "
                "FROM resolution.concept_state_transition st "
                "WHERE st.expired_at IS NULL"
            )
            if not rows:
                return
            # Resolve value ids -> strings
            value_rows = await conn.fetch(
                "SELECT id, value FROM resolution.concept_attribute_value"
            )
            value_by_id = {str(vr["id"]): vr["value"] for vr in value_rows}
            for row in rows:
                trans = ConceptStateTransition(
                    id=str(row["id"]),
                    concept_id=str(row["concept_id"]),
                    from_value=(
                        value_by_id.get(str(row["from_value_id"]))
                        if row["from_value_id"]
                        else None
                    ),
                    to_value=(
                        value_by_id.get(str(row["to_value_id"]))
                        if row["to_value_id"]
                        else ""
                    ),
                    name=row["name"],
                    notes=row["notes"],
                )
                self.interpreter.state_transitions[trans.id] = trans
                concept = self.interpreter.get_concept(str(row["concept_id"]))
                if concept:
                    concept.state_transitions.append(trans)

    async def load_rules(self) -> None:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, name, rule_type, expression_id, severity, "
                "concept_id, concept_relationship_id, representation_id, "
                "notes, state_transition_id, is_relational_check "
                "FROM resolution.rule WHERE expired_at IS NULL"
            )
            for row in rows:
                rule = Rule(
                    id=str(row["id"]),
                    name=row["name"],
                    rule_type=RuleType(row["rule_type"]),
                    expression=(
                        self.interpreter.expressions.get(str(row["expression_id"]))
                        if row["expression_id"]
                        else None
                    ),
                    severity=Severity(row["severity"]),
                    concept_id=str(row["concept_id"]) if row["concept_id"] else None,
                    concept_relationship_id=(
                        str(row["concept_relationship_id"])
                        if row["concept_relationship_id"]
                        else None
                    ),
                    representation_id=(
                        str(row["representation_id"])
                        if row["representation_id"]
                        else None
                    ),
                    state_transition_id=(
                        str(row["state_transition_id"])
                        if row["state_transition_id"]
                        else None
                    ),
                    notes=row["notes"],
                    is_relational_check=row["is_relational_check"],
                )
                self.interpreter.rules[rule.id] = rule

                if rule.concept_id:
                    concept = self.interpreter.get_concept(rule.concept_id)
                    if concept:
                        if rule.rule_type == RuleType.INVARIANT:
                            concept.invariants.append(rule)
                        elif rule.rule_type == RuleType.DERIVATION:
                            concept.derivations.append(rule)
                elif rule.concept_relationship_id:
                    rel = self.interpreter.get_relationship(rule.concept_relationship_id)
                    if rel and rule.rule_type == RuleType.CONDITIONAL:
                        rel.conditionals.append(rule)
                elif rule.state_transition_id:
                    trans = self.interpreter.get_state_transition(rule.state_transition_id)
                    if trans and rule.rule_type == RuleType.GUARD:
                        trans.guards.append(rule)

    # ── Frame dimensions (v31) ──────────────────────────────────

    async def load_frame_dimensions(self) -> None:
        """Load frame_dimension, frame_dimension_value, and proposition_frame_value."""
        async with self.pool.acquire() as conn:
            fd_rows = await conn.fetch(
                "SELECT id, name, description, value_kind, scalar_type "
                "FROM resolution.frame_dimension"
            )
            for row in fd_rows:
                dim = FrameDimension(
                    id=str(row["id"]),
                    name=row["name"],
                    description=row["description"],
                    value_kind=row["value_kind"],
                    scalar_type=row["scalar_type"],
                )
                self.interpreter.frame_dimensions[dim.id] = dim

            fdv_rows = await conn.fetch(
                "SELECT id, dimension_id, value, description "
                "FROM resolution.frame_dimension_value"
            )
            for row in fdv_rows:
                val = FrameDimensionValue(
                    id=str(row["id"]),
                    dimension_id=str(row["dimension_id"]),
                    value=row["value"],
                    description=row["description"],
                )
                self.interpreter.frame_dimension_values[val.id] = val

            pfv_rows = await conn.fetch(
                "SELECT id, proposition_id, dimension_id, "
                "reference_value_id, scalar_value "
                "FROM resolution.proposition_frame_value"
            )
            for row in pfv_rows:
                pfv = PropositionFrameValue(
                    id=str(row["id"]),
                    proposition_id=str(row["proposition_id"]),
                    dimension_id=str(row["dimension_id"]),
                    reference_value_id=(
                        str(row["reference_value_id"])
                        if row["reference_value_id"]
                        else None
                    ),
                    scalar_value=row["scalar_value"],
                )
                self.interpreter.add_proposition_frame_value(pfv)

    # ── Propositions ─────────────────────────────────────────────

    async def load_propositions(self) -> None:
        """Load propositions with assertions, frame values, and real dispositions.

        E8.1 fixes three defects in the original loader:

        1. Disposition mapping — ``disposition_value_id`` is a FK to
           ``resolution.concept_attribute_value.value`` (text like
           'Asserted'/'Rejected'). The map is built from that table, not
           hardcoded; unknown values are counted and skipped LOUDLY
           (never silently defaulted to PROPOSED).
        2. Assertions — ``resolution.proposition_assertion`` rows (join to
           ``resolution.rule``) are loaded and attached. Without this every
           DB-loaded proposition had zero assertions and evaluated ASSERTED
           trivially.
        3. Frame values — ``resolution.proposition_frame_value`` rows are
           loaded and attached so the v31/v32 context gate can fire.

        Propositions whose disposition, assertion rule, or frame dimension
        could not be resolved are skipped and reported in
        ``self.load_report`` — never guessed.
        """
        async with self.pool.acquire() as conn:
            from .models import Disposition as Disp

            # ── 1. Disposition vocabulary: value-id → enum, from the DB ──
            disp_rows = await conn.fetch(
                "SELECT cav.id, cav.value "
                "FROM resolution.concept_attribute_value cav "
                "JOIN resolution.concept_attribute ca ON ca.id = cav.attribute_id "
                "JOIN resolution.concept c ON c.id = ca.concept_id "
                "WHERE c.name = 'Proposition' AND ca.name = 'disposition'"
            )
            disp_map: Dict[str, Disp] = {}
            for row in disp_rows:
                try:
                    disp_map[str(row["id"])] = Disp(str(row["value"]))
                except ValueError:
                    # Unknown vocabulary value: fail loud, skip that value.
                    logging.getLogger(__name__).warning(
                        "load_propositions: unknown disposition value %r (%s) — skipped",
                        row["value"], row["id"],
                    )

            # ── 2. Assertions: proposition_id → [Rule] ────────────────
            assertion_rows = await conn.fetch(
                "SELECT pa.proposition_id, pa.rule_id, r.id AS rule_exists "
                "FROM resolution.proposition_assertion pa "
                "LEFT JOIN resolution.rule r ON r.id = pa.rule_id"
            )
            assertions_by_prop: Dict[str, List[Rule]] = {}
            missing_rules: List[str] = []
            rule_ids: set = set()
            for row in assertion_rows:
                if row["rule_exists"] is None:
                    missing_rules.append(str(row["rule_id"]))
                    continue
                rule_ids.add(str(row["rule_id"]))
                assertions_by_prop.setdefault(str(row["proposition_id"]), []).append(
                    str(row["rule_id"])
                )
            # Materialize Rule objects for any assertion rules not already loaded.
            loaded_rule_ids = set(self.interpreter.rules.keys())
            unloadable = rule_ids - loaded_rule_ids
            if unloadable:
                # Rules must exist in interpreter.rules (loaded by load_rules);
                # missing ones mean load_rules ran against a subset or failed.
                logging.getLogger(__name__).warning(
                    "load_propositions: %d assertion rule(s) not present in "
                    "interpreter.rules — propositions referencing them are skipped",
                    len(unloadable),
                )

            # ── 3. Frame values: proposition_id → [PropositionFrameValue] ──
            frame_rows = await conn.fetch(
                "SELECT pfv.id, pfv.proposition_id, pfv.dimension_id, "
                "pfv.reference_value_id, pfv.scalar_value "
                "FROM resolution.proposition_frame_value pfv"
            )
            frames_by_prop: Dict[str, List[PropositionFrameValue]] = {}
            for row in frame_rows:
                frames_by_prop.setdefault(str(row["proposition_id"]), []).append(
                    PropositionFrameValue(
                        id=str(row["id"]),
                        proposition_id=str(row["proposition_id"]),
                        dimension_id=str(row["dimension_id"]),
                        reference_value_id=(
                            str(row["reference_value_id"])
                            if row["reference_value_id"]
                            else None
                        ),
                        scalar_value=row["scalar_value"],
                    )
                )

            # ── 4. Propositions themselves ────────────────────────────
            rows = await conn.fetch(
                "SELECT id, title, description, asset_concept_id, "
                "subject_entity_id, disposition_value_id, value, "
                "grounding_status_value_id, semantic_type_id "
                "FROM resolution.proposition"
            )
            skipped: Dict[str, int] = {"unknown_disposition": 0, "missing_assertion_rule": 0}
            for row in rows:
                prop_id = str(row["id"])
                disp_raw = str(row["disposition_value_id"]) if row["disposition_value_id"] else None

                if disp_raw is not None and disp_raw not in disp_map:
                    logging.getLogger(__name__).warning(
                        "load_propositions: proposition %s has disposition_value_id "
                        "%s outside the Proposition.disposition vocabulary — skipped",
                        prop_id, disp_raw,
                    )
                    skipped["unknown_disposition"] += 1
                    continue

                prop_assertion_ids = assertions_by_prop.get(prop_id, [])
                if any(rid in unloadable for rid in prop_assertion_ids):
                    skipped["missing_assertion_rule"] += 1
                    continue

                disp = disp_map.get(disp_raw, Disp.PROPOSED) if disp_raw else Disp.PROPOSED
                prop = Proposition(
                    id=prop_id,
                    title=row["title"],
                    description=row["description"],
                    asset_concept_id=str(row["asset_concept_id"]),
                    subject_entity_id=str(row["subject_entity_id"]),
                    disposition=disp,
                    value=row["value"],
                    grounding_status=str(row["grounding_status_value_id"]) if row["grounding_status_value_id"] else None,
                    semantic_type_id=(
                        str(row["semantic_type_id"])
                        if row["semantic_type_id"]
                        else None
                    ),
                    assertions=[
                        self.interpreter.rules[rid] for rid in prop_assertion_ids
                    ],
                    frame_values=frames_by_prop.get(prop_id, []),
                )
                self.interpreter.propositions[prop.id] = prop

            # Fail-loud report, not silent defaults.
            self.load_report = {
                "loaded": len(self.interpreter.propositions),
                "skipped": skipped,
                "missing_assertion_rules": len(missing_rules),
                "frame_values_loaded": len(frame_rows),
                "disposition_vocabulary_size": len(disp_map),
            }

    # ── Frame dimension meanings (v35) ──────────────────────────

    async def load_frame_dimension_meanings(self) -> None:
        """Load frame_dimension_meaning bridge rows (proposition → dimension/value)."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, proposition_id, dimension_id, frame_dimension_value_id "
                "FROM resolution.frame_dimension_meaning"
            )
            for row in rows:
                meaning = FrameDimensionMeaning(
                    id=str(row["id"]),
                    proposition_id=str(row["proposition_id"]),
                    dimension_id=(
                        str(row["dimension_id"]) if row["dimension_id"] else None
                    ),
                    frame_dimension_value_id=(
                        str(row["frame_dimension_value_id"])
                        if row["frame_dimension_value_id"]
                        else None
                    ),
                )
                self.interpreter.frame_dimension_meanings[meaning.id] = meaning

    # ── Entities ─────────────────────────────────────────────────

    async def load_entities(self, concept_name: Optional[str] = None) -> None:
        async with self.pool.acquire() as conn:
            sql = (
                "SELECT r.id, r.concept_id, r.schema_name, r.table_name, "
                "ri.identity_expression "
                "FROM resolution.representation r "
                "JOIN resolution.representation_identity ri "
                "ON ri.representation_id = r.id "
                "JOIN resolution.concept c ON c.id = r.concept_id "
                "WHERE r.expired_at IS NULL"
            )
            params: List[Any] = []
            if concept_name:
                sql += " AND c.name = $1"
                params.append(concept_name)

            rows = await conn.fetch(sql, *params)
            for row in rows:
                schema = row["schema_name"]
                table = row["table_name"]
                # Only load tables from schemas we own — skip external
                # projections (nebula, vision, conduit) that may not exist
                # or have incompatible schemas.
                if schema not in ("resolution",):
                    continue
                try:
                    table_sql = f"SELECT * FROM {schema}.{table}"
                    data_rows = await conn.fetch(table_sql)
                except Exception:
                    # Table may not exist or be empty — skip silently
                    continue
                for data_row in data_rows:
                    attributes = dict(data_row)
                    entity = Entity(
                        id=str(data_row["id"]),
                        concept_id=str(row["concept_id"]),
                        attributes=attributes,
                        external_id=(
                            str(data_row["external_id"])
                            if "external_id" in data_row
                            else None
                        ),
                    )
                    self.interpreter.entities[entity.id] = entity
