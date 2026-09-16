/** Jest ESM config (package is type:module; @nexus/solscript dist is ESM). */
export default {
  preset: 'ts-jest/presets/default-esm',
  testEnvironment: 'node',
  roots: ['<rootDir>/test'],
  testMatch: ['**/*.test.ts'],
  moduleFileExtensions: ['ts', 'js', 'json'],
  extensionsToTreatAsEsm: ['.ts'],
  collectCoverageFrom: [
    'services/**/*.ts',
    '!services/**/*.d.ts'
  ],
  coverageDirectory: 'coverage',
  forceExit: true,
  detectOpenHandles: true,
  transform: {
    '^.+\\.ts$': ['ts-jest', { useESM: true }],
  },
  moduleNameMapper: {
    '^@nexus/solscript$': '<rootDir>/../../typescript/solscript/dist/index.js',
  },
  // The @nexus/solscript dist is ESM; transform it (and our src) via ts-jest
  // instead of letting jest choke on `export` syntax. Babel not needed — the
  // mapped dist .js is routed through ts-jest by the .ts transform when we
  // treat the resolved path as transformable.
  transformIgnorePatterns: [],
};