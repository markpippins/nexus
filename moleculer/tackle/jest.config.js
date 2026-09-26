module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  roots: ['<rootDir>/test'],
  testMatch: ['**/*.test.ts'],
  moduleFileExtensions: ['ts', 'js', 'json'],
  // The service sources use NodeNext-style explicit .js specifiers (required
  // for the ESM build), which jest-resolve cannot map back to the .ts source
  // on its own. Strip the extension so tests can import the real modules.
  moduleNameMapper: {
    '^(\\.{1,2}/.*)\\.js$': '$1',
  },
  collectCoverageFrom: [
    'services/**/*.ts',
    '!services/**/*.d.ts'
  ],
  coverageDirectory: 'coverage',
  forceExit: true,
  detectOpenHandles: true,
};
