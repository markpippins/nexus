/**
 * liveness-probe tests (WO-1 task 4).
 *
 * Covers the restart-builder classification contract: a child that exits
 * before writing must NOT be reported as a successful restart. The
 * gated-no-op case reproduces the live defect (plan 8261653: child died
 * in seconds under the default runtime emit mode while the endpoint
 * answered restarted=true).
 *
 * Pure classification only — no real processes spawned.
 *
 * Usage:
 *   cd typescript/conduit-mcp && npx vitest run src/liveness-probe.test.ts
 */
import { describe, test, expect } from "vitest";

import {
  ChildHandle,
  GATED_NOOP_MARKER,
  isGatedNoOp,
  probeChildLiveness,
} from "./liveness-probe";

/** Scriptable fake: resolves the exit promise on demand. */
function fakeChild(opts: {
  exitAfterMs?: number | null; // null = never exits (stays running)
  code?: number | null;
  signal?: string | null;
  stdout?: string;
  stderr?: string;
  aliveAfterExit?: boolean; // simulate kill(0) still true (zombie edge)
}): { child: ChildHandle; fireExit: () => void } {
  let exitResolve: (e: { code: number | null; signal: string | null }) => void =
    () => {};
  const onceExit = new Promise<{ code: number | null; signal: string | null }>(
    (resolve) => {
      exitResolve = resolve;
    },
  );
  let exited = false;
  const child: ChildHandle = {
    pid: 424242,
    onceExit,
    isAlive: () => (exited && !opts.aliveAfterExit ? false : true),
    stdout: () => opts.stdout ?? "",
    stderr: () => opts.stderr ?? "",
  };
  const fireExit = () => {
    exited = true;
    // Distinguish "not provided" (default 0) from an explicit null code
    // (signal exit) — ?? would collapse the two.
    exitResolve({
      code: opts.code !== undefined ? opts.code : 0,
      signal: opts.signal !== undefined ? opts.signal : null,
    });
  };
  if (typeof opts.exitAfterMs === "number") {
    setTimeout(fireExit, opts.exitAfterMs);
  }
  return { child, fireExit };
}

describe("isGatedNoOp marker matching", () => {
  test("matches the main.py gated marker in a stdout stream", () => {
    const line =
      "Processing plan: 8261653 - W-A for role builder\n" +
      "  [gated] Legacy WR file-emission disabled (CONDUIT_WR_EMIT_MODE='runtime'). " +
      "Plan 8261653 role=builder left for runtime dispatch — no DCO file written, no executor launched.\n";
    expect(isGatedNoOp(line)).toBe(true);
  });

  test("does not match unrelated stdout", () => {
    expect(isGatedNoOp("Restarting builder for plan: 8261653\n")).toBe(false);
    expect(isGatedNoOp("")).toBe(false);
  });

  test("marker string stays in lockstep with main.py", () => {
    expect(GATED_NOOP_MARKER).toBe("[gated] Legacy WR file-emission disabled");
  });
});

describe("probeChildLiveness classification", () => {
  test("child alive through settle window → running", async () => {
    const { child } = fakeChild({ exitAfterMs: null });
    const result = await probeChildLiveness(child, {
      settleMs: 30,
      pollIntervalMs: 10,
    });
    expect(result.status).toBe("running");
  });

  test("child exits during settle window with clean output → exited", async () => {
    const { child } = fakeChild({
      exitAfterMs: 5,
      code: 0,
      stdout: "Processing plan: 8261653 - W-A for role builder\n",
      stderr: "",
    });
    const result = await probeChildLiveness(child, {
      settleMs: 200,
      pollIntervalMs: 10,
    });
    expect(result.status).toBe("exited");
    expect(result.exitCode).toBe(0);
    expect(result.stdoutTail).toContain("Processing plan: 8261653");
  });

  test("child exits immediately with the gate marker → gated-no-op (the live defect)", async () => {
    const { child } = fakeChild({
      exitAfterMs: 1,
      code: 0,
      stdout:
        "  [gated] Legacy WR file-emission disabled (CONDUIT_WR_EMIT_MODE='runtime'). " +
        "Plan 8261653 role=builder left for runtime dispatch — no DCO file written, no executor launched.\n",
    });
    const result = await probeChildLiveness(child, {
      settleMs: 200,
      pollIntervalMs: 10,
    });
    expect(result.status).toBe("gated-no-op");
    expect(result.exitCode).toBe(0);
    expect(result.stdoutTail).toContain(GATED_NOOP_MARKER);
  });

  test("non-zero exit without the marker → exited with code surfaced", async () => {
    const { child } = fakeChild({
      exitAfterMs: 1,
      code: 1,
      stdout: "Traceback (most recent call last):\n",
      stderr: "ModuleNotFoundError: tackle",
    });
    const result = await probeChildLiveness(child, {
      settleMs: 200,
      pollIntervalMs: 10,
    });
    expect(result.status).toBe("exited");
    expect(result.exitCode).toBe(1);
    expect(result.stderrTail).toContain("ModuleNotFoundError");
  });

  test("signal exit → exited with signal surfaced", async () => {
    const { child } = fakeChild({
      exitAfterMs: 1,
      code: null,
      signal: "SIGKILL",
    });
    const result = await probeChildLiveness(child, {
      settleMs: 200,
      pollIntervalMs: 10,
    });
    expect(result.status).toBe("exited");
    expect(result.exitCode).toBeNull();
    expect(result.exitSignal).toBe("SIGKILL");
  });

  test("stdout tails are bounded", async () => {
    const big = "x".repeat(5000);
    const { child } = fakeChild({ exitAfterMs: 1, code: 0, stdout: big });
    const result = await probeChildLiveness(child, {
      settleMs: 200,
      pollIntervalMs: 10,
    });
    expect(result.stdoutTail!.length).toBeLessThanOrEqual(400);
  });
});
