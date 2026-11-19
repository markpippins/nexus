"""PlanExecutionWorkflow — Temporal Workflow port of main.py:_dispatch_one().

Orchestrates the full lifecycle: claim ticket → build DCO → execute with
model chain (primary + fallbacks) → handle success/failure → advance cursor.

This replaces the ~300-line nested retry/fallback loop in _dispatch_one()
with a clean, durable Workflow that survives process crashes.
"""

import asyncio
from datetime import timedelta
from typing import Any, Dict, List, Optional

from temporalio import workflow
from temporalio.common import RetryPolicy

# Import Activities via temporalio proxy (deterministic import)
from temporalio.workflow import ActivityConfig


@workflow.defn
class PlanExecutionWorkflow:
    """Execute a single plan for a given role with model chain fallbacks."""

    def __init__(self):
        self._plan_id = ""
        self._role = ""
        self._current_step = "initializing"
        self._cancelled = False

    @workflow.run
    async def run(
        self,
        plan_id: str,
        role: str = "builder",
        force: bool = False,
    ) -> str:
        """Execute the plan lifecycle.

        Returns: "completed", "failed", "skipped", or "cancelled"
        """
        self._plan_id = plan_id
        self._role = role

        workflow.logger.info(
            f"PlanExecutionWorkflow START plan={plan_id} role={role} force={force}"
        )

        # Check circuit breaker (skip if forced)
        if not force:
            tripped = await workflow.execute_activity(
                "is_circuit_breaker_tripped_activity",
                start_to_close_timeout=timedelta(seconds=10),
            )
            if tripped:
                workflow.logger.warning(
                    f"PlanExecutionWorkflow END plan={plan_id} result=blocked_by_circuit_breaker"
                )
                return "blocked"

        if self._cancelled:
            return "cancelled"

        # Step 1: Claim a Ticket
        session_id = f"{role}-{workflow.now().strftime('%Y%m%d-%H%M%S')}"
        self._current_step = "claim_ticket"

        ticket_id = await workflow.execute_activity(
            "claim_ticket_activity",
            args=[plan_id, role, session_id],
            start_to_close_timeout=timedelta(seconds=10),
        )
        if not ticket_id:
            workflow.logger.info(
                f"PlanExecutionWorkflow END plan={plan_id} result=skipped reason=ticket_already_claimed"
            )
            return "skipped"

        # Create session record in DB with Temporal workflow metadata
        wf_info = workflow.info()
        await workflow.execute_activity(
            "create_session_activity",
            args=[session_id, role, [plan_id], None,  # pid not available here
                  wf_info.workflow_id, wf_info.run_id,
                  wf_info.start_time.isoformat() if wf_info.start_time else None],
            start_to_close_timeout=timedelta(seconds=10),
        )

        # ── All paths after this point must close the session ──
        # Wrapped in try/finally so cancelled, plan-not-found, and
        # model-chain-empty paths don't orphan sessions.
        result: Optional[Dict[str, Any]] = None
        exit_code = -1

        try:
            if self._cancelled:
                await workflow.execute_activity(
                    "release_ticket_activity",
                    args=[plan_id, role, session_id],
                    start_to_close_timeout=timedelta(seconds=10),
                )
                return "cancelled"

            # Step 2: Check requeue cycle count — prevent infinite cycling
            self._current_step = "check_requeue"
            MAX_REQUEUE_CYCLES = 3
            requeue_count = await workflow.execute_activity(
                "get_requeue_count_activity",
                args=[plan_id],
                start_to_close_timeout=timedelta(seconds=10),
            )
            if requeue_count >= MAX_REQUEUE_CYCLES:
                workflow.logger.warning(
                    f"PlanExecutionWorkflow BLOCK plan={plan_id} "
                    f"reason=max_requeue_cycles req={requeue_count}"
                )
                await workflow.execute_activity(
                    "insert_receipt_activity",
                    args=[plan_id, "BLOCK", role, session_id, ticket_id,
                          f"Max requeue cycles ({MAX_REQUEUE_CYCLES}) exceeded. "
                          f"Plan has been requeued {requeue_count} times. "
                          f"Review the plan scope or split into smaller tasks.",
                          {"error": "max_requeue_cycles", "requeue_count": requeue_count},
                          0],
                    start_to_close_timeout=timedelta(seconds=10),
                )
                await workflow.execute_activity(
                    "close_ticket_activity",
                    args=[plan_id, role, session_id, "failed"],
                    start_to_close_timeout=timedelta(seconds=10),
                )
                # Set result so the finally block logs "blocked" not "failed"
                result = {"status": "blocked", "exit_code": 0}
                return "blocked"

            # Step 3: Get the plan data
            self._current_step = "get_plan"
            plan = await workflow.execute_activity(
                "get_plan_by_id_activity",
                args=[plan_id],
                start_to_close_timeout=timedelta(seconds=10),
            )
            if not plan:
                await workflow.execute_activity(
                    "release_ticket_activity",
                    args=[plan_id, role, session_id],
                    start_to_close_timeout=timedelta(seconds=10),
                )
                return "failed"

            # Step 4: Build the WorkRequest DCO
            self._current_step = "build_dco"

            # Resolve model chain
            model_chain = await workflow.execute_activity(
                "resolve_model_chain_activity",
                args=[role],
                start_to_close_timeout=timedelta(seconds=10),
            )
            if not model_chain:
                await workflow.execute_activity(
                    "insert_receipt_activity",
                    args=[plan_id, "BLOCK", role, session_id, ticket_id,
                          f"No model configuration found for role={role}. "
                          f"Configure a model in AI Settings.",
                          {"error": "no_model_config", "role": role},
                          0],
                    start_to_close_timeout=timedelta(seconds=10),
                )
                await workflow.execute_activity(
                    "close_ticket_activity",
                    args=[plan_id, role, session_id, "failed"],
                    start_to_close_timeout=timedelta(seconds=10),
                )
                return "failed"

            # Step 4b: Pre-flight provider health check (skip if forced)
            if not force:
                model_chain = await workflow.execute_activity(
                    "check_provider_health_activity",
                    args=[model_chain],
                    start_to_close_timeout=timedelta(seconds=30),
                )

            if not model_chain:
                workflow.logger.warning(
                    f"PlanExecutionWorkflow BLOCK plan={plan_id} "
                    f"reason=all_providers_down"
                )
                await workflow.execute_activity(
                    "insert_receipt_activity",
                    args=[plan_id, "BLOCK", role, session_id, ticket_id,
                          f"All AI providers are unreachable for role={role}. "
                          f"Check local infrastructure (ollama, opencode).",
                          {"error": "all_providers_down", "role": role},
                          0],
                    start_to_close_timeout=timedelta(seconds=10),
                )
                await workflow.execute_activity(
                    "close_ticket_activity",
                    args=[plan_id, role, session_id, "failed"],
                    start_to_close_timeout=timedelta(seconds=10),
                )
                return "failed"

            # Step 5: Execute with progressive fallback
            self._current_step = "execute"
            final_model = model_chain[0]

            result = await self._execute_with_chain(
                plan=plan,
                plan_id=plan_id,
                role=role,
                model_chain=model_chain,
                session_id=session_id,
                ticket_id=ticket_id,
            )
        finally:
            exit_code = result.get("exit_code", -1) if result else -1
            # Compute workflow timing metadata
            wf_info = workflow.info()
            close_time = workflow.now().isoformat()
            run_time_ms = None
            if wf_info.start_time:
                delta = workflow.now() - wf_info.start_time
                run_time_ms = delta.total_seconds() * 1000
            wf_result = result.get("status", "unknown") if result else "failed"
            await workflow.execute_activity(
                "close_session_activity",
                args=[session_id, exit_code, close_time, run_time_ms, wf_result],
                start_to_close_timeout=timedelta(seconds=10),
            )

        if result is None or result.get("status") == "failed":
            # All models exhausted — trip circuit breaker and requeue
            fr_config = await workflow.execute_activity(
                "get_failure_recovery_config_activity",
                start_to_close_timeout=timedelta(seconds=10),
            )
            if fr_config.get("push_back_to_pending", True):
                await workflow.execute_activity(
                    "trip_and_requeue_activity",
                    args=[
                        plan_id, role, session_id,
                        f"All models exhausted for {role}",
                        result.get("error_summary", "") if result else "",
                        final_model,
                    ],
                    start_to_close_timeout=timedelta(seconds=10),
                )

            await workflow.execute_activity(
                "close_ticket_activity",
                args=[plan_id, role, session_id, "failed"],
                start_to_close_timeout=timedelta(seconds=10),
            )
            return "failed"

        # Success path
        await workflow.execute_activity(
            "close_ticket_activity",
            args=[plan_id, role, session_id, "completed"],
            start_to_close_timeout=timedelta(seconds=10),
        )
        await workflow.execute_activity(
            "advance_cursor_activity",
            args=[role, plan_id, result.get("wr_id", "")],
            start_to_close_timeout=timedelta(seconds=10),
        )

        # Spawn next tickets
        await workflow.execute_activity(
            "create_next_tickets_activity",
            args=[plan_id, role, "completed", ticket_id,
                  plan.get("title") or plan.get("goal", ""),
                  role],
            start_to_close_timeout=timedelta(seconds=10),
        )

        workflow.logger.info(
            f"PlanExecutionWorkflow END plan={plan_id} result=completed"
        )
        return "completed"

    async def _execute_with_chain(
        self,
        plan: Dict[str, Any],
        plan_id: str,
        role: str,
        model_chain: List[Dict[str, str]],
        session_id: str,
        ticket_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Execute the model chain with retry and fallback."""

        fr_config = await workflow.execute_activity(
            "get_failure_recovery_config_activity",
            start_to_close_timeout=timedelta(seconds=10),
        )
        max_retries = fr_config.get("max_retries_per_model", 3)
        retry_delay = fr_config.get("retry_delay_seconds", 120)
        max_fallbacks = min(fr_config.get("max_fallbacks", 3), len(model_chain) - 1)

        # Build the effective chain: primary + up to max_fallbacks fallbacks
        effective_chain = model_chain[:1 + max_fallbacks]

        last_error = None
        for model_idx, model_cfg in enumerate(effective_chain):
            if self._cancelled:
                return None

            # Rebuild DCO when switching models (after first)
            if model_idx > 0:
                workflow.logger.warning(
                    f"Fallback switch plan={plan_id} to={model_cfg['model']} "
                    f"harness={model_cfg['harness']}"
                )

            dco_result = await workflow.execute_activity(
                "build_work_request_dco_activity",
                args=[plan, role, model_cfg, "", session_id],
                start_to_close_timeout=timedelta(seconds=10),
            )

            await workflow.execute_activity(
                "add_work_request_activity",
                args=[dco_result["wr_id"], plan_id,
                      _json_dumps(dco_result["dco"])],
                start_to_close_timeout=timedelta(seconds=10),
            )

            # Execute with retry policy per model
            for attempt in range(1, max_retries + 1):
                if self._cancelled:
                    return None

                workflow.logger.info(
                    f"Execute plan={plan_id} model={model_cfg['model']} "
                    f"attempt={attempt}/{max_retries}"
                )

                try:
                    working_path = dco_result["dco"].get("path", "/home/codex/dev")
                    result = await workflow.execute_activity(
                         "execute_with_model",
                         args=[model_cfg, dco_result["dco"],
                                   dco_result["wr_id"],
                                   dco_result["executor_cmd"],
                                   ticket_id,
                                   working_path,
                                   session_id],
                            retry_policy=RetryPolicy(
                                initial_interval=timedelta(seconds=retry_delay),
                                maximum_attempts=1,  # We handle retries in the workflow
                                non_retryable_error_types=[
                                    "HarnessError",
                                    "LaunchError",
                                    "RateLimitError",
                            ],
                        ),
                        heartbeat_timeout=timedelta(seconds=30),
                        start_to_close_timeout=timedelta(minutes=35),
                    )

                    # Success!
                    await workflow.execute_activity(
                        "insert_receipt_activity",
                        args=[plan_id, _SUCCESS_RECEIPTS.get(role, "IMPLEMENTATION"),
                              role, session_id, ticket_id,
                              f"{role} completed via {dco_result['wr_id']}",
                              {
                                  "work_request_id": dco_result["wr_id"],
                                  "role": role,
                                  "harness": model_cfg["harness"],
                                  "model": model_cfg["model"],
                                  "exit_code": result["exit_code"],
                                  "attempt": attempt,
                                  "model_chain_index": model_idx,
                              },
                              result.get("tokens_used", 0)],
                        start_to_close_timeout=timedelta(seconds=10),
                    )

                    if result.get("tokens_used", 0) > 0:
                        await workflow.execute_activity(
                            "increment_ticket_tokens_activity",
                            args=[ticket_id, result["tokens_used"]],
                            start_to_close_timeout=timedelta(seconds=10),
                        )

                    await workflow.execute_activity(
                        "update_work_request_status_activity",
                        args=[dco_result["wr_id"], "completed"],
                        start_to_close_timeout=timedelta(seconds=10),
                    )

                    return {
                        "status": "completed",
                        "exit_code": 0,
                        "wr_id": dco_result["wr_id"],
                        "model": model_cfg["model"],
                        "harness": model_cfg["harness"],
                    }

                except Exception as e:
                    error_type = type(e).__name__
                    last_error = e

                    if error_type == "RateLimitError":
                        # Insert API_LIMIT receipt and retry same model
                        await workflow.execute_activity(
                            "insert_receipt_activity",
                            args=[plan_id, "API_LIMIT", role, session_id,
                                  ticket_id,
                                  f"Rate limit [{model_cfg['model']}] "
                                  f"attempt {attempt}/{max_retries}: {e}",
                                  {
                                      "work_request_id": dco_result["wr_id"],
                                      "role": role,
                                      "harness": model_cfg["harness"],
                                      "model": model_cfg["model"],
                                      "attempt": attempt,
                                      "model_chain_index": model_idx,
                                  },
                                  0],
                            start_to_close_timeout=timedelta(seconds=10),
                        )

                        if attempt < max_retries:
                            # asyncio.sleep() is durable in Temporal workflows
                            await asyncio.sleep(retry_delay)
                            continue
                        break  # Rate limit retries exhausted → fallback

                    elif error_type in ("HarnessError", "LaunchError"):
                        # Known fatal error → BLOCK receipt, fallback
                        await workflow.execute_activity(
                            "insert_receipt_activity",
                            args=[plan_id,
                                  _FAIL_RECEIPTS.get(role, "BLOCK"),
                                  role, session_id, ticket_id,
                                  f"[{model_cfg['model']}] failed: {e} "
                                  f"attempt {attempt}/{max_retries}",
                                  {
                                      "work_request_id": dco_result["wr_id"],
                                      "role": role,
                                      "harness": model_cfg["harness"],
                                      "model": model_cfg["model"],
                                      "attempt": attempt,
                                      "model_chain_index": model_idx,
                                  },
                                  0],
                            start_to_close_timeout=timedelta(seconds=10),
                        )
                        break

                    else:
                        # Unknown error → BLOCK receipt with error_type, fallback
                        await workflow.execute_activity(
                            "insert_receipt_activity",
                            args=[plan_id,
                                  _FAIL_RECEIPTS.get(role, "BLOCK"),
                                  role, session_id, ticket_id,
                                  f"[{model_cfg['model']}] unknown error: {e} "
                                  f"attempt {attempt}/{max_retries}",
                                  {
                                      "work_request_id": dco_result["wr_id"],
                                      "role": role,
                                      "harness": model_cfg["harness"],
                                      "model": model_cfg["model"],
                                      "error_type": error_type,
                                      "attempt": attempt,
                                      "model_chain_index": model_idx,
                                  },
                                  0],
                            start_to_close_timeout=timedelta(seconds=10),
                        )
                        break

            # If we hit the continue in rate-limit retry, loop to next attempt
            # If we broke, loop to next model
            continue

        # All models exhausted
        return {
            "status": "failed",
            "exit_code": -1,
            "error_summary": str(last_error) if last_error else "All models exhausted",
        }

    @workflow.signal
    async def cancel(self):
        """Signal handler for external cancellation."""
        self._cancelled = True
        workflow.logger.info(
            f"PlanExecutionWorkflow CANCELLED plan={self._plan_id}"
        )

    @workflow.query
    def status(self) -> dict:
        """Query handler for UI status."""
        return {
            "plan_id": self._plan_id,
            "role": self._role,
            "current_step": self._current_step,
            "cancelled": self._cancelled,
        }


# ── Helpers ────────────────────────────────────────────────────────

def _json_dumps(obj: Any) -> str:
    """JSON-dump to string (workflows can't import json directly)."""
    import json
    return json.dumps(obj)


# Role/receipt mappings (from main.py)
_SUCCESS_RECEIPTS = {
    "builder": "IMPLEMENTATION",
    "reviewer": "REVIEW_PASS",
    "planner": "PLAN_CREATE",
    "critic": "CRITIQUE",
}
_FAIL_RECEIPTS = {
    "builder": "BLOCK",
    "reviewer": "REVIEW_REJECT",
    "planner": "PLAN_BLOCK",
    "critic": "CRITIQUE_REJECT",
}
