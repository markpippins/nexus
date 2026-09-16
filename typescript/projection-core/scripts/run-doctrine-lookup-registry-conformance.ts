import { runRegistryConformance } from "../src/runtime/doctrineLookup.registry.test.js";

await runRegistryConformance();
console.log("doctrine lookup registry: conformance passed");
