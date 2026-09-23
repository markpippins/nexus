module.exports = {
  preset: 'ts-jest',
  testEnvironment: 'node',
  roots: ['<rootDir>/test'],
  testMatch: ['**/*.test.ts'],
  moduleFileExtensions: ['ts', 'js', 'json'],
  collectCoverageFrom: [
    'services/**/*.ts',
    '!services/**/*.d.ts'
  ],
  coverageDirectory: 'coverage',
  forceExit: true,
  detectOpenHandles: true,
};
