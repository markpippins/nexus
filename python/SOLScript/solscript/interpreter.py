"""ResolutionInterpreter — in-memory interpreter for the resolution language."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from .events import KeychainEvent, build_transition_event
from .expression_compiler import ExpressionCompiler
from .models import (
    Concept,
    ConceptAttribute,
    ConceptRelationship,
    ConceptStateTransition,
    Disposition,
    Entity,
    Expression,
    FrameDimension,
    FrameDimensionMeaning,
    FrameDimensionValue,
    FunctionBinding,
    Proposition,
    PropositionFrameValue,
    Representation,
    RepresentationComparison,
    Rule,
    RuleType,
    Severity,
)

# Avoid circular import at module level; TYPE_CHECKING guard suffices.


class ResolutionInterpreter:
    """In-memory interpreter for the resolution language.

    Holds the full concept graph, entity store, propositions, rules,
    and expression/function registries.  Evaluation uses compiled
    expression trees for performance.
    """

    def __init__(self) -> None:
        # Core data stores
        self.concepts: Dict[str, Concept] = {}
        self.entities: Dict[str, Entity] = {}
        self.propositions: Dict[str, Proposition] = {}
        self.expressions: Dict[str, Expression] = {}
        self.rules: Dict[str, Rule] = {}
        self.functions: Dict[str, FunctionBinding] = {}
        self.representations: Dict[str, Representation] = {}
        self.relationships: Dict[str, ConceptRelationship] = {}
        self.state_transitions: Dict[str, ConceptStateTransition] = {}
        # v31/v32: frame discipline
        self.frame_dimensions: Dict[str, FrameDimension] = {}
        self.frame_dimension_values: Dict[str, FrameDimensionValue] = {}
        # E8.4: semantic-type requirements are class-level framing rules.
        # A proposition's instance frame values are commitments, not the
        # definition of whether its type requires framing.
        self.semantic_type_required_dimensions: Dict[str, set[str]] = {}
        # v35: frame semantics — propositions describing what a dimension means
        self.frame_dimension_meanings: Dict[str, FrameDimensionMeaning] = {}

        # Runtime state
        self.evaluation_cache: Dict[str, Any] = {}
        self.execution_context: Dict[str, Any] = {}
        self.event_handlers: List[Callable[..., Any]] = []
        # State-transition listeners: fired after a SUCCESSFUL transition
        # (keychains snapshot hook, catalogue 332d6831 — decision points).
        self.transition_listeners: List[Callable[..., Any]] = []
        # Event listeners receive both committed and refused transition
        # attempts. API adapters persist these before dispatching projections.
        self.transition_event_listeners: List[Callable[..., Any]] = []
        self.last_transition_event: Optional[KeychainEvent] = None

        # Components
        self.expression_compiler = ExpressionCompiler(self)

        # Register built-in functions
        self._register_builtin_functions()

    # ── Built-in functions ───────────────────────────────────────

    def _register_builtin_functions(self) -> None:
        builtins: Dict[str, Callable[..., Any]] = {
            "count": lambda *args: len([a for a in args if a is not None]),
            "sum": lambda *args: sum(
                float(a) if a is not None else 0 for a in args
            ),
            "avg": lambda *args: (
                sum(float(a) if a is not None else 0 for a in args)
                / max(len([a for a in args if a is not None]), 1)
            ),
            "min": lambda *args: min(
                float(a) if a is not None else float("inf") for a in args
            ),
            "max": lambda *args: max(
                float(a) if a is not None else float("-inf") for a in args
            ),
            "coalesce": lambda *args: next(
                (a for a in args if a is not None), None
            ),
            "concat": lambda *args: "".join(
                str(a) if a is not None else "" for a in args
            ),
            "contains": lambda s, sub: sub in str(s) if s is not None else False,
            "starts_with": lambda s, prefix: (
                str(s).startswith(prefix) if s is not None else False
            ),
            "ends_with": lambda s, suffix: (
                str(s).endswith(suffix) if s is not None else False
            ),
            "is_null": lambda v: v is None,
            "is_not_null": lambda v: v is not None,
        }
        for name, func in builtins.items():
            self.functions[name] = FunctionBinding(
                function_name=name,
                sql_template="",
                arg_count=0,
                return_type="any",
                notes=f"Built-in: {name}",
                python_func=func,
            )

    # ── Concept / entity / proposition lookups ────────────────────

    def add_concept(self, concept: Concept) -> None:
        self.concepts[concept.id] = concept

    def get_concept(self, concept_id: str) -> Optional[Concept]:
        return self.concepts.get(concept_id)

    def get_concept_by_name(self, name: str) -> Optional[Concept]:
        for c in self.concepts.values():
            if c.name == name:
                return c
        return None

    def add_entity(self, entity: Entity) -> None:
        self.entities[entity.id] = entity

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        return self.entities.get(entity_id)

    def get_entity_by_external_id(self, external_id: str) -> Optional[Entity]:
        for e in self.entities.values():
            if e.external_id == external_id:
                return e
        return None

    def add_proposition(self, proposition: Proposition) -> None:
        self.propositions[proposition.id] = proposition

    def get_proposition(self, proposition_id: str) -> Optional[Proposition]:
        return self.propositions.get(proposition_id)

    def get_attribute(self, attribute_id: str) -> Optional[ConceptAttribute]:
        for concept in self.concepts.values():
            for attr in concept.attributes.values():
                if attr.id == attribute_id:
                    return attr
        return None

    def get_relationship(
        self, relationship_id: str
    ) -> Optional[ConceptRelationship]:
        return self.relationships.get(relationship_id)

    def get_state_transition(
        self, transition_id: str
    ) -> Optional[ConceptStateTransition]:
        return self.state_transitions.get(transition_id)

    def get_function(self, function_name: str) -> Optional[FunctionBinding]:
        return self.functions.get(function_name)

    # ── Expression evaluation ────────────────────────────────────

    def evaluate(
        self, expression: Expression, context: Dict[str, Any]
    ) -> Any:
        compiled = self.expression_compiler.compile_expression(expression)
        return compiled(context)

    # ── Rule evaluation ──────────────────────────────────────────

    def check_rule(
        self, rule: Rule, entity: Entity
    ) -> Tuple[bool, str]:
        context: Dict[str, Any] = {"entity": entity}
        try:
            if rule.expression:
                result = self.evaluate(rule.expression, context)
                passed = bool(result)
                return passed, f"Rule '{rule.name}' {'passed' if passed else 'failed'}"
            return False, f"Rule '{rule.name}' has no expression"
        except Exception as exc:
            if rule.severity == Severity.HARD:
                return False, f"Rule '{rule.name}' error: {exc}"
            return True, f"Rule '{rule.name}' soft error: {exc}"

    def check_transition_guard(
        self, transition: ConceptStateTransition, entity: Entity
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        results: List[Dict[str, Any]] = []
        all_passed = True

        for rule in transition.guards:
            passed, reason = self.check_rule(rule, entity)
            results.append(
                {"rule_id": rule.id, "rule_name": rule.name, "passed": passed, "reason": reason}
            )
            if not passed:
                all_passed = False

        concept = self.concepts.get(transition.concept_id)
        if concept:
            for rule in concept.invariants:
                passed, reason = self.check_rule(rule, entity)
                results.append(
                    {"rule_id": rule.id, "rule_name": rule.name, "passed": passed, "reason": reason}
                )
                if not passed:
                    all_passed = False

        return all_passed, results

    # ── State transitions ────────────────────────────────────────

    def transition_entity(
        self, entity_id: str, transition_id: str,
        *, source_event_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        actor: Optional[str] = None,
        source_namespace: str = "sol-api",
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        entity = self.entities.get(entity_id)
        if not entity:
            results = [{"error": "Entity not found"}]
            event = build_transition_event(
                source_event_id=source_event_id or f"transition:{transition_id}:entity:{entity_id}",
                entity_id=entity_id,
                transition_id=transition_id,
                outcome="rejected",
                results=results,
                correlation_id=correlation_id,
                actor=actor,
                source_namespace=source_namespace,
                effective_at=datetime.now(timezone.utc).isoformat(),
            )
            self.last_transition_event = event
            self._notify_transition_event(event)
            return False, results
        transition = self.state_transitions.get(transition_id)
        if not transition:
            results = [{"error": "Transition not found"}]
            event = build_transition_event(
                source_event_id=source_event_id or f"transition:{transition_id}:entity:{entity_id}",
                entity_id=entity_id,
                transition_id=transition_id,
                outcome="rejected",
                results=results,
                concept_id=entity.concept_id,
                correlation_id=correlation_id,
                actor=actor,
                source_namespace=source_namespace,
                effective_at=datetime.now(timezone.utc).isoformat(),
            )
            self.last_transition_event = event
            self._notify_transition_event(event)
            return False, results

        all_passed, results = self.check_transition_guard(transition, entity)
        event_id = source_event_id or f"transition:{transition_id}:entity:{entity_id}"
        concept = self.concepts.get(entity.concept_id)
        state_attr = (
            next((a for a in concept.attributes.values() if a.is_state_attribute), None)
            if concept else None
        )
        state_before = entity.attributes.get(state_attr.name) if state_attr else None
        outcome = "committed" if all_passed else "refused"

        if not all_passed:
            event = build_transition_event(
                source_event_id=event_id,
                entity_id=entity_id,
                transition_id=transition_id,
                outcome=outcome,
                results=results,
                concept_id=entity.concept_id,
                state_before=state_before,
                state_after=state_before,
                correlation_id=correlation_id,
                actor=actor,
                source_namespace=source_namespace,
                effective_at=datetime.now(timezone.utc).isoformat(),
            )
            self.last_transition_event = event
            self._notify_transition_event(event)
            return False, results

        if state_attr and transition.to_value in state_attr.allowed_values:
            entity.attributes[state_attr.name] = transition.to_value
        state_after = entity.attributes.get(state_attr.name) if state_attr else None
        event = build_transition_event(
            source_event_id=event_id,
            entity_id=entity_id,
            transition_id=transition_id,
            outcome=outcome,
            results=results,
            concept_id=entity.concept_id,
            state_before=state_before,
            state_after=state_after,
            correlation_id=correlation_id,
            actor=actor,
            source_namespace=source_namespace,
            effective_at=datetime.now(timezone.utc).isoformat(),
        )
        self.last_transition_event = event
        # The source API persists this event before dispatching its outbox.
        self._notify_transition_event(event)

        # Fired only on a successful transition (rejected/no-op guards do
        # NOT snapshot — the guarded transition never happened).
        for listener in self.transition_listeners:
            try:
                listener(
                    transition_id=transition_id,
                    entity_id=entity_id,
                    to_value=transition.to_value,
                    effective_at=event.effective_at,
                    event=event,
                )
            except Exception:
                pass

        return True, results

    def register_transition_listener(self, listener: Callable[..., Any]) -> None:
        """Register a callback fired after each successful state transition."""
        self.transition_listeners.append(listener)

    def register_transition_event_listener(self, listener: Callable[..., Any]) -> None:
        """Register a listener for every transition attempt outcome.

        Unlike ``register_transition_listener``, this includes refused and
        rejected attempts because negative governed outcomes are evidence.
        """
        self.transition_event_listeners.append(listener)

    def _notify_transition_event(self, event: KeychainEvent) -> None:
        for listener in self.transition_event_listeners:
            try:
                listener(event=event)
            except Exception:
                pass

    def add_frame_dimension(self, dim: FrameDimension) -> None:
        self.frame_dimensions[dim.id] = dim

    def get_frame_dimension(self, dim_id: str) -> Optional[FrameDimension]:
        return self.frame_dimensions.get(dim_id)

    def get_frame_dimension_by_name(self, name: str) -> Optional[FrameDimension]:
        for d in self.frame_dimensions.values():
            if d.name == name:
                return d
        return None

    def add_frame_dimension_value(self, val: FrameDimensionValue) -> None:
        self.frame_dimension_values[val.id] = val

    def add_proposition_frame_value(self, pfv: PropositionFrameValue) -> None:
        prop = self.propositions.get(pfv.proposition_id)
        if prop:
            prop.frame_values.append(pfv)

    def add_frame_dimension_meaning(self, meaning: FrameDimensionMeaning) -> None:
        self.frame_dimension_meanings[meaning.id] = meaning

    def meanings_of(
        self,
        dimension: str,
        value: Optional[str] = None,
    ) -> List[Proposition]:
        """Return the meaning propositions describing a frame dimension.

        `dimension` may be a dimension id or name.  When `value` is given,
        only value-level meanings for that specific value are returned;
        otherwise whole-dimension meanings (plus value-level meanings for all
        values, since they jointly describe the dimension's meaning).
        """
        dim = self.frame_dimensions.get(dimension) or self.get_frame_dimension_by_name(dimension)
        if dim is None:
            return []

        results: List[Proposition] = []
        for meaning in self.frame_dimension_meanings.values():
            prop = self.propositions.get(meaning.proposition_id)
            if prop is None:
                continue
            if meaning.dimension_id == dim.id:
                # whole-dimension meaning always applies
                results.append(prop)
            elif meaning.frame_dimension_value_id:
                fdv = self.frame_dimension_values.get(meaning.frame_dimension_value_id)
                if fdv and fdv.dimension_id == dim.id:
                    if value is None or fdv.value == value:
                        results.append(prop)
        return results

    # ── Proposition evaluation ───────────────────────────────────

    def register_semantic_type_required_dimension(
        self, semantic_type_id: str, dimension_id: str
    ) -> None:
        """Register one class-level required frame dimension for a type."""
        self.semantic_type_required_dimensions.setdefault(
            str(semantic_type_id), set()
        ).add(str(dimension_id))

    def evaluate_proposition(
        self,
        prop: Proposition,
        context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Disposition, bool, str]:
        """Evaluate a proposition with class-level frame-context discipline (E8.4).

        Returns (disposition, all_passed, context_status) where:
          context_status: 'not_scoped' | 'unframed_required' |
          'context_required' | 'context_mismatch' | 'scoped'
        """
        # ── Context gate: class-level type requirements (E8.4) ─────
        required_dimensions = self.semantic_type_required_dimensions.get(
            str(prop.semantic_type_id), set()
        ) if prop.semantic_type_id else set()
        instance_dimensions = {frame.dimension_id for frame in prop.frame_values}

        # A required type with incomplete instance commitments is not
        # unframed-on-merits. This removes the v32 free pass where declaring
        # no frame rows made an incomplete proposition look not_scoped.
        if not required_dimensions.issubset(instance_dimensions):
            if required_dimensions:
                return (None, False, "unframed_required")  # type: ignore[return-value]
            # No required dimensions: instance frame values are supplementary.
            context_status = "not_scoped"
        else:
            context_status = "scoped"

        if context is not None and not isinstance(context, dict):
            raise ValueError(
                f"evaluate_proposition: context must be a dict, got {type(context).__name__}"
            )

        if context is not None:
            # Unknown keys are invalid whenever a caller supplies context;
            # unframed propositions no longer silently discard commitments.
            for key in context:
                dim = self.get_frame_dimension_by_name(key)
                if dim is None:
                    raise ValueError(
                        f"evaluate_proposition: context key '{key}' names no known frame_dimension"
                    )

            # Required dimensions must be supplied and matched. Supplementary
            # instance values are checked only when the caller supplies that
            # dimension; absence is not grounds for refusal.
            for pfv in prop.frame_values:
                dim = self.frame_dimensions.get(pfv.dimension_id)
                if dim is None:
                    if pfv.dimension_id in required_dimensions:
                        return (None, False, "context_required")  # type: ignore[return-value]
                    continue

                ctx_val = context.get(dim.name)
                if ctx_val is None:
                    if pfv.dimension_id in required_dimensions:
                        return (None, False, "context_required")  # type: ignore[return-value]
                    continue

                if dim.value_kind == "governed_reference":
                    fdv = self.frame_dimension_values.get(pfv.reference_value_id or "")
                    if fdv is None or fdv.value != str(ctx_val):
                        return (None, False, "context_mismatch")  # type: ignore[return-value]
                elif dim.value_kind == "typed_scalar":
                    scalar_type = dim.scalar_type or "text"
                    scalar = pfv.scalar_value
                    try:
                        if scalar_type == "integer":
                            mismatch = int(ctx_val) != int(scalar)  # type: ignore[arg-type]
                        elif scalar_type == "numeric":
                            mismatch = float(ctx_val) != float(scalar)  # type: ignore[arg-type]
                        elif scalar_type == "boolean":
                            mismatch = bool(ctx_val) != (scalar in ("true", "True", "1"))
                        else:  # text / timestamp
                            mismatch = str(ctx_val) != str(scalar)
                    except (ValueError, TypeError):
                        mismatch = True
                    if mismatch:
                        return (None, False, "context_mismatch")  # type: ignore[return-value]
                else:
                    raise ValueError(
                        f"evaluate_proposition: dimension {dim.name} has "
                        f"unrecognized value_kind {dim.value_kind}"
                    )

            # A supplied context matching a supplementary commitment is still
            # a scoped evaluation; a required type was scoped above.
            if context_status == "not_scoped" and prop.frame_values:
                context_status = "scoped"

        elif required_dimensions:
            # Required dimensions exist and the instance is complete, but no
            # caller context was provided.
            return (None, False, "context_required")  # type: ignore[return-value]

        # ── Assertion evaluation ─────────────────────────────────
        entity = self.entities.get(prop.subject_entity_id)
        if not entity:
            return (Disposition.REJECTED, False, context_status)

        all_passed = True
        relational_failed = False

        for rule in prop.assertions:
            passed, _ = self.check_rule(rule, entity)
            if not passed:
                all_passed = False
                if rule.is_relational_check:
                    relational_failed = True

        if all_passed:
            disposition = Disposition.ASSERTED
        elif relational_failed:
            disposition = Disposition.DISPUTED
        else:
            disposition = Disposition.REJECTED

        return (disposition, all_passed, context_status)

    def reopen_disputed_proposition(
        self, prop: Proposition, external_id: str  # noqa: ARG002
    ) -> Disposition:
        if prop.disposition != Disposition.DISPUTED:
            return prop.disposition
        disposition, _, _ = self.evaluate_proposition(prop)
        return disposition or prop.disposition

    # ── Change events ────────────────────────────────────────────

    def on_change(
        self, concept_name: str, entity_id: str
    ) -> List[Tuple[str, str, Disposition]]:
        results: List[Tuple[str, str, Disposition]] = []
        entity = self.entities.get(entity_id)
        if not entity:
            return results
        concept = self.get_concept_by_name(concept_name)
        if not concept:
            return results

        external_id = entity.external_id

        for prop in self.propositions.values():
            if (
                prop.subject_entity_id == entity_id
                and prop.asset_concept_id == concept.id
            ):
                old = prop.disposition
                if old == Disposition.DISPUTED and external_id:
                    new = self.reopen_disputed_proposition(prop, external_id)
                else:
                    new, _, _ = self.evaluate_proposition(prop)
                if new and new != old:
                    prop.disposition = new
                    prop.last_evaluated_at = datetime.now()
                    results.append((prop.id, "event_evaluate", new))

        for handler in self.event_handlers:
            try:
                handler(concept_name, entity_id, results)
            except Exception:
                pass
        return results

    def register_event_handler(self, handler: Callable[..., Any]) -> None:
        self.event_handlers.append(handler)

    # ── Convenience helpers ──────────────────────────────────────

    def add_entity_by_concept_name(
        self,
        concept_name: str,
        attributes: Dict[str, Any],
        external_id: Optional[str] = None,
    ) -> Entity:
        import uuid

        concept = self.get_concept_by_name(concept_name)
        if not concept:
            raise ValueError(f"Concept not found: {concept_name}")
        entity = Entity(
            id=str(uuid.uuid4()),
            concept_id=concept.id,
            attributes=attributes,
            external_id=external_id,
        )
        self.entities[entity.id] = entity
        return entity
