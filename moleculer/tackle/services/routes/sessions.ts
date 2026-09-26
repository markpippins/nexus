import { Router } from "express";
import { getAllSessions, getSession, endSession } from "../db";

export const sessionsRouter = Router();

sessionsRouter.get("/", async (_req, res) => {
  try {
    res.json(await getAllSessions());
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

sessionsRouter.post("/:sessionId/kill", async (req, res) => {
  try {
    const { sessionId } = req.params;

    if (!/^[a-zA-Z0-9_-]+$/.test(sessionId)) {
      res.status(400).json({ error: "Invalid session ID" });
      return;
    }

    const session = await getSession(sessionId);
    if (!session) {
      res.status(404).json({ error: `Session ${sessionId} not found` });
      return;
    }

    if (!session.is_running) {
      res.status(400).json({ killed: false, error: "Session is not running", sessionId });
      return;
    }

    const now = new Date().toISOString();
    const killedPids: number[] = [];
    const errors: string[] = [];

    if (session.pid && session.pid > 0) {
      try {
        process.kill(-session.pid, "SIGKILL");
        killedPids.push(session.pid);
      } catch (e: any) {
        try {
          process.kill(session.pid, "SIGKILL");
          killedPids.push(session.pid);
        } catch (e2: any) {
          errors.push(`PID ${session.pid}: ${e2.message}`);
        }
      }
    }

    await endSession(sessionId, 137, now);

    res.json({
      killed: true,
      sessionId,
      pids: killedPids,
      errors: errors.length > 0 ? errors : undefined,
      timestamp: now,
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});
