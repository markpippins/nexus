import { Router } from "express";
import { getBreaker, saveFailureRecoveryConfig } from "../db";

export const failureRecoveryRouter = Router();

failureRecoveryRouter.get("/", async (_req, res) => {
  try {
    const breaker = await getBreaker();
    res.json({
      max_retries_per_model: breaker.max_retries_per_model ?? 3,
      retry_delay_seconds: breaker.retry_delay_seconds ?? 120,
      max_fallbacks: breaker.max_fallbacks ?? 3,
      push_back_to_pending: breaker.push_back_to_pending === 1 || breaker.push_back_to_pending === null,
      circuit_breaker_retry_after: breaker.retry_after ?? 1800,
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

failureRecoveryRouter.post("/", async (req, res) => {
  try {
    const {
      max_retries_per_model,
      retry_delay_seconds,
      max_fallbacks,
      push_back_to_pending,
      circuit_breaker_retry_after,
    } = req.body || {};
    await saveFailureRecoveryConfig({
      max_retries_per_model,
      retry_delay_seconds,
      max_fallbacks,
      push_back_to_pending,
      circuit_breaker_retry_after,
    });
    res.json({ saved: true });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});
