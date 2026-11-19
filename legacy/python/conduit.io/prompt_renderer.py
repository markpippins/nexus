"""Shared utilities for rendering a WorkRequest DCO into an opencode prompt.

Both the legacy `executor_cloud.py` path and the Temporal activity path need
identical formatting, so we centralise the logic here to guarantee parity.
"""

import logging
from typing import Any, Dict, List

_log = logging.getLogger("conduit.prompt_renderer")


def _resolve_role(req: Dict[str, Any]) -> str:
    """Extract the agent role from DCO metadata (defaults to 'builder')."""
    role = (req.get("metadata") or {}).get("role", "")
    all_roles = {"builder", "reviewer", "planner", "critic", "analyst", "architect", "inspector", "engineer", "rover"}
    resolved = role if role in all_roles else "builder"
    _log.debug("_resolve_role: raw=%s resolved=%s", role, resolved)
    return resolved


def _serialize_dco_for_prompt(req: Dict[str, Any]) -> str:
    """Render the full WorkRequest DCO as structured text for the agent.

    The implementation mirrors the original `_serialize_dco_for_prompt` found in
    `executor_cloud.py`.  Keeping the function identical ensures that both legacy
    and Temporal execution paths produce the same prompt output.
    """
    intent = req.get("intent", {})
    decomposition = req.get("decomposition", {})
    requirements = req.get("requirements", {})
    constraints = req.get("constraints", {})
    success = req.get("success_criteria", {})
    artifacts = req.get("artifacts", {})
    lineage = req.get("lineage", {})
    meta = req.get("metadata", {})

    def _kv(k: str, v: Any) -> str:
        return f"  - **{k}:** {v}"

    blocks: List[str] = []

    # ── Intent ──
    blocks.append("## Intent")
    if intent.get("problem_statement"):
        blocks.append(_kv("Problem", intent["problem_statement"]))
    if intent.get("desired_outcome"):
        blocks.append(_kv("Outcome", intent["desired_outcome"]))
    blocks.append(_kv("Priority", intent.get("priority", "medium")))
    blocks.append(_kv("Abstraction", intent.get("abstraction_level", "task")))
    if intent.get("user_intent_trace"):
        blocks.append(_kv("Prompt ref", intent["user_intent_trace"]))

    # ── Decomposition ──
    steps = decomposition.get("steps", [])
    blocks.append("\n## Decomposition")
    blocks.append(_kv("Strategy", decomposition.get("strategy", "")))
    for i, s in enumerate(steps, 1):
        blocks.append(f"  **Step {i}** [{s.get('type', 'execution')}]: {s.get('description', '')[:300]}")

    # ── Requirements (functional) ──
    func = requirements.get("functional", [])
    if func:
        blocks.append("\n## Requirements (functional)")
        for ac in func:
            blocks.append(f"  - {ac}")

    # ── Constraints (safety) ──
    safety = constraints.get("safety_constraints", [])
    if safety:
        blocks.append("\n## Constraints")
        for sc in safety:
            blocks.append(f"  - {sc}")

    # ── Success Criteria ──
    conditions = success.get("completion_conditions", [])
    if conditions:
        blocks.append("\n## Success Criteria")
        for c in conditions:
            blocks.append(f"  - {c.get('condition', '')}")

    # ── Artifacts / Target Files ──
    files = artifacts.get("produced_files", [])
    if files:
        blocks.append("\n## Target Files")
        for f in files:
            blocks.append(f"  - {f.get('path', '?')}")

    # ── Lineage ──
    derived = lineage.get("derived_from", [])
    if derived:
        blocks.append(f"\n## Lineage: derived from {', '.join(derived)}")

    # ── Metadata tags ──
    tags = meta.get("tags", [])
    if tags:
        blocks.append(f"\n## Metadata: tags={', '.join(tags)}")

    return "\n".join(blocks)


def build_opencode_prompt(req: Dict[str, Any], working_path: str) -> str:
    """High‑level wrapper that assembles the full prompt string.

    It delegates to `_serialize_dco_for_prompt` for the DCO body, then appends
    role‑specific instructions and the working‑directory footer – exactly as the
    original `_build_opencode_prompt` did in both code paths.
    """
    role = _resolve_role(req)
    dco_text = _serialize_dco_for_prompt(req)
    lines = [dco_text, f"\n## Working directory\n{working_path}"]

    # Role‑specific instruction blocks
    if role == "builder":
        lines.extend([
            "\n## Instructions",
            "Execute this WorkRequest. Implement the plan, modifying only "
            "the files listed in Target Files. Satisfy all acceptance criteria "
            "and completion conditions. Respect all safety constraints.",
        ])
    elif role == "reviewer":
        lines.extend([
            "\n## Instructions",
            "Review the implementation described in this WorkRequest. "
            "Compare the change report in CHANGES/committed/ against the plan. "
            "If changes match the acceptance criteria, issue a REVIEW_PASS receipt. "
            "If they don't match, issue a REVIEW_REJECT receipt with explanation.",
        ])
    elif role == "planner":
        lines.extend([
            "\n## Instructions",
            "Elucidate the proposed plan in this WorkRequest. "
            "Define acceptance criteria, identify files affected, and note dependencies. "
            "When the plan is fully defined, issue a PLAN_CREATE receipt.",
        ])
    elif role == "critic":
        lines.extend([
            "\n## Instructions",
            "Critique the plan in this WorkRequest. "
            "Evaluate the acceptance criteria, identify gaps, suggest improvements. "
            "Issue a CRITIQUE_PASS or CRITIQUE_REJECT receipt.",
        ])

    lines.append("\nDo NOT issue receipts — the conduit manager handles the audit trail.")
    return "\n".join(lines)
