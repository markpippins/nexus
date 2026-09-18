package com.aibizarchitect.nexus.v1.spring.serviceregistry.entity;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

/**
 * Unit tests for the health_check_path behavior added by the health-path
 * conformance audit (assembly thread 70d507dc).
 *
 * Contract:
 *  - an explicit stored value always wins over the legacy derived value
 *  - with nothing stored, getHealthCheckPath() falls back to the legacy
 *    derived "<apiBasePath>/actuator/health"
 *  - blank/null input normalizes to null (fallback applies)
 *  - getHealthCheckPathRaw() exposes the stored value without fallback
 */
class ServiceHealthCheckPathTest {

    @Test
    void explicitStoredValueWins() {
        Service s = new Service();
        s.setApiBasePath("http://localhost:3116");
        s.setHealthCheckPath("http://localhost:3116/health");
        assertEquals("http://localhost:3116/health", s.getHealthCheckPath());
    }

    @Test
    void derivedFallbackWhenNothingStored() {
        Service s = new Service();
        s.setApiBasePath("http://localhost:3116");
        assertEquals("http://localhost:3116/actuator/health", s.getHealthCheckPath());
    }

    @Test
    void derivedFallbackIsNullWithoutApiBasePath() {
        Service s = new Service();
        assertNull(s.getHealthCheckPath());
    }

    @Test
    void setterAcceptsBarePath() {
        Service s = new Service();
        s.setApiBasePath("http://localhost:3116");
        s.setHealthCheckPath("/health");
        assertEquals("/health", s.getHealthCheckPath());
        assertEquals("/health", s.getHealthCheckPathRaw());
    }

    @Test
    void setterAcceptsPathWithContext() {
        Service s = new Service();
        s.setHealthCheckPath("/api/health");
        assertEquals("/api/health", s.getHealthCheckPathRaw());
    }

    @Test
    void blankInputNormalizesToNullSoFallbackApplies() {
        Service s = new Service();
        s.setApiBasePath("http://localhost:3101");
        s.setHealthCheckPath("   ");
        assertNull(s.getHealthCheckPathRaw());
        assertEquals("http://localhost:3101/actuator/health", s.getHealthCheckPath());
    }

    @Test
    void nullInputNormalizesToNull() {
        Service s = new Service();
        s.setApiBasePath("http://localhost:3101");
        s.setHealthCheckPath(null);
        assertNull(s.getHealthCheckPathRaw());
        assertEquals("http://localhost:3101/actuator/health", s.getHealthCheckPath());
    }

    @Test
    void inputIsTrimmed() {
        Service s = new Service();
        s.setHealthCheckPath("  http://localhost:3101/health  ");
        assertEquals("http://localhost:3101/health", s.getHealthCheckPathRaw());
    }

    @Test
    void rawAccessorSkipsFallback() {
        Service s = new Service();
        s.setApiBasePath("http://localhost:3106");
        assertNull(s.getHealthCheckPathRaw());
    }
}
