module.exports = {
  testEnvironment: 'node',
  roots: ['<rootDir>/test'],
  testMatch: ['**/*.test.ts'],
  moduleFileExtensions: ['ts', 'js', 'json'],
  moduleNameMapper: {
    '^(\\.{1,2}/.*)\\.js$': '$1',
  },
  // The peb twin's route files are verbatim ESM .js (incumbent sources), so
  // both extensions go through ts-jest (tsconfig allowJs: true). Without the
  // .js entry jest parses them as CJS and the import statements explode.
  transform: {
    '^.+\\.tsx?$': ['ts-jest', { diagnostics: false, isolatedModules: true }],
    '^.+\\.jsx?$': ['ts-jest', { diagnostics: false, isolatedModules: true }],
  },
  collectCoverageFrom: ['services/**/*.{ts,js}', '!services/**/*.d.ts'],
  coverageDirectory: 'coverage',
  forceExit: true,
  detectOpenHandles: true,
};
