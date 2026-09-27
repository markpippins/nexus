// ── routes/links.ts ──────────────────────────────────────────────────────────
// Port of python/substance/routers/links.py.
//
// The Python router carried no prefix and was mounted at the service root, so
// the domain type is a bare path segment:
//   /{domain_type}/{domain_id}/segment-sets
// {domain_type} is one of candidates | requirements.

import { Router, type Request, type Response } from "express";

import * as cache from "../cache";
import { asyncHandler, notFound, requireUuidParam, unprocessable } from "../http";
import * as repo from "../repository";
import { isKnownDomainType } from "../repository";
import {
  parseDomainLinkIn,
  toDomainLinkOut,
  type DomainType,
} from "../schemas";
import { cachedResolve } from "./segment-sets";

export const linksRouter: Router = Router();

/**
 * Resolve the two path parameters. An unsupported domain type is a 404, not a
 * 422: the path shape is valid, the resource family is not. The Python service
 * let FastAPI's 422 through first (the DomainType literal), so this is the one
 * place the TypeScript port deliberately diverges — in the direction of a more
 * accurate code. Consumers branch on the substance-proxy's own 404 mapping, so
 * the observable consequence is nil.
 */
function requireDomain(req: Request): DomainType {
  const domainType = String(req.params.domain_type ?? "");
  if (!isKnownDomainType(domainType)) {
    throw notFound(`unknown domain_type '${domainType}'`);
  }
  return domainType;
}

/** POST /{domain_type}/{domain_id}/segment-sets — link a domain object. */
linksRouter.post(
  "/:domain_type/:domain_id/segment-sets",
  asyncHandler(async (req: Request, res: Response) => {
    const domainType = requireDomain(req);
    const domainId = requireUuidParam(req.params.domain_id, "domain_id");
    const parsed = parseDomainLinkIn(req.body);
    if (!parsed.ok) {
      throw unprocessable(parsed.errors);
    }
    const body = parsed.value;
    const segset = await repo.getSegmentSet(body.segment_set_id);
    if (segset === null) {
      throw notFound("segment set not found");
    }
    await repo.linkDomain(domainType, domainId, body.segment_set_id, body.role);
    await cache.invalidateDomainIndexBestEffort(domainType, domainId);
    const resolved = await cachedResolve(body.segment_set_id);
    res
      .status(201)
      .json(toDomainLinkOut(body.segment_set_id, body.role, true, resolved));
  }),
);

/** GET /{domain_type}/{domain_id}/segment-sets — list linked sets, resolved. */
linksRouter.get(
  "/:domain_type/:domain_id/segment-sets",
  asyncHandler(async (req: Request, res: Response) => {
    const domainType = requireDomain(req);
    const domainId = requireUuidParam(req.params.domain_id, "domain_id");
    const links = await repo.listDomainLinks(domainType, domainId);
    const out = [];
    for (const link of links) {
      // A link whose set has since expired would 404 here; the Python service
      // propagated that too. The read-through cache means the common case is
      // still a single Redis GET.
      const resolved = await cachedResolve(link.segment_set_id);
      out.push(
        toDomainLinkOut(link.segment_set_id, link.role, link.active, resolved),
      );
    }
    res.json(out);
  }),
);

/** DELETE /{domain_type}/{domain_id}/segment-sets/{segment_set_id} — soft-unlink. */
linksRouter.delete(
  "/:domain_type/:domain_id/segment-sets/:segment_set_id",
  asyncHandler(async (req: Request, res: Response) => {
    const domainType = requireDomain(req);
    const domainId = requireUuidParam(req.params.domain_id, "domain_id");
    const segmentSetId = requireUuidParam(
      req.params.segment_set_id,
      "segment_set_id",
    );
    await repo.unlinkDomain(domainType, domainId, segmentSetId);
    await cache.invalidateDomainIndexBestEffort(domainType, domainId);
    res.status(204).end();
  }),
);
