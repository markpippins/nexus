"""Canonical WorkRequest DCO model.

Mirrors /home/codex/dev/nexus/.agents/schema/work_request.schema.json.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class WorkRequestIntent(BaseModel):
    problem_statement: str
    desired_outcome: str
    domain: str
    priority: Literal["low", "medium", "high", "critical"]
    user_intent_trace: str
    abstraction_level: Literal["task", "system", "architecture", "research"]


class WorkRequestStep(BaseModel):
    step_id: str
    description: str
    dependencies: List[str]
    outputs: List[str]
    type: Literal["analysis", "generation", "transformation", "validation", "execution"]


class WorkRequestDecomposition(BaseModel):
    strategy: str
    steps: List[WorkRequestStep]
    parallelism_model: str
    recursion_allowed: bool


class WorkRequestRequirements(BaseModel):
    functional: List[str] = Field(default_factory=list)
    non_functional: List[str] = Field(default_factory=list)
    system_requirements: List[str] = Field(default_factory=list)
    tool_requirements: List[str] = Field(default_factory=list)


class WorkRequestResourceLimits(BaseModel):
    tokens: Optional[int] = None
    time_ms: Optional[int] = None
    memory_mb: Optional[int] = None


class WorkRequestConstraints(BaseModel):
    forbidden_actions: List[str] = Field(default_factory=list)
    safety_constraints: List[str] = Field(default_factory=list)
    resource_limits: Optional[WorkRequestResourceLimits] = None
    architectural_constraints: List[str] = Field(default_factory=list)


class CompletionCondition(BaseModel):
    condition: str
    evaluator: str


class WorkRequestSuccessCriteria(BaseModel):
    validation_rules: List[str] = Field(default_factory=list)
    acceptance_tests: List[str] = Field(default_factory=list)
    completion_conditions: List[CompletionCondition] = Field(default_factory=list)
    failure_modes: List[str] = Field(default_factory=list)


class WorkRequestExecutionState(BaseModel):
    status: Literal["pending", "decomposed", "in_progress", "blocked", "validating", "completed", "failed"]
    current_step: Optional[str] = None
    progress: Optional[float] = None
    retries: Optional[int] = None
    error_state: Optional[str] = None
    context_snapshot_ref: Optional[str] = None
    last_updated: Optional[str] = None


class MergeHistoryItem(BaseModel):
    from_: Optional[str] = Field(default=None, alias="from")
    strategy: Optional[str] = None


class WorkRequestLineage(BaseModel):
    derived_from: List[str] = Field(default_factory=list)
    supersedes: Optional[str] = None
    branches: List[str] = Field(default_factory=list)
    merge_history: List[MergeHistoryItem] = Field(default_factory=list)


class ProducedFile(BaseModel):
    path: Optional[str] = None
    type: Optional[str] = None
    hash: Optional[str] = None
    origin_step: Optional[str] = None


class IntermediateOutput(BaseModel):
    step_id: Optional[str] = None
    data_ref: Optional[str] = None


class WorkRequestArtifacts(BaseModel):
    produced_files: List[ProducedFile] = Field(default_factory=list)
    intermediate_outputs: List[IntermediateOutput] = Field(default_factory=list)


class WorkRequestMetadata(BaseModel):
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    agent_id: Optional[str] = None
    mode: Optional[str] = None
    tags: List[str] = Field(default_factory=list)

    model_config = {"extra": "allow"}


class WorkRequestDCO(BaseModel):
    id: str
    version: int
    intent: WorkRequestIntent
    decomposition: WorkRequestDecomposition
    requirements: WorkRequestRequirements
    constraints: WorkRequestConstraints
    success_criteria: WorkRequestSuccessCriteria
    execution_state: WorkRequestExecutionState
    lineage: WorkRequestLineage
    artifacts: WorkRequestArtifacts
    metadata: WorkRequestMetadata

    model_config = {"extra": "allow"}


class WorkResultEvent(BaseModel):
    """Immutable execution outcome written to the audit trail."""

    work_request_id: str
    status: Literal["success", "failure"]
    outputs: List[str] = Field(default_factory=list)
    artifacts: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    timestamp: str
    executor_id: Optional[str] = None
    harness: Optional[str] = None
    model: Optional[str] = None
    files_written: List[str] = Field(default_factory=list)


__all__ = ["WorkRequestDCO", "WorkResultEvent"]
