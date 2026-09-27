#!/usr/bin/env node
/**
 * CLI for the Structure conformance second runtime (Structure S6).
 *
 *   node src/cli.js --verify <vectors.json> [--implementation ID] [--out FILE]
 *
 * Exits 0 iff every vector passes; the printed verdict JSON is
 * machine-readable and its verdict_hash must equal the Python reference's
 * for the same vectors — that equality is the cross-runtime proof.
 */

import { readFileSync, writeFileSync } from "node:fs";

import { ConformanceError, verifyVectors } from "./verify.js";

function parseArgs(argv) {
  const args = { implementation: undefined, out: undefined, verify: undefined };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--verify") args.verify = argv[++i];
    else if (a === "--implementation") args.implementation = argv[++i];
    else if (a === "--out") args.out = argv[++i];
    else if (a === "--help" || a === "-h") args.help = true;
    else throw new ConformanceError(`unknown argument ${a}`);
  }
  return args;
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help || !args.verify) {
    console.log(
      "usage: node src/cli.js --verify <vectors.json> [--implementation ID] [--out FILE]",
    );
    return args.help ? 0 : 2;
  }
  const vectors = JSON.parse(readFileSync(args.verify, "utf8"));
  const verdict = verifyVectors(vectors, { implementation: args.implementation });
  const rendered = JSON.stringify(verdict, null, 2) + "\n";
  if (args.out) writeFileSync(args.out, rendered, "utf8");
  else process.stdout.write(rendered);
  const failed = verdict.vector_results.filter((r) => !r.ok);
  console.error(
    `conformance: ${verdict.vector_results.length - failed.length}/${verdict.vector_results.length} vectors pass`,
  );
  return failed.length === 0 ? 0 : 1;
}

try {
  process.exit(main());
} catch (err) {
  console.error(`${err.name ?? "Error"}: ${err.message}`);
  process.exit(2);
}
