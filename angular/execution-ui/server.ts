import express from 'express';
import path from 'path';
import { createServer as createViteServer } from 'vite';
import { mockStore } from './src/services/mockData.ts';

async function startServer() {
  const app = express();
  const PORT = parseInt(process.env.PORT || '4205', 10);

  // Environment-selected mode: live (proxy execution surface) is the default
  // for the installed unit. Mock routes are served only when mock mode is
  // explicitly selected via EXECUTION_MOCK_MODE=true (or the client-side
  // VITE_EXECUTION_USE_MOCK build flag, which routes the browser to the same
  // in-memory store through the mock server below).
  const MOCK_MODE = process.env.EXECUTION_MOCK_MODE === 'true';
  // M2 cutover: default execution surface is the broker gateway's
  // worker.execution catalog — the same 18-route contract as legacy
  // execution-srv, mounted at /api/workers/execution instead of
  // /api/execution (prefix rewritten in proxyToExecution below; the
  // broker's GET / serves the legacy-exact server health shape).
  // Rollback: EXECUTION_SURFACE=legacy restores full-path passthrough
  // to execution-srv:3110 (explicit EXECUTION_SRV_URL always wins).
  const EXECUTION_SURFACE = (process.env.EXECUTION_SURFACE || 'broker').toLowerCase();
  const EXECUTION_SRV_URL = process.env.EXECUTION_SRV_URL ||
    (EXECUTION_SURFACE === 'legacy' ? 'http://localhost:3110' : 'http://localhost:4080/api/workers/execution');

  app.use(express.json());

  if (MOCK_MODE) {
    // --- MOCK MODE: in-memory seed data (explicit configuration only) ---

    // 0. Root Readiness Check
    app.get('/health', (req, res) => {
      res.json(mockStore.getRootHealth());
    });

    // 0b. Inline Health Summary
    app.get('/api/execution/health', (req, res) => {
      res.json(mockStore.getInlineHealth());
    });

    // 1. Lifecycle state — the aggregate root
    app.get('/api/execution/requests/:id/state', (req, res) => {
      const state = mockStore.getRequestState(req.params.id);
      if (!state) {
        return res.status(404).json({ error: `Request ${req.params.id} not found` });
      }
      res.json(state);
    });

    // 2. Lease integrity — stale active leases
    app.get('/api/execution/leases/stale', (req, res) => {
      res.json(mockStore.getStaleLeases());
    });

    // 2b. Lease lifecycle
    app.get('/api/execution/leases/:id/lifecycle', (req, res) => {
      const lifecycle = mockStore.getLeaseLifecycle(req.params.id);
      if (!lifecycle) {
        return res.status(404).json({ error: `Lease ${req.params.id} not found` });
      }
      res.json(lifecycle);
    });

    // 3. Cross-table consistency scan
    app.get('/api/execution/health/integrity-scan', (req, res) => {
      res.json(mockStore.getIntegrityScan());
    });

    // 4. Attempt/lease/request tree
    app.get('/api/execution/requests/:id/attempts', (req, res) => {
      const tree = mockStore.getRequestAttemptsTree(req.params.id);
      if (!tree) {
        return res.status(404).json({ error: `Request ${req.params.id} not found` });
      }
      res.json(tree);
    });

    // 4b. Receipts lineage
    app.get('/api/execution/requests/:id/receipts/lineage', (req, res) => {
      const lineage = mockStore.getReceiptsLineage(req.params.id);
      if (!lineage) {
        return res.status(404).json({ error: `Request ${req.params.id} not found` });
      }
      res.json(lineage);
    });

    // 5. Fleet view
    app.get('/api/execution/health/by-executor', (req, res) => {
      const executorId = req.query.executor_id as string | undefined;
      res.json(mockStore.getFleetByExecutor(executorId));
    });

    // 5b. Status distribution
    app.get('/api/execution/health/status-distribution', (req, res) => {
      res.json(mockStore.getStatusDistribution());
    });

    // 6. Pipeline origin (lineage-honest)
    app.get('/api/execution/receipts/:id/pipeline-origin', (req, res) => {
      const origin = mockStore.getPipelineOrigin(req.params.id);
      if (!origin) {
        return res.status(404).json({ error: `Receipt ${req.params.id} not found` });
      }
      res.json(origin);
    });
  } else {
    // --- LIVE MODE: proxy every observability route to the execution surface ---
    // Broker (default): /api/execution/* is rewritten onto the worker.execution
    // catalog (/api/workers/execution/*); /health maps to the catalog root,
    // which serves the legacy-exact server health shape. Legacy: full-path
    // passthrough to execution-srv:3110. Upstream failures are returned as
    // explicit errors — the client never receives mock/seed data in live mode.

    const toUpstreamPath = (p: string): string => {
      if (EXECUTION_SURFACE === 'legacy') return p;
      if (p === '/health') return '/';
      return p.replace(/^\/api\/execution/, '') || '/';
    };

    const proxyToExecutionSrv = async (req: express.Request, res: express.Response) => {
      const qs = req.url.includes('?') ? req.url.substring(req.url.indexOf('?')) : '';
      const targetUrl = `${EXECUTION_SRV_URL}${toUpstreamPath(req.path)}${qs}`;
      try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 5000);
        const upstream = await fetch(targetUrl, {
          method: req.method,
          headers: { 'Content-Type': 'application/json' },
          signal: controller.signal,
          body: ['POST', 'PUT', 'PATCH'].includes(req.method) && req.body
            ? JSON.stringify(req.body)
            : undefined,
        });
        clearTimeout(timeout);
        const text = await upstream.text();
        let data: any = null;
        try {
          data = JSON.parse(text);
        } catch {
          data = { raw: text };
        }
        return res.status(upstream.status).json(data);
      } catch (err: any) {
        return res.status(502).json({
          error: `execution surface unreachable at ${EXECUTION_SRV_URL} (surface: ${EXECUTION_SURFACE})`,
          detail: err?.message || 'timeout',
        });
      }
    };

    // Root readiness (client getRootHealth hits /health)
    app.get('/health', (req, res) => proxyToExecutionSrv(req, res));

    // All observability routes under /api/execution/*
    app.all('/api/execution/*', (req, res) => proxyToExecutionSrv(req, res));
  }

  // --- VITE MIDDLEWARE SETUP ---
  if (process.env.NODE_ENV !== 'production') {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: 'spa',
    });
    app.use(vite.middlewares);
  } else {
    const distPath = path.join(process.cwd(), 'dist');
    app.use(express.static(distPath));
    app.get('*', (req, res) => {
      res.sendFile(path.join(distPath, 'index.html'));
    });
  }

  app.listen(PORT, '0.0.0.0', () => {
    console.log(`[execution-ui] ${MOCK_MODE ? 'MOCK' : `LIVE (proxying ${EXECUTION_SRV_URL})`} on http://0.0.0.0:${PORT}`);
  });
}

startServer();
