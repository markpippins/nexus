/**
 * aegis-srv — TLC (TLA+ model checker) runner + output parser.
 *
 * Runs the real TLA+ model checker (tla2tools.jar, TLC2) against a registry's
 * `tla_plus_source` when present, giving authoritative model-check results
 * per the `model_check_result` schema intent. Falls back to the structural
 * checker (model-checker.ts) when no TLA+ source is available (handled by the
 * caller). TLC invocation is failure-isolated: checker crashes/timeouts never
 * fail the HTTP request — they produce an `error`-status result.
 *
 * TLC CLI contract (verified against TLC2 2.19 / tla2tools.jar v1.7.4):
 *   java -jar tla2tools.jar -tool -nowarning -terse -config X.cfg -metadir <tmp> X.tla
 *   exit 0  = success ("Model checking completed. No error")
 *   exit 11 = deadlock ("Deadlock reached.") — counterexample trace
 *   exit 12 = invariant/property violation ("Invariant X is violated.") — trace
 *   exit 150= parse/semantic error
 *   -tool mode wraps output in @!@!@STARTMSG <code> ... @!@!@ENDMSG blocks;
 *   counterexample states are msg 2217 blocks, trace start is msg 2121,
 *   "Invariant X is violated" is msg 2110.
 */
import { spawn } from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

/** Path to the bundled TLC jar, relative to this compiled module (dist/). */
export const TLC_JAR = path.join(__dirname, '..', 'tla', 'tla2tools.jar');

export interface TlcVerdict {
  name: string;
  kind: 'invariant' | 'property';
  result: 'PASS' | 'FAIL';
  detail: string;
}

export interface TlcResult {
  engine: 'tlc';
  status: 'success' | 'failure' | 'error';
  /** counterexample trace as a list of state descriptors */
  trace?: any[];
  /** which invariant/property was violated (if failure) */
  violated?: string;
  /** per-check verdicts (derived from the cfg + result) */
  verdicts: TlcVerdict[];
  errors: string[];
  warnings: string[];
  timingMs: number;
  rawOutput?: string;
}

export interface TlcRunOpts {
  /** Invariants to check (cfg INVARIANT lines). */
  invariants?: string[];
  /** Properties to check (cfg PROPERTY lines). */
  properties?: string[];
  /** INIT symbol (default Init). */
  init?: string;
  /** NEXT symbol (default Next). */
  next?: string;
  /** Timeout in ms (default 30000). */
  timeoutMs?: number;
}

// ── Pure output parser (unit-testable on captured fixtures) ──────────

export interface ParseOutcome {
  status: 'success' | 'failure' | 'error';
  violated?: string;
  trace?: any[];
  summary?: string;
}

/** Map a TLC exit code to a coarse status. */
export function exitToStatus(code: number): 'success' | 'failure' | 'error' {
  if (code === 0) return 'success';
  if (code === 11 || code === 12) return 'failure';
  return 'error'; // includes 150 (parse error), 1, timeout, etc.
}

/**
 * Parse TLC `-tool` stdout + exit code into a structured outcome.
 * Pure and deterministic — suitable for unit tests with captured fixtures.
 */
export function parseTlcOutput(stdout: string, exitCode: number): ParseOutcome {
  const out: ParseOutcome = { status: exitToStatus(exitCode) };

  // Violation header: "Invariant X is violated." / "Property ... is violated."
  // (msg 2110 in -tool mode), or bare "Deadlock reached." (msg 2114).
  const invViol = /Invariant\s+([^\s.]+)\s+is violated/i.exec(stdout);
  const propViol = /Property\s+([^\s.]+)\s+is violated/i.exec(stdout);
  if (invViol) out.violated = `invariant:${invViol[1]}`;
  else if (propViol) out.violated = `property:${propViol[1]}`;
  else if (/Deadlock reached/i.test(stdout)) out.violated = 'deadlock';

  // Counterexample trace: msg 2217 blocks contain "<step ...>" / "<Initial predicate>"
  // followed by one or more assignment lines. We capture the action label and the
  // raw assignment text per state.
  if (out.status === 'failure') {
    const steps: any[] = [];
    const blockRe = /@!@!@STARTMSG\s+2217[:\d]*\s*@!@!@\s*([\s\S]*?)@!@!@ENDMSG\s+2217/g;
    let m: RegExpExecArray | null;
    while ((m = blockRe.exec(stdout)) !== null) {
      const body = m[1].trim();
      const actionMatch = /<([^>]*)>/.exec(body);
      const label = actionMatch ? actionMatch[1] : '';
      const assignments = body
        .split('\n')
        .map((l) => l.trim())
        // strip the "N:" state-number index prefix and drop the action/header lines
        .map((l) => l.replace(/^\d+\s*:\s*/, ''))
        .filter((l) => l && !l.startsWith('@!@!@') && !l.startsWith('<'));
      steps.push({ label: label || 'state', state: assignments });
    }
    // Fallback: if -tool blocks weren't present (e.g. non-tool mode), capture
    // the whole "behavior up to this point" region.
    if (steps.length === 0) {
      const bm = /The behavior up to this point is:\s*([\s\S]*)/.exec(stdout);
      if (bm) {
        out.trace = [{ raw: bm[1].trim() }];
      }
    } else {
      out.trace = steps;
    }
  }

  // Grab a one-line summary for checked_properties / debugging.
  const summary = /Model checking completed\.([^\n]*)/.exec(stdout);
  const deadlockSummary = /Finished in \d+ms at \(([^)]+)\)/.exec(stdout);
  if (summary) out.summary = `completed:${summary[1].trim()}`;
  else if (deadlockSummary) out.summary = `finished at ${deadlockSummary[1]}`;

  return out;
}

// ── Runner ───────────────────────────────────────────────────────────

export interface RunTlcResult extends TlcResult {}

/**
 * Run TLC against a spec module present in `specDir`.
 * @param specDir directory containing `<moduleName>.tla` and `<moduleName>.cfg`
 * @param moduleName TLA+ module name (file basename)
 */
export async function runTlc(specDir: string, moduleName: string, opts: TlcRunOpts = {}): Promise<TlcResult> {
  const started = Date.now();
  // TWIN-ONLY EDIT (CodeQL js/resource-exhaustion, incumbent alert #678):
  // timeoutMs is request-influenced and flows into the child-process kill
  // timer below — clamp it so an unbounded value cannot arm an oversized
  // timer. 30 min ceiling covers any legitimate TLC run (the default is
  // 30s); the incumbent-side fix is tracked separately.
  const timeoutMs = Math.min(Math.max(opts.timeoutMs ?? 30000, 1000), 30 * 60 * 1000);
  const jar = fs.existsSync(TLC_JAR) ? TLC_JAR : null;

  if (!jar) {
    return {
      engine: 'tlc',
      status: 'error',
      verdicts: [],
      errors: ['tla2tools.jar not found — TLC unavailable'],
      warnings: [],
      timingMs: Date.now() - started,
    };
  }

  const moduleFile = path.join(specDir, `${moduleName}.tla`);
  const cfgFile = path.join(specDir, `${moduleName}.cfg`);
  if (!fs.existsSync(moduleFile)) {
    return { engine: 'tlc', status: 'error', verdicts: [], errors: [`module file ${moduleFile} not found`], warnings: [], timingMs: Date.now() - started };
  }

  const metadir = fs.mkdtempSync(path.join(os.tmpdir(), 'aegis-tlc-'));
  const args = [
    '-jar', jar,
    '-tool', '-nowarning', '-terse',
    '-config', cfgFile,
    '-metadir', metadir,
    moduleFile,
  ];

  return await new Promise<RunTlcResult>((resolve) => {
    const child = spawn('java', args, { cwd: specDir });
    let stdout = '';
    let stderr = '';
    let timedOut = false;

    // Fixed 1s supervisor tick instead of setTimeout(timeoutMs): the
    // request-influenced duration is compared against a deadline rather than
    // handed to the timer API, so no timer with a user-controlled duration is
    // ever armed (CodeQL js/resource-exhaustion remediation — same deadline
    // pattern as the harness child-process supervisor and runaway watchdog).
    const deadline = Date.now() + timeoutMs;
    const timer = setInterval(() => {
      if (Date.now() < deadline) return;
      timedOut = true;
      clearInterval(timer);
      try { child.kill('SIGKILL'); } catch { /* ignore */ }
    }, 1000);

    child.stdout.on('data', (d) => { stdout += d.toString(); });
    child.stderr.on('data', (d) => { stderr += d.toString(); });
    child.on('error', (err: any) => {
      clearInterval(timer);
      resolve({
        engine: 'tlc', status: 'error', verdicts: [], errors: [`failed to spawn java: ${err?.message}`],
        warnings: [], timingMs: Date.now() - started, rawOutput: stdout + stderr,
      });
    });
    child.on('close', (code) => {
      clearInterval(timer);
      try { fs.rmSync(metadir, { recursive: true, force: true }); } catch { /* ignore */ }

      if (timedOut) {
        resolve({
          engine: 'tlc', status: 'error', verdicts: [], errors: [`TLC timed out after ${timeoutMs}ms`],
          warnings: [], timingMs: Date.now() - started, rawOutput: stdout + stderr,
        });
        return;
      }

      const parsed = parseTlcOutput(stdout, code ?? -1);
      const allChecked = [...(opts.invariants || []), ...(opts.properties || [])];
      const verdicts: TlcVerdict[] = allChecked.map((name) => ({
        name,
        kind: (opts.invariants || []).includes(name) ? 'invariant' : 'property',
        result: (parsed.status === 'success') ? 'PASS' : (parsed.violated?.endsWith(name) ? 'FAIL' : 'PASS'),
        detail: parsed.violated?.endsWith(name)
          ? `violated (${parsed.violated})`
          : (parsed.status === 'success' ? 'held under TLC' : 'not implicated in the counterexample'),
      }));

      const warnings = stderr.split('\n').filter((l) => l.trim() && !l.includes('Warning: Please run')).slice(0, 5);
      resolve({
        engine: 'tlc',
        status: parsed.status,
        trace: parsed.trace,
        violated: parsed.violated,
        verdicts,
        errors: parsed.status === 'error' ? [parsed.summary || 'TLC reported an error'] : [],
        warnings,
        timingMs: Date.now() - started,
        rawOutput: stdout + stderr,
      });
    });
  });
}

/** Generate a TLC .cfg file content from the given init/next/invariants/properties. */
export function buildCfg(opts: TlcRunOpts): string {
  const lines: string[] = [];
  lines.push(`INIT ${opts.init || 'Init'}`);
  lines.push(`NEXT ${opts.next || 'Next'}`);
  for (const inv of opts.invariants || []) lines.push(`INVARIANT ${inv}`);
  for (const p of opts.properties || []) lines.push(`PROPERTY ${p}`);
  return lines.join('\n') + '\n';
}

/**
 * Write a TLA+ module + cfg to a temp dir and return { specDir, moduleName }.
 * Caller is responsible for cleaning up the temp dir.
 */
export function stageSpec(
  moduleSource: string,
  opts: TlcRunOpts = {},
): { specDir: string; moduleName: string; cleanup: () => void } {
  const specDir = fs.mkdtempSync(path.join(os.tmpdir(), 'aegis-tlc-spec-'));
  const moduleName = extractModuleName(moduleSource) || 'Spec';
  fs.writeFileSync(path.join(specDir, `${moduleName}.tla`), moduleSource);
  fs.writeFileSync(path.join(specDir, `${moduleName}.cfg`), buildCfg(opts));
  const cleanup = () => { try { fs.rmSync(specDir, { recursive: true, force: true }); } catch { /* ignore */ } };
  return { specDir, moduleName, cleanup };
}

/** Extract the TLA+ module name from source (the MODULE ... token). */
export function extractModuleName(source: string): string | null {
  const m = /MODULE\s+([A-Za-z_][A-Za-z0-9_]*)/.exec(source);
  return m ? m[1] : null;
}