import { Router } from "express";
import {
  listToolAccess,
  updateToolAccess,
  createToolAccess,
  seedToolAccess,
} from "../db";

export const toolAccessRouter = Router();

toolAccessRouter.get("/", async (req, res) => {
  try {
    const role = typeof req.query.role === "string" ? req.query.role : undefined;
    const access = await listToolAccess(role);
    res.json({ count: access.length, access });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

toolAccessRouter.get("/:role", async (req, res) => {
  try {
    const access = await listToolAccess(req.params.role);
    res.json({ count: access.length, access });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// POST /config/ai/tool-access — bulk-create allowlist rows for a role.
// Body: { role, tools: [{ mcp_id, tool_slug }] } or { role, tools: [tool_slug...] }
// (string entries are auto-wrapped with an empty mcp_id rollup).
toolAccessRouter.post("/", async (req, res) => {
  try {
    const { role, tools } = req.body || {};
    if (!role || !Array.isArray(tools) || tools.length === 0) {
      res.status(400).json({ error: "role and tools (non-empty array) are required" });
      return;
    }
    const normalized = tools.map((t: any) =>
      typeof t === "string" ? { mcp_id: "", tool_slug: t } : { mcp_id: t?.mcp_id || "", tool_slug: t?.tool_slug }
    );
    const created = await createToolAccess(role, normalized);
    res.status(201).json({ saved: true, role, count: created });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// POST /config/ai/tool-access/seed — bulk-populate a role's allowlist from a
// template role (default-deny: new roles start with zero tools).
// Body: { role, fromRole }
toolAccessRouter.post("/seed", async (req, res) => {
  try {
    const { role, fromRole } = req.body || {};
    if (!role || !fromRole) {
      res.status(400).json({ error: "role and fromRole are required" });
      return;
    }
    const copied = await seedToolAccess(role, fromRole);
    res.status(201).json({ saved: true, role, fromRole, count: copied });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

toolAccessRouter.patch("/:id", async (req, res) => {
  try {
    const { allowed } = req.body || {};
    if (typeof allowed !== "boolean") {
      res.status(400).json({ error: "allowed (boolean) is required" });
      return;
    }
    const result = await updateToolAccess(req.params.id, { allowed });
    if (!result) {
      res.status(404).json({ error: "Tool access rule not found" });
      return;
    }
    res.json({ updated: true, ...result });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});
