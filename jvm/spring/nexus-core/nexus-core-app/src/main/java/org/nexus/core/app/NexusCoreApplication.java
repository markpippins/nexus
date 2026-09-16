package org.nexus.core.app;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.ComponentScan;

/**
 * Nexus Core — runnable read-only JVM projection monolith.
 *
 * Component-scans both projection modules (org.nexus.core.search + broker)
 * and assembles them into a single Spring context on ONE port (:8090). This is
 * the failover/local posture: read surfaces served locally; writes return 405
 * until the JetStream write path lands.
 */
@SpringBootApplication
@ComponentScan(basePackages = { "org.nexus.core", "org.nexus.solscript" })
public class NexusCoreApplication {

    public static void main(String[] args) {
        SpringApplication.run(NexusCoreApplication.class, args);
    }
}