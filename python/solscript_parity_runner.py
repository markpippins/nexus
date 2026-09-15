#!/usr/bin/env python3
"""solscript_parity_runner.py — Python-side runner for the TS-port parity proof.

Loads test/parity/scenario.json (shared fixture), executes it against the
REFERENCE implementation (python/SOLScript/solscript — in-repo, stdlib-only
usage), and writes canonical JSON results. The TS runner
(typescript/solscript/test/run-solscript-ts-parity.ts) executes the SAME
scenario and the parity driver diffs the two outputs after scrubbing
wall-clock fields.

Usage:
  python3 python/solscript_parity_runner.py --scenario <path> --out <path>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent          # wt root
SOLSCRIPT = REPO / "python" / "SOLScript"
sys.path.insert(0, str(SOLSCRIPT))

from solscript.interpreter import ResolutionInterpreter  # noqa: E402
from solscript.models import (  # noqa: E402
    Concept, ConceptAttribute, ConceptRelationship, ConceptStateTransition,
    Disposition, Entity, Expression, ExpressionKind, FrameDimension,
    FrameDimensionValue, Operator, Proposition, PropositionFrameValue, Rule,
    RuleType, Severity,
)
from solscript.events import stable_digest  # noqa: E402
from solscript.query_builder import QueryBuilder  # noqa: E402


def build_expression(d: dict) -> Expression:
    kind = ExpressionKind(d["kind"])
    return Expression(
        id=d["id"],
        kind=kind,
        return_type=d.get("return_type", ""),
        operator=Operator(d["operator"]) if d.get("operator") else None,
        literal_value=d.get("literal_value"),
        attribute_id=d.get("attribute_id"),
        operands=[build_expression(o) for o in d.get("operands", [])],
    )


def build_rule(d: dict) -> Rule:
    return Rule(
        id=d["id"],
        name=d["name"],
        rule_type=RuleType(d["rule_type"]),
        expression=build_expression(d["expression"]) if d.get("expression") else None,
        severity=Severity(d["severity"]),
        is_relational_check=d.get("is_relational_check", False),
        conditions=[],
    )


def load(interp: ResolutionInterpreter, sc: dict) -> None:
    for c in sc["concepts"]:
        attrs = {}
        for a in c["attributes"]:
            attrs[a["id"]] = ConceptAttribute(
                id=a["id"], concept_id=a["concept_id"], name=a["name"],
                description=None, value_type=a["value_type"],
                is_state_attribute=a["is_state_attribute"],
                allowed_values=list(a.get("allowed_values", [])),
            )
        interp.add_concept(Concept(
            id=c["id"], name=c["name"], description=c.get("description"),
            attributes=attrs, relationships={}, invariants=[],
            derivations=[], state_transitions=[], rules=[],
        ))
    for t in sc["state_transitions"]:
        interp.state_transitions[t["id"]] = ConceptStateTransition(
            id=t["id"], concept_id=t["concept_id"], from_value=t.get("from_value"),
            to_value=t["to_value"], name=t["name"], notes=None,
            guards=[build_rule(g) for g in t["guards"]],
        )
    for e in sc["entities"]:
        interp.add_entity(Entity(
            id=e["id"], concept_id=e["concept_id"],
            attributes=dict(e["attributes"]), external_id=e.get("external_id"),
        ))
    for p in sc["propositions"]:
        interp.add_proposition(Proposition(
            id=p["id"], title=p["title"], description=None,
            asset_concept_id=p["asset_concept_id"],
            subject_entity_id=p["subject_entity_id"],
            disposition=Disposition(p["disposition"]),
            assertions=[build_rule(a) for a in p["assertions"]],
            comparisons=[],
        ))
    for d in sc["frame_dimensions"]:
        interp.add_frame_dimension(FrameDimension(
            id=d["id"], name=d["name"], description=None, value_kind=d["value_kind"],
        ))
    for v in sc["frame_dimension_values"]:
        interp.add_frame_dimension_value(FrameDimensionValue(
            id=v["id"], dimension_id=v["dimension_id"], value=v["value"],
        ))
    for pfv in sc.get("propositions", []):
        pass
    # frame values come from the proposition entries
    for p in sc["propositions"]:
        for pfv in p.get("frame_values", []):
            interp.add_proposition_frame_value(PropositionFrameValue(
                id=pfv["id"], proposition_id=pfv["proposition_id"],
                dimension_id=pfv["dimension_id"],
                reference_value_id=pfv.get("reference_value_id"),
                scalar_value=pfv.get("scalar_value"),
            ))


def run(sc: dict) -> dict:
    interp = ResolutionInterpreter()
    load(interp, sc)
    out: dict = {"function_calls": [], "digests": [], "transitions": [],
                 "evaluations": [], "queries": []}

    for fc in sc["function_calls"]:
        fn = interp.functions[fc["function"]].python_func
        out["function_calls"].append({"id": fc["id"], "result": fn(*fc["args"])})

    for dg in sc["digest_cases"]:
        out["digests"].append({"id": dg["id"], "digest": stable_digest(dg["value"])})

    for tc in sc["transition_checks"]:
        committed, results = interp.transition_entity(
            tc["entity_id"], tc["transition_id"],
            source_event_id=f"evt-{tc['id']}", actor="parity",
        )
        entity = interp.entities[tc["entity_id"]]
        state_attr = next(
            (a for a in interp.concepts[entity.concept_id].attributes.values()
             if a.is_state_attribute), None)
        out["transitions"].append({
            "id": tc["id"], "committed": committed,
            "state_after": entity.attributes.get(state_attr.name) if state_attr else None,
            "results": results,
            "event_kind": interp.last_transition_event.kind if interp.last_transition_event else None,
        })

    for ec in sc["evaluation_checks"]:
        prop = interp.propositions[ec["proposition_id"]]
        try:
            d, passed, status = interp.evaluate_proposition(prop, ec.get("context"))
            out["evaluations"].append({
                "id": ec["id"], "disposition": d.value if d else None,
                "context_status": status,
            })
        except ValueError as exc:
            out["evaluations"].append({
                "id": ec["id"], "error": str(exc),
            })

    for q in sc["queries"]:
        query = QueryBuilder(interp).select(q["concept"])
        attr, op, value = q["where"]
        from solscript.models import Operator as Op
        query.where(attr, Op(op), value)
        if q.get("order_by"):
            query.order_by(q["order_by"][0], q["order_by"][1])
        query.select_fields(*q["select_fields"])
        out["queries"].append({"id": q["id"], "rows": query.execute()})

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scenario", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    sc = json.loads(args.scenario.read_text())
    result = run(sc)
    args.out.write_text(json.dumps(result, sort_keys=True, indent=1, default=str))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
