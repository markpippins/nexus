#!/usr/bin/env node
// Ping the shrapnel API running locally to verify encode/decode round-trip.
// Usage: node src/scripts/smoke.js [BASE_URL]
import assert from 'node:assert/strict';

const BASE = process.argv[2] || `http://localhost:${process.env.SHRAPNEL_SRV_PORT || 3110}`;

async function req(method, path, body) {
  const r = await fetch(BASE + path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await r.text();
  let json = null;
  try { json = text ? JSON.parse(text) : null; } catch { json = { raw: text }; }
  return { status: r.status, json };
}

async function main() {
  console.log(`[smoke] base=${BASE}`);

  // Health
  {
    const { status, json } = await req('GET', '/health');
    assert.equal(status, 200, `health: ${status} ${JSON.stringify(json)}`);
    console.log(`[smoke] /health ok ->`, json);
  }

  // Type registry
  {
    const { status, json } = await req('GET', '/api/field-types');
    assert.equal(status, 200);
    assert.equal(json.field_types.length, 7, 'expected 7 types');
    console.log(`[smoke] /api/field-types ->`, json.field_types.map(t => t.name).join(', '));
  }

  // Encode directly through /api/encode
  const payload = {
    fields: [
      { property_name: 'name', label: 'Full Name', name: 'Name', type: 'String' },
      { property_name: 'age',  label: 'User Age',  name: 'Age',  type: 'Long' },
    ],
    values: { name: 'Alice', age: 30 },
  };
  let objectId;
  {
    const { status, json } = await req('POST', '/api/encode', payload);
    assert.equal(status, 201, `encode: ${status} ${JSON.stringify(json)}`);
    objectId = json.object_id;
    console.log(`[smoke] POST /api/encode -> object_id=${objectId}`);
    assert.deepEqual(json.decoded, { name: 'Alice', age: 30 }, 'decoded snapshot mismatch');
  }

  // Get it back through /api/objects/:id
  {
    const { status, json } = await req('GET', `/api/objects/${objectId}`);
    assert.equal(status, 200, `decode: ${status} ${JSON.stringify(json)}`);
    assert.equal(json.object.id, objectId);
    assert.equal(json.object.values.name, 'Alice');
    assert.equal(json.object.values.age, 30);
    console.log(`[smoke] GET /api/objects/${objectId} ->`, json.object.values);
  }

  // List with ?decode=true
  {
    const { status, json } = await req('GET', '/api/objects?limit=3&decode=true');
    assert.equal(status, 200);
    const found = json.objects.find(o => o.id === objectId);
    assert.ok(found, 'object should appear in list');
    assert.deepEqual(found.values, { name: 'Alice', age: 30 });
    console.log(`[smoke] GET /api/objects?decode=true found ${json.objects.length} rows; round-trip ok`);
  }

  // Raw bindings
  {
    const { status, json } = await req('GET', `/api/objects/${objectId}/values`);
    assert.equal(status, 200);
    assert.equal(json.values.length, 2);
    console.log(`[smoke] GET /api/objects/${objectId}/values -> ${json.values.length} bindings`);
  }

  // Encode fully-inferred JSON (no `fields` array)
  let inferredId;
  {
    const { status, json } = await req('POST', '/api/encode', {
      values: { username: 'bob', active: true, ratio: 3.14, profile: { role: 'admin' }, joined: '2024-01-01T00:00:00Z', id: 'f47ac10b-58cc-4372-a567-0e02b2c3d479' },
    });
    assert.equal(status, 201, `inferred encode: ${status} ${JSON.stringify(json)}`);
    inferredId = json.object_id;
    console.log(`[smoke] POST /api/encode (inferred) -> object_id=${inferredId}`);
    assert.deepEqual(json.decoded, {
      username: 'bob',
      active: true,
      ratio: 3.14,
      profile: { role: 'admin' },
      joined: '2024-01-01T00:00:00.000Z',
      id: 'f47ac10b-58cc-4372-a567-0e02b2c3d479',
    }, 'inferred decode mismatch');
  }

  // Cleanup
  {
    const s1 = await req('DELETE', `/api/objects/${objectId}`);
    assert.equal(s1.status, 200);
    const s2 = await req('DELETE', `/api/objects/${inferredId}`);
    assert.equal(s2.status, 200);
    console.log(`[smoke] cleanup -> deleted #${objectId}, #${inferredId}`);
  }

  // Verify gone
  {
    const s = await req('GET', `/api/objects/${objectId}`);
    assert.equal(s.status, 404);
    console.log(`[smoke] GET /api/objects/${objectId} after delete -> 404 ok`);
  }

  // StereoType API surface (read-only: 0005 functions exist and answer)
  {
    const s = await req('GET', '/api/stereotypes');
    assert.equal(s.status, 200, `stereotypes list: ${s.status} ${JSON.stringify(s.json)}`);
    assert.ok(Array.isArray(s.json.stereotypes), 'stereotypes should be an array');
    const firstName = s.json.stereotypes[0]?.name;
    console.log(`[smoke] GET /api/stereotypes -> ${s.json.stereotypes.length} stereotype(s)`);

    if (firstName) {
      const c = await req('GET', `/api/stereotypes/${encodeURIComponent(firstName)}/contract`);
      assert.equal(c.status, 200, `contract: ${c.status} ${JSON.stringify(c.json)}`);
      assert.ok(Array.isArray(c.json.contract), 'contract should be an array');
      console.log(`[smoke] GET /api/stereotypes/${firstName}/contract -> ${c.json.contract.length} field(s)`);

      const ch = await req('GET', `/api/stereotypes/${encodeURIComponent(firstName)}/chain`);
      assert.equal(ch.status, 200, `chain: ${ch.status} ${JSON.stringify(ch.json)}`);
      assert.ok(Array.isArray(ch.json.chain) && ch.json.chain.length >= 1, 'chain should have >= 1 hop');
      console.log(`[smoke] GET /api/stereotypes/${firstName}/chain -> ${ch.json.chain.length} hop(s)`);
    }

    // Unknown name must 404 through stereotype_resolve
    const nf = await req('GET', '/api/stereotypes/definitely_not_a_stereotype_zz/contract');
    assert.equal(nf.status, 404, `unknown stereotype should 404, got ${nf.status}`);
    console.log('[smoke] unknown stereotype -> 404 ok');
  }

  console.log('\n[smoke] ALL CHECKS PASSED');
}

main().catch((err) => {
  console.error('[smoke] FAILED:', err);
  process.exit(1);
});
