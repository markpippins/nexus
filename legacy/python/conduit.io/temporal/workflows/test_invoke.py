"""TestInvokeWorkflow — Temporal Workflow for model test-invocation.

Provides durable lifecycle management for test-invoke sessions:
signal-based cancellation, status queries, and proper session cleanup.

Usage (TypeScript side, via temporal-client.ts):
    await startTestInvokeWorkflow(modelId, testPrompt, sessionId);
"""

import asyncio
from datetime import timedelta, datetime
from typing import Any, Dict, Optional

from temporalio import workflow


@workflow.defn
class TestInvokeWorkflow:
    """Execute a simple model test invocation with Temporal lifecycle."""

    def __init__(self):
        self._model_id = ""
        self._test_prompt = ""
        self._session_id = ""
        self._current_step = "initializing"
        self._cancelled = False

    @workflow.run
    async def run(
        self,
        model_id: str,
        test_prompt: str,
        session_id: str,
    ) -> Dict[str, Any]:
        """Execute a single model test invocation.

        Returns a dict with:
          - status: "completed" | "failed" | "cancelled"
          - exit_code: int
          - session_id: str
        """
        self._model_id = model_id
        self._test_prompt = test_prompt
        self._session_id = session_id

        workflow.logger.info(
            f"TestInvokeWorkflow START model={model_id} session={session_id}"
        )

        if self._cancelled:
            return {"status": "cancelled", "exit_code": -1, "session_id": session_id}

        # Step 1: Resolve model config (harness + identifier)
        self._current_step = "resolve_model"
        model_cfg = await workflow.execute_activity(
            "resolve_test_model_activity",
            args=[model_id],
            start_to_close_timeout=timedelta(seconds=15),
        )
        if not model_cfg:
            workflow.logger.error(
                f"TestInvokeWorkflow END model={model_id} result=failed reason=model_not_found"
            )
            return {"status": "failed", "exit_code": 1, "session_id": session_id}

        if self._cancelled:
            return {"status": "cancelled", "exit_code": -1, "session_id": session_id}

        # Step 2: Execute the model with the test prompt
        self._current_step = "execute"

        try:
            result = await workflow.execute_activity(
                "execute_test_invoke_activity",
                args=[model_cfg, test_prompt, session_id],
                retry_policy=workflow.RetryPolicy(
                    maximum_attempts=1,
                    non_retryable_error_types=["HarnessError", "LaunchError"],
                ),
                heartbeat_timeout=timedelta(seconds=30),
                start_to_close_timeout=timedelta(minutes=30),
            )

            if self._cancelled:
                return {"status": "cancelled", "exit_code": -1, "session_id": session_id}

            exit_code = result.get("exit_code", 0)
            status = "completed" if exit_code == 0 else "failed"

            workflow.logger.info(
                f"TestInvokeWorkflow END model={model_id} "
                f"session={session_id} status={status} exit_code={exit_code}"
            )

            return {
                "status": status,
                "exit_code": exit_code,
                "session_id": session_id,
                "output": result.get("output", ""),
                "stderr": result.get("stderr", ""),
                "tokens_used": result.get("tokens_used", 0),
            }

        except workflow.ActivityError as e:
            workflow.logger.error(
                f"TestInvokeWorkflow ERROR model={model_id} "
                f"session={session_id}: {e}"
            )
            return {
                "status": "failed",
                "exit_code": -1,
                "session_id": session_id,
                "error": str(e),
            }

    @workflow.signal
    async def cancel(self):
        """Signal handler for external cancellation."""
        self._cancelled = True
        workflow.logger.info(
            f"TestInvokeWorkflow CANCELLED model={self._model_id} "
            f"session={self._session_id}"
        )

    @workflow.query
    def status(self) -> dict:
        """Query handler for UI status."""
        return {
            "model_id": self._model_id,
            "session_id": self._session_id,
            "current_step": self._current_step,
            "cancelled": self._cancelled,
        }
