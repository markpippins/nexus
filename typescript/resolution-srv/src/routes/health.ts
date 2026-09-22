import { Router } from "express";
import { getDb } from "../db";
import { HEALTH_CHECK_TABLES } from "../tables";

export const healthRouter = Router();

healthRouter.get("/", async (_req, res) => {
  try {
    const db = getDb();
    await db.query("SELECT 1");
    const missing: string[] = [];
    for (const t of HEALTH_CHECK_TABLES) {
      try {
        await db.query(`SELECT 1 FROM resolution.${t} LIMIT 1`);
      } catch {
        missing.push(t);
      }
    }
    res.json({
      service: "resolution-srv",
      status: missing.length === 0 ? "ok" : "degraded",
      database: "connected",
      missingTables: missing,
      time: new Date().toISOString(),
    });
  } catch (err: any) {
    res.status(503).json({
      service: "resolution-srv",
      status: "error",
      database: "unreachable",
      message: err.message,
    });
  }
});
