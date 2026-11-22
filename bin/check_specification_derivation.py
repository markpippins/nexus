#!/usr/bin/env python3
"""Guard: a Specification -> Requirement derivation package is well formed.

Why this exists
---------------
The operator asked to derive Specifications (grouped collections of
Requirements) from the Analyst's synthesis and, per Requirement, a Blueprint and
a WorkRequest. The live type layer cannot yet represent that chain:

  * no Specification can group Requirements (resolution.specification is a
    per-agenda revision of ONE requirement; nebula.spec_requirements is empty
    and points at spec documents),
  * the live Requirements are in nebula.requirements (27) while V220's link
    table FKs to resolution.requirement (0),
  * POST /api/plans hardcodes payload.spec_ref/requirement_ref to null, so a
    Blueprint is born without a parent,
  * resolution.request_requirement_link is empty and V221 removed the
    source_requirement_id shortcut.

So the derivation has to exist as a reviewable artifact BEFORE the type work,
and it has to be checkable. This guard checks the artifact, not the database:
structure, referential integrity of dependencies, and that every Requirement
states how it would be verified and compiled. It deliberately does not assert
representability -- that is what the recorded type gaps are for, and asserting
it here would encode today's schema as if it were the target.

Hermetic: reads one JSON file. No database, no network.

Dual-runnable: `pytest bin/tests/test_check_specification_derivation.py` or
`python3 bin/tests/test_check_specification_derivation.py`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REQUIRED_TOP = ("artifact", "version", "status", "derived_from", "specifications")
REQUIRED_REQUIREMENT = (
    "id",
    "title",
    "outcome",
    "acceptance_criteria",
    "evidence",
    "depends_on",
    "verification_owner",
    "blueprint_intent",
    "work_request_intent",
)
REQUIRED_BLUEPRINT = ("approach", "deliverable", "tests")
REQUIRED_WORK_REQUEST = ("request_kind", "deliverable", "compile_via")
VERIFICATION_OWNERS = {"tester", "reviewer", "dba", "analyst", "architect", "operator"}


def _non_empty_str(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _non_empty_list(value: object) -> bool:
    return isinstance(value, list) and len(value) > 0 and all(_non_empty_str(v) for v in value)


def validate(pkg: object) -> list[str]:
    """Return a list of human-readable problems; empty means the package is valid."""
    errors: list[str] = []
    if not isinstance(pkg, dict):
        return ["package root is not a JSON object"]

    for key in REQUIRED_TOP:
        if key not in pkg:
            errors.append(f"missing top-level field: {key}")
    if errors:
        return errors

    if not _non_empty_str(pkg.get("artifact")):
        errors.append("artifact must be a non-empty string")
    if not isinstance(pkg.get("version"), int) or pkg["version"] < 1:
        errors.append("version must be a positive integer")
    derived_from = pkg.get("derived_from")
    if not isinstance(derived_from, dict) or not _non_empty_str(derived_from.get("record_id")):
        errors.append("derived_from.record_id is required (provenance)")

    gap_ids = {
        gap.get("id")
        for gap in pkg.get("type_gaps", [])
        if isinstance(gap, dict) and _non_empty_str(gap.get("id"))
    }

    specs = pkg.get("specifications")
    if not isinstance(specs, list) or not specs:
        return errors + ["specifications must be a non-empty list"]

    seen_specs: set[str] = set()
    seen_reqs: set[str] = set()
    depends_on: dict[str, list[str]] = {}

    for index, spec in enumerate(specs):
        where = f"specifications[{index}]"
        if not isinstance(spec, dict):
            errors.append(f"{where} is not an object")
            continue
        spec_id = spec.get("id")
        if not _non_empty_str(spec_id):
            errors.append(f"{where}.id is required")
            continue
        where = spec_id
        if spec_id in seen_specs:
            errors.append(f"{where}: duplicate specification id")
        seen_specs.add(spec_id)
        for field in ("title", "goal", "outcome"):
            if not _non_empty_str(spec.get(field)):
                errors.append(f"{where}.{field} is required")

        requirements = spec.get("requirements")
        if not isinstance(requirements, list) or not requirements:
            errors.append(f"{where}: needs at least one requirement")
            continue

        for req_index, req in enumerate(requirements):
            rwhere = f"{where}.requirements[{req_index}]"
            if not isinstance(req, dict):
                errors.append(f"{rwhere} is not an object")
                continue
            req_id = req.get("id")
            if not _non_empty_str(req_id):
                errors.append(f"{rwhere}.id is required")
                continue
            rwhere = req_id
            if req_id in seen_reqs:
                errors.append(f"{rwhere}: duplicate requirement id")
            seen_reqs.add(req_id)
            if not req_id.startswith(f"{spec_id}."):
                errors.append(f"{rwhere}: id must be namespaced under its specification ({spec_id}.<n>)")

            for field in REQUIRED_REQUIREMENT:
                if field not in req:
                    errors.append(f"{rwhere}: missing field {field}")

            for field in ("title", "outcome"):
                if not _non_empty_str(req.get(field)):
                    errors.append(f"{rwhere}.{field} must be a non-empty string")
            if not _non_empty_list(req.get("acceptance_criteria")):
                errors.append(f"{rwhere}.acceptance_criteria must be a non-empty list of strings")
            if not _non_empty_list(req.get("evidence")):
                errors.append(f"{rwhere}.evidence must name at least one piece of evidence")
            if req.get("verification_owner") not in VERIFICATION_OWNERS:
                errors.append(
                    f"{rwhere}.verification_owner must be one of {sorted(VERIFICATION_OWNERS)}"
                )
            if not isinstance(req.get("representable_today"), bool):
                errors.append(f"{rwhere}.representable_today must be a boolean")

            blocked_by = req.get("blocked_by", [])
            if not isinstance(blocked_by, list):
                errors.append(f"{rwhere}.blocked_by must be a list")
            else:
                for gap in blocked_by:
                    if gap not in gap_ids:
                        errors.append(f"{rwhere}.blocked_by names undeclared gap {gap!r}")
                if req.get("representable_today") is True and blocked_by:
                    errors.append(f"{rwhere}: representable_today=true contradicts blocked_by")

            deps = req.get("depends_on", [])
            if not isinstance(deps, list) or not all(_non_empty_str(d) for d in deps):
                errors.append(f"{rwhere}.depends_on must be a list of requirement ids")
                deps = []
            if req_id in deps:
                errors.append(f"{rwhere}.depends_on must not reference itself")
            depends_on[req_id] = deps

            blueprint = req.get("blueprint_intent")
            if not isinstance(blueprint, dict) or any(
                not _non_empty_str(blueprint.get(f)) for f in REQUIRED_BLUEPRINT
            ):
                errors.append(
                    f"{rwhere}.blueprint_intent needs {', '.join(REQUIRED_BLUEPRINT)}"
                )
            work_request = req.get("work_request_intent")
            if not isinstance(work_request, dict) or any(
                not _non_empty_str(work_request.get(f)) for f in REQUIRED_WORK_REQUEST
            ):
                errors.append(
                    f"{rwhere}.work_request_intent needs {', '.join(REQUIRED_WORK_REQUEST)}"
                )

    for req_id, deps in depends_on.items():
        for dep in deps:
            if dep not in seen_reqs:
                errors.append(f"{req_id}.depends_on references unknown requirement {dep!r}")

    errors.extend(_cycles(depends_on))
    return errors


def _cycles(graph: dict[str, list[str]]) -> list[str]:
    """Report dependency cycles (DFS with a colour map)."""
    state: dict[str, int] = {}
    found: list[str] = []

    def visit(node: str, stack: list[str]) -> None:
        marker = state.get(node, 0)
        if marker == 1:
            cycle = " -> ".join(stack[stack.index(node):] + [node])
            found.append(f"dependency cycle: {cycle}")
            return
        if marker == 2:
            return
        state[node] = 1
        for dep in graph.get(node, []):
            if dep in graph:
                visit(dep, stack + [node])
        state[node] = 2

    for node in graph:
        visit(node, [])
    return found


def summarise(pkg: dict) -> str:
    specs = pkg["specifications"]
    reqs = [r for s in specs for r in s.get("requirements", [])]
    representable = sum(1 for r in reqs if r.get("representable_today") is True)
    return (
        f"{len(specs)} specification(s), {len(reqs)} requirement(s), "
        f"{representable} representable today, {len(reqs) - representable} blocked "
        f"by {len(pkg.get('type_gaps', []))} recorded type gap(s)"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("package", type=Path, help="derivation package (.json)")
    parser.add_argument("--quiet", action="store_true", help="only print failures")
    args = parser.parse_args(argv)

    try:
        pkg = json.loads(args.package.read_text())
    except FileNotFoundError:
        print(f"FAIL {args.package}: file not found", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"FAIL {args.package}: invalid JSON: {exc}", file=sys.stderr)
        return 1

    errors = validate(pkg)
    if errors:
        print(f"FAIL {args.package}: {len(errors)} problem(s)", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"OK {args.package}: {summarise(pkg)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
