/**
 * Test setup for fresh-database schema integration tests.
 *
 * Generates a unique test schema name and sets CONDUIT_PG_SCHEMA before the
 * db.ts module is imported, so module-level const PG_SCHEMA picks it up.
 *
 * The schema name is also stored in TEST_CONDUIT_SCHEMA for teardown.
 */

const schema = `test_conduit_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;

process.env.CONDUIT_PG_SCHEMA = schema;
process.env.TEST_CONDUIT_SCHEMA = schema;
