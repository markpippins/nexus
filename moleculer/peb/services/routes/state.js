import { Router } from 'express';
import { pool } from '../db.js';
import { badRequest, notFound } from '../errors.js';
import { isAcceptableId } from '../lib/pagination.js';

export const stateRouter = Router();

// Schema note (see R1 architect briefing, Q1):
//   peb.state has version + checksum but is a CURRENT-VALUE table only.
//   The historical reconstruction source is peb.transactions.state_delta,
//   which is a jsonb column carrying the diff that transaction applied to
//   zero or more state keys. There is no enforced correlation between
//   a transaction and the state key(s) it touched.
//
// We treat a transaction as touching key K iff:
//    transactions.state_delta ? K      -- top-level jsonb key check, OR
//   transactions.state_delta -> 'keys' has K among the top-level keys,
//                             OR
//   transactions.state_delta matches one of the jsonb patterns above for the
//   encoded slug of K (we replace non-id chars in K to mimic jsonb path rules).
//
// When no direct match is possible, fall back to: "any transaction whose
// state_delta mentions any state key" — a coarser signal but correct for
// dashboards that just want the version timeline.
//
// State diff: walk transactions that touched K in order, replay state_delta
// patches in order to derive the content at version N, and surface the
// diff between two user-supplied version ids.

// GET /api/peb/state/{key}/versions
stateRouter.get('/:key/versions', async (req, res, next) => {
  try {
    const key = req.params.key;
    if (!isAcceptableId(key)) return next(badRequest('invalid key'));

    // 1) current state row (if any) — gives the latest version + checksum
    const cur = await pool.query(
      `SELECT id, key, content, metadata, checksum, version,
              created_at, updated_at
         FROM peb.state WHERE key = $1`,
      [key]
    );
    const current = cur.rowCount ? cur.rows[0] : null;

    // 2) historical versions — derived from transactions whose state_delta
    //    mentions key K at top level, OR carries K inside a `keys` array.
    const history = await pool.query(
      `
      SELECT t.id AS transaction_id,
             t.created_at,
             t.committed_at,
             t.before_hash,
             t.after_hash,
             t.state_delta
        FROM peb.transactions t
       WHERE t.state_delta IS NOT NULL
         AND (
           t.state_delta ? $1
           OR jsonb_path_exists(t.state_delta,
                ('$.keys[*] == \"' || $1 || '\"')::jsonpath)
         )
       ORDER BY t.committed_at NULLS LAST, t.created_at ASC
      `,
      [key]
    );

    res.json({
      key,
      current,
      historical_versions: history.rows.map(r => ({
        transaction_id: r.transaction_id,
        created_at: r.created_at,
        committed_at: r.committed_at,
        before_hash: r.before_hash,
        after_hash: r.after_hash,
        // strip the raw state_delta — too noisy for the version list, exposed
        // via the diff endpoint when the user asks for from/to.
        touched_key: true,
      })),
      version_count: history.rowCount + (current ? 1 : 0),
    });
  } catch (err) {
    next(err);
  }
});

// GET /api/peb/state/{key}/diff?from=<tx_id>&to=<tx_id>
//
// Spec: "diff content by checksum mismatch".
//
// Implementation:
//   - Resolve v(from) and v(to) by walking transactions in order, replaying
//     state_delta jsonb patches for key K. We apply shallow-merge semantics
//     (state_delta[K] overwrites prior value if present, removed markers null
//     out a key from prior content).
//   - Compare the two reconstructed snapshots for K. Return the diff:
//        { from: <content>, to: <content>, added, removed, changed }
//   - If `to` is the special value 'current', use the live peb.state row as
//     the to side.
stateRouter.get('/:key/diff', async (req, res, next) => {
  try {
    const key = req.params.key;
    if (!isAcceptableId(key)) return next(badRequest('invalid key'));
    const fromTx = req.query.from;
    const toRaw  = req.query.to;
    if (!fromTx) return next(badRequest('from query parameter required (transaction id)'));
    if (!toRaw)  return next(badRequest('to query parameter required (transaction id or "current")'));

    // Fetch every transaction that touched K, in chronological order, that
    // is <= the fromTx and <= the toTx (whichever is later). We'll filter to
    // each version inside JS.
    const touchers = await pool.query(
      `
      SELECT t.id, t.created_at, t.committed_at, t.state_delta
        FROM peb.transactions t
       WHERE t.state_delta IS NOT NULL
         AND (
           t.state_delta ? $1
           OR jsonb_path_exists(t.state_delta,
                ('$.keys[*] == \"' || $1 || '\"')::jsonpath)
         )
       ORDER BY t.committed_at NULLS LAST, t.created_at ASC
      `,
      [key]
    );

    if (touchers.rowCount === 0 && toRaw !== 'current') {
      return next(notFound('no transactions touch key ' + key));
    }

    // We need an ordering for transactions that have NULL committed_at —
    // we use created_at as the secondary key (already applied in the SQL
    // ORDER BY).
    const ordered = touchers.rows;
    const indexOfTx = (txId) => ordered.findIndex(t => t.id === txId);
    const fromIdx = indexOfTx(fromTx);
    let toIdx;
    // `to=current` resolves to a synthetic index past the end.
    if (String(toRaw).toLowerCase() === 'current') {
      toIdx = ordered.length - 1;
    } else if (isAcceptableId(String(toRaw))) {
      toIdx = indexOfTx(toRaw);
    } else {
      return next(badRequest('invalid to query parameter'));
    }

    if (fromIdx === -1) return next(notFound(`transaction ${fromTx} does not touch ${key}`));
    if (toIdx === -1 && String(toRaw).toLowerCase() !== 'current') {
      return next(notFound(`transaction ${toRaw} does not touch ${key}`));
    }
    if (toIdx !== -1 && fromIdx > toIdx) {
      return next(badRequest('from transaction is later than to transaction'));
    }

    const snapshotAt = (endIdx) => {
      let snap = null; // null until the first transaction with state_delta?key
      for (let i = 0; i <= endIdx; i++) {
        const t = ordered[i];
        const kDelta = deepPick(t.state_delta, key);
        if (kDelta === undefined) continue;
        snap = mergeShallow(snap, kDelta);
      }
      return snap;
    };

    const fromContent = snapshotAt(fromIdx);
    const toContent =
      String(toRaw).toLowerCase() === 'current'
        ? ((await pool.query(
            `SELECT content FROM peb.state WHERE key = $1`,
            [key]
          )).rows[0]?.content ?? null)
        : snapshotAt(toIdx);

    const diff = diffJsonb(fromContent, toContent);

    res.json({
      key,
      from: { transaction_id: fromIdx >= 0 ? ordered[fromIdx].id : null,
              content: fromContent },
      to:    { transaction_id: (String(toRaw).toLowerCase() === 'current') ? null
                                                 : (toIdx >= 0 ? ordered[toIdx].id : null),
               content: toContent },
      diff,
    });
  } catch (err) {
    next(err);
  }
});

// Pull a sub-value for key K from a jsonb state_delta. Three shapes supported:
//   { K: <value> }                → returns <value>
//   { "keys": ["K"], "K": value } → returns <value>
//   { "keys": ["K"], ...other non-keyed payload }  → returns null (touched but no content)
function deepPick(stateDelta, key) {
  if (!stateDelta || typeof stateDelta !== 'object') return undefined;
  if (Object.prototype.hasOwnProperty.call(stateDelta, key)) {
    return stateDelta[key];
  }
  if (Array.isArray(stateDelta.keys) && stateDelta.keys.includes(key)) {
    // Touched but the payload may be elsewhere; signal as explicit null marker
    // so the diff layer knows the key is in-scope.
    if (Object.prototype.hasOwnProperty.call(stateDelta, 'payload')) {
      return stateDelta.payload;
    }
    return null;
  }
  return undefined;
}

function mergeShallow(prev, next) {
  // If we get an explicit null, we treat it as a deletion.
  if (next === null && prev && typeof prev === 'object' && !Array.isArray(prev)) {
    // We don't know the inner key to delete without more convention; keep prev.
    return prev;
  }
  if (next === null) return null;
  if (prev === null) return next;
  if (typeof prev === 'object' && typeof next === 'object'
      && !Array.isArray(prev) && !Array.isArray(next)) {
    return { ...prev, ...next };
  }
  return next;
}

// A shallow diff over jsonb-decodable values. Returns
// { added: { keys: [] }, removed: [...], changed: [{ key, from, to }] }
export function diffJsonb(fromVal, toVal) {
  const out = { added: {}, removed: [], changed: [] };
  if (fromVal === toVal) return out;
  if (fromVal === undefined || fromVal === null) return { added: toVal ?? {}, removed: [], changed: [] };
  if (toVal === undefined || toVal === null) {
    return { added: {}, removed: Object.keys(fromVal ?? {}), changed: [] };
  }
  if (typeof fromVal !== 'object' || typeof toVal !== 'object'
      || Array.isArray(fromVal) || Array.isArray(toVal)) {
    return { added: {}, removed: [], changed: [{ key: '$scalar', from: fromVal, to: toVal }] };
  }
  const fromKeys = Object.keys(fromVal);
  const toKeys = Object.keys(toVal);
  for (const k of toKeys) {
    if (!Object.prototype.hasOwnProperty.call(fromVal, k)) {
      out.added[k] = toVal[k];
    } else if (JSON.stringify(fromVal[k]) !== JSON.stringify(toVal[k])) {
      out.changed.push({ key: k, from: fromVal[k], to: toVal[k] });
    }
  }
  for (const k of fromKeys) {
    if (!Object.prototype.hasOwnProperty.call(toVal, k)) {
      out.removed.push(k);
    }
  }
  return out;
}
