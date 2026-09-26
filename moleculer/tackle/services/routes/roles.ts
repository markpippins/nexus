import { Router } from "express";
import {
  getRoles,
  getRole,
  upsertRole,
  deleteRole,
  getRoleReadiness,
  provisionRole,
} from "../db";
import { triggerRefresh } from "../memory";

export const rolesRouter = Router();

rolesRouter.get("/", async (_req, res) => {
  try {
    const roles = await getRoles();
    res.json({ count: roles.length, roles });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// GET /roles/readiness/:name — readiness checklist (must be registered
// BEFORE /:id so "readiness" isn't captured as an id).
rolesRouter.get("/readiness/:name", async (req, res) => {
  try {
    const readiness = await getRoleReadiness(req.params.name);
    res.json(readiness);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

rolesRouter.get("/:id", async (req, res) => {
  try {
    const role = await getRole(req.params.id);
    if (!role) {
      res.status(404).json({ error: "Role not found" });
      return;
    }
    res.json(role);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

rolesRouter.post("/", async (req, res) => {
  try {
    const { id, name, description } = req.body || {};
    if (!name) {
      res.status(400).json({ error: "name is required" });
      return;
    }
    const role = await upsertRole({ id, name, description });
    res.json({ saved: true, role });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// POST /roles/provision — atomic role setup orchestrator (Gap 1).
// Collapses role identity + config bundle + persona + tool access +
// procedure cards + nebula.roles sync + assembly user into one transaction,
// then returns the readiness report. See db.provisionRole for the spec shape.
rolesRouter.post("/provision", async (req, res) => {
  const spec = req.body || {};
  if (!spec.name) {
    res.status(400).json({ error: "name is required" });
    return;
  }
  try {
    const result = await provisionRole(spec);
    // role_memory may have changed — refresh the PG→Redis procedure registry
    // so the new assignments are live immediately (best-effort).
    const refresh = await triggerRefresh();
    const readiness = await getRoleReadiness(spec.name);
    res.status(201).json({
      provisioned: true,
      ...result,
      refreshed: refresh.success,
      refreshError: refresh.success ? undefined : refresh.error,
      readiness,
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

rolesRouter.delete("/:id", async (req, res) => {
  try {
    const deleted = await deleteRole(req.params.id);
    res.json({ deleted, id: req.params.id });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});
