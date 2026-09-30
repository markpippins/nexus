// ── Wind event processor & scheduler config ─────────────────────────

export const config = {
  // Event processor polling interval (ms)
  pollIntervalMs: parseInt(process.env.WIND_POLL_INTERVAL_MS || '5000', 10),

  // Batch size per poll cycle
  batchSize: parseInt(process.env.WIND_POLL_BATCH_SIZE || '10', 10),

  // Recovery lag (ms) — skip events created within this window,
  // giving the real-time (NATS) path a chance to handle them.
  recoveryLagMs: parseInt(process.env.WIND_RECOVERY_LAG_MS || '5000', 10),

  // Rover scheduler interval (ms) — 30 minutes
  roverSchedulerIntervalMs: parseInt(process.env.WIND_ROVER_SCHEDULER_INTERVAL_MS || `${30 * 60 * 1000}`, 10),

  // NATS URL
  natsUrl: process.env.NATS_URL || 'nats://localhost:4222',

  // NEXUS PG DSN
  pgDsn: process.env.WIND_PG_DSN || process.env.NEXUS_PG_DSN || 'postgresql://pguser:pgpass@localhost:5432/nexus',

  // Nebula agent record URL (for outbox notifications)
  nebulaUrl: process.env.NEBULA_URL || 'http://localhost:3101',
};
