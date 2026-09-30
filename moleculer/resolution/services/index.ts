// Service index — moleculer-runner loads services via the package.json
// glob "services/**/*.service.ts" (compiled: dist/services/**/*.service.js).
// This module exists so `main` and programmatic broker.createService()
// consumers have a stable entrypoint listing both services.
// (Line comments only: the glob literal contains star-slash.)
import { Service, ServiceBroker } from "moleculer";
import ApiService from "./api.service.js";
import ResolutionService from "./resolution.service.js";

export function makeServices(broker: ServiceBroker): Service[] {
  return [
    new ApiService(broker),
    new ResolutionService(broker),
  ];
}

export { ApiService, ResolutionService };
