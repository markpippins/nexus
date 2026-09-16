import { readdir, readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { runReplayConformance, type ReplayFixtureDocument } from "../src/runtime/replayVerifier.test.js";

const root = join(dirname(fileURLToPath(import.meta.url)), "../../../python/governance-envelope/replay_fixtures");
const names = (await readdir(root)).filter((name) => /^F0[1-7]_.*\.json$/.test(name)).sort();
const documents = await Promise.all(names.map(async (name) =>
  JSON.parse(await readFile(join(root, name), "utf8")) as ReplayFixtureDocument));
await runReplayConformance(documents);
console.log(`replay verifier: ${documents.length} fixture documents passed`);
