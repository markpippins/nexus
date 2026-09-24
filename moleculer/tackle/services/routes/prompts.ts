import { Router } from "express";
import { listPrompts, getPromptByRoleSlug, upsertPrompt } from "../db";

export const promptsRouter = Router();

promptsRouter.get("/", async (req, res) => {
  try {
    const role = typeof req.query.role === "string" ? req.query.role : undefined;
    const prompts = await listPrompts(role);
    res.json({ count: prompts.length, prompts });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

promptsRouter.post("/", async (req, res) => {
  try {
    const { id, role, slug, version, title, body_md, parameter_schema, tags } = req.body || {};
    if (!role || !slug || !title || !body_md) {
      res.status(400).json({ error: "role, slug, title, and body_md are required" });
      return;
    }
    const result = await upsertPrompt({ id, role, slug, version, title, body_md, parameter_schema, tags });
    res.json({ saved: true, ...result });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// GET /prompts/:role — list prompts for a role (wind-ui compat)
promptsRouter.get("/:role", async (req, res) => {
  try {
    const prompts = await listPrompts(req.params.role);
    res.json({ count: prompts.length, prompts });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// GET /prompts/:role/:slug — single prompt by role+slug (wind-ui compat)
promptsRouter.get("/:role/:slug", async (req, res) => {
  try {
    const prompt = await getPromptByRoleSlug(req.params.role, req.params.slug);
    if (!prompt) {
      res.status(404).json({ error: "Prompt not found" });
      return;
    }
    res.json(prompt);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});
