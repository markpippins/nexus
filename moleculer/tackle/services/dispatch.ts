import type { Express, Request, Response } from "express";

// finalhandler is Express's own terminal handler: a top-level Express app
// (app.listen) supplies it as the `done` callback of the router, which is
// where the incumbent's default `Cannot GET <url>` HTML 404 comes from.
// Embedded (dispatch-through-Express) the caller passes its own next(), so
// Express defers the 404 to US — this module — and must reproduce the
// incumbent's terminal default for any request the stack never answered.
// (Same express dependency tree → same finalhandler → same bytes.)
// eslint-disable-next-line @typescript-eslint/no-var-requires
const finalhandler = require("finalhandler") as (
  req: Request,
  res: Response,
) => (err?: any) => void;

/** Route a real gateway request through the Express app and wait for completion. */
export function dispatch(app: Express, req: Request, res: Response): Promise<void> {
  return new Promise<void>((resolve, reject) => {
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

    app(req, res, (err?: any) => {
      cleanup();
      if (err) {
        reject(err);
        return;
      }
      if (!res.headersSent) {
        // Stack exhausted without an answer: the incumbent's http-server
        // embedding would now run finalhandler (default 404). Do the same.
        // Do NOT resolve here — finalhandler owns the response now (it may
        // write synchronously, or defer until the request stream finishes);
        // resolving early lets the gateway's sendResponse res.end() race the
        // deferred write and crash the process (ERR_HTTP_HEADERS_SENT inside
        // finalhandler's removeHeader — observed LIVE). onFinish/onClose
        // below resolve once the response actually ends.
        finalhandler(req, res)();
        return;
      }
      setImmediate(resolve);
    });
  });
}

export default dispatch;
