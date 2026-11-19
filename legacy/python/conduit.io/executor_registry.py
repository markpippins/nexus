from __future__ import annotations

import logging
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ── Module-level logger ─────────────────────────────────────────────
_log = logging.getLogger("conduit.executor_registry")


class InvocationContract(BaseModel):
    type: Literal["cli", "http", "module"]
    command: Optional[str] = None
    model_config = {"extra": "allow"}


class ExecutorRegistration(BaseModel):
    executor_id: str
    supports: List[str]
    invocation_contract: InvocationContract
    system_prompt: Optional[str] = None
    model_config = {"extra": "allow"}


class ModelConfig(BaseModel):
    """A harness + model pair used to resolve which AI to invoke."""
    harness: Literal["opencode", "ollama", "codex"]
    model: str


class RegistryConfig(BaseModel):
    """Top-level registry: default/fallback models + executor catalogue."""
    default_model: ModelConfig
    fallback_model: ModelConfig
    executors: List[ExecutorRegistration] = Field(default_factory=list)
    model_config = {"extra": "allow"}


# load_registry removed — executor resolution uses DB exclusively via DBAdapter.


def resolve_executor(
    registry: RegistryConfig, harness: str
) -> ExecutorRegistration:
    """Return the first registered executor that supports *harness*.

    Raises :exc:`ValueError` when no executor matches.
    """
    _log.debug("resolve_executor: entry harness=%s", harness)
    for executor in registry.executors:
        if harness in executor.supports:
            _log.debug("resolve_executor: matched executor=%s harness=%s",
                       executor.executor_id, harness)
            return executor
    _log.warning("resolve_executor: no executor for harness=%s", harness)
    raise ValueError(
        f"No executor registered for harness '{harness}'. "
        f"Available harnesses: {available_harnesses(registry)}"
    )


def available_harnesses(registry: RegistryConfig) -> list[str]:
    """Return every harness covered by the registry's executors."""
    seen: set[str] = set()
    for executor in registry.executors:
        seen.update(executor.supports)
    harnesses = sorted(seen)
    _log.debug("available_harnesses: count=%d harnesses=%s", len(harnesses), harnesses)
    return harnesses


__all__ = [
    "ExecutorRegistration",
    "InvocationContract",
    "ModelConfig",
    "RegistryConfig",
    "available_harnesses",
    "resolve_executor",
]
