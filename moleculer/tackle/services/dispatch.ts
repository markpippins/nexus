import type { Express, Request, Response } from "express";

/**
 * Dispatch — the funnel behind all 86 gateway aliases.
 *
 * Extracted from tackle.service.ts so the hermetic suite can drive it
 * directly with real req/res objects (no broker), exactly like the sibling
 * ports' handler extraction.
 *
 * Contract: handed the REAL req/res (stashed on ctx.meta by the gateway's
 * onBeforeCall), routes through the incumbent's express app and resolves
 * when the response is finished. Rejects only on dispatch failure before
 * any handler ran.
 */

export function dispatch(
  app: Express,
  req: Request,
  res: Response
): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    // res 'finish'/'close' fire for normal responses; the SSE route ends
    // itself after its 30s window (its 'close'/'end' resolves us the same
    // way).
    const cleanup = () => {
      res.off("finish", onFinish);
      res.off("close", onClose);
    };
    const onFinish = () => {
      cleanup();
      resolve();
    };
    const onClose = () => {
      cleanup();
      resolve();
    };
    res.once("finish", onFinish);
    res.once("close", onClose);

    // Route through the full incumbent middleware stack
    // (cors → json → request-log → routers). express(req, res) is exactly
    // what app.listen runs per-request.
    app(req, res, (err?: any) => {
      cleanup();
      if (err) {
        reject(err);
        return;
      }
      // No error and no handler took the request: express's default 404
      // (finalhandler) already wrote the response, but 'finish' may not
      // have fired yet if the socket is idle — resolve on next tick.
      else setImmediate(resolve);
    });
  });
}

export default dispatch;
