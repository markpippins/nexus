package org.nexus.peb.bootstrap;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.yaml.snakeyaml.Yaml;

import java.io.InputStream;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;

/**
 * Pins the JVM peb-bootstrap server port.
 *
 * <p>Ruling {@code 8ae4761b} (documented in {@code jvm/ARCHITECTURE.md},
 * F2-peb-bootstrap): port {@code 8098} belongs to the live <em>Python</em>
 * peb-kernel ({@code python3 -m peb_kernel.main}). The JVM bootstrap must not
 * claim it and is allocated {@code 8099}, dormant until deployed.
 *
 * <p>This is a config-pin regression test, not a style check: re-claiming
 * {@code 8098} would only surface at activation time, as a socket collision
 * between two kernels. Failing in CI is strictly cheaper.
 */
class ServerPortAuthorityTest {

    /** Owned by the live Python peb-kernel on titanium. */
    private static final int PYTHON_KERNEL_PORT = 8098;

    /** Allocated to this JVM bootstrap by the ratified port map. */
    private static final int JVM_BOOTSTRAP_PORT = 8099;

    @SuppressWarnings("unchecked")
    private Map<String, Object> applicationYaml() {
        try (InputStream in = getClass().getClassLoader()
                .getResourceAsStream("application.yml")) {
            assertNotNull(in, "application.yml must be on the test classpath");
            return new Yaml().load(in);
        } catch (Exception e) {
            throw new AssertionError("could not read application.yml", e);
        }
    }

    @Test
    @DisplayName("server.port is 8099 — never the Python kernel's 8098")
    void serverPortDoesNotClaimThePythonKernelPort() {
        Object server = applicationYaml().get("server");
        assertNotNull(server, "application.yml must declare a server block");

        Object port = ((Map<String, Object>) server).get("port");
        assertNotNull(port, "server.port must be declared explicitly, not left to the default");

        int actual = ((Number) port).intValue();
        assertNotEquals(PYTHON_KERNEL_PORT, actual,
                "8098 is owned by the live Python peb-kernel (ruling 8ae4761b) — "
                        + "the JVM bootstrap must not claim it");
        assertEquals(JVM_BOOTSTRAP_PORT, actual,
                "ratified port map allocates 8099 to spring/peb-kernel/peb-bootstrap "
                        + "(jvm/ARCHITECTURE.md, F2-peb-bootstrap)");
    }
}
