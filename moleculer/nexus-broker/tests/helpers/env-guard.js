/**
 * Preload guard for the whole test run (`node --require ./tests/helpers/env-guard.js --test ...`).
 *
 * `helpers/isolated-mongo.js` validates the environment when it is imported,
 * which covers every file that uses it — but "every file that uses it" is the
 * problem: a new test file that boots a broker without the helper is exactly
 * the file that would write production state, and it would never import the
 * check. Preloading this module puts `assertSafeTestEnv()` in front of every
 * test file in the run, whether or not that file knows the helper exists.
 *
 * Deliberately does nothing else. Patching drivers, trapping queries or
 * rewriting connection strings from a preload would make the failure mode
 * "tests pass, but against something else"; this only refuses a configuration
 * that names deployment state, and leaves everything else byte for byte as it
 * was.
 *
 * Kept separate from the helper so it can be required by `node --require`
 * without pulling in anything but node built-ins and that module.
 */
const { assertSafeTestEnv } = require('./isolated-mongo')

assertSafeTestEnv()
