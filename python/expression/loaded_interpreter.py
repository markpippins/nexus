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

from .solscript_adapter import INTERPRETER_REVISION, evaluate_bundle_with_interpreter

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
    ) -> dict[str, Any]:
        """Run one Expression bundle against the DB-loaded interpreter."""
        envelope = evaluate_bundle_with_interpreter(
            bundle,
            self._interpreter,
            read_set=read_set,
            ontology_revision=ontology_revision,
            authority_owner=authority_owner,
            context=context,
        )
        # Re-run per candidate through the ID seam so unregistered candidates
        # get the explicit pending outcome instead of interpreter lookups.
        evaluator = self._bound_evaluator(context=context)
        results = []
        for candidate in bundle.get("proposition_candidates", []):
            result = evaluator(candidate, {"read_set": read_set})
            result["request_fingerprint"] = _candidate_fingerprint(candidate, read_set)
            results.append(result)
        results.sort(key=lambda item: item.get("proposition_id") or "")
        envelope["results"] = results
        envelope["loaded_population_fingerprint"] = self.population_fingerprint()
        envelope["registrations"] = self.registrations()
        return envelope


def _candidate_fingerprint(candidate: dict[str, Any], read_set: dict[str, Any]) -> str:
    material = json_canonical({"candidate": candidate.get("proposition_id"), "read_set": read_set})
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def json_canonical(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
