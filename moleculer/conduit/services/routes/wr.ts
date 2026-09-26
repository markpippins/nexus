// /wr — work-request projection-drift endpoints.
// Moved from conduit-mcp per plan 1285 (Architect decision: conduit-srv
// owns SQL, conduit-mcp delegates). The drift-scan endpoint gives the
// projection-vs-replay invariant a live caller.
import { Router } from "express";
import {
  checkProjectionDrift,
  resolveWrUuid,
  scanProjectionDrift,
} from "../db/drift.js";

const router = Router();

router.get("/:id/projection-drift", async (req, res) => {
  try {
    const { id } = req.params;
    const uuid = await resolveWrUuid(id);
    const drift = await checkProjectionDrift(uuid);
    if (!drift) {
      res.status(404).json({ ok: false, error: `WorkRequest ${id} not found` });
      return;
    }
    res.json({ ok: true, workRequestId: uuid, drift });
  } catch (err: any) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

router.get("/drift-scan", async (req, res) => {
  try {
    const limit = req.query.limit ? parseInt(req.query.limit as string, 10) : 100;
    const status = req.query.status as string | undefined;
    const { scanned, drifted } = await scanProjectionDrift({
      limit,
      statusFilter: status ? [status] : undefined,
    });
    res.json({
      ok: true,
      scanned: scanned.length,
      drifted: drifted.length,
      findings: drifted.map((s) => ({
        work_request_uuid: s.work_request_uuid,
        wr_id: s.wr_id,
        status: s.status,
        expected_state: s.drift.expected_state,
        live_state: s.drift.live_state,
        expected_vision_stage: s.drift.expected_vision_stage,
        live_vision_stage: s.drift.live_vision_stage,
        expected_vision_ir_version: s.drift.expected_vision_ir_version,
        live_vision_ir_version: s.drift.live_vision_ir_version,
        expected_last_event_id: s.drift.expected_last_event_id,
        live_last_event_id: s.drift.live_last_event_id,
      })),
    });
  } catch (err: any) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

export default router;
