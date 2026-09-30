module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  roots: ['<rootDir>/test'],
  testMatch: ['**/*.test.ts'],
  moduleFileExtensions: ['ts', 'js', 'json'],
  moduleNameMapper: {
    '^(\\.{1,2}/.*)\\.js$': '$1',
  },
  collectCoverageFrom: ['services/**/*.ts', '!services/**/*.d.ts'],
  coverageDirectory: 'coverage',
  globals: {
    'ts-jest': { diagnostics: false, isolatedModules: true },
  },
  forceExit: true,
  detectOpenHandles: true,
};
