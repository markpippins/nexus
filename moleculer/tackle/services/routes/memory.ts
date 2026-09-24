import { Router } from "express";
import {
  getProceduresForRole,
  getProcedureBySlug,
  triggerRefresh,
  hasRoleMemoryChangedSince,
} from "../memory";
import {
  getDb,
  getRoleCheckpoints,
  assignProcedures,
  unassignProcedure,
} from "../db";

export const memoryRouter = Router();

memoryRouter.get("/procedures/:role", async (req, res) => {
  try {
    const procedures = await getProceduresForRole(req.params.role);
    res.json({ role: req.params.role, count: procedures.length, procedures });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

memoryRouter.get("/procedure/:slug", async (req, res) => {
  try {
    const card = await getProcedureBySlug(req.params.slug);
    if (!card) {
      res.status(404).json({ error: `Procedure '${req.params.slug}' not found` });
      return;
    }
    res.json(card);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

memoryRouter.post("/check-since", async (req, res) => {
  try {
    const { role, since } = req.body || {};
    if (!role || !since) {
      res.status(400).json({ error: "role and since are required" });
      return;
    }
    const pool = getDb();
    const changed = await hasRoleMemoryChangedSince(pool, role, since);
    res.json({ role, since, changed });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// POST /memory/assign — assign procedure cards to a role.
// Body: { role, slugs: ["slug", ...] }. Writes tackle.role_memory, then
// triggers the PG→Redis refresh so the new assignments are live immediately.
memoryRouter.post("/assign", async (req, res) => {
  try {
    const { role, slugs } = req.body || {};
    if (!role || !Array.isArray(slugs) || slugs.length === 0) {
      res.status(400).json({ error: "role and slugs (non-empty array) are required" });
      return;
    }
    const assigned = await assignProcedures(role, slugs);
    const refresh = await triggerRefresh();
    res.status(201).json({
      assigned,
      role,
      missing: slugs.length - assigned,
      refreshed: refresh.success,
      refreshError: refresh.success ? undefined : refresh.error,
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// DELETE /memory/assign — unassign a procedure card from a role by expiring
// the active assignment (bitemporal-preserving soft delete).
// Body or query: { role, slug }
memoryRouter.delete("/assign", async (req, res) => {
  try {
    const role = req.body?.role ?? (typeof req.query.role === "string" ? req.query.role : undefined);
    const slug = req.body?.slug ?? (typeof req.query.slug === "string" ? req.query.slug : undefined);
    if (!role || !slug) {
      res.status(400).json({ error: "role and slug are required" });
      return;
    }
    const unassigned = await unassignProcedure(role, slug);
    const refresh = await triggerRefresh();
    res.json({
      unassigned,
      role,
      slug,
      refreshed: refresh.success,
      refreshError: refresh.success ? undefined : refresh.error,
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

memoryRouter.post("/refresh", async (_req, res) => {
  try {
    const result = await triggerRefresh();
    if (!result.success) {
      res.status(500).json({ error: `Refresh failed: ${result.error}` });
      return;
    }
    res.json({
      refreshed: true,
      procedures: result.result?.procedures ?? 0,
      roleIndices: result.result?.roleIndices ?? 0,
      timestamp: result.result?.timestamp ?? new Date().toISOString(),
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

memoryRouter.get("/role-updates", async (_req, res) => {
  try {
    const checkpoints = await getRoleCheckpoints();
    res.json(checkpoints);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});
