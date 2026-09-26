// /config — cron schedule and failure recovery configuration.
// Extracted from conduit-mcp per Architect decision (No SQL in MCP Servers).
import { Router } from "express";
import { query, queryOne } from "../db/client.js";

const router = Router();

router.get("/cron", async (_req, res) => {
  const PIPELINE_CRON = process.env.PIPELINE_CRON || "*/3";
  const match = PIPELINE_CRON.match(/^\*\/(\d+)$/);
  const intervalMinutes = match ? parseInt(match[1], 10) : 3;
  res.json({
    cron: PIPELINE_CRON,
    intervalMinutes,
    description: `Every ${intervalMinutes} minute${intervalMinutes === 1 ? "" : "s"}`,
    timestamp: new Date().toISOString(),
  });
});

router.get("/failure-recovery", async (_req, res) => {
  try {
    const breaker = await queryOne<any>(
      `SELECT max_retries_per_model, retry_delay_seconds, max_fallbacks,
              push_back_to_pending, retry_after
       FROM circuit_breaker WHERE id = 1`
    );
    if (!breaker) {
      res.json({
        max_retries_per_model: 3,
        retry_delay_seconds: 120,
        max_fallbacks: 3,
        push_back_to_pending: true,
        circuit_breaker_retry_after: 1800,
      });
      return;
    }
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

router.post("/failure-recovery", async (req, res) => {
  try {
    const {
      max_retries_per_model,
      retry_delay_seconds,
      max_fallbacks,
      push_back_to_pending,
      circuit_breaker_retry_after,
    } = req.body || {};
    await query(
      `UPDATE circuit_breaker SET
         max_retries_per_model = COALESCE($1, max_retries_per_model),
         retry_delay_seconds = COALESCE($2, retry_delay_seconds),
         max_fallbacks = COALESCE($3, max_fallbacks),
         push_back_to_pending = COALESCE($4, push_back_to_pending),
         retry_after = COALESCE($5, retry_after),
         updated_at = NOW()
       WHERE id = 1`,
      [
        max_retries_per_model ?? null,
        retry_delay_seconds ?? null,
        max_fallbacks ?? null,
        push_back_to_pending !== undefined ? (push_back_to_pending ? 1 : 0) : null,
        circuit_breaker_retry_after ?? null,
      ]
    );
    console.log(`[${new Date().toISOString()}] FAILURE RECOVERY config updated`);
    res.json({ saved: true });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

export default router;
