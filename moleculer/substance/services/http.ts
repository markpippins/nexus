// ── http.ts ──────────────────────────────────────────────────────────────────
// HTTP plumbing that mirrors the FastAPI contract the Python service exposed.
//
// nebula-srv's substance-proxy only inspects the status code
// (`/substance 404/ → 404`, everything else → 502), so the envelopes below are
// for humans and direct callers, not for the proxy. Keeping FastAPI's shapes
// means nothing breaks for any other consumer that learned them from the
// Python service.

import type { NextFunction, Request, RequestHandler, Response } from "express";

import type { ValidationErrorItem } from "./schemas";

export class HttpError extends Error {
  readonly status: number;
  /** FastAPI renders `{"detail": <detail>}`. */
  readonly detail: unknown;

  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.name = "HttpError";
    this.status = status;
    this.detail = detail;
  }
}

export function notFound(detail: string): HttpError {
  return new HttpError(404, detail);
}

/** FastAPI's 422 envelope for request-body validation failures. */
export function unprocessable(errors: readonly ValidationErrorItem[]): HttpError {
  return new HttpError(422, errors);
}

/**
 * Express 4 does not forward rejected promises from handlers to the error
 * middleware, so every async route is wrapped in this.
 */
export function asyncHandler(
  fn: (req: Request, res: Response, next: NextFunction) => Promise<unknown>,
): RequestHandler {
  return (req, res, next) => {
    fn(req, res, next).catch(next);
  };
}

/** Single error middleware translating domain errors into HTTP responses. */
export function errorHandler() {
  return (err: unknown, _req: Request, res: Response, next: NextFunction): void => {
    if (res.headersSent) {
      next(err);
      return;
    }
    if (err instanceof HttpError) {
      res.status(err.status).json({ detail: err.detail });
      return;
    }
    const anyErr = err as { type?: string; message?: string; code?: string } | null;
    // express.json() rejects malformed bodies with a SyntaxError carrying `type`.
    if (anyErr?.type === "entity.parse.failed") {
      res.status(422).json({
        detail: [{ loc: ["body"], msg: "Invalid JSON body", type: "json_invalid" }],
      });
      return;
    }
    if (anyErr?.type === "entity.too.large") {
      res.status(413).json({ detail: "Request body too large" });
      return;
    }
    console.error("[substance] unhandled error:", anyErr?.message ?? err);
    res.status(500).json({ detail: "internal server error" });
  };
}

/**
 * Parse a path parameter that must be a UUID. FastAPI would have rejected a
 * non-UUID path segment with a 422; the router asks for it explicitly here so
 * the status code is the same one clients already handle.
 */
export function requireUuidParam(raw: string | undefined, name: string): string {
  const value = (raw ?? "").trim();
  const CANONICAL =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (!CANONICAL.test(value)) {
    throw unprocessable([
      { loc: ["path", name], msg: "Input should be a valid UUID", type: "uuid_parsing" },
    ]);
  }
  return value.toLowerCase();
}

/** Parse an integer query parameter, falling back when absent or unparseable. */
export function intQuery(
  raw: unknown,
  dflt: number,
  { min, max }: { min?: number; max?: number } = {},
): number {
  let value = dflt;
  if (raw !== undefined) {
    const parsed = Number.parseInt(String(raw), 10);
    if (Number.isFinite(parsed)) {
      value = parsed;
    }
  }
  if (min !== undefined && value < min) {
    value = min;
  }
  if (max !== undefined && value > max) {
    value = max;
  }
  return value;
}
