import { AppError } from './errors.js';

export function errorHandler(err, _req, res, _next) {
  if (res.headersSent) {
    return;
  }

  console.error('[error]', err);

  if (err instanceof AppError) {
    const body = { error: err.message };
    // Propagate PG error codes for client-side duplicate detection
    if (err.code) body.code = err.code;
    if (err.constraint) body.constraint = err.constraint;
    res.status(err.statusCode).json(body);
    return;
  }

  // Propagate PG error codes on non-AppError errors too
  const body = { error: 'Internal server error' };
  if (err.code) body.code = err.code;
  res.status(500).json(body);
}
