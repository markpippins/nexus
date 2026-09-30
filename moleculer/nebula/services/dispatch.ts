import type { Express, Request, Response } from "express";

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
      setImmediate(resolve);
    });
  });
}

export default dispatch;
