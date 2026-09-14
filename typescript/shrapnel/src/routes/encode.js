import { Router } from 'express';
import { withTransaction } from '../db.js';
import { encodePayload } from '../lib/encode.js';
import { decodeObject } from '../lib/encode.js';
import { pool } from '../db.js';
import { writeLimiter } from '../lib/rate-limit.js';

export const encodeRouter = Router();

// POST /api/encode
// Generic encode: takes any JSON payload, infers fields if not provided,
// creates object + values, returns object_id plus the decoded snapshot used
// to verify the round-trip.
encodeRouter.post('/', writeLimiter, async (req, res, next) => {
  try {
    const result = await withTransaction(async (client) => {
      return encodePayload(client, req.body);
    });
    const decoded = await decodeObject(pool, result.object_id);
    res.status(201).json({
      object_id: result.object_id,
      fields: result.fields,
      decoded,
    });
  } catch (err) {
    next(err);
  }
});
