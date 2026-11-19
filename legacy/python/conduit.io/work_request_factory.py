import json
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime
from executor_registry import ModelConfig

_log = logging.getLogger("conduit.work_request_factory")
from work_request import (
    CompletionCondition,
    ProducedFile,
    WorkRequestArtifacts,
    WorkRequestConstraints,
    WorkRequestDCO,
    WorkRequestDecomposition,
    WorkRequestExecutionState,
    WorkRequestIntent,
    WorkRequestLineage,
    WorkRequestMetadata,
    WorkRequestRequirements,
    WorkRequestStep,
    WorkRequestSuccessCriteria,
)


# ── helpers ────────────────────────────────────────────────────────────────

def _parse_json(val):
    """Parse a JSON string or list into a Python list, never raising."""
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, list) else [parsed]
        except (json.JSONDecodeError, TypeError) as exc:
            _log.debug("_parse_json: parse failed val=%r error=%s", val[:80], exc)
            return [val] if val.strip() else []
    if isinstance(val, list):
        return val
    return [] if val is None else [val]


def _infer_priority(plan: Dict[str, Any]) -> str:
    """Heuristic priority from plan metadata."""
    title = (plan.get("title") or "").lower()
    if any(kw in title for kw in ("critical", "urgent", "fix", "blocker")):
        priority = "high"
    elif any(kw in title for kw in ("clean", "refactor", "polish")):
        priority = "low"
    else:
        priority = "medium"
    _log.debug("_infer_priority: plan=%s priority=%s", plan.get("id", "?"), priority)
    return priority


def _infer_abstraction(plan: Dict[str, Any]) -> str:
    """Infer abstraction level from plan size / acceptance criteria count."""
    n_criteria = len(_parse_json(plan.get("acceptance_criteria")))
    n_files = len(_parse_json(plan.get("files_affected")))
    level = "system" if (n_criteria >= 4 or n_files >= 4) else "task"
    _log.debug("_infer_abstraction: plan=%s criteria=%d files=%d level=%s", plan.get("id", "?"), n_criteria, n_files, level)
    return level


def _build_completion_conditions(
    acceptance_criteria: List[str],
) -> List[CompletionCondition]:
    conditions = [
        CompletionCondition(
            condition=ac,
            evaluator="code-reviewer",
        )
        for ac in acceptance_criteria
    ]
    _log.debug("_build_completion_conditions: built %d conditions", len(conditions))
    return conditions


def _build_files_affected(plan: Dict[str, Any]) -> List[ProducedFile]:
    files = _parse_json(plan.get("files_affected"))
    if not files:
        _log.debug("_build_files_affected: no files affected for plan %s", plan.get("id", "?"))
        return []
    result = [
        ProducedFile(
            path=str(f),
            type="code",
            origin_step="step_1",
        )
        for f in files
    ]
    _log.debug("_build_files_affected: plan=%s files=%d", plan.get("id", "?"), len(result))
    return result


def _build_safety_constraints() -> List[str]:
    constraints = [
        "Do not delete or overwrite historical plan artifacts",
        "Do not modify .conduit-data/ directory structure",
        "Preserve existing receipt and audit records",
        "Follow existing project conventions when editing code",
    ]
    _log.debug("_build_safety_constraints: %d constraints", len(constraints))
    return constraints


class WorkRequestFactory:
    @staticmethod
    def create_from_plan(
        plan: Dict[str, Any],
        role: str = "builder",
        model_cfg: Optional[ModelConfig] = None,
        working_path: str = ".",
        session_id: str = "",
    ) -> WorkRequestDCO:
        plan_id = plan.get("id", "?")
        _log.info("create_from_plan: plan=%s role=%s model=%s",
                  plan_id, role, model_cfg.model if model_cfg else "default")
        wr_id = f"wr-{plan['id']}-{int(datetime.utcnow().timestamp())}"
        now = datetime.utcnow().isoformat() + "Z"

        acceptance_criteria = _parse_json(plan.get("acceptance_criteria"))
        files_affected = _build_files_affected(plan)

        # ── intent ────────────────────────────────────────────────────
        intent = WorkRequestIntent(
            problem_statement=plan.get("goal", ""),
            desired_outcome=plan.get("title", ""),
            domain="nexus",
            priority=_infer_priority(plan),
            user_intent_trace=plan.get("prompt_ref", ""),
            abstraction_level=_infer_abstraction(plan),
        )

        # ── decomposition ─────────────────────────────────────────────
        step = WorkRequestStep(
            step_id="step_1",
            description=(
                f"Plan {plan['id']}: {plan.get('title', '')}\n\n"
                f"Goal: {plan.get('goal', '')}\n\n"
                f"Content: {plan.get('content', '')}\n\n"
                f"Acceptance criteria:\n"
                + "\n".join(f"  - {ac}" for ac in acceptance_criteria)
            ),
            dependencies=[],
            outputs=["changes_committed"],
            type="execution",
        )

        decomposition = WorkRequestDecomposition(
            strategy="Direct implementation of plan steps",
            steps=[step],
            parallelism_model="sequential",
            recursion_allowed=False,
        )

        # ── requirements ──────────────────────────────────────────────
        requirements = WorkRequestRequirements(
            functional=acceptance_criteria,
            non_functional=[],
            system_requirements=[],
            tool_requirements=[],
        )

        # ── constraints ───────────────────────────────────────────────
        constraints = WorkRequestConstraints(
            forbidden_actions=[],
            safety_constraints=_build_safety_constraints(),
            resource_limits=None,
            architectural_constraints=[],
        )

        # ── success criteria ──────────────────────────────────────────
        success_criteria = WorkRequestSuccessCriteria(
            validation_rules=acceptance_criteria,
            acceptance_tests=acceptance_criteria,
            completion_conditions=_build_completion_conditions(
                acceptance_criteria
            ),
            failure_modes=[
                "Files affected list does not match actual changes",
                "Acceptance criteria not satisfied",
                "Typecheck or tests fail",
            ],
        )

        # ── execution state ───────────────────────────────────────────
        execution_state = WorkRequestExecutionState(
            status="pending",
            current_step="step_1",
            progress=0.0,
            retries=0,
            last_updated=now,
        )

        # ── lineage ───────────────────────────────────────────────────
        lineage = WorkRequestLineage(
            derived_from=[plan["id"]],
            supersedes=None,
            branches=[],
            merge_history=[],
        )

        # ── artifacts ─────────────────────────────────────────────────
        artifacts = WorkRequestArtifacts(
            produced_files=files_affected,
            intermediate_outputs=[],
        )

        # ── metadata ──────────────────────────────────────────────────
        metadata = WorkRequestMetadata(
            created_at=now,
            updated_at=now,
            agent_id="conduit",
            mode="default",
            tags=["plan-migration", role],
            role=role,
            harness=model_cfg.harness if model_cfg else "opencode",
            model=model_cfg.model if model_cfg else "",
            session_id=session_id,
        )

        wr = WorkRequestDCO(
            id=wr_id,
            version=1,
            path=working_path,  # executor_cloud.py uses this for artifact placement
            intent=intent,
            decomposition=decomposition,
            requirements=requirements,
            constraints=constraints,
            success_criteria=success_criteria,
            execution_state=execution_state,
            lineage=lineage,
            artifacts=artifacts,
            metadata=metadata,
        )
        _log.info("create_from_plan: created %s for plan=%s role=%s", wr_id, plan_id, role)
        return wr
