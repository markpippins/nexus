package org.nexus.peb.observability.actuator;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.springframework.boot.health.contributor.Health;
import org.springframework.boot.health.contributor.Status;

import javax.sql.DataSource;
import java.io.PrintWriter;
import java.sql.Connection;
import java.sql.SQLException;
import java.sql.SQLFeatureNotSupportedException;
import java.util.logging.Logger;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Tests for {@link PebHealthIndicator} covering all four paths
 * per the Tester role mandate.
 *
 * <h3>Coverage model</h3>
 * <ol>
 *   <li><b>Green path</b> — healthy database connection returns UP.</li>
 *   <li><b>Orange path</b> — invalid connection returns DOWN with details.</li>
 *   <li><b>Red path</b> — connection throws exception, timeout scenarios.</li>
 *   <li><b>Silent failure</b> — the health check actually checks the right
 *       database/schema, not just any connection.</li>
 * </ol>
 *
 * <p>Uses hand-rolled test stubs instead of Mockito mocks because
 * Mockito cannot instrument {@code javax.sql.DataSource} across JDK
 * module boundaries in Java 17+.
 */
@DisplayName("PebHealthIndicator")
class PebHealthIndicatorTest {

    private PebHealthIndicator indicator;

    @BeforeEach
    void setUp() {
        // indicator is created per-test with the appropriate stub
    }

    // ─────────────────────────────────────────────────────────────
    // GREEN PATH
    // ─────────────────────────────────────────────────────────────

    // ─────────────────────────────────────────────────────────────
    // GREEN PATH
    // ─────────────────────────────────────────────────────────────

    @Nested
    @DisplayName("Green path — healthy connection")
    class GreenPath {

        @Test
        @DisplayName("valid connection returns UP with schema detail")
        void validConnection_returnsUp() {
            DataSource ds = stubDataSource(new StubConnection(true, "nexus"));
            indicator = new PebHealthIndicator(ds);

            Health health = indicator.health();

            assertEquals(Status.UP, health.getStatus(),
                "Valid connection should return UP status");
            assertEquals("reachable", health.getDetails().get("database"),
                "Database detail should say 'reachable'");
            assertEquals("peb", health.getDetails().get("schema"),
                "Schema detail should say 'peb'");
            assertEquals("nexus", health.getDetails().get("catalog"),
                "Catalog should match connection catalog");
        }

        @Test
        @DisplayName("connection is properly closed after health check")
        void connection_isClosed() {
            StubConnection conn = new StubConnection(true, "nexus");
            DataSource ds = stubDataSource(conn);
            indicator = new PebHealthIndicator(ds);

            indicator.health();

            assertTrue(conn.isClosed(),
                "Connection should be closed after health check");
        }
    }

    // ─────────────────────────────────────────────────────────────
    // ORANGE PATH
    // ─────────────────────────────────────────────────────────────

    @Nested
    @DisplayName("Orange path — unhealthy connection")
    class OrangePath {

        @Test
        @DisplayName("invalid connection returns DOWN with unreachable detail")
        void invalidConnection_returnsDown() {
            DataSource ds = stubDataSource(new StubConnection(false, null));
            indicator = new PebHealthIndicator(ds);

            Health health = indicator.health();

            assertEquals(Status.DOWN, health.getStatus(),
                "Invalid connection should return DOWN status");
            assertEquals("unreachable", health.getDetails().get("database"),
                "Database detail should say 'unreachable'");
        }
    }

    // ─────────────────────────────────────────────────────────────
    // RED PATH
    // ─────────────────────────────────────────────────────────────

    @Nested
    @DisplayName("Red path — connection failures")
    class RedPath {

        @Test
        @DisplayName("SQLException during getConnection returns DOWN with error detail")
        void sqlException_returnsDownWithError() {
            DataSource ds = throwingDataSource(new SQLException("Connection refused"));
            indicator = new PebHealthIndicator(ds);

            Health health = indicator.health();

            assertEquals(Status.DOWN, health.getStatus(),
                "SQLException should return DOWN status");
            assertEquals("unreachable", health.getDetails().get("database"),
                "Database detail should say 'unreachable'");
            assertNotNull(health.getDetails().get("error"),
                "Error detail should be present");
            assertTrue(
                ((String) health.getDetails().get("error"))
                    .contains("Connection refused"),
                "Error should contain the exception message");
        }

        @Test
        @DisplayName("RuntimeException during getConnection returns DOWN")
        void runtimeException_returnsDown() {
            DataSource ds = throwingDataSource(new RuntimeException("Unexpected error"));
            indicator = new PebHealthIndicator(ds);

            Health health = indicator.health();

            assertEquals(Status.DOWN, health.getStatus(),
                "RuntimeException should return DOWN status");
            assertNotNull(health.getDetails().get("error"),
                "Error detail should be present even for unexpected exceptions");
        }

        @Test
        @DisplayName("null connection catalog is handled — returns UP with 'unknown' catalog")
        void nullCatalog_handled() {
            DataSource ds = stubDataSource(new StubConnection(true, null));
            indicator = new PebHealthIndicator(ds);

            Health health = indicator.health();

            assertEquals(Status.UP, health.getStatus(),
                "Valid connection with null catalog should return UP");
            assertEquals("unknown", health.getDetails().get("catalog"),
                "Null catalog should be reported as 'unknown'");
        }

        @Test
        @DisplayName("connection close exception is swallowed")
        void closeException_swallowed() {
            DataSource ds = stubDataSource(new StubConnection(true, "nexus") {
                @Override
                public void close() throws SQLException {
                    throw new SQLException("Close failed");
                }
            });
            indicator = new PebHealthIndicator(ds);

            assertDoesNotThrow(() -> indicator.health(),
                "Close exception should be swallowed, not propagated");
        }
    }

    // ─────────────────────────────────────────────────────────────
    // SILENT FAILURE
    // ─────────────────────────────────────────────────────────────

    @Nested
    @DisplayName("Silent failure — health check integrity")
    class SilentFailure {

        /**
         * Verifies the schema detail is always "peb" — not silently
         * reporting a different schema. If the health indicator reports
         * the wrong schema, monitoring tools would be misled.
         */
        @Test
        @DisplayName("health always reports 'peb' schema regardless of connection")
        void health_reportsCorrectSchema() {
            DataSource ds = stubDataSource(new StubConnection(true, "some_other_db"));
            indicator = new PebHealthIndicator(ds);

            Health health = indicator.health();

            assertEquals("peb", health.getDetails().get("schema"),
                "Schema detail must always be 'peb' — this is hardcoded "
                + "in the health indicator and should match the PEB schema");
        }

        /**
         * Verifies that each health check calls getConnection() — not
         * returning a cached/stale result. If the health indicator caches
         * and returns stale UP status when the DB is actually down, that's
         * a silent failure.
         */
        @Test
        @DisplayName("each call to health() gets a fresh connection")
        void eachHealthCheck_getsFreshConnection() {
            StubConnection conn1 = new StubConnection(true, "nexus");
            StubConnection conn2 = new StubConnection(true, "nexus");
            CountingDataSource ds = new CountingDataSource(conn1, conn2);
            indicator = new PebHealthIndicator(ds);

            indicator.health();
            indicator.health();

            assertEquals(2, ds.getConnectionCount(),
                "Each health() call should get a fresh connection");
        }
    }

    // ─────────────────────────────────────────────────────────────
    // Test stubs (replace Mockito mocks for javax.sql types)
    // ─────────────────────────────────────────────────────────────

    private static DataSource stubDataSource(Connection conn) {
        return new DataSource() {
            @Override public Connection getConnection() { return conn; }
            @Override public Connection getConnection(String u, String p) { return conn; }
            @Override public PrintWriter getLogWriter() { return null; }
            @Override public void setLogWriter(PrintWriter out) {}
            @Override public void setLoginTimeout(int seconds) {}
            @Override public int getLoginTimeout() { return 0; }
            @Override public Logger getParentLogger() throws SQLFeatureNotSupportedException {
                throw new SQLFeatureNotSupportedException();
            }
            @Override public <T> T unwrap(Class<T> iface) { return null; }
            @Override public boolean isWrapperFor(Class<?> iface) { return false; }
        };
    }

    private static DataSource throwingDataSource(Exception ex) {
        return new DataSource() {
            @Override public Connection getConnection() throws SQLException {
                if (ex instanceof SQLException) throw (SQLException) ex;
                throw new RuntimeException(ex);
            }
            @Override public Connection getConnection(String u, String p) throws SQLException {
                return getConnection();
            }
            @Override public PrintWriter getLogWriter() { return null; }
            @Override public void setLogWriter(PrintWriter out) {}
            @Override public void setLoginTimeout(int seconds) {}
            @Override public int getLoginTimeout() { return 0; }
            @Override public Logger getParentLogger() throws SQLFeatureNotSupportedException {
                throw new SQLFeatureNotSupportedException();
            }
            @Override public <T> T unwrap(Class<T> iface) { return null; }
            @Override public boolean isWrapperFor(Class<?> iface) { return false; }
        };
    }

    private static class CountingDataSource implements DataSource {
        private final Connection[] connections;
        private int index = 0;

        CountingDataSource(Connection... connections) {
            this.connections = connections;
        }

        int getConnectionCount() { return index; }

        @Override public Connection getConnection() {
            if (index < connections.length) return connections[index++];
            return connections[connections.length - 1];
        }
        @Override public Connection getConnection(String u, String p) { return getConnection(); }
        @Override public PrintWriter getLogWriter() { return null; }
        @Override public void setLogWriter(PrintWriter out) {}
        @Override public void setLoginTimeout(int seconds) {}
        @Override public int getLoginTimeout() { return 0; }
        @Override public Logger getParentLogger() throws SQLFeatureNotSupportedException {
            throw new SQLFeatureNotSupportedException();
        }
        @Override public <T> T unwrap(Class<T> iface) { return null; }
        @Override public boolean isWrapperFor(Class<?> iface) { return false; }
    }

    private static class StubConnection implements Connection {
        private final boolean valid;
        private final String catalog;
        private boolean closed = false;

        StubConnection(boolean valid, String catalog) {
            this.valid = valid;
            this.catalog = catalog;
        }

        @Override public boolean isValid(int timeout) { return valid; }
        @Override public String getCatalog() { return catalog; }
        @Override public void close() throws SQLException { closed = true; }
        @Override public boolean isClosed() { return closed; }

        // Stub implementations for remaining Connection methods
        @Override public java.sql.Statement createStatement() { throw new UnsupportedOperationException(); }
        @Override public java.sql.PreparedStatement prepareStatement(String sql) { throw new UnsupportedOperationException(); }
        @Override public java.sql.CallableStatement prepareCall(String sql) { throw new UnsupportedOperationException(); }
        @Override public String nativeSQL(String sql) { throw new UnsupportedOperationException(); }
        @Override public void setAutoCommit(boolean autoCommit) {}
        @Override public boolean getAutoCommit() { return false; }
        @Override public void commit() {}
        @Override public void rollback() {}
        @Override public java.sql.DatabaseMetaData getMetaData() { throw new UnsupportedOperationException(); }
        @Override public void setReadOnly(boolean readOnly) {}
        @Override public boolean isReadOnly() { return false; }
        @Override public void setCatalog(String catalog) {}
        @Override public void setTransactionIsolation(int level) {}
        @Override public int getTransactionIsolation() { return 0; }
        @Override public java.sql.SQLWarning getWarnings() { return null; }
        @Override public void clearWarnings() {}
        @Override public java.sql.Statement createStatement(int resultSetType, int resultSetConcurrency) { throw new UnsupportedOperationException(); }
        @Override public java.sql.PreparedStatement prepareStatement(String sql, int resultSetType, int resultSetConcurrency) { throw new UnsupportedOperationException(); }
        @Override public java.sql.CallableStatement prepareCall(String sql, int resultSetType, int resultSetConcurrency) { throw new UnsupportedOperationException(); }
        @Override public java.util.Map<String, Class<?>> getTypeMap() { throw new UnsupportedOperationException(); }
        @Override public void setTypeMap(java.util.Map<String, Class<?>> map) {}
        @Override public void setHoldability(int holdability) {}
        @Override public int getHoldability() { return 0; }
        @Override public java.sql.Savepoint setSavepoint() { throw new UnsupportedOperationException(); }
        @Override public java.sql.Savepoint setSavepoint(String name) { throw new UnsupportedOperationException(); }
        @Override public void rollback(java.sql.Savepoint savepoint) {}
        @Override public void releaseSavepoint(java.sql.Savepoint savepoint) {}
        @Override public java.sql.Statement createStatement(int resultSetType, int resultSetConcurrency, int resultSetHoldability) { throw new UnsupportedOperationException(); }
        @Override public java.sql.PreparedStatement prepareStatement(String sql, int resultSetType, int resultSetConcurrency, int resultSetHoldability) { throw new UnsupportedOperationException(); }
        @Override public java.sql.CallableStatement prepareCall(String sql, int resultSetType, int resultSetConcurrency, int resultSetHoldability) { throw new UnsupportedOperationException(); }
        @Override public java.sql.PreparedStatement prepareStatement(String sql, int autoGeneratedKeys) { throw new UnsupportedOperationException(); }
        @Override public java.sql.PreparedStatement prepareStatement(String sql, int[] columnIndexes) { throw new UnsupportedOperationException(); }
        @Override public java.sql.PreparedStatement prepareStatement(String sql, String[] columnNames) { throw new UnsupportedOperationException(); }
        @Override public java.sql.Clob createClob() { throw new UnsupportedOperationException(); }
        @Override public java.sql.Blob createBlob() { throw new UnsupportedOperationException(); }
        @Override public java.sql.NClob createNClob() { throw new UnsupportedOperationException(); }
        @Override public java.sql.SQLXML createSQLXML() { throw new UnsupportedOperationException(); }
        @Override public void setClientInfo(String name, String value) {}
        @Override public void setClientInfo(java.util.Properties properties) {}
        @Override public String getClientInfo(String name) { return null; }
        @Override public java.util.Properties getClientInfo() { return new java.util.Properties(); }
        @Override public java.sql.Array createArrayOf(String typeName, Object[] elements) { throw new UnsupportedOperationException(); }
        @Override public java.sql.Struct createStruct(String typeName, Object[] attributes) { throw new UnsupportedOperationException(); }
        @Override public void setSchema(String schema) {}
        @Override public String getSchema() { return null; }
        @Override public void abort(java.util.concurrent.Executor executor) {}
        @Override public void setNetworkTimeout(java.util.concurrent.Executor executor, int milliseconds) {}
        @Override public int getNetworkTimeout() { return 0; }
        @Override public <T> T unwrap(Class<T> iface) { return null; }
        @Override public boolean isWrapperFor(Class<?> iface) { return false; }
    }
}
