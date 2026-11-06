"""Expression E8.2 — database-loaded interpreter adapter.

Bridges Expression to a ``DatabaseLoader``-populated
``ResolutionInterpreter``: the loader materializes concepts, rules, frame
dimensions, propositions (with assertions and frame values, per E8.1),
and optionally entities from the resolution schema; this module then runs
Expression bundles through the live interpreter with an explicit identity
seam.

Identity seam (the core E8.2 contract):

- Expression proposition candidates carry deterministic digest IDs.
- Database propositions carry UUIDs.
- The adapter evaluates a candidate ONLY through a pinned mapping entry
  (``expression:<digest>`` → DB proposition UUID) registered by
  :func:`register_candidates`. Unregistered candidates stay
  ``pending`` (``proposition_not_in_interpreter``). No ID is ever
  rewritten, aliased silently, or guessed.
"""
from __future__ import annotations

import hashlib
from typing import Any

from .solscript_adapter import INTERPRETER_REVISION, solscript_evaluator
from .evaluator import _digest, _read_set_fingerprint, _is_resolved_source_link_gate
from .source_links import resolve_source_links

EXPRESSION_ID_PREFIX = "expression:"


def loaded_population_fingerprint(population: dict[str, Any]) -> str:
    """Fingerprint the loaded interpreter population for replay pinning.

    Covers exactly what evaluation depends on: propositions (id,
    disposition, assertion rule ids, frame values), frame dimensions, and
    frame dimension values. Excludes mutable runtime state such as
    ``last_evaluated_at`` and evaluation caches.
    """
    material = {
        "propositions": sorted(
            (
                {
                    "id": prop_id,
                    "disposition": (
                        prop.disposition.value
                        if hasattr(prop.disposition, "value")
                        else str(prop.disposition)
                    ),
                    "assertion_rule_ids": sorted(r.id for r in prop.assertions),
                    "frame_values": sorted(
                        (fv.dimension_id, fv.reference_value_id, fv.scalar_value)
                        for fv in prop.frame_values
                    ),
                }
                for prop_id, prop in population.get("propositions", {}).items()
            ),
            key=lambda item: item["id"],
        ),
        "frame_dimensions": sorted(
            (dim_id, dim.name, dim.value_kind)
            for dim_id, dim in population.get("frame_dimensions", {}).items()
        ),
        "frame_dimension_values": sorted(
            (val_id, val.dimension_id, val.value)
            for val_id, val in population.get("frame_dimension_values", {}).items()
        ),
        "semantic_type_required_dimensions": sorted(
            (type_id, sorted(dimension_ids))
            for type_id, dimension_ids in population.get(
                "semantic_type_required_dimensions", {}
            ).items()
        ),
    }
    return hashlib.sha256(
        repr(sorted(material.items())).encode("utf-8")
    ).hexdigest()


class LoadedInterpreter:
    """A DatabaseLoader-populated interpreter with an explicit ID seam."""

    def __init__(self, interpreter: Any, *, loader_report: dict[str, Any] | None = None):
        self._interpreter = interpreter
        self._loader_report = loader_report or {}
        # expression:<digest> -> db proposition UUID (pinned, auditable)
        self._id_map: dict[str, str] = {}
        self._registrations: list[dict[str, Any]] = []

    @property
    def interpreter(self) -> Any:
        return self._interpreter

    @property
    def loader_report(self) -> dict[str, Any]:
        return self._loader_report

    def population_snapshot(self) -> dict[str, Any]:
        return {
            "propositions": self._interpreter.propositions,
            "frame_dimensions": self._interpreter.frame_dimensions,
            "frame_dimension_values": self._interpreter.frame_dimension_values,
            "semantic_type_required_dimensions": (
                self._interpreter.semantic_type_required_dimensions
            ),
        }

    def population_fingerprint(self) -> str:
        return loaded_population_fingerprint(self.population_snapshot())

    def register_candidate(self, expression_id: str, db_proposition_id: str) -> dict[str, Any]:
        """Pin one expression-digest → DB-UUID mapping after verification.

        Refuses to register unless the DB proposition actually exists in the
        loaded interpreter. Returns the registration record for audit.
        """
        if not expression_id.startswith(EXPRESSION_ID_PREFIX):
            expression_id = f"{EXPRESSION_ID_PREFIX}{expression_id}"
        db_proposition_id = str(db_proposition_id)
        if db_proposition_id not in self._interpreter.propositions:
            raise ValueError(
                f"cannot register {expression_id}: DB proposition "
                f"{db_proposition_id} is not loaded in the interpreter"
            )
        existing = self._id_map.get(expression_id)
        if existing is not None and existing != db_proposition_id:
            raise ValueError(
                f"identity conflict: {expression_id} already maps to "
                f"{existing}; refusing re-registration to {db_proposition_id}"
            )
        self._id_map[expression_id] = db_proposition_id
        record = {
            "expression_id": expression_id,
            "db_proposition_id": db_proposition_id,
        }
        self._registrations.append(record)
        return record

    def registrations(self) -> list[dict[str, Any]]:
        return list(self._registrations)

    def _bound_evaluator(self, context: dict[str, Any] | None = None):
        """Evaluator callable with ID-seam translation applied per candidate."""
        from .solscript_adapter import solscript_evaluator

        base_evaluator = solscript_evaluator(self._interpreter, context=context)

        def evaluator(candidate: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
            expression_id = candidate.get("proposition_id") or ""
            key = (
                expression_id
                if expression_id.startswith(EXPRESSION_ID_PREFIX)
                else f"{EXPRESSION_ID_PREFIX}{expression_id}"
            )
            mapped = self._id_map.get(key)
            if mapped is None:
                return {
                    "disposition": "pending",
                    "reason_code": "proposition_not_registered",
                    "reason": (
                        f"Candidate {key} has no pinned DB proposition mapping; "
                        "register it via register_candidate() before evaluation."
                    ),
                }
            translated = {**candidate, "proposition_id": mapped}
            return base_evaluator(translated, request)

        return evaluator

    def evaluate_bundle(
        self,
        bundle: dict[str, Any],
        *,
        read_set: dict[str, Any],
        ontology_revision: str,
        authority_owner: str = "resolution",
        context: dict[str, Any] | None = None,
        source_link_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Evaluate with a required, pinned source-link gate."""
        report = source_link_report
        if report is None:
            from .source_links import LiveGovernedArtifactResolver

            report = resolve_source_links(bundle, LiveGovernedArtifactResolver())

        gates = {
            item.get("proposition_id"): item
            for item in report.get("proposition_gates", [])
            if isinstance(item, dict)
        }
        evaluator = self._bound_evaluator(context=context)
        results = []
        for candidate in bundle.get("proposition_candidates", []):
            proposition_id = candidate.get("proposition_id")
            gate = gates.get(proposition_id)
            if gate is None:
                result = {
                    "disposition": "refused",
                    "reason_code": "source_link_gate_missing",
                    "reason": "No source-link gate was supplied for this proposition.",
                    "authority_status": "evaluation_only",
                    "source_link_gate": None,
                }
                request_fingerprint = None
            elif not _is_resolved_source_link_gate(gate):
                result = {
                    "disposition": "refused",
                    "reason_code": "source_link_unresolved",
                    "reason": "; ".join(gate.get("reasons", [])) or "Source-link gate did not prove every link and relation resolved.",
                    "authority_status": "evaluation_only",
                    "source_link_gate": gate,
                }
                request_fingerprint = None
            else:
                # Use the regular pinned E4 request fingerprint and pass its
                # gate to the interpreter callback only after validation.
                from .evaluator import build_evaluation_request

                request = build_evaluation_request(
                    bundle, candidate, read_set=read_set,
                    evaluator_revision=INTERPRETER_REVISION,
                    ontology_revision=ontology_revision,
                    authority_owner=authority_owner,
                    source_link_gate=gate,
                )
                result = evaluator(candidate, request)
                result["source_link_gate"] = gate
                request_fingerprint = request["request_fingerprint"]
            result["proposition_id"] = proposition_id
            result["request_fingerprint"] = request_fingerprint
            results.append(result)

        results.sort(key=lambda item: item.get("proposition_id") or "")
        envelope = {
            "contract_revision": bundle.get("contract_revision", "expression-v0.1"),
            "source_fingerprint": bundle.get("source_fingerprint"),
            "evaluator_revision": INTERPRETER_REVISION,
            "ontology_revision": ontology_revision,
            "authority_owner": authority_owner,
            "read_set_fingerprint": _read_set_fingerprint(read_set),
            "source_link_report": report,
            "authority_status": "evaluation_only",
            "mutation_policy": "forbidden",
            "results": results,
            "loaded_population_fingerprint": self.population_fingerprint(),
            "registrations": self.registrations(),
        }
        envelope["evaluation_fingerprint"] = _digest(envelope)
        return envelope


def _candidate_fingerprint(candidate: dict[str, Any], read_set: dict[str, Any]) -> str:
    material = json_canonical({"candidate": candidate.get("proposition_id"), "read_set": read_set})
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def json_canonical(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
