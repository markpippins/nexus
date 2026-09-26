import { Router } from "express";
import {
  listSchedulerEntries,
  getSchedulerEntry,
  getDueSchedulerEntries,
  createSchedulerEntry,
  updateSchedulerEntry,
  deleteSchedulerEntry,
} from "../db";

export const schedulerRouter = Router();

schedulerRouter.get("/", async (_req, res) => {
  try {
    const entries = await listSchedulerEntries();
    res.json({ count: entries.length, entries });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

schedulerRouter.get("/due", async (_req, res) => {
  try {
    const due = await getDueSchedulerEntries();
    res.json({ count: due.length, entries: due });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

schedulerRouter.get("/:id", async (req, res) => {
  try {
    const id = parseInt(req.params.id, 10);
    if (isNaN(id)) {
      res.status(400).json({ error: "Invalid id" });
      return;
    }
    const entry = await getSchedulerEntry(id);
    if (!entry) {
      res.status(404).json({ error: "Not found" });
      return;
    }
    res.json(entry);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

schedulerRouter.post("/", async (req, res) => {
  try {
    const { role, model_id, harness, agent_config, schedule_type, schedule_value, cron_expr, event_criteria, project_dir, task_slug, enabled } = req.body || {};
    if (!role) {
      res.status(400).json({ error: "role is required" });
      return;
    }
    const entry = await createSchedulerEntry({ role, model_id, harness, agent_config, schedule_type, schedule_value, cron_expr, event_criteria, project_dir, task_slug, enabled });
    res.json({ created: true, entry });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

schedulerRouter.patch("/:id", async (req, res) => {
  try {
    const id = parseInt(req.params.id, 10);
    if (isNaN(id)) {
      res.status(400).json({ error: "Invalid id" });
      return;
    }
    const entry = await updateSchedulerEntry(id, req.body);
    if (!entry) {
      res.status(404).json({ error: "Not found" });
      return;
    }
    res.json({ updated: true, entry });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

schedulerRouter.delete("/:id", async (req, res) => {
  try {
    const id = parseInt(req.params.id, 10);
    if (isNaN(id)) {
      res.status(400).json({ error: "Invalid id" });
      return;
    }
    const deleted = await deleteSchedulerEntry(id);
    res.json({ deleted, id });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});
