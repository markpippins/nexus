import { Router } from "express";
import os from "os";

export const healthRouter = Router();

// ── In-memory metric history (last 60 snapshots, one per minute) ───

interface MetricPoint {
  timestamp: string;
  cpu_percent: number;
  memory_percent: number;
  memory_used_mb: number;
  memory_total_mb: number;
  active_requests: number;
  latency_avg_ms: number;
}

const metricsHistory: MetricPoint[] = [];

function collectMetrics(): MetricPoint {
  const totalMem = os.totalmem();
  const freeMem = os.freemem();
  const usedMem = totalMem - freeMem;
  const loadAvg = os.loadavg()[0];
  const cpuCount = os.cpus().length;
  const cpuPercent = Math.round((loadAvg / cpuCount) * 100 * 10) / 10;

  return {
    timestamp: new Date().toISOString(),
    cpu_percent: Math.max(0, Math.min(100, cpuPercent)),
    memory_percent: Math.round((usedMem / totalMem) * 1000) / 10,
    memory_used_mb: Math.round(usedMem / (1024 * 1024) * 10) / 10,
    memory_total_mb: Math.round(totalMem / (1024 * 1024) * 10) / 10,
    active_requests: 0, // placeholder — would need request tracking
    latency_avg_ms: 0,
  };
}

// Collect initial snapshot and start periodic collection
metricsHistory.push(collectMetrics());
setInterval(() => {
  metricsHistory.push(collectMetrics());
  if (metricsHistory.length > 60) {
    metricsHistory.shift();
  }
}, 60_000);

function getSystemHealth() {
  const latest = metricsHistory[metricsHistory.length - 1];
  const processUptime = Math.round(process.uptime());
  const systemUptime = Math.round(os.uptime());
  const loadAvg = os.loadavg();
  const cpuCount = os.cpus().length;

  return {
    status: loadAvg[0] > cpuCount * 0.9 ? "degraded" : "ok",
    port: parseInt(process.env.TACKLE_SRV_PORT || "3410", 10),
    pid: process.pid,
    timestamp: new Date().toISOString(),
    uptime_seconds: systemUptime,
    cpu: {
      usage_percent: latest.cpu_percent,
      cores: cpuCount,
      load_average: [Math.round(loadAvg[0] * 100) / 100, Math.round(loadAvg[1] * 100) / 100, Math.round(loadAvg[2] * 100) / 100],
    },
    memory: {
      used_mb: latest.memory_used_mb,
      total_mb: latest.memory_total_mb,
      usage_percent: latest.memory_percent,
      free_mb: Math.round(os.freemem() / (1024 * 1024) * 10) / 10,
      heap_used_mb: Math.round(process.memoryUsage().heapUsed / (1024 * 1024) * 10) / 10,
      heap_total_mb: Math.round(process.memoryUsage().heapTotal / (1024 * 1024) * 10) / 10,
    },
    history: metricsHistory,
  };
}

// GET /health/history — time-series metrics
healthRouter.get("/history", (_req, res) => {
  const health = getSystemHealth();
  res.json({
    status: health.status,
    timestamp: health.timestamp,
    count: health.history.length,
    history: health.history,
  });
});

// GET /health/metrics — current snapshot with full details
healthRouter.get("/metrics", (_req, res) => {
  res.json(getSystemHealth());
});

// POST /health/simulate-load — no-op stub (live server has no load simulation)
healthRouter.post("/simulate-load", (_req, res) => {
  const health = getSystemHealth();
  res.json(health);
});
