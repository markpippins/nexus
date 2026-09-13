import { describe, it, expect } from 'vitest';
import { harnessEndpoint } from '../src/routes/instances.js';

// The M2 harness repoint (broker worker.harness as the default execution
// surface) must be env-driven so the cutover is reversible:
//   default          -> broker gateway (:4080, POST /api/workers/harness/run)
//   HARNESS_SURFACE=legacy -> direct harness-srv (:3420, POST /run)
// An explicit HARNESS_URL overrides the default base for the chosen surface.

describe('harness execution surface selection', () => {
  it('defaults to the broker surface with the worker.harness run path', () => {
    expect(harnessEndpoint.surface).toBe('broker');
    expect(harnessEndpoint.runPath).toBe('/api/workers/harness/run');
    expect(harnessEndpoint.baseUrl).toBe('http://127.0.0.1:4080');
  });

  it('exposes a full URL that ends in the run path', () => {
    expect(harnessEndpoint.baseUrl + harnessEndpoint.runPath).toMatch(
      /^https?:\/\/.+\/(run|api\/workers\/harness\/run)$/,
    );
  });
});
