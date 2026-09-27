// Service index — moleculer-runner loads services via the package.json
// glob "services/**/*.service.ts" (compiled: dist/services/**/*.service.js).
// This module exists so `main` and programmatic broker.createService()
// consumers have a stable entrypoint listing both services.
// (Note: this file uses line comments because the glob literal contains
// the sequence star-slash, which terminates a block comment early.)
import { Service, ServiceBroker } from "moleculer";
import ApiService from "./api.service.js";
import AegisService from "./aegis.service.js";

export function makeServices(broker: ServiceBroker): Service[] {
  return [
    new ApiService(broker),
    new AegisService(broker),
  ];
}

export { ApiService, AegisService };
