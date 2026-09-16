import { runPgDoctrineLookupConformance } from "../src/runtime/doctrineLookup.pg.test.js";

await runPgDoctrineLookupConformance();
console.log("pg doctrine lookup: conformance passed");
