/**
 * framing-parity-driver.mjs — TypeScript-side adapter for the Ruling 32 §4
 * cross-runtime framing parity guard.
 *
 * Not a test of its own: it loads the shared matrix produced by
 * `python/solscript_framing_parity`, builds one fresh in-memory interpreter
 * per case and emits canonical JSON for the Python guard to compare against
 * the Python and SQL results.
 *
 * Usage (the Python guard does this for you):
 *   node framing-parity-driver.mjs <matrix.json> <file-url-to-compiled-interpreter.js>
 *
 * The interpreter module URL must point at a freshly compiled copy of
 * `typescript/solscript/src/interpreter.ts` so the guard is bound to source,
 * not to a possibly stale `dist/` directory.
 */

import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const [, , matrixPath, interpreterModule] = process.argv;

if (!matrixPath || !interpreterModule) {
  console.error(
    "usage: framing-parity-driver.mjs <matrix.json> <module-path-or-url>",
  );
  process.exit(64);
}

const matrix = JSON.parse(readFileSync(matrixPath, "utf8"));

const moduleUrl = interpreterModule.startsWith("file:")
  ? interpreterModule
  : pathToFileURL(interpreterModule).href;

const { ResolutionInterpreter } = await import(moduleUrl);

const fixture = matrix.fixture;
const dimensions = matrix.dimensions;

const results = {};

for (const testCase of matrix.cases) {
  const interp = new ResolutionInterpreter();

  interp.addEntity({
    id: fixture.entity_id,
    conceptId: fixture.asset_concept_id,
    attributes: {},
  });

  for (const [name, spec] of Object.entries(dimensions)) {
    interp.addFrameDimension({
      id: spec.id,
      name,
      valueKind: spec.value_kind,
      scalarType: spec.scalar_type ?? undefined,
    });
    for (const [valueName, valueId] of Object.entries(spec.values)) {
      interp.addFrameDimensionValue({
        id: valueId,
        dimensionId: spec.id,
        value: valueName,
      });
    }
  }

  const proposition = {
    id: fixture.proposition_id,
    title: testCase.id,
    assetConceptId: fixture.asset_concept_id,
    subjectEntityId: fixture.entity_id,
    disposition: "Pending",
    assertions: [],
    comparisons: [],
    semanticTypeId: testCase.required_dimensions.length
      ? fixture.semantic_type_id
      : undefined,
    frameValues: [],
  };
  interp.addProposition(proposition);

  testCase.instance_frames.forEach((frame, index) => {
    const spec = dimensions[frame.dimension];
    if (!spec) throw new Error(`unknown matrix dimension: ${frame.dimension}`);
    const referenceValueId = spec.values[frame.reference_value];
    if (!referenceValueId) {
      throw new Error(
        `unknown matrix frame value: ${frame.dimension}=${frame.reference_value}`,
      );
    }
    interp.addPropositionFrameValue({
      id: `${testCase.id}:pfv:${index}`,
      propositionId: proposition.id,
      dimensionId: spec.id,
      referenceValueId,
    });
  });

  try {
    const [disposition, allPassed, contextStatus] =
      testCase.context === null
        ? interp.evaluateProposition(proposition)
        : interp.evaluateProposition(proposition, testCase.context);
    results[testCase.id] = {
      kind: "outcome",
      disposition: disposition ?? null,
      all_passed: Boolean(allPassed),
      context_status: contextStatus,
    };
  } catch (error) {
    results[testCase.id] = {
      kind: "error",
      message: String((error && error.message) || error),
    };
  }
}

process.stdout.write(JSON.stringify({ results }) + "\n");
