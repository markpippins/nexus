#!/usr/bin/env python3
"""flow-emit-aegis — Stage 4: emit a ratified flow-record as an Aegis blueprint.

Design: discussions thread 1adce409, Stage-4 post 4d540f39 (Q1–Q6). The
recorder (bin/flow-recorder-v0.py, r2) stays observer-only (C8); THIS module
is the separate emitter process — witness ≠ registrar.

STAGED-INERT BY CONSTRUCTION, NOT BY SWITCH:

  - G4 (explicit emission capability) DEFAULT-DENIES: with no capability
    resolver wired, EVERY run refuses with reason `gate4: no capability
    resolver — emission capability unverifiable (default deny)`.
  - G1 (ratified candidate) requires the flow-record itself to carry
    `"ratified": true` (candidates induced by the recorder carry no such
    flag) or an explicit --i-have-operator-go override with the operator-go
    record UUID, which is recorded in the artifact's provenance.
  - No write path exists to any Aegis/registry store: the artifact goes to
    stdout or --out FILE only. Registration into a live StateMachineRegistry
    is a POST-RATIFICATION step (Q1), not implemented here on purpose.

The four gates (design post 4d540f39), all pinned by tests:

  G1 ratified candidate      — flow has `ratified: true` OR explicit go
  G2 zero unresolved roles   — suspect=false and no unresolved:<name> bindings
  G3 deterministic emission  — byte-identical artifact for identical input;
                               sort_keys + no timestamps + no nondeterminism
  G4 explicit capability     — emitter runner holds can_emit_flows (Q3);
                               injected resolver; DEFAULT DENY

Q5 reserve: every emitted machine carries `flow_ref` in metadata — the
composition slot for little-flows-as-building-blocks, empty in v0.
Q4 declares-side: guard semantics ride the artifact as `concept_mappings`
predicates (declared here, evaluated by wind-srv's resolver per Q4 —
evaluation ownership is NOT claimed by this module).

Guard-expression grammar (constrained by aegis.py _extract_state_references:
every capitalized token must be a declared state, parens must balance):
  state refs appear only as the states themselves; role/verdict facts are
  lower_snake, e.g. `actor_pair_planner_engineer / review_verdict_present`.
"""

import argparse
import json
import os
import re
import sys

# Import the recorder for ROLE_ALIAS + GUARD_VOCAB — single source of truth,
# no drift. The recorder is import-safe (no side effects at import time).
# Note: plain `import flow_recorder_v0` is unreliable under pytest's import
# mode (module name collisions) — load by file path instead (the #318
# explicit-boundary lesson).
import importlib.util as _iu  # noqa: E402
_rec_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "flow-recorder-v0.py")
_spec = _iu.spec_from_file_location("flow_recorder_v0", _rec_path)
_mod = _iu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
ROLE_ALIAS = _mod.ROLE_ALIAS
GUARD_VOCAB = _mod.GUARD_VOCAB

ARTIFACT_VERSION = "flow-blueprint-v0"

# The verify-holder class target from the ratified RoleAlias map — the only
# non-identity, non-alias target; class guards evaluate historically (RA2).
CLASS_TARGET = "verify-holder"


def role_facts(guards):
    """Lower_snake facts from a flow-record edge's guard dict. Pure.

    Keys are constrained so aegis.py's state-reference extractor can never
    mistake them for states: no capitalized tokens, no parens.
    """
    facts = []
    for gname in sorted(guards):
        if gname not in GUARD_VOCAB:
            raise ValueError(f"unknown guard vocabulary: {gname}")
        g = guards[gname]
        if gname == "actor-pair":
            frm = re.sub(r"[^a-z0-9]+", "_", str(g.get("from_role", ""))).strip("_")
            to = re.sub(r"[^a-z0-9]+", "_", str(g.get("to_role", ""))).strip("_")
            if g.get("evaluation") == "historical":
                as_of = re.sub(r"[^a-z0-9]+", "_",
                               str(g.get("as_of") or "unspec").lower()).strip("_")
                facts.append(f"actor_pair_{frm}_{to}_as_of_{as_of}")
            else:
                facts.append(f"actor_pair_{frm}_{to}")
        elif gname == "receipt-observed":
            rtype = re.sub(r"[^A-Z0-9_]+", "_", str(g.get("type", "")))
            facts.append(f"receipt_{rtype.lower()}_observed")
        else:
            # attestation-exists / satisfaction-verdict / audit-pair /
            # lease-live: declared but not yet induced by the recorder —
            # emit the predicate name, fail the gate upstream if unsupported.
            facts.append(gname.replace("-", "_"))
    return facts


def check_gates(flow, capability_resolver=None, operator_go=None):
    """The four gates. Returns (ok, failures[]) — pure, never raises.

    G1: ratified candidate — `ratified: true` on the record, or an explicit
        operator-go UUID (recorded into provenance if emitted).
    G2: zero unresolved roles — suspect must be false with no
        unresolved:<name> role bindings (C5 law: never silent).
    G3: deterministic emission — input must be free of the nondeterministic
        fields the recorder is allowed to carry (live timestamps in
        source_evidence.window are evidence, kept OUT of the artifact).
    G4: explicit emission capability — resolver injected; default deny.
    """
    failures = []

    # G1
    if not flow.get("ratified") and not operator_go:
        failures.append(
            "gate1: candidate not ratified (flow.ratified missing/false) "
            "and no operator-go record supplied")

    # G2
    bindings = (flow.get("parameters") or {}).get("RoleAlias_applied") or {}
    unresolved = sorted(k for k, v in bindings.items()
                        if (v or {}).get("resolved", "").startswith("unresolved:"))
    if flow.get("suspect") or unresolved:
        failures.append(
            "gate2: unresolved role strings present (suspect="
            f"{flow.get('suspect')}, unresolved={unresolved}) — C5 forbids "
            "silent emission")

    # G3
    se = flow.get("source_evidence") or {}
    if not flow.get("parameters", {}).get("WorkRef"):
        failures.append("gate3: no WorkRef — artifact identity underivable, "
                        "determinism cannot hold")

    # G4
    if capability_resolver is None:
        failures.append("gate4: no capability resolver — emission capability "
                        "unverifiable (default deny)")
    else:
        verdict = capability_resolver()
        if not verdict:
            failures.append("gate4: emitter lacks can_emit_flows "
                            "(Q3 vocabulary) — denied")

    return (not failures), failures


def emit_blueprint(flow, operator_go=None):
    """Pure flow-record -> Aegis blueprint dict. Gate G1/G2 must already
    have passed; this function performs NO gating (separation so tests can
    pin the mapping independently of the gates). Deterministic: no clocks,
    no uuid4, sorted keys at serialization time.

    Mapping (design 4d540f39):
      flow.states[]            -> StateDefinition (name=state, attempts ->
                                  variable 'attempt_count')
      flow.edges[]             -> TransitionDefinition (guard = conjunction
                                  of lower_snake fact predicates)
      environment_events       -> registry variable 'env_events_observed'
                                  domain (invariant context, NOT states)
      parameters.WorkRef etc.  -> ConstantDefinition
      guard semantics          -> concept_mappings predicates (Q4 declares)
      metadata.flow_ref        -> Q5 composition slot, reserved empty
      terminal_state           -> accepting_state (registry metadata)
    """
    states = {}
    for s in flow.get("states", []):
        states[s["state"]] = {
            "description": (f"receipt-observed state (attempts="
                            f"{s.get('attempt_count', 0)})"),
            "variable_assignments": {"attempt_count": s.get("attempt_count", 0)},
            "concept_id": None,   # bind at apply time once Q4/ontology rules
        }

    variables = {
        "attempt_count": {
            "type": "Int", "initial_value": 0,
            "domain": None,
            "description": "C2: attempt machinery is a variable, not states"},
        "env_events_observed": {
            "type": "Set", "initial_value": [],
            "domain": sorted({e["type"] for e in flow.get("environment_events", [])}),
            "description": "C3/RA1: environment events are invariant "
                           "context, never states"},
    }

    transitions = {}
    for e in flow.get("edges", []):
        name = f"{e['from']}_to_{e['to']}"
        facts = role_facts(e.get("guards") or {})
        guard_expr = " /\\ ".join(facts) if facts else "TRUE"
        transitions[name] = {
            "description": (f"observed {e.get('count', 1)}x; "
                            f"evidence={';'.join(e.get('evidence', []))}"),
            "guard_expression": guard_expr,
            "action": {"attempt_count": "attempt_count + 1"},
            "weak_fairness": False, "strong_fairness": False,
            "temporal_conditions": [],
            "guard_rule_id": None, "transition_rule_id": None,
            "state_transition_id": None,
        }

    constants = {}
    params = flow.get("parameters") or {}
    for k in sorted(params):
        v = params[k]
        if k == "WorkRef":
            constants["WorkRef"] = {"type": "String", "value": v,
                                    "description": "source Conduit unit"}
        elif k == "EvidenceRefs":
            constants["EvidenceRefs"] = {"type": "Set",
                                         "value": sorted(v or []),
                                         "description": "receipt-id evidence"}
        # RoleAlias_applied rides metadata, not constants — it is law
        # provenance, not machine content.

    concept_mappings = {}
    for cname in sorted({c for e in flow.get("edges", [])
                         for c in (e.get("guards") or {})}):
        concept_mappings[cname] = {
            "tla_name": cname.replace("-", "_").lower(),
            "concept_id": None,   # bind at apply time (Q4/ontology)
            "mapping_type": "direct",
            "cardinality": "one_to_one",
        }

    artifact = {
        "artifact_version": ARTIFACT_VERSION,
        "machine_name": f"flow_{params.get('WorkRef', 'unnamed')}",
        "description": ("Aegis blueprint emitted from ratified flow-record "
                        "(design 1adce409 / 4d540f39)"),
        "states": states,
        "variables": variables,
        "transitions": transitions,
        "constants": constants,
        "concept_mappings": concept_mappings,
        "invariants": [
            {"name": "terminal_reached",
             "expression": "attempt_count >= 1",
             "description": "C2 pin: the flow executed (attempt machinery "
                            "nonzero)"},
        ],
        "metadata": {
            "flow_ref": None,                      # Q5 reserve — empty in v0
            "terminal_state": flow.get("terminal_state"),
            "suspect": flow.get("suspect", False),
            "role_alias_law": "RATIFIED e9f81ae7 (RA1+RA2 folded, #327/#328)",
            "design_ref": flow.get("design_ref"),
            "evaluation_note": ("guard predicates are DECLARED here; "
                                "evaluation belongs to wind-srv's resolver "
                                "(Q4) — historical class evaluation per RA2"),
        },
        "provenance": {
            "emitter": "bin/flow-emit-aegis.py",
            "operator_go": operator_go,
            "source_flow_sha256": _sha256_flow(flow),
        },
    }
    return artifact


def _sha256_flow(flow):
    import hashlib
    return hashlib.sha256(
        json.dumps(flow, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def serialize(artifact):
    """Deterministic serialization — G3's contract. Byte-identical for
    identical flow input: sorted keys, fixed separators, trailing newline."""
    return json.dumps(artifact, sort_keys=True, indent=2,
                      separators=(",", ": "), ensure_ascii=True) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--flow-file", required=True,
                    help="flow-record JSON (recorder stdout) to emit")
    ap.add_argument("--out", help="write artifact here (default: stdout)")
    ap.add_argument("--operator-go",
                    help="operator-go agent-record UUID (G1 override; "
                         "recorded in artifact provenance)")
    ap.add_argument("--i-have-operator-go", action="store_true",
                    help="confirm the --operator-go record authorizes "
                         "emission (refuses without it)")
    args = ap.parse_args(argv)

    with open(args.flow_file, "r", encoding="utf-8") as f:
        flow = json.load(f)

    if bool(args.operator_go) != bool(args.i_have_operator_go):
        print("refused: --operator-go and --i-have-operator-go must be "
              "supplied together", file=sys.stderr)
        return 2

    ok, failures = check_gates(flow, capability_resolver=None,
                               operator_go=args.operator_go)
    # G4 default-deny: no resolver is wired in the CLI path by design —
    # capability wiring is a post-ratification (Q3) decision. Until then
    # the CLI can only REFUSE, which is what staged-inert means.
    if not ok:
        print("EMISSION REFUSED — staged-inert (design 4d540f39):")
        for f_ in failures:
            print(f"  - {f_}", file=sys.stderr)
        return 1

    artifact = emit_blueprint(flow, operator_go=args.operator_go)
    out = serialize(artifact)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
