/**
 * Unit test: widening the failover ladder (src/ladder.ts).
 *
 * The property that was missing on 2026-11-06: a role's ladder was exactly its
 * configured bundles, so a role whose three providers were dead had three
 * dead rungs — while nvidia answered in ten seconds the whole time.
 *
 * Usage: npx tsx tests/ladder.test.ts
 */

import {
  DEFAULT_CATALOG,
  DEFAULT_MAX_RUNGS,
  catalogFromEnv,
  maxRungsFromEnv,
  mergeLadder,
  providerRankOfModel,
} from "../src/ladder";

let passed = 0;
let failed = 0;

function check(name: string, cond: boolean, detail?: unknown) {
  if (cond) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL ${name}${detail === undefined ? "" : ` — ${JSON.stringify(detail)}`}`);
  }
}

const rung = (model: string, source: "configured" | "catalog" = "catalog") => ({ model, source });

// ── provider preference ─────────────────────────────────────────────
check("nvidia ranks first", providerRankOfModel("nvidia/nvidia/nemotron-3.5-super-vl-120b-a12b") === 0);
check("opencode ranks second", providerRankOfModel("opencode/big-pickle") === 1);
check("opencode-go ranks third", providerRankOfModel("opencode-go/glm-5.3-flash") === 2);
check("openrouter ranks fourth", providerRankOfModel("openrouter/openrouter/free") === 3);
check("ollama ranks fifth", providerRankOfModel("ollama/qwen2.5-coder") === 4);
check("unknown provider ranks last", providerRankOfModel("someprovider/model") === 6);
check("null model ranks last", providerRankOfModel(null) === 6);
check("prefix match is on the first slash", providerRankOfModel("nvidia/z-ai/glm-5.3-flash") === 0);

// ── catalog parsing ────────────────────────────────────────────────
check("unset catalog uses the default", catalogFromEnv(undefined).length === DEFAULT_CATALOG.length);
check("empty catalog is an explicit opt-out", catalogFromEnv("").length === 0);
check(
  "catalog entries are trimmed and blanks dropped",
  JSON.stringify(catalogFromEnv(" a , b ,, ")) === JSON.stringify(["a", "b"]),
);
check(
  "default catalog starts with nvidia",
  DEFAULT_CATALOG[0].startsWith("nvidia/"),
  DEFAULT_CATALOG[0],
);
check("default catalog includes the openrouter free router", DEFAULT_CATALOG.includes("openrouter/openrouter/free"));

check("unset rung cap uses the default", maxRungsFromEnv(undefined) === DEFAULT_MAX_RUNGS);
check("zero rung cap falls back to the default", maxRungsFromEnv("0") === DEFAULT_MAX_RUNGS);
check("garbage rung cap falls back to the default", maxRungsFromEnv("nope") === DEFAULT_MAX_RUNGS);
check("explicit rung cap is honored", maxRungsFromEnv("3") === 3);

// ── merge rules ────────────────────────────────────────────────────
const configured = [
  rung("opencode/big-pickle", "configured"), // primary (currently rate-limited)
  rung("openrouter/openai/gpt-oss-120b", "configured"),
];

const merged = mergeLadder(configured, DEFAULT_CATALOG, (m) => rung(m), DEFAULT_MAX_RUNGS);

check("configured primary stays first", merged[0].model === "opencode/big-pickle", merged[0]);
check("the ladder is widened", merged.length > configured.length, merged.length);
check("the cap is respected", merged.length <= DEFAULT_MAX_RUNGS, merged.length);
check("nvidia is reachable before the openrouter fallback", merged.slice(1).some((r) => r.model.startsWith("nvidia/")));
check(
  "a catalog rung sorts ahead of a lower-ranked configured fallback",
  merged.findIndex((r) => r.model.startsWith("nvidia/")) < merged.findIndex((r) => r.model === "openrouter/openai/gpt-oss-120b"),
  merged.map((r) => r.model),
);
check(
  "configured fallbacks are not dropped while under the cap",
  merged.some((r) => r.model === "openrouter/openai/gpt-oss-120b"),
);
check(
  "no model appears twice",
  new Set(merged.map((r) => r.model)).size === merged.length,
  merged.map((r) => r.model),
);

// A configured rung that is already in the catalog must not be duplicated.
const overlap = mergeLadder(
  [rung("opencode/nemotron-3.5-lightning-free"), rung("opencode/nemotron-3.5-lightning-free")],
  DEFAULT_CATALOG,
  (m) => rung(m),
  10,
);
check(
  "duplicate configured rungs collapse to one",
  overlap.filter((r) => r.model === "opencode/nemotron-3.5-lightning-free").length === 1,
  overlap.map((r) => r.model),
);

// Order within one provider is preserved (configured order, not catalog order).
const sameProvider = mergeLadder(
  [rung("opencode/primary"), rung("opencode/second"), rung("opencode/third")],
  [],
  (m) => rung(m),
  10,
);
check(
  "configured order survives inside one provider",
  sameProvider.map((r) => r.model).join(",") === "opencode/primary,opencode/second,opencode/third",
  sameProvider.map((r) => r.model),
);

// The cap removes from the END of the preference order, never the primary.
const capped = mergeLadder(configured, DEFAULT_CATALOG, (m) => rung(m), 3);
check("cap keeps the primary", capped[0].model === "opencode/big-pickle");
check("cap yields exactly the cap", capped.length === 3, capped.length);

// No configured bundle → no ladder. Admission rejects this upstream; inventing
// a primary here would bypass it.
check("an empty configured ladder stays empty", mergeLadder([], DEFAULT_CATALOG, (m) => rung(m)).length === 0);

// An unset primary model is preserved rather than silently dropped.
const unsetPrimary = mergeLadder([{ model: undefined }, { model: "opencode/big-pickle" }], [], (m) => rung(m), 5);
check("an unset primary keeps its slot", unsetPrimary.length === 2 && unsetPrimary[0].model === undefined);
check("an unset primary does not block dedupe of the fallback", unsetPrimary[1].model === "opencode/big-pickle");

// Opting out of the catalog leaves the configured ladder as-is.
check(
  "empty catalog leaves the configured ladder untouched",
  JSON.stringify(mergeLadder(configured, [], (m) => rung(m), 5)) === JSON.stringify(configured),
);

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
