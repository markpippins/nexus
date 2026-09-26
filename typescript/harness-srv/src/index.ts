// harness-srv — Generic execution harness: merges Tackle role context with
// Wind task context and invokes an agent.
//
// The Express app and every route live in ./app.ts (extracted verbatim so
// tests can exercise the rate limiter and the path-containment guards
// without binding a port). This file owns the process lifecycle only.

import { resolveContext, resolveRoleModel, emitEvent, pool, redis, checkConfigAdmission, incrementConsumedUnits, emitGovernanceReceipt } from "./db";
import { ADMISSION_OUTCOME } from "./admission";
import { execFile, spawn } from "child_process";
import { promisify } from "util";
import { createHash } from "crypto";
import type { ServerResponse } from "http";
import { writeFile, readFile, unlink, mkdir, appendFile } from "fs/promises";
import { join, resolve } from "path";
import { v4 as uuidv4 } from "uuid";
import {
  app, PORT, WORK_DIR, PROMPT_DIR, LOG_FILE, log, startWatchdog,
} from "./app";

// ── Start ───────────────────────────────────────────────────────────

const server = app.listen(PORT, () => {
  console.log(`[harness-srv] listening on port ${PORT}`);
  console.log(`[harness-srv] work dir: ${WORK_DIR}`);
  console.log(`[harness-srv] prompt dir: ${PROMPT_DIR}`);
  log("info", `listening on port ${PORT} (work dir: ${WORK_DIR}, log: ${LOG_FILE})`);
  startWatchdog();
});

server.on('error', (err: NodeJS.ErrnoException) => {
  if (err.code === 'EADDRINUSE') {
    console.error(`harness-srv: port ${PORT} already in use, exiting (code EADDRINUSE)`);
  } else {
    console.error('harness-srv: listen error:', err.message);
  }
  process.exit(1);
});
