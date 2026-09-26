// /workflows — workflow status backed by sessions (Temporal removed).
// Returns active sessions formatted the same way the UI expects.
import { Router } from "express";
import { query } from "../db/client.js";

const router = Router();

router.get("/", async (_req, res) => {
  try {
    const sessions = await query<any>(
      `SELECT id, agent_role, start_iso, end_iso, is_running, plans_processed, pid, created_at
       FROM sessions ORDER BY start_iso DESC`
    );
    const active = sessions.filter((s) => s.is_running === 1 || s.is_running === true);
    const workflows = active.map((s) => {
      let planId = "";
      try {
        const plans = JSON.parse(s.plans_processed || "[]");
        if (Array.isArray(plans) && plans.length > 0) planId = plans[0];
      } catch {}
      return {
        workflowId: planId ? `plan-${planId}-${s.agent_role}` : s.id,
        runId: s.id,
        status: "running",
        startTime: s.start_iso || s.created_at || null,
        closeTime: s.end_iso || null,
        planId,
        role: s.agent_role,
        pid: s.pid ?? null,
      };
    });
    const counts = { running: workflows.length, completed: 0, failed: 0, cancelled: 0, total: workflows.length };
    res.json({ connected: true, counts, workflows });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

export default router;
