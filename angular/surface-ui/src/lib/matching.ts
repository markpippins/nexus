import { schemaFields } from "./schema";
import type { Widget } from "./widget-types";

export interface MatchResult {
  widget: Widget;
  score: number;
  sharedEndpoints: string[];
  sharedInputs: string[];
  sharedResponseFields: string[];
}

const jaccard = (a: string[], b: string[]) => {
  const sa = new Set(a);
  const sb = new Set(b);
  if (!sa.size && !sb.size) return 1;
  const inter = [...sa].filter((x) => sb.has(x)).length;
  return inter / (sa.size + sb.size - inter);
};

const inputKeys = (w: Widget) => w.inputs.map((i) => i.name.toLowerCase());
const endpointKeys = (w: Widget) => w.endpoints.map((e) => e.signature);
const responseKeys = (w: Widget) =>
  Object.values(w.mocks).flatMap((m) => schemaFields(m.schema ?? null));

/**
 * Find widgets that share the same REST API *and* have a compatible input
 * scheme. Sharing an endpoint is required; the score ranks by how closely the
 * input scheme and response shape line up.
 */
export function findMatches(target: Widget, all: Widget[]): MatchResult[] {
  const tEndpoints = endpointKeys(target);
  const tInputs = inputKeys(target);
  const tFields = responseKeys(target);

  return all
    .filter((w) => w.id !== target.id)
    .map((w) => {
      const sharedEndpoints = endpointKeys(w).filter((e) => tEndpoints.includes(e));
      const sharedInputs = inputKeys(w).filter((i) => tInputs.includes(i));
      const sharedResponseFields = responseKeys(w).filter((f) => tFields.includes(f));
      const score =
        0.55 * jaccard(tInputs, inputKeys(w)) +
        0.3 * jaccard(tFields, responseKeys(w)) +
        0.15 * jaccard(tEndpoints, endpointKeys(w));
      return { widget: w, score, sharedEndpoints, sharedInputs, sharedResponseFields };
    })
    .filter((m) => m.sharedEndpoints.length > 0)
    .sort((a, b) => b.score - a.score);
}
