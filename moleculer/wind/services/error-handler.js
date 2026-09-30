import { AppError } from './errors.js';

export function errorHandler(err, _req, res, _next) {
  if (err instanceof AppError) {
    return res.status(err.statusCode).json({
      error: err.message,
      code: err.code,
    });
  }

  console.error('[wind-srv] Unhandled error:', err);
  res.status(500).json({ error: 'Internal server error' });
}
