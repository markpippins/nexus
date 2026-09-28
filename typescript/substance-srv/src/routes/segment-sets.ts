// ── routes/segment-sets.ts ───────────────────────────────────────────────────
// Port of python/substance/routers/segment_sets.py.
//
// Mounted at /segment-sets, matching the FastAPI APIRouter prefix. The Python
// service registered "/from-segments" after "/{segment_set_id}", so a POST to
// /segment-sets/from-segments reaches the ingest route and not the UUID parser;
// the same ordering is reproduced here.

import { Router, type Request, type Response } from "express";

import * as cache from "../cache";
import { asyncHandler, intQuery, notFound, requireUuidParam, unprocessable } from "../http";
import * as repo from "../repository";
import {
  parseMembersAddIn,
  parseSegmentSetCreate,
  parseSegmentSetFromSegmentsCreate,
  parseSegmentSetUpdate,
  toSegmentSetOut,
  type SegmentSetOut,
} from "../schemas";

export const segmentSetsRouter: Router = Router();

/**
 * Build the full resolved view of a segment set straight from Postgres.
 * Callers that want the cache should use `cachedResolve` instead.
 */
export async function resolveSegmentSet(segmentSetId: string): Promise<SegmentSetOut> {
  const row = await repo.getSegmentSet(segmentSetId);
  if (row === null) {
    throw notFound("segment set not found");
  }
  const members = await repo.listResolvedSegments(segmentSetId);
  return toSegmentSetOut(row, members);
}

/**
 * Read-through cache: nexus:segset:{id} in Redis, falling back to Postgres and
 * repopulating on a miss. Shared with the domain-links router, which resolves
 * each linked set through here.
 */
export async function cachedResolve(segmentSetId: string): Promise<SegmentSetOut> {
  const cached = await cache.getSegset(segmentSetId);
  if (cached !== null) {
    return cached as unknown as SegmentSetOut;
  }
  const resolved = await resolveSegmentSet(segmentSetId);
  await cache.setSegset(segmentSetId, resolved);
  return resolved;
}

/** GET /segment-sets — list current-valid segment sets. */
segmentSetsRouter.get(
  "/",
  asyncHandler(async (req: Request, res: Response) => {
    const limit = intQuery(req.query.limit, 200, { min: 0, max: 1000 });
    const offset = intQuery(req.query.offset, 0, { min: 0 });
    const rows = await repo.listSegmentSets(limit, offset);
    res.json(rows.map((row) => toSegmentSetOut(row)));
  }),
);

/** POST /segment-sets/from-segments — strict-atomic transcript ingest. */
segmentSetsRouter.post(
  "/from-segments",
  asyncHandler(async (req: Request, res: Response) => {
    const parsed = parseSegmentSetFromSegmentsCreate(req.body);
    if (!parsed.ok) {
      throw unprocessable(parsed.errors);
    }
    const body = parsed.value;
    const row = await repo.createSegmentSetFromSegments(
      body.name,
      body.description,
      body.metadata,
      body.conversation_id,
      body.snapshot_id,
      body.segments,
    );
    if (row === null) {
      throw notFound("segment set not found");
    }
    res.status(201).json(await resolveSegmentSet(row.id));
  }),
);

/** POST /segment-sets — create, optionally with initial members. */
segmentSetsRouter.post(
  "/",
  asyncHandler(async (req: Request, res: Response) => {
    const parsed = parseSegmentSetCreate(req.body);
    if (!parsed.ok) {
      throw unprocessable(parsed.errors);
    }
    const body = parsed.value;
    const row = await repo.createSegmentSet(body.name, body.description, body.metadata);
    if (body.members.length > 0) {
      await repo.addMembers(row.id, body.members);
    }
    res.status(201).json(await resolveSegmentSet(row.id));
  }),
);

/** GET /segment-sets/:id — resolved view, Redis-cached. */
segmentSetsRouter.get(
  "/:segment_set_id",
  asyncHandler(async (req: Request, res: Response) => {
    const id = requireUuidParam(req.params.segment_set_id, "segment_set_id");
    res.json(await cachedResolve(id));
  }),
);

/** PATCH /segment-sets/:id — update name/description/status/metadata. */
segmentSetsRouter.patch(
  "/:segment_set_id",
  asyncHandler(async (req: Request, res: Response) => {
    const id = requireUuidParam(req.params.segment_set_id, "segment_set_id");
    const parsed = parseSegmentSetUpdate(req.body);
    if (!parsed.ok) {
      throw unprocessable(parsed.errors);
    }
    const row = await repo.updateSegmentSet(id, parsed.value);
    if (row === null) {
      throw notFound("segment set not found");
    }
    await cache.invalidateSegsetBestEffort(id);
    res.json(await resolveSegmentSet(id));
  }),
);

/** POST /segment-sets/:id/members — upsert one or more segments. */
segmentSetsRouter.post(
  "/:segment_set_id/members",
  asyncHandler(async (req: Request, res: Response) => {
    const id = requireUuidParam(req.params.segment_set_id, "segment_set_id");
    const parsed = parseMembersAddIn(req.body);
    if (!parsed.ok) {
      throw unprocessable(parsed.errors);
    }
    const existing = await repo.getSegmentSet(id);
    if (existing === null) {
      throw notFound("segment set not found");
    }
    await repo.addMembers(id, parsed.value.segments);
    await cache.invalidateSegsetBestEffort(id);
    res.json(await resolveSegmentSet(id));
  }),
);

/** DELETE /segment-sets/:id/members/:segment_id — soft-exclude. */
segmentSetsRouter.delete(
  "/:segment_set_id/members/:segment_id",
  asyncHandler(async (req: Request, res: Response) => {
    const id = requireUuidParam(req.params.segment_set_id, "segment_set_id");
    const segmentId = requireUuidParam(req.params.segment_id, "segment_id");
    await repo.excludeMember(id, segmentId);
    await cache.invalidateSegsetBestEffort(id);
    res.json(await resolveSegmentSet(id));
  }),
);
