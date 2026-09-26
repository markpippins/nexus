// routes/tasks.ts — REST surface over the tackle.tasks registry.
//
// Exposes the task registry created by the v7 migration (and seeded by
// v8) so the inspector role (and any other consumer) can discover what
// work is queued for them and resolve the prompt template each task
// binds to — without a second round-trip to look up tackle.prompts.

import { Router } from "express";
import {
  listTackleTasks,
  getTackleTask,
  getInspectorDispatch,
  upsertTackleTask,
  deleteTackleTask,
} from "../db";

export const tasksRouter = Router();

/**
 * GET /tasks/inspector/dispatch — resolve the dispatch payload for the
 * inspector role: every active inspector task, each bundled with the
 * FULL prompt body_md for the task's template (latest version of the
 * (role, slug) referenced by prompt_id).
 *
 * This is the central piece of wiring the inspector task registry to
 * execution: a consumer fetches this single document and has everything
 * it needs to execute the task against the agent persona prompt.
 *
 * Response:
 *   { tasks: Array<TackleTaskRow & {
 *       prompt_role, prompt_slug, prompt_version,
 *       prompt_body_md, prompt_title,
 *       prompt_parameter_schema, prompt_tags
 *   }> }
 *
 * Route ordering: declared before /:task_slug. The two routes don't
 * actually conflict (different path arity: 2 segments vs 1), but
 * declaring the specific path first is the conventional safe practice
 * in Express routers.
 */
tasksRouter.get("/inspector/dispatch", async (_req, res) => {
  try {
    const dispatch = await getInspectorDispatch();
    res.json(dispatch);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

/**
 * GET /tasks — list tasks.
 *
 * Query params:
 *   ?role=<role>      filter by role (default: all roles)
 *   ?all=true         include inactive tasks (default: active only)
 *
 * Response:
 *   { count: number, tasks: TackleTaskRow[] }
 */
tasksRouter.get("/", async (req, res) => {
  try {
    const role = typeof req.query.role === "string" ? req.query.role : undefined;
    const includeInactive =
      typeof req.query.all === "string" && req.query.all !== "false";
    const tasks = await listTackleTasks(role, includeInactive);
    res.json({ count: tasks.length, tasks });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

/**
 * GET /tasks/:task_slug — fetch one task (most-recent active or, if
 * none active, most-recent inactive) with its joined prompt reference
 * (prompt_role / prompt_slug / prompt_version).
 *
 * Response: TackleTaskRow (200) or { error: "Task not found" } (404).
 *
 * Route ordering: declared after the fixed-path routes above. /:task_slug
 * is a single path segment, so it won't capture "/inspector/dispatch"
 * (two segments) — they're non-overlapping — but ordered declaration is
 * the conventional and safest practice.
 */
tasksRouter.get("/:task_slug", async (req, res) => {
  try {
    const task = await getTackleTask(req.params.task_slug);
    if (!task) {
      res.status(404).json({ error: "Task not found" });
      return;
    }
    res.json(task);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

tasksRouter.post("/", async (req, res) => {
  try {
    const { id, role, task_slug, scope, acceptance_criteria, prompt_id, active } = req.body || {};
    if (!role || !task_slug || !prompt_id) {
      res.status(400).json({ error: "role, task_slug, and prompt_id are required" });
      return;
    }
    const task = await upsertTackleTask({ id, role, task_slug, scope, acceptance_criteria, prompt_id, active });
    res.json({ saved: true, task });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

tasksRouter.delete("/:task_slug", async (req, res) => {
  try {
    const role = typeof req.query.role === "string" ? req.query.role : undefined;
    const deleted = await deleteTackleTask(req.params.task_slug, role);
    if (!deleted) {
      res.status(404).json({ error: "Task not found" });
      return;
    }
    res.json({ deleted: true, task_slug: req.params.task_slug, role: role ?? null });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});
